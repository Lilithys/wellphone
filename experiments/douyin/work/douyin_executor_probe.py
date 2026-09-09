"""Interactive executor test: no AutoGLM client, no model success claims.

The operator sees the current secondary PNG before proposing one bounded action.
Existing startup/conversation/input/send guards remain the dispatch authority.
"""
import json

from douyin_conversation import ConversationActions, execute_fixed_one
from douyin_policy import RECIPIENT, require_same_context
from douyin_startup import StartupActions
from douyin_transition import wait_for_navigation_transition


def require_probe_scope(args):
    if not getattr(args, "executor_test", False):
        return
    if (not all(getattr(args, name, False) for name in
                ("send_one_flow", "confirm_home_first", "non_presentation", "auto_messages", "low_fps_trial"))
            or getattr(args, "preflight", False) or getattr(args, "startup_only", False)
            or getattr(args, "step_by_step", False) or getattr(args, "message", "1") != "1"):
        raise RuntimeError("执行器单测仅支持已限定的非展示屏低帧率单数字完整流程。")


def read_proposal():
    raw = input('本次新图的一步提案JSON（仅Tap/Swipe；其他取消）：')
    if len(raw) > 200:
        raise RuntimeError("提案过长；未执行。")
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate")
            value[key] = item
        return value
    try:
        action = json.loads(raw, object_pairs_hook=unique)
        fields = {"Tap": {"action", "element"}, "Swipe": {"action", "start", "end"}}
        if (not isinstance(action, dict) or action.get("action") not in fields
                or set(action) != fields[action["action"]]):
            raise ValueError("shape")
        for name in set(action) - {"action"}:
            point = action[name]
            if not isinstance(point, list) or len(point) != 2 or any(type(v) is not int or not 0 <= v < 1000 for v in point):
                raise ValueError("point")
    except (ValueError, TypeError):
        raise RuntimeError("不是合法的一步坐标提案；没有执行，不猜测或修复。") from None
    return {"_metadata": "do", **action}


def capture_for_operator(session, name):
    session.reference = session.capture(name)
    session.reference_context = session.last_capture_context
    print(f"只核对本轮副屏新图：{session.output / name}", flush=True)


def run_executor_probe(session, journal, root, delegate, budget):
    require_probe_scope(session.args)
    session.report.update(mode="douyin_executor_probe", model_called=False,
        execution_driver="operator_visual_navigation_and_fixed_input",
        autoglm_end_to_end_verified=False, automatic_navigation=False,
        navigation_semantics="operator_review_of_current_secondary_frame",
        requested_message="1", recipient=RECIPIENT)
    gate = session.report.get("home_gate", {})
    if gate.get("status") != "READY_FOR_MODEL" or gate.get("display_id") != session.display_id:
        raise RuntimeError("执行器单测也必须先核对本次正常首页。")
    require_same_context(gate.get("context"), session.context())
    startup = StartupActions(delegate, session)
    startup.home_ready = True
    capture_for_operator(session, "executor-home.png")
    proposal = read_proposal()
    if proposal.get("action") != "Tap":
        raise RuntimeError("首页仅允许一次底部消息Tap；未执行。")
    session.report.setdefault("executor_proposals", []).append({"phase": "HOME", "action": proposal})
    session.save()
    result = startup.execute(proposal, 1080, 2400)
    if not result.success:
        raise RuntimeError(result.message)
    if not startup.messages_click_attempted:
        raise RuntimeError("没有尝试本轮消息导航，不进入会话。")
    session.reference = wait_for_navigation_transition(session, startup.navigation_before,
        startup.navigation_context, startup.navigation_action)
    session.reference_context = session.last_capture_context
    capture_for_operator(session, "executor-messages.png")
    # This is an operator readiness review, never a fabricated model response.
    result = startup.execute({"_metadata": "finish", "message": "MESSAGES_READY"}, 1080, 2400)
    if not result.success or session.report.get("messages_ready_by_user") is not True:
        raise RuntimeError(result.message or "消息页未核对。")
    session.report["startup_readiness"][-1]["source"] = "operator_layout_review_no_model"
    session.report.update(text_input_enabled=True, messages_allowed=1, allowed_payloads=["1"])
    actions = ConversationActions(delegate, session, journal, root)
    for number in range(1, budget + 1):
        if actions.stage == "CHAT" and actions.focus_attempted:
            execute_fixed_one(actions, session, budget - number + 1)
        capture_for_operator(session, f"executor-conversation-{number:02d}.png")
        print(f"执行器阶段：{actions.stage}；本次对象{RECIPIENT}，不搜索、不改草稿。", flush=True)
        proposal = read_proposal()
        actions.remaining_after_request = budget - number
        session.report.setdefault("executor_proposals", []).append({"phase": actions.stage,
            "number": number, "action": proposal, "source": "operator_current_frame"})
        session.save()
        result = actions.execute(proposal, 1080, 2400)
        if not result.success:
            raise RuntimeError(result.message or "执行器停止；不自动重试。")
        if session.report.get("send_attempted"):
            return
    raise RuntimeError("执行器步骤预算耗尽；不重复输入或发送。")
