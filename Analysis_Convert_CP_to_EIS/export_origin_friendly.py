# -*- coding: utf-8 -*-
"""
Write Origin-friendly CSV exports for point-by-point inspection/editing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def _write_csv(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_csv(path, index=False)


def export_origin_bundle(
    output_dir,
    sample_name,
    dc_data=None,
    treated_time=None,
    treated_voltage=None,
    treated_current=None,
    diff_time=None,
    diff_voltage=None,
    diff_current=None,
    reference_freq=None,
    reference_re=None,
    reference_im=None,
    recovered_freq=None,
    recovered_re=None,
    recovered_im=None,
    fit_freq=None,
    fit_re=None,
    fit_im=None,
    fft_analysis_freq=None,
    fft_analysis_re=None,
    fft_analysis_im=None,
    stable_mask=None,
):
    export_dir = Path(output_dir) / "origin_export"
    export_dir.mkdir(parents=True, exist_ok=True)

    if dc_data is not None:
        dc = np.asarray(dc_data)
        if dc.ndim == 2 and dc.shape[1] >= 3:
            _write_csv(
                export_dir / f"{sample_name}_raw_dc.csv",
                {
                    "time_s": dc[:, 0],
                    "voltage_V": dc[:, 1],
                    "current_A": dc[:, 2],
                },
            )

    if treated_time is not None:
        _write_csv(
            export_dir / f"{sample_name}_treated_dc.csv",
            {
                "time_s": treated_time,
                "voltage_V": treated_voltage,
                "current_A": treated_current,
            },
        )

    if diff_time is not None:
        _write_csv(
            export_dir / f"{sample_name}_differentiated.csv",
            {
                "time_s": diff_time,
                "dVdt": diff_voltage,
                "dIdt": diff_current,
            },
        )

    if reference_freq is not None:
        _write_csv(
            export_dir / f"{sample_name}_reference_peis.csv",
            {
                "freq_Hz": reference_freq,
                "ReZ_Ohm": reference_re,
                "ImZ_Ohm": reference_im,
                "neg_ImZ_Ohm": -np.asarray(reference_im),
            },
        )

    if recovered_freq is not None:
        _write_csv(
            export_dir / f"{sample_name}_recovered_eis.csv",
            {
                "freq_Hz": recovered_freq,
                "ReZ_Ohm": recovered_re,
                "ImZ_Ohm": recovered_im,
                "neg_ImZ_Ohm": -np.asarray(recovered_im),
            },
        )

    if fit_freq is not None:
        _write_csv(
            export_dir / f"{sample_name}_fit_input.csv",
            {
                "freq_Hz": fit_freq,
                "ReZ_Ohm": fit_re,
                "ImZ_Ohm": fit_im,
                "neg_ImZ_Ohm": -np.asarray(fit_im),
            },
        )

    if fft_analysis_freq is not None:
        data = {
            "freq_Hz": fft_analysis_freq,
            "ReZ_Ohm": fft_analysis_re,
            "ImZ_Ohm": fft_analysis_im,
            "neg_ImZ_Ohm": -np.asarray(fft_analysis_im),
        }
        if stable_mask is not None:
            data["stable_mask"] = np.asarray(stable_mask, dtype=int)
        _write_csv(export_dir / f"{sample_name}_fft_analysis.csv", data)

    return export_dir
