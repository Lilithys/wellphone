#!/usr/bin/env python3
"""Create an exclusive local source snapshot; never modify either source tree."""
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path("/Users/yishanma/Desktop/wellphone")
WORKSPACE = Path(__file__).resolve().parent.parent
DEST = Path("/Users/yishanma/Documents/Codex/wellphone-versioned")
EVIDENCE = "autoglm-20260908-110710-85cph8cw"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if DEST.exists():
        raise SystemExit(f"Refusing to overwrite existing destination: {DEST}")
    report = json.loads((WORKSPACE / "outputs" / EVIDENCE / "result.json").read_text())
    if not (report.get("task_verified") and report.get("completed")
            and report.get("virtual_display_removed") and report.get("human_observation") == "正常"):
        raise SystemExit("Expected successful acceptance evidence is missing")
    DEST.mkdir(mode=0o700)
    ignore = shutil.ignore_patterns(".git", ".DS_Store", "__pycache__", "*.pyc", "scrcpyvenv",
                                   "*.egg-info", ".env", ".env.*", "*.pem", "*.key")
    hashes = {}
    for source, relative in [(PROJECT, "app"), (WORKSPACE / "work", "work")]:
        target = DEST / relative
        shutil.copytree(source, target, ignore=ignore)
        for copied in sorted(target.rglob("*")):
            if copied.is_symlink():
                raise RuntimeError(f"Unexpected symlink in snapshot: {copied}")
            if copied.is_file():
                original = source / copied.relative_to(target)
                value = digest(copied)
                if value != digest(original):
                    raise RuntimeError(f"Copy verification failed: {copied}")
                hashes[str(copied.relative_to(DEST))] = value
    metadata = DEST / "baseline"
    metadata.mkdir()
    (metadata / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    python = PROJECT / "scrcpyvenv/bin/python"
    packages = json.loads(subprocess.check_output([str(python), "-m", "pip", "list", "--format=json"], text=True))
    # Do not freeze editable paths, index URLs, environment variables or credentials.
    pinned = sorted(f"{item['name']}=={item['version']}" for item in packages if item["name"] != "phone-agent")
    (metadata / "python-packages.txt").write_text("\n".join(pinned) + "\n")
    summary = {
        "saved_at": datetime.now().isoformat(), "evidence_run": EVIDENCE,
        "task": report["task"], "task_verified": True, "human_observation": "正常",
        "actions": [step["action"]["action"] if "action" in step["action"] else "finish"
                    for step in report["steps"]],
        "focus_samples": len(report["focus_trace"]),
        "all_samples_primary_focus": all(x.get("top_focused_display_id") == 0 for x in report["focus_trace"]),
        "all_samples_primary_ime": all("mCurTokenDisplayId=0" in x.get("ime_state", []) for x in report["focus_trace"]),
        "all_samples_keyboard_shown": all("mInputShown=true" in x.get("ime_state", []) for x in report["focus_trace"]),
        "virtual_display_removed": True, "model_steps": len(report["steps"]),
        "client_sha256": report["client_sha256"], "server_sha256": report["server_sha256"],
        "python": subprocess.check_output([str(python), "--version"], text=True).strip(),
        "source_files_verified": len(hashes),
        "limitations": "Local source baseline, not a phone or macOS image; agent text input remains disabled.",
    }
    (metadata / "acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    private = DEST / "private_evidence"
    private.mkdir(mode=0o700)
    shutil.copytree(WORKSPACE / "outputs" / EVIDENCE, private / EVIDENCE)
    print(json.dumps({"destination": str(DEST), "verified_source_files": len(hashes),
                      "evidence": str(private / EVIDENCE), "api_key_copied": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
