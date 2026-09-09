#!/usr/bin/env python3
"""Run the isolated scrcpy 4.1 focus experiment. No model, account or API key."""
import argparse
import hashlib
from pathlib import Path

from test_keyboard_isolation import KeyboardTest


def main():
    parser = argparse.ArgumentParser(description="实验版焦点隔离测试；原有 scrcpy 保持不变。")
    parser.add_argument("--serial", help="多设备时指定手机")
    parser.add_argument("--probe-only", action="store_true",
                        help="自动创建并关闭无桌面空显示，仅验证标记；不打开应用或发送输入")
    args = parser.parse_args()
    server = Path(__file__).resolve().parent / "focus_experiment" / "scrcpy-server-focus"
    expected = "f837bbb986ea311f02a1328bde88a97399c4bd81c507f86b6d824774cd0d8d78"
    if not server.is_file() or hashlib.sha256(server.read_bytes()).hexdigest() != expected:
        parser.error("实验服务端缺失或校验不一致，未启动手机操作。")
    args.server_path = str(server)
    args.ime_policy = "local"
    args.no_system_decorations = args.probe_only
    args.require_focus_flags = True
    args.trace_focus = not args.probe_only
    return KeyboardTest(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
