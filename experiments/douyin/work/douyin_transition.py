"""Bounded passive observation after the ONE messages click, never a click retry."""
import time

from douyin_navigation_frame import require_same_messages_regions
from douyin_policy import RegionChanged, require_same_context


def wait_for_navigation_transition(session, home, context, action, *, timeout=20, max_samples=10):
    """Wait until the old homepage navigation no longer matches twice.

    A changed footer is only a reason to ask the model to verify the NEW frame.
    It is NOT messages-page success, nor a source/capture timestamp guarantee.
    Local PNG mtime proves delivery only; it can belong to a queued older frame.
    """
    started = time.monotonic()
    report = {"status": "OBSERVING", "max_seconds": timeout, "max_samples": max_samples,
              "samples": [], "action_replayed": False, "semantic_proof": False}
    session.report["startup_transition"] = report
    changed = 0
    print("消息入口已点击一次；现在只等待副屏画面转换，最多20秒，不重点击、不调用模型。", flush=True)
    try:
        for index in range(max_samples):
            if time.monotonic() - started >= timeout:
                break
            data = session.capture(f"startup-transition-{index+1:02d}.png")
            require_same_context(context, session.last_capture_context)
            try:
                require_same_messages_regions(home, data, action,
                    before_context=context, after_context=session.last_capture_context)
            except RegionChanged:
                changed += 1
                state = "OLD_NAVIGATION_CHANGED"
            else:
                changed = 0
                state = "OLD_NAVIGATION_STILL_MATCHES"
            report["samples"].append({"number": index+1, "state": state,
                                      "elapsed_ms": round((time.monotonic() - started) * 1000)})
            session.save()
            if time.monotonic() - started > timeout:
                break
            if changed >= 2:
                report["status"] = "VISUAL_TRANSITION_NOT_PAGE_VERIFIED"
                session.save()
                return data
            if index + 1 < max_samples:
                time.sleep(min(.5, max(0, timeout - (time.monotonic() - started))))
        report["status"] = "TIMEOUT_NO_VERIFIED_TRANSITION"
        raise RuntimeError("消息点击后尚未观察到可靠画面转换；不把旧首页交给模型重复点击。停止并保留现场，未输入或发送。")
    except Exception as exc:
        if report["status"] == "OBSERVING":
            report.update(status="GUARD_OR_CAPTURE_FAILED", error_type=type(exc).__name__)
        raise
    finally:
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        session.save()
