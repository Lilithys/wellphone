"""Native frame freshness/lifecycle checks; no ADB, ffmpeg or network."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scrcpy_frames import FrameStream


class FrameStreamTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="wellphone-frames-unit-")
        self.addCleanup(self.directory.cleanup)
        self.stream = FrameStream(self.directory.name)
        self.stream.process = Mock()
        self.stream.process.poll.return_value = None

    def test_live_source_with_new_quiet_write_is_ready(self):
        self.stream.frame_path.write_bytes(b"test readiness; PNG validation is in adapter")
        self.stream.wait_for_frame(timeout=1, settle_seconds=0.01)

    def test_old_frame_cannot_satisfy_post_action_request(self):
        self.stream.frame_path.write_bytes(b"old frame")
        os.utime(self.stream.frame_path, ns=(1, 1))
        with self.assertRaises(RuntimeError):
            self.stream.wait_for_frame(timeout=0.05, not_before_ns=time.time_ns())

    def test_dead_decoder_rejects_even_existing_frame(self):
        self.stream.frame_path.write_bytes(b"old frame")
        self.stream.process.poll.return_value = 1
        with self.assertRaises(RuntimeError):
            self.stream.wait_for_frame()

    def test_missing_ffmpeg_does_not_create_fifo(self):
        with patch("scrcpy_frames.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                self.stream.start()
        self.assertFalse(self.stream.pipe_path.exists())

    def test_cleanup_only_removes_owned_fifo(self):
        os.mkfifo(self.stream.pipe_path, 0o600)
        self.stream.stop()
        self.stream.process.terminate.assert_called_once()
        self.assertFalse(self.stream.pipe_path.exists())
        self.stream.pipe_path.write_bytes(b"not a FIFO; preserve")
        self.stream.process.poll.return_value = 0
        self.stream.stop()
        self.assertTrue(self.stream.pipe_path.is_file())


if __name__ == "__main__":
    unittest.main()
