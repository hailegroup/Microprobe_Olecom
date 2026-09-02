# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_DIR / "tools"
for p in (PROJECT_DIR, TOOLS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _stub_olecom_deps():
    """This module hard-imports comtypes/galvani (Windows/OLE-COM-only,
    not installable on this dev machine) purely to talk to EC-Lab -- none
    of that is exercised by _wait_for_point_count_stable itself, so a
    bare module stub is enough to let the import succeed."""
    if "comtypes" not in sys.modules:
        comtypes_stub = types.ModuleType("comtypes")
        comtypes_client_stub = types.ModuleType("comtypes.client")
        comtypes_stub.client = comtypes_client_stub
        sys.modules["comtypes"] = comtypes_stub
        sys.modules["comtypes.client"] = comtypes_client_stub
    if "galvani" not in sys.modules:
        galvani_stub = types.ModuleType("galvani")
        biologic_stub = types.ModuleType("galvani.BioLogic")
        galvani_stub.BioLogic = biologic_stub
        sys.modules["galvani"] = galvani_stub
        sys.modules["galvani.BioLogic"] = biologic_stub


_stub_olecom_deps()
import run_olecom_pre_scout_post_hybrid as m  # noqa: E402


class WaitForPointCountStableTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.mpr_path = Path(path)

    def tearDown(self):
        try:
            os.unlink(self.mpr_path)
        except Exception:
            pass

    def test_returns_immediately_once_stable_without_verify_fn(self):
        ctrl = mock.Mock()
        ctrl.point_count.return_value = 100
        result = m._wait_for_point_count_stable(
            ctrl, self.mpr_path, stable_s=0.05, timeout_s=2.0, poll_s=0.01, label="test",
        )
        self.assertEqual(result, 100)

    def test_waits_for_on_disk_count_to_catch_up_to_live_count(self):
        # Reproduces the real bug: OLE-COM's own point count is stable and
        # correct immediately, but an independent on-disk parse of the same
        # path starts out under-reporting (buffer not fully flushed yet)
        # and only catches up after a few more polls.
        ctrl = mock.Mock()
        ctrl.point_count.return_value = 100
        calls = {"n": 0}

        def verify(_path):
            calls["n"] += 1
            return min(calls["n"] * 30, 100)

        result = m._wait_for_point_count_stable(
            ctrl, self.mpr_path, stable_s=0.02, timeout_s=3.0, poll_s=0.01, label="test",
            verify_row_count_fn=verify,
        )
        self.assertEqual(result, 100)
        self.assertGreaterEqual(calls["n"], 4, "expected the on-disk count to be re-checked more than once")

    def test_raises_timeout_if_on_disk_count_never_catches_up(self):
        ctrl = mock.Mock()
        ctrl.point_count.return_value = 100
        with self.assertRaises(TimeoutError):
            m._wait_for_point_count_stable(
                ctrl, self.mpr_path, stable_s=0.02, timeout_s=0.2, poll_s=0.01, label="test",
                verify_row_count_fn=lambda p: 5,
            )

    def test_verify_fn_receives_the_mpr_path(self):
        ctrl = mock.Mock()
        ctrl.point_count.return_value = 100
        seen_paths = []

        def verify(path):
            seen_paths.append(path)
            return 100

        m._wait_for_point_count_stable(
            ctrl, self.mpr_path, stable_s=0.02, timeout_s=2.0, poll_s=0.01, label="test",
            verify_row_count_fn=verify,
        )
        self.assertTrue(all(p == self.mpr_path for p in seen_paths))
        self.assertTrue(len(seen_paths) >= 1)


if __name__ == "__main__":
    unittest.main()
