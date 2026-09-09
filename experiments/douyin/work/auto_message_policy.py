"""CLI-authorized one-message policy. User explicitly approved no second prompt.

Authorization is only the current CLI task, never screenshot instructions.
Keep recipient ambiguity rejection, display/IME guards, and durable no-retry.
This module has no device, model, or network operations.
"""
import hashlib
import json
import os
from pathlib import Path
import re


def decode_object(text):
    if not isinstance(text, str) or len(text) > 16000:
        raise ValueError("模型没有返回受限JSON。")
    value = text.strip()
    wrapped = re.fullmatch(r"(?:<think>[\s\S]*?</think>\s*)?<answer>([\s\S]*?)</answer>", value)
    if wrapped:
        value = wrapped[1].strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", value)
    if fenced:
        value = fenced[1]
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("模型JSON包含重复字段。")
            result[key] = item
        return result
    result = json.loads(value, object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("非法JSON数字")))
    if not isinstance(result, dict):
        raise ValueError("模型JSON必须为对象。")
    return result


FIELDS = {"page", "blocked", "recipient", "group", "ambiguous", "draft", "target_count", "next", "point"}


def same_candidate_point(left, right):
    """Allow tiny model rounding differences, never rewrite a proposed point."""
    return all(abs(a - b) <= 2 for a, b in zip(left, right))


def validate_observation(value, stage, task):
    if set(value) != FIELDS:
        raise ValueError("模型观察字段不匹配，不猜测或补齐。")
    if value["page"] not in {"home", "messages", "chat", "loading", "other"}:
        raise ValueError("未知页面。")
    if any(type(value[key]) is not bool for key in ("blocked", "group", "ambiguous")):
        raise ValueError("页面风险字段不是布尔值。")
    if value["blocked"] or value["group"] or value["ambiguous"]:
        raise ValueError("页面受阻、群聊或对象不唯一，停止。")
    for key in ("recipient", "draft"):
        if value[key] is not None and not isinstance(value[key], str):
            raise ValueError("联系人/草稿字段类型错误。")
    if type(value["target_count"]) is not int or not 0 <= value["target_count"] <= 20:
        raise ValueError("目标数量无效。")
    if not isinstance(value["next"], str):
        raise ValueError("动作类型无效。")
    if value["next"] == "wait" and value["page"] == "loading" and stage in {"START", "FIND", "CHAT"}:
        if value["point"] is not None:
            raise ValueError("等待不允许坐标。")
        return value
    expected = {
        "START": {("home", "open_messages"), ("messages", "ready")},
        "FIND": {("messages", "open_recipient"), ("messages", "scroll")},
        "CHAT": {("chat", "focus")},
        "EMPTY": {("chat", "input")},
        "DRAFT": {("chat", "send")},
    }
    if (value["page"], value["next"]) not in expected.get(stage, set()):
        raise ValueError("模型动作与当前阶段不符；不返回、换人或重放。")
    if stage in {"CHAT", "EMPTY", "DRAFT"} or value["next"] == "open_recipient":
        if value["recipient"] != task.recipient or value["target_count"] != 1:
            raise ValueError("没有唯一完整匹配的收件人，停止。")
    if value["next"] == "scroll" and value["target_count"] != 0:
        raise ValueError("已有目标或同名歧义，不继续滚动。")
    if stage in {"CHAT", "EMPTY"} and value["draft"] != "":
        raise ValueError("输入框非空或无法读取；不清空、不追加。")
    if stage == "DRAFT" and value["draft"] != task.message:
        raise ValueError("草稿与任务消息不完全一致，不发送。")
    point = value["point"]
    ranges = {"open_messages": (640, 790, 935, 985), "open_recipient": (80, 550, 210, 880),
              "focus": (150, 600, 870, 980), "send": (700, 980, 800, 990)}
    if value["next"] in ranges:
        if not isinstance(point, list) or len(point) != 2 or any(type(v) is not int for v in point):
            raise ValueError("点击坐标无效。")
        x0, x1, y0, y1 = ranges[value["next"]]
        if not x0 <= point[0] <= x1 or not y0 <= point[1] <= y1:
            raise ValueError("点击超出当前阶段允许区域，不裁剪坐标。")
    elif point is not None:
        raise ValueError("此动作不接受坐标。")
    return value


def validate_delivery(value, task):
    fields = {"recipient", "group", "ambiguous", "draft", "new_outgoing", "outgoing_text", "send_state"}
    if set(value) != fields:
        raise ValueError("发送后验证字段不匹配；结果未知，不重发。")
    if (value["recipient"] != task.recipient or value["group"] is not False
            or value["ambiguous"] is not False or value["draft"] != ""
            or value["new_outgoing"] is not True or value["outgoing_text"] != task.message
            or value["send_state"] != "sent"):
        raise ValueError("没有确认相较发送前新增的己方消息；结果未知，不重发。")
    return value


class TaskJournal:
    """Default task ID is deterministic. A new ID cannot bypass pending input."""
    def __init__(self, root, serial, task, request_id=None):
        self.root = Path(root)
        recipient_key = hashlib.sha256((serial + "\0" + task.recipient).encode()).hexdigest()[:24]
        self.pending = self.root / ("auto-pending-" + recipient_key + ".json")
        identity = request_id or hashlib.sha256((task.recipient + "\0" + task.message).encode()).hexdigest()
        key = hashlib.sha256((serial + "\0" + identity).encode()).hexdigest()[:24]
        self.path = self.root / ("auto-request-" + key + ".json")

    def require_unused(self):
        if self.path.exists() or self.pending.exists():
            raise RuntimeError("已有同任务记录或该收件人的未决输入/发送；不重试，不删除记录绕过。")

    def reserve_input(self):
        self.require_unused()
        self.root.mkdir(parents=True, exist_ok=True)
        self._exclusive(self.pending, {"status": "INPUT_RESERVED", "request": self.path.name})
        self._exclusive(self.path, {"status": "INPUT_RESERVED"})

    @staticmethod
    def _exclusive(path, data):
        with path.open("x", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def reserve_send(self):
        if not self.path.exists() or not self.pending.exists():
            raise RuntimeError("缺少本次输入事务，不发送。")
        if json.loads(self.pending.read_text()).get("request") != self.path.name:
            raise RuntimeError("未决事务不属于本任务，不发送。")
        self._exclusive(self.path.with_name(self.path.stem + "-send.json"),
                        {"status": "SEND_RESERVED_NOT_DELIVERY_PROOF"})

    def confirmed(self):
        if not self.path.with_name(self.path.stem + "-send.json").is_file():
            raise RuntimeError("没有发送尝试，不允许记为成功。")
        if json.loads(self.pending.read_text()).get("request") != self.path.name:
            raise RuntimeError("未决事务归属变化，不清理。")
        self._exclusive(self.path.with_name(self.path.stem + "-verified.json"),
                        {"status": "MODEL_CONFIRMED_NEW_OUTGOING", "recipient_delivery_verified": False})
        self.pending.unlink()  # Verified pending lock only; attempt ledgers remain.
