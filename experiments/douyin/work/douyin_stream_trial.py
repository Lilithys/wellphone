"""Opt-in source-rate comparison; not a proof of frame capture freshness."""
import os
from pathlib import Path


BASE_CLIENT = "work/frame_stream/scrcpy-live"
TRIAL_CLIENT = "work/display_experiment/scrcpy-low-fps"
SOURCE_MAX_FPS = 5


def require_stream_trial_scope(args):
    if not getattr(args, "low_fps_trial", False):
        return
    startup = bool(getattr(args, "startup_only", False))
    flow = bool(getattr(args, "send_one_flow", False))
    if (startup == flow
            or not getattr(args, "confirm_home_first", False)
            or not getattr(args, "non_presentation", False)
            or not getattr(args, "auto_messages", False)
            or getattr(args, "preflight", False)
            or getattr(args, "step_by_step", False)
            or (flow and getattr(args, "message", "1") != "1")):
        raise RuntimeError("--low-fps-trial 须选 --startup-only 或 --send-one-flow 之一，并同时指定 "
                           "--confirm-home-first --non-presentation --auto-messages；不兼容预检/逐步确认。"
                           "发送实验仍只允许数字1及原有逐阶段人工核验。")


def select_client(root, args):
    require_stream_trial_scope(args)
    root = Path(root)
    if not getattr(args, "low_fps_trial", False):
        return root / BASE_CLIENT
    # The wrapper execs the same frozen binary, preserving PID/signals/env/args.
    # Main verify_source() still verifies that binary before any device access.
    path = root / TRIAL_CLIENT
    if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError("低帧率对照启动器缺失或不可执行；不回退到不限帧率版本。")
    return path


def trial_metadata(args=None):
    if args is not None:
        require_stream_trial_scope(args)
    flow = bool(getattr(args, "send_one_flow", False))
    return {"variant": "source_max_fps_5_trial", "requested_source_max_fps": SOURCE_MAX_FPS,
            "actual_source_fps_verified": False, "capture_latency_verified": False,
            "native_size": [1080, 2400], "decoder": "unchanged_frozen_FrameStream",
            "client": BASE_CLIENT, "launcher": TRIAL_CLIENT,
            "scope": "supervised_one_message_flow" if flow else "home_to_messages_only",
            "input_or_send_enabled": flow, "input_and_send_require_confirmation": flow,
            "full_flow_accepted": False}
