"""Untrusted source/model data never chooses execution commands or timestamps."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from router.planning import clean_title, now_local, validate_goal
from .sources import MAX_TEXT, public_url


def json_loads(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 包含重复键。")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("JSON 数值无效。")))
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("不是完整的严格 JSON；不自动修复。") from None


def fields(obj, expected):
    if not isinstance(obj, dict) or set(obj) != set(expected):
        raise ValueError("字段不符合固定 schema；不执行额外工具或命令。")


def text(value, limit, empty=False):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value.strip())
            or any(ord(c) < 32 and c not in "\n\t" for c in value)
            or any(c in value for c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")):
        raise ValueError("文字为空、过长或包含不支持的控制字符。")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_context(context, now=None):
    fields(context, {"schema", "timezone", "goal", "background", "priorities", "urls", "slots"})
    if type(context["schema"]) is not int or context["schema"] != 1 or context["timezone"] != "Asia/Shanghai":
        raise ValueError("首版只支持 schema 1 与 Asia/Shanghai 时区。")
    text(context["goal"], 1500)
    text(context["background"], 1500)
    if not isinstance(context["priorities"], list) or not 1 <= len(context["priorities"]) <= 6:
        raise ValueError("请给出 1–6 个复习关注点。")
    for item in context["priorities"]:
        text(item, 200)
    if not isinstance(context["urls"], list) or not 1 <= len(context["urls"]) <= 3:
        raise ValueError("请提供 1–3 个公开资料 URL。")
    urls = [public_url(url) for url in context["urls"]]
    if len(set(urls)) != len(urls):
        raise ValueError("资料 URL 重复，请先去重。")
    if not isinstance(context["slots"], list) or not 1 <= len(context["slots"]) <= 3:
        raise ValueError("请明确提供 1–3 个可用复习时段，不由模型猜测空闲时间。")
    ids, intervals = set(), []
    for slot in context["slots"]:
        fields(slot, {"id", "start", "end"})
        if not isinstance(slot["id"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,19}", slot["id"]) or slot["id"] in ids:
            raise ValueError("复习时段 ID 缺失、重复或格式无效。")
        ids.add(slot["id"])
        goal = validate_goal({"kind": "calendar.create", "title": "复习", "start": slot["start"], "end": slot["end"]}, now)
        start, end = datetime.fromisoformat(goal["start"]), datetime.fromisoformat(goal["end"])
        if not 15 * 60 <= (end-start).total_seconds() <= 120 * 60:
            raise ValueError("每个复习时段限制为 15–120 分钟。")
        if any(start < old_end and old_start < end for old_start, old_end in intervals):
            raise ValueError("用户提供的复习时段相互重叠。")
        intervals.append((start, end))
    return context


def validate_sources(sources, context):
    if not isinstance(sources, list) or len(sources) != len(context["urls"]):
        raise ValueError("资料未全部读取；不悄悄丢弃失败来源。")
    refs = {}
    for index, source in enumerate(sources, 1):
        fields(source, {"id", "url", "title", "body_sha256", "retrieved_at", "truncated", "paragraphs"})
        if source["id"] != f"S{index}" or type(source["truncated"]) is not bool:
            raise ValueError("来源 ID 或截断标记无效。")
        public_url(source["url"])
        from urllib.parse import urlsplit
        if urlsplit(public_url(context["urls"][index-1])).hostname != urlsplit(source["url"]).hostname:
            raise ValueError("来源域名不在用户提供的范围内。")
        text(source["title"], 200)
        if not isinstance(source["body_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", source["body_sha256"]):
            raise ValueError("来源正文哈希无效。")
        if not isinstance(source["retrieved_at"], str) or datetime.fromisoformat(source["retrieved_at"]).utcoffset() is None:
            raise ValueError("来源获取时间无效。")
        paragraphs = source["paragraphs"]
        if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= MAX_TEXT:
            raise ValueError("来源缺少正文。")
        used = 0
        for number, paragraph in enumerate(paragraphs, 1):
            fields(paragraph, {"id", "text"})
            expected = f"S{index}:P{number}"
            if paragraph["id"] != expected:
                raise ValueError("来源段落 ID 无效。")
            text(paragraph["text"], 1200)
            refs[expected] = paragraph["text"]
            used += len(paragraph["text"])
        if not 120 <= used <= MAX_TEXT:
            raise ValueError("来源正文字数无效。")
    return refs


def validate_plan(plan, context, sources, now=None):
    validate_context(context, now)
    allowed_refs = validate_sources(sources, context)
    fields(plan, {"status", "summary", "focus", "sessions", "question"})
    text(plan["summary"], 600, empty=True)
    if plan["status"] == "CLARIFY":
        if plan["focus"] != [] or plan["sessions"] != []:
            raise ValueError("澄清时不能同时包含复习安排。")
        text(plan["question"], 500)
        return plan
    if plan["status"] != "PLANNED" or plan["question"] is not None:
        raise ValueError("完整计划必须 status=PLANNED 且 question=null。")
    text(plan["summary"], 600)
    if not isinstance(plan["focus"], list) or not 1 <= len(plan["focus"]) <= 6:
        raise ValueError("资料要点必须为 1–6 项。")
    def references(values):
        if (not isinstance(values, list) or not 1 <= len(values) <= 4
                or any(not isinstance(v, str) or v not in allowed_refs for v in values)
                or len(values) != len(set(values))):
            raise ValueError("引用不存在、重复或不是给定资料的段落 ID。")
    for point in plan["focus"]:
        fields(point, {"point", "refs"})
        text(point["point"], 350)
        references(point["refs"])
    slots = {slot["id"]: slot for slot in context["slots"]}
    if not isinstance(plan["sessions"], list) or not 1 <= len(plan["sessions"]) <= len(slots):
        raise ValueError("复习安排数量超出提供的时段。")
    used = set()
    for item in plan["sessions"]:
        fields(item, {"slot_id", "title", "objective", "steps", "refs"})
        if not isinstance(item["slot_id"], str) or item["slot_id"] not in slots or item["slot_id"] in used:
            raise ValueError("模型选择了不存在或重复的时段；不接受模型自造日期。")
        used.add(item["slot_id"])
        if clean_title(item["title"]) != item["title"]:
            raise ValueError("日程标题含首尾空白。")
        text(item["objective"], 300)
        if not isinstance(item["steps"], list) or not 1 <= len(item["steps"]) <= 5:
            raise ValueError("复习步骤数量无效。")
        for step in item["steps"]:
            text(step, 250)
        references(item["refs"])
    return plan


def goals_for(plan, context):
    slots = {s["id"]: s for s in context["slots"]}
    return [validate_goal({"kind": "calendar.create", "title": item["title"],
                          "start": slots[item["slot_id"]]["start"], "end": slots[item["slot_id"]]["end"]})
            for item in plan["sessions"]]


def make_bundle(context, sources, plan, model):
    validate_plan(plan, context, sources)
    payload = {"schema": 1, "context": context, "sources": sources, "plan": plan,
               "model": model, "created_at": now_local().isoformat()}
    return {**payload, "sha256": digest(payload)}


def validate_bundle(bundle):
    fields(bundle, {"schema", "context", "sources", "plan", "model", "created_at", "sha256"})
    if type(bundle["schema"]) is not int or bundle["schema"] != 1:
        raise ValueError("计划包版本无效。")
    text(bundle["model"], 120)
    text(bundle["created_at"], 80)
    if bundle["sha256"] != digest({k: v for k, v in bundle.items() if k != "sha256"}):
        raise ValueError("计划包已发生变化；请重新生成并核对，不执行旧批准。")
    validate_plan(bundle["plan"], bundle["context"], bundle["sources"])
    return bundle


def escape_md(value):
    # Generated text is display data, not trusted HTML, image embeds or links.
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*{}\[\]()#+!_|])", r"\\\1", value).replace("\n", " ")


def render_brief(bundle):
    context, plan, sources = bundle["context"], bundle["plan"], bundle["sources"]
    lines = ["# Wellphone 复习简报", "", "目标：" + escape_md(context["goal"]),
             "", "背景（用户提供，不是网页验收证明）：" + escape_md(context["background"]),
             "", "## 为什么这样安排（模型建议）", "", escape_md(plan["summary"])]
    if plan["status"] == "CLARIFY":
        lines += ["", "需要澄清：" + escape_md(plan["question"])]
    else:
        lines += ["", "## 资料要点（模型归纳）", ""]
        for point in plan["focus"]:
            lines.append("- " + escape_md(point["point"]) + "〔" + ", ".join(point["refs"]) + "〕")
        slots = {s["id"]: s for s in context["slots"]}
        lines += ["", "## 复习安排（建议，尚未写入日历）", ""]
        for item in plan["sessions"]:
            slot = slots[item["slot_id"]]
            lines += ["### " + escape_md(item["title"]), "", slot["start"] + " → " + slot["end"],
                      "", escape_md(item["objective"]), ""]
            lines += [f"{i}. {escape_md(step)}" for i, step in enumerate(item["steps"], 1)]
            lines += ["", "参考段落：" + ", ".join(item["refs"]), ""]
    lines += ["", "## 来源与验证边界", "",
              "引用 ID 已检查存在于获取的正文；这不证明模型解释正确。请对照 evidence.md 逐项核对，sources.txt 保留所有读取段落。",
              "可用时段来自你的输入；没有读取已有日程，不能宣称已排除日历冲突。", ""]
    for source in sources:
        url = source["url"].replace("(", "%28").replace(")", "%29")
        lines.append(f'- {source["id"]} [{escape_md(source["title"])}]({url})；获取时间 {source["retrieved_at"]}' +
                     ("；正文已截断，只基于保留部分" if source["truncated"] else ""))
    return "\n".join(lines) + "\n"


def render_evidence(bundle):
    """Deterministic review aid, not another model or a semantic verifier."""
    plan, sources = bundle["plan"], bundle["sources"]
    refs = {p["id"]: (source, p["text"]) for source in sources for p in source["paragraphs"]}
    lines = ["# 引用核对", "", "以下是模型结论与其引用的提取段落，不是自动事实核查。",
             "请核对原文是否支持完整结论、适用版本是否一致；项目实施事实只来自已确认背景。",
             "段落来自正文提取；过长块可能拆分，截断来源不代表全文。", ""]
    for index, point in enumerate(plan["focus"], 1):
        lines += [f"## 要点 {index}", "", escape_md(point["point"]), ""]
        for ref in point["refs"]:
            source, paragraph = refs[ref]
            url = source["url"].replace("(", "%28").replace(")", "%29")
            lines += [f"### {ref} — [{escape_md(source['title'])}]({url})", "",
                      "来源截断：" + ("是" if source["truncated"] else "否"), "",
                      "> " + escape_md(paragraph), ""]
    if not plan["focus"]:
        lines.append("本次需要澄清，没有可执行安排或资料要点。")
    lines += ["## 学习安排的依据", "", "学习步骤是建议，不是来源给出的项目验收结论。", ""]
    for item in plan["sessions"]:
        lines += ["- " + escape_md(item["title"]) + "：" + ", ".join(item["refs"])]
    return "\n".join(lines) + "\n"
