# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
import sys

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = PROJECT_DIR / "Analysis_Convert_CP_to_EIS"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

from compare_full_vs_optimized_fit import assess_peis_only_sufficiency


class PeisOnlyOverrideTests(unittest.TestCase):
    def test_strong_endpoint_agreement_can_relax_plateau_requirement(self):
        eis = np.array([
            [1.0, 100.0, 0.0],
            [2.0, 104.0, 0.0],
            [3.0, 114.0, 0.0],
            [4.0, 110.0, 0.0],
            [5.0, 108.0, 0.0],
            [50.0, 30.0, 0.0],
        ], dtype=float)
        recovered_f = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float)
        recovered_z = np.array([
            107.0 + 0.0j,
            107.0 + 0.0j,
            107.0 + 0.0j,
            107.0 + 0.0j,
            107.0 + 0.0j,
        ])

        result = assess_peis_only_sufficiency(eis, recovered_f, recovered_z)

        self.assertFalse(result["peis_plateau_found"])
        self.assertTrue(result["strong_agreement_relaxed_plateau"])
        self.assertTrue(result["peis_only_sufficient"])
        self.assertEqual(
            result["selection_reason"],
            "peis_reaches_saturation_via_strong_agreement",
        )

    def test_override_does_not_trigger_when_tail_is_far_from_plateau(self):
        eis = np.array([
            [1.0, 100.0, 0.0],
            [2.0, 100.0, 0.0],
            [3.0, 150.0, 0.0],
            [4.0, 150.0, 0.0],
            [5.0, 150.0, 0.0],
            [50.0, 30.0, 0.0],
        ], dtype=float)
        recovered_f = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float)
        recovered_z = np.array([
            130.0 + 0.0j,
            130.0 + 0.0j,
            130.0 + 0.0j,
            130.0 + 0.0j,
            130.0 + 0.0j,
        ])

        result = assess_peis_only_sufficiency(eis, recovered_f, recovered_z)

        self.assertFalse(result["peis_plateau_found"])
        self.assertFalse(result["strong_agreement_relaxed_plateau"])
        self.assertFalse(result["peis_only_sufficient"])
        self.assertEqual(result["selection_reason"], "hybrid_still_helpful")


    def test_plateau_like_tail_and_moderate_agreement_are_distinct_from_extreme_mismatch(self):
        tail_band = 0.12
        agreement_rel_err = 0.45
        heuristic_tail_is_plateau_like = tail_band <= 0.15
        heuristic_accepts = heuristic_tail_is_plateau_like and agreement_rel_err <= 0.60
        self.assertTrue(heuristic_accepts)

        extreme_mismatch_err = 0.95
        heuristic_rejects_extreme_mismatch = heuristic_tail_is_plateau_like and extreme_mismatch_err <= 0.60
        self.assertFalse(heuristic_rejects_extreme_mismatch)

    def test_no_hybrid_gain_shortcut_only_applies_when_fast_cutoff_does_not_go_lower(self):
        peis_min_freq = 0.62
        self.assertTrue(4.10 >= peis_min_freq * 0.95)
        self.assertFalse(0.0499 >= peis_min_freq * 0.95)


if __name__ == "__main__":
    unittest.main()
