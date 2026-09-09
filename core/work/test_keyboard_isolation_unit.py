"""Offline checks for the diagnostic runner. Never connects to a phone."""
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_keyboard_isolation import KeyboardTest, activity_on_display, focus_state


class DiagnosticsTests(unittest.TestCase):
    def subject(self, display=29, verified=True):
        obj = KeyboardTest.__new__(KeyboardTest)
        obj.display_id = display
        obj.serial = "unit-device"
        obj.args = SimpleNamespace(require_focus_flags=True)
        obj.focus_flags_verified = threading.Event()
        if verified:
            obj.focus_flags_verified.set()
        obj.process = Mock()
        obj.process.poll.return_value = None
        obj.shell = Mock(return_value='DisplayInfo{"scrcpy", displayId 29, type VIRTUAL}')
        return obj

    def test_focus_state_keeps_only_whitelist(self):
        result = focus_state("mCurTokenDisplayId=0\nmInputShown=true\n"
                             "mCurAttribute=PRIVATE_EDITOR_DATA\ntext=PRIVATE_TYPED_TEXT",
                             "mTopFocusedDisplayId=0\nPRIVATE_WINDOW_TITLE")
        self.assertEqual(result["top_focused_display_id"], 0)
        self.assertEqual(result["ime_state"], ["mCurTokenDisplayId=0", "mInputShown=true"])
        self.assertNotIn("PRIVATE", str(result))

    def test_missing_top_focus_is_unknown(self):
        self.assertIsNone(focus_state("", "")["top_focused_display_id"])

    def test_activity_is_scoped_by_display(self):
        dump = ("Display #0 (activities from top to bottom):\n"
                " topResumedActivity=ActivityRecord{x u0 example.chat/.Editor t1}\n"
                "Display #29 (activities from top to bottom):\n"
                " topResumedActivity=ActivityRecord{y u0 com.android.settings/.Settings$DeviceInfoSettingsActivity t2}\n")
        self.assertEqual(activity_on_display(dump, 0), "example.chat/.Editor")
        self.assertTrue(activity_on_display(dump, 29).endswith("$DeviceInfoSettingsActivity"))
        self.assertEqual(activity_on_display(dump, 30), "unknown")

    def test_guard_refuses_primary_display_before_adb(self):
        obj = self.subject(display=0)
        with self.assertRaises(RuntimeError):
            obj.guard()
        obj.shell.assert_not_called()

    def test_guard_refuses_unverified_server(self):
        obj = self.subject(verified=False)
        with self.assertRaises(RuntimeError):
            obj.guard()
        obj.shell.assert_not_called()

    def test_guard_refuses_dead_server(self):
        obj = self.subject()
        obj.process.poll.return_value = 1
        with self.assertRaises(RuntimeError):
            obj.guard()

    def test_guard_refuses_other_display(self):
        obj = self.subject()
        obj.shell.return_value = 'DisplayInfo{"scrcpy", displayId 30, type VIRTUAL}'
        with self.assertRaises(RuntimeError):
            obj.guard()

    def test_guard_accepts_owned_live_virtual_display(self):
        self.subject().guard()

    def test_back_is_display_targeted(self):
        obj = self.subject()
        obj.require_settings = Mock()
        obj.press_back()
        obj.shell.assert_called_once_with("input", "-d", 29, "keyevent", "KEYCODE_BACK")

    def test_shell_quotes_dollar_in_activity(self):
        obj = self.subject()
        with patch("test_keyboard_isolation.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="ok")
            KeyboardTest.shell(obj, "am", "start", "-n", "com.android.settings/.Settings$DeviceInfoSettingsActivity")
        remote_command = run.call_args.args[0][-1]
        self.assertIn("'com.android.settings/.Settings$DeviceInfoSettingsActivity'", remote_command)


if __name__ == "__main__":
    unittest.main()
