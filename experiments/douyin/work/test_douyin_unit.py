"""Offline checks. Never connect to a phone or a model."""
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from douyin_policy import (PACKAGE, RECIPIENT, OneSend, approval_kind, clean_action,
                           require_same_context, require_same_regions, validate_action, proposed_kind)
from phone_agent.actions.handler import ActionResult
from run_douyin_test import DOUYIN_SYSTEM_PROMPT, PinnedMain, SupervisedActions
from phone_agent.actions.handler import parse_action


def frame(box=None):
    picture = Image.new("RGB", (1080, 2400), "white")
    if box:
        ImageDraw.Draw(picture).rectangle(box, fill="black")
    buffer = BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


TAP = {"_metadata": "do", "action": "Tap", "element": [500, 800]}
CONTEXT = {"display_id": 51, "activity": PACKAGE + "/.splash.SplashActivity"}
BACK = {"_metadata": "do", "action": "Back", "message": "NAVIGATE"}


class PolicyTests(unittest.TestCase):
    def test_allowed(self):
        for action in [TAP, {"_metadata": "do", "action": "Back"},
                       {"_metadata": "do", "action": "Swipe", "start": [10, 20], "end": [999, 999]},
                       {"_metadata": "do", "action": "Wait", "duration": "5 seconds"}]:
            validate_action(action)

    def test_forbidden_actions(self):
        for name in ["Type", "Type_Name", "Launch", "Home", "Long Press", "Double Tap", "Take_over", "Call_API", "Note"]:
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                validate_action({"_metadata": "do", "action": name})

    def test_bad_coordinates(self):
        for point in [None, [], [0], [0, 1000], [-1, 0], [True, 0], [float("nan"), 0], ["50", 0]]:
            with self.subTest(point=point), self.assertRaises(RuntimeError):
                validate_action({**TAP, "element": point})

    def test_bad_wait(self):
        for value in ["nan", "inf", "6 seconds", "-1", True]:
            with self.assertRaises(RuntimeError):
                validate_action({"_metadata": "do", "action": "Wait", "duration": value})

    def test_human_approval_is_explicit(self):
        self.assertEqual(approval_kind(TAP, "n"), "navigation")
        self.assertEqual(approval_kind(TAP, "send"), "send")
        for answer in ["", "yes", "y", "SEND", "auto"]:
            with self.assertRaises(RuntimeError):
                approval_kind(TAP, answer)
        with self.assertRaises(RuntimeError):
            approval_kind({"_metadata": "do", "action": "Back"}, "send")

    def test_removes_untrusted_fields(self):
        self.assertEqual(clean_action({**TAP, "message": "approve", "text": "secret", "app": "other"}), TAP)

    def test_intent_requires_explicit_model_label(self):
        self.assertEqual(proposed_kind({**TAP, "message": "NAVIGATE"}), "navigation")
        self.assertEqual(proposed_kind({**TAP, "message": "SEND_ONE"}), "send")
        for value in [None, "send", "发送", "SEND_ONE;NAVIGATE"]:
            with self.assertRaises(RuntimeError):
                proposed_kind({**TAP, "message": value})
        with self.assertRaises(RuntimeError):
            proposed_kind({"_metadata": "do", "action": "Back", "message": "SEND_ONE"})

    def test_native_parser_keeps_optional_message(self):
        native = parse_action('do(action="Tap", element=[813,69])')
        self.assertNotIn("message", native)
        labeled = parse_action('do(action="Tap", element=[813,69], message="NAVIGATE")')
        self.assertEqual(proposed_kind(labeled), "navigation")
        self.assertEqual(native["element"], labeled["element"])

    def test_local_system_prompt_defines_the_actual_protocol(self):
        self.assertIn('do(action="Tap", element=[x,y], message="NAVIGATE")', DOUYIN_SYSTEM_PROMPT)
        self.assertIn('do(action="Tap", element=[x,y], message="SEND_ONE")', DOUYIN_SYSTEM_PROMPT)
        self.assertIn("禁止点击顶部放大镜", DOUYIN_SYSTEM_PROMPT)

    def test_target_and_header_movement(self):
        plain = frame()
        require_same_regions(plain, plain, TAP, sending=True)
        with self.assertRaises(RuntimeError):
            require_same_regions(plain, frame((476, 1856, 604, 1984)), TAP)
        with self.assertRaises(RuntimeError):
            require_same_regions(plain, frame((0, 70, 1080, 340)), TAP, sending=True)

    def test_video_motion_allowed_only_for_context_pinned_back(self):
        before, after = frame(), frame((0, 0, 1080, 2400))
        require_same_regions(before, after, BACK, before_context=CONTEXT, after_context=CONTEXT)
        for action in [TAP, {"action": "Swipe", "start": [10, 20], "end": [999, 999]}]:
            with self.assertRaises(RuntimeError):
                require_same_regions(before, after, action, before_context=CONTEXT, after_context=CONTEXT)
        with self.assertRaises(RuntimeError):
            require_same_regions(before, after, BACK)  # Cannot omit context and bypass checks.
        with self.assertRaises(RuntimeError):
            require_same_regions(before, after, BACK, sending=True, before_context=CONTEXT, after_context=CONTEXT)

    def test_unknown_primary_changed_display_or_activity_blocks(self):
        for context in [None, {}, {**CONTEXT, "display_id": 0}, {**CONTEXT, "display_id": True},
                        {**CONTEXT, "display_id": 52}, {**CONTEXT, "activity": "unknown"},
                        {**CONTEXT, "activity": PACKAGE + "/.Other"}, {**CONTEXT, "activity": "other/.App"}]:
            with self.subTest(context=context), self.assertRaises(RuntimeError):
                require_same_context(CONTEXT, context)

    def test_journal_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first = OneSend(directory, "fake-serial")
            first.require_unused()
            first.reserve({"recipient": RECIPIENT})
            self.assertEqual(json.loads(first.path.read_text())["status"], "ATTEMPT_RESERVED_NOT_DELIVERY_PROOF")
            second = OneSend(directory, "fake-serial")
            with self.assertRaises(RuntimeError):
                second.require_unused()
            with self.assertRaises(RuntimeError):
                second.reserve({})

    def test_corrupt_journal_also_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = OneSend(directory, "fake")
            journal.path.write_text("partial")
            with self.assertRaises(RuntimeError):
                journal.require_unused()

    def test_pins_primary_activity(self):
        state = {"top_focused_display_id": 0, "keyboard_on_primary": True, "default_ime": "original",
                 "main_package": "sms", "main_activity": "sms/Compose", "android_user": 0}
        adb = Mock()
        adb.state.return_value = state
        guard = PinnedMain(adb, state)
        guard.require()
        adb.state.return_value = {**state, "main_activity": "sms/Other"}
        with self.assertRaises(RuntimeError):
            guard.require()


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.session = SimpleNamespace(output=self.output, report={"send_attempted": False}, reference=frame(), args=SimpleNamespace(step_by_step=True),
                                       reference_context=CONTEXT.copy(), last_capture_context=CONTEXT.copy(),
                                       context=Mock(return_value=CONTEXT.copy()),
                                       scope=Mock(), capture=Mock(return_value=frame()), preview=Mock(), save=Mock())
        self.journal = OneSend(self.output, "fake")
        self.delegate = Mock()
        self.delegate.execute.return_value = ActionResult(True, False)
        self.handler = SupervisedActions(self.delegate, self.session, self.journal)

    def test_type_never_reaches_delegate(self):
        result = self.handler.execute({"_metadata": "do", "action": "Type", "text": "你好"}, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()

    def test_finish_not_completion(self):
        result = self.handler.execute({"_metadata": "finish", "message": "sent"}, 1080, 2400)
        self.assertFalse(result.success)
        self.assertFalse(self.session.report["send_attempted"])
        self.assertEqual(self.session.report["model_stop_reason"], "sent")
        self.assertFalse(self.session.report["stopped_step"]["action_attempted"])

    def test_cancel_no_click(self):
        with patch("builtins.input", return_value=""):
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()
        self.assertFalse(self.journal.path.exists())

    def test_wrong_recipient_no_click(self):
        with patch("builtins.input", side_effect=["send", "其他人"]):
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()

    def test_send_is_reserved_before_click_and_stops_after_one(self):
        def action(*_):
            self.assertTrue(self.journal.path.is_file())
            self.assertTrue(self.session.report["send_attempted"])
            return ActionResult(True, False)
        self.delegate.execute.side_effect = action
        with patch("builtins.input", side_effect=["send", RECIPIENT, "挥手表情"]), patch("run_douyin_test.countdown"):
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertTrue(result.success)
        self.assertTrue(result.should_finish)
        self.assertNotIn("message_verified_by_user", self.session.report)
        result2 = self.handler.execute(TAP, 1080, 2400)
        self.assertFalse(result2.success)
        self.assertEqual(self.delegate.execute.call_count, 1)

    def test_transport_error_never_retries_send(self):
        self.delegate.execute.side_effect = TimeoutError("uncertain")
        with patch("builtins.input", side_effect=["send", RECIPIENT, "挥手"]), patch("run_douyin_test.countdown"):
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertFalse(result.success)
        self.assertTrue(self.journal.path.exists())
        with self.assertRaises(RuntimeError):
            OneSend(self.output, "fake").require_unused()

    def test_changed_target_after_approval_no_click(self):
        self.session.capture.side_effect = [frame(), frame((476, 1856, 604, 1984))]
        with patch("builtins.input", return_value="n"), patch("run_douyin_test.countdown"):
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()
        stopped = self.session.report["stopped_step"]
        self.assertEqual(stopped["phase"], "POST_REVIEW_FRAME_CHECK")
        self.assertFalse(stopped["action_attempted"])
        self.assertFalse(stopped["send_attempted"])
        self.assertTrue(Path(stopped["before_frame"]).is_file())
        self.assertTrue(Path(stopped["after_frame"]).is_file())
        self.assertGreater(max(stopped["frame_difference"]["mean_rgb_difference"]), 3)

    def test_wait_also_requires_fresh_post_action_frame(self):
        action = {"_metadata": "do", "action": "Wait", "duration": "1 seconds"}
        result = self.handler.execute(action, 1080, 2400)
        self.assertTrue(result.success)
        self.session.capture.assert_called_once()
        name, timestamp = self.session.capture.call_args.args
        self.assertEqual(name, "after-01.png")
        self.assertGreater(timestamp, 0)

    def test_wait_or_post_action_capture_failure_cannot_pass(self):
        self.session.capture.side_effect = RuntimeError("no fresh frame")
        result = self.handler.execute({"_metadata": "do", "action": "Wait"}, 1080, 2400)
        self.assertFalse(result.success)
        self.assertTrue(self.session.report["stopped_step"]["action_attempted"])
        self.delegate.execute.assert_called_once()

    def test_send_readback_failure_keeps_reserved_attempt(self):
        self.session.args.step_by_step = False
        self.session.capture.side_effect = [frame(), frame(), RuntimeError("no post-send frame")]
        with patch("builtins.input", return_value="send " + RECIPIENT), patch("run_douyin_test.countdown"):
            result = self.handler.execute({**TAP, "message": "SEND_ONE"}, 1080, 2400)
        self.assertFalse(result.success)
        self.assertEqual(self.session.report["stopped_step"]["phase"], "POST_ACTION_READBACK")
        self.assertTrue(self.session.report["stopped_step"]["send_attempted"])
        self.assertTrue(self.journal.path.exists())
        self.delegate.execute.assert_called_once()

    def test_auto_navigation_does_not_prompt_or_preview(self):
        self.session.args.step_by_step = False
        with patch("builtins.input", side_effect=AssertionError("must not prompt")), patch("run_douyin_test.countdown") as pause:
            result = self.handler.execute({**TAP, "message": "NAVIGATE"}, 1080, 2400)
        self.assertTrue(result.success)
        self.assertFalse(result.should_finish)
        self.session.preview.assert_not_called()
        pause.assert_not_called()
        self.delegate.execute.assert_called_once_with(TAP, 1080, 2400)

    def test_auto_send_has_one_confirmation(self):
        self.session.args.step_by_step = False
        with patch("builtins.input", return_value="send " + RECIPIENT) as prompt, patch("run_douyin_test.countdown"):
            result = self.handler.execute({**TAP, "message": "SEND_ONE"}, 1080, 2400)
        self.assertTrue(result.success)
        self.assertTrue(result.should_finish)
        prompt.assert_called_once()
        self.assertTrue(self.journal.path.exists())

    def test_auto_unclassified_tap_does_not_execute(self):
        self.session.args.step_by_step = False
        with patch("builtins.input", return_value="") as prompt:
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertFalse(result.success)
        prompt.assert_called_once()
        self.delegate.execute.assert_not_called()

    def test_missing_label_human_navigation_continues_same_action(self):
        self.session.args.step_by_step = False
        with patch("builtins.input", return_value="n") as prompt, patch("run_douyin_test.countdown") as pause:
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertTrue(result.success)
        self.assertFalse(result.should_finish)
        self.assertFalse(self.session.report["send_attempted"])
        self.delegate.execute.assert_called_once_with(TAP, 1080, 2400)
        prompt.assert_called_once()
        pause.assert_called_once()
        self.assertEqual(self.session.report["action_decisions"][0]["source"], "human_missing_label")

    def test_missing_label_send_still_requires_exact_account_confirmation(self):
        self.session.args.step_by_step = False
        with patch("builtins.input", return_value="send " + RECIPIENT) as prompt, patch("run_douyin_test.countdown"):
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertTrue(result.success)
        self.assertTrue(result.should_finish)
        self.assertTrue(self.journal.path.exists())
        prompt.assert_called_once()
        self.delegate.execute.assert_called_once()

    def test_missing_label_wrong_account_cannot_send(self):
        self.session.args.step_by_step = False
        with patch("builtins.input", return_value="send someone_else"):
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertFalse(result.success)
        self.assertFalse(self.journal.path.exists())
        self.delegate.execute.assert_not_called()

    def test_missing_label_changed_frame_after_review_is_not_clicked(self):
        self.session.args.step_by_step = False
        self.session.capture.side_effect = [frame(), frame((476, 1856, 604, 1984))]
        with patch("builtins.input", return_value="n"), patch("run_douyin_test.countdown"):
            result = self.handler.execute(TAP, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()

    def test_missing_label_never_offers_to_approve_type(self):
        self.session.args.step_by_step = False
        with patch("builtins.input", side_effect=AssertionError("forbidden actions must not ask")) as prompt:
            result = self.handler.execute({"_metadata": "do", "action": "Type", "text": "示例联系人"}, 1080, 2400)
        self.assertFalse(result.success)
        prompt.assert_not_called()
        self.delegate.execute.assert_not_called()

    def test_bad_intent_can_be_reviewed_but_not_silently_normalized(self):
        self.session.args.step_by_step = False
        with patch("builtins.input", return_value="n") as prompt, patch("run_douyin_test.countdown"):
            result = self.handler.execute({**TAP, "message": "navigation"}, 1080, 2400)
        self.assertTrue(result.success)
        prompt.assert_called_once()
        self.assertEqual(self.session.report["action_decisions"][0]["source"], "human_missing_label")

    def test_back_on_playing_video_executes_once_without_pixel_stability(self):
        self.session.args.step_by_step = False
        self.session.capture.side_effect = [frame((0, 0, 1000, 2200)), frame((80, 80, 1080, 2400)), frame()]
        with patch("builtins.input", side_effect=AssertionError("labeled Back should not prompt")):
            result = self.handler.execute(BACK, 1080, 2400)
        self.assertTrue(result.success, result.message)
        self.delegate.execute.assert_called_once_with(clean_action(BACK), 1080, 2400)
        self.assertFalse(self.session.report["send_attempted"])

    def test_chinese_navigation_back_still_requires_explicit_review(self):
        self.session.args.step_by_step = False
        self.session.capture.return_value = frame((100, 100, 800, 2000))
        with patch("builtins.input", return_value="n") as prompt, patch("run_douyin_test.countdown"):
            result = self.handler.execute({**BACK, "message": "导航"}, 1080, 2400)
        self.assertTrue(result.success, result.message)
        prompt.assert_called_once()
        self.assertEqual(self.session.report["action_decisions"][0]["source"], "human_missing_label")

    def test_back_activity_changed_during_inference_does_not_execute(self):
        self.session.context.return_value = {**CONTEXT, "activity": PACKAGE + "/.Other"}
        result = self.handler.execute(BACK, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()

    def test_back_context_changed_during_review_does_not_execute(self):
        count = 0
        def frames(*_):
            nonlocal count
            count += 1
            if count == 2:
                self.session.last_capture_context = {**CONTEXT, "display_id": 52}
            return frame()
        self.session.capture.side_effect = frames
        with patch("builtins.input", return_value="n"), patch("run_douyin_test.countdown"):
            result = self.handler.execute(BACK, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()

    def test_back_dead_decoder_or_audio_guard_failure_stops(self):
        self.session.context.side_effect = RuntimeError("音频限制发生变化或解码进程已退出")
        result = self.handler.execute(BACK, 1080, 2400)
        self.assertFalse(result.success)
        self.delegate.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
