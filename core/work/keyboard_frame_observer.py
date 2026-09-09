"""Bounded frame diagnostics for keyboard tests, independent of UI quiescence."""
import hashlib
import os
import time
from io import BytesIO

from PIL import Image


def read_native(path):
    # ffmpeg atomically replaces the path. Read bytes and metadata from the same
    # open inode, not a stat(path) followed by a potentially different frame.
    with path.open("rb") as handle:
        info = os.fstat(handle.fileno())
        data = handle.read()
    with Image.open(BytesIO(data)) as picture:
        if picture.format != "PNG" or picture.size != (1080, 2400):
            raise ValueError(f"Unexpected native frame: {picture.format}, {picture.size}")
        pixels = picture.convert("RGB").tobytes()  # Also force a complete decode.
    return data, info.st_mtime_ns, hashlib.sha256(pixels).hexdigest()


def observe_frames(stream, not_before_ns=0, timeout=5.0, sample_seconds=1.0):
    """Require a valid post-action file; observe rewrites, never require silence.

    File write time is local delivery evidence, not a device capture timestamp.
    No frame is fed to a model by this diagnostic-only helper.
    """
    started = time.monotonic()
    deadline = started + timeout
    sample_until = None
    latest = None
    version = None
    last_write_seen = None
    quiet_max = 0.0
    pixel_hashes = set()
    report = {"status": "no_fresh_frame", "not_before_ns": not_before_ns,
              "fresh_frame_received": False, "events": [], "read_errors": [],
              "observed_file_updates": 0, "observed_content_variants": 0}
    while time.monotonic() < deadline:
        try:
            stream.assert_live()
        except Exception as exc:
            report.update(status="producer_error", producer_error=str(exc))
            latest = None  # Never accept old evidence from a dead decoder.
            break
        now = time.monotonic()
        if sample_until is not None and now >= sample_until:
            break
        try:
            data, mtime_ns, pixel_hash = read_native(stream.frame_path)
            current = (mtime_ns, len(data))
            if current != version:
                version = current
                if last_write_seen is not None:
                    quiet_max = max(quiet_max, now - last_write_seen)
                last_write_seen = now
                report["observed_file_updates"] += 1
                pixel_hashes.add(pixel_hash)
                fresh = mtime_ns >= not_before_ns
                report["events"].append({"elapsed_ms": round((now - started) * 1000),
                                         "mtime_ns": mtime_ns, "bytes": len(data),
                                         "pixel_sha256": pixel_hash, "post_action_write": fresh})
                if fresh:
                    latest = data
                    report["fresh_frame_received"] = True
                    if sample_until is None:
                        report["first_fresh_frame_ms"] = round((now - started) * 1000)
                        sample_until = min(deadline, now + sample_seconds)
        except (OSError, ValueError) as exc:
            # Missing/invalid data is recorded, not silently treated as success.
            message = str(exc)
            if message not in report["read_errors"]:
                report["read_errors"].append(message)
        time.sleep(0.1)
    elapsed = time.monotonic() - started
    quiet_end = max(0, time.monotonic() - last_write_seen) if last_write_seen is not None else 0
    report.update(elapsed_ms=round(elapsed * 1000),
                  observed_content_variants=len(pixel_hashes),
                  max_observed_write_gap_ms=round(max(quiet_max, quiet_end) * 1000),
                  write_quiet_at_end_ms=round(quiet_end * 1000))
    if latest is not None:
        # Re-check process state before returning a usable capture.
        try:
            stream.assert_live()
            report["status"] = "fresh_frame_received"
        except Exception as exc:
            latest = None
            report.update(status="producer_error", producer_error=str(exc))
    return latest, report
