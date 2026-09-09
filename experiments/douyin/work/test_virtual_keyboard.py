#!/usr/bin/env python3
"""Phase 1: request the secondary display keyboard without typing or switching IME."""
from __future__ import annotations

import argparse
import hashlib
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from test_keyboard_isolation import KeyboardTest, activity_on_display
from scrcpy_frames import FrameStream
from keyboard_frame_observer import observe_frames
from run_autoglm_focus import CLIENT_SHA256, SERVER_SHA256, normalize_component, interrupt_on_signal


def require_primary_keyboard(state):
    if state.get("top_focused_display_id") != 0:
        raise RuntimeError("主屏顶层焦点未确认；不执行下一步。")
    ime = state.get("ime_state", [])
    if "mCurTokenDisplayId=0" not in ime or "mInputShown=true" not in ime:
        raise RuntimeError("主屏键盘未保持显示/归属主屏；不执行下一步。")


class KeyboardProbe(KeyboardTest):
    introduction = "仅测试点击副屏输入框和返回：不调用模型，不输入文字，不修改默认输入法。"
    success_message = "输入框专项通过；仍未验证任何副屏文字输入。"

    def __init__(self, args):
        super().__init__(args)
        self.frames = None
        self.original_ime = None
        self.report.update(mode="virtual_keyboard_request_only", model_called=False,
                           text_input_enabled=False, changes_default_ime=False)

    def read_default_ime(self):
        value = self.shell("settings", "get", "secure", "default_input_method").strip()
        if not value or value == "null":
            raise RuntimeError("无法读取当前默认输入法；不会主动切换输入法。")
        return value

    def require_unchanged_main(self):
        require_primary_keyboard(self.read_focus())
        if self.read_default_ime() != self.original_ime:
            raise RuntimeError("默认输入法发生变化，停止；脚本不会自动改回或覆盖用户选择。")
        main = activity_on_display(self.shell("dumpsys", "activity", "activities"), 0)
        if main.startswith("com.android.settings/"):
            raise RuntimeError("主屏打开了系统设置，停止以避免共享应用状态冲突。")

    def save_native(self, name, not_before_ns=0):
        data, diagnostic = None, {}
        try:
            self.require_settings()
            data, diagnostic = observe_frames(self.frames, not_before_ns=not_before_ns)
            self.require_settings()
            if data is not None:
                path = self.output / name
                path.write_bytes(data)
                self.report.setdefault("frames", []).append(str(path))
        except Exception as exc:
            data = None
            diagnostic.update(status="capture_error", error=str(exc))
        diagnostic.update(name=name, capture_available=data is not None)
        self.report.setdefault("frame_observations", []).append(diagnostic)
        self.save()
        if data is None:
            print("本阶段截图证据不足；先记录你的键盘观察，随后停止下一阶段。", flush=True)
        else:
            print(f"已收到原生帧；观察到 {diagnostic['observed_file_updates']} 次文件更新、"
                  f"{diagnostic['observed_content_variants']} 种像素内容；不要求画面停止刷新。", flush=True)

    def setup_settings(self):
        self.require_unchanged_main()
        self.frames = FrameStream(self.output)
        self.frames.start()
        self.args.record_path = str(self.frames.pipe_path)
        self.start_display()
        started = time.time_ns()
        self.launch_settings()
        self.save_native("A_settings.png", started)

    def click_search(self):
        self.require_unchanged_main()
        self.require_settings()
        current = activity_on_display(self.shell("dumpsys", "activity", "activities"), self.display_id)
        if normalize_component(current) != normalize_component(self.settings_component):
            raise RuntimeError("副屏不是已解析的设置首页，拒绝坐标点击。")
        started = time.time_ns()
        # Verified HONOR Magic3 settings search box for 1080x2400/420.
        # User must visually confirm this box before entering this stage.
        self.shell("input", "-d", self.display_id, "tap", 540, 410)
        self.save_native("B_search_keyboard.png", started)
        print("现在继续在手机上打字 10 秒；不往副屏发送任何文字。", flush=True)
        time.sleep(7)  # KeyboardTest.stage adds another 3 seconds.

    def leave_search(self):
        self.require_unchanged_main()
        started = time.time_ns()
        self.press_back()
        # Back may only dismiss a secondary IME, or may leave search directly.
        # Either is recorded; never send repeated blind Back keys.
        time.sleep(1)
        self.save_native("C_after_back.png", started)

    def checked_stage(self, name, action, inspect_secondary=False):
        capture_start = len(self.report.get("frame_observations", []))
        self.stage(name, action)
        stage = self.report["stages"][-1]
        if stage.get("observation") != "正常":
            raise RuntimeError("本阶段异常或未观察清楚，停止后续动作并关闭本次副屏。")
        if stage.get("focus_trace_incomplete") or not stage.get("focus_trace"):
            raise RuntimeError("焦点采样不完整，不能判定通过。")
        for sample in stage["focus_trace"]:
            require_primary_keyboard(sample)
        self.require_unchanged_main()
        if inspect_secondary:
            self.report["secondary_editor_observation"] = input("副屏是否进入搜索输入界面？输入 是 / 否 / 未看清：").strip()
            self.report["secondary_keyboard_observation"] = input("副屏软键盘：显示 / 不显示 / 未看清：").strip()
            self.save()
            if self.report["secondary_editor_observation"] != "是":
                raise RuntimeError("没有确认进入副屏搜索输入界面，不能判定输入框测试通过。")
        captures = self.report.get("frame_observations", [])[capture_start:]
        if any(not item["capture_available"] for item in captures):
            raise RuntimeError("键盘观察已记录，但本阶段新帧证据不完整；停止后续动作，不判定键盘隔离失败。")

    def perform_stages(self):
        self.checked_stage("A 创建副屏并打开设置首页", self.setup_settings)
        print("请看电脑副屏：必须是设置首页，顶部‘搜索设置项’完整可见。")
        print("本轮点击该机型已核对的坐标 (540, 410)，并非通用页面识别。")
        if input("确认搜索框位于顶部且页面未滚动？输入 yes 继续，否则停止：").strip().lower() != "yes":
            raise RuntimeError("未确认搜索框位置，尚未点击。")
        self.report["search_position_confirmed"] = True
        self.checked_stage("B 点击副屏搜索框并观察主屏键盘", self.click_search, inspect_secondary=True)
        self.checked_stage("C 对副屏发送一次返回", self.leave_search)
        self.checked_stage("D 关闭本次副屏", self.stop_display)

    def run_probe(self):
        try:
            self.prepare()
            self.original_ime = self.read_default_ime()
            self.report["default_ime_before"] = self.original_ime
            print(self.introduction)
            print("主屏用短信草稿持续输入，不发送消息。每阶段回车后有 5 秒准备。")
            self.perform_stages()
            self.report["completed"] = True
        except (EOFError, KeyboardInterrupt):
            self.report["interrupted"] = True
            print("已中断，关闭本次实验副屏。", flush=True)
        except Exception as exc:
            self.report["error"] = str(exc)
            print(f"测试停止：{exc}", flush=True)
        finally:
            try:
                self.stop_display()
            except Exception as exc:
                self.report["cleanup_error"] = str(exc)
            if self.frames is not None:
                try:
                    self.frames.stop()
                except Exception as exc:
                    self.report["decoder_cleanup_error"] = str(exc)
            if self.serial:
                self.report["after_cleanup"] = self.snapshot()
                try:
                    self.report["default_ime_after"] = self.read_default_ime()
                except Exception as exc:
                    self.report["ime_read_error"] = str(exc)
            self.report["finished_at"] = datetime.now().isoformat()
            try:
                require_primary_keyboard(self.report.get("after_cleanup", {}))
                if self.report.get("default_ime_after") != self.original_ime or not self.original_ime:
                    raise RuntimeError("清理后默认输入法未确认与测试前一致。")
            except RuntimeError as exc:
                self.report["post_cleanup_check_error"] = str(exc)
            self.save()
            (self.output / "scrcpy.log").write_text("\n".join(self.scrcpy_log), encoding="utf-8")
        passed = (self.report.get("completed") and self.report.get("virtual_display_removed")
                  and not self.report.get("cleanup_error") and not self.report.get("decoder_cleanup_error")
                  and not self.report.get("post_cleanup_check_error"))
        print(self.success_message if passed else "本轮未通过；基线代码没有改动。", flush=True)
        print(f"记录：{self.output / 'result.json'}", flush=True)
        return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description="副屏搜索框/键盘请求专项，不输入文字")
    parser.add_argument("--serial")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("需要在 Terminal 交互运行，确保用户已准备好持续打字；尚未操作手机。")
    root = Path(__file__).resolve().parent
    for path, digest in [(root / "focus_experiment/scrcpy-server-focus", SERVER_SHA256),
                         (root / "frame_stream/scrcpy-live", CLIENT_SHA256)]:
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            parser.error("实验二进制缺失或哈希不匹配，尚未操作手机。")
    args.server_path = str(root / "focus_experiment/scrcpy-server-focus")
    args.client_path = str(root / "frame_stream/scrcpy-live")
    args.require_focus_flags, args.trace_focus, args.live_mkv = True, True, True
    args.ime_policy, args.no_system_decorations = "local", False
    args.output_prefix, args.window_title = "keyboard-input", "Wellphone-Keyboard-Input-Test"
    signal.signal(signal.SIGTERM, interrupt_on_signal)
    return KeyboardProbe(args).run_probe()


if __name__ == "__main__":
    raise SystemExit(main())
