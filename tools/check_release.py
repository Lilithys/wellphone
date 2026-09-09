#!/usr/bin/env python3
"""Read-only local integrity/dependency checks. No network, API, or phone IO."""
import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ("core", "experiments/douyin", "experiments/web-context")


def check_files():
    failures = []
    count = 0
    for folder in SNAPSHOTS:
        base = ROOT / folder
        manifest = json.loads((base / "baseline/source-sha256.json").read_text())
        for name, expected in manifest.items():
            path = base / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                failures.append(str(Path(folder) / name))
            count += 1
    release_manifest = ROOT / "RELEASE-MANIFEST.json"
    if release_manifest.is_file():
        data = json.loads(release_manifest.read_text())
        for name, expected in data["files"].items():
            path = ROOT / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                failures.append(name)
    return count, sorted(set(failures))


def check_dependencies():
    failures = []
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        failures.append("Bundled client is macOS arm64 only; this platform was not accepted.")
    for binary in ("adb", "scrcpy", "ffmpeg"):
        if shutil.which(binary) is None:
            failures.append(f"Missing executable: {binary}")
    if platform.system() == "Darwin" and shutil.which("otool"):
        proc = subprocess.run(["otool", "-L", str(ROOT / "experiments/douyin/work/frame_stream/scrcpy-live")],
                              capture_output=True, text=True, timeout=10)
        if proc.returncode:
            failures.append("Cannot inspect bundled client dependencies.")
        else:
            for row in proc.stdout.splitlines()[1:]:
                library = row.strip().split(" (", 1)[0]
                if library.startswith("/opt/homebrew/") and not Path(library).is_file():
                    failures.append(f"Missing dynamic library: {library}")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files-only", action="store_true", help="Do not inspect local external dependencies")
    args = parser.parse_args()
    count, failures = check_files()
    print(f"Frozen file checks: {count} across 3 snapshots (93 per snapshot).")
    if not args.files_only:
        failures.extend(check_dependencies())
    for problem in failures:
        print("FAIL:", problem)
    print("PASS: no phone/model operations performed." if not failures else "Not ready; do not bypass checks.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
