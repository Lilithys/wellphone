"""Recipient wiring only, in a fresh process; no device/model operations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from douyin_policy import configured_recipient


class BaselineEntryTests(unittest.TestCase):
    def test_invalid_recipient_rejected_without_io(self):
        for value in ("", " a", "a ", "a\nb", "a\x7fb", "x" * 65):
            with self.subTest(value=value), patch.dict(os.environ, {"WELLPHONE_DOUYIN_RECIPIENT": value}):
                with self.assertRaises(ValueError):
                    configured_recipient()

    def test_custom_recipient_reaches_all_legacy_checks_and_journals(self):
        # Separate interpreter avoids cached 'from policy import RECIPIENT' values.
        code = '''
import hashlib, json
from types import SimpleNamespace
import douyin_policy as policy
import douyin_input as inp
import douyin_conversation as conv
import douyin_executor_probe as probe
import run_douyin_test as runner
name = policy.RECIPIENT
assert all(m.RECIPIENT == name for m in (inp, conv, probe, runner))
assert name in inp.ONE_TASK and name in inp.ONE_SYSTEM_PROMPT
assert name in runner.TASK
for stage, focus in (("FIND_RECIPIENT", False), ("CHAT", False), ("CHAT", True), ("READY_SEND", True)):
    progress = {"stage": stage, "focus_attempted": focus, "system_editor_focus_verified": True}
    assert name in conv.conversation_instruction(progress)[2]
token = hashlib.sha256(("TEST" + "\\0" + name).encode()).hexdigest()[:24]
assert policy.OneSend("/not-written", "TEST").path.name == "douyin-send-" + token + ".json"
assert inp.OneDraft("/not-written", "TEST").path.name == "douyin-input-one-" + token + ".json"
fake = SimpleNamespace(stage="FIND_RECIPIENT", swipes=0, focus_attempted=False, waits=0,
                       relocalizations=0, rejected_proposal=None)
assert conv.ConversationActions.progress(fake)["recipient"] == name
print(json.dumps({"recipient": name, "model_called": False}))
'''
        for name in ("测试联系人甲", "test-user-02"):
            env = os.environ.copy()
            env["WELLPHONE_DOUYIN_RECIPIENT"] = name
            result = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                                    cwd=Path(__file__).resolve().parent,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["recipient"], name)


if __name__ == "__main__":
    unittest.main()
