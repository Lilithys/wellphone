import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from douyin_editor_diagnostic import hierarchy_metadata


class DiagnosticTests(unittest.TestCase):
    def test_session_commands_cannot_consume_terminal_answers(self):
        from run_douyin_test import DouyinSession
        with patch("run_douyin_test.subprocess.run", return_value=SimpleNamespace(stdout="ok")) as run:
            self.assertEqual(DouyinSession.shell(SimpleNamespace(serial="fake"), "dumpsys", "activity"), "ok")
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(run.call_args.kwargs["check"])

    def setUp(self):
        self.target = {"display_id": 51, "activity_token": "beef",
                       "component": "com.ss.android.ugc.aweme/com.ss.android.ugc.aweme.splash.SplashActivity"}
        self.dump = ("ACTIVITY com.ss.android.ugc.aweme/.splash.SplashActivity beef pid=123 "
                     "userId=0 uid=10240 displayId=51(type=VIRTUAL)\n"
                     "    View Hierarchy:\n"
                     "      android.widget.FrameLayout{aa V.E...... ........ 0,0-1080,2400}\n"
                     "        com.ss.android.ugc.aweme.im.widget.SearchableEditText{bb VFED.VCL. .F...... "
                     "0,0-100,100 app:id/msg_et PRIVATE-TEXT}\n"
                     "    Looper: PRIVATE-DETAIL\n"
                     "      android.widget.EditText{cc VFED.VCL. .F...... 0,0-100,100}\n")

    def test_scoped_structure_only_and_section_boundary(self):
        data = hierarchy_metadata(self.dump, self.target)
        self.assertEqual(data["parsed_view_count"], 2)
        self.assertEqual(len(data["focused_nodes"]), 1)
        self.assertEqual(data["known_editor_in_hierarchy"], 1)
        self.assertNotIn("PRIVATE", json.dumps(data))
        self.assertNotIn("cc", json.dumps(data))

    def test_wrong_display_activity_or_ambiguous_hierarchy(self):
        for dump in (self.dump.replace("displayId=51", "displayId=0"), self.dump.replace("beef", "cafe")):
            with self.assertRaises(RuntimeError):
                hierarchy_metadata(dump, self.target)
        self.assertEqual(hierarchy_metadata(self.dump + "View Hierarchy:\n", self.target),
                         {"hierarchy_headers": 2})


if __name__ == "__main__":
    unittest.main()
