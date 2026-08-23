# -*- coding: utf-8 -*-
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_DIR / "tools"
for p in (PROJECT_DIR, TOOLS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import make_olecom_full_arc_from_run as marc  # noqa: E402
import ca_fft_tuning_gui as tuning  # noqa: E402


def _build_synthetic_run_dir(run_dir: Path, *, dt_s: float = 0.1, spike: bool = True) -> None:
    rng = np.random.default_rng(1)
    true_params = np.array([2.0, 500.0, 3e-4, 0.85])  # Rs, R, Q, alpha

    pre_t = np.arange(0.0, 5.0, dt_s)
    pre_v = np.zeros_like(pre_t)
    pre_i = 1e-7 * rng.standard_normal(len(pre_t))

    post_t = np.arange(5.0, 25.0, dt_s)
    dv = 0.05
    tau = 500.0 * 3e-4
    post_v = np.full_like(post_t, dv)
    post_i = (dv / 500.0) * np.exp(-(post_t - 5.0) / max(tau, 1e-6)) + 1e-6
    post_i = post_i + 2e-8 * rng.standard_normal(len(post_t))
    if spike and len(post_i) > 30:
        post_i[25] += 8e-5  # one large isolated outlier

    t = np.concatenate([pre_t, post_t])
    v = np.concatenate([pre_v, post_v])
    i = np.concatenate([pre_i, post_i])

    raw_ca_txt = run_dir / "ca_for_fft_pre_plus_scout_only.txt"
    np.savetxt(raw_ca_txt, np.column_stack([t, v, i]), header="time/s V/V I/A", comments="")

    freqs = np.geomspace(2000.0, 50.0, 12)
    z = marc._z_rrq(true_params, freqs)
    peis = np.column_stack([freqs, z.real, -z.imag])
    peis_txt = run_dir / "measured_peis.txt"
    np.savetxt(peis_txt, peis, delimiter="\t", header="freq_Hz\tReZ_ohm\tnegImZ_ohm", comments="")

    summary = {
        "ca_fft_txt": str(raw_ca_txt),
        "ca_fft_raw_txt": str(raw_ca_txt),
        "peis_txt": str(peis_txt),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


class LoadAndSmoothRawCaTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _build_synthetic_run_dir(self.tmpdir)
        self.raw_ca_txt = self.tmpdir / "ca_for_fft_pre_plus_scout_only.txt"
        self.auto_trim_kwargs = dict(
            voltage_step_sigma=8.0, smooth_window_points=11,
            stability_window_s=2.0, sustain_window_s=3.0, std_factor=2.5,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _smooth(self, **overrides):
        params = dict(
            average_bin_s=None,
            despike_sigma=6.0,
            despike_window_s=1.0,
            despike_step_guard_s=2.0,
            segmented_smoothing_window_s=None,
            auto_trim_kwargs=self.auto_trim_kwargs,
        )
        params.update(overrides)
        return marc._load_and_smooth_raw_ca(self.raw_ca_txt, **params)

    def test_returns_equal_length_arrays(self):
        t, v, i = self._smooth()
        self.assertEqual(len(t), len(v))
        self.assertEqual(len(t), len(i))
        self.assertGreater(len(t), 10)

    def test_aggressive_despike_flattens_isolated_spike_more_than_lenient(self):
        t_tight, v_tight, i_tight = self._smooth(despike_sigma=0.5)
        t_loose, v_loose, i_loose = self._smooth(despike_sigma=1e8)
        # The synthetic spike sits at post-step t=5+25*0.1=7.5s; look at the
        # peak current anywhere in the post-step window near it.
        window = (t_tight > 6.5) & (t_tight < 8.5)
        window_loose = (t_loose > 6.5) & (t_loose < 8.5)
        self.assertLess(np.max(i_tight[window]), np.max(i_loose[window_loose]))

    def test_average_bin_s_none_auto_detects_from_raw_spacing(self):
        t_auto, _v, _i = self._smooth(average_bin_s=None)
        dt_auto = float(np.median(np.diff(t_auto)))
        self.assertAlmostEqual(dt_auto, 0.1, places=2)

    def test_explicit_average_bin_s_overrides_auto_detection(self):
        t_auto, _v, _i = self._smooth(average_bin_s=None)
        t_coarse, _v2, _i2 = self._smooth(average_bin_s=2.0)
        self.assertLess(len(t_coarse), len(t_auto))


class PrefillDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_falls_back_to_module_defaults_without_full_arc_summary(self):
        params = tuning._prefill_defaults(self.tmpdir)
        self.assertEqual(params, tuning.DEFAULT_PARAMS)

    def test_uses_full_arc_summary_when_present(self):
        (self.tmpdir / "full_arc_summary.json").write_text(
            json.dumps({
                "zero_pad_factor": 77,
                "n_log_points": 55,
                "fft_duration_guard_periods": 0.3,
                "fft_peis_anchor_gap_decades": 0.2,
                "fit_model": "RQRQ",
            }),
            encoding="utf-8",
        )
        params = tuning._prefill_defaults(self.tmpdir)
        self.assertEqual(params["zero_pad_factor"], 77)
        self.assertEqual(params["n_log_points"], 55)
        self.assertAlmostEqual(params["duration_guard_periods"], 0.3)
        self.assertAlmostEqual(params["peis_anchor_gap_decades"], 0.2)
        self.assertEqual(params["fit_model"], "RQRQ")
        # Smoothing/auto-trim knobs aren't recorded in full_arc_summary.json
        # today, so they must still fall back to the module defaults.
        self.assertEqual(params["despike_sigma"], tuning.DEFAULT_PARAMS["despike_sigma"])


class DetectStepTimeTests(unittest.TestCase):
    def test_returns_absolute_time_of_largest_voltage_jump(self):
        t = np.arange(0.0, 20.0, 0.1)
        v = np.where(t < 10.0, 0.0, 0.05)
        step_t = tuning._detect_step_time(t, v)
        self.assertAlmostEqual(step_t, 10.0, places=1)

    def test_matches_regardless_of_where_the_trace_starts(self):
        # The raw trace and a differently-trimmed processed trace can start
        # at different absolute times but should agree on the step time as
        # long as both still contain it.
        t_a = np.arange(0.0, 20.0, 0.1)
        v_a = np.where(t_a < 10.0, 0.0, 0.05)
        t_b = np.arange(3.0, 20.0, 0.1)
        v_b = np.where(t_b < 10.0, 0.0, 0.05)
        self.assertAlmostEqual(
            tuning._detect_step_time(t_a, v_a), tuning._detect_step_time(t_b, v_b), places=1
        )


class ParseFieldTests(unittest.TestCase):
    def test_int_and_float(self):
        self.assertEqual(tuning._parse_field("30", "int"), 30)
        self.assertAlmostEqual(tuning._parse_field("1.5", "float"), 1.5)

    def test_optional_blank_returns_none(self):
        self.assertIsNone(tuning._parse_field("", "optional_float"))
        self.assertIsNone(tuning._parse_field("   ", "optional_int"))

    def test_optional_nonblank_parses(self):
        self.assertEqual(tuning._parse_field("5", "optional_int"), 5)
        self.assertAlmostEqual(tuning._parse_field("0.25", "optional_float"), 0.25)


class FormatValueWithErrorTests(unittest.TestCase):
    def test_shows_plus_minus_when_error_present(self):
        self.assertEqual(tuning._format_value_with_error(50.49, 0.075), "50.49 +/- 0.075")

    def test_omits_plus_minus_when_error_missing(self):
        self.assertEqual(tuning._format_value_with_error(50.49, None), "50.49")

    def test_omits_plus_minus_when_error_is_nan(self):
        self.assertEqual(tuning._format_value_with_error(50.49, float("nan")), "50.49")

    def test_value_none_still_falls_back_to_placeholder(self):
        self.assertEqual(tuning._format_value_with_error(None, 0.1), "--")


class FormatConditionLineTests(unittest.TestCase):
    def test_shows_condition_number_without_marker_when_well_conditioned(self):
        line = tuning._format_condition_line({"fit_condition_number": 2.1e8, "fit_errors_ill_conditioned": False})
        self.assertIn("2.1e+08", line)
        self.assertNotIn("ILL-CONDITIONED", line)

    def test_shows_ill_conditioned_marker_when_flagged(self):
        line = tuning._format_condition_line({"fit_condition_number": 5.7e14, "fit_errors_ill_conditioned": True})
        self.assertIn("ILL-CONDITIONED", line)

    def test_missing_condition_number_falls_back_to_placeholder(self):
        line = tuning._format_condition_line({})
        self.assertIn("--", line)
        self.assertNotIn("ILL-CONDITIONED", line)


class FormatExtractedRowErrorBarTests(unittest.TestCase):
    def _base_extracted(self):
        return {
            "fit_model": "RRQRQ", "fit_score": 0.02, "fit_cost": 0.4,
            "Rs_ohm": 9.41, "RQ1_R_ohm": 50.49, "RQ1_alpha": 0.865,
            "RQ1_fchar_Hz": 40590.5, "RQ1_resistance_fraction": 0.112,
            "RQ1_Q_F_s_alpha_minus_1": 4.2e-7,
            "RQ1_C_F": 1e-7, "RQ2_R_ohm": 399.67, "RQ2_alpha": 0.9,
            "RQ2_fchar_Hz": 2.64, "RQ2_resistance_fraction": 0.888,
            "RQ2_Q_F_s_alpha_minus_1": 2.0e-4,
            "RQ2_C_F": 2e-4, "R1+R2_ohm": 450.15,
            # Deliberately no *_err_ohm / *_err keys -- added per test below.
        }

    def test_q_and_capacitance_shown_with_monte_carlo_error_only(self):
        # Q gets both Jacobian and MC error; capacitance has no Jacobian
        # error at all (nonlinear function of R/Q/alpha) -- MC only.
        extracted = self._base_extracted()
        extracted.update({
            "RQ1_Q_err_F_s_alpha_minus_1": 7.4e-8, "RQ1_Q_mc_err_F_s_alpha_minus_1": 9.1e-8,
            "RQ1_C_mc_err_F": 3.7e-8,
            "mc_n_iterations": 30, "mc_n_success": 28,
        })
        text = tuning._format_extracted_row({}, extracted, [])
        self.assertIn("Q=4.2e-07 +/- 7.4e-08", text)
        self.assertIn("[MC +/- 9.1e-08, N=28/30]", text)
        self.assertIn("C=1e-07  [MC +/- 3.7e-08, N=28/30] F", text)

    def test_errors_render_as_plus_minus_lines(self):
        extracted = self._base_extracted()
        extracted.update({
            "Rs_err_ohm": 0.075, "RQ1_R_err_ohm": 0.075, "RQ1_alpha_err": 0.016,
            "RQ2_R_err_ohm": 0.0039, "RQ2_alpha_err": 0.0019, "R1+R2_err_ohm": 0.075,
        })
        text = tuning._format_extracted_row({}, extracted, [])
        self.assertIn("Rs: 9.41 +/- 0.075 ohm", text)
        self.assertIn("R=50.49 +/- 0.075 ohm", text)
        self.assertIn("alpha=0.865 +/- 0.016", text)
        self.assertIn("R1+R2: 450.15 +/- 0.075 ohm", text)

    def test_missing_errors_render_plain_values_no_dangling_plus_minus(self):
        text = tuning._format_extracted_row({}, self._base_extracted(), [])
        self.assertNotIn("+/-", text)
        self.assertIn("Rs: 9.41 ohm", text)
        self.assertIn("R=50.49 ohm", text)

    def test_ill_conditioned_fit_shows_warning_marker_in_full_panel(self):
        extracted = self._base_extracted()
        extracted.update({
            "Rs_err_ohm": 0.075, "RQ1_R_err_ohm": 200.0,
            "fit_condition_number": 5.7e14, "fit_errors_ill_conditioned": True,
        })
        text = tuning._format_extracted_row({}, extracted, [])
        self.assertIn("ILL-CONDITIONED", text)


class CAFFTTuningWindowTests(unittest.TestCase):
    def setUp(self):
        # An isolated parent directory containing just this one row folder,
        # so sibling-scanning (Prev/Next/Save fits) never touches the real
        # OS temp directory. Resolved up front (macOS aliases /tmp under
        # /private/tmp) so comparisons against the app's own resolved paths
        # match exactly.
        self.parent_dir = Path(tempfile.mkdtemp()).resolve()
        self.tmpdir = self.parent_dir / "row001_sample"
        self.tmpdir.mkdir()
        _build_synthetic_run_dir(self.tmpdir)
        self.app = tuning.CAFFTTuningWindow(self.tmpdir)
        self.app.withdraw()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        shutil.rmtree(self.parent_dir, ignore_errors=True)

    def test_window_builds_and_prefills_fields(self):
        self.assertIn("zero_pad_factor", self.app._vars)
        self.assertEqual(self.app._vars["zero_pad_factor"].get(), str(tuning.DEFAULT_PARAMS["zero_pad_factor"]))

    def test_relative_error_defaults_true_and_is_read_by_read_params(self):
        self.assertTrue(self.app._relative_error_var.get())
        self.assertTrue(self.app._read_params()["relative_error"])

    def test_unchecking_relative_error_records_it_in_recompute_output(self):
        self.app._relative_error_var.set(False)
        self.app._on_recompute()
        summary_path = tuning._summary_json_path(self.app.run_dir, tuning.TUNING_OUTPUT_SUFFIX)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertIs(summary["fit_relative_error"], False)
        self.assertIn("Weighting: ABSOLUTE", self.app._metrics_var.get())

    @mock.patch("ca_fft_tuning_gui.messagebox.showwarning")
    @mock.patch("ca_fft_tuning_gui.monte_carlo_param_errors")
    def test_monte_carlo_uses_the_fit_that_produced_it_not_a_since_toggled_checkbox(self, mock_mc, _mock_showwarning):
        # Recompute with relative_error unchecked, then flip the checkbox
        # back on BEFORE clicking Monte Carlo -- Monte Carlo must still use
        # the (absolute/unweighted) setting that actually produced the
        # displayed fit, not the checkbox's current state, or it would be
        # resampling residuals inconsistent with the fit that generated them.
        # monte_carlo_param_errors mocked out (rather than letting the real
        # bootstrap run) since what's under test here is which arguments
        # _on_monte_carlo passes, not the stochastic refit outcome.
        mock_mc.return_value = None
        self.app._relative_error_var.set(False)
        self.app._on_recompute()
        self.app._relative_error_var.set(True)  # toggled back after Recompute
        self.app._mc_n_var.set("6")
        self.app._on_monte_carlo()
        mock_mc.assert_called_once()
        self.assertFalse(mock_mc.call_args.kwargs["relative_error"])

    def test_recompute_writes_tuning_preview_and_updates_metrics(self):
        self.app._on_recompute()
        matches = list(self.tmpdir.glob(f"nyquist_equal_aspect_*_{tuning.TUNING_OUTPUT_SUFFIX}.png"))
        self.assertTrue(matches, "expected a tuning_preview Nyquist PNG to be written")
        self.assertTrue(
            (self.tmpdir / tuning.CA_TRACE_PREVIEW_FILENAME).exists(),
            "expected a raw-vs-processed CA trace comparison PNG to be written",
        )
        metrics_text = self.app._metrics_var.get()
        self.assertIn("Fit quality score", metrics_text)
        self.assertIn("Rs:", metrics_text)
        self.assertIn("RQ1:", metrics_text)

    def test_recompute_shows_rq_breakdown_for_non_rrqrq_models_too(self):
        # RQRQ has no separate Rs term (see EIS_Fitting.py's MODEL_PARAM_KEYS)
        # -- the results panel must still show the RQ1/RQ2 breakdown for it,
        # not just for RRQRQ.
        self.app._vars["fit_model"].set("RQRQ")
        self.app._on_recompute()
        metrics_text = self.app._metrics_var.get()
        self.assertIn("Fit model: RQRQ", metrics_text)
        self.assertIn("RQ1:", metrics_text)
        self.assertIn("RQ2:", metrics_text)
        self.assertNotIn("no RQ-element breakdown available", metrics_text)

    def test_save_params_writes_json_file(self):
        before = set(self.tmpdir.glob("tuning_params_*.json"))
        self.app._on_save_params()
        after = set(self.tmpdir.glob("tuning_params_*.json"))
        self.assertEqual(len(after - before), 1)

    def test_single_folder_has_no_navigable_siblings(self):
        # Only row001_sample lives under parent_dir, so it's its own sole
        # entry in the sibling list -- Prev/Next should both be disabled.
        self.assertEqual(self.app._siblings, [self.tmpdir])
        self.assertEqual(self.app._sibling_index, 0)
        self.assertEqual(str(self.app._prev_btn["state"]), "disabled")
        self.assertEqual(str(self.app._next_btn["state"]), "disabled")

    @mock.patch("ca_fft_tuning_gui.messagebox.showerror")
    def test_monte_carlo_guarded_without_prior_recompute(self, mock_showerror):
        # self._last_result is None until a Recompute has run -- Monte Carlo
        # resamples that fit's own residuals, so there's nothing to run yet.
        self.assertIsNone(self.app._last_result)
        self.app._on_monte_carlo()
        mock_showerror.assert_called_once()
        self.assertIn("Recompute", mock_showerror.call_args[0][1])

    def test_monte_carlo_after_recompute_merges_fields_and_updates_panel(self):
        self.app._on_recompute()
        self.assertIsNotNone(self.app._last_result)
        summary_path = tuning._summary_json_path(self.app.run_dir, tuning.TUNING_OUTPUT_SUFFIX)
        before = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertNotIn("mc_n_iterations", before)

        # Small N here only to keep this test fast -- production default is
        # tuning.DEFAULT_MC_ITERATIONS (30). A few points above
        # marc.MC_MIN_SUCCESSFUL_REFITS (5) as margin in case a bootstrap
        # refit or two doesn't converge.
        self.app._mc_n_var.set("8")
        self.app._on_monte_carlo()

        metrics_text = self.app._metrics_var.get()
        self.assertIn("[MC +/-", metrics_text)

        after = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(after["mc_n_iterations"], 8)
        self.assertGreaterEqual(after["mc_n_success"], 5)
        self.assertIn("Rs (Ohm) Monte Carlo error", after["fit"])
        self.assertIn("Rs_mc_err_ohm", after["rrqrq_components"])
        # Values/Jacobian-based fields from the original Recompute must
        # survive the merge untouched, not get clobbered.
        self.assertEqual(after["fit_model"], before["fit_model"])
        self.assertIn("Rs_err_ohm", after["rrqrq_components"])

    @mock.patch("ca_fft_tuning_gui.messagebox.showerror")
    def test_monte_carlo_rejects_invalid_n(self, mock_showerror):
        self.app._on_recompute()
        self.app._mc_n_var.set("not-a-number")
        self.app._on_monte_carlo()
        mock_showerror.assert_called_once()

    @mock.patch("ca_fft_tuning_gui.messagebox.showerror")
    @mock.patch("ca_fft_tuning_gui.monte_carlo_param_errors")
    def test_monte_carlo_surfaces_exception_instead_of_failing_silently(self, mock_mc, mock_showerror):
        # Regression test: a button callback that raises is otherwise
        # swallowed by Tkinter's default exception handler (traceback to
        # stderr only) -- from the GUI it looks exactly like "the button
        # did nothing." _on_monte_carlo must catch and report this.
        self.app._on_recompute()
        mock_mc.side_effect = RuntimeError("boom")
        self.app._mc_n_var.set("5")
        self.app._on_monte_carlo()  # must not raise
        mock_showerror.assert_called_once()
        self.assertIn("boom", mock_showerror.call_args[0][1])
        self.assertIn("failed", self.app._status_var.get().lower())

    @mock.patch("ca_fft_tuning_gui.messagebox.showwarning")
    @mock.patch("ca_fft_tuning_gui.monte_carlo_param_errors")
    def test_monte_carlo_none_result_shows_warning_dialog(self, mock_mc, mock_showwarning):
        # Too few successful refits used to only update a small status label
        # at the bottom of a dense panel -- easy to miss and indistinguishable
        # from "nothing happened". Must also show a dialog.
        self.app._on_recompute()
        mock_mc.return_value = None
        self.app._mc_n_var.set("5")
        self.app._on_monte_carlo()
        mock_showwarning.assert_called_once()


class CAFFTTuningWindowNavigationTests(unittest.TestCase):
    def setUp(self):
        self.parent_dir = Path(tempfile.mkdtemp()).resolve()
        self.row_dirs = []
        for name in ("row001_a", "row002_b", "row003_c"):
            row_dir = self.parent_dir / name
            row_dir.mkdir()
            _build_synthetic_run_dir(row_dir)
            self.row_dirs.append(row_dir)
        # Open on the middle folder so both Prev and Next are exercised.
        self.app = tuning.CAFFTTuningWindow(self.row_dirs[1])
        self.app.withdraw()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        shutil.rmtree(self.parent_dir, ignore_errors=True)

    def test_siblings_detected_sorted_and_indexed(self):
        self.assertEqual(self.app._siblings, self.row_dirs)
        self.assertEqual(self.app._sibling_index, 1)
        self.assertEqual(str(self.app._prev_btn["state"]), "normal")
        self.assertEqual(str(self.app._next_btn["state"]), "normal")

    def test_prev_and_next_navigate_and_disable_at_bounds(self):
        self.app._on_prev()
        self.assertEqual(self.app.run_dir, self.row_dirs[0])
        self.assertEqual(str(self.app._prev_btn["state"]), "disabled")
        self.assertEqual(str(self.app._next_btn["state"]), "normal")

        self.app._on_next()
        self.app._on_next()
        self.assertEqual(self.app.run_dir, self.row_dirs[2])
        self.assertEqual(str(self.app._prev_btn["state"]), "normal")
        self.assertEqual(str(self.app._next_btn["state"]), "disabled")

    def test_navigate_keeps_current_parameter_values(self):
        distinctive = str(tuning.DEFAULT_PARAMS["zero_pad_factor"] + 7)
        self.app._vars["zero_pad_factor"].set(distinctive)
        self.app._on_next()
        self.assertEqual(self.app.run_dir, self.row_dirs[2])
        self.assertEqual(self.app._vars["zero_pad_factor"].get(), distinctive)

    def test_navigate_auto_recomputes_for_the_new_folder(self):
        self.app._on_next()
        matches = list(self.row_dirs[2].glob(f"nyquist_equal_aspect_*_{tuning.TUNING_OUTPUT_SUFFIX}.png"))
        self.assertTrue(matches, "expected navigation to auto-recompute against the new folder")

    def test_position_label_updates_on_navigate(self):
        self.assertEqual(self.app._goto_var.get(), "2")
        self.assertIn("of 3", self.app._goto_of_var.get())
        self.assertEqual(self.app._position_var.get(), self.row_dirs[1].name)
        self.app._on_prev()
        self.assertEqual(self.app._goto_var.get(), "1")
        self.assertEqual(self.app._position_var.get(), self.row_dirs[0].name)

    def test_goto_jumps_directly_to_typed_row_number(self):
        self.app._goto_var.set("3")
        self.app._on_goto()
        self.assertEqual(self.app.run_dir, self.row_dirs[2])
        matches = list(self.row_dirs[2].glob(f"nyquist_equal_aspect_*_{tuning.TUNING_OUTPUT_SUFFIX}.png"))
        self.assertTrue(matches, "expected Go to auto-recompute against the new folder")

    def test_goto_rejects_out_of_range_and_non_numeric_input(self):
        self.app._goto_var.set("99")
        with mock.patch("ca_fft_tuning_gui.messagebox.showerror") as mock_showerror:
            self.app._on_goto()
        mock_showerror.assert_called_once()
        self.assertEqual(self.app.run_dir, self.row_dirs[1])  # unchanged
        self.assertEqual(self.app._goto_var.get(), "2")  # reset to current position

        self.app._goto_var.set("abc")
        with mock.patch("ca_fft_tuning_gui.messagebox.showerror") as mock_showerror:
            self.app._on_goto()
        mock_showerror.assert_called_once()
        self.assertEqual(self.app.run_dir, self.row_dirs[1])

    @mock.patch("ca_fft_tuning_gui.messagebox.showinfo")
    @mock.patch("ca_fft_tuning_gui.messagebox.askyesno", return_value=True)
    def test_save_fits_writes_official_summary_in_every_sibling(self, _askyesno, _showinfo):
        self.app._on_save_fits()
        for row_dir in self.row_dirs:
            summary_path = row_dir / "full_arc_summary.json"
            self.assertTrue(summary_path.exists(), f"expected {summary_path} to be written")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary.get("manual_override"))
            self.assertEqual(summary.get("manual_override_source"), "ca_fft_tuning_gui_batch_save")
            # The automated hot/relaxed10x30/zp30 variant files are a
            # separate pipeline (main() in make_olecom_full_arc_from_run.py)
            # -- Save fits must not create or touch them.
            self.assertFalse((row_dir / "full_arc_summary_hot.json").exists())

    @mock.patch("ca_fft_tuning_gui.messagebox.showinfo")
    @mock.patch("ca_fft_tuning_gui.messagebox.askyesno", return_value=True)
    def test_save_fits_skips_broken_folder_and_reports_it(self, _askyesno, mock_showinfo):
        broken_dir = self.parent_dir / "row004_broken"
        broken_dir.mkdir()
        (broken_dir / "measured_peis.txt").write_text(
            "freq_Hz\tReZ_ohm\tnegImZ_ohm\n", encoding="utf-8"
        )
        self.app._load_run_dir(self.row_dirs[1])  # refresh self._siblings to include it

        self.app._on_save_fits()

        self.assertFalse((broken_dir / "full_arc_summary.json").exists())
        for row_dir in self.row_dirs:
            self.assertTrue((row_dir / "full_arc_summary.json").exists())
        summary_text = mock_showinfo.call_args[0][1]
        self.assertIn("Saved 3/4", summary_text)
        self.assertIn("row004_broken", summary_text)

    @mock.patch("ca_fft_tuning_gui.messagebox.askyesno", return_value=False)
    def test_save_fits_does_nothing_if_confirmation_declined(self, _askyesno):
        self.app._on_save_fits()
        for row_dir in self.row_dirs:
            self.assertFalse((row_dir / "full_arc_summary.json").exists())

    @mock.patch("ca_fft_tuning_gui.messagebox.showinfo")
    @mock.patch("ca_fft_tuning_gui.messagebox.askyesno", return_value=True)
    def test_save_fits_with_monte_carlo_writes_mc_fields_in_every_sibling(self, _askyesno, _showinfo):
        # Small N here only to keep test runtime reasonable -- production
        # default is tuning.DEFAULT_MC_ITERATIONS (30). Below
        # marc.MC_MIN_SUCCESSFUL_REFITS (5) would make every folder fail MC,
        # so use enough headroom that a folder or two failing to converge
        # still leaves >=5 successes.
        self.app._mc_n_var.set("8")
        self.app._on_save_fits_with_monte_carlo()
        for row_dir in self.row_dirs:
            summary_path = row_dir / "full_arc_summary.json"
            self.assertTrue(summary_path.exists(), f"expected {summary_path} to be written")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            # The normal batch-refit fields must still be there (Monte Carlo
            # is additive, not a replacement for the regular Save fits path).
            self.assertTrue(summary.get("manual_override"))
            self.assertEqual(summary.get("manual_override_source"), "ca_fft_tuning_gui_batch_save_monte_carlo")
            self.assertEqual(summary.get("mc_n_iterations"), 8)
            self.assertGreaterEqual(summary.get("mc_n_success", 0), 5)
            self.assertIn("Rs (Ohm) Monte Carlo error", summary.get("fit", {}))
            if summary.get("fit_model") == "RRQRQ":
                self.assertIn("Rs_mc_err_ohm", summary.get("rrqrq_components", {}))

    @mock.patch("ca_fft_tuning_gui.messagebox.showerror")
    def test_save_fits_with_monte_carlo_rejects_invalid_n(self, mock_showerror):
        self.app._mc_n_var.set("not-a-number")
        self.app._on_save_fits_with_monte_carlo()
        mock_showerror.assert_called_once()
        for row_dir in self.row_dirs:
            self.assertFalse((row_dir / "full_arc_summary.json").exists())

    @mock.patch("ca_fft_tuning_gui.monte_carlo_param_errors")
    @mock.patch("ca_fft_tuning_gui.messagebox.showinfo")
    @mock.patch("ca_fft_tuning_gui.messagebox.askyesno", return_value=True)
    def test_save_fits_with_monte_carlo_passes_weighting_and_bins_through(self, _askyesno, _showinfo, mock_mc):
        # Mocked (rather than a real stochastic bootstrap) since what's under
        # test is which arguments the batch action passes through, not the
        # refit outcome -- mirrors the equivalent single-row test.
        mock_mc.return_value = None
        self.app._relative_error_var.set(False)
        self.app._mc_n_var.set("6")
        self.app._mc_n_freq_bins_var.set("2")
        self.app._on_save_fits_with_monte_carlo()
        self.assertEqual(mock_mc.call_count, len(self.row_dirs))
        for call in mock_mc.call_args_list:
            self.assertFalse(call.kwargs["relative_error"])
            self.assertEqual(call.kwargs["n_freq_bins"], 2)
            self.assertEqual(call.kwargs["n_iterations"], 6)


if __name__ == "__main__":
    unittest.main()
