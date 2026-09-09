"""Startup safety/transition tests: synthetic frames, no phone/model/network."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from douyin_policy import ContextChanged, require_same_context
from douyin_startup import (StartupActions, record_reobserve, run_startup,
                            startup_capture, validate_startup_action)
from phone_agent.actions.handler import ActionResult
from run_douyin_test import DouyinSession
from test_douyin_unit import CONTEXT, frame

OTHER = {**CONTEXT, "activity": "com.ss.android.ugc.aweme/.MainActivity"}
WAIT = {"_metadata": "do", "action": "Wait", "duration": "2 seconds"}
BACK = {"_metadata": "do", "action": "Back", "message": "EXIT_FULLSCREEN"}
SKIP = {"_metadata": "do", "action": "Tap", "element": [930, 50], "message": "SKIP_AD"}
MESSAGES = {**SKIP, "element": [695, 963], "message": "OPEN_MESSAGES"}
HOME_READY = {"_metadata": "finish", "message": "HOME_READY"}
MESSAGES_READY = {"_metadata": "finish", "message": "MESSAGES_READY"}


class StartupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.frame = frame()
        self.s = SimpleNamespace(display_id=51, report={"send_attempted": False}, output=self.root,
            reference=self.frame, reference_context=dict(CONTEXT), last_capture_context=dict(CONTEXT),
            context=Mock(return_value=dict(CONTEXT)), capture=Mock(return_value=self.frame),
            scope=Mock(), preview=Mock(), save=Mock())
        self.delegate = Mock()
        self.delegate.execute.return_value = ActionResult(True, False)
        self.actions = StartupActions(self.delegate, self.s)
        self.delay = patch("run_douyin_test.countdown", Mock())
        self.delay.start()
        self.addCleanup(self.delay.stop)

    def test_strict_protocol_no_type_send_swipe_or_generic_navigation(self):
        cases = [{"_metadata": "do", "action": "Type", "text": "1", "message": "INPUT_ONE"},
                 {**SKIP, "message": "SEND_ONE"}, {**SKIP, "message": "NAVIGATE"},
                 {**SKIP, "text": "hidden"}, {**SKIP, "app": "Douyin"},
                 {"_metadata": "do", "action": "Swipe", "start": [1, 2], "end": [3, 4]},
                 {**WAIT, "message": "SEND_ONE"}, {**HOME_READY, "action": "Tap"}]
        for action in cases:
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                validate_startup_action(action, True)

    def test_messages_click_requires_human_home_state(self):
        with self.assertRaises(RuntimeError):
            validate_startup_action(MESSAGES)
        self.assertEqual(validate_startup_action(MESSAGES, True), "OPEN_MESSAGES")

    def test_wait_is_bounded_and_dispatches_only_wait(self):
        with patch("builtins.input") as question:
            result = self.actions.execute(WAIT, 1080, 2400)
        self.assertTrue(result.success)
        question.assert_not_called()
        self.delegate.execute.assert_called_once_with(WAIT, 1080, 2400)
        self.assertGreater(self.s.capture.call_args.args[1], 0)
        with self.assertRaises(RuntimeError):
            validate_startup_action({**WAIT, "duration": "8 seconds"})

    def test_skip_requires_specific_human_confirmation(self):
        for answer in ("", "yes", "n", "send 336789"):
            with patch("builtins.input", return_value=answer):
                result = self.actions.execute(SKIP, 1080, 2400)
            self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()

    def test_skip_executes_once_and_strips_label(self):
        with patch("builtins.input", return_value="skip"):
            result = self.actions.execute(SKIP, 1080, 2400)
        self.assertTrue(result.success)
        self.delegate.execute.assert_called_once_with({"_metadata": "do", "action": "Tap", "element": [930, 50]}, 1080, 2400)
        self.assertEqual(self.s.report["startup_actions"][0]["approval"], "human")

    def test_no_action_on_changed_planning_context(self):
        self.s.context.return_value = OTHER
        result = self.actions.execute(BACK, 1080, 2400)
        self.assertFalse(result.success)
        self.assertTrue(self.actions.needs_reobserve)
        self.delegate.execute.assert_not_called()
        self.assertEqual(self.s.report["startup_reobservations"][0]["before"], CONTEXT)
        self.assertEqual(self.s.report["startup_reobservations"][0]["after"], OTHER)
        self.assertNotIn("stopped_step", self.s.report)

    def test_changed_display_is_never_reobserved(self):
        self.s.context.return_value = {**OTHER, "display_id": 52}
        result = self.actions.execute(BACK, 1080, 2400)
        self.assertFalse(result.success)
        self.assertFalse(self.actions.needs_reobserve)
        self.delegate.execute.assert_not_called()

    def test_invalid_or_other_app_context_is_terminal(self):
        for context in (None, {**OTHER, "activity": "permissions/.Grant"}, {**OTHER, "display_id": 0}):
            self.s.context.return_value = context
            result = self.actions.execute(BACK, 1080, 2400)
            self.assertFalse(result.success)
            self.assertFalse(self.actions.needs_reobserve)
        self.delegate.execute.assert_not_called()

    def test_primary_audio_or_frame_failure_never_retries(self):
        for reason in ("主屏异常", "音频限制改变", "decoder died"):
            self.s.context.side_effect = RuntimeError(reason)
            result = self.actions.execute(WAIT, 1080, 2400)
            self.assertFalse(result.success)
            self.assertFalse(self.actions.needs_reobserve)
        self.delegate.execute.assert_not_called()

    def test_retry_budget_is_three_shared_with_capture(self):
        self.s.capture.side_effect = ContextChanged(CONTEXT, OTHER)
        with self.assertRaisesRegex(RuntimeError, "超过3次"):
            startup_capture(self.s)
        self.assertEqual(self.s.capture.call_count, 4)
        self.assertEqual(len(self.s.report["startup_reobservations"]), 3)

    def test_reobserve_reguards_primary_and_audio(self):
        self.s.scope.side_effect = RuntimeError("主屏异常")
        with self.assertRaisesRegex(RuntimeError, "主屏异常"):
            record_reobserve(self.s, ContextChanged(CONTEXT, OTHER), "TEST")
        self.assertEqual(self.s.report["startup_reobservations"], [])

    def test_no_reobserve_after_draft_or_send(self):
        for field in ("send_attempted", "fixed_input_attempted"):
            self.s.report = {field: True}
            with self.assertRaises(RuntimeError):
                record_reobserve(self.s, ContextChanged(CONTEXT, OTHER), "TEST")

    def test_changed_context_while_user_reviews_discards_approval(self):
        def capture(*_):
            if self.s.capture.call_count == 2:
                self.s.last_capture_context = OTHER
            return self.frame
        self.s.capture.side_effect = capture
        with patch("builtins.input", return_value="skip"):
            result = self.actions.execute(SKIP, 1080, 2400)
        self.assertFalse(result.success)
        self.assertTrue(self.actions.needs_reobserve)
        self.delegate.execute.assert_not_called()

    def test_target_motion_stops_not_replanned(self):
        self.s.capture.return_value = frame((900, 0, 1080, 250))
        result = self.actions.execute(SKIP, 1080, 2400)
        self.assertFalse(result.success)
        self.assertFalse(self.actions.needs_reobserve)
        self.delegate.execute.assert_not_called()
        self.assertIn("frame_difference", self.s.report["stopped_step"])

    def test_navigation_readback_transition_never_replays_click(self):
        self.s.capture.side_effect = [self.frame, self.frame, ContextChanged(CONTEXT, OTHER), self.frame]
        with patch("builtins.input", return_value="skip"):
            result = self.actions.execute(SKIP, 1080, 2400)
        self.assertTrue(result.success)
        self.delegate.execute.assert_called_once()
        self.assertTrue(self.s.report["startup_reobservations"][0]["action_attempted"])
        self.assertFalse(self.s.report["startup_reobservations"][0]["action_replayed"])

    def test_unknown_action_outcome_is_terminal_no_replay(self):
        self.delegate.execute.return_value = ActionResult(False, False, "timeout")
        with patch("builtins.input", return_value="skip"):
            result = self.actions.execute(SKIP, 1080, 2400)
        self.assertFalse(result.success)
        self.assertFalse(self.actions.needs_reobserve)
        self.assertTrue(self.s.report["stopped_step"]["action_attempted"])
        self.delegate.execute.assert_called_once()

    def test_home_readiness_is_human_not_full_frame_stillness(self):
        self.s.capture.side_effect = [self.frame, frame((0, 340, 1080, 2200))]
        with patch("builtins.input", return_value="home"):
            result = self.actions.execute(HOME_READY, 1080, 2400)
        self.assertTrue(result.success)
        self.assertTrue(self.actions.home_ready)
        self.assertTrue(self.s.report["home_ready_by_user"])
        self.assertNotIn("messages_ready_by_user", self.s.report)
        self.delegate.execute.assert_not_called()

    def test_model_claim_alone_does_not_verify_messages(self):
        with patch("builtins.input", return_value="no"):
            result = self.actions.execute(MESSAGES_READY, 1080, 2400)
        self.assertFalse(result.success)
        self.assertNotIn("messages_ready_by_user", self.s.report)

    def test_already_messages_does_not_need_home_detour(self):
        with patch("builtins.input", return_value="messages"):
            result = self.actions.execute(MESSAGES_READY, 1080, 2400)
        self.assertTrue(result.success)
        self.assertTrue(self.s.report["messages_ready_by_user"])
        self.delegate.execute.assert_not_called()

    def test_generic_finish_is_obstruction_not_success(self):
        result = self.actions.execute({"_metadata": "finish", "message": "登录提示"}, 1080, 2400)
        self.assertFalse(result.success)
        self.assertEqual(self.s.report["model_stop_reason"], "登录提示")

    def test_loop_resets_model_and_caps_requests(self):
        model = Mock(action_handler=self.delegate)
        model.step.return_value = SimpleNamespace(action=WAIT, success=True, finished=False)
        with self.assertRaisesRegex(RuntimeError, "请求上限"):
            run_startup(model, self.s, self.root / "frozen.png", 20)
        self.assertEqual(model.step.call_count, 8)
        self.assertEqual(model.reset.call_count, 8)

    def test_reobserve_flag_cannot_retry_subsequent_model_failure(self):
        model = Mock(action_handler=self.delegate)
        self.s.context.return_value = OTHER
        def step(_):
            if model.step.call_count == 1:
                result = model.action_handler.execute(BACK, 1080, 2400)
                return SimpleNamespace(action=BACK, success=result.success, finished=True, message=result.message)
            return SimpleNamespace(action=None, success=False, finished=True, message="Model error: timeout")
        model.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, "Model error: timeout"):
            run_startup(model, self.s, self.root / "frozen.png", 8)
        self.assertEqual(model.step.call_count, 2)
        self.delegate.execute.assert_not_called()

    def test_startup_session_report_disables_numeric_executor(self):
        args = SimpleNamespace(preflight=False, step_by_step=False, message="1", startup_only=True)
        def parent_init(session, arguments):
            session.args, session.report = arguments, {}
        with patch("run_douyin_test.KeyboardTest.__init__", parent_init):
            session = DouyinSession(args)
        self.assertEqual(session.report["messages_allowed"], 0)
        self.assertFalse(session.report["text_input_enabled"])
        self.assertFalse(session.report["automatic_navigation"])
        self.assertIsNone(session.report["recipient"])

    def test_launch_capture_transition_keeps_startup_mode(self):
        self.s.capture.side_effect = [ContextChanged(CONTEXT, OTHER), self.frame]
        data = startup_capture(self.s, "launch.png", 123, phase="LAUNCH_OBSERVE")
        self.assertEqual(data, self.frame)
        self.assertEqual(self.s.report["startup_reobservations"][0]["phase"], "LAUNCH_OBSERVE")
        self.assertFalse(self.s.report["startup_reobservations"][0]["action_attempted"])

    def test_context_change_keeps_both_valid_activity_names(self):
        with self.assertRaises(ContextChanged) as error:
            require_same_context(CONTEXT, OTHER)
        self.assertEqual(error.exception.evidence, {"before": CONTEXT, "after": OTHER})

    def test_capture_persists_changed_context_and_secondary_frame(self):
        session = DouyinSession.__new__(DouyinSession)
        session.output, session.report, session.save = self.root, {}, Mock()
        session.context = Mock(side_effect=[CONTEXT, OTHER])
        session.frames = SimpleNamespace(frame_path=self.root / "source.png")
        valid = frame((20, 20, 600, 1600))
        with patch("run_douyin_test.read_native", return_value=(valid, 1, "hash")):
            with self.assertRaises(ContextChanged):
                session.capture()
        event = session.report["capture_context_changes"][0]
        self.assertEqual(event["before"], CONTEXT)
        self.assertEqual(event["after"], OTHER)
        self.assertEqual(Path(event["frame"]).read_bytes(), valid)


if __name__ == "__main__":
    unittest.main()
