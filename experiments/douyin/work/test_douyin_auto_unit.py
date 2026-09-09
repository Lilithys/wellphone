"""No-popup navigation and passive transition checks; fake phone/model only."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from douyin_display import require_comparison_scope
from douyin_policy import ContextChanged
from douyin_startup import StartupActions, run_startup
from douyin_transition import wait_for_navigation_transition
from phone_agent.actions.handler import ActionResult
from test_douyin_navigation_frame_unit import TAP, navigation_frame
from test_douyin_unit import CONTEXT, frame


class AutoMessagesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = navigation_frame()
        self.s = SimpleNamespace(output=Path(temp.name), display_id=51,
            args=SimpleNamespace(auto_messages=True, startup_only=True, confirm_home_first=True,
                                 non_presentation=True, preflight=False, step_by_step=False),
            report={"same_session_handoff": True, "non_presentation_verified": True,
                    "home_gate": {"status": "READY_FOR_MODEL", "context": CONTEXT, "display_id": 51}},
            reference=self.home, reference_context=CONTEXT, confirmed_home_frame=self.home,
            last_capture_context=CONTEXT, context=Mock(return_value=CONTEXT),
            capture=Mock(return_value=self.home), save=Mock(), scope=Mock(), preview=Mock())
        self.delegate = Mock(execute=Mock(return_value=ActionResult(True, False)))
        self.actions = StartupActions(self.delegate, self.s)
        self.actions.home_ready = True

    def test_explicit_and_unlabeled_one_tap_need_no_preview_prompt_or_countdown(self):
        for action in (TAP, {**TAP, "message": "OPEN_MESSAGES"}):
            self.setUp()
            with patch("builtins.input") as prompt, patch("run_douyin_test.countdown") as countdown:
                result = self.actions.execute(action, 1080, 2400)
            self.assertTrue(result.success)
            self.s.preview.assert_not_called()
            prompt.assert_not_called()
            countdown.assert_not_called()
            self.delegate.execute.assert_called_once_with(TAP, 1080, 2400)
            self.assertFalse(self.s.report["startup_auto_navigation"]["per_action_human_review"])
            self.assertEqual(self.s.report["startup_actions"][0]["approval"], "preauthorized_bounded_navigation")
            self.assertFalse(self.s.report.get("startup_label_reviews"))

    def test_second_tap_is_rejected_including_missing_label(self):
        self.assertTrue(self.actions.execute(TAP, 1080, 2400).success)
        for action in (TAP, {**TAP, "message": "OPEN_MESSAGES"}):
            result = self.actions.execute(action, 1080, 2400)
            self.assertFalse(result.success)
            self.assertIn("禁止再次点击", result.message)
        self.delegate.execute.assert_called_once()

    def test_coordinate_outside_qualified_interior_never_prompts_or_changes_it(self):
        with patch("builtins.input") as prompt:
            for point in ([650, 965], [750, 965], [696, 990], [500, 965]):
                self.assertFalse(self.actions.execute({**TAP, "element": point}, 1080, 2400).success)
        prompt.assert_not_called()
        self.s.preview.assert_not_called()
        self.delegate.execute.assert_not_called()

    def test_home_marker_or_verified_display_missing_blocks_auto(self):
        for missing in ("home_gate", "non_presentation_verified"):
            value = self.s.report.pop(missing)
            self.assertFalse(self.actions.execute(TAP, 1080, 2400).success)
            self.s.report[missing] = value
        self.s.confirmed_home_frame = None
        self.assertFalse(self.actions.execute(TAP, 1080, 2400).success)
        self.delegate.execute.assert_not_called()

    def test_changed_confirmed_home_does_not_become_an_auto_target(self):
        self.s.confirmed_home_frame = frame()
        self.assertFalse(self.actions.execute(TAP, 1080, 2400).success)
        self.delegate.execute.assert_not_called()

    def test_primary_failure_after_first_check_still_prevents_dispatch(self):
        self.s.capture.side_effect = [self.home, RuntimeError("主屏异常")]
        self.assertFalse(self.actions.execute(TAP, 1080, 2400).success)
        self.delegate.execute.assert_not_called()

    def test_final_result_observation_remains_but_does_not_open_preview(self):
        with patch("builtins.input", return_value="messages") as prompt:
            result = self.actions.execute({"_metadata": "finish", "message": "MESSAGES_READY"}, 1080, 2400)
        self.assertTrue(result.success)
        prompt.assert_called_once()
        self.s.preview.assert_not_called()
        self.delegate.execute.assert_not_called()

    def test_auto_flag_cannot_enable_send_or_unqualified_modes(self):
        for field, value in (("non_presentation", False), ("startup_only", False),
                             ("confirm_home_first", False), ("preflight", True), ("step_by_step", True)):
            args = SimpleNamespace(**vars(self.s.args))
            setattr(args, field, value)
            with self.assertRaises(RuntimeError):
                require_comparison_scope(args)
        with patch("builtins.input") as prompt:
            for action in ({**TAP, "message": "SEND_ONE"}, {"_metadata": "do", "action": "Type", "text": "1"}):
                self.assertFalse(self.actions.execute(action, 1080, 2400).success)
        prompt.assert_not_called()
        self.delegate.execute.assert_not_called()

    def test_transition_waits_for_two_changed_observations_without_model_or_action(self):
        self.s.capture.side_effect = [self.home, self.home, frame(), frame()]
        with patch("douyin_transition.time.sleep"):
            result = wait_for_navigation_transition(self.s, self.home, CONTEXT, TAP)
        self.assertEqual(result, frame())
        report = self.s.report["startup_transition"]
        self.assertEqual(report["status"], "VISUAL_TRANSITION_NOT_PAGE_VERIFIED")
        self.assertEqual(len(report["samples"]), 4)
        self.assertFalse(report["semantic_proof"])
        self.delegate.execute.assert_not_called()
        self.s.preview.assert_not_called()

    def test_transient_change_is_not_sufficient(self):
        self.s.capture.side_effect = [frame(), self.home, frame()]
        with patch("douyin_transition.time.sleep"), self.assertRaises(RuntimeError):
            wait_for_navigation_transition(self.s, self.home, CONTEXT, TAP, max_samples=3)
        self.assertEqual(self.s.report["startup_transition"]["status"], "TIMEOUT_NO_VERIFIED_TRANSITION")

    def test_old_home_updates_never_count_as_completed_navigation(self):
        self.s.capture.side_effect = [navigation_frame((-i, -i), changed_content=True) for i in range(3)]
        with patch("douyin_transition.time.sleep"), self.assertRaises(RuntimeError):
            wait_for_navigation_transition(self.s, self.home, CONTEXT, TAP, max_samples=3)
        self.assertTrue(all(row["state"] == "OLD_NAVIGATION_STILL_MATCHES" for row in self.s.report["startup_transition"]["samples"]))
        self.delegate.execute.assert_not_called()

    def test_capture_and_context_failures_stop_observation(self):
        for error in (RuntimeError("primary/audio guard"), ContextChanged(CONTEXT, {**CONTEXT, "display_id": 52})):
            self.s.capture.side_effect = error
            with self.assertRaises(RuntimeError):
                wait_for_navigation_transition(self.s, self.home, CONTEXT, TAP)
            self.assertEqual(self.s.report["startup_transition"]["status"], "GUARD_OR_CAPTURE_FAILED")

    def test_deadline_exceeded_during_capture_cannot_return_success(self):
        self.s.capture.return_value = frame()
        with patch("douyin_transition.time.monotonic", side_effect=[0, 0, 21, 21, 21]), self.assertRaises(RuntimeError):
            wait_for_navigation_transition(self.s, self.home, CONTEXT, TAP)
        self.assertEqual(self.s.report["startup_transition"]["status"], "TIMEOUT_NO_VERIFIED_TRANSITION")

    def test_second_model_request_uses_exact_transition_frame(self):
        frozen = self.s.output / "model-frame.png"
        messages = frame()
        model = Mock(action_handler=self.delegate)
        def transition(*_):
            self.s.report["startup_transition"] = {"status": "VISUAL_TRANSITION_NOT_PAGE_VERIFIED"}
            self.s.capture.return_value = messages
            return messages
        def step(_):
            if model.step.call_count == 1:
                action = TAP
            else:
                self.assertEqual(frozen.read_bytes(), messages)
                path = Path(self.s.report["model_requests"][-1]["frame"])
                self.assertEqual(path.read_bytes(), messages)
                action = {"_metadata": "finish", "message": "MESSAGES_READY"}
            result = model.action_handler.execute(action, 1080, 2400)
            return SimpleNamespace(action=action, success=result.success, finished=result.should_finish, message=result.message)
        model.step.side_effect = step
        with patch("douyin_transition.wait_for_navigation_transition", side_effect=transition), patch("builtins.input", return_value="messages"):
            run_startup(model, self.s, frozen, 8)
        self.assertEqual(model.step.call_count, 2)
        self.delegate.execute.assert_called_once()
        self.s.preview.assert_not_called()

    def test_transition_failure_blocks_second_model_request_and_any_replay(self):
        model = Mock(action_handler=self.delegate)
        def step(_):
            result = model.action_handler.execute(TAP, 1080, 2400)
            return SimpleNamespace(action=TAP, success=result.success, finished=result.should_finish, message=result.message)
        model.step.side_effect = step
        with patch("douyin_transition.wait_for_navigation_transition", side_effect=RuntimeError("observation timeout")), \
             patch("builtins.input") as prompt, self.assertRaisesRegex(RuntimeError, "observation timeout"):
            run_startup(model, self.s, self.s.output / "model-frame.png", 8)
        model.step.assert_called_once()
        self.delegate.execute.assert_called_once()
        prompt.assert_not_called()

    def test_freeform_finish_after_owned_transition_only_requests_human_review(self):
        self.actions.messages_click_attempted = True
        self.s.report["startup_transition"] = {"status": "VISUAL_TRANSITION_NOT_PAGE_VERIFIED"}
        with patch("builtins.input", return_value="messages") as prompt:
            result = self.actions.execute({"_metadata": "finish", "message": "确认完成！当前页面是消息列表。"}, 1080, 2400)
        self.assertTrue(result.success)
        prompt.assert_called_once()
        self.assertTrue(self.s.report["messages_ready_by_user"])
        self.assertFalse(self.s.report["startup_finish_reviews"][0]["automatic_success"])
        self.delegate.execute.assert_not_called()

    def test_freeform_finish_rejected_by_user_is_not_success(self):
        self.actions.messages_click_attempted = True
        self.s.report["startup_transition"] = {"status": "VISUAL_TRANSITION_NOT_PAGE_VERIFIED"}
        with patch("builtins.input", return_value="no"):
            self.assertFalse(self.actions.execute({"_metadata": "finish", "message": "已完成"}, 1080, 2400).success)
        self.assertFalse(self.s.report.get("messages_ready_by_user"))
        self.assertEqual(self.s.report["startup_finish_reviews"][0]["status"], "HUMAN_REJECTED")

    def test_freeform_finish_without_owned_transition_cannot_be_promoted(self):
        with patch("builtins.input") as prompt:
            for attempted in (False, True):
                self.actions.messages_click_attempted = attempted
                self.assertFalse(self.actions.execute({"_metadata": "finish", "message": "消息列表已就绪"}, 1080, 2400).success)
        prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
