"""Real filesystem locks in temporary directories, never on a phone."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from douyin_state import experiment_lease, shared_state_root
from router.device import device_lease


class SharedStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.local, self.shared = self.base / "douyin", self.base / "wellphone-router"
        self.local.mkdir()
        self.shared.mkdir()
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WELLPHONE_STATE_ROOT", None)

    def test_defaults_to_router_sibling(self):
        self.assertEqual(shared_state_root(self.local), self.shared)

    def test_explicit_root_and_missing_root(self):
        os.environ["WELLPHONE_STATE_ROOT"] = str(self.local)
        self.assertEqual(shared_state_root(self.local), self.local)
        os.environ["WELLPHONE_STATE_ROOT"] = str(self.base / "absent")
        with self.assertRaises(RuntimeError):
            with experiment_lease(self.local, "fake"):
                self.fail("must not enter")
        self.assertFalse((self.base / "absent").exists())

    def test_busy_router_prevents_experiment(self):
        with device_lease(self.shared, "fake"):
            with self.assertRaises(RuntimeError):
                with experiment_lease(self.local, "fake"):
                    self.fail("must not enter")

    def test_experiment_blocks_router_and_older_local_process(self):
        with experiment_lease(self.local, "fake"):
            for root in (self.shared, self.local):
                with self.assertRaises(RuntimeError):
                    with device_lease(root, "fake"):
                        self.fail("must not enter")
        for root in (self.shared, self.local):
            with device_lease(root, "fake"):
                pass

    def test_busy_local_releases_acquired_shared_lock(self):
        with device_lease(self.local, "fake"):
            with self.assertRaises(RuntimeError):
                with experiment_lease(self.local, "fake"):
                    self.fail("must not enter")
            with device_lease(self.shared, "fake"):
                pass

    def test_same_root_not_locked_twice_and_error_releases(self):
        os.environ["WELLPHONE_STATE_ROOT"] = str(self.local)
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with experiment_lease(self.local, "fake"):
                raise RuntimeError("synthetic")
        with experiment_lease(self.local, "fake"):
            pass


if __name__ == "__main__":
    unittest.main()
