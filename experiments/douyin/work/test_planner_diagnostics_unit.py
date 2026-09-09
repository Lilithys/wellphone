"""Offline rejection diagnostics: preserve strictness, redact keys, never use adb."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from router import cli
from router.planning import parse_model_plan
from router.planner_client import planner_config, request_plan
from router.planner_diagnostics import PlanValidationError, save_diagnostic
from test_planner_client_unit import ENV, NOW, response, client_mock


class PlannerDiagnosticTests(unittest.TestCase):
    def reject(self, raw, request="方案评审，打开关于手机页面"):
        sdk = client_mock(response(raw))
        with patch("openai.OpenAI", sdk), self.assertRaises(PlanValidationError) as caught:
            request_plan(request, planner_config(ENV), "unit-sensitive-key", NOW)
        sdk.return_value.__enter__.return_value.chat.completions.create.assert_called_once()
        return caught.exception

    def test_specific_schema_and_clarification_reasons(self):
        for raw, code in [('{"goals":[],"question":null,"status":"ok"}', "ROOT_SCHEMA"),
                          ('{"goals":[{"kind":"gui.settings_about"}],"question":""}', "CLARIFICATION_SCHEMA"),
                          ('{"goals":[{"kind":"gui.settings_about","extra":true}],"question":null}', "GOAL_SCHEMA"),
                          ('{"goals":[],"goals":[],"question":"何时"}', "DUPLICATE_JSON_KEY"),
                          ('{"goals":[],"question":null}', "EMPTY_PLAN")]:
            with self.subTest(code=code):
                self.assertEqual(self.reject(raw).diagnostic["validation_code"], code)

    def test_title_timezone_and_date_range_are_distinguished(self):
        goal = {"kind": "calendar.create", "title": "方案评审", "start": "2026-09-09T16:00:00+08:00",
                "end": "2026-09-09T16:30:00+08:00"}
        for changes, code in [({"title": "模型编造的会议"}, "TITLE_NOT_IN_REQUEST"),
                              ({"start": "2026-09-09T16:00:00"}, "TIMEZONE_MISSING"),
                              ({"start": "2026-09-08T16:00:00+08:00"}, "START_RANGE"),
                              ({"end": "2026-09-09T15:00:00+08:00"}, "DURATION_RANGE"),
                              ({"start": "unit-sensitive-key"}, "VALUE_FORMAT")]:
            with self.subTest(code=code):
                error = self.reject(json.dumps({"goals": [{**goal, **changes}], "question": None}))
                self.assertEqual(error.diagnostic["validation_code"], code)
                self.assertNotIn("unit-sensitive-key", str(error))

    def test_json_error_reports_location_without_echoing_raw_text(self):
        error = self.reject('unit-sensitive-key not JSON')
        self.assertEqual(error.diagnostic["validation_code"], "JSON_SYNTAX")
        self.assertNotIn("unit-sensitive-key", str(error))
        self.assertIn("第 1 行", str(error))

    def test_model_and_request_are_redacted_before_local_save(self):
        error = self.reject('unit-sensitive-key sk-example-secret', 'unit-sensitive-key sk-other-secret')
        with tempfile.TemporaryDirectory() as root:
            path = save_diagnostic(root, planner_config(ENV), error.diagnostic)
            text = path.read_text()
            for secret in ['unit-sensitive-key', 'sk-example-secret', 'sk-other-secret']:
                self.assertNotIn(secret, text)
            data = json.loads(text)
            self.assertEqual(data["execution"], "NOT_EXECUTED")
            self.assertEqual(data["reference_time"], NOW.isoformat())
            self.assertNotIn("api_key", data)
            self.assertNotIn("reasoning_content", data)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_content_is_bounded_and_valid_json_is_replayable_offline(self):
        large = self.reject('x' * 13000)
        self.assertEqual(len(large.diagnostic["model_content"]), 12000)
        self.assertTrue(large.diagnostic["model_content_truncated"])
        error = self.reject('{"goals":[],"question":null}')
        with self.assertRaises(ValueError):
            parse_model_plan(error.diagnostic["model_content"], error.diagnostic["request"], NOW)

    def test_cli_failure_saves_one_diagnostic_and_never_contacts_phone(self):
        sdk = client_mock(response('{"goals":[],"question":null}'))
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, ENV, clear=True), \
                patch("router.cli.ROOT", Path(root)), patch("router.cli.ADB") as adb, \
                patch("openai.OpenAI", sdk), redirect_stdout(stdout):
            with self.assertRaises(PlanValidationError):
                cli.main(["plan", "打开关于手机页面", "--planner", "llm"])
            files = list((Path(root) / "outputs").glob("planner-diagnostic-*/result.json"))
            self.assertEqual(len(files), 1)
            self.assertIn(str(files[0]), stdout.getvalue())
            self.assertEqual(json.loads(files[0].read_text())["validation_code"], "EMPTY_PLAN")
            adb.assert_not_called()
        sdk.return_value.__enter__.return_value.chat.completions.create.assert_called_once()

    def test_write_failure_cannot_trigger_retry_or_success(self):
        sdk, stdout = client_mock(response('{"goals":[],"question":null}')), io.StringIO()
        with patch.dict(os.environ, ENV, clear=True), patch("openai.OpenAI", sdk), \
                patch("router.cli.save_diagnostic", side_effect=OSError("unit-secret")), \
                patch("router.cli.ADB") as adb, redirect_stdout(stdout):
            with self.assertRaises(PlanValidationError):
                cli.main(["plan", "打开关于手机页面", "--planner", "llm"])
        self.assertNotIn("unit-secret", stdout.getvalue())
        adb.assert_not_called()
        sdk.return_value.__enter__.return_value.chat.completions.create.assert_called_once()
