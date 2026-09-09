"""No phone/API calls: operator proposal grammar, bounded probe and provenance."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from douyin_executor_probe import read_proposal, require_probe_scope, run_executor_probe
from phone_agent.actions.handler import ActionResult
from test_douyin_unit import CONTEXT, frame


def args():
    return SimpleNamespace(executor_test=True, send_one_flow=True, confirm_home_first=True,
        non_presentation=True, auto_messages=True, low_fps_trial=True, message="1",
        preflight=False, startup_only=False, step_by_step=False)


class ExecutorProbeTests(TestCase):
    def test_only_exact_tap_swipe_integer_proposals(self):
        for raw in ('{"action":"Tap","element":[696,965]}',
                    '{"action":"Swipe","start":[300,700],"end":[300,300]}'):
            with patch("builtins.input", return_value=raw):
                self.assertEqual(read_proposal()["_metadata"], "do")

    def test_no_type_extra_fields_duplicate_keys_code_or_invalid_coordinates(self):
        for raw in ('{"action":"Type","text":"1"}', '{"action":"Back"}',
                    '{"action":"Tap","element":[696,965],"message":"SEND_ONE"}',
                    '{"action":"Tap","action":"Tap","element":[696,965]}',
                    '{"action":"Tap","element":[true,965]}',
                    '{"action":"Tap","element":[0,1000]}',
                    '{"action":"Tap","element":[0.0,965]}',
                    '{"action":[],"element":[696,965]}',
                    'do(action="Tap",element=[696,965])', 'q', '{}', '[]', '1', 'x'*201):
            with self.subTest(raw=raw), patch("builtins.input", return_value=raw), self.assertRaises(RuntimeError):
                read_proposal()

    def test_explicit_scope_is_required(self):
        require_probe_scope(args())
        for name in ("send_one_flow", "confirm_home_first", "non_presentation", "auto_messages", "low_fps_trial"):
            option = args()
            setattr(option, name, False)
            with self.assertRaises(RuntimeError):
                require_probe_scope(option)
        for name in ("preflight", "startup_only", "step_by_step"):
            option = args()
            setattr(option, name, True)
            with self.assertRaises(RuntimeError):
                require_probe_scope(option)

    def test_probe_stops_before_any_action_without_same_session_home(self):
        s = SimpleNamespace(args=args(), report={}, display_id=51)
        with patch("douyin_executor_probe.StartupActions") as startup, self.assertRaises(RuntimeError):
            run_executor_probe(s, Mock(), Path("/unused"), Mock(), 10)
        startup.assert_not_called()
        self.assertFalse(s.report["model_called"])
        self.assertFalse(s.report["autoglm_end_to_end_verified"])

    def test_probe_startup_failure_does_not_replay_or_enter_conversation(self):
        s = SimpleNamespace(args=args(), display_id=51, output=Path("/unused"), save=Mock(),
            context=Mock(return_value=CONTEXT), capture=Mock(return_value=frame()),
            last_capture_context=CONTEXT, report={"home_gate": {"status":"READY_FOR_MODEL", "display_id":51, "context":CONTEXT}})
        startup = Mock()
        startup.execute.return_value = ActionResult(False, True, "guard refused")
        with patch("douyin_executor_probe.StartupActions", return_value=startup), \
             patch("douyin_executor_probe.ConversationActions") as conversation, \
             patch("builtins.input", return_value='{"action":"Tap","element":[696,965]}'), \
             self.assertRaisesRegex(RuntimeError, "guard refused"):
            run_executor_probe(s, Mock(), Path("/unused"), Mock(), 10)
        startup.execute.assert_called_once()
        conversation.assert_not_called()
        self.assertFalse(s.report["model_called"])
