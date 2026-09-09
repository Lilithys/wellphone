"""Synthetic UI metadata and mock input only; no phone/model connections."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from douyin_input import FixedOneActions, OneDraft, require_editor, target_on_display, ONE_SYSTEM_PROMPT
from douyin_policy import OneSend, PACKAGE
from run_douyin_test import PinnedMain, SupervisedActions
from test_douyin_unit import CONTEXT, TAP, frame
from phone_agent.actions.handler import ActionResult
from ascii_focus_guard import client_dump_metadata

ACTIVITIES = f"""Display #0 (activities from top to bottom):
 topResumedActivity=ActivityRecord{{aaaa u0 com.hihonor.mms/.Compose t8}}
Display #51 (activities from top to bottom):
 topResumedActivity=ActivityRecord{{beef u0 {PACKAGE}/.splash.SplashActivity t9}}
"""
WINDOWS = f"""Display: mDisplayId=0 (organized)
 mCurrentFocus=Window{{aa u0 com.hihonor.mms/.Compose}}
Display: mDisplayId=51 (organized)
 mCurrentFocus=Window{{face u0 {PACKAGE}/.splash.SplashActivity}}
"""
CLIENT = f"""ACTIVITY {PACKAGE}/.splash.SplashActivity beef pid=123 userId=0 uid=10240 displayId=51(type=VIRTUAL)
 View Hierarchy:
  android.widget.EditText{{bb VFED..CL. .F...... 0,0-900,120 #123 {PACKAGE}:id/editor}}
  PRIVATE-CHAT-MUST-NOT-BE-SAVED
"""
TYPE_ONE = {"_metadata": "do", "action": "Type", "text": "1", "message": "INPUT_ONE"}


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client_dump = CLIENT
        def shell(*args):
            if args == ("dumpsys", "activity", "activities"):
                return ACTIVITIES
            if args == ("dumpsys", "window", "displays"):
                return WINDOWS
            if args == ("dumpsys", "activity", "-c", "-p", PACKAGE, "-d", 51, "beef"):
                return self.client_dump
            if args == ("input", "keyboard", "-d", 51, "text", "1"):
                self.assertTrue(self.draft.path.is_file())
                self.assertTrue(self.session.report["fixed_input_attempted"])
                return ""
            raise AssertionError("Unexpected command: " + str(args))
        self.session = SimpleNamespace(output=self.root, display_id=51,
            args=SimpleNamespace(step_by_step=False),
            report={"send_attempted": False, "requested_message": "1", "fixed_input_verified": False},
            reference=frame(), reference_context=CONTEXT.copy(), last_capture_context=CONTEXT.copy(),
            context=Mock(return_value=CONTEXT.copy()), capture=Mock(return_value=frame()),
            shell=Mock(side_effect=shell), save=Mock(), preview=Mock(), scope=Mock(),
            audio_lease=SimpleNamespace(data={"uid": 10240}))
        self.delegate = Mock()
        self.delegate.execute.return_value = ActionResult(True, False)
        self.send = OneSend(self.root, "fake")
        self.draft = OneDraft(self.root, "fake")
        self.navigation = SupervisedActions(self.delegate, self.session, self.send)
        self.handler = FixedOneActions(self.navigation, self.session, self.draft)

    def run_input(self, answers=("type 1", "yes"), action=None):
        with patch("builtins.input", side_effect=answers), patch("run_douyin_test.countdown"):
            return self.handler.execute(TYPE_ONE if action is None else action, 1080, 2400)

    def input_calls(self):
        return [call for call in self.session.shell.call_args_list if call.args[0] == "input"]

    def test_scopes_activity_and_window_to_same_nonprimary_app(self):
        target = target_on_display(ACTIVITIES, WINDOWS, 51)
        self.assertEqual(target["activity_token"], "beef")
        self.assertEqual(target["window_token"], "face")
        for display in (0, True, None, "51", 52):
            with self.assertRaises(RuntimeError):
                target_on_display(ACTIVITIES, WINDOWS, display)

    def test_wrong_ambiguous_or_other_user_window_rejected(self):
        for activities, windows in [(ACTIVITIES + ACTIVITIES, WINDOWS),
            (ACTIVITIES.replace(PACKAGE, "com.other"), WINDOWS),
            (ACTIVITIES, WINDOWS.replace("u0 " + PACKAGE, "u10 " + PACKAGE)),
            (ACTIVITIES, WINDOWS.replace("SplashActivity", "OtherActivity"))]:
            with self.assertRaises(RuntimeError):
                target_on_display(activities, windows, 51)

    def test_editor_inspection_is_scoped_and_never_saves_raw_chat(self):
        result = require_editor(self.session)
        self.assertEqual(result["status"], "focused_editor")
        self.assertNotIn("PRIVATE-CHAT", json.dumps(self.session.report))
        self.session.shell.assert_any_call("dumpsys", "activity", "-c", "-p", PACKAGE, "-d", 51, "beef")
        self.assertEqual(self.input_calls(), [])

    def use_activity_list(self, client=None):
        from test_douyin_activity_client_unit import CLIENT as LIST_CLIENT
        self.session.args.editor_read_mode = "activity-list"
        ordinary = self.session.shell.side_effect
        def shell(*args):
            if args == ("dumpsys", "activity", "-c", "-p", PACKAGE, "-d", 51, "activities"):
                return LIST_CLIENT if client is None else client
            return ordinary(*args)
        self.session.shell.side_effect = shell

    def test_list_reader_is_scoped_and_runs_only_once_without_fallback(self):
        self.use_activity_list()
        evidence = require_editor(self.session)
        self.assertEqual(evidence["read_mode"], "activity-list")
        self.assertEqual(evidence["status"], "focused_editor")
        self.assertEqual(len(evidence["client_read_attempts"]), 1)
        self.assertNotIn("PRIVATE", json.dumps(self.session.report))
        self.assertEqual(self.input_calls(), [])

    def test_list_reader_timeout_never_falls_back_or_retries(self):
        from test_douyin_activity_client_unit import CLIENT as LIST_CLIENT
        self.use_activity_list(LIST_CLIENT + "Failure while dumping the activity: java.io.IOException: Timeout")
        self.session.args.allow_editor_reobserve = True
        with self.assertRaises(RuntimeError): require_editor(self.session)
        calls = [call for call in self.session.shell.call_args_list if call.args[:3] == ("dumpsys", "activity", "-c")]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[-1], "activities")
        self.assertEqual(self.input_calls(), [])

    def test_list_reader_rechecks_window_and_preserves_fixed_input_dispatch(self):
        self.use_activity_list()
        target = target_on_display(ACTIVITIES, WINDOWS, 51)
        with patch("douyin_input.read_target", side_effect=[target, {**target, "window_token": "changed"}]), \
                self.assertRaisesRegex(RuntimeError, "窗口变化"):
            require_editor(self.session)
        self.assertTrue(self.run_input().success)
        self.assertEqual(len(self.input_calls()), 1)
        self.assertEqual(self.input_calls()[0].args, ("input", "keyboard", "-d", 51, "text", "1"))

    def test_failed_dump_is_never_used_even_if_it_contains_a_focused_editor(self):
        self.client_dump = CLIENT + "Failure while dumping the activity: private detail\n"
        with self.assertRaisesRegex(RuntimeError, "转储不完整"):
            require_editor(self.session)
        self.assertEqual(self.input_calls(), [])
        self.assertEqual(len(self.session.report["fixed_editor_checks"][-1]["client_read_attempts"]), 1)
        self.assertNotIn("private detail", json.dumps(self.session.report))

    def test_client_failure_diagnostics_are_fixed_labels_not_exception_text(self):
        for suffix, kind in [
            ("Failure while dumping the activity: java.io.IOException: Timeout", "transfer_pipe_timeout"),
            ("Failure while dumping the activity: java.io.IOException: PRIVATE-DETAIL", "io_exception"),
            ("Failure while dumping the activity: SecurityException: PRIVATE-DETAIL", "security_exception"),
            ("Got a RemoteException while dumping the activity", "remote_exception"),
            ("Failure while dumping the activity: PRIVATE-DETAIL", "unclassified"),
        ]:
            metadata = client_dump_metadata(CLIENT + suffix)
            self.assertEqual(metadata["error_categories"], ["client_dump_failed"])
            self.assertEqual(metadata["client_failures"][0]["kind"], kind)
            self.assertNotIn("PRIVATE-", json.dumps(metadata))
            self.assertGreater(metadata["line_count"], 0)
            self.assertGreater(metadata["output_bytes"], 0)

    def test_repeated_timeout_remains_blocked_and_has_read_timing(self):
        self.session.args.allow_editor_reobserve = True
        self.client_dump = CLIENT + "Failure while dumping the activity: java.io.IOException: Timeout\n"
        with self.assertRaisesRegex(RuntimeError, "transfer_pipe_timeout"):
            require_editor(self.session)
        reads = self.session.report["fixed_editor_checks"][-1]["client_read_attempts"]
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(item["elapsed_ms"] >= 0 for item in reads))
        self.assertEqual(self.input_calls(), [])
        self.delegate.execute.assert_not_called()

    def test_executor_reobserves_partial_client_once_on_same_target_without_clicking(self):
        self.session.args.executor_test = True
        ordinary = self.session.shell.side_effect
        reads = iter([CLIENT + "Failure while dumping the activity: private detail\n", CLIENT])
        def shell(*args):
            return next(reads) if args[:3] == ("dumpsys", "activity", "-c") else ordinary(*args)
        self.session.shell.side_effect = shell
        result = require_editor(self.session)
        self.assertEqual(result["status"], "focused_editor")
        self.assertEqual(len(result["client_read_attempts"]), 2)
        self.assertEqual(self.input_calls(), [])
        self.delegate.execute.assert_not_called()
        self.assertNotIn("private detail", json.dumps(self.session.report))

    def test_cli_authorized_flow_reobserves_scoped_dump_once_without_input(self):
        self.session.args.allow_editor_reobserve = True
        ordinary = self.session.shell.side_effect
        reads = iter([CLIENT + "Failure while dumping the activity: private detail\n", CLIENT])
        self.session.shell.side_effect = lambda *args: next(reads) if args[:3] == ("dumpsys", "activity", "-c") else ordinary(*args)
        result = require_editor(self.session)
        self.assertEqual(len(result["client_read_attempts"]), 2)
        self.assertEqual(result["status"], "focused_editor")
        self.assertEqual(self.input_calls(), [])
        self.assertFalse(getattr(self.session.args, "executor_test", False))

    def test_second_partial_wrong_owner_permission_or_real_unfocused_dump_still_blocks(self):
        self.session.args.executor_test = True
        for candidate in (CLIENT + "Failure while dumping the activity: detail\n",
                          CLIENT.replace("uid=10240", "uid=1000") + "Failure while dumping the activity: detail\n",
                          CLIENT + "Permission Denial\n",
                          CLIENT.replace(".F......", "........")):
            self.client_dump = candidate
            with self.assertRaises(RuntimeError):
                require_editor(self.session)
            count = len(self.session.report["fixed_editor_checks"][-1]["client_read_attempts"])
            self.assertEqual(count, 2 if candidate == CLIENT + "Failure while dumping the activity: detail\n" else 1)
        self.assertEqual(self.input_calls(), [])

    def test_partial_dump_does_not_reobserve_a_different_display_target(self):
        self.session.args.executor_test = True
        self.client_dump = CLIENT + "Failure while dumping the activity: detail\n"
        target = target_on_display(ACTIVITIES, WINDOWS, 51)
        with patch("douyin_input.read_target", side_effect=[target, {**target, "window_token":"different"}]), \
                self.assertRaisesRegex(RuntimeError, "目标变化"):
            require_editor(self.session)
        self.assertEqual(len(self.session.report["fixed_editor_checks"][-1]["client_read_attempts"]), 1)
        self.assertEqual(self.input_calls(), [])

    def test_uid_user_display_or_unfocused_editor_blocks_input(self):
        for candidate in [CLIENT.replace("uid=10240", "uid=1000"), CLIENT.replace("userId=0", "userId=1"),
                          CLIENT.replace("displayId=51", "displayId=0"), CLIENT.replace(".F......", "........"),
                          CLIENT.replace("android.widget.EditText", "android.widget.TextView")]:
            self.client_dump = candidate
            result = self.run_input(("type 1",))
            self.assertFalse(result.success)
            self.assertEqual(self.input_calls(), [])
            self.assertFalse(self.draft.path.exists())

    def test_only_fixed_exact_protocol_is_accepted(self):
        for change in [{"text": "11"}, {"text": "1\n"}, {"text": "你好"}, {"message": "NAVIGATE"},
                       {"message": "INPUT_ONE", "extra": "execute"}, {"text": 1}]:
            result = self.run_input((), {**TYPE_ONE, **change})
            self.assertFalse(result.success)
            self.assertEqual(self.input_calls(), [])
        self.delegate.execute.assert_not_called()

    def test_cancel_does_not_inspect_editor_or_type(self):
        result = self.run_input(("no",))
        self.assertFalse(result.success)
        self.session.shell.assert_not_called()
        self.assertFalse(self.draft.path.exists())

    def test_fixed_input_targets_display_and_never_calls_generic_type(self):
        result = self.run_input()
        self.assertTrue(result.success, result.message)
        self.assertFalse(result.should_finish)
        self.assertEqual(len(self.input_calls()), 1)
        self.assertEqual(self.input_calls()[0].args, ("input", "keyboard", "-d", 51, "text", "1"))
        self.delegate.execute.assert_not_called()
        self.assertTrue(self.session.report["fixed_input_verified"])
        self.assertFalse(self.session.report["send_attempted"])

    def test_unconfirmed_input_never_sends_or_retypes(self):
        result = self.run_input(("type 1", "no"))
        self.assertFalse(result.success)
        self.assertFalse(self.session.report["fixed_input_verified"])
        self.assertFalse(self.send.path.exists())
        result = self.run_input(())
        self.assertFalse(result.success)
        self.assertEqual(len(self.input_calls()), 1)

    def test_input_journal_survives_restart_and_is_not_send_proof(self):
        self.assertTrue(self.run_input().success)
        with self.assertRaises(RuntimeError):
            OneDraft(self.root, "fake").require_unused()
        OneSend(self.root, "fake").require_unused()
        self.assertNotIn("message_verified_by_user", self.session.report)

    def test_transport_failure_retains_attempt_without_retry(self):
        normal = self.session.shell.side_effect
        def broken(*args):
            if args[0] == "input":
                raise TimeoutError("unknown input outcome")
            return normal(*args)
        self.session.shell.side_effect = broken
        result = self.run_input(("type 1",))
        self.assertFalse(result.success)
        self.assertTrue(self.draft.path.exists())
        self.assertFalse(self.session.report["fixed_input_verified"])
        self.assertFalse(self.send.path.exists())

    def test_changed_context_stops_before_input(self):
        self.session.context.return_value = {**CONTEXT, "display_id": 0}
        self.assertFalse(self.run_input(()).success)
        self.assertEqual(self.input_calls(), [])

    def test_send_before_verified_digit_is_blocked(self):
        result = self.handler.execute({**TAP, "message": "SEND_ONE"}, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()
        self.assertFalse(self.send.path.exists())

    def test_end_to_end_input_then_one_reviewed_send(self):
        self.assertTrue(self.run_input().success)
        with patch("builtins.input", return_value="send 示例联系人"), patch("run_douyin_test.countdown"):
            result = self.handler.execute({**TAP, "message": "SEND_ONE"}, 1080, 2400)
        self.assertTrue(result.success, result.message)
        self.assertTrue(result.should_finish)
        self.delegate.execute.assert_called_once()
        self.assertEqual(json.loads(self.send.path.read_text())["requested_message"], "1")

    def test_no_navigation_after_digit_inserted(self):
        self.assertTrue(self.run_input().success)
        result = self.handler.execute({**TAP, "message": "NAVIGATE"}, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()

    def test_prompt_allows_only_digit_and_separates_input_from_send(self):
        self.assertIn('text="1", message="INPUT_ONE"', ONE_SYSTEM_PROMPT)
        self.assertIn("取消表情任务", ONE_SYSTEM_PROMPT)
        self.assertIn("不按回车发送", ONE_SYSTEM_PROMPT)


class MainDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.state = {"time": "test", "serial": "fake", "connected": True, "top_focused_display_id": 0,
                      "keyboard_on_primary": True, "default_ime": "original", "main_package": "sms",
                      "main_activity": "sms/Compose", "android_user": 0}
        self.adb = Mock()
        self.guard = PinnedMain(self.adb, self.state)

    def test_success_preserves_exact_rules_and_one_probe(self):
        self.adb.state.return_value = self.state
        self.assertEqual(self.guard.require(), self.state)
        self.adb.state.assert_called_once()
        self.assertEqual(self.guard.failed_checks, [])

    def test_failed_state_is_recorded_without_arbitrary_ui_fields_or_retry(self):
        bad = {**self.state, "main_activity": "sms/List", "private_text": "DO NOT SAVE"}
        self.adb.state.return_value = bad
        with self.assertRaises(RuntimeError):
            self.guard.require()
        failure = self.guard.failed_checks[0]
        self.assertEqual(failure["observed"]["main_activity"], "sms/List")
        self.assertEqual(failure["expected_activity"], "sms/Compose")
        self.assertNotIn("DO NOT SAVE", json.dumps(failure))
        self.adb.state.assert_called_once()

    def test_focus_ime_and_unknown_state_still_fail_closed(self):
        for updates in [{"top_focused_display_id": 51}, {"keyboard_on_primary": False},
                        {"default_ime": "other"}, {"main_package": None}, {"android_user": 1}]:
            self.adb.state.return_value = {**self.state, **updates}
            with self.assertRaises(RuntimeError):
                self.guard.require()
        self.assertEqual(len(self.guard.failed_checks), 5)

    def test_probe_failure_is_unknown_not_stale_success(self):
        self.adb.state.side_effect = TimeoutError("probe timeout")
        with self.assertRaises(TimeoutError):
            self.guard.require()
        self.assertIsNone(self.guard.failed_checks[0]["observed"])


if __name__ == "__main__":
    unittest.main()
