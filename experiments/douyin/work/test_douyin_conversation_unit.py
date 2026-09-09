"""Finite flow tests with fake device/model and temporary attempt journals."""
import base64
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from douyin_conversation import ConversationActions, conversation_instruction, execute_fixed_one, input_focus_preflight, PROMPT_VERSION, run_conversation
from douyin_display import require_comparison_scope
from douyin_policy import OneSend, require_same_regions
from phone_agent.actions.handler import ActionResult
from run_douyin_test import DouyinSession
from test_douyin_unit import CONTEXT, frame

SWIPE = {"_metadata": "do", "action": "Swipe", "start": [350, 750], "end": [350, 450]}
OPEN = {"_metadata": "do", "action": "Tap", "element": [250, 600]}
FOCUS = {"_metadata": "do", "action": "Tap", "element": [400, 935]}
TYPE = {"_metadata": "do", "action": "Type", "text": "1"}
SEND = {"_metadata": "do", "action": "Tap", "element": [900, 935]}
CENTRAL_ROW = {"_metadata": "do", "action": "Tap", "element": [600, 594]}
OBSERVED_ROW = {"_metadata": "do", "action": "Tap", "element": [499, 552]}


class ConversationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.s = SimpleNamespace(output=self.root, serial="fake", display_id=51,
            args=SimpleNamespace(step_by_step=False, send_one_flow=True),
            report={"messages_ready_by_user": True, "send_attempted": False, "requested_message": "1"},
            reference=frame(), reference_context=CONTEXT, last_capture_context=CONTEXT,
            context=Mock(return_value=CONTEXT), capture=Mock(return_value=frame()), shell=Mock(return_value=""),
            scope=Mock(), preview=Mock(), save=Mock())
        self.delegate = Mock(execute=Mock(return_value=ActionResult(True, False)))
        self.journal = OneSend(self.root / "outputs", "fake")
        self.actions = ConversationActions(self.delegate, self.s, self.journal, self.root)
        self.actions.remaining_after_request = 5

    def execute(self, action):
        return self.actions.execute(action, 1080, 2400)

    def focus_candidate(self):
        self.assertTrue(self.execute(OPEN).success)
        self.assertTrue(self.execute(FOCUS).success)

    def test_complete_sequence_keeps_fixed_input_and_single_durable_send(self):
        with patch("builtins.input", side_effect=["type 1", "yes", "send 示例联系人"]), \
             patch("run_douyin_test.countdown"), \
             patch("douyin_input.require_editor", return_value={"target": CONTEXT, "focused_editor": {"view_token": "a"}}):
            for action in (SWIPE, OPEN, FOCUS, TYPE, SEND):
                result = self.execute(action)
                self.assertTrue(result.success, result.message)
        self.assertTrue(result.should_finish)
        self.assertEqual(self.actions.stage, "SENT_ATTEMPTED")
        self.s.shell.assert_called_once_with("input", "keyboard", "-d", 51, "text", "1")
        self.assertEqual(self.delegate.execute.call_count, 4)
        self.assertTrue(self.journal.path.exists())
        self.assertFalse(any(c.args[0].get("action") == "Type" for c in self.delegate.execute.call_args_list))
        self.assertFalse(self.execute(SEND).success)
        self.assertEqual(self.delegate.execute.call_count, 4)

    def test_runner_routes_fixed_payload_without_a_model_type_request(self):
        model = Mock()
        proposals = iter((OPEN, FOCUS, SEND))
        def step(_):
            action = next(proposals)
            result = model.action_handler.execute(action, 1080, 2400)
            return SimpleNamespace(action=action, success=result.success,
                                   finished=result.should_finish, message=result.message)
        model.step.side_effect = step
        with patch("builtins.input", side_effect=["type 1", "yes", "send 示例联系人"]), \
             patch("run_douyin_test.countdown"), \
             patch("douyin_input.require_editor", return_value={"target": CONTEXT, "focused_editor": {"view_token": "a"}}):
            run_conversation(model, self.s, self.root / "model.png", self.journal, self.root, 5, self.delegate)
        self.assertEqual(model.step.call_count, 3)
        self.assertEqual([r["substage"] for r in self.s.report["model_requests"]],
                         ["LOCATE_RECIPIENT_IN_LIST", "LOCATE_EMPTY_CHAT_EDITOR", "LOCATE_SEND_BUTTON"])
        self.assertFalse(self.s.report["fixed_input_route"]["model_requested"])
        self.s.shell.assert_called_once_with("input", "keyboard", "-d", 51, "text", "1")
        self.assertTrue(self.journal.path.exists())

    def test_fixed_route_does_not_infer_candidate_or_focus_or_skip_budget(self):
        with patch("douyin_input.require_editor") as editor:
            for stage, focused, budget in (("FIND_RECIPIENT", False, 2), ("CHAT", False, 2), ("CHAT", True, 0)):
                self.actions.stage, self.actions.focus_attempted = stage, focused
                with self.assertRaises(RuntimeError):
                    execute_fixed_one(self.actions, self.s, budget)
        editor.assert_not_called()
        self.s.shell.assert_not_called()

    def test_wrong_recipient_or_nonempty_draft_observation_stops_before_input(self):
        self.focus_candidate()
        with patch("builtins.input", return_value="no"):
            self.assertFalse(self.execute(TYPE).success)
        self.s.shell.assert_not_called()
        self.assertFalse(self.journal.path.exists())

    def test_input_requires_candidate_focus_and_followup_budget(self):
        with patch("builtins.input") as prompt:
            self.assertFalse(self.execute(TYPE).success)
            self.assertTrue(self.execute(OPEN).success)
            self.assertFalse(self.execute(TYPE).success)
            self.assertTrue(self.execute(FOCUS).success)
            self.actions.remaining_after_request = 0
            self.assertFalse(self.execute(TYPE).success)
        prompt.assert_not_called()
        self.s.shell.assert_not_called()

    def test_cannot_send_or_open_right_side_shortcut_from_list(self):
        for action in (SEND, {**OPEN, "message": "SEND_ONE"}, {**OPEN, "element": [850, 500]}):
            self.assertFalse(self.execute(action).success)
        self.delegate.execute.assert_not_called()

    def test_central_row_rejection_only_requests_one_new_localization_without_dispatch(self):
        self.assertFalse(self.execute(CENTRAL_ROW).success)
        self.assertTrue(self.actions.needs_relocalize)
        self.assertEqual(self.actions.stage, "FIND_RECIPIENT")
        self.assertEqual(self.s.report["conversation_relocalizations"][0]["action"], CENTRAL_ROW)
        self.assertFalse(self.s.report["conversation_relocalizations"][0]["action_attempted"])
        self.delegate.execute.assert_not_called()
        self.s.shell.assert_not_called()
        self.assertFalse(self.execute(CENTRAL_ROW).success)
        self.assertFalse(self.actions.needs_relocalize)
        self.assertEqual(len(self.s.report["conversation_relocalizations"]), 1)
        self.delegate.execute.assert_not_called()

    def test_relocalization_does_not_expand_dispatch_bounds_or_rewrite_coordinate(self):
        self.assertIn("x=80–550", conversation_instruction(self.actions.progress())[2])
        self.assertFalse(self.execute(CENTRAL_ROW).success)
        self.assertTrue(self.execute(OPEN).success)
        self.assertEqual(self.delegate.execute.call_args.args[0], OPEN)
        self.assertIsNone(self.actions.rejected_proposal)
        self.assertEqual(self.actions.stage, "CHAT")
        self.assertFalse(self.execute(CENTRAL_ROW).success)
        self.assertFalse(self.actions.needs_relocalize)
        self.delegate.execute.assert_called_once()

    def test_no_relocalization_for_other_regions_labels_dimensions_or_exhausted_budget(self):
        for action in ({**CENTRAL_ROW, "element": [651, 594]},
                       {**CENTRAL_ROW, "element": [499, 100]},
                       {**CENTRAL_ROW, "message": "SEND_ONE"},
                       {**CENTRAL_ROW, "text": "1"}):
            self.assertFalse(self.execute(action).success)
            self.assertFalse(self.actions.needs_relocalize)
        self.assertFalse(self.actions.execute(CENTRAL_ROW, 720, 1600).success)
        self.assertFalse(self.actions.needs_relocalize)
        self.actions.remaining_after_request = 0
        self.assertFalse(self.execute(CENTRAL_ROW).success)
        self.assertFalse(self.actions.needs_relocalize)
        self.delegate.execute.assert_not_called()

    def test_runner_refreezes_new_frame_and_gives_rejection_feedback_within_total_budget(self):
        before, newest = frame(), frame((0, 0, 12, 12))
        def capture(name=None, *_):
            return before if name == "conversation-model-01.png" else newest
        self.s.capture.side_effect = capture
        model, frozen = Mock(), self.root / "model.png"
        seen = []
        def step(task):
            seen.append((task, frozen.read_bytes()))
            action = CENTRAL_ROW if len(seen) == 1 else OPEN
            result = model.action_handler.execute(action, 1080, 2400)
            return SimpleNamespace(action=action, success=result.success, finished=result.should_finish,
                                   message=result.message)
        model.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, "总模型预算"):
            run_conversation(model, self.s, frozen, self.journal, self.root, 2, self.delegate)
        self.assertEqual([data for _, data in seen], [before, newest])
        self.assertIn("600", seen[1][0])
        self.assertIn("x=80–550", seen[1][0])
        self.assertEqual(len(self.s.report["model_requests"]), 2)
        self.delegate.execute.assert_called_once()
        self.assertEqual(self.delegate.execute.call_args.args[0], OPEN)
        self.s.shell.assert_not_called()

    def test_runner_stops_on_second_rejection_without_extra_model_or_phone_action(self):
        model = Mock()
        def step(_):
            result = model.action_handler.execute(CENTRAL_ROW, 1080, 2400)
            return SimpleNamespace(action=CENTRAL_ROW, success=result.success,
                                   finished=result.should_finish, message=result.message)
        model.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, "联系人点击超出导航限定区"):
            run_conversation(model, self.s, self.root / "model.png", self.journal, self.root, 5, self.delegate)
        self.assertEqual(model.step.call_count, 2)
        self.delegate.execute.assert_not_called()

    def test_relocalization_cannot_bypass_failed_new_capture(self):
        model = Mock()
        self.s.capture.side_effect = [frame(), RuntimeError("主屏状态查询失败")]
        def step(_):
            result = model.action_handler.execute(CENTRAL_ROW, 1080, 2400)
            return SimpleNamespace(action=CENTRAL_ROW, success=result.success,
                                   finished=result.should_finish, message=result.message)
        model.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, "主屏状态查询失败"):
            run_conversation(model, self.s, self.root / "model.png", self.journal, self.root, 5, self.delegate)
        model.step.assert_called_once()
        self.delegate.execute.assert_not_called()

    def test_model_error_after_relocalization_is_not_retried(self):
        model = Mock()
        requests = []
        def step(task):
            requests.append(task)
            if len(requests) == 2:
                return SimpleNamespace(action=None, success=False, finished=True, message="Model error: timeout")
            result = model.action_handler.execute(CENTRAL_ROW, 1080, 2400)
            return SimpleNamespace(action=CENTRAL_ROW, success=result.success,
                                   finished=result.should_finish, message=result.message)
        model.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, "Model error: timeout"):
            run_conversation(model, self.s, self.root / "model.png", self.journal, self.root, 5, self.delegate)
        self.assertEqual(model.step.call_count, 2)
        self.assertFalse(self.s.report["conversation_steps"][-1]["relocalization_requested"])
        self.delegate.execute.assert_not_called()

    def test_only_six_vertical_upward_list_swipes(self):
        for _ in range(6):
            self.assertTrue(self.execute(SWIPE).success)
        self.assertFalse(self.execute(SWIPE).success)
        self.assertEqual(self.delegate.execute.call_count, 6)
        for action in ({**SWIPE, "end": [600, 450]}, {**SWIPE, "start": [350, 300], "end": [350, 800]}):
            self.assertFalse(self.execute(action).success)

    def test_once_candidate_opened_no_back_swipe_or_other_row(self):
        self.assertTrue(self.execute(OPEN).success)
        for action in (OPEN, SWIPE, {"_metadata": "do", "action": "Back"}):
            self.assertFalse(self.execute(action).success)
        self.delegate.execute.assert_called_once()

    def test_extra_fields_other_text_and_wrong_labels_are_not_repaired(self):
        for action in ({**OPEN, "text": "1"}, {**OPEN, "message": "OTHER"},
                       {**TYPE, "text": "11"}, {**TYPE, "text": "1\n"}, {**TYPE, "text": 1},
                       {"_metadata": "do", "action": "Launch", "app": "Other"}):
            self.assertFalse(self.execute(action).success)
        self.delegate.execute.assert_not_called()
        self.s.shell.assert_not_called()

    def test_missing_verified_message_list_blocks_all_actions(self):
        self.s.report["messages_ready_by_user"] = False
        self.assertFalse(self.execute(OPEN).success)
        self.delegate.execute.assert_not_called()

    def test_primary_guard_failure_prevents_candidate_click(self):
        self.s.context.side_effect = RuntimeError("主屏异常")
        self.assertFalse(self.execute(OPEN).success)
        self.delegate.execute.assert_not_called()

    def test_failed_candidate_dispatch_is_not_retried(self):
        self.delegate.execute.return_value = ActionResult(False, False, "timeout")
        self.assertFalse(self.execute(OPEN).success)
        self.assertFalse(self.actions.needs_relocalize)
        self.assertFalse(self.execute(OPEN).success)
        self.delegate.execute.assert_called_once()

    def test_finish_before_send_never_counts_as_success(self):
        self.assertFalse(self.execute({"_metadata": "finish", "message": "已发送"}).success)
        self.assertFalse(self.s.report["send_attempted"])
        self.delegate.execute.assert_not_called()

    def test_find_request_is_list_only_not_the_overall_send_task(self):
        stage, system, task = conversation_instruction(self.actions.progress())
        self.assertEqual(stage, "LOCATE_RECIPIENT_IN_LIST")
        self.assertIn("下一步是向上滑动列表，不是搜索", task)
        self.assertIn("x=80–550", task)
        self.assertNotIn('action="Type"', task + system)
        self.assertNotIn('"payload"', task)
        self.assertNotIn("发一条数字1", task + system)

    def test_chat_requests_separate_focus_from_input(self):
        self.actions.stage = "CHAT"
        stage, _, focus_task = conversation_instruction(self.actions.progress())
        self.assertEqual(stage, "LOCATE_EMPTY_CHAT_EDITOR")
        self.assertNotIn('action="Type"', focus_task)
        self.assertNotIn("LOCATE_SEND_BUTTON", focus_task)
        self.actions.focus_attempted = True
        stage, input_system, input_task = conversation_instruction({**self.actions.progress(), "system_editor_focus_verified": True})
        self.assertEqual(stage, "PROPOSE_FIXED_ONE_INPUT")
        self.assertIn('do(action="Type",text="1")', input_system)
        self.assertNotIn('do(action="Tap"', input_task + input_system)

    def test_user_tasks_do_not_contain_executable_examples_for_echo_parser(self):
        for stage, focused in (("FIND_RECIPIENT", False), ("CHAT", False), ("CHAT", True), ("READY_SEND", True)):
            progress = {**self.actions.progress(), "stage": stage, "focus_attempted": focused,
                        "system_editor_focus_verified": True}
            _, system, task = conversation_instruction(progress)
            self.assertNotIn("finish(message=", task)
            self.assertNotIn("do(action=", task)
            self.assertIn("不复述任务或动作示例", system)

    def test_input_prompt_requires_system_focus_evidence_not_keyboard_appearance(self):
        self.actions.stage, self.actions.focus_attempted = "CHAT", True
        with self.assertRaises(RuntimeError):
            conversation_instruction(self.actions.progress())
        with patch("douyin_input.require_editor") as check:
            progress = input_focus_preflight(self.s, self.actions.progress())
        check.assert_called_once_with(self.s)
        self.assertTrue(progress["system_editor_focus_verified"])
        self.assertFalse(progress["keyboard_visibility_required"])
        _, _, task = conversation_instruction(progress)
        self.assertIn("不要凭键盘、光标外观重复判断系统焦点", task)
        self.assertNotIn("system_editor_focus_verified", self.actions.progress())
        self.s.shell.assert_not_called()

    def test_focus_preflight_failure_is_recorded_and_never_assumed_or_written(self):
        self.actions.stage, self.actions.focus_attempted = "CHAT", True
        with patch("douyin_input.require_editor", side_effect=RuntimeError("editor not focused")), \
                self.assertRaisesRegex(RuntimeError, "editor not focused"):
            input_focus_preflight(self.s, self.actions.progress())
        self.assertEqual(self.s.report["conversation_editor_preflight"][-1]["status"], "FOCUS_NOT_CONFIRMED")
        self.assertFalse(self.s.report["conversation_editor_preflight"][-1]["input_attempted"])
        self.delegate.execute.assert_not_called()
        self.s.shell.assert_not_called()

    def test_other_stages_do_not_inspect_editors_or_reuse_focus_evidence(self):
        with patch("douyin_input.require_editor") as check:
            for stage, focused in (("FIND_RECIPIENT", False), ("CHAT", False), ("READY_SEND", True)):
                self.actions.stage, self.actions.focus_attempted = stage, focused
                self.assertEqual(input_focus_preflight(self.s, self.actions.progress()), self.actions.progress())
        check.assert_not_called()

    def test_runner_stops_before_input_model_call_when_focus_cannot_be_verified(self):
        model = Mock()
        def step(_):
            action = OPEN if model.step.call_count == 1 else FOCUS
            result = model.action_handler.execute(action, 1080, 2400)
            return SimpleNamespace(action=action, success=result.success, finished=False)
        model.step.side_effect = step
        with patch("douyin_input.require_editor", side_effect=RuntimeError("no focused editor")), \
                self.assertRaisesRegex(RuntimeError, "no focused editor"):
            run_conversation(model, self.s, self.root / "model.png", self.journal, self.root, 5, self.delegate)
        self.assertEqual(model.step.call_count, 2)
        self.assertEqual(self.delegate.execute.call_count, 2)
        self.s.shell.assert_not_called()
        self.assertFalse(self.journal.path.exists())

    def test_send_request_never_invites_more_input_or_navigation(self):
        self.actions.stage = "READY_SEND"
        stage, _, task = conversation_instruction(self.actions.progress())
        self.assertEqual(stage, "LOCATE_SEND_BUTTON")
        self.assertIn("再次请求人工批准", task)
        self.assertNotIn('action="Type"', task)
        self.assertNotIn('action="Swipe"', task)

    def test_unknown_or_ended_prompt_stage_fails_closed(self):
        for stage in ("BAD", "SENT_ATTEMPTED"):
            with self.assertRaisesRegex(RuntimeError, "未知或已结束"):
                conversation_instruction({**self.actions.progress(), "stage": stage})

    def test_search_proposal_still_never_dispatches_or_enters_relocalization(self):
        result = self.execute({"_metadata": "do", "action": "Tap", "element": [813, 69]})
        self.assertFalse(result.success)
        self.assertFalse(self.actions.needs_relocalize)
        self.assertEqual(self.actions.stage, "FIND_RECIPIENT")
        self.delegate.execute.assert_not_called()
        self.s.shell.assert_not_called()

    def test_observed_row_center_dispatches_unchanged_with_extra_row_guard(self):
        with patch("builtins.input") as prompt:
            result = self.execute(OBSERVED_ROW)
        self.assertTrue(result.success, result.message)
        self.delegate.execute.assert_called_once()
        self.assertEqual(self.delegate.execute.call_args.args[0], OBSERVED_ROW)
        self.assertEqual(self.s.report["action_decisions"][0]["frame_check"], "target_and_recipient_row")
        self.assertEqual(self.actions.stage, "CHAT")
        self.assertTrue(self.actions.navigation.recipient_row_check)
        prompt.assert_not_called()
        self.s.shell.assert_not_called()
        self.assertFalse(self.s.report["send_attempted"])

    def test_candidate_range_only_extends_to_550_not_right_shortcuts(self):
        for x in (80, 450, 499, 550):
            result = self.actions.normalize({**OBSERVED_ROW, "element": [x, 552]})
            self.assertEqual(result["element"], [x, 552])
        for point in ([79, 552], [551, 552], [650, 552], [850, 552], [499, 209], [499, 881]):
            with self.assertRaises(RuntimeError):
                self.actions.normalize({**OBSERVED_ROW, "element": point})

    def test_candidate_name_band_change_is_detected_even_if_center_stays_blank(self):
        changed = frame((180, 1280, 360, 1380))
        require_same_regions(frame(), changed, OBSERVED_ROW)  # Old point-only check sees white.
        self.s.capture.return_value = changed
        result = self.execute(OBSERVED_ROW)
        self.assertFalse(result.success)
        self.assertEqual(self.s.report["stopped_step"]["phase"], "PRE_REVIEW_FRAME_CHECK")
        self.assertEqual(self.s.report["stopped_step"]["frame_difference"]["box"][0], 32)
        self.delegate.execute.assert_not_called()
        self.assertFalse(self.actions.needs_relocalize)

    def test_candidate_name_band_is_rechecked_immediately_before_dispatch(self):
        self.s.capture.side_effect = [frame(), frame((180, 1280, 360, 1380))]
        result = self.execute(OBSERVED_ROW)
        self.assertFalse(result.success)
        self.assertEqual(self.s.report["stopped_step"]["phase"], "POST_REVIEW_FRAME_CHECK")
        self.delegate.execute.assert_not_called()
        self.s.shell.assert_not_called()

    def test_candidate_row_guard_is_not_reused_for_chat_editor(self):
        self.assertTrue(self.execute(OBSERVED_ROW).success)
        self.assertTrue(self.execute(FOCUS).success)
        self.assertFalse(self.actions.navigation.recipient_row_check)
        self.assertEqual(self.s.report["action_decisions"][-1]["frame_check"], "target_region")

    def test_model_cannot_enable_or_disable_row_guard(self):
        for name in ("recipient_row", "recipient_row_check"):
            self.assertFalse(self.execute({**OBSERVED_ROW, name: False}).success)
        self.delegate.execute.assert_not_called()

    def test_real_phone_agent_message_builder_receives_only_current_stage_and_exact_frame(self):
        from phone_agent.agent import PhoneAgent, AgentConfig
        model = PhoneAgent.__new__(PhoneAgent)  # No API client or device initialization.
        model.agent_config = AgentConfig(verbose=False)
        model.reset()
        frozen, sent = self.root / "model.png", []
        factory = Mock()
        factory.get_current_app.return_value = "Douyin"
        factory.get_screenshot.side_effect = lambda *_: SimpleNamespace(
            width=1080, height=2400, base64_data=base64.b64encode(frozen.read_bytes()).decode())
        responses = iter(['do(action="Tap",element=[250,600])',
                          'do(action="Tap",element=[400,935])',
                          'do(action="Tap",element=[900,935])'])
        def request(messages):
            sent.append(copy.deepcopy(messages))
            return SimpleNamespace(action=next(responses), thinking="offline test")
        model.model_client = SimpleNamespace(request=Mock(side_effect=request))
        with patch("phone_agent.agent.get_device_factory", return_value=factory), \
             patch("builtins.input", side_effect=["type 1", "yes", "send 示例联系人"]), \
             patch("run_douyin_test.countdown"), \
             patch("douyin_input.require_editor", return_value={"target": CONTEXT, "focused_editor": {"view_token": "a"}}):
            run_conversation(model, self.s, frozen, self.journal, self.root, 4, self.delegate)
        self.assertEqual(len(sent), 3)
        expected = ["LOCATE_RECIPIENT_IN_LIST", "LOCATE_EMPTY_CHAT_EDITOR", "LOCATE_SEND_BUTTON"]
        for messages, record, stage in zip(sent, self.s.report["model_requests"], expected):
            self.assertEqual([m["role"] for m in messages], ["system", "user"])
            self.assertEqual(record["substage"], stage)
            self.assertEqual(record["prompt_version"], PROMPT_VERSION)
            text = next(block["text"] for block in messages[1]["content"] if block["type"] == "text")
            self.assertIn(record["task"], text)
            data = next(block["image_url"]["url"] for block in messages[1]["content"] if block["type"] == "image_url")
            self.assertEqual(base64.b64decode(data.split(",", 1)[1]), frame())
        self.assertTrue(self.s.report["send_attempted"])
        self.assertEqual(self.delegate.execute.call_count, 3)
        self.s.shell.assert_called_once_with("input", "keyboard", "-d", 51, "text", "1")

    def test_flow_preview_never_opens_mac_app(self):
        with patch("run_douyin_test.subprocess.run") as run:
            DouyinSession.preview(self.s, frame(), SEND, 1)
        run.assert_not_called()

    def test_scope_requires_explicit_flow_and_full_safety_flags(self):
        flags = dict(send_one_flow=True, non_presentation=True, confirm_home_first=True,
                     auto_messages=True, message="1")
        require_comparison_scope(SimpleNamespace(**flags))
        for field, value in (("non_presentation", False), ("confirm_home_first", False),
                             ("auto_messages", False), ("message", "11"), ("preflight", True),
                             ("startup_only", True), ("step_by_step", True)):
            with self.assertRaises(RuntimeError):
                require_comparison_scope(SimpleNamespace(**{**flags, field: value}))

    def test_total_budget_blocks_further_requests_before_input(self):
        self.s.report["model_requests"] = [{"number": i} for i in range(2)]
        model = Mock()
        with self.assertRaises(RuntimeError):
            run_conversation(model, self.s, self.root / "model.png", self.journal, self.root, 2, self.delegate)
        model.step.assert_not_called()


if __name__ == "__main__":
    unittest.main()
