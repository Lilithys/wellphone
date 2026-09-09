"""Local CLI: explain-only by default; phone writes require interactive approval."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from .planning import CAPABILITIES, now_local, route, understand_rules, validate_goal
from .planner_client import planner_config, request_plan
from .planner_diagnostics import PlanValidationError, save_diagnostic
from .device import ADB, DeviceError, MainGuard, Monitor, device_lease
from .calendar_tool import CalendarTool, qualified, save_qualification, operation_key

ROOT = Path(__file__).resolve().parents[2]


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


class Record:
    def __init__(self, root, request, mode):
        directory = Path(root) / "outputs"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix="router-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-", dir=directory)) / "result.json"
        self.data = {"request": request, "mode": mode, "state": "RECEIVED", "transitions": [], "operations": []}
        self.transition("RECEIVED")

    def save(self):
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def transition(self, state, **fields):
        self.data.update(state=state, **fields)
        self.data["transitions"].append({"state": state, "at": datetime.now().isoformat()})
        self.save()


def choose_calendar(state, explicit=None):
    ids = state.get("writable_calendar_ids", [])
    return explicit if explicit is not None else ids[0] if len(ids) == 1 else None


def understand(args):
    if args.planner == "rules":
        return understand_rules(args.task)
    config = planner_config(os.environ)
    key = os.environ.get(config.key_name, "").strip()
    if not key and sys.stdin.isatty():
        key = getpass.getpass(f"{config.key_name}（规划专用，隐藏输入，不保存）：").strip()
    if not key or key in {"...", "EMPTY"}:
        raise ValueError(f"未配置有效的 {config.key_name}；不会使用 PHONE_AGENT_API_KEY。")
    print(f"规划模型：{config.model}；接口：{config.base_url}；密钥来源：{config.key_name}。", flush=True)
    print("只发送任务文字与固定规则/时间，不读取主屏或日历数据；本次会产生一次 API 请求。", flush=True)
    print("若目标校验失败，将在本地 outputs 保存脱敏的任务文字、模型正文和拒绝原因；不保存密钥或请求头。", flush=True)
    try:
        return request_plan(args.task, config, key)
    except PlanValidationError as exc:
        try:
            path = save_diagnostic(ROOT, config, exc.diagnostic)
        except OSError:
            print("诊断文件保存失败；仍停止执行，不重试模型。", flush=True)
        else:
            print(f"诊断记录：{path}", flush=True)
        raise
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError("规划模型调用未通过（" + type(exc).__name__ + "）；不回退为执行。") from None


def countdown():
    print("电脑回车后的准备倒计时；现在请回到手机主屏持续打字。", flush=True)
    for remaining in range(5, 0, -1):
        print(f"{remaining}…", flush=True)
        time.sleep(1)


def run_calendar(adb, state, goal, calendar_id, record):
    guard = MainGuard(adb, state["default_ime"], state["calendar_packages"])
    operation = {"goal": goal, "capability": "calendar.create", "calendar_id": calendar_id,
                 "effect_verified": False, "isolation_verified": False,
                 "operation_key": operation_key(adb.serial, calendar_id, validate_goal(goal))}
    record.data["operations"].append(operation)
    record.save()
    tool = CalendarTool(adb, ROOT / "outputs" / "router-journal.sqlite3")
    monitor = Monitor(guard)
    error = None
    try:
        with monitor:
            operation.update(tool.create(goal, calendar_id, monitor.require))
            record.transition("VERIFYING")
            time.sleep(3)
    except Exception as exc:
        error = str(exc)
    finally:
        row = tool.db.execute("SELECT write_started,event_id FROM operations WHERE key=?", (operation["operation_key"],)).fetchone()
        operation["journal_write_started"] = bool(row and row[0])
        operation["journal_event_id"] = row[1] if row else None
        tool.close()
        operation.update(focus_samples=monitor.samples, focus_errors=monitor.errors)
        if error:
            operation["error"] = error
        record.save()
    print("手机主屏持续打字是否正常？0=正常，1=有异常，2=未看清。即使写入失败也请记录观察。")
    observation = input("输入编号：").strip()
    operation["human_observation"] = {"0": "正常", "1": "异常", "2": "未看清"}.get(observation, "未看清")
    operation["isolation_verified"] = bool(monitor.samples and not monitor.errors and observation == "0")
    operation["passed"] = bool(not error and operation["effect_verified"] and operation["isolation_verified"])
    record.save()
    return operation


def execute(args, calibration=False):
    if not sys.stdin.isatty():
        raise ValueError("执行必须在 Terminal 交互确认；没有操作手机。plan/doctor 不需要执行确认。")
    if calibration:
        now = now_local()
        start = (now + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
        goal = {"kind": "calendar.create", "title": "Wellphone 验收 " + now.strftime("%Y%m%d-%H%M%S"),
                "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat()}
        request = "用户显式启动静默日历专项验收"
        plan = {"status": "PLANNED", "source": "fixed_calibration", "goals": [goal], "question": None}
    else:
        request, plan = args.task, None
    record = Record(ROOT, request, "calendar-test" if calibration else "run")
    try:
        if plan is None:
            plan = understand(args)
        record.transition("UNDERSTOOD", understanding=plan)
        if plan["status"] != "PLANNED":
            record.transition("WAITING", reason=plan["question"])
            dump(plan)
            return 2
        adb = ADB(args.serial).connect()
        with device_lease(ROOT, adb.serial):
            state = adb.probe()
            calendar_id = choose_calendar(state, args.calendar_id)
            routes = [route(goal, state, calendar_id, qualified(ROOT, state, calendar_id), calibration) for goal in plan["goals"]]
            record.transition("PLANNED", device=state, routes=routes)
            dump({"目标": plan["goals"], "路由": routes})
            if any(item["status"] != "READY" for item in routes):
                record.transition("WAITING", reason="前置条件或能力验收不满足，未执行任何动作。")
                return 2
            print("请核对以上目标、日历 ID 和时间。日历写入可能同步到该账号；不设提醒、不发送邀请。")
            print("所有数据写入保留在手机，不自动删除。GUI 仅执行固定的设置→关于手机任务，会调用 AutoGLM。")
            print("确认前先让手机主屏短信草稿键盘保持显示，不发送消息；不要在主屏打开日历或设置。")
            if input("同意执行以上具体操作？输入 yes；其他输入均取消：").strip().lower() != "yes":
                record.transition("CANCELLED")
                return 2
            record.transition("AUTHORIZED")
            countdown()
            for goal in plan["goals"]:
                validate_goal(goal)
                current = adb.probe()
                decision = route(goal, current, calendar_id, qualified(ROOT, current, calendar_id), calibration)
                if decision["status"] != "READY" or current["default_ime"] != state["default_ime"]:
                    record.transition("WAITING", reason="执行前条件改变，不继续后续操作。", current_route=decision)
                    return 2
                record.transition("RUNNING", active_capability=goal["kind"])
                if goal["kind"] == "calendar.create":
                    receipt = run_calendar(adb, state, goal, calendar_id, record)
                else:
                    from .gui_tool import execute_about
                    receipt = execute_about(adb, state["default_ime"], args.max_steps)
                    receipt.update(capability=goal["kind"], goal=goal)
                    record.data["operations"].append(receipt)
                    record.save()
                if not receipt["passed"]:
                    record.transition("FAILED", reason="目标结果或主屏隔离没有同时通过；已完成的写入不自动回滚。")
                    return 1
                if calibration:
                    if receipt.get("reused"):
                        record.transition("FAILED", reason="复用旧事件不能代替本次写入验收。")
                        return 1
                    save_qualification(ROOT, state, calendar_id, record.path)
            record.transition("COMPLETED")
            print("已完成并验证。结果仅在电脑显示；没有向手机发送通知、弹窗或声音。")
            if calibration:
                print("静默日历专项通过；测试日程已保留，可稍后在手机日历中查看或自行删除。")
            return 0
    except (KeyboardInterrupt, EOFError):
        record.transition("INTERRUPTED", reason="已停止后续动作；若发生过写入，请查看 operation_key 和事件 ID，不要盲目重试。")
        return 130
    except Exception as exc:
        record.transition("FAILED", reason=str(exc))
        print(str(exc), flush=True)
        return 1
    finally:
        print(f"记录：{record.path}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Wellphone：理解目标、规则路由、执行与独立验收")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = commands.add_parser(name, help="只理解、不操作设备" if name == "plan" else "交互确认后执行")
        command.add_argument("task")
        command.add_argument("--planner", choices=["rules", "llm"], default="rules")
        if name == "run":
            command.add_argument("--serial")
            command.add_argument("--calendar-id", type=int)
            command.add_argument("--max-steps", type=int, default=8, choices=range(1, 9))
    command = commands.add_parser("doctor", help="只读检查本机能力与验收状态")
    command.add_argument("--serial")
    command = commands.add_parser("calendar-test", help="交互创建一条明确预览的测试日程，不调用模型")
    command.add_argument("--serial")
    command.add_argument("--calendar-id", type=int)
    command.set_defaults(max_steps=8)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.command == "plan":
        plan = understand(args)
        dump({"understanding": plan, "candidate_capabilities": [{"capability": g["kind"], **CAPABILITIES[g["kind"]]} for g in plan["goals"]],
              "execution": "NOT_EXECUTED", "note": "尚未检查实时设备状态；这不是可执行授权。"})
        return 0 if plan["status"] == "PLANNED" else 2
    if args.command == "doctor":
        adb = ADB(args.serial).connect()
        state = adb.probe()
        dump({"device": state, "capabilities": CAPABILITIES,
              "calendar_qualified": {str(cid): qualified(ROOT, state, cid) for cid in state["writable_calendar_ids"]},
              "mutations": 0})
        return 0
    return execute(args, args.command == "calendar-test")
