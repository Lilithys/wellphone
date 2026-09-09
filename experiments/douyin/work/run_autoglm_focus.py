#!/usr/bin/env python3
"""AutoGLM + verified no-steal-focus scrcpy session. Settings-only smoke task."""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

from test_keyboard_isolation import KeyboardTest, activity_on_display
from scrcpy_frames import FrameStream


TASK = "打开设置，进入关于手机页面，然后结束。不要修改任何设置。不要输入文字，不要打开其他应用。"
SERVER_SHA256 = "f837bbb986ea311f02a1328bde88a97399c4bd81c507f86b6d824774cd0d8d78"
CLIENT_SHA256 = "f9f8c02f887d1218a51dd953fc7dccf70dd4cc6557d46c5bca7ce08bd72171f2"
BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


def normalize_component(component):
    if "/" not in component:
        return component
    package, name = component.split("/", 1)
    return package + "/" + (package + name if name.startswith(".") else name)


def validate_action(action, app_packages):
    """Bound the smoke test; this is not a general semantic safety proof for taps."""
    if action.get("_metadata") == "finish":
        return
    if action.get("_metadata") != "do":
        raise RuntimeError("无法识别的动作类型，已停止。")
    name = action.get("action")
    if name not in {"Launch", "Tap", "Swipe", "Back", "Wait", "Note"}:
        raise RuntimeError(f"本轮不允许动作 {name}；文字输入、Home 等尚未纳入验收。")
    if name == "Launch" and app_packages.get(action.get("app")) != "com.android.settings":
        raise RuntimeError("本轮只允许启动系统设置。")
    for field in {"Tap": ("element",), "Swipe": ("start", "end")}.get(name, ()):
        point = action.get(field)
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1000
            for value in point
        ):
            raise RuntimeError("动作坐标无效，已停止。")
    if name == "Wait":
        duration = str(action.get("duration", "1 seconds"))
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:seconds)?", duration.strip())
        if not match or not 0 <= float(match.group(1)) <= 10:
            raise RuntimeError("本轮单次等待只允许 0–10 秒。")


class AutoGLMSession(KeyboardTest):
    def prepare(self):
        super().prepare()
        self.report["client_path"] = self.args.client_path
        self.report["client_sha256"] = hashlib.sha256(Path(self.args.client_path).read_bytes()).hexdigest()
        resolved = self.shell("cmd", "package", "resolve-activity", "--brief", "-a",
                              "android.settings.DEVICE_INFO_SETTINGS", "-p", "com.android.settings")
        candidates = re.findall(r"(?m)^com\.android\.settings/[\w.$]+$", resolved)
        if len(candidates) != 1:
            raise RuntimeError("无法确定关于手机的真实目标页面，尚未启动应用。")
        self.goal_component = normalize_component(candidates[0])
        self.report.update(task=TASK, goal_component=self.goal_component,
                           mode="preflight" if self.args.preflight else "autoglm",
                           model="autoglm-phone", base_url=BASE_URL)

    def assert_task_scope(self):
        self.guard()
        if getattr(self, "frames", None) is not None:
            self.frames.assert_live()
        dump = self.shell("dumpsys", "activity", "activities")
        main = activity_on_display(dump, 0)
        virtual = activity_on_display(dump, self.display_id)
        if main.startswith("com.android.settings/"):
            raise RuntimeError("主屏正在使用设置；为避免共享应用状态冲突，已停止。")
        if not virtual.startswith("com.android.settings/"):
            raise RuntimeError("副屏离开系统设置，已停止截图和动作。")
        state = self.read_focus()
        if state["top_focused_display_id"] != 0:
            raise RuntimeError("无法确认主屏持有顶层焦点，已停止后续动作。")
        return virtual


class GuardedActions:
    def __init__(self, delegate, session):
        self.delegate, self.session = delegate, session

    def execute(self, action, width, height):
        from phone_agent.actions.handler import ActionResult
        from phone_agent.config.apps import APP_PACKAGES
        try:
            validate_action(action, APP_PACKAGES)
            current = self.session.assert_task_scope()
            if action.get("_metadata") == "finish" and normalize_component(current) != self.session.goal_component:
                raise RuntimeError("模型声称完成，但副屏没有到达关于手机页面。")
            started_ns = time.time_ns()
            result = self.delegate.execute(action, width, height)
            if result.success and not result.should_finish and getattr(self.session, "frames", None) is not None:
                changing = action.get("action") in {"Tap", "Swipe", "Back"}
                self.session.frames.wait_for_frame(not_before_ns=started_ns if changing else 0)
            return result
        except Exception as exc:
            return ActionResult(success=False, should_finish=True, message=str(exc))


def save_frame(session, name):
    from PIL import Image, ImageStat
    from phone_agent.device_factory import get_device_factory
    session.assert_task_scope()
    frame = get_device_factory().get_screenshot(session.serial)
    data = base64.b64decode(frame.base64_data, validate=True)
    with Image.open(BytesIO(data)) as picture:
        if picture.size != (1080, 2400) or max(ImageStat.Stat(picture.convert("RGB")).stddev) < 2:
            raise RuntimeError("副屏截图尺寸异常或接近纯色，未调用模型。")
    path = session.output / name
    path.write_bytes(data)
    return str(path)


def execute(args):
    from phone_agent.agent import AgentConfig, PhoneAgent
    from phone_agent.device_factory import set_virtual_display
    from phone_agent.model import ModelConfig

    session = AutoGLMSession(args)
    trace_stop, trace_thread = threading.Event(), None
    api_key = ""
    agent = None
    frames = None
    try:
        session.prepare()
        if not args.preflight:
            api_key = os.environ.get("PHONE_AGENT_API_KEY", "").strip()
            if not api_key:
                if not sys.stdin.isatty():
                    raise RuntimeError("当前进程没有 API key。请在 Terminal 运行；可以安全地隐藏输入，不要发到聊天里。")
                api_key = getpass.getpass("请输入 PHONE_AGENT_API_KEY（隐藏输入，不保存）：").strip()
            if not api_key or api_key in {"...", "EMPTY"}:
                raise RuntimeError("API key 为空或仍是占位符。")
            print(f"本轮只把副屏的系统设置画面发送给 {BASE_URL}，最多 {args.max_steps} 步。", flush=True)
            print("请在手机主屏打开之前的短信草稿并调出键盘，不要发送消息。", flush=True)
            input("电脑按回车后有 5 秒准备时间，再持续在手机上打字：")
            for remaining in range(5, 0, -1):
                print(f"{remaining}…", flush=True)
                time.sleep(1)
        else:
            print("预检：副屏设置 → 关于手机 → 返回，检查实时截图；不调用模型、不需要 API key。", flush=True)

        session.report["before"] = session.snapshot()
        session.report["focus_trace"] = []

        def record_focus():
            while not trace_stop.is_set():
                try:
                    session.report["focus_trace"].append(session.read_focus())
                except Exception as exc:
                    session.report["focus_trace"].append({"error": str(exc)})
                trace_stop.wait(0.4)

        trace_thread = threading.Thread(target=record_focus, daemon=True)
        trace_thread.start()
        frames = FrameStream(session.output)
        session.frames = frames
        frames.start()
        args.record_path = str(frames.pipe_path)
        session.start_display()
        launch_started_ns = time.time_ns()
        session.launch_settings()
        frames.wait_for_frame(not_before_ns=launch_started_ns)
        set_virtual_display(session.display_id, window_title=args.window_title,
                            window_pid=session.process.pid, device_id=session.serial,
                            frame_path=str(frames.frame_path), frame_producer_pid=frames.process.pid)
        session.report["capture_source"] = "native_scrcpy_video"
        session.report["preflight_frame"] = save_frame(session, "preflight_settings.png")
        if args.preflight:
            changed_ns = time.time_ns()
            session.open_info()
            frames.wait_for_frame(not_before_ns=changed_ns)
            current = session.assert_task_scope()
            if normalize_component(current) != session.goal_component:
                raise RuntimeError("预检没有到达关于手机页面。")
            session.report["preflight_about_frame"] = save_frame(session, "preflight_about.png")
            changed_ns = time.time_ns()
            session.press_back()
            frames.wait_for_frame(not_before_ns=changed_ns)
            session.report["preflight_return_frame"] = save_frame(session, "preflight_return.png")
        session.report["preflight_passed"] = True
        session.save()
        print("副屏截图预检通过。", flush=True)

        if not args.preflight:
            agent = PhoneAgent(
                model_config=ModelConfig(base_url=BASE_URL, api_key=api_key,
                                         model_name="autoglm-phone", lang="cn"),
                agent_config=AgentConfig(device_id=session.serial, max_steps=args.max_steps,
                                         verbose=False, lang="cn"),
                confirmation_callback=lambda message: False,
                takeover_callback=reject_takeover,
            )
            agent.model_client.client = agent.model_client.client.with_options(timeout=45.0, max_retries=0)
            agent.action_handler = GuardedActions(agent.action_handler, session)
            session.report["steps"] = []
            for index in range(args.max_steps):
                session.assert_task_scope()
                step = agent.step(TASK if index == 0 else None)
                entry = {"number": index + 1, "success": step.success, "finished": step.finished,
                         "action": step.action,
                         "message": step.message.replace(api_key, "[REDACTED]") if step.message else None,
                         "state": session.snapshot()}
                session.report["steps"].append(entry)
                session.save()
                print(f"第 {index + 1} 步：{step.action}；成功={step.success}", flush=True)
                if not step.success:
                    raise RuntimeError(step.message or "模型或动作执行失败")
                if step.finished:
                    current = session.assert_task_scope()
                    if normalize_component(current) != session.goal_component:
                        raise RuntimeError("最终页面验证失败，不能判定任务完成。")
                    session.report["final_frame"] = save_frame(session, "final_about_phone.png")
                    session.report["task_verified"] = True
                    break
            if not session.report.get("task_verified"):
                raise RuntimeError("达到步数上限，未验证任务完成。")
        session.report["completed"] = True
    except (KeyboardInterrupt, EOFError):
        session.report["interrupted"] = True
        print("已中断，正在关闭本次副屏。", flush=True)
    except Exception as exc:
        error = str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc)
        session.report["error"] = error
        print(f"联调停止：{error}", flush=True)
    finally:
        if agent is not None:
            try:
                agent.model_client.client.close()
            except Exception:
                pass  # Always clean up the owned scrcpy session even if HTTP cleanup fails.
        try:
            session.stop_display()
        except Exception as exc:
            session.report["cleanup_error"] = str(exc)
        if frames is not None:
            try:
                frames.stop()
            except Exception as exc:
                session.report["cleanup_error"] = str(exc)
        trace_stop.set()
        if trace_thread is not None:
            trace_thread.join(timeout=3)
            session.report["trace_incomplete"] = trace_thread.is_alive()
        if session.serial:
            session.report["after"] = session.snapshot()
        session.report["finished_at"] = datetime.now().isoformat()
        session.save()
        (session.output / "scrcpy.log").write_text("\n".join(session.scrcpy_log), encoding="utf-8")

    passed = session.report.get("completed") and not session.report.get("cleanup_error")
    if passed:
        print("预检完成，尚未调用 AutoGLM。" if args.preflight else "已验证副屏到达关于手机页面，本次副屏已关闭。", flush=True)
    if passed and not args.preflight and sys.stdin.isatty():
        try:
            session.report["human_observation"] = input("主屏持续打字是否受影响？输入 正常 / 白屏 / 收起 / 丢字 / 未观察：").strip()
            session.save()
        except (KeyboardInterrupt, EOFError):
            pass
    print(f"记录：{session.output / 'result.json'}", flush=True)
    return 0 if passed else 1


def reject_takeover(message):
    raise RuntimeError("本轮不自动接管主屏")


def interrupt_on_signal(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description="AutoGLM + 同机焦点隔离联调；仅浏览系统设置。")
    parser.add_argument("--project-dir", default=os.environ.get("WELLPHONE_PROJECT_DIR", "/Users/yishanma/Desktop/wellphone"))
    parser.add_argument("--serial", help="多设备时显式指定手机")
    parser.add_argument("--preflight", action="store_true", help="不调用模型，检查设置/关于手机/返回的实时截图")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.max_steps <= 20:
        parser.error("--max-steps 必须在 1–20 之间")
    project = Path(args.project_dir).expanduser().resolve()
    venv = project / "scrcpyvenv"
    if Path(sys.prefix).resolve() != venv.resolve():
        python = venv / "bin" / "python"
        if not python.is_file():
            parser.error(f"找不到项目虚拟环境：{python}")
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.path.insert(0, str(project))
    server = Path(__file__).resolve().parent / "focus_experiment" / "scrcpy-server-focus"
    if not server.is_file() or hashlib.sha256(server.read_bytes()).hexdigest() != SERVER_SHA256:
        parser.error("实验服务端缺失或哈希不匹配，未操作手机。")
    args.server_path = str(server)
    client = Path(__file__).resolve().parent / "frame_stream" / "scrcpy-live"
    if not client.is_file() or hashlib.sha256(client.read_bytes()).hexdigest() != CLIENT_SHA256:
        parser.error("低延迟 scrcpy 客户端缺失或哈希不匹配，未操作手机。")
    args.client_path, args.live_mkv = str(client), True
    args.require_focus_flags, args.trace_focus = True, True
    args.ime_policy, args.no_system_decorations = "local", False
    args.output_prefix, args.window_title = "autoglm", "Wellphone-Agent-E2E"
    signal.signal(signal.SIGTERM, interrupt_on_signal)
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
