# -*- coding: utf-8 -*-
"""
Single-point Rapid EIS measurement sequence.

One measurement = 4 steps:
  1. CA hold at V_dc (establish steady state)
  2. PEIS at V_dc    (reference EIS, high frequencies)
  3. CA perturbation (V_dc + dV, single pulse → FFT-EIS low frequencies)
  4. Save raw data   (caller handles FFT + fitting)
"""

import os
import time
import numpy as np


def rapid_eis_sequence(biologic, v_dc, dv=0.03,
                        hold_time=60, peis_f_high=1e5, peis_f_low=0.1,
                        peis_npts=60, ca_duration=200, ca_dt=0.01,
                        save_dir=None, label=''):
    """
    Execute one Rapid EIS measurement at a given DC bias.

    Parameters
    ----------
    biologic      : BioLogicController (connected)
    v_dc          : DC bias voltage (V)
    dv            : perturbation amplitude (V), default 30 mV
    hold_time     : CA hold duration before PEIS (s)
    peis_f_high   : PEIS upper frequency (Hz)
    peis_f_low    : PEIS lower frequency (Hz)
    peis_npts     : number of PEIS frequencies
    ca_duration   : CA perturbation duration (s) — sets lowest recovered freq ≈ 1/ca_duration
    ca_dt         : CA recording interval (s)
    save_dir      : folder to save raw txt files; None = don't save
    label         : filename prefix (e.g. '600C_pO2-0p2_0p3V')

    Returns
    -------
    eis_data  : np.ndarray [freq, ReZ, -ImZ]  — from PEIS
    ca_data   : np.ndarray [time, V, I]       — from CA perturbation
    """
    print(f"\n{'='*60}")
    print(f"Rapid EIS: {label}  V_dc={v_dc:.3f}V  dV={dv*1000:.1f}mV")
    print(f"{'='*60}")

    # Step 1: CA hold
    print("\n[Step 1] CA hold ...")
    biologic.run_ca_hold(v_dc, hold_time, dt_record=0.5)

    # Step 2: PEIS
    print("\n[Step 2] PEIS ...")
    eis_data = biologic.run_peis(v_dc, peis_f_high, peis_f_low, peis_npts)
    print(f"  PEIS complete: {len(eis_data)} points, "
          f"f={eis_data[:,0].min():.3g}~{eis_data[:,0].max():.3g} Hz")

    # Step 3: CA perturbation
    print("\n[Step 3] CA perturbation ...")
    ca_data = biologic.run_ca_perturbation(v_dc, dv, ca_duration, dt_record=ca_dt)
    print(f"  CA complete: {len(ca_data)} points, "
          f"dt={ca_data[1,0]-ca_data[0,0]:.4f}s")

    # Save raw files
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        ts = time.strftime('%Y%m%d_%H%M%S')
        eis_path = os.path.join(save_dir, f"{label}_PEIS_{ts}.txt")
        ca_path  = os.path.join(save_dir, f"{label}_CA_{ts}.txt")
        np.savetxt(eis_path, eis_data, header='freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm', comments='')
        np.savetxt(ca_path,  ca_data,  header='time/s  V/V  I/A', comments='')
        print(f"  Saved: {eis_path}")
        print(f"  Saved: {ca_path}")

    return eis_data, ca_data
