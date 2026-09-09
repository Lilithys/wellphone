"""Offline fail-closed checks for keyboard-request experiment."""
import unittest
from unittest.mock import Mock, patch

from test_virtual_keyboard import KeyboardProbe, require_primary_keyboard


GOOD = {"top_focused_display_id": 0, "ime_state": ["mCurTokenDisplayId=0", "mInputShown=true"]}


class KeyboardRequestTests(unittest.TestCase):
    def test_good_primary_keyboard(self):
        require_primary_keyboard(GOOD)

    def test_unknown_or_moved_focus_is_not_success(self):
        for state in [{}, {"error": "ADB disconnected"}, {**GOOD, "top_focused_display_id": 2},
                      {**GOOD, "ime_state": ["mCurTokenDisplayId=0", "mInputShown=false"]},
                      {**GOOD, "ime_state": ["mCurTokenDisplayId=2", "mInputShown=true"]}]:
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                require_primary_keyboard(state)

    def subject(self):
        probe = KeyboardProbe.__new__(KeyboardProbe)
        probe.report = {"stages": [{"observation": "正常", "focus_trace": [GOOD]}]}
        probe.stage = Mock()
        probe.require_unchanged_main = Mock()
        return probe

    def test_abnormal_human_observation_stops_even_when_focus_is_ok(self):
        probe = self.subject()
        probe.report["stages"][0]["observation"] = "白屏"
        with self.assertRaises(RuntimeError):
            probe.checked_stage("test", Mock())

    def test_abnormal_sample_stops_even_when_human_says_ok(self):
        probe = self.subject()
        probe.report["stages"][0]["focus_trace"].append({**GOOD, "top_focused_display_id": 4})
        with self.assertRaises(RuntimeError):
            probe.checked_stage("test", Mock())

    def test_missing_samples_cannot_pass(self):
        probe = self.subject()
        probe.report["stages"][0]["focus_trace"] = []
        with self.assertRaises(RuntimeError):
            probe.checked_stage("test", Mock())

    def test_missing_capture_still_collects_secondary_observations(self):
        probe = self.subject()
        probe.save = Mock()
        probe.stage.side_effect = lambda *args: probe.report.update(
            frame_observations=[{"capture_available": False, "status": "no_fresh_frame"}])
        with patch("builtins.input", side_effect=["是", "不显示"]) as ask:
            with self.assertRaisesRegex(RuntimeError, "新帧证据不完整"):
                probe.checked_stage("B", Mock(), inspect_secondary=True)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(probe.report["secondary_editor_observation"], "是")
        self.assertEqual(probe.report["secondary_keyboard_observation"], "不显示")

    def test_continuously_redrawn_valid_capture_does_not_stop_stage(self):
        probe = self.subject()
        probe.stage.side_effect = lambda *args: probe.report.update(
            frame_observations=[{"capture_available": True, "observed_file_updates": 15,
                                 "observed_content_variants": 1, "write_quiet_at_end_ms": 0}])
        probe.checked_stage("A", Mock())
        probe.require_unchanged_main.assert_called_once()

    def test_click_is_targeted_and_never_types(self):
        probe = self.subject()
        probe.require_settings = Mock()
        probe.serial, probe.display_id = "unit-phone", 8
        probe.settings_component = "com.android.settings/.HWSettings"
        probe.shell = Mock(return_value="Display #8 (activities from top to bottom):\n"
                           " topResumedActivity=ActivityRecord{x u0 com.android.settings/.HWSettings t1}")
        probe.save_native = Mock()
        from unittest.mock import patch
        with patch("test_virtual_keyboard.time.sleep"):
            probe.click_search()
        self.assertEqual(probe.shell.call_args.args, ("input", "-d", 8, "tap", 540, 410))
        self.assertEqual(probe.shell.call_count, 2)

    def test_wrong_settings_page_is_not_clicked(self):
        probe = self.subject()
        probe.require_settings = Mock()
        probe.serial, probe.display_id = "unit-phone", 8
        probe.settings_component = "com.android.settings/.HWSettings"
        probe.shell = Mock(return_value="Display #8 (activities from top to bottom):\n"
                           " topResumedActivity=ActivityRecord{x u0 com.android.settings/.About t1}")
        with self.assertRaises(RuntimeError):
            probe.click_search()
        self.assertEqual(probe.shell.call_count, 1)


if __name__ == "__main__":
    unittest.main()
