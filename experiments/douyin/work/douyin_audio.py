"""Reversible package-only AppOps lease. No recording, global volume, or model."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from douyin_policy import PACKAGE
from test_keyboard_isolation import activity_on_display

OPS = ("TAKE_AUDIO_FOCUS", "PLAY_AUDIO")
MODES = {"allow", "ignore", "deny", "default"}


def parse_mode(text, op):
    if op not in OPS:
        raise RuntimeError("音频操作不在固定白名单。")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == 2 and lines[0] == "No operations.":
        match = re.fullmatch(r"Default mode: (allow|ignore|deny|default)", lines[1])
        if match:
            # Restore the actual default value, not the literal MODE_DEFAULT.
            return {"mode": match[1], "explicit": False}
    if len(lines) == 1:
        match = re.fullmatch(re.escape(op) + r": (allow|ignore|deny|default)(?:;[^\r\n]*)?", lines[0])
        if match:
            return {"mode": match[1], "explicit": True}
    raise RuntimeError("音频权限回读格式未知或含 UID 覆盖；不猜测原值。")


def player_states(dump, uid):
    """Whitelist this UID's current player states, never persist raw audio dump."""
    sections = re.split(r"(?m)^  players:\s*$", dump)
    if len(sections) != 2:
        raise RuntimeError("没有唯一的当前播放器列表，不能确认播放已经停止。")
    result, ended = [], False
    for line in sections[1].splitlines():
        if not line.strip():
            continue
        if line.strip() == "ducked players piids:":
            ended = True
            break
        if "AudioPlaybackConfiguration" not in line:
            raise RuntimeError("播放器列表出现未知行，不能确认是否仍在播放。")
        match = re.search(r"AudioPlaybackConfiguration piid:(\d+).*?u/pid:(\d+)/(\d+) state:([a-z]+)\b", line)
        if not match:
            raise RuntimeError("播放器列表结构未知。")
        if int(match[2]) == uid:
            if match[4] not in {"idle", "started", "paused", "stopped", "released"}:
                raise RuntimeError("抖音播放器状态未知。")
            mute = re.search(r"\bmutedState:([a-zA-Z0-9_,|]+)", line)
            result.append({"id": int(match[1]), "state": match[4], "muted": mute[1] if mute else None})
    if not ended:
        raise RuntimeError("播放器列表不完整，不能确认播放已停止。")
    return result


def durable_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def restore_safely(lease, report, timeout=8):
    """Wait for quiet before unmuting; retain the journal on any uncertainty."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            lease.require_quiet()
            break
        except Exception:
            if time.monotonic() >= deadline:
                break
            time.sleep(.5)
    try:
        lease.restore()
        report["permissions_restored"] = True
    except Exception as exc:
        report["permissions_restored"] = False
        report["recovery_error"] = str(exc)
        print("恢复未确认！请先关闭抖音，然后运行以下恢复命令：", flush=True)
        runner = lease.path.parent.parent / "run.py"
        print(f"python3 {runner} audio --restore --serial {lease.adb.serial}", flush=True)
        print("恢复记录：" + str(lease.path), flush=True)
    report["audio_transaction"] = lease.data


class AudioLease:
    def __init__(self, adb, root):
        self.adb = adb
        token = hashlib.sha256(adb.serial.encode()).hexdigest()[:24]
        self.path = Path(root) / "outputs" / ("audio-lease-" + token + ".json")
        self.data = None

    def read_op(self, op, uid=False):
        target = str(self.data["uid"]) if uid else PACKAGE
        return parse_mode(self.adb.shell("cmd", "appops", "get", "--user", "0", target, op), op)

    def identity(self):
        if self.adb.shell("am", "get-current-user").strip() != "0":
            raise RuntimeError("当前 Android 用户不是 0。")
        rows = self.adb.shell("pm", "list", "packages", "-U", PACKAGE).strip().splitlines()
        if len(rows) != 1 or not (match := re.fullmatch("package:" + re.escape(PACKAGE) + r" uid:(\d+)", rows[0])):
            raise RuntimeError("抖音安装/UID 未唯一匹配。")
        uid = int(match[1])
        shared = self.adb.shell("pm", "list", "packages", "--uid", str(uid)).strip().splitlines()
        if shared not in [["package:" + PACKAGE], ["package:" + PACKAGE + " uid:" + str(uid)]]:
            raise RuntimeError("抖音 UID 与其他应用共用或查询格式未知，不修改权限。")
        build = hashlib.sha256(self.adb.shell("getprop", "ro.build.fingerprint").strip().encode()).hexdigest()
        return {"serial": self.adb.serial, "package": PACKAGE, "uid": uid, "build_hash": build, "user": 0}

    def save(self):
        durable_json(self.path, self.data)

    def require_no_pending(self):
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or data.get("phase") != "RESTORED":
                raise RuntimeError("存在未完成音频恢复记录；先运行 audio --restore，不启动新的抖音任务。")

    def require_identity(self):
        current = self.identity()
        if any(self.data.get(k) != v for k, v in current.items()):
            raise RuntimeError("设备/系统版本/应用 UID 发生变化，拒绝修改或恢复到不同目标。")

    def require_quiet(self):
        main = activity_on_display(self.adb.shell("dumpsys", "activity", "activities"), 0)
        if main == "unknown" or main.startswith(PACKAGE + "/"):
            raise RuntimeError("主屏正使用抖音或前台未知，暂不修改音频权限。")
        players = player_states(self.adb.shell("dumpsys", "audio"), self.data["uid"])
        if any(p["state"] == "started" for p in players):
            raise RuntimeError("抖音仍有启动中的播放器。请先停止/关闭抖音，再运行 audio --restore；暂不解禁以免出声。")

    def prepare(self):
        self.require_no_pending()
        self.data = {"schema": 1, **self.identity(), "phase": "PREPARED", "original": {}, "attempted": [], "restored_ops": []}
        self.require_quiet()
        for op in OPS:
            uid_mode = self.read_op(op, uid=True)
            if uid_mode != {"mode": "allow", "explicit": False}:
                raise RuntimeError("存在 UID 级音频覆盖，拒绝包级权限测试。")
            self.data["original"][op] = self.read_op(op)
        self.save()  # Capture all original values before any write.

    def set_op(self, op, mode):
        if op not in OPS or mode not in MODES:
            raise RuntimeError("拒绝非音频白名单设置。")
        self.adb.shell("cmd", "appops", "set", "--user", "0", PACKAGE, op, mode)

    def apply(self):
        if not self.data or self.data["phase"] != "PREPARED":
            raise RuntimeError("音频事务未准备，禁止写入。")
        self.require_identity()
        self.require_quiet()
        for op in OPS:
            if self.read_op(op)["mode"] != self.data["original"][op]["mode"]:
                raise RuntimeError("准备后音频权限变化，停止，不覆盖。")
        for op in OPS:
            self.data["attempted"].append(op)
            self.data["phase"] = "APPLYING"
            self.save()  # A timeout can mean the write happened. Recovery must include it.
            self.set_op(op, "ignore")
            if self.read_op(op)["mode"] != "ignore":
                raise RuntimeError("系统未接受音频限制；不启动抖音。")
        self.data["phase"] = "RESTRICTED"
        self.save()

    def require_restricted(self):
        if not self.data or self.data["phase"] != "RESTRICTED":
            raise RuntimeError("尚未确认两个音频限制。")
        if any(self.read_op(op)["mode"] != "ignore" for op in OPS):
            raise RuntimeError("音频限制发生变化，停止实验。")

    def load_recovery(self):
        data = json.loads(self.path.read_text())
        if (data.get("schema") != 1 or data.get("package") != PACKAGE or data.get("serial") != self.adb.serial
                or data.get("user") != 0 or set(data.get("original", {})) != set(OPS)
                or not isinstance(data.get("attempted"), list) or not set(data["attempted"]) <= set(OPS)
                or not isinstance(data.get("restored_ops"), list) or not set(data["restored_ops"]) <= set(OPS)
                or data.get("phase") not in {"PREPARED", "APPLYING", "RESTRICTED", "NEEDS_RECOVERY", "RESTORED"}
                or any(data["original"][op].get("mode") not in MODES for op in OPS)):
            raise RuntimeError("恢复记录不符合固定范围，不执行命令。")
        self.data = data

    def restore(self):
        if self.data is None:
            self.load_recovery()
        if self.data["phase"] == "RESTORED":
            # Even an old success needs a fresh device check before reporting success.
            self.require_identity()
            if any(self.read_op(op)["mode"] != self.data["original"][op]["mode"] for op in OPS):
                raise RuntimeError("旧恢复记录与当前设置不同，不覆盖后来的修改。")
            return
        self.require_identity()
        self.require_quiet()
        errors = []
        for op in OPS:
            if op not in self.data["attempted"]:
                continue
            original = self.data["original"][op]["mode"]
            try:
                current = self.read_op(op)["mode"]
                if current not in {original, "ignore"}:
                    raise RuntimeError("权限已被其他操作改动，不覆盖。")
                if current != original:
                    if op == "PLAY_AUDIO":
                        self.require_quiet()
                    self.set_op(op, original)
                if self.read_op(op)["mode"] != original:
                    raise RuntimeError("恢复回读未匹配原值。")
                if op not in self.data["restored_ops"]:
                    self.data["restored_ops"].append(op)
            except Exception as exc:
                errors.append(op + ": " + str(exc))
            self.save()
        self.data["phase"] = "NEEDS_RECOVERY" if errors else "RESTORED"
        self.data["recovery_errors"] = errors
        self.save()
        if errors:
            raise RuntimeError("音频恢复未全部通过：" + "; ".join(errors))
