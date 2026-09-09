#!/usr/bin/env python3
"""Interactive two-shot ASCII probe, separate from AutoGLM and baseline Type.

No IME switch, clipboard, clear, Enter, retries, or arbitrary input strings.
"""
from __future__ import annotations

import argparse
import hashlib
import signal
import sys
import time
from pathlib import Path

from ascii_focus_guard import activity_target, focused_window, editor_evidence, client_dump_metadata
from test_virtual_keyboard import KeyboardProbe
from run_autoglm_focus import CLIENT_SHA256, SERVER_SHA256, interrupt_on_signal


class AsciiProbe(KeyboardProbe):
    introduction = ("定向 ASCII 专项：仅先发 7，人工确认后再追加 abc123；不调用模型、不切输入法。\n"
                    "手机请持续输入汉字，不要手动输入 7 或 abc123，以便发现串屏。不要发送短信。\n"
                    "不要在电脑副屏手动点击/输入；看到异常立即 Ctrl+C。设置可能自行记录搜索历史。")
    success_message = "本轮固定 ASCII 输入通过；尚未验证中文、通用 Type 或其他应用。"

    def __init__(self, args):
        super().__init__(args)
        self.report.update(mode="virtual_fixed_ascii_probe", text_input_enabled=True,
                           allowed_payloads=["7", "abc123"], input_attempts=[], input_verifications=[])

    def read_target(self):
        self.require_settings()
        target = activity_target(self.shell("dumpsys", "activity", "activities"), self.display_id)
        window = focused_window(self.shell("dumpsys", "window", "displays"), target)
        return {**target, **window}

    def checked_stage(self, name, action, inspect_secondary=False):
        errors = []

        def observed_action():
            try:
                action()
            except Exception as exc:
                errors.append(exc)
                self.report["stages"][-1]["action_error"] = str(exc)
                self.save()
                print(f"本阶段停止动作：{exc}\n请仍然记录主屏键盘观察；不会执行下一步。", flush=True)

        super().checked_stage(name, observed_action, inspect_secondary)
        if errors:
            raise errors[0]

    def require_focused_editor(self):
        evidence = {"started_ns": time.time_ns()}
        self.report.setdefault("editor_checks", []).append(evidence)
        try:
            self.require_unchanged_main()
            target = self.read_target()
            evidence["target"] = target
            # -d and an exact ActivityRecord token avoid dumping the primary UI.
            dump = self.shell("dumpsys", "activity", "-c", "-p", "com.android.settings",
                              "-d", self.display_id, target["activity_token"])
            evidence["client_dump_metadata"] = client_dump_metadata(dump)
            evidence.update(editor_evidence(dump, target))
            if evidence["status"] != "focused_editor":
                raise RuntimeError("副屏未确认存在唯一可见、启用且已获焦点的编辑框；没有发送文字。")
            self.require_unchanged_main()
            if self.read_target() != target:
                raise RuntimeError("检查期间副屏 Activity/窗口变化；没有发送文字。")
            evidence["checked_ns"] = time.time_ns()
            return evidence
        except Exception as exc:
            evidence["error"] = str(exc)
            raise
        finally:
            self.save()

    def focus_search_editor(self):
        if not self.report.get("search_page_confirmed"):
            raise RuntimeError("尚未人工确认搜索页；拒绝坐标点击。")
        self.require_unchanged_main()
        self.read_target()
        started = time.time_ns()
        # Native 1080x2400 screenshot B_search_keyboard.png, 2026-09-08 13:26 run.
        # Search editor is at the top of the SEARCH page, not the homepage y=410.
        self.shell("input", "-d", self.display_id, "tap", 540, 75)
        self.save_native("C_editor_focused.png", started)

    def send_fixed(self, payload):
        attempts = self.report["input_attempts"]
        if not self.report.get("empty_editor_confirmed"):
            raise RuntimeError("尚未确认空白搜索框；拒绝输入。")
        if payload == "7":
            allowed = not attempts
        elif payload == "abc123":
            allowed = (len(attempts) == 1 and attempts[0].get("payload") == "7"
                       and attempts[0].get("command_returned") is True
                       and self.report["input_verifications"] == [{"expected": "7", "confirmed": True}])
        else:
            allowed = False
        if not allowed:
            raise RuntimeError("只允许先输入一次 7，确认后追加一次 abc123；拒绝其他文字、重试或跳步。")
        self.require_focused_editor()
        # Persist the attempt BEFORE invoking input: timeout is an unknown outcome,
        # never permission to send a second copy of the same text.
        attempt = {"payload": payload, "display_id": self.display_id, "started_ns": time.time_ns(),
                   "command_returned": False}
        attempts.append(attempt)
        self.save()
        try:
            self.shell("input", "keyboard", "-d", self.display_id, "text", payload)
            attempt["command_returned"] = True  # Not a delivery/success assertion.
        except Exception as exc:
            attempt["error"] = str(exc)
            print("输入命令结果不确定；不会重试。请检查两块屏幕，结果不计通过。", flush=True)
            raise
        finally:
            self.save()
        self.save_native("D_single_digit.png" if payload == "7" else "E_ascii.png", attempt["started_ns"])

    def verify_input(self, expected):
        # No user-provided text is stored; only the fixed expectation and a boolean.
        answer = input(f"副屏搜索框完整内容是否恰为 {expected}，且手机主屏完全没有混入这些字符？"
                       "仅两项都确认输入 yes；其他答案均停止：").strip().lower()
        confirmed = answer == "yes"
        self.report["input_verifications"].append({"expected": expected, "confirmed": confirmed})
        self.save()
        if not confirmed:
            raise RuntimeError("文字落点或主屏未受干扰尚未确认；停止下一次输入。")

    def perform_stages(self):
        self.checked_stage("A 创建副屏并打开设置首页", self.setup_settings)
        if input("电脑副屏是设置首页，顶部搜索框可见且未滚动？输入 yes 继续：").strip().lower() != "yes":
            raise RuntimeError("未确认设置首页，不点击。")
        self.report["search_position_confirmed"] = True
        self.checked_stage("B 进入搜索页（此时不输入文字）", self.click_search)
        print("判断搜索页：左上返回箭头、顶部‘搜索设置项’，下方‘搜索历史’。不要求软键盘弹出。")
        if input("副屏符合上述搜索页布局？输入 yes 继续：").strip().lower() != "yes":
            raise RuntimeError("未确认搜索页，不继续。")
        self.report["search_page_confirmed"] = True
        self.checked_stage("C 点击副屏顶部编辑框（540,75；仍不输入文字）", self.focus_search_editor)
        if input("副屏搜索框当前为空，仅有‘搜索设置项’提示文字？输入 yes 继续：").strip().lower() != "yes":
            raise RuntimeError("未确认空框；不自动清空已有内容。")
        self.report["empty_editor_confirmed"] = True
        self.checked_stage("D 检查焦点后仅向副屏输入 7", lambda: self.send_fixed("7"))
        self.verify_input("7")
        self.checked_stage("E 向副屏追加 abc123，不清空、不按回车", lambda: self.send_fixed("abc123"))
        self.verify_input("7abc123")
        self.checked_stage("F 关闭本次副屏", self.stop_display)


def main():
    parser = argparse.ArgumentParser(description="定向 ASCII 专项：7 → 人工确认 → abc123；不切输入法")
    parser.add_argument("--serial")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("请在 Terminal 交互运行；用户准备好之前不会操作手机。")
    root = Path(__file__).resolve().parent
    for path, digest in [(root / "focus_experiment/scrcpy-server-focus", SERVER_SHA256),
                         (root / "frame_stream/scrcpy-live", CLIENT_SHA256)]:
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            parser.error("实验二进制缺失或哈希不匹配，尚未操作手机。")
    args.server_path = str(root / "focus_experiment/scrcpy-server-focus")
    args.client_path = str(root / "frame_stream/scrcpy-live")
    args.require_focus_flags, args.trace_focus, args.live_mkv = True, True, True
    args.ime_policy, args.no_system_decorations = "local", False
    args.output_prefix, args.window_title = "ascii-input", "Wellphone-ASCII-Input-Test"
    signal.signal(signal.SIGTERM, interrupt_on_signal)
    return AsciiProbe(args).run_probe()


if __name__ == "__main__":
    raise SystemExit(main())
