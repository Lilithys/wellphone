"""Offline CLI contract; never load phone code or create a real device client."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("delivery_entry", ROOT / "run.py")
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)
TASK = '在抖音给“测试联系人”发送“1”'


class DeliveryEntryTests(unittest.TestCase):
    def command(self, *args):
        return entry.build_command(entry.parse_args(args))

    def test_explicit_task_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            entry.parse_args([])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            entry.parse_args(["douyin"])

    def test_task_routes_to_legacy_model_actions_without_manual_json_probe(self):
        command = self.command("douyin", TASK)
        self.assertEqual(command[:3], [sys.executable, str(entry.DOUYIN_ENTRY), "douyin"])
        self.assertEqual(command[command.index("--max-steps") + 1], "20")
        self.assertEqual(command[command.index("--reviewer") + 1], "user")
        self.assertEqual(command[command.index("--message") + 1], "1")
        for flag in ("--send-one-flow", "--confirm-home-first",
                     "--non-presentation", "--auto-messages", "--low-fps-trial",
                     "--editor-read-mode"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--editor-read-mode") + 1], "activity-list")
        self.assertNotIn("--allow-editor-reobserve", command)
        for flag in ("auto", "--task", "--request-id", "--yes", "--executor-test"):
            self.assertNotIn(flag, command)

    def test_task_only_shorthand(self):
        self.assertEqual(self.command(TASK), self.command("douyin", TASK))

    def test_serial_and_budget(self):
        command = self.command(TASK, "--serial", "TEST", "--max-steps", "12")
        self.assertEqual(command[-2:], ["--serial", "TEST"])
        self.assertEqual(command[command.index("--max-steps") + 1], "12")

    def test_scope_overrides_and_invalid_input_rejected_before_execution(self):
        for args in (("router",), ("baseline",), ("douyin", TASK, "--message", "2"),
                     (TASK, "--max-steps", "0"), (TASK, "--max-steps", "21"),
                     (TASK, "--request-id", "../new"), (TASK, "--yes"),
                     ('抖音给“测试联系人”发送“你好”',),
                     ('抖音给“测试联系人”发送“2”',),
                     ('抖音给“测试联系人”发送“hello 1”',)):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    entry.parse_args(args)
                self.assertEqual(raised.exception.code, 2)

    def test_audio_can_only_restore(self):
        self.assertEqual(self.command("audio", "--restore", "--serial", "TEST"),
                         [sys.executable, str(entry.DOUYIN_ENTRY), "audio", "--restore", "--serial", "TEST"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            entry.parse_args(["audio"])

    def test_verify_and_tests_are_not_phone_modes(self):
        self.assertEqual(self.command("tests"), [sys.executable, str(entry.DOUYIN_ENTRY), "tests"])
        self.assertEqual(self.command("verify", "--files-only"),
                         [sys.executable, str(ROOT / "tools/check_release.py"), "--files-only"])

    def test_dry_run_never_executes(self):
        with patch.object(entry.os, "execve") as execute, patch.object(entry.subprocess, "run") as run:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(entry.main([TASK, "--dry-run"]), 0)
            execute.assert_not_called()
            run.assert_not_called()
            data = json.loads(output.getvalue())
            self.assertEqual(data["recipient"], "测试联系人")
            self.assertEqual(data["message"], "1")
            self.assertEqual(data["execution"], "NOT_EXECUTED")
            self.assertEqual(data["max_model_requests"], 20)
            self.assertTrue(data["operator_review_required"])

    def test_explicit_cli_delegates_without_prefilled_answers(self):
        with patch.object(entry.os, "execve") as execute, patch("builtins.input", side_effect=AssertionError("prompt")):
            entry.main([TASK, "--serial", "TEST"])
            execute.assert_called_once_with(sys.executable, self.command(TASK, "--serial", "TEST"),
                                            entry.child_environment(entry.parse_args([TASK, "--serial", "TEST"])))

    def test_task_recipient_is_process_local_and_not_a_default(self):
        with patch.dict(entry.os.environ, {"WELLPHONE_DOUYIN_RECIPIENT": "旧目标"}):
            self.assertEqual(entry.child_environment(entry.parse_args([TASK]))["WELLPHONE_DOUYIN_RECIPIENT"],
                             "测试联系人")
            self.assertEqual(entry.os.environ["WELLPHONE_DOUYIN_RECIPIENT"], "旧目标")
            self.assertNotIn("WELLPHONE_DOUYIN_RECIPIENT", entry.child_environment(entry.parse_args(["tests"])))

    def test_failed_entry_tests_prevent_next_process(self):
        with patch.object(entry.os, "execve") as execute:
            with patch.object(entry.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                self.assertEqual(entry.main(["tests"]), 1)
            execute.assert_not_called()

    def test_cli_help_exits_without_loading_dependencies(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "run.py"), "--help"],
                                capture_output=True, text=True, check=False, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("router", result.stdout)

    def test_dry_run_independent_of_working_directory(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "run.py"), TASK, "--dry-run"],
                                cwd=ROOT.parent, capture_output=True, text=True, check=False, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["message"], "1")


if __name__ == "__main__":
    unittest.main()
