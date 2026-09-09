"""Opt-in display comparison and supervised messages handoff; no draft/send path."""
import hashlib
from pathlib import Path
import re
from douyin_stream_trial import require_stream_trial_scope


NON_PRESENTATION_SHA256 = "556bebcba8c260956b674750dc7a10c1c52af2740616703142caf28c04466542"
NON_PRESENTATION_RELATIVE = "work/display_experiment/scrcpy-server-non-presentation"


def require_comparison_scope(args):
    require_stream_trial_scope(args)
    flow = getattr(args, "send_one_flow", False)
    if getattr(args, "editor_read_mode", "activity-token") != "activity-token" and not flow:
        raise RuntimeError("新的副屏编辑框读取路径仅用于固定数字完整流程。")
    if getattr(args, "allow_editor_reobserve", False) and not flow:
        raise RuntimeError("编辑框只读重查仅允许在固定数字完整流程中启用。")
    if flow and (getattr(args, "startup_only", False) or getattr(args, "preflight", False)
                 or not getattr(args, "non_presentation", False) or not getattr(args, "confirm_home_first", False)
                 or not getattr(args, "auto_messages", False) or getattr(args, "step_by_step", False)
                 or getattr(args, "message", "1") != "1"):
        raise RuntimeError("--send-one-flow 仅允许非展示屏+人工首页门槛+自动消息入口的一条数字1流程，不兼容其他模式或内容。")
    if getattr(args, "auto_messages", False):
        if (not getattr(args, "non_presentation", False) or not (getattr(args, "startup_only", False) or flow)
                or not getattr(args, "confirm_home_first", False) or getattr(args, "preflight", False)
                or getattr(args, "step_by_step", False)):
            raise RuntimeError("--auto-messages 仅与 --startup-only --confirm-home-first --non-presentation 合用；不兼容预检/逐步确认，不开放输入或发送。")
    if not getattr(args, "non_presentation", False):
        return
    preflight = getattr(args, "preflight", False)
    startup = getattr(args, "startup_only", False) or flow
    home_gate = getattr(args, "confirm_home_first", False)
    if not ((preflight and not startup and not home_gate) or (not preflight and startup and home_gate)):
        raise RuntimeError("--non-presentation 仅允许 --preflight，或 --startup-only --confirm-home-first；不开放通用导航、输入或发送。")


def select_server(root, args):
    require_comparison_scope(args)
    if not getattr(args, "non_presentation", False):
        return Path(root) / "work/focus_experiment/scrcpy-server-focus"
    path = Path(root) / NON_PRESENTATION_RELATIVE
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != NON_PRESENTATION_SHA256:
        raise RuntimeError("独立非展示屏服务端缺失或哈希不符；不回退到其他服务端。")
    return path


def verify_non_presentation(session):
    """Pinned server reads actual Display flags before publishing the display ID."""
    if not getattr(session.args, "non_presentation", False):
        return
    session.report["non_presentation_verified"] = False
    if type(session.display_id) is not int or session.display_id <= 0:
        raise RuntimeError("非展示屏验证没有有效的副屏ID。")
    if not session.focus_flags_verified.is_set():
        raise RuntimeError("原有焦点隔离尚未确认，禁止启动应用。")
    rows = []
    for line in session.scrcpy_log:
        match = re.fullmatch(r"(?:\[server\] )?INFO: Wellphone non-presentation VERIFIED: display=(\d+) "
                             r"actual=0x([0-9a-f]+) presentation_mask=0x([0-9a-f]+)", line.strip())
        if match:
            rows.append(tuple(int(value, 10 if index == 0 else 16) for index, value in enumerate(match.groups())))
    if len(rows) != 1 or rows[0][0] != session.display_id:
        raise RuntimeError("没有唯一匹配本次副屏的非展示屏回读，禁止启动应用。")
    display_id, actual, mask = rows[0]
    if mask <= 0 or mask & (mask - 1) or actual & mask:
        raise RuntimeError("系统未确认去掉PRESENTATION标记，禁止启动应用。")
    session.report.update(non_presentation_verified=True,
                          display_category_check={"display_id": display_id, "actual_flags": hex(actual),
                              "presentation_mask": hex(mask), "presentation": False,
                              "source": "pinned_server_actual_display_flags"})
    session.save()
