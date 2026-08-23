# -*- coding: utf-8 -*-
"""
Single-point Rapid EIS measurement sequence.

One measurement = 4 steps:
  1. CA hold at V_dc before PEIS
  2. PEIS at V_dc using dV as the PEIS amplitude
  3. CA sequence at [V_dc, V_dc + dV] after PEIS
  4. Save raw data
"""

import os
import shutil
import time
import numpy as np


class SequenceResult:
    """Measurement result container with backward-compatible tuple unpacking."""

    def __init__(self, *,
                 measurement_mode,
                 eis_data,
                 ca_data=None,
                 pre_ca_data=None,
                 pre_ca_path=None,
                 peis_path=None,
                 peis_mpr_path=None,
                 post_ca_path=None):
        self.measurement_mode = measurement_mode
        self.eis_data = eis_data
        self.ca_data = ca_data if ca_data is not None else np.empty((0, 3))
        self.pre_ca_data = pre_ca_data if pre_ca_data is not None else np.empty((0, 3))
        self.pre_ca_path = pre_ca_path
        self.peis_path = peis_path
        self.peis_mpr_path = peis_mpr_path
        self.post_ca_path = post_ca_path

    def __iter__(self):
        yield self.eis_data
        yield self.ca_data


def rapid_eis_sequence(biologic, v_dc, dv=0.03,
                        hold_time=60, post_peis_hold_time=30,
                        peis_f_high=1e5, peis_f_low=0.1,
                        peis_npts=60, ca_duration=200, ca_dt=0.1,
                        channel=1, skip_ca=False, bandwidth=None, n_average=1,
                        save_dir=None, label='', monitor_callback=None,
                        stop_event=None):
    """
    Execute one Rapid EIS measurement at a given DC bias.

    Parameters
    ----------
    biologic      : BioLogicController (connected)
    v_dc          : DC bias voltage (V)
    dv            : shared perturbation amplitude (V) for both PEIS amplitude and post-PEIS CA voltage step
    hold_time     : CA hold duration before PEIS (s)
    post_peis_hold_time : short CA hold duration at V_dc after PEIS (s)
    peis_f_high   : PEIS upper frequency (Hz)
    peis_f_low    : PEIS lower frequency (Hz)
    peis_npts     : number of PEIS frequencies
    ca_duration   : long CA duration at (V_dc + dV) (s)
    ca_dt         : CA recording interval (s)
    skip_ca       : if True, skip both the pre-PEIS CA hold (Step 1) and the
                    post-PEIS CA sequence (Step 3) entirely -- only PEIS runs.
    bandwidth     : BioLogic bandwidth setting for PEIS (e.g. 'BW4'), or None
                    for the driver's own hardware default.
    n_average     : number of repeated measurements averaged per PEIS
                    frequency point (EC-Lab "N average").
    save_dir      : folder to save raw txt files; None = don't save
    label         : filename prefix (e.g. '600C_pO2-0p2_0p3V')

    Returns
    -------
    eis_data  : np.ndarray [freq, ReZ, -ImZ]  — from PEIS
    ca_data   : np.ndarray [time, V, I] — CA sequence from post-PEIS hold through long CA
    """
    print(f"\n{'='*60}")
    print(f"Rapid EIS: {label}  V_dc={v_dc:.3f}V  dV={dv*1000:.1f}mV")
    print(f"{'='*60}")

    def _emit(event, **payload):
        if monitor_callback:
            monitor_callback(event=event, label=label, **payload)

    # Step 1: CA hold
    if skip_ca:
        print("\n[Step 1] CA hold skipped (skip_ca)")
        pre_ca_data = np.empty((0, 3))
    else:
        print("\n[Step 1] CA hold ...")
        _emit('step', step='ca_hold', message='CA hold started')
        pre_ca_data = biologic.run_ca_hold(
            v_dc, hold_time, dt_record=0.5,
            channel=channel,
            on_segment=lambda data, _segment: _emit('dc_data', step='ca_hold', data=data),
            stop_event=stop_event,
        )
        _emit('dc_data', step='ca_hold_done', data=pre_ca_data)
        _emit('step', step='ca_hold_done', message='CA hold finished')

    # Step 2: PEIS
    print("\n[Step 2] PEIS ...")
    _emit('step', step='peis', message='PEIS started')
    eis_data = biologic.run_peis(
        v_dc, peis_f_high, peis_f_low, peis_npts,
        amplitude_mv=dv * 1000.0,
        channel=channel,
        bandwidth=bandwidth,
        n_average=n_average,
        save_dir=save_dir,
        label=label,
        on_segment=lambda data, _segment: _emit('eis_data', step='peis', data=data),
        stop_event=stop_event,
    )
    # Some easy-biologic builds do not stream on_data callbacks during PEIS.
    # Always emit the final parsed PEIS result so the monitor is populated.
    _emit('eis_data', step='peis_done', data=eis_data)
    _emit('step', step='peis_done', message='PEIS finished')
    if len(eis_data) == 0:
        print("  [WARNING] PEIS returned 0 points — check BioLogic connection and parameters")
        return SequenceResult(
            measurement_mode='rapid_eis',
            eis_data=np.empty((0, 3)),
            ca_data=np.empty((0, 3)),
            pre_ca_data=pre_ca_data,
        )
    print(f"  PEIS complete: {len(eis_data)} points, "
          f"f={eis_data[:,0].min():.3g}~{eis_data[:,0].max():.3g} Hz")

    # Step 3: combined CA sequence in one technique
    if skip_ca:
        print("\n[Step 3] Post-PEIS CA sequence skipped (skip_ca)")
        ca_data = np.empty((0, 3))
    else:
        print("\n[Step 3] Post-PEIS CA sequence ...")
        _emit('step', step='post_peis_ca_sequence', message='Post-PEIS CA sequence started')
        ca_data = biologic.run_ca_sequence(
            voltage_steps=[v_dc, v_dc + dv],
            duration_steps=[post_peis_hold_time, ca_duration],
            dt_record=ca_dt,
            channel=channel,
            on_segment=lambda data, _segment: _emit('dc_data', step='post_peis_ca_sequence', data=data),
            stop_event=stop_event,
        )
        # Same fallback for CA sequence: make sure the full result reaches the monitor.
        _emit('dc_data', step='post_peis_ca_sequence_done', data=ca_data)
        _emit('step', step='post_peis_ca_sequence_done', message='Post-PEIS CA sequence finished')
        if len(ca_data) > 1:
            print(f"  CA sequence complete: {len(ca_data)} points, "
                  f"dt={ca_data[1,0]-ca_data[0,0]:.4f}s")
        else:
            print(f"  CA sequence complete: {len(ca_data)} points")

    # Save raw files
    pre_ca_path = None
    eis_path = None
    eis_mpr_path = None
    ca_path = None
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        ts = time.strftime('%Y%m%d_%H%M%S')
        eis_path = os.path.join(save_dir, f"{label}_PEIS_{ts}.txt")
        np.savetxt(eis_path, eis_data)
        print(f"  Saved: {eis_path}")
        # Preserve the raw EC-Lab .mpr (OLE-COM backend only -- driver_biologic.py
        # never sets this attribute) under the exact same base name as the
        # .txt above, so the two are trivially pairable by filename.
        mpr_src = getattr(biologic, '_last_peis_mpr_path', None)
        if mpr_src and os.path.exists(mpr_src):
            eis_mpr_path = os.path.join(save_dir, f"{label}_PEIS_{ts}.mpr")
            try:
                shutil.copy2(mpr_src, eis_mpr_path)
                print(f"  Saved: {eis_mpr_path}")
            except Exception as exc:
                print(f"  [WARNING] could not preserve raw .mpr ({exc})")
                eis_mpr_path = None
        if not skip_ca:
            pre_ca_path = os.path.join(save_dir, f"Pre_stabilization_{label}_CA_{ts}.txt")
            ca_path  = os.path.join(save_dir, f"{label}_CA_{ts}.txt")
            np.savetxt(pre_ca_path, pre_ca_data, header='time/s  V/V  I/A', comments='')
            np.savetxt(ca_path,  ca_data,  header='time/s  V/V  I/A', comments='')
            print(f"  Saved: {pre_ca_path}")
            print(f"  Saved: {ca_path}")

    _emit('sequence_done', step='done', eis_points=len(eis_data), ca_points=len(ca_data))

    return SequenceResult(
        measurement_mode='rapid_eis',
        eis_data=eis_data,
        ca_data=ca_data,
        pre_ca_data=pre_ca_data,
        pre_ca_path=pre_ca_path,
        peis_path=eis_path,
        peis_mpr_path=eis_mpr_path,
        post_ca_path=ca_path,
    )


def normal_eis_sequence(biologic, v_dc,
                        peis_f_high=1e5, peis_f_low=0.1,
                        peis_npts=60, amplitude_mv=10.0,
                        channel=1, bandwidth=None, n_average=1,
                        save_dir=None, label='', monitor_callback=None,
                        stop_event=None):
    """
    Execute a PEIS-only measurement at a given DC bias.

    Returns
    -------
    SequenceResult
        `ca_data` and `pre_ca_data` are empty because no CA hold/perturbation
        is executed in normal-EIS mode.
    """
    print(f"\n{'='*60}")
    print(f"Normal EIS: {label}  V_dc={v_dc:.3f}V  amp={amplitude_mv:.1f}mV")
    print(f"{'='*60}")

    def _emit(event, **payload):
        if monitor_callback:
            monitor_callback(event=event, label=label, **payload)

    _emit('step', step='normal_peis', message='Normal EIS started')
    eis_data = biologic.run_peis(
        v_dc, peis_f_high, peis_f_low, peis_npts,
        amplitude_mv=amplitude_mv,
        channel=channel,
        bandwidth=bandwidth,
        n_average=n_average,
        save_dir=save_dir,
        label=label,
        on_segment=lambda data, _segment: _emit('eis_data', step='normal_peis', data=data),
        stop_event=stop_event,
    )
    _emit('eis_data', step='normal_peis_done', data=eis_data)
    _emit('step', step='normal_peis_done', message='Normal EIS finished')

    eis_path = None
    eis_mpr_path = None
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        ts = time.strftime('%Y%m%d_%H%M%S')
        eis_path = os.path.join(save_dir, f"{label}_PEIS_{ts}.txt")
        np.savetxt(eis_path, eis_data)
        print(f"  Saved: {eis_path}")
        mpr_src = getattr(biologic, '_last_peis_mpr_path', None)
        if mpr_src and os.path.exists(mpr_src):
            eis_mpr_path = os.path.join(save_dir, f"{label}_PEIS_{ts}.mpr")
            try:
                shutil.copy2(mpr_src, eis_mpr_path)
                print(f"  Saved: {eis_mpr_path}")
            except Exception as exc:
                print(f"  [WARNING] could not preserve raw .mpr ({exc})")
                eis_mpr_path = None

    _emit('sequence_done', step='done', eis_points=len(eis_data), ca_points=0)

    return SequenceResult(
        measurement_mode='normal_eis',
        eis_data=eis_data,
        peis_path=eis_path,
        peis_mpr_path=eis_mpr_path,
    )
