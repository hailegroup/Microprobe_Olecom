# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import cp_first_policy as cfp  # noqa: E402


def _build_trace_with_guarded_peak_and_unguarded_spike():
    """Flat pre-step baseline, a real step peak right at t=0 of the post
    segment (must survive smoothing, protected by the step guard), and a
    second, unrelated spike well outside the guard window (should get
    smoothed/diluted, proving the guard isn't just disabling smoothing
    entirely)."""
    pre_t = np.arange(0.0, 5.0, 0.1)
    pre = np.column_stack([pre_t, np.zeros_like(pre_t), np.zeros_like(pre_t)])

    post_t = np.arange(5.0, 15.0, 0.1)
    post_v = np.full_like(post_t, 0.05)
    post_i = np.zeros_like(post_t)
    post_i[0] = 100.0  # the real step peak, at t=5.0 (post[0,0])
    post_i[60] = 100.0  # t=11.0 -- 6s after the step, well outside a 2s guard
    post = np.column_stack([post_t, post_v, post_i])

    return np.vstack([pre, post])


class SegmentedSmoothingStepGuardTests(unittest.TestCase):
    def test_peak_within_guard_window_survives_smoothing_unchanged(self):
        data = _build_trace_with_guarded_peak_and_unguarded_spike()
        work = cfp.build_fft_ready_ca_trace(
            data,
            average_bin_s=None,
            segmented_smoothing_window_s=1.1,
            current_despike=False,
            despike_step_guard_s=2.0,
        )
        step_idx = cfp._detect_voltage_step_idx(work)
        self.assertAlmostEqual(work[step_idx, 2], 100.0, places=6)

    def test_spike_outside_guard_window_is_smoothed_and_diluted(self):
        data = _build_trace_with_guarded_peak_and_unguarded_spike()
        work = cfp.build_fft_ready_ca_trace(
            data,
            average_bin_s=None,
            segmented_smoothing_window_s=1.1,
            current_despike=False,
            despike_step_guard_s=2.0,
        )
        step_idx = cfp._detect_voltage_step_idx(work)
        self.assertLess(work[step_idx + 60, 2], 50.0)
        self.assertGreater(work[step_idx + 60, 2], 0.0)  # smoothing spreads it, doesn't erase it

    def test_zero_guard_smooths_the_peak_too(self):
        # Sanity check that the guard is actually doing something: with it
        # disabled, the same peak that stays exactly 100.0 above instead
        # gets diluted by the moving average, just like the unguarded
        # spike does. (Not fully to ~9: the peak sits at the very first
        # array index, so _moving_average's edge-padding replicates it
        # into its own left-side window instead of diluting it as evenly
        # as the interior spike -- still a clear, measurable reduction.)
        data = _build_trace_with_guarded_peak_and_unguarded_spike()
        work = cfp.build_fft_ready_ca_trace(
            data,
            average_bin_s=None,
            segmented_smoothing_window_s=1.1,
            current_despike=False,
            despike_step_guard_s=0.0,
        )
        step_idx = cfp._detect_voltage_step_idx(work)
        self.assertLess(work[step_idx, 2], 90.0)
        self.assertGreater(work[step_idx, 2], 0.0)


class SegmentedSmoothingIsTimeBasedTests(unittest.TestCase):
    """Regression coverage: segmented_smoothing_window_s must produce a
    real-time smoothing width that's roughly independent of the trace's own
    sample density -- the whole point of expressing it in seconds instead of
    points. Before this fix (a literal point count, applied post-bin), the
    same setting spread a spike over ~10x less real time on unbinned/dense
    data than on coarser data, which read as "smoothing doesn't do anything"
    unless binning ran first.
    """

    def _spike_spread_seconds(self, dt: float, window_s: float = 1.0) -> float:
        pre_t = np.arange(0.0, 5.0, dt)
        pre = np.column_stack([pre_t, np.zeros_like(pre_t), np.zeros_like(pre_t)])
        post_t = np.arange(5.0, 15.0, dt)
        post_v = np.full_like(post_t, 0.05)
        post_i = np.zeros_like(post_t)
        spike_idx = len(post_t) // 2  # well clear of both segment edges
        post_i[spike_idx] = 100.0
        post = np.column_stack([post_t, post_v, post_i])
        data = np.vstack([pre, post])

        work = cfp.build_fft_ready_ca_trace(
            data,
            average_bin_s=None,
            segmented_smoothing_window_s=window_s,
            current_despike=False,
            despike_step_guard_s=0.0,
        )
        step_idx = cfp._detect_voltage_step_idx(work)
        current = work[step_idx:, 2]
        # Boxcar averaging spreads the single spike's influence across every
        # sample the window touches -- count how many samples on ONE side
        # remain measurably nonzero and convert that count back to seconds.
        affected = np.where(current[spike_idx:] > 0.01)[0]
        n_affected_one_side = int(affected.max()) if len(affected) else 0
        return float(n_affected_one_side) * dt

    def test_dense_and_coarse_sampling_get_a_comparable_real_time_smoothing_width(self):
        spread_dense = self._spike_spread_seconds(dt=0.02)  # 50 samples/s
        spread_coarse = self._spike_spread_seconds(dt=0.2)  # 5 samples/s -- 10x sparser
        self.assertGreater(spread_dense, 0.0)
        self.assertGreater(spread_coarse, 0.0)
        # Both should sit near window_s/2 = 0.5s -- NOT differ by anything
        # close to the 10x density ratio between them (the pre-fix behavior).
        self.assertLess(abs(spread_dense - spread_coarse), 0.3)


if __name__ == "__main__":
    unittest.main()
