#!/usr/bin/env python3
"""Archive only clean Git-tracked files; never copy live outputs or symlinks."""
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "wellphone-delivery-v1"
MANIFEST = "RELEASE-MANIFEST.json"
BLOCKED_PARTS = {".git", "outputs", "private_evidence", "__pycache__", "scrcpyvenv", ".venv", "dist"}
SECRET = re.compile(rb"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def tracked_files():
    names = [name.decode() for name in git("ls-files", "-z").split(b"\0") if name]
    for name in names:
        parts = PurePosixPath(name).parts
        if (PurePosixPath(name).is_absolute() or ".." in parts or BLOCKED_PARTS.intersection(parts)
                or any(part == ".env" or part.startswith(".env.") for part in parts)
                or name.endswith((".key", ".pem", ".sqlite", ".sqlite3", ".pyc", ".pyo"))):
            raise RuntimeError("Refusing private/runtime path: " + name)
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Refusing non-regular tracked file: " + name)
        if SECRET.search(path.read_bytes()):
            raise RuntimeError("Review possible secret in: " + name)  # Never print matching value.
    return names


def main():
    if git("status", "--porcelain").strip():
        raise RuntimeError("Commit the reviewed release changes before packaging.")
    names = tracked_files()
    commit = git("rev-parse", "HEAD").decode().strip()
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
              for name in names if name != MANIFEST}
    manifest = {"release": RELEASE, "source_commit": commit,
                "scope": "Git-tracked source only; excludes runtime state, credentials, private screenshots and .git",
                "secret_scan": "Heuristic common token/private-key scan, not a guarantee of full anonymization",
                "files": hashes}
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / (RELEASE + ".zip")
    if archive.exists():
        raise RuntimeError("Archive already exists; choose a new release directory/version, do not overwrite.")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for name in names:
            if name != MANIFEST:
                out.write(ROOT / name, RELEASE + "/" + name)
        out.writestr(RELEASE + "/" + MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise RuntimeError("Archive CRC validation failed")
        for name, expected in hashes.items():
            if hashlib.sha256(check.read(RELEASE + "/" + name)).hexdigest() != expected:
                raise RuntimeError("Archive content mismatch: " + name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / (RELEASE + ".zip.sha256")).write_text(digest + "  " + archive.name + "\n")
    (dist / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"archive": str(archive), "sha256": digest, "source_commit": commit,
                      "tracked_file_count": len(hashes), "crc_and_hashes_verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
