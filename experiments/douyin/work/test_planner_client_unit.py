"""Synthetic credentials and mocked SDK only: no network, no phone, no real keys."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from router import cli
from router.planner_client import planner_config, request_plan
from router.planner_diagnostics import PlanValidationError
from router.planning import ZONE

ENV = {"WELLPHONE_PLANNER_MODEL": "deepseek-v4-flash",
       "WELLPHONE_PLANNER_BASE_URL": "https://api.deepseek.com",
       "DEEPSEEK_API_KEY": "unit-deepseek-secret", "PHONE_AGENT_API_KEY": "unit-gui-secret",
       "WELLPHONE_PLANNER_API_KEY": "unit-generic-secret"}
NOW = datetime(2026, 9, 8, 19, 0, tzinfo=ZONE)


def response(raw=None, finish="stop", tool_calls=None):
    raw = raw if raw is not None else '{"goals":[{"kind":"gui.settings_about"}],"question":null}'
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
                           message=SimpleNamespace(content=raw, tool_calls=tool_calls))])


def client_mock(result=None):
    sdk = MagicMock()
    sdk.return_value.__enter__.return_value.chat.completions.create.return_value = result or response()
    return sdk


class PlannerConfigTests(unittest.TestCase):
    def test_deepseek_selects_only_its_key(self):
        for base in ["https://api.deepseek.com", "https://api.deepseek.com/v1/", "https://API.DEEPSEEK.COM:443/"]:
            config = planner_config({**ENV, "WELLPHONE_PLANNER_BASE_URL": base})
            self.assertEqual(config.provider, "deepseek")
            self.assertEqual(config.key_name, "DEEPSEEK_API_KEY")
            self.assertNotIn("secret", repr(config))

    def test_other_and_lookalike_hosts_never_select_deepseek_or_gui_key(self):
        for base in ["https://open.bigmodel.cn/api/paas/v4", "https://api.deepseek.com.example.org"]:
            config = planner_config({**ENV, "WELLPHONE_PLANNER_BASE_URL": base})
            self.assertEqual(config.key_name, "WELLPHONE_PLANNER_API_KEY")
            self.assertEqual(config.provider, "openai_compatible")

    def test_bad_urls_are_rejected_without_echoing_credentials(self):
        for base in ["http://api.deepseek.com", "https://unit-secret@api.deepseek.com",
                     "https://api.deepseek.com?key=unit-secret", "https://api.deepseek.com/#unit-secret",
                     "https://api.deepseek.com:bad", "https://api.deepseek.com:1234",
                     "https://api.deepseek.com/anthropic", "https://api.deepseek.com/\n",
                     "https://api.deepseek.com\\@example.org"]:
            with self.subTest(base=base):
                with self.assertRaises(ValueError) as caught:
                    planner_config({**ENV, "WELLPHONE_PLANNER_BASE_URL": base})
                self.assertNotIn("unit-secret", str(caught.exception))

    def test_model_is_explicit(self):
        with self.assertRaises(ValueError):
            planner_config({"DEEPSEEK_API_KEY": "unit-secret"})


class PlannerTransportTests(unittest.TestCase):
    def test_prompt_explicitly_specifies_both_nullable_question_branches(self):
        sdk = client_mock()
        with patch("openai.OpenAI", sdk):
            request_plan("打开关于手机页面", planner_config(ENV), "unit-key", NOW)
        prompt = sdk.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn('question 必须是 JSON null', prompt)
        self.assertIn('禁止用空字符串表示不需要澄清', prompt)
        self.assertIn('{"goals":[{"kind":"gui.settings_about"}],"question":null}', prompt)
        self.assertIn('{"goals":[],"question":"请提供日程的开始与结束时间。"}', prompt)
        self.assertIn('示例只说明 JSON 格式，不是本次任务', prompt)

    def test_captured_empty_question_remains_rejected_without_normalization(self):
        # Regression for 2026-09-08 19:37 diagnostic. No real API call or phone IO.
        goals = [{"kind": "calendar.create", "title": "方案评审", "start": "2026-09-09T16:00:00+08:00",
                  "end": "2026-09-09T16:30:00+08:00"}, {"kind": "gui.settings_about"}]
        task = "帮我把明天下午四点到四点半的「方案评审」记入日历，然后打开设置里的关于手机页面，不修改设置。"
        for question in ["", "null", "请确认时间"]:
            raw = json.dumps({"goals": goals, "question": question}, ensure_ascii=False)
            with self.subTest(question=question), patch("openai.OpenAI", client_mock(response(raw))):
                with self.assertRaises(PlanValidationError) as caught:
                    request_plan(task, planner_config(ENV), "unit-key", NOW)
                self.assertEqual(caught.exception.diagnostic["validation_code"], "CLARIFICATION_SCHEMA")
                self.assertEqual(caught.exception.diagnostic["model_content"], raw)

    def test_same_goals_with_explicit_null_pass_without_other_changes(self):
        goals = [{"kind": "calendar.create", "title": "方案评审", "start": "2026-09-09T16:00:00+08:00",
                  "end": "2026-09-09T16:30:00+08:00"}, {"kind": "gui.settings_about"}]
        raw = json.dumps({"goals": goals, "question": None}, ensure_ascii=False)
        with patch("openai.OpenAI", client_mock(response(raw))):
            plan = request_plan("明天下午四点到四点半，方案评审，打开关于手机页面", planner_config(ENV), "unit-key", NOW)
        self.assertEqual(plan["status"], "PLANNED")
        self.assertEqual(plan["goals"], goals)
        self.assertIsNone(plan["question"])

    def test_deepseek_non_thinking_json_and_single_bounded_request(self):
        sdk = client_mock()
        with patch("openai.OpenAI", sdk):
            plan = request_plan("打开关于手机页面", planner_config(ENV), "unit-key", NOW)
        self.assertEqual(plan["source"], "llm")
        self.assertEqual(plan["goals"], [{"kind": "gui.settings_about"}])
        sdk.assert_called_once_with(api_key="unit-key", base_url="https://api.deepseek.com", timeout=35, max_retries=0)
        create = sdk.return_value.__enter__.return_value.chat.completions.create
        create.assert_called_once()
        params = create.call_args.kwargs
        self.assertEqual(params["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(params["response_format"], {"type": "json_object"})
        self.assertEqual(params["max_tokens"], 1200)
        self.assertNotIn("tools", params)
        self.assertEqual(params["messages"][1], {"role": "user", "content": "打开关于手机页面"})
        self.assertNotIn("unit-key", json.dumps(params))

    def test_generic_request_does_not_receive_deepseek_options(self):
        config = planner_config({**ENV, "WELLPHONE_PLANNER_BASE_URL": "https://example.org/v1"})
        sdk = client_mock()
        with patch("openai.OpenAI", sdk):
            request_plan("打开关于手机页面", config, "unit-key", NOW)
        self.assertNotIn("extra_body", sdk.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs)

    def test_calendar_output_uses_existing_strict_validator(self):
        goal = {"kind": "calendar.create", "title": "方案评审", "start": "2026-09-09T16:00:00+08:00",
                "end": "2026-09-09T16:30:00+08:00"}
        raw = json.dumps({"goals": [goal], "question": None})
        with patch("openai.OpenAI", client_mock(response(raw))):
            plan = request_plan("明天下午四点到四点半，把方案评审记入日历", planner_config(ENV), "unit-key", NOW)
        self.assertEqual(plan["goals"], [goal])

    def test_missing_info_clarifies(self):
        with patch("openai.OpenAI", client_mock(response('{"goals":[],"question":"会议何时结束？"}'))):
            plan = request_plan("明天开会", planner_config(ENV), "unit-key", NOW)
        self.assertEqual(plan["status"], "CLARIFY")
        self.assertEqual(plan["goals"], [])

    def test_truncation_tool_calls_empty_and_multiple_choices_rejected(self):
        bad = [response(finish="length"), response(finish="content_filter"), response(finish=None),
               response(tool_calls=[{"name": "shell"}]), SimpleNamespace(choices=[]),
               SimpleNamespace(choices=response().choices * 2)]
        for result in bad:
            with self.subTest(result=result), patch("openai.OpenAI", client_mock(result)), self.assertRaises(ValueError):
                request_plan("打开关于手机页面", planner_config(ENV), "unit-key", NOW)

    def test_invalid_payload_is_not_repaired_or_echoed(self):
        for raw in ["unit-secret", "```json\n{}\n```", '{"goals":[{"kind":"shell"}],"question":null}']:
            with patch("openai.OpenAI", client_mock(response(raw))), self.assertRaises(ValueError) as caught:
                request_plan("打开关于手机页面", planner_config(ENV), "unit-key", NOW)
            self.assertNotIn("unit-secret", str(caught.exception))

    def test_provider_errors_are_sanitized_without_retry(self):
        sdk = client_mock()
        create = sdk.return_value.__enter__.return_value.chat.completions.create
        create.side_effect = RuntimeError("Authorization: unit-secret")
        with patch("openai.OpenAI", sdk), self.assertRaises(RuntimeError) as caught:
            request_plan("打开关于手机页面", planner_config(ENV), "unit-key", NOW)
        self.assertNotIn("unit-secret", str(caught.exception))
        create.assert_called_once()

    def test_invalid_request_or_key_never_constructs_client(self):
        for request, key in [("x" * 2001, "unit-key"), ("", "unit-key"), ("x", ""), ("x", " "), ("x", "...")]:
            with patch("openai.OpenAI") as sdk, self.assertRaises(ValueError):
                request_plan(request, planner_config(ENV), key, NOW)
            sdk.assert_not_called()


class PlannerCliTests(unittest.TestCase):
    def test_plan_uses_only_deepseek_key_and_never_constructs_adb(self):
        sdk, stdout = client_mock(), io.StringIO()
        with patch.dict(os.environ, ENV, clear=True), patch("openai.OpenAI", sdk), \
                patch("router.cli.ADB") as adb, redirect_stdout(stdout):
            self.assertEqual(cli.main(["plan", "打开关于手机页面", "--planner", "llm"]), 0)
            self.assertEqual(os.environ["PHONE_AGENT_API_KEY"], "unit-gui-secret")
        self.assertEqual(sdk.call_args.kwargs["api_key"], "unit-deepseek-secret")
        self.assertIn("NOT_EXECUTED", stdout.getvalue())
        self.assertNotIn("unit-deepseek-secret", stdout.getvalue())
        self.assertNotIn("unit-gui-secret", stdout.getvalue())
        adb.assert_not_called()

    def test_missing_deepseek_key_does_not_fall_back(self):
        env = {k: v for k, v in ENV.items() if k != "DEEPSEEK_API_KEY"}
        with patch.dict(os.environ, env, clear=True), patch("router.cli.sys.stdin.isatty", return_value=False), \
                patch("router.cli.request_plan") as request, self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
            cli.understand(SimpleNamespace(planner="llm", task="打开关于手机页面"))
        request.assert_not_called()

    def test_interactive_prompt_is_provider_scoped_and_does_not_save_key(self):
        env = {k: v for k, v in ENV.items() if k != "DEEPSEEK_API_KEY"}
        with patch.dict(os.environ, env, clear=True), patch("router.cli.sys.stdin.isatty", return_value=True), \
                patch("router.cli.getpass.getpass", return_value="unit-entered") as prompt, \
                patch("router.cli.request_plan", return_value={}) as request, redirect_stdout(io.StringIO()):
            cli.understand(SimpleNamespace(planner="llm", task="打开关于手机页面"))
            self.assertNotIn("DEEPSEEK_API_KEY", os.environ)
            self.assertEqual(os.environ["PHONE_AGENT_API_KEY"], "unit-gui-secret")
        self.assertIn("DEEPSEEK_API_KEY", prompt.call_args.args[0])
        self.assertEqual(request.call_args.args[2], "unit-entered")

    def test_rules_never_reads_or_calls_planner(self):
        with patch("router.cli.planner_config") as config, patch("router.cli.request_plan") as request:
            cli.understand(SimpleNamespace(planner="rules", task="打开关于手机页面"))
        config.assert_not_called()
        request.assert_not_called()
