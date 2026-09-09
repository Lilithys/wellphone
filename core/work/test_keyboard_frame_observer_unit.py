"""No-phone regression tests for continuous redraw, stale data and decoder death."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from keyboard_frame_observer import observe_frames, read_native


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FrameObserverTests(unittest.TestCase):
    def setUp(self):
        self.stream = SimpleNamespace(assert_live=Mock(), frame_path=Path("unused.png"))
        self.clock = Clock()

    def observe(self, reader, **kwargs):
        with patch("keyboard_frame_observer.read_native", side_effect=reader), \
             patch("keyboard_frame_observer.time.monotonic", side_effect=self.clock.monotonic), \
             patch("keyboard_frame_observer.time.sleep", side_effect=self.clock.sleep):
            return observe_frames(self.stream, **kwargs)

    def test_continuous_identical_frame_rewrites_do_not_time_out(self):
        counter = iter(range(100, 1000))
        data, report = self.observe(lambda _: (b"image", next(counter), "same pixels"), not_before_ns=100)
        self.assertEqual(data, b"image")
        self.assertEqual(report["status"], "fresh_frame_received")
        self.assertGreaterEqual(report["observed_file_updates"], 8)
        self.assertEqual(report["observed_content_variants"], 1)
        self.assertLess(report["max_observed_write_gap_ms"], 200)
        self.assertLess(report["elapsed_ms"], 2000)

    def test_continuous_pixel_animation_is_recorded_not_rejected(self):
        counter = iter(range(100, 1000))

        def animated(_):
            value = next(counter)
            return b"image", value, f"pixels{value % 2}"

        data, report = self.observe(animated, not_before_ns=100)
        self.assertIsNotNone(data)
        self.assertEqual(report["observed_content_variants"], 2)

    def test_pre_action_frame_does_not_satisfy_new_frame_requirement(self):
        data, report = self.observe(lambda _: (b"old", 50, "pixels"), not_before_ns=100, timeout=0.3)
        self.assertIsNone(data)
        self.assertEqual(report["status"], "no_fresh_frame")
        self.assertFalse(report["fresh_frame_received"])

    def test_static_post_action_frame_is_accepted(self):
        data, report = self.observe(lambda _: (b"image", 100, "pixels"), not_before_ns=100)
        self.assertEqual(data, b"image")
        self.assertEqual(report["observed_file_updates"], 1)

    def test_frame_arriving_late_is_timed(self):
        def delayed(_):
            return b"image", 50 if self.clock.now < 0.5 else 100, "pixels"
        data, report = self.observe(delayed, not_before_ns=100)
        self.assertIsNotNone(data)
        self.assertGreaterEqual(report["first_fresh_frame_ms"], 500)

    def test_decoder_dies_after_receiving_a_frame(self):
        self.stream.assert_live.side_effect = [None, RuntimeError("decoder died")]
        data, report = self.observe(lambda _: (b"image", 100, "pixels"), not_before_ns=100)
        self.assertIsNone(data)
        self.assertEqual(report["status"], "producer_error")

    def test_missing_frame_is_not_success(self):
        data, report = self.observe(Mock(side_effect=FileNotFoundError("missing")), timeout=0.3)
        self.assertIsNone(data)
        self.assertEqual(report["read_errors"], ["missing"])

    def test_invalid_frame_is_not_success(self):
        data, report = self.observe(Mock(side_effect=ValueError("bad dimensions")), timeout=0.3)
        self.assertIsNone(data)
        self.assertIn("bad dimensions", report["read_errors"])

    def test_native_reader_uses_original_png_and_metadata(self):
        with tempfile.TemporaryDirectory(prefix="wellphone-observer-unit-") as folder:
            path = Path(folder) / "frame.png"
            Image.new("RGB", (1080, 2400), "blue").save(path)
            data, timestamp, pixel_hash = read_native(path)
            self.assertEqual(data, path.read_bytes())
            self.assertEqual(timestamp, path.stat().st_mtime_ns)
            self.assertEqual(len(pixel_hash), 64)

    def test_native_reader_rejects_thumbnail(self):
        with tempfile.TemporaryDirectory(prefix="wellphone-observer-unit-") as folder:
            path = Path(folder) / "frame.png"
            Image.new("RGB", (92, 300), "blue").save(path)
            with self.assertRaises(ValueError):
                read_native(path)


if __name__ == "__main__":
    unittest.main()
