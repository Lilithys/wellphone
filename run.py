#!/usr/bin/env python3
"""Natural-language entry for the preserved AutoGLM-action fixed-digit flow."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from delivery_task import parse_task

ROOT = Path(__file__).resolve().parent
DOUYIN_ENTRY = ROOT / "experiments" / "douyin" / "run.py"


def step_limit(value):
    number = int(value)
    if not 1 <= number <= 20:
        raise argparse.ArgumentTypeError("旧执行器最多允许1–20步。")
    return number


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='副屏抖音旧动作协议：AutoGLM导航，保留原核对。例：在抖音给“联系人昵称”发送“1”')
    modes = parser.add_subparsers(dest="mode", required=True)
    douyin = modes.add_parser("douyin", help="向指令明确指定的已有一对一联系人发送一条消息")
    douyin.add_argument("task", help="明确联系人；本入口只支持旧基线的数字1")
    douyin.add_argument("--serial", help="ADB序列号；省略时检查唯一设备")
    douyin.add_argument("--max-steps", type=step_limit, default=20)
    douyin.add_argument("--dry-run", action="store_true", help="只解析展示目标，不连接手机或模型")
    modes.add_parser("tests", help="入口、解析和抖音离线测试")
    verify = modes.add_parser("verify", help="只检查文件和本机依赖")
    verify.add_argument("--files-only", action="store_true")
    audio = modes.add_parser("audio", help="只恢复本副本记录的抖音音频原值")
    audio.add_argument("--restore", action="store_true", required=True)
    audio.add_argument("--serial")
    argv = list(argv)
    if argv and argv[0] not in {"douyin", "tests", "verify", "audio", "-h", "--help"}:
        if not argv[0].startswith("-"):
            argv.insert(0, "douyin")
    args = parser.parse_args(argv)
    if args.mode == "douyin":
        try:
            args.parsed_task = parse_task(args.task)
            if args.parsed_task.message != "1":
                raise ValueError("本入口回到旧基线，只支持发送数字1；未连接手机。")
        except ValueError as exc:
            parser.error(str(exc))
    return args


def build_command(args):
    if args.mode == "douyin":
        command = [sys.executable, str(DOUYIN_ENTRY), "douyin",
                   "--send-one-flow", "--confirm-home-first",
                   "--non-presentation", "--auto-messages", "--low-fps-trial",
                   "--editor-read-mode", "activity-list",
                   "--reviewer", "user", "--message", "1",
                   "--max-steps", str(args.max_steps)]
    elif args.mode == "audio":
        command = [sys.executable, str(DOUYIN_ENTRY), "audio", "--restore"]
    elif args.mode == "verify":
        command = [sys.executable, str(ROOT / "tools" / "check_release.py")]
        if args.files_only:
            command.append("--files-only")
    elif args.mode == "tests":
        command = [sys.executable, str(DOUYIN_ENTRY), "tests"]
    else:
        raise ValueError("Unsupported delivery mode")
    if getattr(args, "serial", None):
        command.extend(["--serial", args.serial])
    return command


def child_environment(args):
    """Pass only this task's recipient; never persist or change the parent's env."""
    env = os.environ.copy()
    env.pop("WELLPHONE_DOUYIN_RECIPIENT", None)
    if args.mode == "douyin":
        env["WELLPHONE_DOUYIN_RECIPIENT"] = args.parsed_task.recipient
    return env


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.mode == "douyin" and args.dry_run:
        print(json.dumps({"recipient": args.parsed_task.recipient, "message": args.parsed_task.message,
                          "execution": "NOT_EXECUTED", "driver": "legacy_autoglm_action_protocol",
                          "operator_review_required": True, "max_model_requests": args.max_steps,
                          "max_steps": args.max_steps}, ensure_ascii=False, indent=2))
        return 0
    if args.mode == "tests":
        result = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"],
            cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    command = build_command(args)
    if args.mode == "douyin":
        print("已选择旧版AutoGLM动作协议：模型提出受限导航动作；保留首页、输入和发送核对。", flush=True)
    os.execve(sys.executable, command, child_environment(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
