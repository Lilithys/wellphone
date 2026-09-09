"""Offline comparison tests. The only launched child below is a fake client."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from douyin_display import require_comparison_scope
from douyin_stream_trial import (BASE_CLIENT, SOURCE_MAX_FPS, TRIAL_CLIENT,
                                 require_stream_trial_scope, select_client, trial_metadata)


ROOT = Path(__file__).resolve().parents[1]


def arguments(**changes):
    values = dict(low_fps_trial=True, startup_only=True, confirm_home_first=True,
                  non_presentation=True, auto_messages=True, send_one_flow=False,
                  preflight=False, step_by_step=False)
    values.update(changes)
    return SimpleNamespace(**values)


class StreamTrialTests(unittest.TestCase):
    def test_default_client_and_missing_flag_are_unchanged(self):
        self.assertEqual(select_client("/fake", SimpleNamespace()), Path("/fake") / BASE_CLIENT)
        self.assertEqual(select_client("/fake", arguments(low_fps_trial=False)), Path("/fake") / BASE_CLIENT)
        require_stream_trial_scope(SimpleNamespace())

    def test_only_explicit_bounded_home_to_messages_scope_allowed(self):
        require_comparison_scope(arguments())
        for field in ("startup_only", "confirm_home_first", "non_presentation", "auto_messages"):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                require_comparison_scope(arguments(**{field: False}))
        for field in ("send_one_flow", "preflight", "step_by_step"):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                require_comparison_scope(arguments(**{field: True}))

    def test_send_mode_requires_explicit_flow_and_all_existing_safety_flags(self):
        require_comparison_scope(arguments(startup_only=False, send_one_flow=True))
        for field, value in (("confirm_home_first", False), ("non_presentation", False),
                             ("auto_messages", False), ("message", "11"), ("step_by_step", True),
                             ("preflight", True), ("startup_only", True), ("send_one_flow", False)):
            flags = vars(arguments(startup_only=False, send_one_flow=True)).copy()
            flags[field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                require_comparison_scope(SimpleNamespace(**flags))

    def test_missing_nonexecutable_and_symlink_launcher_never_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / TRIAL_CLIENT
            with self.assertRaises(RuntimeError):
                select_client(folder, arguments())
            path.parent.mkdir(parents=True)
            path.write_text("#!/bin/sh\nexit 1\n")
            path.chmod(0o600)
            with self.assertRaises(RuntimeError):
                select_client(folder, arguments())
            path.unlink()
            path.symlink_to(ROOT / TRIAL_CLIENT)
            with self.assertRaises(RuntimeError):
                select_client(folder, arguments())

    def test_bundled_launcher_is_selected_only_when_requested(self):
        self.assertEqual(select_client(ROOT, arguments()), ROOT / TRIAL_CLIENT)

    def test_launcher_preserves_pid_arguments_and_protection_environment(self):
        with tempfile.TemporaryDirectory(prefix="wellphone fps trial ") as folder:
            root = Path(folder)
            launcher, client = root / TRIAL_CLIENT, root / BASE_CLIENT
            launcher.parent.mkdir(parents=True)
            client.parent.mkdir(parents=True)
            launcher.write_bytes((ROOT / TRIAL_CLIENT).read_bytes())
            launcher.chmod(0o700)
            client.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                              "print(json.dumps({'pid':os.getpid(),'argv':sys.argv[1:],"
                              "'live':os.getenv('WELLPHONE_LIVE_MKV'),"
                              "'server':os.getenv('SCRCPY_SERVER_PATH')}))\n")
            client.chmod(0o700)
            argv = ["--serial", "fake-device", "--new-display=1080x2400/420",
                    "--display-ime-policy=local", "--no-audio", "--no-clipboard-autosync",
                    "--record=/fake/path with spaces/video.pipe", "--record-format=mkv",
                    "--window-title=literal $NOT_EXPANDED; quoted ' text"]
            env = {"PATH": os.defpath, "WELLPHONE_LIVE_MKV": "1",
                   "SCRCPY_SERVER_PATH": "/fake/non presentation server"}
            with subprocess.Popen([str(launcher), *argv], env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, err)
                data = json.loads(out)
                self.assertEqual(data["pid"], process.pid)
            self.assertEqual(data["argv"], [f"--max-fps={SOURCE_MAX_FPS}", *argv])
            self.assertEqual(data["live"], "1")
            self.assertEqual(data["server"], env["SCRCPY_SERVER_PATH"])

    def test_prepare_rejects_wrong_client_before_baseline_device_checks(self):
        from run_douyin_test import DouyinSession
        session = object.__new__(DouyinSession)
        session.args = arguments(client_path=str(ROOT / BASE_CLIENT))
        with patch("run_douyin_test.KeyboardTest.prepare") as prepare:
            with self.assertRaises(RuntimeError):
                session.prepare()
            prepare.assert_not_called()

    def test_cli_refuses_low_fps_full_send_without_home_gate_before_execution(self):
        from run_douyin_test import main
        argv = ["run", "--send-one-flow", "--non-presentation",
                "--auto-messages", "--low-fps-trial"]
        with patch.object(sys, "argv", argv), patch("run_douyin_test.verify_source") as verify, \
                patch("run_douyin_test.execute") as execute, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                main()
            self.assertEqual(stopped.exception.code, 2)
            verify.assert_not_called()
            execute.assert_not_called()

    def test_cli_selects_trial_but_never_changes_other_runtime_parameters(self):
        from run_douyin_test import main
        argv = ["run", "--startup-only", "--confirm-home-first", "--non-presentation",
                "--auto-messages", "--low-fps-trial", "--serial", "fake-device"]
        with patch.object(sys, "argv", argv), patch("sys.stdin.isatty", return_value=True), \
                patch("run_douyin_test.verify_source"), patch("run_douyin_test.os.umask"), \
                patch("run_douyin_test.signal.signal"), patch("run_douyin_test.execute", return_value=0) as execute:
            self.assertEqual(main(), 0)
        args = execute.call_args.args[0]
        self.assertEqual(args.client_path, str(ROOT / TRIAL_CLIENT))
        self.assertTrue(args.startup_only)
        self.assertFalse(args.send_one_flow)
        self.assertTrue(args.require_focus_flags and args.trace_focus and args.live_mkv)
        self.assertEqual(args.ime_policy, "local")
        self.assertFalse(args.no_system_decorations)

    def test_metadata_does_not_claim_measured_capture_freshness_or_acceptance(self):
        report = trial_metadata()
        self.assertEqual(report["requested_source_max_fps"], 5)
        self.assertEqual(report["native_size"], [1080, 2400])
        self.assertFalse(report["actual_source_fps_verified"])
        self.assertFalse(report["capture_latency_verified"])
        self.assertFalse(report["input_or_send_enabled"])

    def test_flow_metadata_requires_confirmation_and_does_not_claim_full_acceptance(self):
        report = trial_metadata(arguments(startup_only=False, send_one_flow=True))
        self.assertEqual(report["scope"], "supervised_one_message_flow")
        self.assertTrue(report["input_or_send_enabled"])
        self.assertTrue(report["input_and_send_require_confirmation"])
        self.assertFalse(report["full_flow_accepted"])
        self.assertFalse(report["capture_latency_verified"])

    def test_cli_full_flow_uses_same_trial_client_but_startup_only_remains_distinct(self):
        from run_douyin_test import main
        argv = ["run", "--send-one-flow", "--confirm-home-first", "--non-presentation",
                "--auto-messages", "--low-fps-trial", "--max-steps", "20", "--serial", "fake-device"]
        with patch.object(sys, "argv", argv), patch("sys.stdin.isatty", return_value=True), \
                patch("run_douyin_test.verify_source"), patch("run_douyin_test.os.umask"), \
                patch("run_douyin_test.signal.signal"), patch("run_douyin_test.execute", return_value=0) as execute:
            self.assertEqual(main(), 0)
        args = execute.call_args.args[0]
        self.assertEqual(args.client_path, str(ROOT / TRIAL_CLIENT))
        self.assertTrue(args.send_one_flow)
        self.assertFalse(args.startup_only)
        self.assertEqual(args.message, "1")
        self.assertEqual(args.max_steps, 20)
        self.assertTrue(args.require_focus_flags and args.trace_focus and args.live_mkv)


if __name__ == "__main__":
    unittest.main()
