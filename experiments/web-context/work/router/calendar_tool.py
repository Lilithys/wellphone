"""Single-event Calendar Provider writer with readback and durable no-retry journal."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .device import DeviceError, numeric_rows
from .planning import validate_goal

EVENTS = "content://com.android.calendar/events"


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def operation_key(serial, calendar_id, goal):
    content = json.dumps([serial, calendar_id, goal], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()


def implementation_hash():
    directory = Path(__file__).resolve().parent
    return hashlib.sha256(b"".join((directory / name).read_bytes() for name in
                                   ["calendar_tool.py", "device.py", "planning.py"])).hexdigest()


class CalendarTool:
    def __init__(self, adb, journal_path):
        self.adb = adb
        Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(journal_path)
        self.db.execute("CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, write_started INTEGER NOT NULL, event_id INTEGER)")
        self.db.commit()

    def close(self):
        self.db.close()

    def ids(self, where):
        result = self.adb.shell("content", "query", "--user", "0", "--uri", EVENTS,
                                "--projection", "_id", "--where", where)
        return [row["_id"] for row in numeric_rows(result, ["_id"])]

    def create(self, goal, calendar_id, require_safe):
        goal = validate_goal(goal)
        if goal["kind"] != "calendar.create" or type(calendar_id) is not int or calendar_id <= 0:
            raise ValueError("无效的日历写入目标。")
        require_safe()
        if calendar_id not in self.adb.calendars():
            raise DeviceError("所选日历已不再满足可写条件。")
        key = operation_key(self.adb.serial, calendar_id, goal)
        marker = "wellphone-" + key
        start = int(datetime.fromisoformat(goal["start"]).timestamp() * 1000)
        end = int(datetime.fromisoformat(goal["end"]).timestamp() * 1000)
        fields = {"calendar_id": ("i", calendar_id), "title": ("s", goal["title"]),
                  "dtstart": ("l", start), "dtend": ("l", end), "eventTimezone": ("s", "Asia/Shanghai"),
                  "description": ("s", marker), "allDay": ("i", 0), "hasAlarm": ("i", 0), "hasAttendeeData": ("i", 0)}
        marker_where = "description=" + literal(marker)
        exact_where = " AND ".join(name + "=" + (literal(value) if kind == "s" else str(value))
                                   for name, (kind, value) in fields.items()) + " AND deleted=0"
        existing = self.ids(marker_where)
        journal = self.db.execute("SELECT write_started,event_id FROM operations WHERE key=?", (key,)).fetchone()
        if existing:
            if len(existing) != 1 or self.ids(exact_where) != existing:
                raise DeviceError("同一操作的日程已被修改、删除或重复；不覆盖用户结果。")
            self.db.execute("INSERT OR REPLACE INTO operations VALUES (?,1,?)", (key, existing[0]))
            self.db.commit()
            return {"event_id": existing[0], "operation_key": key, "reused": True, "effect_verified": True}
        if journal:
            raise DeviceError("本地已有写入尝试但未找到原事件；结果不确定或用户已删除，不自动重试。")
        require_safe()
        # Commit BEFORE the phone call, including across process crashes/restarts.
        self.db.execute("INSERT INTO operations VALUES (?,1,NULL)", (key,))
        self.db.commit()
        command = ["content", "insert", "--user", "0", "--uri", EVENTS]
        for name, (kind, value) in fields.items():
            # All string values deliberately exclude ':' and '\\' for OEM parser compatibility.
            if kind == "s" and (":" in value or "\\" in value):
                raise ValueError("不支持的 Provider bind 字符。")
            command.extend(["--bind", f"{name}:{kind}:{value}"])
        warning = None
        try:
            self.adb.shell(*command)
        except DeviceError:
            warning = "写入命令结果不确定；只读回查，不重发。"
        # A successful shell return is NOT proof that an event was created.
        verified = self.ids(exact_where)
        if len(verified) != 1 or self.ids(marker_where) != verified:
            raise DeviceError("事件写入结果未能唯一回读验证；保留待核查状态，不自动重试或切 GUI。")
        self.db.execute("UPDATE operations SET event_id=? WHERE key=?", (verified[0], key))
        self.db.commit()
        return {"event_id": verified[0], "operation_key": key, "reused": False,
                "effect_verified": True, "transport_warning": warning}


def qualification_path(root, serial, calendar_id):
    key = hashlib.sha256(f"{serial}:{calendar_id}".encode()).hexdigest()[:24]
    return Path(root) / "outputs" / "qualifications" / (key + ".json")


def qualified(root, state, calendar_id):
    path = qualification_path(root, state["serial"], calendar_id)
    try:
        data = json.loads(path.read_text())
        return (data.get("passed") is True and data.get("serial") == state["serial"]
                and data.get("calendar_id") == calendar_id and data.get("build_hash") == state.get("build_hash")
                and data.get("implementation_hash") == implementation_hash())
    except (OSError, ValueError, TypeError):
        return False


def save_qualification(root, state, calendar_id, evidence_path):
    path = qualification_path(root, state["serial"], calendar_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"passed": True, "serial": state["serial"], "calendar_id": calendar_id,
            "build_hash": state["build_hash"], "implementation_hash": implementation_hash(),
            "evidence_path": str(evidence_path), "verified_at": datetime.now().isoformat()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
