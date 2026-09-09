"""ADB transport and read-only state probes. No global IME/clipboard operations."""
from __future__ import annotations

import fcntl
import hashlib
import re
import shlex
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from test_keyboard_isolation import activity_on_display, focus_state


class DeviceError(RuntimeError):
    pass


def numeric_rows(text, columns):
    text = text.strip()
    if text == "No result found.":
        return []
    if not text:
        raise DeviceError("Provider 没有返回可识别的查询结果。")
    pattern = r"Row: \d+ " + ", ".join(re.escape(name) + r"=(\d+)" for name in columns)
    rows = []
    for line in text.splitlines():
        match = re.fullmatch(pattern, line.strip())
        if not match:
            raise DeviceError("Provider 查询结构不匹配；不将错误或缺失结果当作空表。")
        rows.append(dict(zip(columns, map(int, match.groups()))))
    if len({row.get("_id") for row in rows}) != len(rows):
        raise DeviceError("Provider 返回重复 ID。")
    return rows


class ADB:
    def __init__(self, serial=None):
        self.serial = serial

    def connect(self):
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=True, timeout=15)
        serials = re.findall(r"(?m)^(\S+)\s+device\s*$", result.stdout)
        if self.serial is None:
            if len(serials) != 1:
                raise DeviceError("需连接一台已授权手机；多设备时请传 --serial。")
            self.serial = serials[0]
        if self.serial not in serials:
            raise DeviceError("指定手机未连接或未授权。")
        return self

    def shell(self, *args):
        if not self.serial:
            raise DeviceError("没有固定的设备身份。")
        try:
            result = subprocess.run(["adb", "-s", self.serial, "shell", shlex.join(map(str, args))],
                                    capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise DeviceError("ADB 超时；写入结果可能不确定，不自动重试。") from exc
        if result.returncode or result.stderr.strip() or re.search(r"(?mi)^\s*(?:\[ERROR\]|Error:|Error while|Exception|SecurityException|Permission Denial|Unknown command:)", result.stdout):
            # Do not echo the full command, bind strings, stderr, or surrounding UI.
            raise DeviceError("ADB/Provider 命令报错；不自动提升权限或切换执行方式。")
        return result.stdout

    def state(self):
        activities = self.shell("dumpsys", "activity", "activities")
        ime = self.shell("dumpsys", "input_method")
        windows = self.shell("dumpsys", "window")
        focus = focus_state(ime, windows)
        main = activity_on_display(activities, 0)
        default = self.shell("settings", "get", "secure", "default_input_method").strip()
        return {"time": datetime.now().isoformat(), "connected": True, "serial": self.serial,
                "top_focused_display_id": focus["top_focused_display_id"],
                "keyboard_on_primary": "mCurTokenDisplayId=0" in focus["ime_state"] and "mInputShown=true" in focus["ime_state"],
                "main_package": main.split("/")[0] if "/" in main else None,
                "main_activity": main, "default_ime": default if default and default != "null" else None,
                "android_user": int(self.shell("am", "get-current-user").strip())}

    def calendars(self):
        text = self.shell("content", "query", "--user", "0", "--uri", "content://com.android.calendar/calendars",
                          "--projection", "_id:calendar_access_level:visible", "--where", "calendar_access_level>=500")
        rows = numeric_rows(text, ["_id", "calendar_access_level", "visible"])
        return [row["_id"] for row in rows if row["calendar_access_level"] >= 500 and row["visible"] == 1]

    def probe(self):
        state = self.state()
        state.update(model=self.shell("getprop", "ro.product.model").strip(), sdk=self.shell("getprop", "ro.build.version.sdk").strip(),
                     build_hash=hashlib.sha256(self.shell("getprop", "ro.build.fingerprint").strip().encode()).hexdigest())
        display = self.shell("dumpsys", "display")
        state["virtual_displays"] = [int(m[1]) for line in display.splitlines()
                                     if '"scrcpy"' in line and "type VIRTUAL" in line
                                     and (m := re.search(r"displayId (\d+),", line))]
        try:
            state["writable_calendar_ids"] = self.calendars()
            packages = self.shell("pm", "list", "packages", "calendar")
            state["calendar_packages"] = re.findall(r"(?m)^package:([\w.]+)$", packages)
        except DeviceError:
            state.update(writable_calendar_ids=[], calendar_packages=[], calendar_probe_error=True)
        return state


class MainGuard:
    def __init__(self, adb, original_ime, forbidden_packages=()):
        self.adb, self.original_ime = adb, original_ime
        self.forbidden_packages = set(forbidden_packages)

    def require(self):
        state = self.adb.state()
        if state.get("top_focused_display_id") != 0 or not state.get("keyboard_on_primary"):
            raise DeviceError("主屏键盘/焦点未保持，停止后续动作。")
        if not self.original_ime or state.get("default_ime") != self.original_ime:
            raise DeviceError("默认输入法变化或无法确认，停止后续动作。")
        if not state.get("main_package") or state["main_package"] in self.forbidden_packages:
            raise DeviceError("主屏目标 App 冲突或前台状态未知，暂停。")
        if state.get("android_user") != 0:
            raise DeviceError("Android 用户切换，停止动作。")
        return state


class Monitor:
    def __init__(self, guard):
        self.guard = guard
        self.samples, self.errors = [], []
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        self.samples.append(self.guard.require())
        def sample():
            while not self.stop.wait(.4):
                try:
                    self.samples.append(self.guard.require())
                except Exception as exc:
                    self.errors.append(str(exc))
                    return
        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            self.errors.append("焦点采样线程未及时退出。")
        try:
            self.samples.append(self.guard.require())
        except Exception as exc:
            self.errors.append(str(exc))

    def require(self):
        if self.errors:
            raise DeviceError("采样已发现主屏异常，拒绝后续写入。")
        return self.guard.require()


@contextmanager
def device_lease(root, serial):
    directory = Path(root) / "outputs" / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(serial.encode()).hexdigest()[:24] + ".lock")
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeviceError("另一个路由任务正在使用这台手机；不抢占会话。") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
