"""Offline transport parity and privacy checks. Never connect to ADB."""
import json
import subprocess
import unittest
from unittest.mock import patch

from douyin_diagnostics import DiagnosticADB, PACKAGE, command_category
from router.device import ADB, DeviceError


class DiagnosticTests(unittest.TestCase):
    def test_opt_in_audio_read_recheck_requires_clean_identical_mode_and_records_failure(self):
        adb = DiagnosticADB("fake-phone", recheck_audio_read=True)
        original = subprocess.CompletedProcess([], 0, "PLAY_AUDIO: ignore\n", "unclassified stderr")
        clean = subprocess.CompletedProcess([], 0, "PLAY_AUDIO: ignore\n", "")
        with patch("douyin_diagnostics.subprocess.run", side_effect=[original, clean]) as run:
            self.assertEqual(adb.shell("cmd", "appops", "get", "--user", "0", PACKAGE, "PLAY_AUDIO"), clean.stdout)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0], run.call_args_list[1])
        self.assertEqual(adb.failures[0]["read_recheck"]["status"], "CLEAN_IDENTICAL_MODE_CONFIRMED")
        self.assertNotIn("unclassified stderr", json.dumps(adb.failures))

    def test_recheck_never_accepts_changed_mode_second_warning_error_or_timeout(self):
        original = subprocess.CompletedProcess([], 0, "PLAY_AUDIO: ignore\n", "warning")
        seconds = [subprocess.CompletedProcess([], 0, "PLAY_AUDIO: allow\n", ""), original,
                   subprocess.CompletedProcess([], 1, "PLAY_AUDIO: ignore\n", ""),
                   subprocess.CompletedProcess([], 0, "unknown", ""),
                   subprocess.TimeoutExpired([], 15)]
        for second in seconds:
            adb = DiagnosticADB("fake-phone", recheck_audio_read=True)
            with patch("douyin_diagnostics.subprocess.run", side_effect=[original, second]) as run, self.assertRaises(DeviceError):
                adb.shell("cmd", "appops", "get", "--user", "0", PACKAGE, "PLAY_AUDIO")
            self.assertEqual(run.call_count, 2)

    def test_opt_in_never_retries_write_input_other_query_or_unknown_first_output(self):
        for command in [("cmd", "appops", "set", "--user", "0", PACKAGE, "PLAY_AUDIO", "ignore"),
                        ("input", "keyboard", "-d", "51", "text", "1"), ("dumpsys", "audio")]:
            with patch("douyin_diagnostics.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "PLAY_AUDIO: ignore\n", "warning")) as run, self.assertRaises(DeviceError):
                DiagnosticADB("fake-phone", recheck_audio_read=True).shell(*command)
            run.assert_called_once()
        for stdout, stderr, rc in [("unknown", "warning", 0), ("PLAY_AUDIO: ignore", "warning", 1),
                                   ("PLAY_AUDIO: ignore", "Permission Denial", 0)]:
            with patch("douyin_diagnostics.subprocess.run", return_value=subprocess.CompletedProcess([], rc, stdout, stderr)) as run, self.assertRaises(DeviceError):
                DiagnosticADB("fake-phone", recheck_audio_read=True).shell("cmd", "appops", "get", "--user", "0", PACKAGE, "PLAY_AUDIO")
            run.assert_called_once()

    def test_exact_transport_and_success_output_unchanged(self):
        failures = []
        adb = DiagnosticADB("fake-phone", failures=failures)
        result = subprocess.CompletedProcess([], 0, "unchanged\n", "")
        with patch("douyin_diagnostics.subprocess.run", return_value=result) as run:
            self.assertEqual(adb.shell("dumpsys", "audio"), "unchanged\n")
        run.assert_called_once_with(["adb", "-s", "fake-phone", "shell", "dumpsys audio"],
                                    capture_output=True, text=True, timeout=15)
        self.assertEqual(failures, [])

    def test_frozen_fail_closed_rules_preserved(self):
        cases = [(0, "", ""), (0, "normal", " \n"), (1, "", ""),
                 (0, "normal", "warning"), (0, "normal", "fatal"),
                 (0, "Error: system", ""), (0, "\n Exception occurred", ""),
                 (0, "Permission Denial", ""), (0, "  [ERROR] foo", ""),
                 (0, "SecurityException", ""), (0, "Unknown command: foo", ""),
                 (0, "Error while dumping", ""), (0, "normal: Error in history", "")]
        for returncode, stdout, stderr in cases:
            outcomes = []
            for cls in (ADB, DiagnosticADB):
                with self.subTest(cls=cls.__name__, rc=returncode, stdout=stdout, stderr=stderr):
                    result = subprocess.CompletedProcess([], returncode, stdout, stderr)
                    with patch("douyin_diagnostics.subprocess.run", return_value=result) as run:
                        try:
                            value = cls("fake-phone").shell("dumpsys", "audio")
                            outcomes.append((True, value))
                        except DeviceError:
                            outcomes.append((False, None))
                    run.assert_called_once()  # No retry, even for read-only failures.
            self.assertEqual(outcomes[0], outcomes[1])

    def test_failure_metadata_does_not_leak_command_or_output(self):
        secret = "fake-secret-private-chat-text"
        failures = []
        adb = DiagnosticADB("private-serial", failures=failures)
        result = subprocess.CompletedProcess([], 1, "Error: " + secret, secret)
        with patch("douyin_diagnostics.subprocess.run", return_value=result), self.assertRaises(DeviceError) as error:
            adb.shell("unknown", secret)
        serialized = json.dumps(failures) + str(error.exception)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("private-serial", serialized)
        self.assertEqual(failures[0]["command_category"], "unclassified_command")
        self.assertEqual(failures[0]["failure_signals"], ["NONZERO_EXIT", "STDERR_PRESENT", "ERROR_OUTPUT"])
        self.assertEqual(failures[0]["returncode"], 1)

    def test_timeout_retains_unknown_write_outcome_without_retry_or_private_details(self):
        adb = DiagnosticADB("fake-phone")
        failure = subprocess.TimeoutExpired(["fake-private-command"], 15, output=b"private-output", stderr=b"private-error")
        with patch("douyin_diagnostics.subprocess.run", side_effect=failure) as run, self.assertRaises(DeviceError) as error:
            adb.shell("cmd", "appops", "set", "--user", "0", PACKAGE, "PLAY_AUDIO", "ignore")
        run.assert_called_once()
        self.assertIn("写入结果可能不确定", str(error.exception))
        self.assertEqual(adb.failures[0]["failure_kind"], "TIMEOUT")
        self.assertEqual(adb.failures[0]["command_category"], "audio.write.package.PLAY_AUDIO")
        self.assertNotIn("private", json.dumps(adb.failures) + str(error.exception))

    def test_process_start_failure_is_sanitized_and_not_retried(self):
        adb = DiagnosticADB("fake-phone")
        with patch("douyin_diagnostics.subprocess.run", side_effect=OSError(2, "private path")) as run, self.assertRaises(DeviceError) as error:
            adb.shell("dumpsys", "audio")
        run.assert_called_once()
        self.assertEqual(adb.failures[0]["errno"], 2)
        self.assertNotIn("private path", json.dumps(adb.failures) + str(error.exception))

    def test_missing_identity_never_dispatches(self):
        with patch("douyin_diagnostics.subprocess.run") as run, self.assertRaises(DeviceError):
            DiagnosticADB().shell("dumpsys", "audio")
        run.assert_not_called()

    def test_categories_are_fixed_not_echoed_arguments(self):
        self.assertEqual(command_category(("pm", "list", "packages", "--uid", "10240")), "identity.uid_sharing")
        self.assertEqual(command_category(("dumpsys", "input_method")), "state.ime")
        for op in ("PLAY_AUDIO", "TAKE_AUDIO_FOCUS"):
            for target, scope in ((PACKAGE, "package"), ("10240", "uid")):
                self.assertEqual(command_category(("cmd", "appops", "get", "--user", "0", target, op)),
                                 f"audio.read.{scope}.{op}")
        for args in (("pm", "list", "packages", "--uid", "private"),
                     ("cmd", "appops", "get", "--user", "0", PACKAGE, "private"),
                     ("cmd", "appops", "get", "--user", "0", "private", "PLAY_AUDIO"),
                     ("cmd", "appops", "set", "--user", "0", PACKAGE, "PLAY_AUDIO", "private")):
            self.assertEqual(command_category(args), "unclassified_command")


if __name__ == "__main__":
    unittest.main()
