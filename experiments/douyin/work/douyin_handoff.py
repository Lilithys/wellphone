"""Human home gate and local-only, secondary-scoped window diagnostics."""
from datetime import datetime
import re
import unicodedata

from douyin_input import target_on_display
from douyin_policy import require_same_context
from run_autoglm_focus import normalize_component


def require_same_app_display(before, after):
    """Only for the pre-model observation→human-confirmed-home boundary."""
    for context in (before, after):
        require_same_context(context, context)  # Validate package, class and nonprimary display.
    if before["display_id"] != after["display_id"]:
        raise RuntimeError("人工确认期间副屏ID改变，不能接力到另一个显示。")


def read_home_observation(session):
    """Explicit aliases, bounded retry; never strip control sequences into approval."""
    aliases = {"1": "home", "home": "home", "0": "missing", "missing": "missing", "q": "cancel"}
    for attempt in range(1, 3):
        raw = input("模型接入前当前状态：1=正常首页，0=缺少导航/不是首页，q=取消：")
        value = aliases.get(raw.strip())
        # No raw input, hashes or escaped strings: users might paste a secret by mistake.
        shape = {"attempt": attempt, "recognized": value or "invalid", "characters": len(raw),
                 "control_characters": sum(unicodedata.category(c) == "Cc" for c in raw),
                 "format_characters": sum(unicodedata.category(c) == "Cf" for c in raw),
                 "non_ascii_characters": sum(ord(c) > 127 for c in raw)}
        session.report.setdefault("home_gate_input_checks", []).append(shape)
        session.save()
        if value is not None:
            return value
        print("未识别确认输入；模型尚未接入，没有执行导航。请手动输入数字1、0或q，不要粘贴。", flush=True)
        if shape["control_characters"] or shape["format_characters"]:
            print("输入含控制/不可见格式字符，未将其删除后当作批准。", flush=True)
    return "invalid"


def window_metadata(dump, target):
    """Whitelist structural fields from one exact window; never persist dump text."""
    headers = list(re.finditer(r"(?m)^[ \t]*Window #\d+ Window\{[^\n]+\}:[ \t]*$", dump))
    matches = []
    for i, header in enumerate(headers):
        match = re.fullmatch(r"\s*Window #\d+ Window\{([0-9a-f]+) u(\d+) ([\w.$]+/[\w.$]+)\}:\s*", header[0])
        if (match and match[1] == target["window_token"] and match[2] == "0"
                and normalize_component(match[3]) == target["component"]):
            matches.append(dump[header.end():headers[i+1].start() if i+1 < len(headers) else len(dump)])
    if len(matches) != 1:
        return {"status": "UNRECOGNIZED_OR_AMBIGUOUS_WINDOW"}
    body = matches[0]
    displays = re.findall(r"\bmDisplayId=(\d+)\b", body)
    if not displays or set(displays) != {str(target["display_id"])}:
        return {"status": "DISPLAY_NOT_CONFIRMED"}
    result = {"status": "SCOPED_WINDOW_FIELDS", "display_id": target["display_id"]}
    for key in ("mHasSurface", "isReadyForDisplay()", "isOnScreen", "isVisible", "mDestroying",
                "mRemoved", "mPolicyVisibility", "mHidden", "mHiddenRequested", "mAppStopped"):
        values = re.findall(r"(?<!\w)" + re.escape(key) + r"=(true|false)\b", body)
        if values and len(set(values)) == 1:
            result[key] = values[0] == "true"
    for key in ("mViewVisibility", "mSystemUiVisibility", "mRequestedVisibleTypes"):
        values = re.findall(r"\b" + key + r"=(0x[0-9a-fA-F]+|\d+)\b", body)
        if values and len(set(values)) == 1:
            result[key] = values[0]
    # These raw system fields are diagnostic hints, not a fullscreen/clear-mode classifier.
    return result


def secondary_diagnostic(session, phase):
    """Runs before teardown, with the original primary/audio/ownership guards."""
    event = {"phase": phase, "time": datetime.now().isoformat(), "model_called": bool(session.report.get("model_called"))}
    session.report.setdefault("handoff_diagnostics", []).append(event)
    try:
        before = session.context()
        captured = session.capture(f"handoff-{phase}.png")
        if phase == "home-confirmed":
            # Same-session human-confirmed home only; never a cross-run template.
            session.confirmed_home_frame = captured
        require_same_context(before, session.last_capture_context)
        event.update(context=before, frame=str(session.output / f"handoff-{phase}.png"))
        activities = session.shell("dumpsys", "activity", "activities")
        displays = session.shell("dumpsys", "window", "displays")
        try:
            target = target_on_display(activities, displays, session.display_id)
        except RuntimeError as exc:
            # Unknown OEM formatting is recorded, not replaced by another window.
            # Only fixed parser reasons are persisted; unknown exception text may
            # contain device output and must not be echoed into the report.
            reason = {
                "拒绝主屏或未知显示的输入框。": "INVALID_DISPLAY",
                "拒绝主屏或未知 display id。": "INVALID_DISPLAY",
                "无法唯一定位本次副屏的系统状态。": "DISPLAY_SECTION_NOT_UNIQUE",
                "副屏未唯一匹配当前用户的抖音 Activity，不检查其他窗口。": "ACTIVITY_NOT_UNIQUE",
                "副屏焦点窗口未匹配该抖音 Activity。": "FOCUSED_WINDOW_MISMATCH",
            }.get(str(exc), "UNKNOWN_TARGET_FORMAT")
            event["window"] = {"status": "TARGET_NOT_UNIQUELY_MATCHED", "match_failure": reason}
        else:
            event["target"] = target
            event["window"] = window_metadata(session.shell("dumpsys", "window", "windows"), target)
        require_same_context(before, session.context())
        return event
    except Exception as exc:
        # Do not include subprocess stdout/stderr, hierarchy or primary contents.
        event["error_type"] = type(exc).__name__
        raise
    finally:
        session.save()


def confirm_home_before_model(session):
    """Never constructs a model or dispatches input. Missing navigation stays local."""
    report = session.report
    if report.get("model_called"):
        raise RuntimeError("模型已经调用，不能补记为模型前首页确认。")
    report["same_session_handoff"] = True
    gate = report["home_gate"] = {"status": "AWAITING_HUMAN", "display_id": session.display_id}
    first = secondary_diagnostic(session, "before-gate")
    gate["initial_context"] = first["context"]
    print("模型尚未接入。请只看电脑副屏，不点击、不手动恢复导航；可以等待明确的开屏广告自然结束。", flush=True)
    print("广告结束后，确认顶部栏目及底部首页/消息/我均可见，输入数字1；仍缺少导航或不是首页输入0，取消输入q。")
    answer = read_home_observation(session)
    gate["observation"] = answer
    gate["observed_at"] = datetime.now().isoformat()
    session.save()
    if answer != "home":
        gate["status"] = "MISSING_NAVIGATION" if answer == "missing" else "INVALID_INPUT" if answer == "invalid" else "CANCELLED"
        session.save()
        if answer == "missing":
            secondary_diagnostic(session, "missing-navigation")
            print("现场已保存；不调用模型、不等待广告、不点击。副屏暂时保留，请勿操作。", flush=True)
            input("看完后在电脑按回车，关闭本次副屏并恢复音频设置：")
        reason = {"missing": "用户报告缺少导航/不是首页", "invalid": "两次确认输入均未识别", "cancel": "用户取消首页确认"}[answer]
        raise RuntimeError(reason + "；本轮只保留启动诊断，未调用模型。")
    confirmed = secondary_diagnostic(session, "home-confirmed")
    require_same_app_display(first["context"], confirmed["context"])
    if confirmed["context"]["display_id"] != gate["display_id"]:
        raise RuntimeError("人工确认的首页不在本次副屏，不调用模型。")
    gate["pre_confirmation_transition"] = {"before": first["context"], "after": confirmed["context"],
        "activity_changed": first["context"] != confirmed["context"],
        "basis": "human_confirmed_current_home_on_same_app_display_not_an_ad_classifier"}
    gate.update(status="HOME_CONFIRMED", context=confirmed["context"], confirmed_at=datetime.now().isoformat())
    report["home_ready_by_user"] = True
    session.save()
    from run_douyin_test import countdown
    print("保持同一次副屏，不重启抖音；倒计时后接入模型，只导航到消息列表。", flush=True)
    countdown()
    ready = secondary_diagnostic(session, "before-model")
    require_same_context(gate["context"], ready["context"])
    gate.update(status="READY_FOR_MODEL", ready_at=datetime.now().isoformat())
    session.save()
