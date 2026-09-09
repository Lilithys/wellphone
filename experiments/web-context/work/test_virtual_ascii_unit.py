"""Offline tests only: synthetic dumps and mocked actions, never a real phone."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ascii_focus_guard import activity_target, focused_window, editor_evidence, client_dump_metadata
from test_virtual_ascii import AsciiProbe


ACTIVITIES = """Display #0 (activities from top to bottom):
 topResumedActivity=ActivityRecord{aaaa u0 com.hihonor.mms/.Compose t8}
Display #42 (activities from top to bottom):
 topResumedActivity=ActivityRecord{beef u0 com.android.settings/.HWSettings t9}
"""
WINDOWS = """  Display: mDisplayId=0 (organized)
  mCurrentFocus=Window{cafe u0 com.hihonor.mms/.Compose}
  Display: mDisplayId=42 (organized)
  mCurrentFocus=Window{face u0 com.android.settings/com.android.settings.HWSettings}
"""
CLIENT = """ACTIVITY com.android.settings/.HWSettings beef pid=123
  View Hierarchy:
    com.android.internal.policy.DecorView{aa V.E...... R....... 0,0-1080,2400}
      android.widget.SearchView$SearchAutoComplete{bb VFED..CL. .F...... 0,0-900,120 #123 android:id/search_src_text}
  PRIVATE-TEXT-MUST-NOT-APPEAR-IN-REPORT
"""
# Structural metadata from the read-only display 45 inspection, not UI text.
HONOR_CLIENT = """TASK 1000:com.android.settings id=183 userId=0 displayId=45(type=VIRTUAL)
  ACTIVITY com.android.settings/.HWSettings 27befce pid=10368 userId=0 uid=1000 displayId=45(type=VIRTUAL)
    View Hierarchy:
      com.hihonor.android.widget.SearchView$HwSearchAutoComplete{5f1c1d6 VFED..CL. .F...... android:id/search_src_text}
"""
GOOD = {"top_focused_display_id": 0, "ime_state": ["mCurTokenDisplayId=0", "mInputShown=true"]}


class FocusParserTests(unittest.TestCase):
    def setUp(self):
        self.target = activity_target(ACTIVITIES, 42)

    def test_target_is_scoped_to_secondary_and_normalized(self):
        self.assertEqual(self.target, {"display_id": 42, "activity_token": "beef",
                                      "component": "com.android.settings/com.android.settings.HWSettings"})

    def test_main_unknown_and_missing_display_rejected(self):
        for display_id in [0, -1, None, "42", 7]:
            with self.subTest(display_id=display_id), self.assertRaises(RuntimeError):
                activity_target(ACTIVITIES, display_id)

    def test_wrong_or_ambiguous_activity_rejected(self):
        for dump in [ACTIVITIES.replace("com.android.settings", "com.other"),
                     ACTIVITIES + ACTIVITIES, ACTIVITIES.replace("beef", "unknown"),
                     ACTIVITIES.replace("topResumedActivity=", "lastResumedActivity=")]:
            with self.subTest(dump=dump), self.assertRaises(RuntimeError):
                activity_target(dump, 42)

    def test_secondary_window_ignores_primary_focus(self):
        self.assertEqual(focused_window(WINDOWS, self.target)["window_token"], "face")

    def test_null_missing_wrong_and_ambiguous_secondary_window_rejected(self):
        for dump in [WINDOWS.split("  Display: mDisplayId=42")[0],
                     WINDOWS.replace("Window{face u0 com.android.settings/com.android.settings.HWSettings}", "null"),
                     WINDOWS.replace("com.android.settings.HWSettings", "com.android.settings.About"),
                     WINDOWS + WINDOWS]:
            with self.subTest(dump=dump), self.assertRaises(RuntimeError):
                focused_window(dump, self.target)

    def test_focused_editable_is_recognized_without_text(self):
        evidence = editor_evidence(CLIENT, self.target)
        self.assertEqual(evidence["status"], "focused_editor")
        self.assertEqual(evidence["focused_editor"]["resource_id"], "android:id/search_src_text")
        self.assertNotIn("PRIVATE-TEXT", str(evidence))

    def test_focusable_is_not_focused(self):
        self.assertEqual(editor_evidence(CLIENT.replace(".F......", "........"), self.target)["status"],
                         "editor_not_confirmed")

    def test_hidden_disabled_or_non_editable_is_not_accepted(self):
        for dump in [CLIENT.replace("VFED", "IFED"), CLIENT.replace("VFED", "VF.D"),
                     CLIENT.replace("SearchView$SearchAutoComplete", "TextView")]:
            with self.subTest(dump=dump):
                self.assertIsNone(editor_evidence(dump, self.target)["focused_editor"])

    def test_multiple_focused_editors_are_not_accepted(self):
        dump = CLIENT + "\n android.widget.EditText{cc VFED..CL. .F...... 0,0-100,100}\n"
        self.assertIsNone(editor_evidence(dump, self.target)["focused_editor"])

    def test_client_dump_must_match_exact_activity_and_hierarchy(self):
        for dump in [CLIENT.replace("beef", "aaaa"), CLIENT.replace("com.android.settings", "com.other"),
                     CLIENT + CLIENT, CLIENT.replace("View Hierarchy:", "Unknown:"),
                     CLIENT + "\n ACTIVITY unrecognized header\n"]:
            with self.subTest(dump=dump), self.assertRaises(RuntimeError):
                editor_evidence(dump, self.target)

    def test_client_header_diagnostic_records_structure_not_ui_text(self):
        dump = CLIENT.replace("pid=123", "pid=123 userId=0 uid=1000 displayId=42(type=VIRTUAL) private=SECRET")
        evidence = client_dump_metadata(dump)
        self.assertEqual(evidence["activity_header_count"], 1)
        self.assertEqual(evidence["activity_headers"][0]["displayId"], 42)
        self.assertEqual(evidence["activity_headers"][0]["known_suffix_fields"], ["userId", "uid", "displayId"])
        self.assertNotIn("PRIVATE-TEXT", str(evidence))
        self.assertNotIn("SECRET", str(evidence))
        self.assertNotIn("private", str(evidence))

    def test_client_error_diagnostic_does_not_retain_arbitrary_strings(self):
        evidence = client_dump_metadata("Unknown command: PRIVATE-TEXT\n"
                                        "Bad activity command, or no activities match: PRIVATE-TEXT\n")
        self.assertEqual(evidence["activity_header_count"], 0)
        self.assertEqual(evidence["error_categories"], ["no_activity_match", "unknown_command"])
        self.assertNotIn("PRIVATE-TEXT", str(evidence))

    def test_honor_verbose_header_is_accepted_and_display_checked(self):
        dump = CLIENT.replace("pid=123", "pid=10368 userId=0 uid=1000 displayId=42(type=VIRTUAL)")
        evidence = editor_evidence(dump, self.target)
        self.assertEqual(evidence["status"], "focused_editor")
        self.assertEqual(evidence["validated_client_header"]["display_id"], 42)
        self.assertEqual(evidence["validated_client_header"]["format"], "verbose")

    def test_honor_header_with_wrong_display_id_or_type_is_rejected(self):
        for suffix in ["displayId=0(type=INTERNAL)", "displayId=7(type=VIRTUAL)",
                       "displayId=-1(type=VIRTUAL)", "displayId=42(type=INTERNAL)"]:
            dump = CLIENT.replace("pid=123", "pid=10368 userId=0 uid=1000 " + suffix)
            with self.subTest(suffix=suffix), self.assertRaisesRegex(RuntimeError, "显示 ID/类型"):
                editor_evidence(dump, self.target)

    def test_unknown_partial_or_duplicate_header_suffix_is_not_ignored(self):
        for suffix in [" private=SECRET", " userId=0", " uid=1000 displayId=42(type=VIRTUAL)",
                       " userId=0 uid=1000 displayId=42(type=VIRTUAL) displayId=0",
                       " userId=0 uid=1000 displayId=42(type=VIRTUAL) private=SECRET"]:
            with self.subTest(suffix=suffix), self.assertRaises(RuntimeError):
                editor_evidence(CLIENT.replace("pid=123", "pid=123" + suffix), self.target)

    def test_verbose_header_does_not_relax_activity_identity_check(self):
        dump = CLIENT.replace("pid=123", "pid=10368 userId=0 uid=1000 displayId=42(type=VIRTUAL)")
        for bad in [dump.replace("beef", "aaaa"), dump.replace("com.android.settings", "com.other"),
                    dump + dump]:
            with self.subTest(dump=bad), self.assertRaises(RuntimeError):
                editor_evidence(bad, self.target)

    def test_non_running_or_main_target_is_not_accepted(self):
        for dump in [CLIENT.replace("pid=123", "pid=(not running)"), CLIENT.replace("pid=123", "pid=0")]:
            with self.subTest(dump=dump), self.assertRaises(RuntimeError):
                editor_evidence(dump, self.target)
        with self.assertRaises(RuntimeError):
            editor_evidence(CLIENT, {**self.target, "display_id": 0})

    def test_real_honor_header_and_editor_structural_fixture(self):
        target = {**self.target, "display_id": 45, "activity_token": "27befce"}
        evidence = editor_evidence(HONOR_CLIENT, target)
        self.assertEqual(evidence["status"], "focused_editor")
        self.assertEqual(evidence["focused_editor"]["view_token"], "5f1c1d6")
        self.assertEqual(evidence["focused_editor"]["flags"], ["VFED..CL.", ".F......"])

    def test_honor_class_requires_known_resource_id(self):
        target = {**self.target, "display_id": 45, "activity_token": "27befce"}
        for replacement in ["", "android:id/search_button", "app:id/search_src_text"]:
            with self.subTest(replacement=replacement):
                evidence = editor_evidence(HONOR_CLIENT.replace("android:id/search_src_text", replacement), target)
                self.assertIsNone(evidence["focused_editor"])

    def test_lookalike_oem_class_is_not_accepted(self):
        target = {**self.target, "display_id": 45, "activity_token": "27befce"}
        dump = HONOR_CLIENT.replace("com.hihonor.android.widget.SearchView", "com.unknown.SearchView")
        self.assertIsNone(editor_evidence(dump, target)["focused_editor"])

    def test_honor_editor_still_requires_visible_enabled_focused_flags(self):
        target = {**self.target, "display_id": 45, "activity_token": "27befce"}
        for dump in [HONOR_CLIENT.replace("VFED", "GFED"), HONOR_CLIENT.replace("VFED", "VF.D"),
                     HONOR_CLIENT.replace(".F......", "........")]:
            with self.subTest(dump=dump):
                self.assertIsNone(editor_evidence(dump, target)["focused_editor"])


class AsciiActionTests(unittest.TestCase):
    def subject(self):
        probe = AsciiProbe.__new__(AsciiProbe)
        probe.report = {"input_attempts": [], "input_verifications": [], "empty_editor_confirmed": True,
                        "stages": []}
        probe.display_id = 42
        probe.require_settings = Mock()
        probe.require_unchanged_main = Mock()
        probe.save = Mock()
        probe.shell = Mock(return_value="")
        probe.save_native = Mock()
        return probe

    def ready(self):
        probe = self.subject()
        probe.require_focused_editor = Mock()
        return probe

    def test_fixed_sequence_uses_display_target_and_never_clears(self):
        probe = self.ready()
        probe.send_fixed("7")
        with patch("builtins.input", return_value="yes"):
            probe.verify_input("7")
        probe.send_fixed("abc123")
        self.assertEqual([c.args for c in probe.shell.call_args_list],
                         [("input", "keyboard", "-d", 42, "text", "7"),
                          ("input", "keyboard", "-d", 42, "text", "abc123")])
        self.assertEqual(probe.require_focused_editor.call_count, 2)

    def test_unapproved_payload_or_skipped_first_step_sends_nothing(self):
        for text in ["abc123", "hello", "中文", "7\n", "", "7abc123"]:
            probe = self.ready()
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                probe.send_fixed(text)
            probe.shell.assert_not_called()

    def test_nonempty_or_unconfirmed_editor_sends_nothing(self):
        probe = self.ready()
        probe.report["empty_editor_confirmed"] = False
        with self.assertRaises(RuntimeError):
            probe.send_fixed("7")
        probe.shell.assert_not_called()

    def test_second_payload_requires_manual_confirmation(self):
        probe = self.ready()
        probe.send_fixed("7")
        with self.assertRaises(RuntimeError):
            probe.send_fixed("abc123")
        self.assertEqual(probe.shell.call_count, 1)

    def test_successful_payloads_cannot_be_sent_twice(self):
        probe = self.ready()
        probe.send_fixed("7")
        with self.assertRaises(RuntimeError):
            probe.send_fixed("7")
        with patch("builtins.input", return_value="yes"):
            probe.verify_input("7")
        probe.send_fixed("abc123")
        with self.assertRaises(RuntimeError):
            probe.send_fixed("abc123")
        self.assertEqual(probe.shell.call_count, 2)

    def test_primary_ime_change_prevents_target_inspection_and_input(self):
        probe = self.subject()
        probe.require_unchanged_main.side_effect = RuntimeError("IME moved")
        with self.assertRaises(RuntimeError):
            probe.send_fixed("7")
        probe.shell.assert_not_called()

    def test_anything_but_yes_does_not_pass_human_check(self):
        for response in ["", "1", "7", "否", "没看清", "yes but main also has 7"]:
            probe = self.ready()
            with self.subTest(response=response), patch("builtins.input", return_value=response):
                with self.assertRaises(RuntimeError):
                    probe.verify_input("7")
            self.assertFalse(probe.report["input_verifications"][0]["confirmed"])

    def test_input_is_not_retried_after_timeout(self):
        probe = self.ready()
        probe.shell.side_effect = subprocess.TimeoutExpired("input", 15)
        with self.assertRaises(subprocess.TimeoutExpired):
            probe.send_fixed("7")
        for payload in ["7", "abc123"]:
            with self.assertRaises(RuntimeError):
                probe.send_fixed(payload)
        self.assertEqual(probe.shell.call_count, 1)
        self.assertFalse(probe.report["input_attempts"][0]["command_returned"])

    def test_guard_failure_prevents_input_and_attempt_record(self):
        probe = self.ready()
        probe.require_focused_editor.side_effect = RuntimeError("unknown focus")
        with self.assertRaises(RuntimeError):
            probe.send_fixed("7")
        probe.shell.assert_not_called()
        self.assertEqual(probe.report["input_attempts"], [])

    def test_editor_dump_is_scoped_and_rechecked(self):
        probe = self.subject()
        probe.shell.side_effect = [ACTIVITIES, WINDOWS, CLIENT, ACTIVITIES, WINDOWS]
        evidence = probe.require_focused_editor()
        self.assertEqual(evidence["status"], "focused_editor")
        self.assertEqual(probe.shell.call_args_list[2].args,
                         ("dumpsys", "activity", "-c", "-p", "com.android.settings", "-d", 42, "beef"))
        self.assertEqual(probe.require_unchanged_main.call_count, 2)
        self.assertNotIn("PRIVATE-TEXT", str(probe.report))

    def test_main_display_never_reaches_client_dump_or_input(self):
        probe = self.subject()
        probe.display_id = 0
        probe.shell.return_value = ACTIVITIES
        with self.assertRaises(RuntimeError):
            probe.send_fixed("7")
        self.assertEqual([c.args for c in probe.shell.call_args_list], [("dumpsys", "activity", "activities")])

    def test_activity_change_during_check_aborts_input(self):
        probe = self.subject()
        probe.shell.side_effect = [ACTIVITIES, WINDOWS, CLIENT, ACTIVITIES.replace("beef", "fade"), WINDOWS]
        with self.assertRaisesRegex(RuntimeError, "窗口变化"):
            probe.send_fixed("7")
        self.assertTrue(all(c.args[0] == "dumpsys" for c in probe.shell.call_args_list))

    def test_unsupported_hierarchy_records_error_without_text_input(self):
        probe = self.subject()
        probe.shell.side_effect = [ACTIVITIES, WINDOWS, "No client dump"]
        with self.assertRaises(RuntimeError):
            probe.send_fixed("7")
        self.assertIn("error", probe.report["editor_checks"][-1])
        self.assertEqual(probe.report["editor_checks"][-1]["client_dump_metadata"]["activity_header_count"], 0)
        self.assertTrue(all(c.args[0] == "dumpsys" for c in probe.shell.call_args_list))

    def test_action_error_still_collects_human_observation(self):
        probe = self.subject()

        def run_stage(name, action):
            probe.report["stages"].append({"observation": "正常", "focus_trace": [GOOD]})
            action()
            probe.report["human_observed"] = True

        probe.stage = Mock(side_effect=run_stage)
        with self.assertRaisesRegex(RuntimeError, "unknown focus"):
            probe.checked_stage("D", Mock(side_effect=RuntimeError("unknown focus")))
        self.assertTrue(probe.report["human_observed"])
        self.assertEqual(probe.report["stages"][-1]["action_error"], "unknown focus")

    def test_input_stage_without_new_frame_cannot_pass(self):
        probe = self.subject()

        def run_stage(name, action):
            probe.report["stages"].append({"observation": "正常", "focus_trace": [GOOD]})
            action()
            probe.report["frame_observations"] = [{"capture_available": False}]

        probe.stage = Mock(side_effect=run_stage)
        with self.assertRaisesRegex(RuntimeError, "新帧证据不完整"):
            probe.checked_stage("D", Mock())

    def test_editor_click_requires_confirmed_page_and_correct_display(self):
        probe = self.subject()
        probe.read_target = Mock()
        with self.assertRaises(RuntimeError):
            probe.focus_search_editor()
        probe.shell.assert_not_called()
        probe.report["search_page_confirmed"] = True
        probe.focus_search_editor()
        probe.shell.assert_called_once_with("input", "-d", 42, "tap", 540, 75)

    def test_failure_cleanup_stops_owned_display_and_decoder(self):
        probe = self.subject()
        probe.prepare = Mock()
        probe.read_default_ime = Mock(return_value="test.ime/Service")
        probe.perform_stages = Mock(side_effect=RuntimeError("probe failure"))
        probe.stop_display = Mock(side_effect=lambda: probe.report.update(virtual_display_removed=True))
        probe.frames = Mock()
        probe.serial, probe.scrcpy_log = "unit-phone", []
        probe.snapshot = Mock(return_value=GOOD)
        with tempfile.TemporaryDirectory() as directory:
            probe.output = Path(directory)
            self.assertEqual(probe.run_probe(), 1)
        probe.stop_display.assert_called_once()
        probe.frames.stop.assert_called_once()
        self.assertNotIn("completed", probe.report)


if __name__ == "__main__":
    unittest.main()
