"""No-device tests for the opt-in display-category comparison."""
import hashlib
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from douyin_display import (NON_PRESENTATION_RELATIVE, NON_PRESENTATION_SHA256,
                            require_comparison_scope, select_server, verify_non_presentation)


LOG = "[server] INFO: Wellphone non-presentation VERIFIED: display=66 actual=0x1fc0 presentation_mask=0x8"


class DisplayComparisonTests(unittest.TestCase):
    def session(self, lines=None):
        return SimpleNamespace(args=SimpleNamespace(non_presentation=True, preflight=True), display_id=66,
            focus_flags_verified=Mock(is_set=Mock(return_value=True)), report={}, save=Mock(),
            scrcpy_log=[LOG] if lines is None else lines)

    def test_scope_accepts_only_preflight_or_explicit_home_gated_handoff(self):
        cases = [SimpleNamespace(non_presentation=True),
                 SimpleNamespace(non_presentation=True, preflight=False),
                 SimpleNamespace(non_presentation=True, startup_only=True),
                 SimpleNamespace(non_presentation=True, confirm_home_first=True),
                 SimpleNamespace(non_presentation=True, preflight=True, startup_only=True),
                 SimpleNamespace(non_presentation=True, preflight=True, confirm_home_first=True)]
        for args in cases:
            with self.assertRaises(RuntimeError):
                require_comparison_scope(args)
        require_comparison_scope(SimpleNamespace(non_presentation=True, preflight=True))
        require_comparison_scope(SimpleNamespace(non_presentation=True, startup_only=True, confirm_home_first=True))
        require_comparison_scope(SimpleNamespace())

    def test_default_server_is_exact_baseline(self):
        self.assertEqual(select_server(Path("/fake"), SimpleNamespace()),
                         Path("/fake/work/focus_experiment/scrcpy-server-focus"))

    def test_missing_or_modified_binary_never_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(non_presentation=True, preflight=True)
            with self.assertRaises(RuntimeError):
                select_server(folder, args)
            path = Path(folder) / NON_PRESENTATION_RELATIVE
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not-the-qualified-trial-binary")
            with self.assertRaises(RuntimeError):
                select_server(folder, args)

    def test_bundled_artifact_and_patch_hashes_match_manifest(self):
        root = Path(__file__).resolve().parents[1]
        path = select_server(root, SimpleNamespace(non_presentation=True, preflight=True))
        manifest_path = root / "work/display_experiment/build-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), NON_PRESENTATION_SHA256)
        self.assertEqual(manifest["server_sha256"], NON_PRESENTATION_SHA256)
        for kind in ("patch", "base_patch"):
            resource = manifest_path.parent / manifest[kind]
            self.assertEqual(hashlib.sha256(resource.read_bytes()).hexdigest(), manifest[kind + "_sha256"])

    def test_exact_live_readback_with_original_focus_is_required(self):
        s = self.session()
        verify_non_presentation(s)
        self.assertTrue(s.report["non_presentation_verified"])
        self.assertFalse(s.report["display_category_check"]["presentation"])
        self.assertEqual(s.report["display_category_check"]["display_id"], 66)
        s.save.assert_called_once()

    def test_missing_duplicate_wrong_display_or_presentation_bit_blocks(self):
        cases = [[], [LOG, LOG], [LOG.replace("display=66", "display=65")],
                 [LOG.replace("actual=0x1fc0", "actual=0x1fc8")],
                 [LOG.replace("presentation_mask=0x8", "presentation_mask=0x0")],
                 [LOG.replace("presentation_mask=0x8", "presentation_mask=0x3")]]
        for rows in cases:
            s = self.session(rows)
            with self.assertRaises(RuntimeError):
                verify_non_presentation(s)
            self.assertFalse(s.report["non_presentation_verified"])

    def test_missing_original_focus_protection_or_primary_display_blocks(self):
        s = self.session()
        s.focus_flags_verified.is_set.return_value = False
        with self.assertRaises(RuntimeError):
            verify_non_presentation(s)
        s = self.session()
        s.display_id = 0
        with self.assertRaises(RuntimeError):
            verify_non_presentation(s)

    def test_baseline_does_not_require_the_new_marker(self):
        s = SimpleNamespace(args=SimpleNamespace(), report={})
        verify_non_presentation(s)
        self.assertEqual(s.report, {})

    def test_cli_rejects_ungated_model_and_send_before_tty_build_or_device(self):
        from run_douyin_test import main
        for argv in (["run", "--non-presentation"],
                     ["run", "--non-presentation", "--startup-only"],
                     ["run", "--non-presentation", "--message", "1"]):
            with patch("sys.argv", argv), patch("run_douyin_test.execute") as execute, \
                    patch("run_douyin_test.verify_source") as verify, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main()
            execute.assert_not_called()
            verify.assert_not_called()

    def test_launch_checks_variant_before_starting_app(self):
        from run_douyin_test import DouyinSession
        s = DouyinSession.__new__(DouyinSession)
        s.__dict__.update(self.session([]).__dict__)
        s.output, s.monitor, s.audio_lease = Path("/unused"), Mock(), Mock()
        s.start_display, s.shell = Mock(), Mock()
        with patch("run_douyin_test.FrameStream"), self.assertRaises(RuntimeError):
            s.launch()
        s.start_display.assert_called_once()
        s.shell.assert_not_called()  # In particular, no am start or fallback input.


if __name__ == "__main__":
    unittest.main()
