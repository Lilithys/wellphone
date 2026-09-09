"""No-device tests for audio lease, rollback and crash recovery."""
import json
import itertools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from douyin_audio import AudioLease, OPS, PACKAGE, parse_mode, player_states


def audio_dump(state=None, uid=10240):
    body = ""
    if state:
        body = f"  AudioPlaybackConfiguration piid:719 deviceId:0 type:android.media.AudioTrack u/pid:{uid}/123 state:{state} attr:test mutedState:appops\n"
    return "PlaybackActivityMonitor dump\n  players:\n" + body + "\n  ducked players piids:\n"


class FakeADB:
    serial = "fake-phone"
    def __init__(self):
        self.modes = {op: "allow" for op in OPS}
        self.calls = []
        self.uid, self.build = 10240, "test-build"
        self.playing, self.main_douyin, self.shared, self.uid_override = False, False, False, False
        self.on_set = None

    def shell(self, *args):
        args = tuple(map(str, args))
        self.calls.append(args)
        if args == ("am", "get-current-user"):
            return "0\n"
        if args == ("getprop", "ro.build.fingerprint"):
            return self.build
        if args == ("pm", "list", "packages", "-U", PACKAGE):
            return f"package:{PACKAGE} uid:{self.uid}\n"
        if args[:5] == ("pm", "list", "packages", "--uid", str(self.uid)):
            return f"package:{PACKAGE} uid:{self.uid}\n" + ("package:other.app\n" if self.shared else "")
        if args == ("dumpsys", "activity", "activities"):
            app = PACKAGE + "/.PlayerActivity" if self.main_douyin else "phone.sms/.Compose"
            return f"Display #0 (activities from top to bottom):\n  topResumedActivity=ActivityRecord{{abc u0 {app} x}}\n"
        if args == ("dumpsys", "audio"):
            return audio_dump("started" if self.playing else None)
        if args[:5] == ("cmd", "appops", "get", "--user", "0"):
            target, op = args[5:]
            assert op in OPS
            if target == str(self.uid):
                return f"{op}: allow\n" if self.uid_override else "No operations.\nDefault mode: allow\n"
            assert target == PACKAGE
            if op == "PLAY_AUDIO" and self.modes[op] == "allow":
                return "No operations.\nDefault mode: allow\n"
            return op + ": " + self.modes[op] + "; time=+2m ago\n"
        if args[:6] == ("cmd", "appops", "set", "--user", "0", PACKAGE):
            op, mode = args[6:]
            assert op in OPS and mode in {"allow", "ignore", "deny", "default"}
            self.modes[op] = mode
            if self.on_set:
                self.on_set(op, mode)
            return ""
        raise AssertionError("Unapproved command " + str(args))

    def writes(self):
        return [c for c in self.calls if c[:3] == ("cmd", "appops", "set")]


class ParsingTests(unittest.TestCase):
    def test_default_allow_is_not_literal_default(self):
        self.assertEqual(parse_mode("No operations.\nDefault mode: allow\n", "PLAY_AUDIO"),
                         {"mode": "allow", "explicit": False})

    def test_explicit_allow_with_history(self):
        self.assertEqual(parse_mode("TAKE_AUDIO_FOCUS: allow; time=+2m ago\n", "TAKE_AUDIO_FOCUS"),
                         {"mode": "allow", "explicit": True})

    def test_unknown_uid_and_duplicate_outputs_rejected(self):
        for text in ["", "No operations.", "PLAY_AUDIO: unknown", "Uid mode: PLAY_AUDIO: ignore",
                     "PLAY_AUDIO: allow\nPLAY_AUDIO: ignore", "TAKE_AUDIO_FOCUS: allow"]:
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                parse_mode(text, "PLAY_AUDIO")

    def test_only_current_target_players_returned(self):
        self.assertEqual(player_states(audio_dump(), 10240), [])
        self.assertEqual(player_states(audio_dump("started", 1000), 10240), [])
        result = player_states(audio_dump("started"), 10240)
        self.assertEqual(result, [{"id": 719, "state": "started", "muted": "appops"}])

    def test_missing_and_unknown_player_structure_rejected(self):
        for text in ["", "  players:\n", "  players:\nunknown\n  ducked players piids:\n", audio_dump("mystery")]:
            with self.assertRaises(RuntimeError):
                player_states(text, 10240)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adb = FakeADB()
        self.lease = AudioLease(self.adb, self.root)

    def test_prepare_has_no_writes(self):
        self.lease.prepare()
        self.assertEqual(self.adb.writes(), [])
        self.assertEqual(self.lease.data["original"]["PLAY_AUDIO"]["mode"], "allow")

    def test_apply_restore_exact_scope_and_original_allow(self):
        self.lease.prepare()
        self.lease.apply()
        self.lease.require_restricted()
        self.assertEqual(self.adb.modes, {op: "ignore" for op in OPS})
        self.lease.restore()
        self.assertEqual(self.adb.modes, {op: "allow" for op in OPS})
        self.assertEqual(self.lease.data["phase"], "RESTORED")
        self.assertEqual(len(self.adb.writes()), 4)
        self.assertFalse(any(c[-1] == "default" for c in self.adb.writes()))

    def test_durable_originals_and_attempt_before_write(self):
        def observe(op, mode):
            record = json.loads(self.lease.path.read_text())
            self.assertEqual(set(record["original"]), set(OPS))
            self.assertIn(op, record["attempted"])
        self.lease.prepare()
        self.adb.on_set = observe
        self.lease.apply()
        self.lease.restore()

    def test_partial_timeout_restores_both_attempted_ops(self):
        self.lease.prepare()
        def timeout(op, mode):
            if op == "PLAY_AUDIO" and mode == "ignore":
                raise TimeoutError("uncertain write")
        self.adb.on_set = timeout
        with self.assertRaises(TimeoutError):
            self.lease.apply()
        self.adb.on_set = None
        self.lease.restore()
        self.assertEqual(self.adb.modes, {op: "allow" for op in OPS})

    def test_recovery_after_process_restart(self):
        self.lease.prepare()
        self.lease.apply()
        new = AudioLease(self.adb, self.root)
        new.restore()
        self.assertEqual(new.data["phase"], "RESTORED")
        self.assertEqual(self.adb.modes, {op: "allow" for op in OPS})

    def test_pending_lease_blocks_new_run(self):
        self.lease.prepare()
        self.lease.apply()
        writes = len(self.adb.writes())
        with self.assertRaises(RuntimeError):
            AudioLease(self.adb, self.root).prepare()
        with self.assertRaises(RuntimeError):
            AudioLease(self.adb, self.root).require_no_pending()
        self.assertEqual(len(self.adb.writes()), writes)

    def test_no_pending_check_does_not_touch_phone(self):
        self.lease.require_no_pending()
        self.assertEqual(self.adb.calls, [])

    def test_shared_uid_and_uid_override_never_write(self):
        for attribute in ["shared", "uid_override", "main_douyin", "playing"]:
            with self.subTest(attribute=attribute):
                adb = FakeADB()
                setattr(adb, attribute, True)
                with self.assertRaises(RuntimeError):
                    AudioLease(adb, self.root).prepare()
                self.assertEqual(adb.writes(), [])

    def test_still_playing_defers_unmute(self):
        self.lease.prepare()
        self.lease.apply()
        self.adb.playing = True
        with self.assertRaises(RuntimeError):
            self.lease.restore()
        self.assertEqual(self.adb.modes, {op: "ignore" for op in OPS})
        self.adb.playing = False
        AudioLease(self.adb, self.root).restore()
        self.assertEqual(self.adb.modes, {op: "allow" for op in OPS})

    def test_changed_uid_or_build_refuses_recovery(self):
        self.lease.prepare()
        self.lease.apply()
        self.adb.build = "other-build"
        writes = len(self.adb.writes())
        with self.assertRaises(RuntimeError):
            self.lease.restore()
        self.assertEqual(len(self.adb.writes()), writes)

    def test_external_mode_change_not_overwritten_other_op_restores(self):
        self.lease.prepare()
        self.lease.apply()
        self.adb.modes["TAKE_AUDIO_FOCUS"] = "deny"
        with self.assertRaises(RuntimeError):
            self.lease.restore()
        self.assertEqual(self.adb.modes, {"TAKE_AUDIO_FOCUS": "deny", "PLAY_AUDIO": "allow"})
        self.assertEqual(self.lease.data["phase"], "NEEDS_RECOVERY")

    def test_invalid_recovery_target_never_writes(self):
        self.lease.prepare()
        data = json.loads(self.lease.path.read_text())
        data["package"] = "other.app"
        self.lease.path.write_text(json.dumps(data))
        with self.assertRaises(RuntimeError):
            AudioLease(self.adb, self.root).restore()
        self.assertEqual(self.adb.writes(), [])

    def test_prepare_after_success_allowed_but_changed_originals_preserved(self):
        self.lease.prepare()
        self.lease.apply()
        self.lease.restore()
        self.adb.modes["TAKE_AUDIO_FOCUS"] = "deny"
        new = AudioLease(self.adb, self.root)
        new.prepare()
        new.apply()
        new.restore()
        self.assertEqual(self.adb.modes["TAKE_AUDIO_FOCUS"], "deny")

    def test_old_recovery_does_not_overwrite_later_changes(self):
        self.lease.prepare()
        self.lease.apply()
        self.lease.restore()
        self.adb.modes["PLAY_AUDIO"] = "deny"
        writes = len(self.adb.writes())
        with self.assertRaises(RuntimeError):
            AudioLease(self.adb, self.root).restore()
        self.assertEqual(len(self.adb.writes()), writes)


class RunnerTests(unittest.TestCase):
    def phone_run(self, *, launch_error=False, interrupted=False):
        from run_audio_test import run_phone_probe
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "evidence"
            output.mkdir()
            adb = FakeADB()
            adb.state = Mock(return_value={})
            session = SimpleNamespace(report={}, output=output, scrcpy_log=[],
                                      prepare=Mock(), scope=Mock(), frames=Mock(), capture=Mock(), save=Mock())
            def launch():
                if interrupted:
                    raise KeyboardInterrupt
                if launch_error:
                    raise RuntimeError("launch failed")
                adb.playing = True
            def stop():
                adb.playing = False
                session.report["virtual_display_removed"] = True
            session.launch, session.stop_display = Mock(side_effect=launch), Mock(side_effect=stop)
            monitor = Mock(samples=[{"keyboard_on_primary": True}], errors=[])
            monitor.__enter__ = Mock()
            monitor.__exit__ = Mock()
            args = SimpleNamespace(seconds=2)
            with patch("run_audio_test.ROOT", root), patch("run_audio_test.DouyinSession", return_value=session), \
                 patch("run_audio_test.PinnedMain"), patch("run_audio_test.Monitor", return_value=monitor), \
                 patch("run_audio_test.countdown"), patch("run_audio_test.time.sleep"), \
                 patch("run_audio_test.time.monotonic", side_effect=itertools.count()), \
                 patch("builtins.input", side_effect=["yes", "是", "无", "正常", "正常"]), \
                 patch("phone_agent.agent.PhoneAgent", side_effect=AssertionError("no model in audio probe")):
                code = run_phone_probe(args, adb)
            self.assertEqual(adb.modes, {op: "allow" for op in OPS})
            self.assertTrue(session.report["permissions_restored"])
            self.assertFalse(session.report["model_called"])
            self.assertFalse(session.report["send_attempted"])
            self.assertEqual(session.report["gui_input_actions"], 0)
            session.stop_display.assert_called_once()
            monitor.__exit__.assert_called_once()
            return code, session.report

    def test_phone_probe_success_cleans_display_before_restoring(self):
        code, report = self.phone_run()
        self.assertEqual(code, 0)
        self.assertTrue(report["playback_exercised"])

    def test_phone_launch_failure_still_restores(self):
        code, report = self.phone_run(launch_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "launch failed")

    def test_phone_interruption_still_restores(self):
        code, report = self.phone_run(interrupted=True)
        self.assertEqual(code, 1)
        self.assertTrue(report["interrupted"])

    def test_permission_cycle_restores_and_never_launches_app(self):
        from run_audio_test import permission_cycle
        with tempfile.TemporaryDirectory() as directory, patch("run_audio_test.ROOT", Path(directory)):
            adb = FakeADB()
            self.assertEqual(permission_cycle(adb, approved=True), 0)
            self.assertEqual(adb.modes, {op: "allow" for op in OPS})
            result = json.loads(next((Path(directory) / "outputs").glob("audio-cycle-*/result.json")).read_text())
            self.assertFalse(result["model_called"])
            self.assertFalse(result["app_launched"])
            self.assertFalse(result["audio_isolation_qualified"])

    def test_permission_cycle_failure_restores(self):
        from run_audio_test import permission_cycle
        with tempfile.TemporaryDirectory() as directory, patch("run_audio_test.ROOT", Path(directory)):
            adb = FakeADB()
            def fail(op, mode):
                if op == "PLAY_AUDIO" and mode == "ignore":
                    raise TimeoutError("set outcome unknown")
            adb.on_set = fail
            self.assertEqual(permission_cycle(adb, approved=True), 1)
            self.assertEqual(adb.modes, {op: "allow" for op in OPS})

    def test_cancelled_cycle_does_not_write(self):
        from run_audio_test import permission_cycle
        with tempfile.TemporaryDirectory() as directory, patch("run_audio_test.ROOT", Path(directory)), patch("builtins.input", return_value="no"):
            adb = FakeADB()
            self.assertEqual(permission_cycle(adb), 1)
            self.assertEqual(adb.writes(), [])


if __name__ == "__main__":
    unittest.main()
