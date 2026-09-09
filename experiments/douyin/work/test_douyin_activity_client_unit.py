"""Synthetic activity-list fixtures. Never connects to a device."""
import json
import unittest

from douyin_activity_client import activity_list_editor

PACKAGE = "com.ss.android.ugc.aweme"
TARGET = {"display_id": 51, "activity_token": "beef", "window_token": "face",
          "component": PACKAGE + "/" + PACKAGE + ".splash.SplashActivity"}
EDITOR = ("        com.ss.android.ugc.aweme.im.widget.SearchableEditText"
          "{abcd VFED.VCL. .F...... 0,0-900,100 #123 app:id/msg_et}\n")
CLIENT = ("ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)\n"
          "Display #51 (activities from top to bottom):\n"
          f"    topResumedActivity=ActivityRecord{{beef u0 {PACKAGE}/.splash.SplashActivity t3 mVisible=true}}\n"
          f"    * Hist  #0: ActivityRecord{{beef u0 {PACKAGE}/.splash.SplashActivity t3 mVisible=true}}\n"
          f"      app=ProcessRecord{{face 123:{PACKAGE}/u0a240}}\n"
          "      state=RESUMED stopped=false\n"
          "      Local Activity cafe State:\n"
          "      ViewRoot:\n"
          "      View Hierarchy:\n"
          "        android.widget.FrameLayout{dead V.E...... ........ 0,0-1080,2400}\n" + EDITOR +
          "      Looper (main, tid 1) {abba}\n"
          "      PRIVATE-TEXT-NOT-LOGGED\n")


class ActivityClientTests(unittest.TestCase):
    def read(self, dump=CLIENT, target=TARGET, uid=10240):
        return activity_list_editor(dump, target, uid)

    def test_complete_owned_editor_with_honest_provenance(self):
        data = self.read()
        self.assertEqual(data["status"], "focused_editor")
        self.assertEqual(data["focused_editor"]["resource_id"], "app:id/msg_et")
        self.assertEqual(data["validated_client_header"]["format"], "activity_list")
        self.assertEqual(data["validated_client_header"]["pid"], 123)
        self.assertNotIn("display_type", data["validated_client_header"])
        self.assertNotIn("PRIVATE", json.dumps(data))

    def test_wrong_or_ambiguous_display_activity_process_and_state_rejected(self):
        variants = [CLIENT.replace("Display #51", "Display #0"),
                    CLIENT + "Display #0 (activities from top to bottom):\n",
                    CLIENT.replace("ActivityRecord{beef", "ActivityRecord{cafe", 1),
                    CLIENT.replace("beef u0", "beef u10"),
                    CLIENT.replace("app=ProcessRecord{face 123:", "app=ProcessRecord{face 0:"),
                    CLIENT.replace("u0a240", "u0a241"), CLIENT.replace("u0a240", "u10a240"),
                    CLIENT.replace("state=RESUMED", "state=STOPPED"),
                    CLIENT.replace("app=ProcessRecord", "unknown=ProcessRecord"),
                    CLIENT.replace("state=RESUMED", "state=RESUMED state=RESUMED"),
                    CLIENT.replace("Local Activity cafe State:", "Local Activity cafe State:\n      Local Activity beef State:")]
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises(RuntimeError):
                self.read(candidate)
        for target in ({**TARGET, "display_id": 0}, {**TARGET, "display_id": True},
                       {**TARGET, "activity_token": "cafe"}):
            with self.assertRaises(RuntimeError): self.read(target=target)

    def test_no_acceptance_of_timeout_even_if_editor_is_present(self):
        for error in ["Failure while dumping the activity: java.io.IOException: Timeout",
                      "Got a RemoteException while dumping the activity", "Permission Denial"]:
            with self.assertRaises(RuntimeError): self.read(CLIENT + error)

    def test_truncated_or_foreign_completion_marker_cannot_authorize(self):
        variants = [CLIENT.split("      Looper")[0],
                    CLIENT.replace("      Looper", "    Looper"),
                    CLIENT.replace("      View Hierarchy:", "    View Hierarchy:"),
                    CLIENT.replace(EDITOR, "    Unknown section:\n" + EDITOR),
                    CLIENT.replace("Local Activity cafe State:", "Missing:")]
        for candidate in variants:
            with self.assertRaises(RuntimeError): self.read(candidate)

    def test_editor_flags_and_uniqueness_remain_required(self):
        for editor in [EDITOR.replace("VFED", "IFED"), EDITOR.replace("VFED", "VF.D"),
                       EDITOR.replace(".F......", "........"), EDITOR + EDITOR,
                       EDITOR.replace("SearchableEditText", "TextView")]:
            self.assertEqual(self.read(CLIENT.replace(EDITOR, editor))["status"], "editor_not_confirmed")

    def test_other_dumps_after_completed_tree_cannot_supply_focus(self):
        dump = CLIENT.replace(EDITOR, "") + EDITOR
        self.assertEqual(self.read(dump)["status"], "editor_not_confirmed")


if __name__ == "__main__": unittest.main()
