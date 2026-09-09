#!/usr/bin/env python3
"""Explicit local entry points; never silently load the Desktop project's code."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def verify():
    expected = json.loads((ROOT / "baseline/source-sha256.json").read_text())
    bad = [name for name, digest in expected.items()
           if not (ROOT / name).is_file()
           or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    if bad:
        raise RuntimeError("Baseline source changed; review before using baseline: " + ", ".join(bad))
    print(f"基线校验通过：{len(expected)} 个文件与已保存版本一致。", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Wellphone 本机版本化入口")
    parser.add_argument("mode", choices=["baseline", "tests", "verify", "keyboard", "ascii", "router", "douyin", "audio"])
    parser.add_argument("extra", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode in {"baseline", "verify"}:
        verify()
    if args.mode == "verify":
        return
    venv = Path(os.environ.get("WELLPHONE_BASE_VENV", "/Users/yishanma/Desktop/wellphone/scrcpyvenv")).resolve()
    python = venv / "bin/python"
    if not python.is_file():
        raise RuntimeError("找不到基线 Python 环境；设置 WELLPHONE_BASE_VENV。不会自动安装依赖。")
    local_venv = ROOT / "app/scrcpyvenv"
    if local_venv.exists() or local_venv.is_symlink():
        if local_venv.resolve() != venv:
            raise RuntimeError("本目录已有不同的 Python 环境；不会覆盖，请检查 WELLPHONE_BASE_VENV。")
    else:
        local_venv.symlink_to(venv, target_is_directory=True)
    env = os.environ.copy()
    env["WELLPHONE_PROJECT_DIR"] = str(ROOT / "app")
    env["PYTHONPATH"] = str(ROOT / "app") + os.pathsep + str(ROOT / "work")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if args.mode == "tests":
        command = [str(python), "-m", "unittest", "discover", "-s", str(ROOT / "work"), "-p", "*_unit.py", "-v"]
    else:
        script = ROOT / "work" / {"baseline": "run_autoglm_focus.py",
                                  "keyboard": "test_virtual_keyboard.py",
                                  "ascii": "test_virtual_ascii.py",
                                  "router": "run_router.py", "douyin": "run_douyin_test.py",
                                  "audio": "run_audio_test.py"}[args.mode]
        if not script.is_file():
            raise RuntimeError("此目录没有键盘实验；请在 wellphone-keyboard-experiment 中运行。")
        command = [str(python), str(script), *args.extra]
    os.chdir(ROOT)
    os.execve(str(python), command, env)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        raise SystemExit(str(exc))
