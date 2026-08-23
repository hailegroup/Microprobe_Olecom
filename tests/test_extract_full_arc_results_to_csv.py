# -*- coding: utf-8 -*-
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_DIR / "tools"
for p in (PROJECT_DIR, TOOLS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import extract_full_arc_results_to_csv as ex  # noqa: E402
from gui import _condition_label  # noqa: E402 -- reuse the real label encoding, don't reimplement it


def _brug_capacitance(q, r, alpha):
    return (q ** (1.0 / alpha)) * (r ** ((1.0 - alpha) / alpha))


def _write_row(
    run_dir: Path,
    *,
    row_index: int,
    electrode: int,
    temperature_c: float,
    gas_a: float,
    gas_b: float,
    v_dc: float,
    diameter_um: float,
    rrqrq_components: dict | None,
    fit_model: str = "RRQRQ",
    run_stamp: str = "20260806_172831",
    write_curated: bool = True,
    fit: dict | None = None,
    fit_condition_number: float | None = None,
    fit_errors_ill_conditioned: bool | None = None,
    mc_n_iterations: int | None = None,
    mc_n_success: int | None = None,
    fit_relative_error: bool | None = None,
    mc_relative_error: bool | None = None,
    mc_n_freq_bins: int | None = None,
) -> Path:
    label = _condition_label(
        temperature=temperature_c, gas_a=gas_a, gas_b=gas_b, electrode=electrode,
        x_mm=1.0, y_mm=2.0, z_mm=3.0, voltage=v_dc,
    )
    row_dir = run_dir / f"row{row_index:03d}_{label}_{run_stamp}"
    row_dir.mkdir(parents=True, exist_ok=True)

    full_arc_summary = {
        "fit_model": fit_model,
        "fit_score": 0.99,
        "fit_cost": 1.23,
        "recommended_full_arc_variant": "zp30_no_duration_guard",
    }
    if fit_condition_number is not None:
        full_arc_summary["fit_condition_number"] = fit_condition_number
    if fit_errors_ill_conditioned is not None:
        full_arc_summary["fit_errors_ill_conditioned"] = fit_errors_ill_conditioned
    if mc_n_iterations is not None:
        full_arc_summary["mc_n_iterations"] = mc_n_iterations
    if mc_n_success is not None:
        full_arc_summary["mc_n_success"] = mc_n_success
    if fit_relative_error is not None:
        full_arc_summary["fit_relative_error"] = fit_relative_error
    if mc_relative_error is not None:
        full_arc_summary["mc_relative_error"] = mc_relative_error
    if mc_n_freq_bins is not None:
        full_arc_summary["mc_n_freq_bins"] = mc_n_freq_bins
    if rrqrq_components is not None:
        full_arc_summary["rrqrq_components"] = rrqrq_components
    if fit is not None:
        full_arc_summary["fit"] = fit
    (row_dir / "full_arc_summary.json").write_text(json.dumps(full_arc_summary), encoding="utf-8")

    if write_curated:
        curated = {
            "label": label,
            "row_index": row_index,
            "electrode_diameter_um": diameter_um,
            "row_settings": {
                "Label": label,
                "Temperature_C": str(temperature_c),
                "GasA_setting": str(gas_a),
                "GasB_setting": str(gas_b),
                "V_dc": str(v_dc),
            },
        }
        curated_name = f"{label}_{diameter_um:.0f}um_20260806_180000.json"
        (run_dir / curated_name).write_text(json.dumps(curated), encoding="utf-8")

    return row_dir


class ExtractFullArcResultsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.run_dir = self.tmpdir / "20260806_run"
        self.run_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_happy_path_condition_metadata_and_derived_columns(self):
        rrqrq = {
            "Rs_ohm": 5.0,
            "RQ1_R_ohm": 100.0,
            "RQ1_Q_F_s_alpha_minus_1": 1e-4,
            "RQ1_alpha": 0.8,
            "RQ1_fchar_Hz": 10.0,
            "RQ1_resistance_fraction": 0.2,
            "RQ2_R_ohm": 400.0,
            "RQ2_Q_F_s_alpha_minus_1": 2e-3,
            "RQ2_alpha": 0.9,
            "RQ2_fchar_Hz": 1.0,
            "RQ2_resistance_fraction": 0.8,
            "R1+R2_ohm": 500.0,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0, rrqrq_components=rrqrq,
        )

        df, warnings = ex.extract_all(self.run_dir)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]

        self.assertEqual(row["electrode_number"], 97)
        self.assertAlmostEqual(row["temperature_C"], 600.0)
        self.assertAlmostEqual(row["gas_a_setting"], 0.0)
        self.assertAlmostEqual(row["gas_b_setting"], 20.0)
        self.assertAlmostEqual(row["bias_V_dc"], 0.0)
        self.assertAlmostEqual(row["electrode_diameter_um"], 150.0)

        expected_area_cm2 = math.pi * ((150.0 * 1e-4) / 2.0) ** 2
        self.assertAlmostEqual(row["electrode_area_cm2"], expected_area_cm2, places=12)

        expected_c1 = _brug_capacitance(1e-4, 100.0, 0.8)
        expected_c2 = _brug_capacitance(2e-3, 400.0, 0.9)
        self.assertAlmostEqual(row["RQ1_C_F"], expected_c1, places=12)
        self.assertAlmostEqual(row["RQ2_C_F"], expected_c2, places=12)

        self.assertAlmostEqual(row["R1+R2_ohm"], 100.0 + 400.0, places=9)

        self.assertAlmostEqual(row["Rs_ohm_cm2"], 5.0 * expected_area_cm2, places=12)
        self.assertAlmostEqual(row["RQ1_R_ohm_cm2"], 100.0 * expected_area_cm2, places=12)
        self.assertAlmostEqual(row["RQ2_R_ohm_cm2"], 400.0 * expected_area_cm2, places=12)
        self.assertAlmostEqual(row["R1+R2_ohm_cm2"], 500.0 * expected_area_cm2, places=9)
        self.assertAlmostEqual(row["RQ1_C_F_cm2"], expected_c1 / expected_area_cm2, places=6)
        self.assertAlmostEqual(row["RQ2_C_F_cm2"], expected_c2 / expected_area_cm2, places=6)
        self.assertEqual(warnings, [])

    def test_fit_condition_number_and_ill_conditioned_flag_columns(self):
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components={"Rs_ohm": 1.0, "R1+R2_ohm": 0.0},
            fit_condition_number=2.3e9, fit_errors_ill_conditioned=True,
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertAlmostEqual(row["fit_condition_number"], 2.3e9)
        self.assertEqual(bool(row["fit_errors_ill_conditioned"]), True)

    def test_fit_condition_number_columns_blank_when_absent(self):
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components={"Rs_ohm": 1.0, "R1+R2_ohm": 0.0},
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertTrue(pd.isna(row["fit_condition_number"]))

    def test_relative_error_weighting_columns_present(self):
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components={"Rs_ohm": 1.0, "R1+R2_ohm": 0.0},
            fit_relative_error=False, mc_relative_error=False, mc_n_freq_bins=2,
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertEqual(bool(row["fit_relative_error"]), False)
        self.assertEqual(bool(row["mc_relative_error"]), False)
        self.assertEqual(row["mc_n_freq_bins"], 2)

    def test_relative_error_weighting_columns_blank_for_legacy_json(self):
        # Predates this option -- always fit with relative weighting since no
        # alternative existed, but the column should stay honestly blank
        # rather than assume True.
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components={"Rs_ohm": 1.0, "R1+R2_ohm": 0.0},
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertTrue(pd.isna(row["fit_relative_error"]))

    def test_monte_carlo_columns_present_and_area_scaled(self):
        rrqrq = {
            "Rs_ohm": 5.0, "Rs_mc_err_ohm": 0.3,
            "RQ1_R_ohm": 100.0, "RQ1_R_mc_err_ohm": 2.0,
            "RQ1_Q_F_s_alpha_minus_1": 1e-4, "RQ1_Q_mc_err_F_s_alpha_minus_1": 5e-6,
            "RQ1_alpha": 0.8, "RQ1_alpha_mc_err": 0.02,
            "RQ2_R_ohm": 400.0, "RQ2_R_mc_err_ohm": 3.0,
            "RQ2_Q_F_s_alpha_minus_1": 2e-3, "RQ2_Q_mc_err_F_s_alpha_minus_1": 1e-5,
            "RQ2_alpha": 0.9, "RQ2_alpha_mc_err": 0.01,
            "R1+R2_ohm": 500.0, "R1+R2_mc_err_ohm": 3.5,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0, rrqrq_components=rrqrq,
            mc_n_iterations=30, mc_n_success=27,
        )
        df, warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        area_cm2 = row["electrode_area_cm2"]

        self.assertEqual(row["mc_n_iterations"], 30)
        self.assertEqual(row["mc_n_success"], 27)
        self.assertAlmostEqual(row["Rs_mc_err_ohm"], 0.3)
        self.assertAlmostEqual(row["Rs_mc_err_ohm_cm2"], 0.3 * area_cm2, places=12)
        self.assertAlmostEqual(row["RQ1_R_mc_err_ohm"], 2.0)
        self.assertAlmostEqual(row["RQ1_R_mc_err_ohm_cm2"], 2.0 * area_cm2, places=12)
        self.assertAlmostEqual(row["RQ1_alpha_mc_err"], 0.02)
        self.assertAlmostEqual(row["RQ2_R_mc_err_ohm"], 3.0)
        # R1+R2_mc_err_ohm is read directly (not re-derived as an
        # independent sum), unlike R1+R2_err_ohm.
        self.assertAlmostEqual(row["R1+R2_mc_err_ohm"], 3.5)
        self.assertAlmostEqual(row["R1+R2_mc_err_ohm_cm2"], 3.5 * area_cm2, places=12)
        self.assertEqual(warnings, [])

    def test_monte_carlo_capacitance_columns_present_and_area_scaled(self):
        # Capacitance has no Jacobian-based error at all (nonlinear function
        # of R/Q/alpha, would need their covariance) -- Monte Carlo is the
        # only source of a capacitance error, RQ{i}_C_mc_err_F.
        rrqrq = {
            "RQ1_R_ohm": 100.0, "RQ1_Q_F_s_alpha_minus_1": 1e-4, "RQ1_alpha": 0.8,
            "RQ1_C_mc_err_F": 4e-8,
            "RQ2_R_ohm": 400.0, "RQ2_Q_F_s_alpha_minus_1": 2e-3, "RQ2_alpha": 0.9,
            "RQ2_C_mc_err_F": 8e-7,
            "R1+R2_ohm": 500.0,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0, rrqrq_components=rrqrq,
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        area_cm2 = row["electrode_area_cm2"]

        self.assertAlmostEqual(row["RQ1_C_mc_err_F"], 4e-8)
        self.assertAlmostEqual(row["RQ1_C_mc_err_F_cm2"], 4e-8 / area_cm2, places=18)
        self.assertAlmostEqual(row["RQ2_C_mc_err_F"], 8e-7)
        self.assertAlmostEqual(row["RQ2_C_mc_err_F_cm2"], 8e-7 / area_cm2, places=16)

    def test_monte_carlo_capacitance_columns_blank_when_absent(self):
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components={"RQ1_R_ohm": 1.0, "RQ1_Q_F_s_alpha_minus_1": 1e-4, "RQ1_alpha": 0.8, "R1+R2_ohm": 1.0},
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertTrue(pd.isna(row["RQ1_C_mc_err_F"]))

    def test_generic_non_rrqrq_model_also_gets_monte_carlo_error_columns(self):
        # Regression test: _generic_rq_components used to only pick up the
        # Jacobian-based "<key> error" fields, not "<key> Monte Carlo error"
        # -- so a non-RRQRQ row's MC error was computed and stored in the
        # raw fit dict but never reached the CSV/GUI at all.
        fit = {
            "R0 (Ohm)": 100.0, "R0 (Ohm) Monte Carlo error": 1.5,
            "Q0 (F·s^(a-1))": 1e-4, "Q0 (F·s^(a-1)) Monte Carlo error": 1e-6,
            "a0": 0.8, "a0 Monte Carlo error": 0.01,
            "R1 (Ohm)": 400.0, "R1 (Ohm) Monte Carlo error": 3.0,
            "Q1 (F·s^(a-1))": 2e-3, "Q1 (F·s^(a-1)) Monte Carlo error": 2e-5,
            "a1": 0.9, "a1 Monte Carlo error": 0.005,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components=None, fit_model="RQRQ", fit=fit,
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertAlmostEqual(row["RQ1_R_mc_err_ohm"], 1.5)
        self.assertAlmostEqual(row["RQ2_R_mc_err_ohm"], 3.0)
        self.assertAlmostEqual(row["RQ1_alpha_mc_err"], 0.01)
        self.assertAlmostEqual(row["RQ1_Q_mc_err_F_s_alpha_minus_1"], 1e-6)
        self.assertAlmostEqual(row["R1+R2_mc_err_ohm"], math.sqrt(1.5**2 + 3.0**2), places=9)

    def test_monte_carlo_columns_blank_when_absent(self):
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components={"Rs_ohm": 1.0, "R1+R2_ohm": 0.0},
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertTrue(pd.isna(row["mc_n_iterations"]))
        self.assertTrue(pd.isna(row["Rs_mc_err_ohm"]))
        self.assertTrue(pd.isna(row["R1+R2_mc_err_ohm"]))

    def test_r_reaction_and_polarization_r_total_are_no_longer_separate_columns(self):
        # R_reaction_ohm and polarization_R_total_ohm were always identical
        # (both = sum of RQ-element resistances) -- collapsed into one
        # column, R1+R2_ohm.
        self.assertNotIn("R_reaction_ohm", ex.COLUMNS)
        self.assertNotIn("R_reaction_ohm_cm2", ex.COLUMNS)
        self.assertNotIn("polarization_R_total_ohm", ex.COLUMNS)
        self.assertNotIn("polarization_R_total_ohm_cm2", ex.COLUMNS)
        self.assertIn("R1+R2_ohm", ex.COLUMNS)
        self.assertIn("R1+R2_ohm_cm2", ex.COLUMNS)

    def test_legacy_json_with_old_polarization_key_name_still_extracts_correctly(self):
        # A full_arc_summary.json written before the rename only has the old
        # "polarization_R_total_ohm" key (no "R1+R2_ohm" at all) -- R1+R2_ohm
        # must still come out right, since it's derived fresh from the
        # individual RQ{i}_R_ohm values rather than read from either name.
        legacy_rrqrq = {
            "Rs_ohm": 5.0,
            "RQ1_R_ohm": 100.0,
            "RQ1_Q_F_s_alpha_minus_1": 1e-4,
            "RQ1_alpha": 0.8,
            "RQ2_R_ohm": 400.0,
            "RQ2_Q_F_s_alpha_minus_1": 2e-3,
            "RQ2_alpha": 0.9,
            "polarization_R_total_ohm": 500.0,  # legacy key name, pre-rename
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components=legacy_rrqrq,
        )
        df, warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertAlmostEqual(row["R1+R2_ohm"], 500.0)

    def test_alpha_near_one_does_not_raise_and_approaches_q(self):
        rrqrq = {
            "Rs_ohm": 0.0,
            "RQ1_R_ohm": 50.0,
            "RQ1_Q_F_s_alpha_minus_1": 0.1,
            "RQ1_alpha": 0.999999999999999,
            "RQ1_fchar_Hz": 1.0,
            "RQ1_resistance_fraction": 0.5,
            "RQ2_R_ohm": 3000.0,
            "RQ2_Q_F_s_alpha_minus_1": 1.3,
            "RQ2_alpha": 0.999999999999999,
            "RQ2_fchar_Hz": 0.001,
            "RQ2_resistance_fraction": 0.5,
            "R1+R2_ohm": 3050.0,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=104, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=450.0, rrqrq_components=rrqrq,
        )
        df, warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertAlmostEqual(row["RQ1_C_F"], 0.1, places=6)
        self.assertAlmostEqual(row["RQ2_C_F"], 1.3, places=6)
        self.assertEqual(warnings, [])

    def test_degenerate_alpha_near_zero_is_caught_not_raised(self):
        # Mirrors a real fit seen on actual data: alpha collapsed to ~2.5e-32,
        # which overflows R**((1-alpha)/alpha) -- must be caught and reported
        # as a warning, not crash the whole extraction.
        rrqrq = {
            "Rs_ohm": 6.76,
            "RQ1_R_ohm": 64064.65,
            "RQ1_Q_F_s_alpha_minus_1": 1e-3,
            "RQ1_alpha": 0.9,
            "RQ1_fchar_Hz": 1.0,
            "RQ1_resistance_fraction": 0.05,
            "RQ2_R_ohm": 1628579.33,
            "RQ2_Q_F_s_alpha_minus_1": 4.389207467684513,
            "RQ2_alpha": 2.527990500735131e-32,
            "RQ2_fchar_Hz": 0.0001,
            "RQ2_resistance_fraction": 0.95,
            "R1+R2_ohm": 1692644.0,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0, rrqrq_components=rrqrq,
        )
        df, warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertTrue(pd.isna(row["RQ2_C_F"]))
        self.assertIsNotNone(row["RQ1_C_F"])
        self.assertTrue(any("RQ2 capacitance" in w for w in warnings))

    def test_row_without_curated_json_falls_back_to_folder_name(self):
        row_dir = _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.1, diameter_um=150.0,
            rrqrq_components={"Rs_ohm": 1.0, "R1+R2_ohm": 0.0},
            write_curated=False,
        )
        self.assertTrue(row_dir.exists())

        df, warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertEqual(row["electrode_number"], 97)
        self.assertAlmostEqual(row["temperature_C"], 600.0)
        self.assertAlmostEqual(row["gas_a_setting"], 0.0)
        self.assertAlmostEqual(row["gas_b_setting"], 20.0)
        self.assertAlmostEqual(row["bias_V_dc"], 0.1)
        self.assertTrue(pd.isna(row["electrode_diameter_um"]))
        self.assertTrue(pd.isna(row["electrode_area_cm2"]))
        self.assertTrue(pd.isna(row["Rs_ohm_cm2"]))
        self.assertTrue(any("no matching curated JSON" in w for w in warnings))

    def test_row_without_rrqrq_components_still_included_with_blank_columns(self):
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components=None, fit_model="RQRQ",
        )
        df, warnings = ex.extract_all(self.run_dir)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row["fit_model"], "RQRQ")
        self.assertAlmostEqual(row["temperature_C"], 600.0)
        self.assertAlmostEqual(row["electrode_diameter_um"], 150.0)
        self.assertTrue(pd.isna(row["Rs_ohm"]))
        self.assertTrue(pd.isna(row["RQ1_C_F"]))
        # No rrqrq_components AND no raw "fit" dict either -- truly nothing
        # to derive a breakdown from.
        self.assertTrue(any("no fit component breakdown" in w for w in warnings))

    def test_non_rrqrq_model_falls_back_to_generic_rq_breakdown_from_raw_fit(self):
        # RQRQ has no separate Rs term (see EIS_Fitting.py's MODEL_PARAM_KEYS) --
        # just two RQ elements, R0/Q0/a0 and R1/Q1/a1.
        fit = {
            "R0 (Ohm)": 100.0,
            "Q0 (F·s^(a-1))": 1e-4,
            "a0": 0.8,
            "RQ0 fchar (Hz)": 10.0,
            "R1 (Ohm)": 400.0,
            "Q1 (F·s^(a-1))": 2e-3,
            "a1": 0.9,
            "RQ1 fchar (Hz)": 1.0,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components=None, fit_model="RQRQ", fit=fit,
        )
        df, warnings = ex.extract_all(self.run_dir)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row["fit_model"], "RQRQ")
        # RQRQ has no Rs element -- must stay blank, not fabricated as 0.
        self.assertTrue(pd.isna(row["Rs_ohm"]))
        self.assertAlmostEqual(row["RQ1_R_ohm"], 100.0)
        self.assertAlmostEqual(row["RQ1_alpha"], 0.8)
        self.assertAlmostEqual(row["RQ2_R_ohm"], 400.0)
        self.assertAlmostEqual(row["RQ1_C_F"], _brug_capacitance(1e-4, 100.0, 0.8))
        self.assertAlmostEqual(row["R1+R2_ohm"], 500.0)
        self.assertFalse(any("no fit component breakdown" in w for w in warnings))

    def test_three_element_model_exposes_rq3_in_the_raw_row_dict(self):
        # extract_row's raw dict (as the GUI consumes it) isn't limited to
        # COLUMNS' fixed RQ1/RQ2 -- RQRQRQ's third element should still show
        # up as RQ3_* keys, even though the CSV schema doesn't have a column
        # for it yet.
        fit = {
            "R0 (Ohm)": 10.0, "Q0 (F·s^(a-1))": 1e-5, "a0": 0.95, "RQ0 fchar (Hz)": 100.0,
            "R1 (Ohm)": 20.0, "Q1 (F·s^(a-1))": 1e-4, "a1": 0.9, "RQ1 fchar (Hz)": 10.0,
            "R2 (Ohm)": 30.0, "Q2 (F·s^(a-1))": 1e-3, "a2": 0.85, "RQ2 fchar (Hz)": 1.0,
        }
        row_dir = _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components=None, fit_model="RQRQRQ", fit=fit,
        )
        warnings = []
        row = ex.extract_row(row_dir / "full_arc_summary.json", warnings)
        self.assertAlmostEqual(row["RQ1_R_ohm"], 10.0)
        self.assertAlmostEqual(row["RQ2_R_ohm"], 20.0)
        self.assertAlmostEqual(row["RQ3_R_ohm"], 30.0)
        self.assertAlmostEqual(row["R1+R2_ohm"], 60.0)

    def test_rrqrq_error_columns_present_and_area_scaled(self):
        rrqrq = {
            "Rs_ohm": 5.0, "Rs_err_ohm": 0.5,
            "RQ1_R_ohm": 100.0, "RQ1_R_err_ohm": 1.0,
            "RQ1_Q_F_s_alpha_minus_1": 1e-4, "RQ1_Q_err_F_s_alpha_minus_1": 1e-6,
            "RQ1_alpha": 0.8, "RQ1_alpha_err": 0.01,
            "RQ2_R_ohm": 400.0, "RQ2_R_err_ohm": 2.0,
            "RQ2_Q_F_s_alpha_minus_1": 2e-3, "RQ2_Q_err_F_s_alpha_minus_1": 2e-5,
            "RQ2_alpha": 0.9, "RQ2_alpha_err": 0.005,
            "R1+R2_ohm": 500.0, "R1+R2_err_ohm": math.sqrt(1.0**2 + 2.0**2),
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0, rrqrq_components=rrqrq,
        )
        df, warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        area_cm2 = row["electrode_area_cm2"]

        self.assertAlmostEqual(row["Rs_err_ohm"], 0.5)
        self.assertAlmostEqual(row["Rs_err_ohm_cm2"], 0.5 * area_cm2, places=12)
        self.assertAlmostEqual(row["RQ1_R_err_ohm"], 1.0)
        self.assertAlmostEqual(row["RQ1_R_err_ohm_cm2"], 1.0 * area_cm2, places=12)
        self.assertAlmostEqual(row["RQ1_Q_err_F_s_alpha_minus_1"], 1e-6)
        self.assertAlmostEqual(row["RQ1_alpha_err"], 0.01)
        self.assertAlmostEqual(row["RQ2_R_err_ohm"], 2.0)
        self.assertAlmostEqual(row["RQ2_R_err_ohm_cm2"], 2.0 * area_cm2, places=12)
        # R1+R2_err_ohm is recomputed fresh (independent-sum of the per-RQ
        # errors), not read from the stored total -- same "compute fresh"
        # convention as R1+R2_ohm itself.
        self.assertAlmostEqual(row["R1+R2_err_ohm"], math.sqrt(1.0**2 + 2.0**2), places=9)
        self.assertAlmostEqual(row["R1+R2_err_ohm_cm2"], math.sqrt(1.0**2 + 2.0**2) * area_cm2, places=9)
        self.assertEqual(warnings, [])

    def test_missing_error_fields_leave_error_columns_blank_not_zero(self):
        rrqrq = {
            "Rs_ohm": 5.0,
            "RQ1_R_ohm": 100.0, "RQ1_Q_F_s_alpha_minus_1": 1e-4, "RQ1_alpha": 0.8,
            "RQ2_R_ohm": 400.0, "RQ2_Q_F_s_alpha_minus_1": 2e-3, "RQ2_alpha": 0.9,
            "R1+R2_ohm": 500.0,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0, rrqrq_components=rrqrq,
        )
        df, _warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertTrue(pd.isna(row["Rs_err_ohm"]))
        self.assertTrue(pd.isna(row["RQ1_R_err_ohm"]))
        self.assertTrue(pd.isna(row["R1+R2_err_ohm"]))

    def test_generic_rq_fallback_includes_error_fields(self):
        # RQRQ, going through _generic_rq_components (no rrqrq_components key).
        fit = {
            "R0 (Ohm)": 100.0, "R0 (Ohm) error": 1.5,
            "Q0 (F·s^(a-1))": 1e-4, "Q0 (F·s^(a-1)) error": 1e-6,
            "a0": 0.8, "a0 error": 0.01,
            "R1 (Ohm)": 400.0, "R1 (Ohm) error": 3.0,
            "Q1 (F·s^(a-1))": 2e-3, "Q1 (F·s^(a-1)) error": 2e-5,
            "a1": 0.9, "a1 error": 0.005,
        }
        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components=None, fit_model="RQRQ", fit=fit,
        )
        df, warnings = ex.extract_all(self.run_dir)
        row = df.iloc[0]
        self.assertAlmostEqual(row["RQ1_R_err_ohm"], 1.5)
        self.assertAlmostEqual(row["RQ2_R_err_ohm"], 3.0)
        self.assertAlmostEqual(row["R1+R2_err_ohm"], math.sqrt(1.5**2 + 3.0**2), places=9)
        self.assertFalse(any("no fit component breakdown" in w for w in warnings))

    def test_nan_padded_unused_branch_does_not_create_phantom_rq_element(self):
        # Regression test: EIS_Fitting.py's _result_from_model_params
        # pre-fills every FIT_RESULT_KEYS slot with np.nan and only overwrites
        # the branches a given model actually has, so a 2-branch RQRQ fit's
        # raw dict still carries R2/Q2/a2 as NaN, not absent. The generic
        # fallback used to treat NaN as "present" (only `is None` was
        # checked), picking up a phantom third element and poisoning
        # R1+R2_ohm to NaN.
        import math as _math

        fit = {
            "R0 (Ohm)": 100.0, "Q0 (F·s^(a-1))": 1e-4, "a0": 0.8,
            "R1 (Ohm)": 400.0, "Q1 (F·s^(a-1))": 2e-3, "a1": 0.9,
            "R2 (Ohm)": _math.nan, "Q2 (F·s^(a-1))": _math.nan, "a2": _math.nan,
        }
        row_dir = _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components=None, fit_model="RQRQ", fit=fit,
        )
        warnings: list[str] = []
        row = ex.extract_row(row_dir / "full_arc_summary.json", warnings)
        self.assertNotIn("RQ3_R_ohm", row)
        self.assertAlmostEqual(row["R1+R2_ohm"], 500.0)

    def test_multiple_rows_all_present_in_output(self):
        for idx, electrode in enumerate((97, 98, 99), start=1):
            _write_row(
                self.run_dir, row_index=idx, electrode=electrode, temperature_c=600.0,
                gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
                rrqrq_components={"Rs_ohm": 1.0, "R1+R2_ohm": 0.0},
            )
        df, _warnings = ex.extract_all(self.run_dir)
        self.assertEqual(sorted(df["electrode_number"].tolist()), [97, 98, 99])

    def test_cli_default_output_writes_csv_inside_results_root(self):
        import subprocess

        _write_row(
            self.run_dir, row_index=1, electrode=97, temperature_c=600.0,
            gas_a=0.0, gas_b=20.0, v_dc=0.0, diameter_um=150.0,
            rrqrq_components={"Rs_ohm": 1.0, "R1+R2_ohm": 0.0},
        )
        script = TOOLS_DIR / "extract_full_arc_results_to_csv.py"
        completed = subprocess.run(
            [sys.executable, str(script), str(self.run_dir)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        expected_csv = self.run_dir / "full_arc_results.csv"
        self.assertTrue(expected_csv.exists(), msg=f"expected {expected_csv} to exist; stdout={completed.stdout}")
        df = pd.read_csv(expected_csv)
        self.assertEqual(len(df), 1)

    def test_electrode_area_matches_hand_computed_value(self):
        area = ex._electrode_area_cm2(200.0)
        expected = math.pi * (100.0 * 1e-4) ** 2
        self.assertAlmostEqual(area, expected, places=14)

    def test_electrode_area_none_when_diameter_missing(self):
        self.assertIsNone(ex._electrode_area_cm2(None))


if __name__ == "__main__":
    unittest.main()
