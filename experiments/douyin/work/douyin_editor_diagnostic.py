"""Structural-only diagnostics; never supplies an input authorization."""
import re

from ascii_focus_guard import client_activity_header


def hierarchy_metadata(dump, target):
    client_activity_header(dump, target)
    lines = dump.splitlines()
    headers = [i for i, line in enumerate(lines) if line.strip() == "View Hierarchy:"]
    result = {"hierarchy_headers": len(headers)}
    if len(headers) != 1:
        return result
    start = headers[0]
    indent = len(lines[start]) - len(lines[start].lstrip())
    stop = next((i for i in range(start + 1, len(lines))
                 if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= indent), len(lines))
    body = lines[start + 1:stop]
    nodes = []
    for line in body:
        match = re.match(r"\s*([\w.$]+)\{([0-9a-f]+)\s+([A-Za-z.]{8,12})\s+([A-Za-z.]{8,12})\s", line)
        if not match:
            continue
        resource = re.search(r"\b([\w.]+:id/[\w_]+)\b", line)
        nodes.append({"class": match[1], "view_token": match[2], "flags": [match[3], match[4]],
                      "resource_id": resource[1] if resource else None})
    focused = [node for node in nodes if node["flags"][1][1] == "F"]
    editors = [node for node in nodes if "EditText" in node["class"] or node["resource_id"] == "app:id/msg_et"]
    # Counts locate a format/scope mismatch without logging text around a match.
    known = "com.ss.android.ugc.aweme.im.widget.SearchableEditText"
    result.update(hierarchy_lines=len(body), parsed_view_count=len(nodes),
                  root_nodes=nodes[:3], focused_nodes=focused[:8], editor_nodes=editors[:8],
                  known_editor_in_hierarchy=sum(known in line for line in body),
                  known_editor_outside_hierarchy=sum(known in line for line in lines[:start] + lines[stop:]),
                  chat_editor_resource_in_hierarchy=sum("app:id/msg_et" in line for line in body))
    return result
