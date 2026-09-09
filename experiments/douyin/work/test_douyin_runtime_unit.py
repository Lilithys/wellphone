"""Offline integration checks; FakeADB never contacts a phone or model."""
import os
import signal
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from douyin_audio import AudioLease, OPS, restore_safely
from run_douyin_test import DouyinSession, execute, protected_runtime
from test_douyin_audio_unit import FakeADB
from test_douyin_unit import CONTEXT, TAP, frame
from phone_agent.actions.handler import ActionResult
from router.device import DeviceError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adb = FakeADB()
        self.events = []
        self.session = SimpleNamespace(serial=self.adb.serial, report={}, frames=Mock(),
                                       monitor=None, audio_lease=None, save=Mock())
        self.session.stop_display = Mock(side_effect=self.stop)
        self.session.frames.stop.side_effect = lambda: self.events.append("decoder_stop")
        self.monitor = Mock(samples=[{"keyboard_on_primary": True}], errors=[])
        self.monitor.__enter__ = Mock()
        self.monitor.__exit__ = Mock(side_effect=lambda *_: self.events.append("monitor_stop"))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("run_douyin_test.ROOT", self.root))
        self.stack.enter_context(patch("run_douyin_test.Monitor", return_value=self.monitor))
        self.stack.enter_context(patch("run_douyin_test.restore_safely",
                                       side_effect=lambda lease, report: restore_safely(lease, report, timeout=0)))
        def write(op, mode):
            self.events.append((op, mode))
            if mode == "allow":
                self.assertIn("display_stop", self.events)
                self.assertIn("decoder_stop", self.events)
                self.assertFalse(self.adb.playing)
        self.adb.on_set = write

    def stop(self):
        self.events.append("display_stop")
        self.adb.playing = False
        self.session.report["virtual_display_removed"] = True

    def test_protected_before_launch_restored_before_monitor_stops(self):
        with protected_runtime(self.session, self.adb, Mock()):
            self.assertEqual(self.adb.modes, {op: "ignore" for op in OPS})
            self.session.audio_lease.require_restricted()
            self.adb.playing = True
        self.assertEqual(self.adb.modes, {op: "allow" for op in OPS})
        self.assertTrue(self.session.report["permissions_restored"])
        self.assertEqual(self.events[-1], "monitor_stop")
        self.session.stop_display.assert_called_once()

    def test_launch_model_failure_and_interrupt_all_restore(self):
        old_int, old_term = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
        for error in (RuntimeError("launch failed"), TimeoutError("model timeout"), KeyboardInterrupt(), EOFError()):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                with protected_runtime(self.session, self.adb, Mock()):
                    self.adb.playing = True
                    raise error
            self.assertEqual(self.adb.modes, {op: "allow" for op in OPS})
            self.assertTrue(self.session.report["permissions_restored"])
            self.assertEqual(signal.getsignal(signal.SIGINT), old_int)
            self.assertEqual(signal.getsignal(signal.SIGTERM), old_term)

    def test_partial_apply_failure_never_enters_launch_but_restores(self):
        def fail(op, mode):
            if op == "PLAY_AUDIO" and mode == "ignore":
                raise TimeoutError("unknown write outcome")
        self.adb.on_set = fail
        entered = False
        with self.assertRaises(TimeoutError):
            with protected_runtime(self.session, self.adb, Mock()):
                entered = True
        self.assertFalse(entered)
        self.assertEqual(self.adb.modes, {op: "allow" for op in OPS})
        self.assertTrue(self.session.report["permissions_restored"])
        self.assertTrue(self.session.report["audio_write_attempted"])
        self.assertEqual(self.session.report["audio_setup_stage"], "APPLY_RESTRICTIONS")
        self.assertEqual(self.session.report["audio_cleanup_status"], "RESTORED")

    def test_prepare_query_failure_never_writes_or_claims_restore_failure(self):
        original_shell = self.adb.shell
        def fail_audio_query(*args):
            if args == ("dumpsys", "audio"):
                raise DeviceError("fake query failure")
            return original_shell(*args)
        with patch.object(self.adb, "shell", side_effect=fail_audio_query), self.assertRaises(DeviceError):
            with protected_runtime(self.session, self.adb, Mock()):
                self.fail("must not launch")
        self.assertEqual(self.adb.writes(), [])
        self.assertEqual(self.session.report["audio_setup_stage"], "PREPARE_READONLY")
        self.assertFalse(self.session.report["audio_write_attempted"])
        self.assertIsNone(self.session.report["permissions_restored"])
        self.assertEqual(self.session.report["audio_cleanup_status"], "NOT_PREPARED_NO_CURRENT_WRITES")
        self.assertNotIn("audio_recovery_journal", self.session.report)

    def test_teardown_error_and_active_player_leave_recovery_pending(self):
        self.session.stop_display.side_effect = RuntimeError("device disconnected")
        with protected_runtime(self.session, self.adb, Mock()):
            self.adb.playing = True
        self.assertFalse(self.session.report["permissions_restored"])
        self.assertEqual(self.session.report["audio_cleanup_status"], "RECOVERY_UNCONFIRMED")
        self.assertIn("recovery_error", self.session.report)
        self.assertEqual(self.adb.modes, {op: "ignore" for op in OPS})
        with self.assertRaises(RuntimeError):
            AudioLease(self.adb, self.root).require_no_pending()
        self.monitor.__exit__.assert_called_once()

    def test_decoder_failure_does_not_skip_audio_recovery(self):
        self.session.frames.stop.side_effect = RuntimeError("decoder error")
        self.adb.on_set = None
        with protected_runtime(self.session, self.adb, Mock()):
            self.adb.playing = True
        self.assertTrue(self.session.report["permissions_restored"])
        self.assertIn("decoder_cleanup_error", self.session.report)

    def test_pending_prior_lease_not_overwritten_or_restored_by_new_run(self):
        old = AudioLease(self.adb, self.root)
        old.prepare()
        old.apply()
        writes = len(self.adb.writes())
        with self.assertRaises(RuntimeError):
            with protected_runtime(self.session, self.adb, Mock()):
                self.fail("must not launch")
        self.assertEqual(len(self.adb.writes()), writes)
        self.assertEqual(self.adb.modes, {op: "ignore" for op in OPS})


class SessionGuardTests(unittest.TestCase):
    def test_scope_rejects_absent_or_changed_audio_before_display_access(self):
        session = DouyinSession.__new__(DouyinSession)
        session.monitor, session.guard = Mock(), Mock()
        for lease in (None, Mock()):
            session.audio_lease = lease
            if lease is not None:
                lease.require_restricted.side_effect = RuntimeError("audio mode changed")
            with self.assertRaises(RuntimeError):
                session.scope()
        session.guard.assert_not_called()

    def test_launch_cannot_bypass_audio_lease(self):
        session = DouyinSession.__new__(DouyinSession)
        session.monitor, session.audio_lease = Mock(), None
        with patch("run_douyin_test.FrameStream") as decoder, self.assertRaises(RuntimeError):
            session.launch()
        decoder.assert_not_called()


class EntrypointTests(unittest.TestCase):
    def run_entry(self, *, preflight=False, cancel=False, model_error=False, restore_error=False, music="正常", message="emoji", startup=False,
                  handoff=False, missing_home=False, key="fake-test-key", post_diagnostic_error=False, home_answers=None,
                  unlabeled_tap=False, preflight_diagnostic_error=None, non_presentation=False,
                  trial_verified=True, navigation_motion=None, auto_messages=False,
                  send_one_flow=False, freeform_finish=False, input_cancel=False, send_cancel=False,
                  central_recipient_proposal=False, recipient_point=None, low_fps_trial=False):
        if send_one_flow:
            handoff = non_presentation = auto_messages = True
            message = "1"
        startup = startup or handoff
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            output = root / "evidence"
            output.mkdir()
            adb = FakeADB()
            adb.connect = Mock(return_value=adb)
            adb.state = Mock(return_value={})
            session = SimpleNamespace(serial=adb.serial, output=output, report={"send_attempted": False},
                args=SimpleNamespace(step_by_step=False), prepare=Mock(), save=Mock(), preview=Mock(),
                frames=Mock(), process=SimpleNamespace(pid=1234), display_id=None, scrcpy_log=[], monitor=None,
                context=Mock(return_value=CONTEXT), last_capture_context=CONTEXT, scope=Mock(),
                capture=Mock(return_value=frame()), audio_lease=None, shell=Mock(return_value=""))
            if navigation_motion or auto_messages:
                from test_douyin_navigation_frame_unit import navigation_frame
                native_frames = {"handoff-home-confirmed.png": navigation_frame(),
                                 "startup-model-01.png": navigation_frame(),
                                 "startup-review-01.png": navigation_frame((-2, -2)),
                                 "startup-approved-01.png": navigation_frame((-5, -5), brightness_delta=5,
                                     target_missing=navigation_motion == "disappear")}
                session.capture.side_effect = lambda name=None, *_: native_frames.get(name, frame())
            def launch():
                self.assertEqual(adb.modes, {op: "ignore" for op in OPS})
                session.audio_lease.require_restricted()
                session.display_id = 51
                if non_presentation:
                    session.report["non_presentation_verified"] = trial_verified
                adb.playing = True
            def stop():
                adb.playing = False
                session.report["virtual_display_removed"] = True
                if restore_error:
                    adb.modes["PLAY_AUDIO"] = "deny"  # External edit must not be overwritten.
            session.launch, session.stop_display = Mock(side_effect=launch), Mock(side_effect=stop)
            monitor = Mock(samples=[{"keyboard_on_primary": True}], errors=[])
            monitor.__enter__, monitor.__exit__ = Mock(), Mock()
            model = Mock()
            delegate = model.action_handler
            delegate.execute.return_value = ActionResult(True, False)
            model_steps = []
            def step(_):
                self.assertEqual(adb.modes, {op: "ignore" for op in OPS})
                self.assertEqual(session.reference_context, CONTEXT)
                warming = startup and (not send_one_flow or len(model_steps) < 2)
                if handoff and warming:
                    stage = "VERIFY_MESSAGES_LIST" if model_steps else "LOCATE_MESSAGES_ENTRY"
                    self.assertIn(stage, _)
                    self.assertEqual(session.report["model_requests"][-1]["navigation_stage"], stage)
                    self.assertEqual(session.report["handoff_prompt_version"], "outer-chrome-v1")
                if model_error:
                    raise TimeoutError("model timeout")
                if warming:
                    action = [{"_metadata": "finish", "message": "HOME_READY"},
                              {**TAP, "element": [696, 965], "message": "OPEN_MESSAGES"},
                              {"_metadata": "finish", "message": "MESSAGES_READY"}][len(model_steps) + (1 if handoff else 0)]
                    if freeform_finish and action.get("message") == "MESSAGES_READY":
                        action = {"_metadata": "finish", "message": "确认完成！当前页面是抖音消息列表。"}
                elif send_one_flow:
                    proposals = [
                        {"_metadata": "do", "action": "Swipe", "start": [400, 780], "end": [400, 400]},
                        {**TAP, "element": [250, 450]},
                        {**TAP, "element": [350, 930]},
                        {**TAP, "element": [900, 930]},
                    ]
                    if recipient_point is not None:
                        proposals[1] = {**TAP, "element": recipient_point}
                    if central_recipient_proposal:
                        proposals.insert(1, {**TAP, "element": [600, 594]})
                    action = proposals[len(model_steps) - 2]
                else:
                    action = ({"_metadata": "do", "action": "Type", "text": "1", "message": "INPUT_ONE"}
                              if message == "1" and not model_steps else {**TAP, "message": "SEND_ONE"})
                if unlabeled_tap and action.get("action") == "Tap":
                    action = {"_metadata": "do", "action": "Tap", "element": [696, 967]}
                model_steps.append(action)
                result = model.action_handler.execute(action, 1080, 2400)
                return SimpleNamespace(action=action, success=result.success, finished=result.should_finish,
                                       message=result.message)
            model.step.side_effect = step
            answers = ["no"] if cancel else (["yes", "", "missing", ""] if handoff and missing_home else
                      ["yes", ""] + (["home"] if home_answers is None else home_answers) + ([] if model_error else ["messages"] if auto_messages else ["messages", "messages"]) if handoff else
                      ["yes", ""] + ([] if model_error else ["home", "messages", "messages"]) if startup else
                      ["yes", "", "是"] if preflight else
                      ["yes", ""] + ([] if model_error else (["type 1", "yes"] if message == "1" else []) + ["send 336789", "yes"]))
            if send_one_flow and not cancel and not model_error and not missing_home:
                answers = ["yes", "", "1", "messages"]
                answers += ["no"] if input_cancel else ["type 1", "yes"]
                if not input_cancel:
                    answers += ["no"] if send_cancel else ["send 336789", "yes"]
            if not cancel:
                answers += ["正常", "无", music]
            for name, value in [("ROOT", root), ("DouyinSession", Mock(return_value=session)),
                                ("ADB", Mock(return_value=adb)), ("PinnedMain", Mock()),
                                ("Monitor", Mock(return_value=monitor)), ("countdown", Mock())]:
                stack.enter_context(patch("run_douyin_test." + name, value))
            factory = stack.enter_context(patch("phone_agent.agent.PhoneAgent", return_value=model))
            if preflight_diagnostic_error:
                from douyin_handoff import secondary_diagnostic
                def diagnose_preflight(current, phase):
                    if phase == preflight_diagnostic_error:
                        raise RuntimeError("preflight diagnostic failed")
                    return secondary_diagnostic(current, phase)
                stack.enter_context(patch("douyin_handoff.secondary_diagnostic", side_effect=diagnose_preflight))
            if handoff:
                def construct(*_args, **_kwargs):
                    self.assertEqual(session.report["home_gate"]["status"], "READY_FOR_MODEL")
                    self.assertEqual(session.report["home_gate"]["display_id"], session.display_id)
                    session.launch.assert_called_once()
                    return model
                factory.side_effect = construct
                if post_diagnostic_error:
                    from douyin_handoff import secondary_diagnostic
                    def diagnose(current, phase):
                        if phase == "after-model":
                            raise RuntimeError("post diagnostic failed")
                        return secondary_diagnostic(current, phase)
                    # execute imports this symbol locally; the gate's own calls use the same safe wrapper.
                    stack.enter_context(patch("douyin_handoff.secondary_diagnostic", side_effect=diagnose))
            stack.enter_context(patch("phone_agent.device_factory.set_virtual_display"))
            stack.enter_context(patch("douyin_input.require_editor", return_value={"target": CONTEXT, "focused_editor": {"view_token": "ab"}}))
            stack.enter_context(patch("builtins.input", side_effect=answers))
            hidden_key = stack.enter_context(patch("run_douyin_test.getpass.getpass", side_effect=AssertionError("unexpected key prompt")))
            stack.enter_context(patch.dict(os.environ, {"PHONE_AGENT_API_KEY": key}))
            args = SimpleNamespace(preflight=preflight, step_by_step=False, max_steps=20 if send_one_flow else 3 if startup else 2,
                                   window_title="test", message=message, startup_only=startup and not send_one_flow,
                                   send_one_flow=send_one_flow, confirm_home_first=handoff,
                                   non_presentation=non_presentation, auto_messages=auto_messages,
                                   low_fps_trial=low_fps_trial)
            session.args = args
            code = execute(args)
            if auto_messages and not send_one_flow:
                session.preview.assert_not_called()
            if cancel or preflight or (handoff and missing_home) or (non_presentation and not trial_verified):
                factory.assert_not_called()
                delegate.execute.assert_not_called()
            if cancel:
                self.assertEqual(adb.writes(), [])
                session.launch.assert_not_called()
            else:
                session.stop_display.assert_called_once()
                session.launch.assert_called_once()
            if not restore_error:
                self.assertEqual(adb.modes, {op: "allow" for op in OPS})
            if startup and not send_one_flow:
                if handoff:
                    self.assertTrue(all(call.args[0] == "dumpsys" for call in session.shell.call_args_list))
                    hidden_key.assert_not_called()
                else:
                    session.shell.assert_not_called()
                self.assertFalse(list((root / "outputs").glob("douyin-send-*.json")))
                self.assertFalse(list((root / "outputs").glob("douyin-input-one-*.json")))
            if message == "1" and not startup and not cancel and not preflight and not model_error:
                session.shell.assert_called_once_with("input", "keyboard", "-d", 51, "text", "1")
                self.assertEqual(delegate.execute.call_count, 1)  # send Tap only; never Type.
            if send_one_flow:
                input_calls = [call.args for call in session.shell.call_args_list if call.args[0] == "input"]
                expected_input = not (input_cancel or cancel or model_error or missing_home or not trial_verified)
                self.assertEqual(input_calls, [("input", "keyboard", "-d", 51, "text", "1")] if expected_input else [])
                self.assertEqual(bool(list((root / "outputs").glob("douyin-input-one-*.json"))), expected_input)
                self.assertEqual(bool(list((root / "outputs").glob("douyin-send-*.json"))), bool(session.report["send_attempted"]))
                self.assertTrue(all(call.args[0].get("action") != "Type" for call in delegate.execute.call_args_list))
            return code, session.report

    def test_send_one_flow_reuses_one_runtime_and_budget_through_real_controllers(self):
        code, report = self.run_entry(send_one_flow=True, freeform_finish=True)
        self.assertEqual(code, 0)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertTrue(report["fixed_input_verified"])
        self.assertTrue(report["send_attempted"])
        self.assertTrue(report["message_verified_by_user"])
        self.assertFalse(report["recipient_delivery_verified"])
        self.assertEqual(len(report["model_requests"]), 6)
        self.assertEqual(len(report["conversation_steps"]), 4)
        self.assertFalse(report["fixed_input_route"]["model_requested"])
        self.assertEqual(report["startup_finish_reviews"][0]["status"], "HUMAN_CONFIRMED")
        self.assertTrue(report["permissions_restored"])
        self.assertTrue(report["virtual_display_removed"])

    def test_low_fps_flow_reuses_existing_once_only_input_send_and_cleanup(self):
        code, report = self.run_entry(send_one_flow=True, freeform_finish=True, low_fps_trial=True)
        self.assertEqual(code, 0)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertTrue(report["fixed_input_verified"])
        self.assertTrue(report["message_verified_by_user"])
        self.assertEqual(len(report["startup_actions"]), 1)
        self.assertEqual(len(report["model_requests"]), 6)
        self.assertTrue(report["permissions_restored"])
        self.assertTrue(report["virtual_display_removed"])

    def test_low_fps_flow_still_stops_at_input_or_send_rejection(self):
        for field in ("input_cancel", "send_cancel"):
            code, report = self.run_entry(send_one_flow=True, low_fps_trial=True, **{field: True})
            self.assertEqual(code, 1)
            self.assertFalse(report["send_attempted"])
            self.assertEqual(bool(report.get("fixed_input_attempted")), field == "send_cancel")
            self.assertTrue(report["permissions_restored"])

    def test_low_fps_startup_only_still_never_enters_input_or_send(self):
        code, report = self.run_entry(handoff=True, non_presentation=True, auto_messages=True, low_fps_trial=True)
        self.assertEqual(code, 0)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertNotIn("conversation_steps", report)
        self.assertFalse(report["send_attempted"])
        self.assertNotIn("fixed_input_attempted", report)

    def test_send_one_flow_input_cancel_does_not_write_or_send(self):
        code, report = self.run_entry(send_one_flow=True, input_cancel=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertFalse(report.get("fixed_input_attempted", False))
        self.assertFalse(report["send_attempted"])
        self.assertTrue(report["permissions_restored"])

    def test_send_one_flow_relocalizes_rejected_recipient_before_one_input_and_one_send(self):
        code, report = self.run_entry(send_one_flow=True, central_recipient_proposal=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(report["model_requests"]), 7)
        self.assertEqual(len(report["conversation_relocalizations"]), 1)
        self.assertFalse(report["conversation_relocalizations"][0]["action_attempted"])
        self.assertTrue(report["message_verified_by_user"])
        self.assertTrue(report["permissions_restored"])

    def test_send_one_flow_accepts_observed_row_center_with_identity_input_and_send_checks(self):
        code, report = self.run_entry(send_one_flow=True, recipient_point=[499, 552])
        self.assertEqual(code, 0)
        candidate = report["conversation_steps"][1]
        self.assertEqual(candidate["action"]["element"], [499, 552])
        self.assertTrue(candidate["success"])
        self.assertTrue(report["fixed_input_verified"])
        self.assertTrue(report["message_verified_by_user"])
        self.assertTrue(report["permissions_restored"])

    def test_send_one_flow_send_cancel_keeps_attempt_record_without_sending(self):
        code, report = self.run_entry(send_one_flow=True, send_cancel=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["fixed_input_verified"])
        self.assertFalse(report["send_attempted"])
        self.assertTrue(report["permissions_restored"])

    def test_send_one_flow_restoration_failure_never_counts_as_pass(self):
        code, report = self.run_entry(send_one_flow=True, restore_error=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["message_verified_by_user"])
        self.assertFalse(report["passed"])

    def test_actual_send_entry_applies_restores_and_attempts_only_once(self):
        code, report = self.run_entry()
        self.assertEqual(code, 0)
        self.assertTrue(report["permissions_restored"])
        self.assertTrue(report["send_attempted"])
        self.assertEqual(len(report["steps"]), 1)
        self.assertFalse(report["recipient_delivery_verified"])

    def test_cancel_preflight_never_changes_phone(self):
        code, _ = self.run_entry(preflight=True, cancel=True)
        self.assertEqual(code, 1)

    def test_preflight_also_requires_audio_and_never_calls_model(self):
        code, report = self.run_entry(preflight=True)
        self.assertEqual(code, 0)
        self.assertTrue(report["restriction_readback_verified"])
        self.assertFalse(report["send_attempted"])
        self.assertEqual(report["preflight_check"], "complete_home_navigation_and_primary_input")
        self.assertEqual([d["phase"] for d in report["handoff_diagnostics"]],
                         ["preflight-start", "preflight-before-close"])
        self.assertTrue(all(d["model_called"] is False for d in report["handoff_diagnostics"]))

    def test_preflight_diagnostic_failure_cleans_up_and_never_passes(self):
        for phase in ("preflight-start", "preflight-before-close"):
            code, report = self.run_entry(preflight=True, preflight_diagnostic_error=phase)
            self.assertEqual(code, 1)
            self.assertTrue(report["permissions_restored"])
            self.assertTrue(report["virtual_display_removed"])
            self.assertFalse(report["send_attempted"])
            self.assertIn("preflight diagnostic failed", report["error"])

    def test_comparison_preflight_requires_flag_evidence_as_well_as_isolation_and_cleanup(self):
        for verified in (False, True):
            code, report = self.run_entry(preflight=True, non_presentation=True, trial_verified=verified)
            self.assertEqual(code, 0 if verified else 1)
            self.assertTrue(report["permissions_restored"])
            self.assertTrue(report["virtual_display_removed"])
            self.assertFalse(report["send_attempted"])
            self.assertTrue(all(not d["model_called"] for d in report.get("handoff_diagnostics", [])))

    def test_nonpresentation_handoff_keeps_home_review_and_stops_at_messages(self):
        code, report = self.run_entry(handoff=True, non_presentation=True, home_answers=["1"], unlabeled_tap=True)
        self.assertEqual(code, 0)
        self.assertTrue(report["non_presentation_verified"])
        self.assertTrue(report["messages_ready_by_user"])
        self.assertEqual(len(report["startup_actions"]), 1)
        self.assertEqual(report["startup_actions"][0]["intent"], "OPEN_MESSAGES")
        self.assertFalse(report["send_attempted"])
        self.assertNotIn("fixed_input_attempted", report)
        self.assertTrue(report["permissions_restored"])
        self.assertTrue(report["virtual_display_removed"])

    def test_nonpresentation_missing_flag_evidence_blocks_model_but_still_restores(self):
        code, report = self.run_entry(handoff=True, non_presentation=True, trial_verified=False)
        self.assertEqual(code, 1)
        self.assertNotIn("model_requests", report)
        self.assertNotIn("steps", report)
        self.assertIn("尚未回读确认", report["error"])
        self.assertTrue(report["permissions_restored"])
        self.assertTrue(report["virtual_display_removed"])

    def test_nonpresentation_missing_home_or_model_error_never_dispatches_navigation(self):
        for options in ({"missing_home": True, "key": ""}, {"model_error": True}):
            code, report = self.run_entry(handoff=True, non_presentation=True, **options)
            self.assertEqual(code, 1)
            self.assertNotIn("startup_actions", report)
            self.assertFalse(report["send_attempted"])
            self.assertTrue(report["permissions_restored"])

    def test_model_error_cleans_up_without_sending(self):
        code, report = self.run_entry(model_error=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["permissions_restored"])
        self.assertFalse(report["send_attempted"])

    def test_restoration_error_cannot_pass_even_if_message_verified(self):
        code, report = self.run_entry(restore_error=True)
        self.assertEqual(code, 1)
        self.assertFalse(report["permissions_restored"])
        self.assertTrue(report["message_verified_by_user"])

    def test_abnormal_music_cannot_pass(self):
        code, report = self.run_entry(music="异常")
        self.assertEqual(code, 1)
        self.assertFalse(report["isolation_verified"])

    def test_music_untested_is_not_continuity_proof(self):
        code, report = self.run_entry(music="未测试")
        self.assertEqual(code, 0)
        self.assertFalse(report["background_music_continuity_verified"])

    def test_digit_mode_uses_custom_input_then_single_send_and_restores_audio(self):
        code, report = self.run_entry(message="1")
        self.assertEqual(code, 0)
        self.assertEqual(report["requested_message"], "1")
        self.assertTrue(report["fixed_input_verified"])
        self.assertTrue(report["send_attempted"])
        self.assertEqual(len(report["steps"]), 2)
        self.assertTrue(report["permissions_restored"])

    def test_startup_entry_stops_at_messages_without_input_send_and_restores(self):
        code, report = self.run_entry(startup=True, message="1")
        self.assertEqual(code, 0)
        self.assertTrue(report["home_ready_by_user"])
        self.assertTrue(report["messages_ready_by_user"])
        self.assertFalse(report["send_attempted"])
        self.assertNotIn("fixed_input_attempted", report)
        self.assertIsNone(report["requested_message"])
        self.assertTrue(report["permissions_restored"])

    def test_startup_model_failure_still_restores(self):
        code, report = self.run_entry(startup=True, model_error=True)
        self.assertEqual(code, 1)
        self.assertFalse(report["send_attempted"])
        self.assertTrue(report["permissions_restored"])

    def test_startup_audio_recovery_failure_cannot_pass(self):
        code, report = self.run_entry(startup=True, restore_error=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertFalse(report["permissions_restored"])

    def test_cancel_startup_does_not_launch_or_call_model(self):
        code, _ = self.run_entry(startup=True, cancel=True)
        self.assertEqual(code, 1)

    def test_handoff_confirms_home_before_model_on_single_display(self):
        code, report = self.run_entry(handoff=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(report["steps"]), 2)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertFalse(report["send_attempted"])
        self.assertEqual([e["phase"] for e in report["handoff_diagnostics"]],
                         ["before-gate", "home-confirmed", "before-model", "after-model"])
        self.assertFalse(report["handoff_diagnostics"][2]["model_called"])
        self.assertTrue(report["handoff_diagnostics"][3]["model_called"])
        self.assertEqual(len(report["startup_actions"]), 1)

    def test_handoff_motion_before_and_after_review_then_single_click_and_cleanup(self):
        code, report = self.run_entry(handoff=True, non_presentation=True, navigation_motion="small")
        self.assertEqual(code, 0)
        checks = report["startup_navigation_frame_checks"]
        self.assertEqual([check["phase"] for check in checks], ["STARTUP_PRE_REVIEW", "STARTUP_POST_REVIEW"])
        self.assertEqual([check["translation_native_px"] for check in checks], [[-2, -2], [-3, -3]])
        self.assertEqual(len(report["model_requests"]), 2)
        self.assertEqual(len(report["startup_actions"]), 1)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertFalse(report["send_attempted"])
        self.assertTrue(report["permissions_restored"])
        self.assertTrue(report["virtual_display_removed"])

    def test_handoff_disappearing_target_after_review_stops_and_restores(self):
        code, report = self.run_entry(handoff=True, non_presentation=True, navigation_motion="disappear")
        self.assertEqual(code, 1)
        self.assertEqual(report["stopped_step"]["phase"], "STARTUP_POST_REVIEW")
        self.assertFalse(report["stopped_step"]["action_attempted"])
        self.assertFalse(report.get("startup_actions"))
        self.assertFalse(report["send_attempted"])
        self.assertTrue(report["permissions_restored"])
        self.assertTrue(report["virtual_display_removed"])

    def test_auto_messages_entry_skips_preview_then_observes_transition_and_cleans_up(self):
        code, report = self.run_entry(handoff=True, non_presentation=True, auto_messages=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(report["model_requests"]), 2)
        self.assertEqual(len(report["startup_actions"]), 1)
        self.assertFalse(report["startup_auto_navigation"]["per_action_human_review"])
        self.assertEqual(report["startup_transition"]["status"], "VISUAL_TRANSITION_NOT_PAGE_VERIFIED")
        self.assertTrue(report["messages_ready_by_user"])
        self.assertTrue(report["permissions_restored"])
        self.assertTrue(report["virtual_display_removed"])
        self.assertFalse(report["send_attempted"])

    def test_handoff_missing_navigation_needs_no_key_model_or_click(self):
        code, report = self.run_entry(handoff=True, missing_home=True, key="")
        self.assertEqual(code, 1)
        self.assertEqual(report["home_gate"]["status"], "MISSING_NAVIGATION")
        self.assertTrue(report["permissions_restored"])
        self.assertNotIn("steps", report)

    def test_handoff_model_error_diagnoses_then_restores(self):
        code, report = self.run_entry(handoff=True, model_error=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["permissions_restored"])
        self.assertEqual(report["handoff_diagnostics"][-1]["phase"], "after-model")

    def test_handoff_post_diagnostic_failure_cannot_pass(self):
        code, report = self.run_entry(handoff=True, post_diagnostic_error=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertTrue(report["permissions_restored"])
        self.assertEqual(report["handoff_post_diagnostic_error"], "RuntimeError")

    def test_handoff_post_diagnostic_failure_preserves_original_error(self):
        code, report = self.run_entry(handoff=True, model_error=True, post_diagnostic_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "model timeout")
        self.assertTrue(report["permissions_restored"])

    def test_handoff_restoration_failure_cannot_pass(self):
        code, report = self.run_entry(handoff=True, restore_error=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertFalse(report["permissions_restored"])

    def test_handoff_reasks_invalid_input_then_accepts_numeric_home(self):
        code, report = self.run_entry(handoff=True, home_answers=["home\u200b", "1"])
        self.assertEqual(code, 0)
        self.assertEqual([row["recognized"] for row in report["home_gate_input_checks"]], ["invalid", "home"])
        self.assertEqual(report["home_gate"]["status"], "READY_FOR_MODEL")
        self.assertTrue(report["permissions_restored"])

    def test_handoff_unlabeled_messages_tap_is_reviewed_and_finishes_without_send(self):
        code, report = self.run_entry(handoff=True, home_answers=["1"], unlabeled_tap=True)
        self.assertEqual(code, 0)
        self.assertTrue(report["messages_ready_by_user"])
        self.assertTrue(report["startup_actions"][0]["model_label_missing"])
        self.assertEqual(report["startup_label_reviews"][0]["status"], "HUMAN_CONFIRMED")
        self.assertFalse(report["send_attempted"])
        self.assertTrue(report["permissions_restored"])


if __name__ == "__main__":
    unittest.main()
