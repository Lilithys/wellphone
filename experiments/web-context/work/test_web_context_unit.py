"""Offline tests with synthetic Web, SDK and Calendar Provider; no live IO."""
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from router.calendar_tool import save_qualification
from router.planner_client import planner_config
from router.planning import now_local
from test_router_unit import FakeProvider, STATE
from web_context import cli, sources
from web_context.planner import SYSTEM, StudyValidationError, request_study
from web_context.schema import (digest, goals_for, json_loads, make_bundle, render_brief, render_evidence,
                                validate_bundle, validate_context, validate_plan, validate_sources)


def context():
    start = (now_local() + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
    return {"schema": 1, "timezone": "Asia/Shanghai", "goal": "准备手机 Agent 项目技术面试",
            "background": "已实现基础功能，需要解释设计取舍", "priorities": ["焦点与输入法隔离"],
            "urls": ["https://docs.example.com/input"],
            "slots": [{"id": "A", "start": start.isoformat(), "end": (start + timedelta(minutes=45)).isoformat()}]}


def source_list():
    return [{"id": "S1", "url": "https://docs.example.com/input", "title": "技术资料",
             "body_sha256": "a" * 64, "retrieved_at": now_local().isoformat(), "truncated": False,
             "paragraphs": [{"id": "S1:P1", "text": "输入路由与显示焦点是需要分别验证的系统行为。" * 8}]}]


def plan():
    return {"status": "PLANNED", "summary": "已有实现基础，因此优先练习解释边界。",
            "focus": [{"point": "分别讨论显示与输入路由", "refs": ["S1:P1"]}],
            "sessions": [{"slot_id": "A", "title": "复习显示与输入路由", "objective": "能解释一个失败场景",
                          "steps": ["阅读资料并列出前提", "用自己的项目解释一次失败"], "refs": ["S1:P1"]}],
            "question": None}


def bundle():
    return make_bundle(context(), source_list(), plan(), "unit-model")


class SchemaTests(unittest.TestCase):
    def test_valid_plan_and_exact_user_slots(self):
        ctx = context()
        validate_plan(plan(), ctx, source_list())
        self.assertEqual(goals_for(plan(), ctx)[0]["start"], ctx["slots"][0]["start"])

    def test_missing_slots_fail_before_any_network(self):
        ctx = context()
        ctx["slots"] = []
        with self.assertRaises(ValueError):
            validate_context(ctx)

    def test_invalid_or_overlapping_slots(self):
        for change in ["past", "naive", "overlap", "duplicate", "short"]:
            ctx = context()
            slot = ctx["slots"][0]
            if change == "past":
                slot["start"] = "2000-01-01T00:00:00+08:00"
            if change == "naive":
                slot["start"] = slot["start"][:-6]
            if change == "overlap":
                ctx["slots"].append({**slot, "id": "B"})
            if change == "duplicate":
                ctx["slots"].append(dict(slot))
            if change == "short":
                slot["end"] = slot["start"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_context(ctx)

    def test_extra_command_or_unknown_source_not_accepted(self):
        for obj in [{**plan(), "command": "input text"}, {**plan(), "question": ""}]:
            with self.assertRaises(ValueError):
                validate_plan(obj, context(), source_list())
        for mutate in (lambda p: p["sessions"][0].update(start="model-generated-time"),
                       lambda p: p["sessions"][0].update(slot_id="B"),
                       lambda p: p["sessions"][0].update(title="bad:title"),
                       lambda p: p["focus"][0].update(refs=["S9:P1"])):
            candidate = plan()
            mutate(candidate)
            with self.assertRaises(ValueError):
                validate_plan(candidate, context(), source_list())

    def test_clarification_excludes_execution(self):
        candidate = {"status": "CLARIFY", "summary": "", "focus": [], "sessions": [], "question": "资料与目标不相关，请换资料。"}
        validate_plan(candidate, context(), source_list())
        candidate["sessions"] = plan()["sessions"]
        with self.assertRaises(ValueError):
            validate_plan(candidate, context(), source_list())

    def test_duplicate_json_nan_and_markdown_rejected(self):
        for raw in ['{"a":1,"a":2}', '{"a":NaN}', '```json\n{}\n```']:
            with self.assertRaises(ValueError):
                json_loads(raw)

    def test_missing_source_wrong_host_or_paragraph_rejected(self):
        for mutate in (lambda s: s.clear(), lambda s: s[0].update(url="https://other.example.com/page"),
                       lambda s: s[0]["paragraphs"][0].update(id="S2:P1")):
            src = source_list()
            mutate(src)
            with self.assertRaises(ValueError):
                validate_sources(src, context())

    def test_bundle_integrity_and_expired_slots_rechecked(self):
        value = bundle()
        validate_bundle(value)
        value["plan"]["sessions"][0]["title"] = "篡改"
        with self.assertRaises(ValueError):
            validate_bundle(value)
        value["sha256"] = digest({k: v for k, v in value.items() if k != "sha256"})
        with patch("router.planning.now_local", return_value=now_local()+timedelta(days=3)), self.assertRaises(ValueError):
            validate_bundle(value)

    def test_render_escapes_model_html_and_embedded_images(self):
        value = bundle()
        value["plan"]["summary"] = '<script>hello</script> ![x](https://evil.example/a)'
        rendered = render_brief(value)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("![x]", rendered)
        self.assertIn("S1:P1", rendered)
        self.assertIn("没有读取已有日程", rendered)

    def test_evidence_pairs_actual_paragraph_and_claim_without_verification_claim(self):
        value = bundle()
        rendered = render_evidence(value)
        self.assertIn(value["plan"]["focus"][0]["point"], rendered)
        self.assertIn(value["sources"][0]["paragraphs"][0]["text"], rendered)
        self.assertIn("S1:P1", rendered)
        self.assertIn("不是自动事实核查", rendered)
        value["sources"][0]["paragraphs"][0]["text"] = '<img src="secret"> ![x](https://evil.example)'
        rendered = render_evidence(value)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("![x]", rendered)

    def test_clarification_evidence_has_no_execution_claim(self):
        value = bundle()
        value["plan"] = {"status": "CLARIFY", "summary": "", "focus": [], "sessions": [], "question": "请补充资料"}
        self.assertIn("没有可执行安排", render_evidence(value))

    def test_project_template_distinguishes_actual_focus_patch_from_global_config(self):
        self.assertIn("OWN_FOCUS", cli.DEFAULT_BACKGROUND)
        self.assertIn("STEAL_TOP_FOCUS_DISABLED", cli.DEFAULT_BACKGROUND)
        self.assertIn("没有修改全局", cli.DEFAULT_BACKGROUND)
        self.assertIn("没有修改 /vendor/etc/input-port-associations.xml", cli.DEFAULT_BACKGROUND)
        self.assertIn("尚未验证两套软键盘", cli.DEFAULT_BACKGROUND)
        self.assertEqual(len(cli.DEFAULT_URLS), 3)
        self.assertTrue(any(url.endswith("/ime-support") for url in cli.DEFAULT_URLS))
        self.assertFalse(any(url.endswith("/input-routing") for url in cli.DEFAULT_URLS))


class SourceTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"WELLPHONE_WEB_PROXY": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_private_credential_query_or_non_https_urls_rejected(self):
        for url in ["http://example.com", "file:///tmp/a", "https://localhost/x", "https://127.0.0.1/x",
                    "https://169.254.169.254/x", "https://224.0.0.1/x", "https://u:p@example.com",
                    "https://example.com?token=a", "https://example.com:444/a", "https://x.internal/x",
                    "https://example.com/\nCookie:", "https://example.com\\@localhost"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                sources.public_url(url)

    def test_dns_mixed_public_private_or_multicast_rejected(self):
        for addresses in [["127.0.0.1"], ["8.8.8.8", "10.0.0.2"], ["224.0.0.1"]]:
            rows = [(None, None, None, None, (ip, 443)) for ip in addresses]
            with patch("web_context.sources.socket.getaddrinfo", return_value=rows), self.assertRaises(ValueError):
                sources.resolve_public("docs.example.com")

    def test_numeric_ip_pinned_with_hostname_tls(self):
        connection = sources.PinnedHTTPS("docs.example.com", "8.8.8.8", 5)
        connection._context = Mock()
        with patch("web_context.sources.socket.create_connection") as connect:
            connection.connect()
        connect.assert_called_once_with(("8.8.8.8", 443), 5)
        connection._context.wrap_socket.assert_called_once_with(connect.return_value, server_hostname="docs.example.com")

    def test_explicit_proxy_only_loopback_and_official_target(self):
        for url in ["http://user:pass@127.0.0.1:7897", "http://10.0.0.1:7897", "https://127.0.0.1:7897", "http://127.0.0.1:7897/path"]:
            with self.assertRaises(ValueError):
                sources.local_proxy(url)
        with patch.dict(os.environ, {"WELLPHONE_WEB_PROXY": "http://127.0.0.1:7897"}), \
             patch("web_context.sources.http.client.HTTPSConnection") as connection, \
             patch("web_context.sources.resolve_public", side_effect=AssertionError("DNS delegated only for fixed hosts")):
            sources.connect_to("source.android.com", 10)
            connection.return_value.set_tunnel.assert_called_once_with("source.android.com", 443)
            self.assertEqual(connection.call_args.args[:2], ("127.0.0.1", 7897))
            self.assertTrue(connection.call_args.kwargs["context"].check_hostname)
            with self.assertRaises(ValueError):
                sources.connect_to("arbitrary.example.com", 10)

    def test_proxy_still_rejects_cross_domain_redirect(self):
        connection = self.fake_fetch([(302, {"Location": "https://developer.android.com/elsewhere"})])
        with patch.dict(os.environ, {"WELLPHONE_WEB_PROXY": "http://127.0.0.1:7897"}), \
             patch("web_context.sources.http.client.HTTPSConnection", return_value=connection), self.assertRaises(ValueError):
            sources.fetch("https://source.android.com/page")
        connection.close.assert_called_once()

    def test_extracts_main_omits_script_nav_and_hidden_content(self):
        text = "可读技术正文。" * 30
        html = f"<html><title>测试</title><nav>导航广告</nav><main><script>秘密脚本</script><p>{text}</p><p hidden>隐藏数据</p></main></html>"
        title, paragraphs, truncated = sources.extract(html, "text/html")
        self.assertEqual(title, "测试")
        self.assertEqual("".join(paragraphs), text)
        self.assertFalse(truncated)

    def test_short_and_oversize_text_handling(self):
        with self.assertRaises(ValueError):
            sources.extract("<div>请登录</div>", "text/html")
        _, paragraphs, truncated = sources.extract("x" * 30000, "text/plain")
        self.assertTrue(truncated)
        self.assertLessEqual(sum(map(len, paragraphs)), sources.MAX_TEXT)

    def test_html_soft_wrap_and_inline_code_are_one_paragraph(self):
        sentence = "Android can dispatch display-specific input events to the focused window of that display."
        html = ("<main><p>Android can dispatch\n display-specific <code>input events</code> "
                "to the <a href='/x'>focused window</a>\n of that display.</p><p>" + "其他正文。" * 30 + "</p></main>")
        _, paragraphs, _ = sources.extract(html, "text/html")
        self.assertEqual(paragraphs[0], sentence)
        self.assertEqual(len(paragraphs), 2)

    def test_plain_text_wraps_join_but_blank_lines_separate(self):
        _, paragraphs, _ = sources.extract("First sentence\ncontinues here.\n\n" + "Second paragraph. " * 10, "text/plain")
        self.assertEqual(paragraphs[0], "First sentence continues here.")
        self.assertEqual(len(paragraphs), 2)

    def test_table_cells_do_not_concatenate_words(self):
        _, paragraphs, _ = sources.extract("<main><table><tr><td>display</td><td>focus</td></tr></table><p>" +
                                          "Other explanatory text. " * 10 + "</p></main>", "text/html")
        self.assertEqual(paragraphs[0], "display focus")

    def test_long_blocks_prefer_sentence_or_word_boundaries(self):
        sentence = "This is a sentence with a clear boundary. "
        _, paragraphs, _ = sources.extract("<p>" + sentence * 70 + "</p>", "text/html")
        self.assertTrue(all(p.endswith(".") and len(p) <= 1200 for p in paragraphs))
        self.assertEqual(" ".join(paragraphs), (sentence * 70).strip())
        words = "longword " * 500
        pieces = list(sources.paragraph_pieces(words.strip()))
        self.assertTrue(all(p.endswith("longword") and len(p) <= 1200 for p in pieces))
        self.assertEqual(" ".join(pieces), words.strip())

    def test_cjk_and_unbroken_token_chunks_stay_within_schema_limit(self):
        for text in ["完整的一句话。" * 500, "x" * 2401, "x" * 1199 + "。\"" + "z" * 1300]:
            pieces = list(sources.paragraph_pieces(text))
            self.assertTrue(all(0 < len(p) <= 1200 for p in pieces))
            self.assertEqual("".join(pieces), text)

    def fake_fetch(self, responses):
        class Response:
            def __init__(self, status, headers, body=b""):
                self.status, self.headers, self.body = status, headers, io.BytesIO(body)
            def getheader(self, key, default=None):
                return self.headers.get(key, default)
            def read1(self, size):
                return self.body.read(size)
        connection = Mock()
        connection.getresponse.side_effect = [Response(*r) for r in responses]
        return connection

    def test_fetch_sends_no_cookie_authorization_and_observes_redirect(self):
        connection = self.fake_fetch([(302, {"Location": "/final"}),
                                     (200, {"Content-Type": "text/plain"}, b"technical text" * 20)])
        with patch("web_context.sources.resolve_public", return_value="8.8.8.8"), \
             patch("web_context.sources.PinnedHTTPS", return_value=connection):
            url, text, _, _ = sources.fetch("https://docs.example.com/start")
        self.assertEqual(url, "https://docs.example.com/final")
        self.assertIn("technical", text)
        for call in connection.request.call_args_list:
            self.assertEqual(call.args[0], "GET")
            self.assertNotIn("Cookie", call.kwargs["headers"])
            self.assertNotIn("Authorization", call.kwargs["headers"])
        self.assertEqual(connection.close.call_count, 2)

    def test_unsupported_and_cross_domain_responses_fail(self):
        cases = [(302, {"Location": "https://other.example.com/x"}),
                 (302, {"Location": "http://docs.example.com/x"}),
                 (403, {}), (200, {"Content-Type": "application/pdf"}),
                 (200, {"Content-Type": "text/html", "Content-Encoding": "gzip"}),
                 (200, {"Content-Type": "text/html", "Content-Length": "99999999"})]
        for response in cases:
            connection = self.fake_fetch([response])
            with self.subTest(response=response), patch("web_context.sources.resolve_public", return_value="8.8.8.8"), \
                 patch("web_context.sources.PinnedHTTPS", return_value=connection), self.assertRaises(ValueError):
                sources.fetch("https://docs.example.com/start")
            connection.close.assert_called_once()


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.config = planner_config({"WELLPHONE_PLANNER_MODEL": "unit-model", "WELLPHONE_PLANNER_BASE_URL": "https://api.deepseek.com"})

    def response(self, raw=None, finish="stop", tools=None):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
            message=SimpleNamespace(content=json.dumps(plan()) if raw is None else raw, tool_calls=tools))])

    def call_model(self, response):
        client = Mock()
        client.__enter__, client.__exit__ = Mock(return_value=client), Mock()
        client.chat.completions.create.return_value = response
        with patch("openai.OpenAI", return_value=client) as factory:
            output = request_study(context(), source_list(), self.config, "fake-secret-key")
        return output, client, factory

    def test_payload_includes_only_explicit_context_sources_and_no_tools(self):
        result, client, factory = self.call_model(self.response())
        self.assertEqual(result, plan())
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
        self.assertEqual(factory.call_args.kwargs["timeout"], 45)
        args = client.chat.completions.create.call_args.kwargs
        self.assertNotIn("tools", args)
        self.assertEqual(args["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertNotIn("fake-secret-key", json.dumps(args))
        payload = json.loads(args["messages"][1]["content"])
        self.assertEqual(set(payload), {"now", "context", "untrusted_sources"})

    def test_truncated_tool_call_and_unknown_refs_rejected(self):
        for response in [self.response(finish="length"), self.response(tools=["write"]), self.response(raw='{}')]:
            with self.assertRaises(ValueError):
                self.call_model(response)

    def test_diagnostic_redacts_and_does_not_repair(self):
        with self.assertRaises(StudyValidationError) as error:
            self.call_model(self.response(raw='{"extra":"fake-secret-key"}'))
        self.assertNotIn("fake-secret-key", error.exception.body)
        self.assertIn("REDACTED", error.exception.body)

    def test_prompt_separates_project_facts_sources_and_suggestions(self):
        self.assertIn("不能把文档介绍的配置方法写成用户项目已经使用的方法", SYSTEM)
        self.assertIn("不能把固定 ASCII 验收推导为通用中文输入", SYSTEM)
        self.assertIn("refs 必须支持该结论的完整语义", SYSTEM)


class PrepareTests(unittest.TestCase):
    def test_cancel_never_fetches_or_connects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.json"
            path.write_text(json.dumps(context()))
            with patch("web_context.cli.sys.stdin.isatty", return_value=True), \
                 patch("web_context.cli.planner_config", return_value=SimpleNamespace(model="test", base_url="test", key_name="TEST_KEY")), \
                 patch("builtins.input", return_value="no"), patch("web_context.cli.read_source") as fetch, \
                 patch("web_context.cli.ADB") as adb, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.prepare(SimpleNamespace(context=path)), 2)
            fetch.assert_not_called()
            adb.assert_not_called()

    def test_plan_saves_artifacts_but_no_device_and_no_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "context.json"
            path.write_text(json.dumps(context()))
            config = SimpleNamespace(model="test", provider="test", base_url="test", key_name="TEST_KEY")
            with patch("web_context.cli.ROOT", root), patch("web_context.cli.sys.stdin.isatty", return_value=True), \
                 patch("web_context.cli.planner_config", return_value=config), patch.dict(os.environ, {"TEST_KEY": "fake"}), \
                 patch("builtins.input", return_value="yes"), patch("web_context.cli.read_source", return_value=source_list()[0]), \
                 patch("web_context.cli.request_study", return_value=plan()), patch("web_context.cli.ADB") as adb, \
                 patch("web_context.cli.CalendarTool") as calendar, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.prepare(SimpleNamespace(context=path)), 0)
            adb.assert_not_called()
            calendar.assert_not_called()
            output = next((root / "outputs").glob("web-plan-*"))
            self.assertTrue((output / "brief.md").is_file())
            self.assertTrue((output / "evidence.md").is_file())
            self.assertFalse(cli.read_json(output / "result.json")["semantic_grounding_verified"])
            validate_bundle(cli.read_json(output / "bundle.json"))
            self.assertEqual(cli.read_json(output / "result.json")["state"], "PLANNED_NOT_EXECUTED")


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = FakeProvider()
        self.addCleanup(self.fake.db.close)
        self.path = self.root / "bundle.json"
        self.path.write_text(json.dumps(bundle()))
        save_qualification(self.root, STATE, 1, "unit-evidence")

    def apply(self, answers=None, is_tty=True):
        with patch("web_context.cli.ROOT", self.root), patch("web_context.cli.state_root", return_value=self.root), \
             patch("web_context.cli.sys.stdin.isatty", return_value=is_tty), patch("web_context.cli.ADB", return_value=self.fake), \
             patch("web_context.cli.countdown"), patch("web_context.cli.time.sleep"), \
             patch("builtins.input", side_effect=answers or ["yes", "正常", "无"]), \
             patch("web_context.cli.request_study", side_effect=AssertionError("no model in apply")), \
             patch.dict(os.environ, {}), redirect_stdout(io.StringIO()):
            return cli.apply_bundle(SimpleNamespace(bundle=self.path, serial=None, calendar_id=1))

    def test_provider_readback_and_second_run_reuses_same_event(self):
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.fake.inserts, 1)
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.fake.inserts, 1)
        reports = [cli.read_json(p) for p in (self.root / "outputs").glob("web-apply-*/result.json")]
        self.assertTrue(all(r["passed"] for r in reports))
        self.assertTrue(any(r["operations"][0]["reused"] for r in reports))

    def test_cancel_does_not_write(self):
        self.assertEqual(self.apply(["no"]), 2)
        self.assertEqual(self.fake.inserts, 0)

    def test_unknown_effect_never_retried(self):
        self.fake.fail_before = True
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.fake.inserts, 1)
        self.fake.fail_before = False
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.fake.inserts, 1)

    def test_human_anomaly_fails_even_when_event_written(self):
        self.assertEqual(self.apply(["yes", "异常", "无"]), 1)
        self.assertEqual(self.fake.inserts, 1)
        result = cli.read_json(next((self.root / "outputs").glob("web-apply-*/result.json")))
        self.assertTrue(result["effect_verified"])
        self.assertTrue(result["partial_effect"])
        self.assertFalse(result["isolation_verified"])

    def test_unqualified_does_not_write_or_ask_approval(self):
        with patch("web_context.cli.qualified", return_value=False):
            self.assertEqual(self.apply([]), 2)
        self.assertEqual(self.fake.inserts, 0)

    def test_expired_bundle_rejected_before_phone(self):
        with patch("router.planning.now_local", return_value=now_local()+timedelta(days=3)), self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.fake.inserts, 0)

    def test_noninteractive_rejected(self):
        with self.assertRaises(ValueError):
            self.apply(is_tty=False)
        self.assertEqual(self.fake.inserts, 0)

    def test_state_changes_after_approval_stop_before_write(self):
        self.fake.probe = Mock(side_effect=[STATE, {**STATE, "keyboard_on_primary": False}])
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.fake.inserts, 0)

    def test_interruption_records_durable_attempt_and_closes_monitor(self):
        original_shell = self.fake.shell
        def shell(*args):
            if args[:2] == ("content", "insert"):
                raise KeyboardInterrupt
            return original_shell(*args)
        self.fake.shell = shell
        self.assertEqual(self.apply(), 1)
        result = cli.read_json(next((self.root / "outputs").glob("web-apply-*/result.json")))
        self.assertEqual(result["state"], "INTERRUPTED")
        self.assertTrue(result["operations"][0]["journal_write_started"])
        self.assertFalse(result["operations"][0]["effect_verified"])
        self.fake.shell = original_shell
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.fake.inserts, 0)

    def test_two_sessions_second_failure_keeps_first_and_does_not_retry(self):
        ctx, candidate = context(), plan()
        start = now_local() + timedelta(days=2)
        ctx["slots"].append({"id": "B", "start": start.isoformat(), "end": (start+timedelta(minutes=30)).isoformat()})
        candidate["sessions"].append({**candidate["sessions"][0], "slot_id": "B", "title": "第二次复习"})
        self.path.write_text(json.dumps(make_bundle(ctx, source_list(), candidate, "unit-model")))
        original_shell = self.fake.shell
        def shell(*args):
            if args[:2] == ("content", "insert") and self.fake.inserts == 1:
                self.fake.fail_before = True
            return original_shell(*args)
        self.fake.shell = shell
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.fake.inserts, 2)
        result = cli.read_json(next((self.root / "outputs").glob("web-apply-*/result.json")))
        self.assertTrue(result["operations"][0]["effect_verified"])
        self.assertFalse(result["operations"][1]["effect_verified"])
        self.assertTrue(result["partial_effect"])
        self.fake.shell = original_shell
        self.fake.fail_before = False
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.fake.inserts, 2)


if __name__ == "__main__":
    unittest.main()
