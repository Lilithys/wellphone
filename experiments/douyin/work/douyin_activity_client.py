"""Strict scoped activity-list client reader for the qualified HONOR build.

Its token-specific dump uses TransferPipe.go(fd, 60); the activity-list path
uses 2000 ms. This changes only a read command, never the system timeout.
No synthetic ACTIVITY header and no acceptance of partial client output.
"""
import re

from ascii_focus_guard import client_dump_metadata, editor_nodes
from run_autoglm_focus import normalize_component

PACKAGE = "com.ss.android.ugc.aweme"


def activity_list_editor(dump, target, expected_uid):
    display_id = target.get("display_id")
    if type(display_id) is not int or display_id <= 0 or type(expected_uid) is not int:
        raise RuntimeError("副屏层级读取缺少有效显示或应用 UID。")
    if client_dump_metadata(dump)["error_categories"]:
        raise RuntimeError("副屏 Activity 列表客户端转储报错；不使用残缺层级。")
    displays = re.findall(r"(?m)^Display #(\d+) \(activities from top to bottom\):\s*$", dump)
    if displays != [str(display_id)]:
        raise RuntimeError("客户端转储未严格限定到本次唯一副屏。")
    component = re.escape(PACKAGE) + r"/[\w.$]+"
    record = r"ActivityRecord\{([0-9a-f]+) u(\d+) (" + component + r") t\d+(?: [^{}\n]*)?\}"
    hist_lines = re.findall(r"(?m)^([ \t]*\* Hist\s+#[^\n]*)$", dump)
    hist = re.fullmatch(r"([ \t]*)\* Hist\s+#\d+: " + record, hist_lines[0]) if len(hist_lines) == 1 else None
    resumed = re.findall(r"(?m)^[ \t]*topResumedActivity=(.*)$", dump)
    top = re.fullmatch(record, resumed[0]) if len(resumed) == 1 else None
    if (not hist or not top or hist.groups()[1:] != top.groups()
            or hist[2] != target["activity_token"] or hist[3] != "0"
            or normalize_component(hist[4]) != target["component"]):
        raise RuntimeError("转储必须唯一匹配当前用户、本次副屏的已恢复 Activity。")
    hist_start = dump.index(hist_lines[0])
    if hist_start < dump.index("Display #" + str(display_id)):
        raise RuntimeError("Activity 记录位于副屏边界之外。")
    local = list(re.finditer(r"(?m)^([ \t]*)Local Activity ([0-9a-f]+) State:[ \t]*$", dump))
    hierarchy = list(re.finditer(r"(?m)^([ \t]*)View Hierarchy:[ \t]*$", dump))
    looper = list(re.finditer(r"(?m)^([ \t]*)Looper \(main, tid [1-9]\d*\) \{[0-9a-f]+\}[ \t]*$", dump))
    if len(local) != 1 or len(hierarchy) != 1 or len(looper) != 1:
        raise RuntimeError("缺少唯一客户端层级及其完成标记；不使用截断结果。")
    local, hierarchy, looper = local[0], hierarchy[0], looper[0]
    prefix = local[1]
    if (not hist_start < local.start() < hierarchy.start() < looper.start()
            or prefix != hist[1] + "  " or hierarchy[1] != prefix or looper[1] != prefix):
        raise RuntimeError("客户端层级不在已核对 Activity 的结构边界内。")
    server = dump[hist_start:local.start()]
    processes = re.findall(r"(?m)^[ \t]*app=ProcessRecord\{[0-9a-f]+ ([1-9]\d*):([\w.:]+)/u(\d+)a(\d+)\}[ \t]*$", server)
    if (len(processes) != 1 or processes[0][1] != PACKAGE or processes[0][2] != "0"
            or 10000 + int(processes[0][3]) != expected_uid):
        raise RuntimeError("客户端进程未匹配当前用户的抖音 UID；不输入。")
    states = re.findall(r"\bstate=([A-Z_]+)\b", server)
    if states != ["RESUMED"]:
        raise RuntimeError("客户端 Activity 未唯一确认处于 RESUMED 状态。")
    body = dump[hierarchy.end():looper.start()]
    # The complete tree must remain nested under its header; reject an earlier
    # client/Activity section ending followed by an unrelated Looper marker.
    if any(line.strip() and not line.startswith(prefix + "  ") for line in body.splitlines()):
        raise RuntimeError("层级块出现未知边界；不混用其他窗口的控件。")
    return {"validated_client_header": {"format": "activity_list", "component": target["component"],
                "activity_token": target["activity_token"], "pid": int(processes[0][0]),
                "user_id": 0, "uid": expected_uid, "display_id": display_id},
            "hierarchy_completion": "same_client_main_looper_after_tree",
            "display_type_verification": "session_live_virtual_display_guard",
            **editor_nodes(body)}
