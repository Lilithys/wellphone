"""Fail-closed parsers for an owned secondary Settings window and focused editor.

Only structural metadata is returned. Never persist raw client dumps or text.
View.toString flag group two, index one, is PFLAG_FOCUSED (AOSP View.java).
OEM formats that cannot be recognized are deliberately not accepted.
"""
from __future__ import annotations

import re

from run_autoglm_focus import normalize_component


COMPONENT = r"com\.android\.settings/[\w.$]+"
HONOR_SEARCH_EDITOR = "com.hihonor.android.widget.SearchView$HwSearchAutoComplete"


def secondary_section(dump, display_id, pattern):
    if not isinstance(display_id, int) or display_id <= 0:
        raise RuntimeError("拒绝主屏或未知 display id。")
    parts = re.split(pattern, dump)
    matches = [parts[i + 1] for i in range(1, len(parts), 2)
               if int(parts[i]) == display_id]
    if len(matches) != 1:
        raise RuntimeError("无法唯一定位本次副屏的系统状态。")
    return matches[0]


def activity_target(dump, display_id):
    body = secondary_section(dump, display_id,
                             r"(?m)^\s*Display #(\d+) \(activities from top to bottom\):")
    rows = re.findall(r"(?m)^\s*topResumedActivity=(.*)$", body)
    match = re.fullmatch(r"ActivityRecord\{([0-9a-f]+) u\d+ (" + COMPONENT + r")\s[^\n]*\}",
                         rows[0].strip()) if len(rows) == 1 else None
    if not match:
        raise RuntimeError("副屏当前 Activity 不是可唯一识别的设置页面。")
    return {"display_id": display_id, "activity_token": match[1],
            "component": normalize_component(match[2])}


def focused_window(dump, target):
    body = secondary_section(dump, target["display_id"],
                             r"(?m)^\s*Display:\s+mDisplayId=(\d+)\b")
    rows = re.findall(r"(?m)^\s*mCurrentFocus=(.*)$", body)
    match = re.fullmatch(r"Window\{([0-9a-f]+) u\d+ (" + COMPONENT + r")\}",
                         rows[0].strip()) if len(rows) == 1 else None
    if not match or normalize_component(match[2]) != target["component"]:
        raise RuntimeError("副屏当前焦点窗口未确认属于本次设置 Activity。")
    return {"window_token": match[1], "component": normalize_component(match[2])}


def client_dump_metadata(dump):
    """Record header structure and known errors, never arbitrary output or UI text."""
    lines = dump.splitlines()
    rows = [line.strip() for line in lines if re.match(r"\s*ACTIVITY\s", line)]
    headers = []
    for row in rows[:8]:
        match = re.match(r"ACTIVITY\s+([\w.]+/[\w.$]+)\s+([0-9a-f]+)\s+pid=(\d+)(.*)$", row)
        item = {"base_header_recognized": match is not None}
        if match:
            tail = match[4].strip()
            item.update(component=normalize_component(match[1]), activity_token=match[2],
                        pid=int(match[3]), has_suffix=bool(tail))
            # Only names of supported structural fields, not arbitrary keys/values.
            item["known_suffix_fields"] = [key for key in ("userId", "uid", "displayId")
                                           if re.search(r"\b" + key + "=", tail)]
            for key in item["known_suffix_fields"]:
                values = re.findall(r"\b" + key + r"=(-?\d+)(?=[\s(]|$)", tail)
                if len(values) == 1:
                    item[key] = int(values[0])
        headers.append(item)
    categories = []
    for label, pattern in [
        ("no_activity_match", r"(?mi)^\s*Bad activity command, or no activities match:"),
        ("unknown_command", r"(?mi)^\s*Unknown command:"),
        ("unknown_argument", r"(?mi)^\s*Unknown argument:"),
        ("permission_denied", r"(?mi)^\s*(?:Permission Denial|Permission denied|SecurityException)"),
        ("client_dump_failed", r"(?mi)^\s*(?:Failure while dumping|Failure dumping|Error dumping)"),
    ]:
        if re.search(pattern, dump):
            categories.append(label)
    return {"nonempty": bool(dump.strip()), "activity_header_count": len(rows),
            "activity_headers": headers,
            "view_hierarchy_count": sum(line.strip() == "View Hierarchy:" for line in lines),
            "error_categories": categories}


def client_activity_header(dump, target):
    """Accept legacy and verified verbose headers; never ignore a suffix blindly."""
    if not isinstance(target.get("display_id"), int) or target["display_id"] <= 0:
        raise RuntimeError("客户端层级不能用于主屏或未知显示。")
    rows = [line.strip() for line in dump.splitlines() if re.match(r"\s*ACTIVITY\s", line)]
    # Observed HONOR Android 14 output includes ALL these verbose fields by default:
    # ACTIVITY component token pid=N userId=N uid=N displayId=N(type=VIRTUAL)
    # Only this complete known suffix (or the legacy empty suffix) is supported.
    pattern = (r"ACTIVITY\s+([\w.]+/[\w.$]+)\s+([0-9a-f]+)\s+pid=([1-9]\d*)"
               r"(?:\s+userId=(\d+)\s+uid=(\d+)\s+displayId=(-?\d+)\(type=([A-Z_]+)\))?")
    match = re.fullmatch(pattern, rows[0]) if len(rows) == 1 else None
    if (not match or match[2] != target["activity_token"]
            or normalize_component(match[1]) != target["component"]):
        raise RuntimeError("客户端层级未唯一匹配副屏 Activity；拒绝使用其他窗口的输入框信息。")
    header = {"component": normalize_component(match[1]), "activity_token": match[2],
              "pid": int(match[3]), "format": "verbose" if match[4] is not None else "legacy"}
    if match[4] is not None:
        header.update(user_id=int(match[4]), uid=int(match[5]), display_id=int(match[6]), display_type=match[7])
        if header["display_id"] != target["display_id"] or header["display_type"] != "VIRTUAL":
            raise RuntimeError("客户端返回的显示 ID/类型不是本次副屏；拒绝使用其他显示的输入框。")
    return header


def editor_evidence(dump, target):
    """Inspect only a dump already scoped to an exact secondary ActivityRecord."""
    header = client_activity_header(dump, target)
    parts = re.split(r"(?m)^\s*View Hierarchy:\s*$", dump)
    if len(parts) != 2:
        raise RuntimeError("该系统未提供可识别的副屏 View Hierarchy；没有发送文字。")
    candidates = []
    for line in parts[1].splitlines():
        match = re.match(r"\s*([\w.$]+)\{([0-9a-f]+) ([A-Za-z.]{8,12}) ([A-Za-z.]{8,12})\s", line)
        if not match:
            continue
        resource = re.search(r"\b([\w.]+:id/[\w_]+)\b", line)
        standard_editor = re.search(r"(?:^|[.$])(?:\w*EditText|\w*AutoCompleteTextView|SearchAutoComplete)$", match[1])
        # This exact OEM class + resource was inspected on the user's secondary
        # Settings search page. Do not accept arbitrary *AutoComplete class names.
        honor_editor = (match[1] == HONOR_SEARCH_EDITOR and resource is not None
                        and resource[1] == "android:id/search_src_text")
        if not standard_editor and not honor_editor:
            continue
        candidates.append({"class": match[1], "view_token": match[2],
                           "flags": [match[3], match[4]],
                           "resource_id": resource[1] if resource else None,
                           "eligible": match[3][:3] == "VFE" and match[4][1] == "F"})
    eligible = [item for item in candidates if item["eligible"]]
    return {"status": "focused_editor" if len(eligible) == 1 else "editor_not_confirmed",
            "validated_client_header": header,
            "candidates": candidates, "focused_editor": eligible[0] if len(eligible) == 1 else None}
