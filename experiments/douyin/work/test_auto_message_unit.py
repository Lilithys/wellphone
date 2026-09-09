"""New flow tests: fake device, fake vision, temp ledgers, no API/phone access."""
import contextlib
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from auto_message_policy import (TaskJournal, decode_object, validate_delivery,
                                 validate_observation, same_candidate_point)
from run_douyin_auto import AutoFlow, main
from test_douyin_unit import frame, CONTEXT

TASK = SimpleNamespace(recipient="测试联系人", message="1")


def observation(stage, **change):
    page, action, point = {
        "START": ("home", "open_messages", [696, 965]),
        "FIND": ("messages", "open_recipient", [300, 500]),
        "CHAT": ("chat", "focus", [400, 960]),
        "EMPTY": ("chat", "input", None),
        "DRAFT": ("chat", "send", [900, 950]),
    }[stage]
    return {"page": page, "blocked": False, "recipient": TASK.recipient,
            "group": False, "ambiguous": False, "draft": "1" if stage == "DRAFT" else "",
            "target_count": 1, "next": action, "point": point, **change}


def delivery(**change):
    return {"recipient": TASK.recipient, "group": False, "ambiguous": False, "draft": "",
            "new_outgoing": True, "outgoing_text": "1", "send_state": "sent", **change}


class ProtocolTests(unittest.TestCase):
    def test_small_model_rounding_does_not_rewrite_or_accept_different_button(self):
        point = [900, 950]
        self.assertTrue(same_candidate_point(point, [901, 949]))
        self.assertFalse(same_candidate_point(point, [920, 950]))
        self.assertEqual(point, [900, 950])

    def test_json_only_no_ambiguous_duplicate_or_multiple_response(self):
        for raw in ('{"x":1,"x":2}', '{} {}', 'do(action="Tap")', '[]', '{"x":NaN}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, json.JSONDecodeError)):
                decode_object(raw)
        for raw in ('{"x":1}', '```json\n{"x":1}\n```', '<think>brief</think><answer>{"x":1}</answer>'):
            self.assertEqual(decode_object(raw), {"x": 1})

    def test_each_stage_accepts_only_expected_action(self):
        for stage in ("START", "FIND", "CHAT", "EMPTY", "DRAFT"):
            value = observation(stage)
            self.assertEqual(validate_observation(value, stage, TASK), value)
            with self.assertRaises(ValueError):
                validate_observation({**value, "next": "delete"}, stage, TASK)

    def test_recipient_group_ambiguity_and_nonempty_draft_stop(self):
        for changed in ({"recipient": "其他联系人"}, {"group": True}, {"ambiguous": True},
                        {"blocked": True}, {"target_count": 2}, {"draft": "old"}, {"draft": None}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_observation(observation("EMPTY", **changed), "EMPTY", TASK)

    def test_send_requires_exact_text_and_real_bounded_button(self):
        for changed in ({"draft": "11"}, {"draft": "1 "}, {"point": [813, 69]},
                        {"point": [True, 950]}, {"point": [900.5, 950]}, {"next": "focus"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_observation(observation("DRAFT", **changed), "DRAFT", TASK)

    def test_historical_pending_failed_messages_are_not_new_delivery(self):
        validate_delivery(delivery(), TASK)
        for change in ({"new_outgoing": False}, {"send_state": "pending"}, {"send_state": "failed"},
                       {"recipient": "other"}, {"draft": "1"}, {"outgoing_text": "11"}, {"new_outgoing": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_delivery(delivery(**change), TASK)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_input_reservation_blocks_rerun_and_new_id(self):
        one = TaskJournal(self.root, "TEST", TASK)
        one.reserve_input()
        for other in (TaskJournal(self.root, "TEST", TASK), TaskJournal(self.root, "TEST", TASK, "new-intent")):
            with self.assertRaises(RuntimeError):
                other.require_unused()
        with self.assertRaises(RuntimeError):
            one.confirmed()

    def test_send_is_exclusive_and_confirmed_task_remains_deduplicated(self):
        one = TaskJournal(self.root, "TEST", TASK)
        one.reserve_input()
        one.reserve_send()
        with self.assertRaises(FileExistsError):
            one.reserve_send()
        one.confirmed()
        self.assertTrue(one.path.exists())
        self.assertFalse(one.pending.exists())
        with self.assertRaises(RuntimeError):
            TaskJournal(self.root, "TEST", TASK).require_unused()
        TaskJournal(self.root, "TEST", TASK, "explicit-new-task").require_unused()

    def test_pending_owner_cannot_be_released_or_sent_by_another_task(self):
        first = TaskJournal(self.root, "TEST", TASK)
        second = TaskJournal(self.root, "TEST", TASK, "different")
        first.reserve_input()
        second._exclusive(second.path, {"status": "INPUT_RESERVED"})
        with self.assertRaises(RuntimeError):
            second.reserve_send()
        self.assertTrue(first.pending.exists())


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = TaskJournal(self.root, "TEST", TASK)
        self.editor = {"target": {"display_id": 51}, "focused_editor": {"token": "bb"}}
        self.s = SimpleNamespace(output=self.root, display_id=51, report={},
            last_capture_context=CONTEXT.copy(), context=Mock(return_value=CONTEXT.copy()),
            capture=Mock(return_value=frame()), save=Mock(), shell=Mock(return_value=""))
        self.responses = {"VERIFY": delivery()}
        self.vision = Mock()
        self.vision.budget, self.vision.calls = 30, 0
        self.vision.request.side_effect = lambda stage, *_: self.responses.get(stage, observation(stage) if stage != "VERIFY" else delivery())
        self.flow = AutoFlow(self.s, TASK, self.vision, self.journal)
        self.focus = patch("run_douyin_auto.require_editor", return_value=self.editor).start()
        self.addCleanup(patch.stopall)

    def test_complete_fake_flow_no_human_prompts_and_exactly_one_type_send(self):
        def shell(*args):
            if args[:2] == ("input", "keyboard"):
                self.assertTrue(self.journal.path.exists())
                self.assertTrue(self.journal.pending.exists())
            if args == ("input", "-d", 51, "tap", 972, 2280):
                self.assertTrue(self.journal.path.with_name(self.journal.path.stem + "-send.json").exists())
            return ""
        self.s.shell.side_effect = shell
        with patch("builtins.input", side_effect=AssertionError("Unexpected human prompt")), contextlib.redirect_stdout(io.StringIO()):
            self.flow.run()
        calls = [c.args for c in self.s.shell.call_args_list]
        self.assertEqual(calls.count(("input", "keyboard", "-d", 51, "text", "1")), 1)
        self.assertEqual(calls.count(("input", "-d", 51, "tap", 972, 2280)), 1)
        self.assertEqual(self.flow.stage, "DONE")
        self.assertEqual(self.s.report["send_status"], "MODEL_CONFIRMED_NEW_OUTGOING")
        self.assertFalse(self.s.report["recipient_delivery_verified"])

    def test_incomplete_model_identity_cannot_input(self):
        self.responses["EMPTY"] = observation("EMPTY", recipient="other")
        with self.assertRaises(ValueError):
            self.flow.input_message()
        self.s.shell.assert_not_called()
        self.assertFalse(self.journal.path.exists())

    def test_insufficient_verification_budget_stops_before_any_input_or_send(self):
        self.vision.budget, self.vision.calls = 8, 4
        with self.assertRaisesRegex(RuntimeError, "预算不足"):
            self.flow.input_message()
        self.s.shell.assert_not_called()
        self.assertFalse(self.journal.path.exists())
        self.vision.calls = 6
        with self.assertRaisesRegex(RuntimeError, "预算不足"):
            self.flow.send_and_verify()
        self.s.shell.assert_not_called()

    def test_input_transport_timeout_retains_journal_never_retries(self):
        self.s.shell.side_effect = RuntimeError("timeout")
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.flow.input_message()
        self.assertEqual(self.s.shell.call_count, 1)
        self.assertTrue(self.journal.pending.exists())
        with self.assertRaises(RuntimeError):
            self.flow.input_message()
        self.assertEqual(self.s.shell.call_count, 1)

    def test_wrong_draft_and_editor_change_never_send_or_type(self):
        self.responses["DRAFT"] = observation("DRAFT", draft="11")
        with self.assertRaises(ValueError):
            self.flow.send_and_verify()
        self.s.shell.assert_not_called()
        self.focus.side_effect = [self.editor, {**self.editor, "focused_editor": {"token": "changed"}}]
        with self.assertRaisesRegex(RuntimeError, "归属变化"):
            self.flow.input_message()
        self.s.shell.assert_not_called()

    def test_send_timeout_is_reserved_before_dispatch_and_not_retried(self):
        self.journal.reserve_input()
        self.s.shell.side_effect = RuntimeError("timeout")
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.flow.send_and_verify()
        self.assertTrue(self.s.report["send_attempted"])
        self.assertTrue(self.journal.pending.exists())
        self.assertEqual(self.s.shell.call_count, 1)
        with self.assertRaises(RuntimeError):
            self.flow.send_and_verify()
        self.assertEqual(self.s.shell.call_count, 1)

    def test_old_outgoing_does_not_release_pending_ledger(self):
        self.journal.reserve_input()
        self.responses["VERIFY"] = delivery(new_outgoing=False)
        with self.assertRaises(ValueError):
            self.flow.send_and_verify()
        self.assertEqual(self.s.shell.call_count, 1)
        self.assertTrue(self.journal.pending.exists())
        self.assertNotIn("message_verified_by_model", self.s.report)

    def test_monitor_failure_prevents_dispatch(self):
        self.s.context.side_effect = RuntimeError("primary changed")
        with self.assertRaises(RuntimeError):
            self.flow.run()
        self.s.shell.assert_not_called()

    def test_unicode_or_missing_key_fails_before_device_initialization(self):
        with patch("run_douyin_auto.AutoSession") as session, patch.dict("os.environ", {}, clear=True):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--task", '抖音给“测试联系人”发送“你好”'])
                with self.assertRaises(SystemExit):
                    main(["--task", '抖音给“测试联系人”发送“1”'])
            session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
