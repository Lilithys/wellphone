"""Synthetic handoff evidence and bounded progress; never touches a real phone."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from douyin_handoff import confirm_home_before_model, read_home_observation, secondary_diagnostic, window_metadata
from douyin_input import target_on_display
from douyin_startup import HANDOFF_SYSTEM_PROMPT, StartupActions, handoff_stage, run_startup, startup_task, validate_startup_action
from phone_agent.actions.handler import ActionResult
from test_douyin_input_unit import ACTIVITIES, WINDOWS
from test_douyin_startup_unit import BACK, SKIP, WAIT, MESSAGES, HOME_READY, OTHER
from test_douyin_unit import CONTEXT, frame
from test_douyin_navigation_frame_unit import navigation_frame
from douyin_navigation_frame import MAX_SHIFT

WINDOW_DETAILS = """Window #0 Window{aa u0 com.hihonor.mms/.Compose}:
 mDisplayId=0 mHasSurface=false mViewVisibility=0x8
 title=PRIMARY-PRIVATE-TEXT
Window #1 Window{face u0 com.ss.android.ugc.aweme/.splash.SplashActivity}:
 mDisplayId=51 mHasSurface=true mViewVisibility=0x0
 isReadyForDisplay()=true mSystemUiVisibility=0x1706
 title=SECONDARY-PRIVATE-TEXT
Window #2 Window{123 u0 StatusBar}:
 mDisplayId=0 mAppStopped=true
"""
UNLABELED_MESSAGES = {"_metadata": "do", "action": "Tap", "element": [696, 967]}


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        outputs = {("dumpsys", "activity", "activities"): ACTIVITIES,
                   ("dumpsys", "window", "displays"): WINDOWS,
                   ("dumpsys", "window", "windows"): WINDOW_DETAILS}
        self.s = SimpleNamespace(display_id=51, output=self.root, report={"model_called": False},
            context=Mock(return_value=CONTEXT), capture=Mock(return_value=frame()),
            last_capture_context=CONTEXT, save=Mock(), scope=Mock(), preview=Mock(),
            reference=frame(), reference_context=CONTEXT,
            shell=Mock(side_effect=lambda *args: outputs[args]))
        self.delegate = Mock()
        self.delegate.execute.return_value = ActionResult(True, False)
        self.target = target_on_display(ACTIVITIES, WINDOWS, 51)
        self.delay = patch("run_douyin_test.countdown", Mock())
        self.delay.start()
        self.addCleanup(self.delay.stop)

    def test_window_metadata_is_exact_scoped_and_whitelisted(self):
        result = window_metadata(WINDOW_DETAILS, self.target)
        self.assertEqual(result["display_id"], 51)
        self.assertTrue(result["mHasSurface"])
        self.assertTrue(result["isReadyForDisplay()"])
        self.assertEqual(result["mViewVisibility"], "0x0")
        self.assertNotIn("mAppStopped", result)  # Next non-app window must still delimit the block.
        self.assertNotIn("PRIVATE-TEXT", json.dumps(result))

    def test_unknown_duplicate_or_wrong_display_window_not_substituted(self):
        for dump in ("unknown", WINDOW_DETAILS + WINDOW_DETAILS,
                     WINDOW_DETAILS.replace("mDisplayId=51", "mDisplayId=0"),
                     WINDOW_DETAILS.replace("Window{face u0", "Window{face u10")):
            self.assertNotEqual(window_metadata(dump, self.target)["status"], "SCOPED_WINDOW_FIELDS")

    def test_diagnostic_keeps_only_structural_target_and_no_raw_dump(self):
        event = secondary_diagnostic(self.s, "unit")
        self.assertEqual(event["target"]["window_token"], "face")
        self.assertTrue(event["window"]["mHasSurface"])
        text = json.dumps(self.s.report)
        self.assertNotIn("PRIVATE-TEXT", text)
        self.assertNotIn("com.hihonor.mms", text)
        self.assertTrue(all(call.args[0] == "dumpsys" for call in self.s.shell.call_args_list))

    def test_unrecognized_target_diagnostic_not_other_window_fallback(self):
        self.s.shell.return_value = "unknown"
        self.s.shell.side_effect = None
        event = secondary_diagnostic(self.s, "unknown")
        self.assertEqual(event["window"]["status"], "TARGET_NOT_UNIQUELY_MATCHED")
        self.assertEqual(event["window"]["match_failure"], "DISPLAY_SECTION_NOT_UNIQUE")
        self.assertEqual(self.s.shell.call_count, 2)

    def test_target_mismatch_reasons_are_fixed_and_never_echo_unknown_details(self):
        for message, category in [
            ("副屏未唯一匹配当前用户的抖音 Activity，不检查其他窗口。", "ACTIVITY_NOT_UNIQUE"),
            ("副屏焦点窗口未匹配该抖音 Activity。", "FOCUSED_WINDOW_MISMATCH"),
            ("PRIVATE-WINDOW-TITLE-AND-CHAT", "UNKNOWN_TARGET_FORMAT"),
        ]:
            with patch("douyin_handoff.target_on_display", side_effect=RuntimeError(message)):
                event = secondary_diagnostic(self.s, "mismatch")
            self.assertEqual(event["window"]["match_failure"], category)
        self.assertNotIn("PRIVATE-WINDOW-TITLE-AND-CHAT", json.dumps(self.s.report))

    def test_diagnostic_transport_failure_is_not_ignored(self):
        self.s.shell.side_effect = RuntimeError("transport failed")
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            secondary_diagnostic(self.s, "error")
        self.assertEqual(self.s.report["handoff_diagnostics"][0]["error_type"], "RuntimeError")

    def test_gate_home_records_three_pre_model_stages_without_actions(self):
        with patch("builtins.input", return_value="home"):
            confirm_home_before_model(self.s)
        self.assertEqual(self.s.report["home_gate"]["status"], "READY_FOR_MODEL")
        self.assertEqual(self.s.report["home_gate"]["display_id"], 51)
        self.assertEqual([e["phase"] for e in self.s.report["handoff_diagnostics"]],
                         ["before-gate", "home-confirmed", "before-model"])
        self.assertTrue(all(e["model_called"] is False for e in self.s.report["handoff_diagnostics"]))
        self.assertFalse(self.s.report["model_called"])

    def test_missing_home_keeps_scene_until_ack_and_does_not_ready_model(self):
        def answer(prompt):
            if "当前状态" in prompt:
                return "missing"
            self.assertEqual(self.s.report["home_gate"]["status"], "MISSING_NAVIGATION")
            self.assertEqual(self.s.report["handoff_diagnostics"][-1]["phase"], "missing-navigation")
            return ""
        with patch("builtins.input", side_effect=answer), self.assertRaisesRegex(RuntimeError, "未调用模型"):
            confirm_home_before_model(self.s)
        self.assertFalse(self.s.report["model_called"])
        self.assertNotIn("home_ready_by_user", self.s.report)

    def test_gate_cancel_stops_without_readiness(self):
        with patch("builtins.input", return_value="q"), self.assertRaises(RuntimeError):
            confirm_home_before_model(self.s)
        self.assertEqual(self.s.report["home_gate"]["status"], "CANCELLED")

    def test_gate_cannot_be_forged_after_model_call(self):
        self.s.report["model_called"] = True
        with self.assertRaises(RuntimeError):
            confirm_home_before_model(self.s)
        self.s.capture.assert_not_called()

    def test_gate_accepts_natural_ad_to_human_confirmed_home_on_same_display(self):
        ad = {**CONTEXT, "activity": "com.ss.android.ugc.aweme/com.bytedance.ies.ugc.aweme.commercialize.splash.show.SplashAdActivity"}
        events = [{"context": ad}, {"context": CONTEXT}, {"context": CONTEXT}]
        with patch("douyin_handoff.secondary_diagnostic", side_effect=events), patch("builtins.input", return_value="1"):
            confirm_home_before_model(self.s)
        self.assertEqual(self.s.report["home_gate"]["status"], "READY_FOR_MODEL")
        self.assertEqual(self.s.report["home_gate"]["context"], CONTEXT)
        self.assertTrue(self.s.report["home_gate"]["pre_confirmation_transition"]["activity_changed"])
        self.assertFalse(self.s.report["model_called"])

    def test_gate_still_rejects_changed_display_or_other_app(self):
        for after in ({**OTHER, "display_id": 52}, {**OTHER, "activity": "other.app/.Home"},
                      {**OTHER, "display_id": 0}):
            with patch("douyin_handoff.secondary_diagnostic", side_effect=[{"context": CONTEXT}, {"context": after}]), \
                 patch("builtins.input", return_value="1"), self.assertRaises(RuntimeError):
                confirm_home_before_model(self.s)
            self.assertNotEqual(self.s.report["home_gate"]["status"], "READY_FOR_MODEL")

    def test_activity_change_after_home_confirmation_still_blocks_model(self):
        events = [{"context": CONTEXT}, {"context": CONTEXT}, {"context": OTHER}]
        with patch("douyin_handoff.secondary_diagnostic", side_effect=events), patch("builtins.input", return_value="1"), \
             self.assertRaises(RuntimeError):
            confirm_home_before_model(self.s)
        self.assertNotEqual(self.s.report["home_gate"]["status"], "READY_FOR_MODEL")

    def test_home_input_accepts_only_explicit_numeric_and_legacy_aliases(self):
        for raw, expected in (("1", "home"), ("0", "missing"), ("q", "cancel"),
                              ("home", "home"), ("missing", "missing"), ("  home  ", "home")):
            with patch("builtins.input", return_value=raw):
                self.assertEqual(read_home_observation(self.s), expected)

    def test_invisible_input_does_not_approve_until_explicit_retry(self):
        for invalid in ("\x1b[200~home", "home\u200b", "", "1 send message"):
            with patch("builtins.input", side_effect=[invalid, "1"]) as prompt:
                self.assertEqual(read_home_observation(self.s), "home")
            self.assertEqual(prompt.call_count, 2)
            self.assertEqual(self.s.report["home_gate_input_checks"][-2]["recognized"], "invalid")

    def test_input_retry_limit_and_privacy(self):
        with patch("builtins.input", side_effect=["PRIVATE-TEST-INPUT", "home\u200b"]) as prompt:
            self.assertEqual(read_home_observation(self.s), "invalid")
        self.assertEqual(prompt.call_count, 2)
        saved = json.dumps(self.s.report)
        self.assertNotIn("PRIVATE-TEST-INPUT", saved)
        self.assertNotIn("\\u200b", saved)
        self.assertEqual(self.s.report["home_gate_input_checks"][-1]["format_characters"], 1)

    def test_invalid_home_input_is_distinct_from_user_cancel(self):
        with patch("builtins.input", side_effect=["wrong", "wrong"]), self.assertRaisesRegex(RuntimeError, "两次确认输入均未识别"):
            confirm_home_before_model(self.s)
        self.assertEqual(self.s.report["home_gate"]["status"], "INVALID_INPUT")
        self.assertFalse(self.s.report["model_called"])

    def test_third_consecutive_wait_never_dispatches(self):
        actions = StartupActions(self.delegate, self.s)
        self.assertTrue(actions.execute(WAIT, 1080, 2400).success)
        self.assertTrue(actions.execute(WAIT, 1080, 2400).success)
        result = actions.execute(WAIT, 1080, 2400)
        self.assertFalse(result.success)
        self.assertIn("第三次", result.message)
        self.assertEqual(self.delegate.execute.call_count, 2)
        self.assertEqual(actions.progress()["total_wait_seconds"], 4.0)
        self.assertFalse(self.s.report["stopped_step"]["action_attempted"])

    def test_actual_navigation_resets_consecutive_but_not_total_waits(self):
        actions = StartupActions(self.delegate, self.s)
        actions.execute(WAIT, 1080, 2400)
        with patch("builtins.input", return_value="skip"):
            self.assertTrue(actions.execute(SKIP, 1080, 2400).success)
        self.assertEqual(actions.progress()["consecutive_waits"], 0)
        self.assertEqual(actions.progress()["total_wait_seconds"], 2)
        self.assertEqual(actions.progress()["completed_actions"], ["WAIT", "SKIP_AD"])

    def test_failed_proposal_does_not_reset_wait_budget(self):
        actions = StartupActions(self.delegate, self.s)
        actions.execute(WAIT, 1080, 2400)
        with patch("builtins.input", return_value="cancel"):
            actions.execute(SKIP, 1080, 2400)
        self.assertEqual(actions.progress()["consecutive_waits"], 1)
        self.assertEqual(actions.progress()["completed_actions"], ["WAIT"])

    def test_reset_context_still_gets_executor_owned_wait_history(self):
        model = Mock(action_handler=self.delegate)
        def step(task):
            result = model.action_handler.execute(WAIT, 1080, 2400)
            return SimpleNamespace(action=WAIT, success=result.success, finished=result.should_finish, message=result.message)
        model.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, "第三次"):
            run_startup(model, self.s, self.root / "model.png", 8)
        self.assertEqual(model.step.call_count, 3)
        third = model.step.call_args.args[0]
        self.assertIn('"consecutive_waits": 2', third)
        self.assertIn('"completed_actions": ["WAIT", "WAIT"]', third)
        self.assertIn("不是重新开始", third)

    def test_handoff_prompt_does_not_instruct_wait_for_ads(self):
        self.assertIn("不等广告", startup_task(True, handoff=True))

    def test_handoff_locator_distinguishes_video_content_from_outer_chrome(self):
        task = startup_task(True, progress=self.actions_progress(), handoff=True)
        self.assertIn("LOCATE_MESSAGES_ENTRY", task)
        self.assertIn("外层底部导航栏", task)
        self.assertIn("聊天截图", task)
        self.assertIn("禁止点击底部“首页”", task)
        self.assertIn('message="OPEN_MESSAGES"', task)
        self.assertIn("不能证明消息页已打开", HANDOFF_SYSTEM_PROMPT)

    @staticmethod
    def actions_progress(attempted=False):
        return {"messages_click_attempted": attempted, "completed_actions": [],
                "consecutive_waits": 0, "total_wait_seconds": 0, "discarded_proposals": 0}

    def test_handoff_after_click_only_verifies_and_never_relocates(self):
        task = startup_task(True, progress=self.actions_progress(True), handoff=True)
        self.assertIn("VERIFY_MESSAGES_LIST", task)
        self.assertIn("禁止再次Tap", task)
        self.assertNotIn('message="OPEN_MESSAGES"', task)
        self.assertIn('finish(message="MESSAGES_READY")', task)

    def test_handoff_stage_uses_attempt_flag_not_completed_or_model_claims(self):
        progress = self.actions_progress()
        progress["completed_actions"] = ["OPEN_MESSAGES"]
        self.assertEqual(handoff_stage(progress), "LOCATE_MESSAGES_ENTRY")
        progress = self.actions_progress(True)  # Attempted, not yet verified.
        self.assertEqual(handoff_stage(progress), "VERIFY_MESSAGES_LIST")

    def test_reported_home_tap_remains_blocked_without_review_or_dispatch(self):
        actions = self.unlabeled_handler()
        bad = {"_metadata": "do", "action": "Tap", "element": [99, 965]}
        with patch("builtins.input") as prompt:
            for action in (bad, {**bad, "message": "OPEN_HOME"}):
                self.assertFalse(actions.execute(action, 1080, 2400).success)
        prompt.assert_not_called()
        self.s.preview.assert_not_called()
        self.delegate.execute.assert_not_called()
        self.assertFalse(self.s.report["stopped_step"]["action_attempted"])

    def test_handoff_restricts_navigation_to_messages(self):
        self.s.report["same_session_handoff"] = True
        actions = StartupActions(self.delegate, self.s)
        actions.home_ready = True
        for action in (BACK, SKIP, HOME_READY):
            self.assertFalse(actions.execute(action, 1080, 2400).success)
        self.delegate.execute.assert_not_called()

    def test_handoff_messages_tap_only_once(self):
        self.s.report["same_session_handoff"] = True
        actions = StartupActions(self.delegate, self.s)
        actions.home_ready = True
        with patch("builtins.input", return_value="messages"):
            self.assertTrue(actions.execute(MESSAGES, 1080, 2400).success)
            self.assertFalse(actions.execute(MESSAGES, 1080, 2400).success)
        self.delegate.execute.assert_called_once()

    def test_run_handoff_requires_ready_gate_on_current_display(self):
        self.s.report["same_session_handoff"] = True
        model = Mock(action_handler=self.delegate)
        for gate in ({}, {"status": "READY_FOR_MODEL", "display_id": 52, "context": CONTEXT},
                     {"status": "READY_FOR_MODEL", "display_id": 51, "context": OTHER}):
            self.s.report["home_gate"] = gate
            with self.assertRaises(RuntimeError):
                run_startup(model, self.s, self.root / "model.png", 8)
        model.step.assert_not_called()

    def unlabeled_handler(self):
        self.s.report["same_session_handoff"] = True
        actions = StartupActions(self.delegate, self.s)
        actions.home_ready = True
        return actions

    def test_unlabeled_tap_only_enters_review_in_confirmed_handoff(self):
        for home, enabled in ((False, False), (True, False), (False, True)):
            with self.assertRaises(RuntimeError):
                validate_startup_action(UNLABELED_MESSAGES, home, review_unlabeled_messages=enabled)
        self.assertEqual(validate_startup_action(UNLABELED_MESSAGES, True, review_unlabeled_messages=True),
                         "REVIEW_UNLABELED_MESSAGES")

    def test_unlabeled_tap_requires_preview_and_human_before_dispatch(self):
        actions = self.unlabeled_handler()
        def answer(question):
            self.delegate.execute.assert_not_called()
            self.s.preview.assert_called_once()
            self.assertEqual(self.s.report["startup_label_reviews"][-1]["status"], "PENDING_HUMAN_REVIEW")
            self.assertIn("底部消息按钮", question)
            return "messages"
        with patch("builtins.input", side_effect=answer):
            result = actions.execute(UNLABELED_MESSAGES, 1080, 2400)
        self.assertTrue(result.success)
        self.delegate.execute.assert_called_once_with(UNLABELED_MESSAGES, 1080, 2400)
        self.assertTrue(self.s.report["startup_actions"][0]["model_label_missing"])
        self.assertEqual(self.s.report["startup_label_reviews"][0]["status"], "HUMAN_CONFIRMED")

    def test_unlabeled_tap_cannot_use_generic_or_send_approval(self):
        actions = self.unlabeled_handler()
        for answer in ("yes", "n", "send 336789", "1", "q", ""):
            with patch("builtins.input", return_value=answer):
                self.assertFalse(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
        self.delegate.execute.assert_not_called()
        self.assertTrue(all(r["status"] == "HUMAN_REJECTED" for r in self.s.report["startup_label_reviews"]))

    def test_unlabeled_tap_unknown_fields_labels_and_non_bottom_coords_rejected(self):
        actions = self.unlabeled_handler()
        cases = [{**UNLABELED_MESSAGES, "message": "SEND_ONE"}, {**UNLABELED_MESSAGES, "message": "导航"},
                 {**UNLABELED_MESSAGES, "text": "1"}, {**UNLABELED_MESSAGES, "message": None},
                 {**UNLABELED_MESSAGES, "element": [696, 500]}, {**UNLABELED_MESSAGES, "element": [100, 967]},
                 {**UNLABELED_MESSAGES, "element": [True, 967]}]
        with patch("builtins.input") as prompt:
            for action in cases:
                self.assertFalse(actions.execute(action, 1080, 2400).success)
        self.delegate.execute.assert_not_called()
        prompt.assert_not_called()

    def test_unlabeled_tap_guard_failure_prevents_human_approval(self):
        actions = self.unlabeled_handler()
        self.s.context.side_effect = RuntimeError("primary/audio guard failure")
        with patch("builtins.input") as prompt:
            self.assertFalse(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
        prompt.assert_not_called()
        self.delegate.execute.assert_not_called()

    def test_unlabeled_tap_stale_target_after_approval_never_dispatches(self):
        actions = self.unlabeled_handler()
        self.s.capture.side_effect = [frame(), frame((700, 2250, 820, 2380))]
        with patch("builtins.input", return_value="messages"):
            self.assertFalse(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
        self.delegate.execute.assert_not_called()
        self.assertFalse(self.s.report["stopped_step"]["action_attempted"])

    def test_tiny_shift_before_review_still_requires_human_and_keeps_coordinates(self):
        actions = self.unlabeled_handler()
        self.s.reference = navigation_frame()
        current = navigation_frame((-2, -2))
        self.s.capture.return_value = current
        def answer(question):
            self.delegate.execute.assert_not_called()
            self.assertEqual(self.s.preview.call_args.args[0], current)
            self.assertIn("整个红圈", question)
            return "messages"
        with patch("builtins.input", side_effect=answer):
            self.assertTrue(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
        self.delegate.execute.assert_called_once_with(UNLABELED_MESSAGES, 1080, 2400)
        self.assertEqual(self.s.report["startup_navigation_frame_checks"][0]["phase"], "STARTUP_PRE_REVIEW")

    def test_tiny_shift_does_not_bypass_human_rejection(self):
        actions = self.unlabeled_handler()
        self.s.reference = navigation_frame()
        self.s.capture.return_value = navigation_frame((-2, -2))
        with patch("builtins.input", return_value="no"):
            self.assertFalse(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
        self.delegate.execute.assert_not_called()

    def test_tiny_shift_after_approval_is_checked_without_rewriting_coordinates(self):
        actions = self.unlabeled_handler()
        current, shifted = navigation_frame(), navigation_frame((-3, -3), brightness_delta=5)
        self.s.reference = current
        self.s.capture.side_effect = [current, shifted, shifted]
        with patch("builtins.input", return_value="messages"):
            self.assertTrue(actions.execute(MESSAGES, 1080, 2400).success)
        self.assertEqual(self.delegate.execute.call_args.args[0]["element"], MESSAGES["element"])
        self.assertEqual(self.s.report["startup_navigation_frame_checks"][0]["phase"], "STARTUP_POST_REVIEW")
        self.assertEqual(self.s.report["startup_navigation_frame_checks"][0]["translation_native_px"], [-3, -3])

    def test_larger_shift_after_approval_still_stops(self):
        actions = self.unlabeled_handler()
        current = navigation_frame()
        self.s.reference = current
        self.s.capture.side_effect = [current, navigation_frame((-MAX_SHIFT - 1, -2))]
        with patch("builtins.input", return_value="messages"):
            self.assertFalse(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
        self.delegate.execute.assert_not_called()
        self.assertEqual(self.s.report["stopped_step"]["phase"], "STARTUP_POST_REVIEW")

    def test_non_handoff_navigation_retains_strict_pixel_guard(self):
        actions = StartupActions(self.delegate, self.s)
        actions.home_ready = True
        self.s.reference = navigation_frame()
        self.s.capture.return_value = navigation_frame((-2, -2))
        with patch("builtins.input") as prompt:
            self.assertFalse(actions.execute(MESSAGES, 1080, 2400).success)
        prompt.assert_not_called()
        self.delegate.execute.assert_not_called()

    def test_explicit_messages_label_does_not_allow_home_coordinates(self):
        actions = self.unlabeled_handler()
        with patch("builtins.input") as prompt:
            self.assertFalse(actions.execute({**MESSAGES, "element": [99, 965]}, 1080, 2400).success)
        prompt.assert_not_called()
        self.delegate.execute.assert_not_called()

    def test_unlabeled_tap_does_not_allow_second_messages_attempt(self):
        actions = self.unlabeled_handler()
        with patch("builtins.input", return_value="messages") as prompt:
            self.assertTrue(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
            self.assertFalse(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
        prompt.assert_called_once()
        self.delegate.execute.assert_called_once()

    def test_unlabeled_failed_dispatch_is_not_retried(self):
        actions = self.unlabeled_handler()
        self.delegate.execute.return_value = ActionResult(False, False, "timeout")
        with patch("builtins.input", return_value="messages"):
            self.assertFalse(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
            self.assertFalse(actions.execute(UNLABELED_MESSAGES, 1080, 2400).success)
        self.delegate.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
