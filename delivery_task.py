"""Local, deterministic natural-language task parsing; no model or device IO."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MessageTask:
    recipient: str
    message: str


def parse_task(text):
    if not isinstance(text, str) or not 1 <= len(text) <= 400:
        raise ValueError("请提供一条明确的抖音发送指令。")
    # Quoting makes names containing 发/发送 unambiguous. Do not infer omissions.
    quoted = re.fullmatch(r'\s*(?:请\s*)?(?:打开|在|用)?抖音\s*[，,]?\s*给\s*[「“\"]([^「」“”\"\n]+)[」”\"]\s*发送?\s*[「“\"]([^「」“”\"\n]+)[」”\"]\s*[。]?\s*', text)
    plain = re.fullmatch(r'\s*(?:请\s*)?(?:打开|在|用)?抖音\s*[，,]?\s*给\s*([^\s，,。；;「」“”\"]+?)\s*发送?\s*([^\s，,。；;「」“”\"]+)\s*', text)
    match = quoted or plain
    if not match:
        raise ValueError('格式不明确。例：在抖音给“测试联系人”发送“1”。只接受单联系人、单消息。')
    recipient, message = match.groups()
    if not 1 <= len(recipient) <= 64 or recipient != recipient.strip():
        raise ValueError("联系人名称须完整且不含首尾空白。")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in recipient):
        raise ValueError("联系人名称不能包含控制字符。")
    if not re.fullmatch(r"[A-Za-z0-9 .,!?_+@:#/()\-]{1,80}", message) or message != message.strip():
        raise ValueError("当前定向输入仅支持1–80个安全ASCII字符（数字/英文及常用标点）；不支持中文、换行或百分号。未连接手机。")
    return MessageTask(recipient, message)
