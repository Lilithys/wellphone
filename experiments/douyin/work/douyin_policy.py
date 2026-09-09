"""Small, offline-testable limits for the supervised Douyin experiment.

Navigation uses explicit model labels; sends and unclassified actions need human
review. Neither labels nor coordinates independently prove a tap's effect.
The one-attempt journal prevents retries after a potentially delivered message.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

PACKAGE = "com.ss.android.ugc.aweme"


def configured_recipient():
    """Entry parameter only; no model, IO, or recipient inference."""
    value = os.environ.get("WELLPHONE_DOUYIN_RECIPIENT", "示例联系人")
    if (not 1 <= len(value) <= 64 or value != value.strip()
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError("联系人参数无效；未连接手机。")
    return value


RECIPIENT = configured_recipient()


class UnclassifiedAction(RuntimeError):
    """A valid GUI action needs human review, not automatic approval."""


class ContextChanged(RuntimeError):
    """Two valid Douyin contexts differ; never authorizes a stale action."""
    def __init__(self, before, after):
        super().__init__("副屏显示或 Activity 在规划后发生变化，停止动作。")
        self.evidence = {"before": dict(before), "after": dict(after)}


class RegionChanged(RuntimeError):
    """Machine-readable frame mismatch, not a semantic recipient assessment."""
    def __init__(self, box, mean_difference, sending):
        super().__init__("待操作区域或收件人标题发生变化，停止，不使用旧坐标。")
        self.evidence = {"box": list(box), "mean_rgb_difference": list(mean_difference),
                         "threshold": 3, "sending": bool(sending)}


def validate_action(action):
    if not isinstance(action, dict) or action.get("_metadata") not in {"do", "finish"}:
        raise RuntimeError("无法识别的动作。")
    if action["_metadata"] == "finish":
        return
    name = action.get("action")
    if name not in {"Tap", "Swipe", "Back", "Wait"}:
        raise RuntimeError(f"本轮禁止 {name}：不输入文字、不切换输入法、不启动其他应用。")
    for field in {"Tap": ("element",), "Swipe": ("start", "end")}.get(name, ()):
        point = action.get(field)
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1000 for v in point
        ):
            raise RuntimeError("坐标无效。")
    if name == "Wait":
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:seconds)?", str(action.get("duration", "1 seconds")).strip())
        if not match or not 0 <= float(match[1]) <= 5:
            raise RuntimeError("等待只允许 0–5 秒。")


def clean_action(action):
    return {k: action[k] for k in ("_metadata", "action", "element", "start", "end", "duration") if k in action}


def approval_kind(action, answer):
    validate_action(action)
    if answer == "send" and action.get("action") == "Tap":
        return "send"
    if answer == "n" and action.get("action") in {"Tap", "Swipe", "Back"}:
        return "navigation"
    raise RuntimeError("未批准本次动作，停止；没有发送这个动作。")


def proposed_kind(action):
    """Explicit model intent, not an independent proof of a tap's effect."""
    validate_action(action)
    intent = action.get("message")
    if intent == "NAVIGATE" and action.get("action") in {"Tap", "Swipe", "Back"}:
        return "navigation"
    if intent == "SEND_ONE" and action.get("action") == "Tap":
        return "send"
    raise UnclassifiedAction("模型没有明确区分导航与发送；此步需要人工核对。")


class OneSend:
    """One durable attempt per phone+recipient in this experiment, across reruns."""
    def __init__(self, output_root, serial):
        token = hashlib.sha256((serial + "\0" + RECIPIENT).encode()).hexdigest()[:24]
        self.path = Path(output_root) / ("douyin-send-" + token + ".json")

    def require_unused(self):
        if self.path.exists():
            raise RuntimeError(f"已存在发送尝试记录，不自动重发（包括上次结果不确定）：{self.path}")

    def reserve(self, evidence):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Exclusive creation also blocks concurrent sessions from sending twice.
            with self.path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps({"status": "ATTEMPT_RESERVED_NOT_DELIVERY_PROOF", **evidence}, ensure_ascii=False, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise RuntimeError("已有发送尝试，拒绝重复发送。") from exc
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def require_same_context(before, after):
    """Display/Activity continuity, not proof of same dialog or chat contents."""
    for context in (before, after):
        if (not isinstance(context, dict) or type(context.get("display_id")) is not int
                or context["display_id"] <= 0 or not isinstance(context.get("activity"), str)
                or not re.fullmatch(re.escape(PACKAGE) + r"/[\w.$]+", context["activity"])):
            raise RuntimeError("副屏上下文缺失或不是本次抖音显示，停止动作。")
    if before != after:
        raise ContextChanged(before, after)


def require_same_regions(before, after, action, sending=False, *, before_context=None, after_context=None,
                         recipient_row=False):
    """Fail closed on target movement. This is NOT a recipient/semantic verifier."""
    from PIL import Image, ImageChops, ImageStat
    from io import BytesIO
    if action.get("action") == "Back" or before_context is not None or after_context is not None:
        require_same_context(before_context, after_context)
    with Image.open(BytesIO(before)) as left, Image.open(BytesIO(after)) as right:
        if left.size != (1080, 2400) or right.size != left.size:
            raise RuntimeError("帧尺寸变化，拒绝点击。")
        if action.get("action") == "Back":
            if sending:
                raise RuntimeError("返回动作不能被批准为发送。")
            # Back has no coordinate target. Video motion alone is not a stale
            # coordinate; caller still checks live owned display and primary IME.
            left.load()
            right.load()
            return
        if action.get("action") == "Tap":
            x, y = [int(v * size / 1000) for v, size in zip(action["element"], left.size)]
            boxes = [(max(0, x-64), max(0, y-64), min(1080, x+65), min(2400, y+65))]
            if recipient_row:
                # Candidate row center can be blank. Also compare its left-hand
                # avatar/text band; continuity still does not prove identity.
                boxes.append((32, max(0, y-72), 640, min(2400, y+73)))
            if sending:
                boxes.append((0, 70, 1080, 340))  # Also compare conversation header.
        else:
            boxes = [(0, 70, 1080, 2320)]
        for box in boxes:
            diff = ImageChops.difference(left.crop(box).convert("RGB"), right.crop(box).convert("RGB"))
            means = ImageStat.Stat(diff).mean
            if max(means) > 3:
                raise RegionChanged(box, means, sending)
