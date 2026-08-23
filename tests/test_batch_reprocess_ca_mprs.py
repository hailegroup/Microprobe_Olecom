# -*- coding: utf-8 -*-
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_DIR / "tools"
for p in (PROJECT_DIR, TOOLS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _stub_olecom_deps():
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
import batch_reprocess_ca_mprs as brc  # noqa: E402
import run_olecom_pre_scout_post_hybrid as rop  # noqa: E402


def _synthetic_full_ca_array() -> np.ndarray:
    """A dense, complete [t, V, I, Ns] array like a real, fully-flushed .mpr
    would parse to -- 2000 pre-hold rows (Ns=0) then 2000 scout rows (Ns=1)."""
    pre_t = np.arange(0.0, 10.0, 0.005)
    pre = np.column_stack([pre_t, np.zeros_like(pre_t), 1e-8 * np.ones_like(pre_t), np.zeros_like(pre_t)])
    scout_t = np.arange(10.0, 20.0, 0.005)
    scout = np.column_stack([scout_t, np.full_like(scout_t, 0.05), 2e-6 * np.ones_like(scout_t), np.ones_like(scout_t)])
    return np.vstack([pre, scout])


class ReprocessRowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.mpr_path = self.tmpdir / "olecom_pre_scout_ca_C01.mpr"
        self.mpr_path.write_bytes(b"")  # content irrelevant, _parse_mpr_dc is mocked

        # Existing, corrupted-looking .txt: only a handful of sparse pre
        # points (mirrors the real flush-race bug this tool exists to fix).
        old_txt = self.tmpdir / "ca_for_fft_pre_plus_scout_only.txt"
        old_data = np.array([[0.0, 0.0, 0.0], [10.005, 0.05, 2e-6], [10.01, 0.05, 1.9e-6]])
        np.savetxt(old_txt, old_data, header="time/s V/V I/A", comments="")
        self.old_txt = old_txt

        (self.tmpdir / "summary.json").write_text(
            json.dumps({"ca_fft_pre_tail_s": 10.0}), encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reprocess_writes_backup_and_corrected_txt(self):
        with mock.patch.object(rop, "_parse_mpr_dc", return_value=_synthetic_full_ca_array()):
            report = brc.reprocess_row(self.mpr_path, default_pre_tail_s=10.0, dry_run=False)

        self.assertTrue(report["changed"])
        self.assertEqual(report["old_txt_points"], 3)
        self.assertGreater(report["new_txt_points"], 3)

        backup_path = self.tmpdir / "ca_for_fft_pre_plus_scout_only.txt.pre_reprocess_backup"
        self.assertTrue(backup_path.exists())
        backup_data = np.loadtxt(backup_path, skiprows=1)
        self.assertEqual(len(backup_data), 3)  # preserves the original, un-reprocessed content

        new_data = np.loadtxt(self.old_txt, skiprows=1)
        self.assertEqual(len(new_data), report["new_txt_points"])
        self.assertGreater(len(new_data), 100)  # real pre-tail density restored, not just 3 pts

    def test_dry_run_does_not_write_anything(self):
        original_bytes = self.old_txt.read_bytes()
        with mock.patch.object(rop, "_parse_mpr_dc", return_value=_synthetic_full_ca_array()):
            report = brc.reprocess_row(self.mpr_path, default_pre_tail_s=10.0, dry_run=True)

        self.assertTrue(report["changed"])
        self.assertEqual(self.old_txt.read_bytes(), original_bytes)
        backup_path = self.tmpdir / "ca_for_fft_pre_plus_scout_only.txt.pre_reprocess_backup"
        self.assertFalse(backup_path.exists())

    def test_existing_backup_is_never_overwritten(self):
        backup_path = self.tmpdir / "ca_for_fft_pre_plus_scout_only.txt.pre_reprocess_backup"
        backup_path.write_text("sentinel original backup", encoding="utf-8")

        with mock.patch.object(rop, "_parse_mpr_dc", return_value=_synthetic_full_ca_array()):
            brc.reprocess_row(self.mpr_path, default_pre_tail_s=10.0, dry_run=False)

        self.assertEqual(backup_path.read_text(encoding="utf-8"), "sentinel original backup")

    def test_summary_json_updated_with_reprocess_metadata(self):
        with mock.patch.object(rop, "_parse_mpr_dc", return_value=_synthetic_full_ca_array()):
            brc.reprocess_row(self.mpr_path, default_pre_tail_s=10.0, dry_run=False)

        summary = json.loads((self.tmpdir / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["ca_fft_raw_reprocessed"])
        self.assertEqual(summary["ca_fft_raw_reprocessed_old_points"], 3)

    def test_uses_pre_tail_s_from_summary_json_not_the_default(self):
        (self.tmpdir / "summary.json").write_text(
            json.dumps({"ca_fft_pre_tail_s": 3.0}), encoding="utf-8"
        )
        with mock.patch.object(rop, "_parse_mpr_dc", return_value=_synthetic_full_ca_array()):
            report = brc.reprocess_row(self.mpr_path, default_pre_tail_s=999.0, dry_run=True)
        self.assertEqual(report["pre_tail_s"], 3.0)


class FindPreScoutMprsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_finds_mprs_recursively_and_ignores_others(self):
        row1 = self.tmpdir / "row001"
        row2 = self.tmpdir / "row002"
        row1.mkdir()
        row2.mkdir()
        (row1 / "olecom_pre_scout_ca_C01.mpr").write_bytes(b"")
        (row2 / "olecom_pre_scout_ca_C02.mpr").write_bytes(b"")
        (row2 / "olecom_separate_post_hold_C02.mpr").write_bytes(b"")  # must NOT be picked up

        found = brc.find_pre_scout_mprs(self.tmpdir)
        names = sorted(p.name for p in found)
        self.assertEqual(names, ["olecom_pre_scout_ca_C01.mpr", "olecom_pre_scout_ca_C02.mpr"])


if __name__ == "__main__":
    unittest.main()
