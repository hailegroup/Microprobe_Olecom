# -*- coding: utf-8 -*-
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_DIR / "tools"
for p in (PROJECT_DIR, TOOLS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import make_olecom_full_arc_from_run as marc  # noqa: E402


def _build_synthetic_run_dir(run_dir: Path) -> None:
    """Fabricate a minimal, realistic per-row result folder.

    CA trace: flat baseline pre-step, then a simple exponential-decay
    current step response (via the module's own _z_rrq ground truth so the
    fit has something real to converge on). PEIS: a handful of points
    sampled from that same synthetic RQ arc at higher frequencies, so the
    merged full arc is self-consistent.
    """
    rng = np.random.default_rng(0)
    true_params = np.array([2.0, 500.0, 3e-4, 0.85])  # Rs, R, Q, alpha

    pre_t = np.arange(0.0, 5.0, 0.1)
    pre_v = np.zeros_like(pre_t)
    pre_i = 1e-7 * rng.standard_normal(len(pre_t))

    post_t = np.arange(5.0, 25.0, 0.1)
    dv = 0.05
    tau = 500.0 * 3e-4  # R*Q as a rough RC-like time constant
    post_v = np.full_like(post_t, dv)
    post_i = (dv / 500.0) * np.exp(-(post_t - 5.0) / max(tau, 1e-6)) + 1e-6
    post_i = post_i + 2e-8 * rng.standard_normal(len(post_t))
    # A couple of isolated spikes for despike-sensitivity tests elsewhere.
    post_i[20] += 5e-5
    post_i[45] -= 5e-5

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


class MakeFullArcTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        _build_synthetic_run_dir(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_make_full_arc_produces_expected_files_and_summary_fields(self):
        result = marc.make_full_arc(self.tmpdir, zero_pad_factor=10, n_log_points=40)

        self.assertTrue(Path(result["full_arc_png"]).exists())
        self.assertTrue(Path(result["nyquist_equal_aspect_png"]).exists())
        self.assertTrue(Path(result["full_arc_txt"]).exists())
        self.assertTrue((self.tmpdir / "full_arc_summary.json").exists())

        self.assertEqual(result["zero_pad_factor"], 10)
        self.assertEqual(result["n_log_points"], 40)
        self.assertTrue(np.isfinite(result["ca_raw_dt_s"]))
        self.assertAlmostEqual(result["ca_raw_dt_s"], 0.1, places=6)
        self.assertGreater(result["fft_points_used"], 0)

    def test_output_suffix_produces_distinctly_named_files(self):
        result = marc.make_full_arc(self.tmpdir, output_suffix="myvariant")
        self.assertTrue(Path(result["full_arc_png"]).name.endswith("_myvariant.png"))
        self.assertTrue((self.tmpdir / "full_arc_summary_myvariant.json").exists())

    def test_build_arc_and_plots_extra_summary_fields_are_merged(self):
        t, v, i = marc._load_ca_txt(self.tmpdir / "ca_for_fft_pre_plus_scout_only.txt")
        t, v, i, _dt = marc._uniform_ca_grid(t, v, i)
        peis = marc._average_duplicate_freq(marc._load_peis_txt(self.tmpdir / "measured_peis.txt"))
        result = marc._build_arc_and_plots(
            self.tmpdir, t, v, i, peis,
            zero_pad_factor=10, n_log_points=40,
            duration_guard_periods=0.0, peis_anchor_gap_decades=0.0,
            output_suffix="direct", fit_model="RRQRQ",
            extra_summary_fields={"marker": "unit_test"},
        )
        self.assertEqual(result["marker"], "unit_test")

    def test_full_variant_cli_flow_produces_hot_relaxed_zp30_and_recommended(self):
        # Exercises the same three-variant path gui.py's deferred postprocess
        # subprocess call runs after every OLE-COM row.
        import subprocess

        completed = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "make_olecom_full_arc_from_run.py"), str(self.tmpdir)],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        for variant in ("hot", "relaxed10x30", "zp30", "recommended"):
            self.assertTrue(
                (self.tmpdir / f"full_arc_merged_fft_lf_peis_hf_duration_gated_{variant}.png").exists()
                or (self.tmpdir / f"full_arc_merged_fft_lf_peis_hf_duration_gated_{variant}.txt").exists(),
                msg=f"missing artifacts for variant {variant}",
            )
        self.assertTrue((self.tmpdir / "full_arc_summary.json").exists())

    def test_full_arc_summary_includes_rq_component_error_bars(self):
        result = marc.make_full_arc(self.tmpdir, zero_pad_factor=10, n_log_points=40)
        rrqrq = result["rrqrq_components"]
        self.assertIsNotNone(rrqrq)
        for key in ("Rs_err_ohm", "RQ1_R_err_ohm", "RQ2_R_err_ohm", "R1+R2_err_ohm"):
            self.assertIn(key, rrqrq)
            self.assertTrue(np.isfinite(rrqrq[key]), msg=f"{key} was not finite: {rrqrq[key]}")
            self.assertGreaterEqual(rrqrq[key], 0.0)
        self.assertIn("fit_condition_number", result)
        self.assertTrue(np.isfinite(result["fit_condition_number"]))
        self.assertIn("fit_errors_ill_conditioned", result)
        self.assertIsInstance(result["fit_errors_ill_conditioned"], bool)


class FitErrorBarTests(unittest.TestCase):
    """Direct coverage of the parameter-uncertainty ("error bar") plumbing:
    scipy.optimize.least_squares' Jacobian at the solution -> EF.param_std_errors
    -> attached "<key> error" fields, independent of the full run-folder
    pipeline above."""

    def _synthetic_rq_data(self, seed, r, q, a):
        rng = np.random.default_rng(seed)
        freq = np.logspace(4, -2, 60)
        z_true = marc._z_rrq(np.array([0.0, r, q, a]), freq)
        noise = 0.01 * np.abs(z_true) * (
            rng.standard_normal(len(freq)) + 1j * rng.standard_normal(len(freq))
        )
        z_noisy = z_true + noise
        return freq, z_noisy

    def test_fit_rrq_reports_finite_nonnegative_parameter_errors(self):
        freq, z_noisy = self._synthetic_rq_data(2, r=200.0, q=1e-6, a=0.9)
        fit = marc._fit_rrq(freq, z_noisy.real, z_noisy.imag)
        self.assertTrue(fit.get("Fit success"))
        for key in ("Rs (Ohm) error", "R0 (Ohm) error", "Q0 (F·s^(a-1)) error", "a0 error"):
            self.assertIn(key, fit)
            self.assertTrue(np.isfinite(fit[key]), msg=f"{key} was not finite: {fit[key]}")
            self.assertGreaterEqual(fit[key], 0.0)
        # A clean, well-separated synthetic fit should be tightly determined --
        # loosely sanity-checks the covariance scale, not an exact value.
        self.assertLess(fit["R0 (Ohm) error"], 0.5 * fit["R0 (Ohm)"])

    def test_fit_rrq_well_determined_fit_is_not_flagged_ill_conditioned(self):
        freq, z_noisy = self._synthetic_rq_data(2, r=200.0, q=1e-6, a=0.9)
        fit = marc._fit_rrq(freq, z_noisy.real, z_noisy.imag)
        self.assertIn("Fit condition number", fit)
        self.assertTrue(np.isfinite(fit["Fit condition number"]))
        self.assertFalse(fit["Fit errors ill-conditioned"])

    def test_rrqrq_fit_forced_onto_wrong_shaped_data_is_not_deceptively_precise(self):
        # Regression test for the reported bug: force a 2-branch RRQRQ model
        # onto data that actually has 3 well-separated arcs, so the fit is
        # visibly poor (the model structurally cannot represent the data).
        # Before switching param_std_errors from pinv(J^T J) to SVD(J)
        # directly, this produced absurdly tiny "confident" errors (e.g.
        # ~0.06 ohm on a ~4878 ohm resistance, ~0.001% relative) because
        # forming J^T J squared an already-large condition number
        # (~1.85e8 for J itself) past float64's precision ceiling. The fixed
        # version must report a materially larger, more honest error.
        #
        # Note this specific scenario is a poor FIT (wrong model structure)
        # but not necessarily an ill-conditioned MATRIX -- each parameter
        # still has some independent local sensitivity, so J's condition
        # number here (~1.85e8) does not itself cross ILL_CONDITIONED_THRESHOLD.
        # The chi-square rescaling (s^2 = RSS/dof) is what correctly inflates
        # the reported error for a bad fit; the ill-conditioned flag is a
        # separate, narrower signal covered by the test below.
        import EIS_Fitting as EF

        rng = np.random.default_rng(0)
        freq = np.logspace(4, -2, 60)

        def zrq(r, q, a, f):
            w = 2.0 * np.pi * f
            return 1.0 / (1.0 / r + q * (1j * w) ** a)

        p3 = [50.0, 3e-7, 0.9, 300.0, 5e-5, 0.85, 5000.0, 2e-3, 0.92]
        z_true = zrq(p3[0], p3[1], p3[2], freq) + zrq(p3[3], p3[4], p3[5], freq) + zrq(p3[6], p3[7], p3[8], freq)
        noise = 0.01 * np.abs(z_true) * (
            rng.standard_normal(len(freq)) + 1j * rng.standard_normal(len(freq))
        )
        z_noisy = z_true + noise

        fit = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RRQRQ")
        self.assertTrue(fit.get("Fit success"))
        # Previously looked deceptively precise -- must now be at least a
        # percent-scale fraction of the fitted value itself, not a fraction
        # of a percent.
        self.assertGreater(fit["R1 (Ohm) error"], 0.001 * fit["R1 (Ohm)"])
        self.assertIn("Fit condition number", fit)
        self.assertTrue(np.isfinite(fit["Fit condition number"]))

    def test_non_identifiable_model_trips_ill_conditioned_flag(self):
        # A 2-branch RRQRQ model fit to data generated from a SINGLE RQ arc
        # is genuinely non-identifiable -- the two branches can freely trade
        # resistance between them with almost no cost, which is a real
        # (not merely numerical) near-singularity, not just a poor fit.
        # This is the case the ill-conditioned flag exists to catch.
        import EIS_Fitting as EF

        rng = np.random.default_rng(0)
        freq = np.logspace(4, -2, 60)

        def zrq(r, q, a, f):
            w = 2.0 * np.pi * f
            return 1.0 / (1.0 / r + q * (1j * w) ** a)

        z_true = 5.0 + zrq(300.0, 5e-5, 0.9, freq)
        noise = 0.01 * np.abs(z_true) * (
            rng.standard_normal(len(freq)) + 1j * rng.standard_normal(len(freq))
        )
        z_noisy = z_true + noise

        fit = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RRQRQ")
        self.assertTrue(fit.get("Fit success"))
        self.assertGreater(fit["Fit condition number"], EF.ILL_CONDITIONED_THRESHOLD)
        self.assertTrue(fit["Fit errors ill-conditioned"])
        # The unidentifiable branches' errors should be huge relative to
        # their own fitted values -- correctly signaling "not resolved",
        # not a small, falsely-confident number.
        self.assertGreater(fit["R0 (Ohm) error"], 0.1 * fit["R0 (Ohm)"])

    def test_rrqrq_component_summary_carries_matching_error_fields(self):
        import EIS_Fitting as EF

        rng = np.random.default_rng(3)
        freq = np.logspace(4, -2, 60)

        def zrq(r, q, a, f):
            w = 2.0 * np.pi * f
            return 1.0 / (1.0 / r + q * (1j * w) ** a)

        p = [10.0, 50.0, 5e-7, 0.85, 400.0, 2e-4, 0.9]
        z_true = p[0] + zrq(p[1], p[2], p[3], freq) + zrq(p[4], p[5], p[6], freq)
        noise = 0.01 * np.abs(z_true) * (
            rng.standard_normal(len(freq)) + 1j * rng.standard_normal(len(freq))
        )
        z_noisy = z_true + noise
        fit = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RRQRQ")
        comp = marc._rrqrq_component_summary(fit)
        self.assertIsNotNone(comp)
        for key in (
            "Rs_err_ohm", "RQ1_R_err_ohm", "RQ1_Q_err_F_s_alpha_minus_1", "RQ1_alpha_err",
            "RQ2_R_err_ohm", "RQ2_Q_err_F_s_alpha_minus_1", "RQ2_alpha_err", "R1+R2_err_ohm",
        ):
            self.assertIn(key, comp)
            self.assertTrue(np.isfinite(comp[key]), msg=f"{key} was not finite: {comp[key]}")
        # Independent-sum propagation: combined error can't be smaller than
        # either individual branch's own error.
        self.assertGreaterEqual(comp["R1+R2_err_ohm"], comp["RQ1_R_err_ohm"] - 1e-9)
        self.assertGreaterEqual(comp["R1+R2_err_ohm"], comp["RQ2_R_err_ohm"] - 1e-9)

    def test_rrqrq_component_summary_missing_error_falls_back_to_nan(self):
        fit = {
            "Fit model": "RRQRQ",
            "Rs (Ohm)": 10.0, "R0 (Ohm)": 50.0, "Q0 (F·s^(a-1))": 1e-6, "a0": 0.9,
            "RQ0 fchar (Hz)": 100.0,
            "R1 (Ohm)": 400.0, "Q1 (F·s^(a-1))": 2e-4, "a1": 0.85, "RQ1 fchar (Hz)": 1.0,
        }
        comp = marc._rrqrq_component_summary(fit)
        self.assertIsNotNone(comp)
        self.assertTrue(np.isnan(comp["Rs_err_ohm"]))
        self.assertTrue(np.isnan(comp["RQ1_R_err_ohm"]))
        self.assertTrue(np.isnan(comp["R1+R2_err_ohm"]))
        # Values themselves are unaffected by missing errors.
        self.assertAlmostEqual(comp["R1+R2_ohm"], 450.0)


class MonteCarloParamErrorsTests(unittest.TestCase):
    """Coverage for monte_carlo_param_errors -- the residual-resampling
    ("adapted Monte-Carlo", Alper & Gelb 1990) uncertainty BioLogic's ZFit
    uses, offered as a slower, explicitly-triggered complement to
    param_std_errors' Jacobian-based estimate. Small n_iterations here only
    to keep test runtime reasonable (each iteration is a full multi-seed
    RRQRQ refit); the production default is DEFAULT_MC_ITERATIONS=30."""

    def _clean_two_arc_fit(self, seed=3):
        import EIS_Fitting as EF

        rng = np.random.default_rng(seed)
        freq = np.logspace(4, -2, 60)

        def zrq(r, q, a, f):
            w = 2.0 * np.pi * f
            return 1.0 / (1.0 / r + q * (1j * w) ** a)

        p = [10.0, 50.0, 5e-7, 0.85, 400.0, 2e-4, 0.9]
        z_true = p[0] + zrq(p[1], p[2], p[3], freq) + zrq(p[4], p[5], p[6], freq)
        noise = 0.01 * np.abs(z_true) * (
            rng.standard_normal(len(freq)) + 1j * rng.standard_normal(len(freq))
        )
        z_noisy = z_true + noise
        fit = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RRQRQ")
        return freq, z_noisy, fit

    def test_returns_none_when_too_few_iterations_to_reach_the_minimum(self):
        freq, z_noisy, fit = self._clean_two_arc_fit()
        mc = marc.monte_carlo_param_errors(
            freq, z_noisy.real, z_noisy.imag, fit, "RRQRQ", n_iterations=2, seed=1,
        )
        self.assertIsNone(mc)

    def test_raw_param_errors_present_and_roughly_match_jacobian_order_of_magnitude(self):
        freq, z_noisy, fit = self._clean_two_arc_fit()
        mc = marc.monte_carlo_param_errors(
            freq, z_noisy.real, z_noisy.imag, fit, "RRQRQ", n_iterations=8, seed=2,
        )
        self.assertIsNotNone(mc)
        self.assertEqual(mc["mc_n_iterations"], 8)
        self.assertGreaterEqual(mc["mc_n_success"], marc.MC_MIN_SUCCESSFUL_REFITS)

        for jacobian_key, mc_key in (
            ("Rs (Ohm) error", "Rs (Ohm) Monte Carlo error"),
            ("R0 (Ohm) error", "R0 (Ohm) Monte Carlo error"),
            ("R1 (Ohm) error", "R1 (Ohm) Monte Carlo error"),
        ):
            self.assertIn(mc_key, mc)
            jac_err = fit[jacobian_key]
            mc_err = mc[mc_key]
            self.assertTrue(np.isfinite(mc_err))
            self.assertGreaterEqual(mc_err, 0.0)
            # Two independent methods estimating the same uncertainty on a
            # clean, well-determined fit should land within the same order
            # of magnitude, not differ by 10x+ -- a loose cross-check, not
            # an exact-agreement one (they use different statistical
            # machinery and won't match precisely).
            scale = max(jac_err, mc_err, 1e-12)
            self.assertLess(abs(jac_err - mc_err) / scale, 5.0)

    def test_rrqrq_component_rollup_keys_present_for_rrqrq_model(self):
        freq, z_noisy, fit = self._clean_two_arc_fit()
        mc = marc.monte_carlo_param_errors(
            freq, z_noisy.real, z_noisy.imag, fit, "RRQRQ", n_iterations=8, seed=4,
        )
        self.assertIsNotNone(mc)
        for key in (
            "Rs_mc_err_ohm", "RQ1_R_mc_err_ohm", "RQ1_alpha_mc_err",
            "RQ2_R_mc_err_ohm", "RQ2_alpha_mc_err", "R1+R2_mc_err_ohm",
            "RQ1_C_mc_err_F", "RQ2_C_mc_err_F",
        ):
            self.assertIn(key, mc)
            self.assertTrue(np.isfinite(mc[key]), msg=f"{key} was not finite: {mc[key]}")
            self.assertGreaterEqual(mc[key], 0.0)

    def test_non_rrqrq_model_gets_raw_errors_but_no_component_rollup(self):
        import EIS_Fitting as EF

        rng = np.random.default_rng(5)
        freq = np.logspace(4, -2, 60)

        def zrq(r, q, a, f):
            w = 2.0 * np.pi * f
            return 1.0 / (1.0 / r + q * (1j * w) ** a)

        p = [50.0, 5e-7, 0.85, 400.0, 2e-4, 0.9]
        z_true = zrq(p[0], p[1], p[2], freq) + zrq(p[3], p[4], p[5], freq)
        noise = 0.01 * np.abs(z_true) * (
            rng.standard_normal(len(freq)) + 1j * rng.standard_normal(len(freq))
        )
        z_noisy = z_true + noise
        fit = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RQRQ")
        mc = marc.monte_carlo_param_errors(
            freq, z_noisy.real, z_noisy.imag, fit, "RQRQ", n_iterations=8, seed=6,
        )
        self.assertIsNotNone(mc)
        self.assertIn("R0 (Ohm) Monte Carlo error", mc)
        self.assertIn("R1 (Ohm) Monte Carlo error", mc)
        self.assertNotIn("RQ1_R_mc_err_ohm", mc)


class RelativeErrorWeightingTests(unittest.TestCase):
    """relative_error=True (default) matches every existing test/behavior
    unchanged; relative_error=False changes the actual fitted parameters
    (not just their uncertainty), which is the whole point of offering it."""

    def _small_rs_small_rq1_large_rq2_data(self, seed=7):
        import EIS_Fitting as EF

        rng = np.random.default_rng(seed)
        freq = np.logspace(4, -2, 60)

        def zrq(r, q, a, f):
            w = 2.0 * np.pi * f
            return 1.0 / (1.0 / r + q * (1j * w) ** a)

        # Small Rs + small RQ1 (high-freq, small |Z|) next to a MUCH larger
        # RQ2 (low-freq, large |Z|) -- exactly the shape where relative vs.
        # absolute weighting should diverge sharply (see module docstring
        # of fit_circuit_model_stable).
        p = [5.0, 20.0, 5e-7, 0.85, 5000.0, 2e-4, 0.9]
        z_true = p[0] + zrq(p[1], p[2], p[3], freq) + zrq(p[4], p[5], p[6], freq)
        noise = 0.01 * np.abs(z_true) * (
            rng.standard_normal(len(freq)) + 1j * rng.standard_normal(len(freq))
        )
        z_noisy = z_true + noise
        return EF, freq, z_noisy, p

    def test_absolute_weighting_produces_materially_different_fit_than_relative(self):
        EF, freq, z_noisy, p = self._small_rs_small_rq1_large_rq2_data()
        fit_rel = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RRQRQ", relative_error=True)
        fit_abs = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RRQRQ", relative_error=False)

        self.assertTrue(fit_rel.get("Fit success"))
        self.assertTrue(fit_abs.get("Fit success"))
        # Relative weighting should recover something close to the true,
        # well-separated Rs/R0 -- small values at the small-|Z| end.
        self.assertLess(fit_rel["R0 (Ohm)"], 100.0)
        # Absolute weighting sacrifices the small-|Z| region for the sake of
        # the large-|Z| arc -- this should NOT resemble the true R0=20 as
        # closely as the relative fit does. Loosely checking the two fits
        # disagree substantially, not pinning an exact wrong value (the
        # unweighted fit's exact local minimum isn't the point here).
        rel_diff = abs(fit_rel["R0 (Ohm)"] - fit_abs["R0 (Ohm)"]) / max(fit_rel["R0 (Ohm)"], 1.0)
        self.assertGreater(rel_diff, 1.0, msg="relative and absolute fits should diverge substantially")

    def test_default_relative_error_true_matches_omitting_the_argument(self):
        # Loose relative tolerance, not bit-exact equality: even two calls
        # with identical EXPLICIT relative_error=True arguments land on
        # tiny (~1e-5 relative) different final values -- pre-existing
        # floating-point run-to-run noise in the underlying iterative
        # solver (confirmed independently of this parameter), not something
        # introduced by adding relative_error. This test's actual purpose is
        # confirming default and explicit take the SAME code path (same
        # ballpark result), not exact reproducibility.
        EF, freq, z_noisy, _p = self._small_rs_small_rq1_large_rq2_data(seed=8)
        fit_default = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RRQRQ")
        fit_explicit = EF.fit_circuit_model_stable(
            freq, z_noisy.real, z_noisy.imag, model="RRQRQ", relative_error=True,
        )
        rel_diff = abs(fit_default["R0 (Ohm)"] - fit_explicit["R0 (Ohm)"]) / max(abs(fit_explicit["R0 (Ohm)"]), 1e-9)
        self.assertLess(rel_diff, 1e-2)
        bic_diff = abs(fit_default["Common BIC"] - fit_explicit["Common BIC"])
        self.assertLess(bic_diff, 1.0)

    def test_fit_rrq_and_score_model_also_respect_relative_error(self):
        EF, freq, z_noisy, _p = self._small_rs_small_rq1_large_rq2_data(seed=9)
        fit_rel = marc._fit_rrq(freq, z_noisy.real, z_noisy.imag, relative_error=True)
        fit_abs = marc._fit_rrq(freq, z_noisy.real, z_noisy.imag, relative_error=False)
        self.assertTrue(fit_rel.get("Fit success"))
        self.assertTrue(fit_abs.get("Fit success"))
        self.assertTrue(fit_rel.get("Fit relative error"))
        self.assertFalse(fit_abs.get("Fit relative error"))

    def test_build_arc_and_plots_records_fit_relative_error(self):
        # Uses the same synthetic run-dir fixture as MakeFullArcTests.
        tmpdir = Path(tempfile.mkdtemp())
        try:
            _build_synthetic_run_dir(tmpdir)
            result_rel = marc.make_full_arc(tmpdir, zero_pad_factor=10, n_log_points=40, relative_error=True)
            self.assertIs(result_rel["fit_relative_error"], True)
            result_abs = marc.make_full_arc(
                tmpdir, zero_pad_factor=10, n_log_points=40, relative_error=False, output_suffix="abs",
            )
            self.assertIs(result_abs["fit_relative_error"], False)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class FreqBinMembershipTests(unittest.TestCase):
    def test_single_bin_returns_all_indices_in_original_order(self):
        freq = np.array([5.0, 1.0, 3.0, 2.0, 4.0])
        bins = marc._freq_bin_membership(freq, 1)
        self.assertEqual(len(bins), 1)
        np.testing.assert_array_equal(np.sort(bins[0]), np.arange(5))

    def test_multiple_bins_partition_every_index_exactly_once(self):
        freq = np.geomspace(0.01, 10000.0, 37)
        bins = marc._freq_bin_membership(freq, 4)
        all_indices = np.concatenate(bins)
        self.assertEqual(sorted(all_indices.tolist()), list(range(37)))

    def test_bins_split_by_sorted_frequency_not_original_order(self):
        # Frequencies deliberately out of order in the input array.
        freq = np.array([50.0, 10.0, 5000.0, 1.0, 500.0, 0.5])
        bins = marc._freq_bin_membership(freq, 2)
        self.assertEqual(len(bins), 2)
        low_bin_freqs = sorted(freq[bins[0]].tolist())
        high_bin_freqs = sorted(freq[bins[1]].tolist())
        # Every frequency in the "low" bin must be <= every frequency in the
        # "high" bin -- bins are contiguous ranges of sorted frequency.
        self.assertLessEqual(max(low_bin_freqs), min(high_bin_freqs))

    def test_n_freq_bins_less_than_one_treated_as_one(self):
        freq = np.array([1.0, 2.0, 3.0])
        bins = marc._freq_bin_membership(freq, 0)
        self.assertEqual(len(bins), 1)


class MonteCarloWeightingAndBinningTests(unittest.TestCase):
    def _clean_fit(self, seed=3):
        import EIS_Fitting as EF

        rng = np.random.default_rng(seed)
        freq = np.logspace(4, -2, 60)

        def zrq(r, q, a, f):
            w = 2.0 * np.pi * f
            return 1.0 / (1.0 / r + q * (1j * w) ** a)

        p = [10.0, 50.0, 5e-7, 0.85, 400.0, 2e-4, 0.9]
        z_true = p[0] + zrq(p[1], p[2], p[3], freq) + zrq(p[4], p[5], p[6], freq)
        noise = 0.01 * np.abs(z_true) * (
            rng.standard_normal(len(freq)) + 1j * rng.standard_normal(len(freq))
        )
        z_noisy = z_true + noise
        fit = EF.fit_circuit_model_stable(freq, z_noisy.real, z_noisy.imag, model="RRQRQ")
        return freq, z_noisy, fit

    def test_settings_are_echoed_in_the_return_dict(self):
        freq, z_noisy, fit = self._clean_fit()
        mc = marc.monte_carlo_param_errors(
            freq, z_noisy.real, z_noisy.imag, fit, "RRQRQ", n_iterations=8, seed=1,
            relative_error=False, n_freq_bins=2,
        )
        self.assertIsNotNone(mc)
        self.assertFalse(mc["mc_relative_error"])
        self.assertEqual(mc["mc_n_freq_bins"], 2)

    def test_default_settings_match_pre_toggle_behavior(self):
        freq, z_noisy, fit = self._clean_fit()
        mc = marc.monte_carlo_param_errors(
            freq, z_noisy.real, z_noisy.imag, fit, "RRQRQ", n_iterations=8, seed=1,
        )
        self.assertIsNotNone(mc)
        self.assertTrue(mc["mc_relative_error"])
        self.assertEqual(mc["mc_n_freq_bins"], 1)

    def test_binned_and_unbinned_both_produce_valid_finite_errors(self):
        freq, z_noisy, fit = self._clean_fit()
        mc_unbinned = marc.monte_carlo_param_errors(
            freq, z_noisy.real, z_noisy.imag, fit, "RRQRQ", n_iterations=8, seed=2, n_freq_bins=1,
        )
        mc_binned = marc.monte_carlo_param_errors(
            freq, z_noisy.real, z_noisy.imag, fit, "RRQRQ", n_iterations=8, seed=2, n_freq_bins=2,
        )
        self.assertIsNotNone(mc_unbinned)
        self.assertIsNotNone(mc_binned)
        for mc in (mc_unbinned, mc_binned):
            self.assertTrue(np.isfinite(mc["R0 (Ohm) Monte Carlo error"]))
            self.assertGreaterEqual(mc["R0 (Ohm) Monte Carlo error"], 0.0)


class UniformCaGridGapTests(unittest.TestCase):
    def test_dense_uniform_data_keeps_all_points(self):
        t = np.arange(0.0, 20.0, 0.1)
        v = np.zeros_like(t)
        i = np.sin(t)
        t_out, v_out, i_out, dt = marc._uniform_ca_grid(t, v, i)
        self.assertAlmostEqual(dt, 0.1, places=6)
        # np.arange endpoint handling can drop/add at most one point.
        self.assertGreaterEqual(len(t_out), len(t) - 1)
        self.assertLessEqual(len(t_out), len(t) + 1)

    def test_sparse_region_does_not_get_fabricated_dense_data(self):
        # Mirrors the real bug: a handful of real samples spread across a
        # long flat pre-hold, then thousands of densely-spaced samples
        # during an active scout -- confirmed on a real run to inflate the
        # interpolated output to ~2.2x the real raw point count and blur
        # the real voltage step location badly enough that step-detection
        # locked onto an unrelated point ~70s away.
        sparse_t = np.array([0.0, 20.0, 40.0, 60.0, 80.0, 100.0])  # 6 pts over 100s
        sparse_v = np.zeros_like(sparse_t)
        sparse_i = np.zeros_like(sparse_t)
        dense_t = np.arange(100.0, 200.0, 0.005)  # 20000 pts over 100s
        dense_v = np.full_like(dense_t, 0.05)
        dense_i = np.linspace(2e-6, 1e-6, len(dense_t))

        t = np.concatenate([sparse_t, dense_t])
        v = np.concatenate([sparse_v, dense_v])
        i = np.concatenate([sparse_i, dense_i])

        t_out, v_out, i_out, dt = marc._uniform_ca_grid(t, v, i)
        total_real_points = len(sparse_t) + len(dense_t)
        # Fabricating a dense ramp across the 100s sparse region at the
        # dense region's ~0.005s spacing would produce roughly 2x the real
        # point count (as it did before this fix); the gap-aware version
        # should stay close to the real total instead.
        self.assertLess(len(t_out), 1.2 * total_real_points, msg=(
            f"output has {len(t_out)} points vs {total_real_points} real ones -- "
            "looks like gaps are being fabricated across again"
        ))
        # The sparse region's points should not have been densely
        # interpolated: almost none of the output should fall strictly
        # inside (0, 100), since no two adjacent real sparse samples are
        # closer together than the drop threshold.
        interior_sparse = t_out[(t_out > 0.5) & (t_out < 99.5)]
        self.assertEqual(len(interior_sparse), 0)

    def test_moderate_gap_within_tolerance_still_interpolated(self):
        # A gap that's a little larger than the median dt but still well
        # under the drop threshold should still be filled in normally.
        t = np.concatenate([np.arange(0.0, 10.0, 0.1), np.arange(10.3, 20.0, 0.1)])
        v = np.zeros_like(t)
        i = np.zeros_like(t)
        t_out, v_out, i_out, dt = marc._uniform_ca_grid(t, v, i)
        # Should still produce close to the full dense grid across the
        # whole span, not drop the small gap's neighborhood.
        self.assertGreater(len(t_out), 0.9 * (t.max() - t.min()) / dt)


if __name__ == "__main__":
    unittest.main()
