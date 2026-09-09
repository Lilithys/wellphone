#!/usr/bin/env python3
"""Private, memory-only credential handoff to a fixed local test supervisor.

No shell commands/RPC are accepted through the pipe, only one model key.
The operator's own PTY controls run/quit; every child keeps the existing guards.
"""
import argparse
import json
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
LIMIT = 512  # POSIX minimum PIPE_BUF (also this Mac's value): one atomic write.


def encode_key(key):
    key = key.strip()
    if not key or key in {"...", "EMPTY"} or any(c in key for c in "\r\n\0"):
        raise RuntimeError("当前终端没有有效的 PHONE_AGENT_API_KEY；不会回显密钥。")
    payload = (json.dumps({"api_key": key}) + "\n").encode("utf-8")
    if len(payload) > LIMIT:
        raise RuntimeError("密钥交接长度不合法；不会回显内容。")
    return payload


def check_private_pipe(path):
    path = Path(path)
    parent, node = path.parent.lstat(), path.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
            or not stat.S_ISFIFO(node.st_mode) or node.st_uid != os.getuid()
            or stat.S_IMODE(node.st_mode) != 0o600):
        raise RuntimeError("交接端点不是本人专用的私有管道；未传递密钥。")
    return node


def send_key(path):
    payload = encode_key(os.environ.get("PHONE_AGENT_API_KEY", ""))
    expected = check_private_pipe(path)
    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise RuntimeError("交接端点发生变化；未传递密钥。")
        if os.write(fd, payload) != len(payload):
            raise RuntimeError("本机交接未完整完成；不自动重试。")
    finally:
        os.close(fd)
    print("密钥已通过本机私有管道交接；未写入普通文件，未回显。之后由测试进程接管。", flush=True)


def receive_key(fd, timeout=600):
    deadline = time.monotonic() + timeout
    payload = b""
    while time.monotonic() < deadline:
        try:
            chunk = os.read(fd, LIMIT + 1)
        except BlockingIOError:
            chunk = b""
        payload += chunk
        if len(payload) > LIMIT:
            raise RuntimeError("交接内容超限；未启动测试。")
        if b"\n" in payload:
            try:
                value = json.loads(payload)
                if set(value) != {"api_key"} or not isinstance(value["api_key"], str):
                    raise ValueError()
                return json.loads(encode_key(value["api_key"]))["api_key"]
            except (ValueError, TypeError, RuntimeError):
                raise RuntimeError("交接内容无效；未启动测试，不记录内容。") from None
        time.sleep(.1)
    raise RuntimeError("本机密钥交接超时；未启动手机任务。")


def test_command(serial):
    return [sys.executable, str(ROOT / "run.py"), "douyin", "--send-one-flow",
            "--confirm-home-first", "--non-presentation", "--auto-messages", "--low-fps-trial",
            "--reviewer", "assistant", "--max-steps", "20", "--serial", serial]


def run_child(serial, env):
    # Do not let subprocess.run() kill the child on Ctrl+C before audio cleanup.
    child = subprocess.Popen(test_command(serial), cwd=ROOT, env=env)
    try:
        return child.wait()
    except KeyboardInterrupt:
        print("请求本轮正常停止，等待副屏及音频恢复；不强杀或自动重跑。", flush=True)
        if child.poll() is None:
            child.terminate()  # Child's existing SIGTERM handler runs cleanup.
        try:
            return child.wait(timeout=60)
        except subprocess.TimeoutExpired:
            raise RuntimeError("清理尚未确认；保留现有恢复记录，不强杀。") from None


def supervise(serial):
    if not sys.stdin.isatty():
        raise RuntimeError("接管进程须有独立交互PTY；不能预填批准答案。")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="wellphone-key-handoff-", dir="/private/tmp") as folder:
        pipe = Path(folder) / "key.pipe"
        os.mkfifo(pipe, 0o600)
        fd = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            print("WAITING_FOR_LOCAL_KEY " + str(pipe), flush=True)
            key = receive_key(fd)
        finally:
            os.close(fd)
        pipe.unlink()  # Only our temporary kernel pipe, never a user file.
    print("KEY_RECEIVED_IN_MEMORY; no phone task has started.", flush=True)
    while True:
        print("OPERATOR_READY: run / quit（15分钟闲置后退出并丢弃内存密钥）", flush=True)
        ready, _, _ = select.select([sys.stdin], [], [], 900)
        if not ready:
            return
        choice = sys.stdin.readline()
        if not choice or choice.strip() == "quit":
            return
        if choice.strip() != "run":
            print("未识别的接管指令；没有执行。", flush=True)
            continue
        env = os.environ.copy()
        env["PHONE_AGENT_API_KEY"] = key
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = run_child(serial, env)
        print(f"TEST_EXIT_CODE={result}; 不自动重跑，已有输入/发送记录仍有效。", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("receive").add_argument("--serial", required=True)
    commands.add_parser("send").add_argument("--pipe", required=True)
    args = parser.parse_args()
    try:
        if args.command == "send":
            send_key(args.pipe)
        else:
            supervise(args.serial)
    except (OSError, RuntimeError, EOFError, KeyboardInterrupt):
        # No arbitrary OS/JSON exception or payload in logs.
        print("本机交接/接管未完成或已退出；未输出密钥。请检查管道是否过期及终端环境。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
