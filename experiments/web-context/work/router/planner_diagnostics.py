"""Explain rejected model output without loosening validation or saving credentials."""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path


# These are static messages from the qualification-pinned validator. Never echo
# an arbitrary exception string: datetime/SDK exceptions may include input data.
REASONS = {
    "模型返回为空或过长。": "CONTENT_SIZE",
    "模型 JSON 包含重复键。": "DUPLICATE_JSON_KEY",
    "模型输出不符合目标清单格式。": "ROOT_SCHEMA",
    "模型目标数量无效。": "GOAL_COUNT",
    "澄清状态不能同时包含可执行目标。": "CLARIFICATION_SCHEMA",
    "模型没有提供目标或澄清问题。": "EMPTY_PLAN",
    "目标必须是对象。": "GOAL_TYPE",
    "目标或参数不在允许清单内；不执行模型指定的命令、URI 或工具名。": "GOAL_SCHEMA",
    "日程标题必须是 1–120 个字符。": "TITLE_LENGTH",
    "首版标题不支持控制字符、英文冒号和反斜杠；不猜测不同 content 命令的转义规则。": "TITLE_CHARACTERS",
    "日程时间必须是带时区的 ISO 字符串。": "TIME_TYPE",
    "时间缺少时区。": "TIMEZONE_MISSING",
    "开始时间必须在未来一年内。": "START_RANGE",
    "日程时长只支持 1 分钟至 8 小时。": "DURATION_RANGE",
    "日程标题不在用户原文中；必须澄清，不编造内容。": "TITLE_NOT_IN_REQUEST",
    "用户没有明确要求关于手机页面。": "GUI_NOT_IN_REQUEST",
}


def redact(text, api_key):
    # No key is put in a prompt. Still redact accidental echoes before persisting
    # a bounded task/model text, including JSON-escaped spellings of this key.
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
        text = text.replace(json.dumps(api_key, ensure_ascii=True)[1:-1], "[REDACTED]")
    return re.sub(r"(?i)\bsk-[a-z0-9_-]+", "[REDACTED]", text)


class PlanValidationError(ValueError):
    def __init__(self, cause, content, request, now, api_key):
        message = str(cause)
        if isinstance(cause, json.JSONDecodeError):
            code, reason = "JSON_SYNTAX", f"模型内容不是合法 JSON（第 {cause.lineno} 行，第 {cause.colno} 列）。"
        elif message in REASONS:
            code, reason = REASONS[message], message
        else:
            code, reason = "VALUE_FORMAT", "模型字段值无法解析，例如日期或时间格式不合法。"
        super().__init__(f"模型输出未通过目标校验 [{code}]：{reason} 未执行手机任务。")
        self.diagnostic = {
            "state": "REJECTED", "execution": "NOT_EXECUTED",
            "validation_code": code, "validation_reason": reason,
            "reference_time": now.isoformat(), "request": redact(request, api_key)[:2000],
            "model_content_type": type(content).__name__,
            "model_content": redact(content, api_key)[:12000] if isinstance(content, str) else None,
            "model_content_truncated": isinstance(content, str) and len(content) > 12000,
            "notice": "模型内容是不可信诊断数据，不是执行指令；只在本地保存，不保存请求头、密钥或思考内容。",
        }


def save_diagnostic(root, config, diagnostic):
    output = Path(root) / "outputs"
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="planner-diagnostic-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-", dir=output))
    path = directory / "result.json"
    data = {**diagnostic, "model": config.model, "provider": config.provider}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
