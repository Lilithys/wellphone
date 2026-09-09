"""Only artificial credentials and private temporary FIFOs; no device/network."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from local_operator_session import (LIMIT, check_private_pipe, encode_key,
                                    receive_key, run_child, send_key, test_command)


class LocalOperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.folder.chmod(0o700)
        self.pipe = self.folder / "key.pipe"
        os.mkfifo(self.pipe, 0o600)
        self.fd = os.open(self.pipe, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, self.fd)

    def test_key_encode_rejects_empty_placeholder_control_and_oversize(self):
        for key in ("", "...", "EMPTY", "fake\nkey", "fake\0key", "a" * LIMIT):
            with self.subTest(size=len(key)), self.assertRaises(RuntimeError):
                encode_key(key)
        self.assertEqual(json.loads(encode_key("fake-test-key")), {"api_key": "fake-test-key"})

    def test_one_local_handoff_never_prints_key(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"PHONE_AGENT_API_KEY": "fake-test-key"}), redirect_stdout(output):
            send_key(self.pipe)
        self.assertNotIn("fake-test-key", output.getvalue())
        self.assertEqual(receive_key(self.fd, timeout=.5), "fake-test-key")

    def test_sender_rejects_nonprivate_pipe_and_symlink(self):
        self.pipe.chmod(0o644)
        with self.assertRaises(RuntimeError):
            check_private_pipe(self.pipe)
        self.pipe.chmod(0o600)
        alias = self.folder / "alias.pipe"
        alias.symlink_to(self.pipe)
        with self.assertRaises(RuntimeError):
            check_private_pipe(alias)
        self.folder.chmod(0o755)
        with self.assertRaises(RuntimeError):
            check_private_pipe(self.pipe)
        self.folder.chmod(0o700)

    def test_regular_file_is_not_a_credential_destination(self):
        path = self.folder / "ordinary.txt"
        path.write_text("unchanged")
        path.chmod(0o600)
        with patch.dict(os.environ, {"PHONE_AGENT_API_KEY": "fake-test-key"}), self.assertRaises(RuntimeError):
            send_key(path)
        self.assertEqual(path.read_text(), "unchanged")

    def test_receiver_accepts_no_commands_or_extra_fields(self):
        for value in ({"api_key": "fake-test-key", "command": "anything"}, {"api_key": 123}, [], "fake-test-key"):
            os.write(self.fd, (json.dumps(value) + "\n").encode())
            with self.assertRaises(RuntimeError) as stopped:
                receive_key(self.fd, timeout=.5)
            self.assertNotIn("fake-test-key", str(stopped.exception))

    def test_receiver_times_out_or_rejects_oversize_without_disclosing_payload(self):
        with self.assertRaises(RuntimeError):
            receive_key(self.fd, timeout=.01)
        os.write(self.fd, b"a" * (LIMIT + 1))
        with self.assertRaises(RuntimeError):
            receive_key(self.fd, timeout=.5)

    def test_child_command_keeps_exact_scope_and_records_assistant_reviewer(self):
        command = test_command("fake-device")
        for option in ("--send-one-flow", "--confirm-home-first", "--non-presentation",
                       "--auto-messages", "--low-fps-trial"):
            self.assertIn(option, command)
        self.assertEqual(command[command.index("--reviewer") + 1], "assistant")
        self.assertEqual(command[command.index("--max-steps") + 1], "20")
        self.assertEqual(command[-2:], ["--serial", "fake-device"])
        self.assertNotIn("--api-key", command)

    def test_interrupt_requests_cleanup_and_waits_without_kill(self):
        child = Mock(wait=Mock(side_effect=[KeyboardInterrupt(), 130]), poll=Mock(return_value=None))
        with patch("local_operator_session.subprocess.Popen", return_value=child), redirect_stdout(io.StringIO()):
            self.assertEqual(run_child("fake-device", {}), 130)
        child.terminate.assert_called_once()
        child.kill.assert_not_called()
        self.assertEqual(child.wait.call_count, 2)

    def test_cleanup_timeout_does_not_kill_or_spawn_retry(self):
        child = Mock(wait=Mock(side_effect=[KeyboardInterrupt(), subprocess.TimeoutExpired("fake", 60)]),
                     poll=Mock(return_value=None))
        with patch("local_operator_session.subprocess.Popen", return_value=child) as launch, \
                redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            run_child("fake-device", {})
        launch.assert_called_once()
        child.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
