"""Offline policy and adapter regression tests; no phone or network."""
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image

sys.path.insert(0, os.environ.get("WELLPHONE_PROJECT_DIR", "/Users/yishanma/Desktop/wellphone"))
from phone_agent.virtual_display import device
from phone_agent.actions.handler import ActionResult
from run_autoglm_focus import GuardedActions, execute, normalize_component, validate_action


class PolicyTests(unittest.TestCase):
    packages = {"Settings": "com.android.settings", "WeChat": "com.tencent.mm"}

    def test_settings_launch_allowed(self):
        validate_action({"_metadata": "do", "action": "Launch", "app": "Settings"}, self.packages)

    def test_other_app_launch_blocked(self):
        with self.assertRaises(RuntimeError):
            validate_action({"_metadata": "do", "action": "Launch", "app": "WeChat"}, self.packages)

    def test_unvalidated_actions_blocked(self):
        for action in ["Type", "Type_Name", "Home", "Double Tap", "Long Press", "Call_API", "Take_over"]:
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                validate_action({"_metadata": "do", "action": action}, self.packages)

    def test_bad_coordinates_blocked(self):
        for point in [[-1, 10], [1000, 0], [float("nan"), 10], [10], "10,20", [True, 20]]:
            with self.subTest(point=point), self.assertRaises(RuntimeError):
                validate_action({"_metadata": "do", "action": "Tap", "element": point}, self.packages)

    def test_bounded_wait(self):
        validate_action({"_metadata": "do", "action": "Wait", "duration": "1 seconds"}, self.packages)
        for duration in ["infinity", "1000 seconds", "-1", "nan"]:
            with self.subTest(duration=duration), self.assertRaises(RuntimeError):
                validate_action({"_metadata": "do", "action": "Wait", "duration": duration}, self.packages)

    def test_component_normalization(self):
        self.assertEqual(normalize_component("com.android.settings/.Settings$DeviceInfoSettingsActivity"),
                         "com.android.settings/com.android.settings.Settings$DeviceInfoSettingsActivity")

    def test_claiming_finish_on_wrong_page_fails(self):
        session = SimpleNamespace(assert_task_scope=Mock(return_value="com.android.settings/.HWSettings"),
                                  goal_component="com.android.settings/com.android.settings.Settings$DeviceInfoSettingsActivity")
        delegate = Mock()
        result = GuardedActions(delegate, session).execute({"_metadata": "finish", "message": "done"}, 1080, 2400)
        self.assertFalse(result.success)
        self.assertTrue(result.should_finish)
        delegate.execute.assert_not_called()

    def test_finish_on_verified_page_is_delegated(self):
        component = "com.android.settings/com.android.settings.Settings$DeviceInfoSettingsActivity"
        session = SimpleNamespace(assert_task_scope=Mock(return_value=component), goal_component=component)
        delegate = Mock()
        delegate.execute.return_value = ActionResult(True, True)
        result = GuardedActions(delegate, session).execute({"_metadata": "finish"}, 1080, 2400)
        self.assertTrue(result.success)

    def test_scope_failure_stops_before_action(self):
        session = SimpleNamespace(assert_task_scope=Mock(side_effect=RuntimeError("focus lost")))
        delegate = Mock()
        result = GuardedActions(delegate, session).execute({"_metadata": "do", "action": "Back"}, 1080, 2400)
        self.assertFalse(result.success)
        delegate.execute.assert_not_called()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        device.configure(30, window_pid=1234, device_id="unit-phone")

    def tearDown(self):
        device.configure(30)

    def test_primary_display_rejected(self):
        with self.assertRaises(ValueError):
            device.configure(0)

    def test_device_binding(self):
        self.assertEqual(device._adb_prefix(None), ["adb", "-s", "unit-phone"])
        with self.assertRaises(RuntimeError):
            device._adb_prefix("other-phone")

    def test_adb_shell_quotes_remote_metacharacters(self):
        with patch.object(device.subprocess, "run") as run:
            device._adb_shell(None, "am", "start", "-n", "com.android.settings/.Settings$DeviceInfoSettingsActivity")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["adb", "-s", "unit-phone", "shell"])
        self.assertIn("'com.android.settings/.Settings$DeviceInfoSettingsActivity'", command[-1])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_live_scrcpy_display_required(self):
        for dump in ["mDisplayId=30", 'DisplayInfo{"other", displayId 30, type VIRTUAL}',
                     'DisplayInfo{"scrcpy", displayId 31, type VIRTUAL}']:
            with patch.object(device.os, "kill"), patch.object(device, "_adb_shell", return_value=SimpleNamespace(stdout=dump)):
                with self.assertRaises(RuntimeError):
                    device._assert_display_exists(None)

    def test_expected_display_accepted(self):
        with patch.object(device.os, "kill"), patch.object(device, "_adb_shell", return_value=SimpleNamespace(
            stdout='DisplayInfo{"scrcpy", displayId 30, type VIRTUAL}')):
            device._assert_display_exists(None)

    def test_dead_scrcpy_rejected_before_adb(self):
        with patch.object(device.os, "kill", side_effect=ProcessLookupError), patch.object(device, "_adb_shell") as shell:
            with self.assertRaises(RuntimeError):
                device._assert_display_exists(None)
            shell.assert_not_called()

    def test_text_editing_sends_nothing(self):
        with patch.object(device, "_adb_shell") as shell:
            with self.assertRaises(RuntimeError):
                device.type_text("abc", "unit-phone")
            with self.assertRaises(RuntimeError):
                device.clear_text("unit-phone")
            shell.assert_not_called()

    def test_tap_and_back_target_virtual_display(self):
        with patch.object(device, "_assert_display_exists"), patch.object(device, "_adb_shell") as shell:
            device.tap(10, 20, "unit-phone", delay=0)
            device.back("unit-phone", delay=0)
        self.assertEqual(shell.call_args_list[0].args, ("unit-phone", "input", "-d", 30, "tap", 10, 20))
        self.assertEqual(shell.call_args_list[1].args, ("unit-phone", "input", "-d", 30, "keyevent", "KEYCODE_BACK"))

    def test_native_png_is_not_resized_or_captured_from_mac(self):
        with tempfile.TemporaryDirectory(prefix="wellphone-unit-") as directory:
            frame = Path(directory) / "frame.png"
            Image.new("RGB", (1080, 2400), "blue").save(frame)
            device.configure(30, frame_path=str(frame), frame_producer_pid=777)
            with patch.object(device, "_assert_display_exists"), patch.object(device.os, "kill"), \
                 patch.object(device, "_find_window_id") as mac:
                result = device.get_screenshot()
            self.assertEqual(base64.b64decode(result.base64_data), frame.read_bytes())
            mac.assert_not_called()

    def test_native_thumbnail_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="wellphone-unit-") as directory:
            frame = Path(directory) / "frame.png"
            Image.new("RGB", (92, 300), "blue").save(frame)
            device.configure(30, frame_path=str(frame), frame_producer_pid=777)
            with patch.object(device, "_assert_display_exists"), patch.object(device.os, "kill"):
                with self.assertRaises(RuntimeError):
                    device.get_screenshot()

    def test_dead_native_producer_has_no_mac_fallback(self):
        device.configure(30, frame_path="/private/tmp/nonexistent-wellphone-unit.png", frame_producer_pid=777)
        with patch.object(device, "_assert_display_exists"), \
             patch.object(device.os, "kill", side_effect=ProcessLookupError), \
             patch.object(device, "_find_window_id") as mac:
            with self.assertRaises(RuntimeError):
                device.get_screenshot()
            mac.assert_not_called()


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.frames_patch = patch("run_autoglm_focus.FrameStream")
        self.frames = self.frames_patch.start().return_value
        self.frames.process.pid = 777
        self.frames.frame_path = Path("/private/tmp/wellphone-unit-frame.png")
        self.frames.pipe_path = Path("/private/tmp/wellphone-unit-video.pipe")

    def tearDown(self):
        self.frames_patch.stop()

    def session(self, output):
        return SimpleNamespace(
            prepare=Mock(), report={}, serial="unit-phone", process=SimpleNamespace(pid=1234),
            display_id=30, output=Path(output), scrcpy_log=[], start_display=Mock(),
            launch_settings=Mock(), stop_display=Mock(), save=Mock(),
            open_info=Mock(), press_back=Mock(), goal_component="com.android.settings/com.android.settings.About",
            assert_task_scope=Mock(return_value="com.android.settings/.About"),
            snapshot=Mock(return_value={"top_focused_display_id": 0}),
            read_focus=Mock(return_value={"top_focused_display_id": 0, "ime_state": []}),
        )

    def test_preflight_success_never_constructs_model(self):
        with tempfile.TemporaryDirectory(prefix="wellphone-unit-") as output:
            session = self.session(output)
            with patch("run_autoglm_focus.AutoGLMSession", return_value=session), \
                 patch("run_autoglm_focus.save_frame", return_value="frame.png"), \
                 patch("phone_agent.device_factory.set_virtual_display") as configure, \
                 patch("phone_agent.agent.PhoneAgent") as model:
                code = execute(SimpleNamespace(preflight=True, window_title="test", max_steps=8))
            self.assertEqual(code, 0)
            self.assertTrue(session.report["preflight_passed"])
            self.assertNotIn("task_verified", session.report)
            model.assert_not_called()
            configure.assert_called_once_with(30, window_title="test", window_pid=1234, device_id="unit-phone",
                                              frame_path=str(self.frames.frame_path), frame_producer_pid=777)
            session.stop_display.assert_called_once()

    def test_capture_failure_cleans_up_without_model(self):
        with tempfile.TemporaryDirectory(prefix="wellphone-unit-") as output:
            session = self.session(output)
            with patch("run_autoglm_focus.AutoGLMSession", return_value=session), \
                 patch("run_autoglm_focus.save_frame", side_effect=RuntimeError("capture unavailable")), \
                 patch("phone_agent.device_factory.set_virtual_display"), \
                 patch("phone_agent.agent.PhoneAgent") as model:
                code = execute(SimpleNamespace(preflight=True, window_title="test", max_steps=8))
            self.assertEqual(code, 1)
            self.assertNotIn("completed", session.report)
            model.assert_not_called()
            session.stop_display.assert_called_once()

    def test_missing_key_noninteractive_never_starts_display(self):
        with tempfile.TemporaryDirectory(prefix="wellphone-unit-") as output:
            session = self.session(output)
            with patch("run_autoglm_focus.AutoGLMSession", return_value=session), \
                 patch.dict(os.environ, {"PHONE_AGENT_API_KEY": ""}), \
                 patch("run_autoglm_focus.sys.stdin.isatty", return_value=False):
                code = execute(SimpleNamespace(preflight=False, window_title="test", max_steps=8))
            self.assertEqual(code, 1)
            session.start_display.assert_not_called()
            self.assertIn("API key", session.report["error"])

    def test_cleanup_failure_cannot_report_pass(self):
        with tempfile.TemporaryDirectory(prefix="wellphone-unit-") as output:
            session = self.session(output)
            session.stop_display.side_effect = RuntimeError("display still exists")
            with patch("run_autoglm_focus.AutoGLMSession", return_value=session), \
                 patch("run_autoglm_focus.save_frame", return_value="frame.png"), \
                 patch("phone_agent.device_factory.set_virtual_display"):
                code = execute(SimpleNamespace(preflight=True, window_title="test", max_steps=8))
            self.assertEqual(code, 1)
            self.assertIn("cleanup_error", session.report)


if __name__ == "__main__":
    unittest.main()
