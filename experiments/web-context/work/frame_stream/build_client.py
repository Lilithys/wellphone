#!/usr/bin/env python3
"""Client-only macOS/Homebrew fallback build, no global installs or server rebuild."""
import argparse
import platform
import re
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path, help="scrcpy 4.1 source with live-mkv.patch applied")
parser.add_argument("output", type=Path, help="new binary path (must not exist)")
args = parser.parse_args()
if platform.system() != "Darwin" or args.output.exists():
    parser.error("macOS only; output must not already exist")
app = args.source.resolve() / "app"
meson = (app / "meson.build").read_text()
sources = re.findall(r"'([^']+\.c)'", meson.split("src = [", 1)[1].split("]", 1)[0])
sources += ["src/sys/unix/file.c", "src/sys/unix/process.c"]
subprocess.run([
    "clang", "-std=c11", "-O2", "-DNDEBUG", "-D_GNU_SOURCE", "-D_POSIX_C_SOURCE=200809L",
    "-D_XOPEN_SOURCE=700", "-D_DARWIN_C_SOURCE",
    "-I" + str(Path(__file__).resolve().parent), "-I" + str(app / "src"),
    "-I/opt/homebrew/include", *[str(app / name) for name in sources],
    "-L/opt/homebrew/lib", "-lavformat", "-lavcodec", "-lavutil", "-lswresample", "-lSDL3",
    "-o", str(args.output.resolve()),
], check=True)
subprocess.run([str(args.output.resolve()), "--version"], check=True)
