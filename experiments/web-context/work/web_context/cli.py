"""Public Web preparation and separately authorized on-phone calendar execution."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from router.calendar_tool import CalendarTool, operation_key, qualified
from router.cli import choose_calendar, countdown
from router.device import ADB, MainGuard, Monitor, device_lease
from router.planner_client import planner_config
from router.planner_diagnostics import redact
from router.planning import ZONE, now_local, route
from .planner import StudyValidationError, request_study
from .schema import (digest, goals_for, json_loads, make_bundle, render_brief, render_evidence,
                     validate_bundle, validate_context, validate_sources)
from .sources import local_proxy, read_source

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URLS = ["https://source.android.com/docs/core/display/multi_display/displays",
                "https://source.android.com/docs/core/display/multi_display/ime-support",
                "https://developer.android.com/identity/providers/calendar-provider"]
DEFAULT_BACKGROUND = (
    "本项目已验收事实（仅 HONOR Magic3 / Android 14，不代表所有手机）："
    "电脑编排并调用模型，执行发生在同一台手机。修改 scrcpy 服务端创建虚拟显示，"
    "启用并回读 OWN_FOCUS 与 STEAL_TOP_FOCUS_DISABLED；通过 adb input -d 指定副屏。"
    "没有修改全局 config_perDisplayFocusEnabled，没有修改 /vendor/etc/input-port-associations.xml，"
    "没有改 ROM、切换默认输入法或使用剪贴板。主屏持续打字时，副屏设置→关于手机任务通过。"
    "副屏软键盘曾被系统以 display ID mismatch 拒绝；仅另行验收了无副屏软键盘的固定 ASCII 7abc123，"
    "尚未验证两套软键盘同时工作、通用 Type 或中文输入。"
    "日历通过电脑 ADB shell 调用 Calendar Provider 写入并精确回读，绕开日历表单；"
    "这不是普通 Android App 的后台权限实现，不含提醒/邀请。"
    "需结合文档解释焦点、IME、定向输入的区别和边界，不能把文档里的其他配置当成项目已经使用。"
)


def save_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("上下文/计划包文件过大。")
    return json_loads(raw.decode("utf-8"))


class Record:
    def __init__(self, mode):
        directory = ROOT / "outputs"
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix=mode + "-" + now_local().strftime("%Y%m%d-%H%M%S") + "-", dir=directory))
        self.path = self.directory / "result.json"
        self.data = {"mode": mode, "state": "RECEIVED", "transitions": [], "operations": []}
        self.transition("RECEIVED")

    def save(self):
        save_json(self.path, self.data)

    def transition(self, state, **fields):
        self.data.update(state=state, **fields)
        self.data["transitions"].append({"state": state, "at": now_local().isoformat()})
        self.save()


def verify_source():
    manifest = read_json(ROOT / "baseline/source-sha256.json")
    for name, expected in manifest.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("冻结基线文件变化，停止：" + name)


def state_root():
    sibling = ROOT.parent / "wellphone-router"
    root = Path(os.environ.get("WELLPHONE_STATE_ROOT", str(sibling if sibling.is_dir() else ROOT))).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("执行状态目录不存在；不新建替代旧的防重复记录。")
    return root


def init_context(args):
    if (not args.start or args.minutes is None) and not sys.stdin.isatty():
        raise ValueError("需要明确的开始时间；请交互运行 init 或传 --start 与 --minutes。")
    start_text = args.start or input("复习开始时间（本地北京时间，格式 YYYY-MM-DD HH:MM，必须在未来）：").strip()
    minutes = args.minutes if args.minutes is not None else int(input("本次复习时长（15–120 分钟）：").strip())
    start = datetime.strptime(start_text, "%Y-%m-%d %H:%M").replace(tzinfo=ZONE)
    context = {"schema": 1, "timezone": "Asia/Shanghai",
        "goal": "准备 Wellphone 手机 Agent 项目技术答辩，能够解释不抢主屏的设计取舍和失败处理。",
        "background": DEFAULT_BACKGROUND,
        "priorities": ["独立显示焦点、顶层焦点与定向输入的区别", "单 IME 与副屏键盘限制", "Provider 与打开 App 表单的差别", "结合真实验收说明失败边界，不扩大成功范围"],
        "urls": DEFAULT_URLS,
        "slots": [{"id": "A", "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat()}]}
    validate_context(context)
    record = Record("web-context")
    path = record.directory / "context.json"
    save_json(path, context)
    record.transition("TEMPLATE_CREATED_NOT_EXECUTED", context_path=str(path))
    print("已生成可编辑上下文。项目事实和三篇官方文档是本项目示例；请确认符合你的实际需求。")
    print(json.dumps(context, ensure_ascii=False, indent=2))
    print(f"上下文：{path}")
    print(f"确认内容后运行：python3 {ROOT / 'run.py'} web plan --context '{path}'")
    return 0


def prepare(args):
    context = validate_context(read_json(args.context))
    config = planner_config(os.environ)
    if not sys.stdin.isatty():
        raise ValueError("公开网页读取与上下文上传需要 Terminal 交互确认；未请求网页或模型。")
    print(f"规划模型：{config.model}；接口：{config.base_url}；密钥来源：{config.key_name}。")
    if os.environ.get("WELLPHONE_WEB_PROXY"):
        port = local_proxy(os.environ["WELLPHONE_WEB_PROXY"])
        print(f"网页使用你显式指定的本机代理 127.0.0.1:{port}；只支持默认的两个官方文档域名，保留目标 HTTPS 校验。")
    print("将读取以下公开网页，并把提取的正文＋你提供的背景/关注点/时段发送给该模型（一次请求，可能计费）。")
    print(json.dumps(context, ensure_ascii=False, indent=2))
    print("不读取主屏、聊天、已有日历或浏览器登录信息；不调用 AutoGLM、不连接手机、不写日历。")
    if input("同意读取这些网页并上传以上上下文？输入 yes；其他输入取消：").strip() != "yes":
        print("已取消，无网络请求或手机操作。")
        return 2
    key = os.environ.get(config.key_name, "").strip() or getpass.getpass(config.key_name + "（隐藏输入，不保存）：").strip()
    if not key or key in {"...", "EMPTY"}:
        raise ValueError("规划密钥为空或占位符；未请求资料或模型。")
    record = Record("web-plan")
    record.data.update(model=config.model, provider=config.provider, phone_connected=False, model_called=False)
    try:
        sources = []
        record.transition("READING_SOURCES")
        for index, url in enumerate(context["urls"], 1):
            print(f"读取资料 {index}/{len(context['urls'])}：{url}", flush=True)
            sources.append(read_source(url, index))
        validate_sources(sources, context)
        save_json(record.directory / "sources.json", sources)
        (record.directory / "sources.txt").write_text("\n\n".join(
            f"{s['id']} {s['url']}\n" + "\n\n".join(f"[{p['id']}] {p['text']}" for p in s["paragraphs"])
            for s in sources), encoding="utf-8")
        record.transition("PLANNING", source_count=len(sources), model_called=True)
        print("资料读取完成，正在结合背景和可用时段规划……", flush=True)
        plan = request_study(context, sources, config, key)
        bundle = make_bundle(context, sources, plan, config.model)
        path = record.directory / "bundle.json"
        save_json(path, bundle)
        brief = render_brief(bundle)
        (record.directory / "brief.md").write_text(brief, encoding="utf-8")
        evidence_path = record.directory / "evidence.md"
        evidence_path.write_text(render_evidence(bundle), encoding="utf-8")
        record.transition("PLANNED_NOT_EXECUTED" if plan["status"] == "PLANNED" else "CLARIFY",
                          bundle_path=str(path), bundle_sha256=bundle["sha256"],
                          evidence_path=str(evidence_path), semantic_grounding_verified=False,
                          routes=[{"capability": "web.read", "channel": "public_https", "executed": True},
                                  {"capability": "brief.create", "channel": "local_file", "executed": True},
                                  {"capability": "calendar.create", "channel": "system_tool", "executed": False}],
                          question=plan["question"])
        print(brief, flush=True)
        print(f"简报：{record.directory / 'brief.md'}")
        print(f"逐项引用核对：{evidence_path}（引用存在不等于结论已核实）")
        if plan["status"] == "PLANNED":
            print("计划已生成，尚未验证设备资格或写入日历。")
            print(f"核对后执行：python3 {ROOT / 'run.py'} web apply --bundle '{path}'")
            return 0
        print("需要你补充上下文，未生成手机执行任务。")
        return 2
    except StudyValidationError as exc:
        save_json(record.directory / "planner-diagnostic.json", {"reason": redact(str(exc), key),
                  "model_content": exc.body, "execution": "NOT_EXECUTED"})
        record.transition("REJECTED", reason=redact(str(exc), key))
        print("模型输出未通过校验；诊断已保存，不自动修复或重试。")
        return 1
    except (KeyboardInterrupt, EOFError):
        record.transition("INTERRUPTED", phone_connected=False)
        return 130
    except Exception as exc:
        record.transition("FAILED", reason=redact(str(exc), key))
        print("资料任务停止：" + record.data["reason"])
        return 1
    finally:
        print(f"记录：{record.path}", flush=True)


def apply_bundle(args):
    if not sys.stdin.isatty():
        raise ValueError("写日历需要 Terminal 交互确认；未连接手机。")
    # This stage has no model call; do not pass model credentials to adb children.
    for key_name in ("PHONE_AGENT_API_KEY", "DEEPSEEK_API_KEY", "WELLPHONE_PLANNER_API_KEY"):
        os.environ.pop(key_name, None)
    bundle = validate_bundle(read_json(args.bundle))
    if bundle["plan"]["status"] != "PLANNED":
        raise ValueError("当前计划需要澄清，不能写入日历。")
    goals = goals_for(bundle["plan"], bundle["context"])
    shared = state_root()
    record = Record("web-apply")
    record.data.update(bundle_sha256=bundle["sha256"], bundle_path=str(Path(args.bundle).resolve()),
                       shared_state_root=str(shared), model_called=False, gui_started=False)
    monitor = None
    try:
        adb = ADB(args.serial).connect()
        with device_lease(shared, adb.serial):
            state = adb.probe()
            calendar_id = choose_calendar(state, args.calendar_id)
            routes = [route(g, state, calendar_id, qualified(shared, state, calendar_id)) for g in goals]
            record.transition("PLANNED", device=state, goals=goals, routes=routes)
            print(render_brief(bundle))
            print(json.dumps({"日历目标": goals, "路由": routes}, ensure_ascii=False, indent=2))
            if any(r["status"] != "READY" for r in routes):
                record.transition("WAITING", reason="设备资格或实时状态未满足，没有写入。")
                return 2
            print("只将以上标题和起止时间写入手机日历；详细简报和来源留在电脑，不写进日历备注。")
            print("没有读取现有日程，不能保证不冲突。日程可能同步到账号；不设提醒、不发邀请，不自动删除。")
            print("主屏请保持短信草稿键盘，不发送短信、不打开日历。确认后 5 秒准备，再持续打字。")
            if input("确认内容和时间正确，同意写入以上日程？输入 yes；其他输入取消：").strip() != "yes":
                record.transition("CANCELLED")
                return 2
            record.transition("AUTHORIZED")
            countdown()
            validate_bundle(bundle)  # Slots can expire while reading/approving.
            guard = MainGuard(adb, state["default_ime"], state["calendar_packages"])
            monitor = Monitor(guard)
            tool = CalendarTool(adb, shared / "outputs" / "router-journal.sqlite3")
            try:
                with monitor:
                    for goal in goals:
                        current = adb.probe()
                        decision = route(goal, current, calendar_id, qualified(shared, current, calendar_id))
                        if (decision["status"] != "READY" or current["default_ime"] != state["default_ime"]
                                or current["build_hash"] != state["build_hash"] or current["serial"] != state["serial"]):
                            raise ValueError("执行前设备或交互状态变化，停止后续写入。")
                        operation = {"goal": goal, "calendar_id": calendar_id, "effect_verified": False,
                                     "operation_key": operation_key(adb.serial, calendar_id, goal)}
                        record.data["operations"].append(operation)
                        record.transition("RUNNING")
                        try:
                            operation.update(tool.create(goal, calendar_id, monitor.require))
                        finally:
                            row = tool.db.execute("SELECT write_started,event_id FROM operations WHERE key=?",
                                                  (operation["operation_key"],)).fetchone()
                            operation.update(journal_write_started=bool(row and row[0]), journal_event_id=row[1] if row else None)
                            record.save()
                        record.transition("VERIFYING")
                    time.sleep(3)
            finally:
                tool.close()
    except (KeyboardInterrupt, EOFError):
        record.data["interrupted"] = True
        print("已停止后续动作；已经写入的事件保留，请核对记录，不盲目重试。")
    except Exception as exc:
        record.data["error"] = str(exc)
        print("执行停止：" + str(exc))
    finally:
        if monitor:
            record.data.update(primary_samples=monitor.samples, primary_errors=monitor.errors)
            try:
                record.data["keyboard_observation"] = input("主屏持续打字：正常 / 异常 / 未观察：").strip()
                record.data["other_disturbance"] = input("额外弹窗、声音或明显卡顿：无 / 有 / 未观察：").strip()
            except (KeyboardInterrupt, EOFError):
                record.data["interrupted"] = True
        record.save()
        print(f"记录：{record.path}", flush=True)
    data = record.data
    effect = len(data["operations"]) == len(goals) and all(o.get("effect_verified") for o in data["operations"])
    isolated = bool(monitor and monitor.samples and not monitor.errors
                    and data.get("keyboard_observation") == "正常" and data.get("other_disturbance") == "无")
    passed = bool(effect and isolated and not data.get("error") and not data.get("interrupted"))
    record.transition("COMPLETED" if passed else "INTERRUPTED" if data.get("interrupted") else "FAILED",
                      passed=passed, effect_verified=effect, isolation_verified=isolated,
                      partial_effect=any(o.get("effect_verified") for o in data["operations"]) and not passed)
    print("复习安排已写入并回读验证，主屏观察通过；没有手机通知。" if passed else
          "本轮未全部通过；已完成的写入保留，结果不确定时不要重新生成计划来绕过防重。")
    return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="公开技术资料＋明确上下文 → 复习简报 → 确认后静默日历")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("init", help="生成可编辑上下文，不联网、不连接手机")
    command.add_argument("--start", help="北京时间 YYYY-MM-DD HH:MM")
    command.add_argument("--minutes", type=int)
    command = commands.add_parser("plan", help="读取网页并调用规划模型，不连接手机")
    command.add_argument("--context", required=True)
    command = commands.add_parser("apply", help="不调用模型；核对并执行已保存的计划")
    command.add_argument("--bundle", required=True)
    command.add_argument("--serial")
    command.add_argument("--calendar-id", type=int)
    args = parser.parse_args(argv)
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    verify_source()
    if args.command == "init":
        return init_context(args)
    if args.command == "plan":
        return prepare(args)
    return apply_bundle(args)
