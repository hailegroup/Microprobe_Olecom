# -*- coding: utf-8 -*-
"""
Microprobe Automated Measurement System
========================================
Reads a condition CSV and runs overnight measurements:
  - For each row: set T -> set raw gas settings -> move stage -> Rapid EIS sequence

CSV columns (all required):
  Temperature_C   : target temperature
  GasA_setting    : Gas A raw DMFC setting (0-100)
  GasB_setting    : Gas B raw DMFC setting (0-100)
  X_mm, Y_mm, Z_mm: stage position (mm)
  AutoContactZ     : 0/1 - if 1, run OCV-based Z contact search from seed Z
  ContactStartOffset_mm, ContactStep_mm, ContactMaxDrop_mm,
  ContactMaxBeyondSeed_mm, ContactOCVThreshold_V, ContactSettle_s, ContactEngage_mm:
                     optional Z contact search parameters
  V_dc            : DC bias voltage (V)
  dV              : perturbation amplitude (V)
  HoldTime_s      : CA hold duration before PEIS (s)
  PostPEIS_HoldTime_s : CA hold duration at V_dc after PEIS (s)
  PEIS_fHigh      : PEIS upper frequency (Hz)
  PEIS_fLow       : PEIS lower frequency (Hz)
  PEIS_nPts       : number of PEIS frequency points
  CA_duration_s   : long CA duration at (V_dc + dV) after the post-PEIS hold (s)
  CA_dt           : CA sample interval (s)
  StableTime_s    : seconds to hold after a changed temperature reaches target (s)
  GasStableTime_s : seconds to hold after changed gas settings; overlaps temperature ramp/stabilization
  Label           : string label for file naming
  Skip            : 0/1 - skip this row if 1

Usage
-----
  python run_automation.py conditions.csv [--result-dir ./results]
"""

import os
import time
import argparse
import traceback
import json

import pandas as pd

from adaptive_engine import AdaptiveMeasurementEngine, AdaptiveEngineSettings
from adaptive_types import MeasurementExecution, MeasurementFiles, PointMetadata
from driver_biologic import BioLogicController
from driver_motor import MDriveMotor
from driver_temp import WatlowController
from driver_mfc import AeraMFC
from live_seeded_sequence import live_seeded_rapid_eis_sequence
from measurement_sequence import rapid_eis_sequence, normal_eis_sequence
from config import STAGE_SAFE_MOVE, MFC_WRITE_ENABLED


_SKIP_TOKENS = {'', 'nan', 'none', 'non', 'skip', '-', 'na', 'n/a'}
GAS_SETTING_COLUMNS = {
    'A': ('GasA_setting', 'GasA_sccm'),
    'B': ('GasB_setting', 'GasB_sccm'),
}
CONTACT_CONFIRM_DURATION_S = 10.0
CONTACT_CONFIRM_POLL_S = 1.0


def _parse_optional_float(value, default=None):
    if value is None:
        return default
    if pd.isna(value):
        return default
    text = str(value).strip().lower()
    if text in _SKIP_TOKENS:
        return default
    return float(value)


def _parse_boolish(value, default=False):
    if value is None:
        return default
    if pd.isna(value):
        return default
    text = str(value).strip().lower()
    if text in _SKIP_TOKENS:
        return default
    return text in {'1', 'true', 'yes', 'y', 'on'}


def _series_has_value(series):
    for value in series:
        if _parse_optional_float(value, default=None) is not None:
            return True
    return False


def _row_gas_setting(row, channel, default=None):
    for column in GAS_SETTING_COLUMNS[channel]:
        if column in row:
            value = _parse_optional_float(row.get(column), default=None)
            if value is not None:
                return value
    return default


def move_stage(motor, row, tol_mm=0.01):
    """Move all three axes to position specified in CSV row."""
    targets = {
        'X': _parse_optional_float(row.get('X_mm'), default=None),
        'Y': _parse_optional_float(row.get('Y_mm'), default=None),
        'Z': _parse_optional_float(row.get('Z_mm'), default=None),
    }
    current = {
        axis: (motor.get_position(axis) if target is not None else None)
        for axis, target in targets.items()
    }
    moves = motor.move_xyz_safe(
        x_mm=targets['X'],
        y_mm=targets['Y'],
        z_mm=targets['Z'],
        tol_mm=tol_mm,
        current_positions=current,
        log_fn=print,
    )
    if not moves:
        for axis, target in targets.items():
            if target is not None and current[axis] is not None:
                print(f"  {axis} already at {current[axis]:.3f} mm (within {tol_mm} mm)")
        return


def _confirm_contact_candidate(
    biologic,
    *,
    channel,
    ocv_threshold,
    log_fn=print,
    confirm_s=CONTACT_CONFIRM_DURATION_S,
    poll_s=CONTACT_CONFIRM_POLL_S,
):
    deadline = time.time() + float(confirm_s)
    samples = 0
    while time.time() < deadline:
        ocv = biologic.get_ocv(channel=channel) if hasattr(biologic, 'get_ocv') else None
        samples += 1
        if ocv is None:
            log_fn("  AutoContactZ: candidate rejected during confirm; OCV unavailable")
            return False
        if abs(float(ocv)) > float(ocv_threshold):
            log_fn(
                f"  AutoContactZ: candidate rejected during confirm; "
                f"OCV={float(ocv):+.4f} V"
            )
            return False
        remaining = max(0.0, deadline - time.time())
        log_fn(
            f"  AutoContactZ: candidate stable, OCV={float(ocv):+.4f} V, "
            f"{remaining:.0f}s remaining"
        )
        time.sleep(min(float(poll_s), remaining))
    return samples > 0


def find_contact_z(motor, biologic, row, *, channel=1, log_fn=print):
    """Find OCV contact from a seed Z and return the measurement Z."""
    base_z = _parse_optional_float(row.get('Z_mm'), default=None)
    if base_z is None:
        base_z = motor.get_position('Z')
    start_offset = _parse_optional_float(row.get('ContactStartOffset_mm'), default=0.200)
    step_mm = abs(_parse_optional_float(row.get('ContactStep_mm'), default=0.020))
    max_drop_mm = _parse_optional_float(row.get('ContactMaxDrop_mm'), default=0.400)
    max_beyond_seed_mm = _parse_optional_float(row.get('ContactMaxBeyondSeed_mm'), default=0.200)
    ocv_threshold = _parse_optional_float(row.get('ContactOCVThreshold_V'), default=0.200)
    settle_s = _parse_optional_float(row.get('ContactSettle_s'), default=0.30)
    engage_mm = _parse_optional_float(row.get('ContactEngage_mm'), default=0.100)
    if step_mm <= 0:
        raise RuntimeError('Contact step must be > 0 mm')

    positive_z_is_up = bool(STAGE_SAFE_MOVE.get('positive_z_is_up', True))
    approach_sign = -1.0 if positive_z_is_up else 1.0
    start_z = float(base_z) - approach_sign * abs(float(start_offset))
    log_fn(f"  AutoContactZ: seed Z={float(base_z):.3f} mm, start Z={start_z:.3f} mm")
    motor.move_abs_wait('Z', start_z)

    max_steps = max(1, int(round(float(max_drop_mm) / step_mm)))
    max_beyond_seed_mm = abs(float(max_beyond_seed_mm)) if max_beyond_seed_mm is not None else None
    found_contact = None
    for idx in range(max_steps + 1):
        z_here = start_z + approach_sign * idx * step_mm
        if max_beyond_seed_mm is not None:
            beyond_seed = approach_sign * (z_here - float(base_z))
            if beyond_seed > max_beyond_seed_mm:
                raise RuntimeError(
                    f'AutoContactZ safety lock: Z would pass seed by {beyond_seed:.3f} mm '
                    f'(limit {max_beyond_seed_mm:.3f} mm)'
                )
        motor.move_abs_wait('Z', z_here)
        time.sleep(float(settle_s))
        ocv = biologic.get_ocv(channel=channel) if hasattr(biologic, 'get_ocv') else None
        log_fn(
            f"  AutoContactZ: Z={z_here:.3f} mm, OCV={float(ocv):+.4f} V"
            if ocv is not None else
            f"  AutoContactZ: Z={z_here:.3f} mm, OCV unavailable"
        )
        if ocv is not None and abs(float(ocv)) <= float(ocv_threshold):
            log_fn(
                f"  AutoContactZ: contact candidate at Z={z_here:.3f} mm; "
                f"confirming for {CONTACT_CONFIRM_DURATION_S:.0f}s"
            )
            if _confirm_contact_candidate(
                biologic,
                channel=channel,
                ocv_threshold=ocv_threshold,
                log_fn=log_fn,
            ):
                found_contact = z_here
                break

    if found_contact is None:
        raise RuntimeError('AutoContactZ did not find a valid OCV threshold')

    measure_z = found_contact + approach_sign * abs(float(engage_mm))
    motor.move_abs_wait('Z', measure_z)
    log_fn(f"  AutoContactZ: contact Z={found_contact:.3f} mm, measurement Z={measure_z:.3f} mm")
    return measure_z


def set_gas(mfc, row):
    """Set raw DMFC settings from CSV row."""
    setter = getattr(mfc, 'set_setting', mfc.set_flow)
    for ch in ('A', 'B'):
        target = _row_gas_setting(row, ch, default=None)
        if target is not None:
            setter(ch, target)


def _contact_position_key(row):
    x = _parse_optional_float(row.get('X_mm'), default=None)
    y = _parse_optional_float(row.get('Y_mm'), default=None)
    if x is None or y is None:
        return None
    return (round(float(x), 5), round(float(y), 5))


def _contact_exact_key(row):
    pos_key = _contact_position_key(row)
    if pos_key is None:
        return None
    temp = _parse_optional_float(row.get('Temperature_C'), default=None)
    gas_a = _row_gas_setting(row, 'A', default=None)
    gas_b = _row_gas_setting(row, 'B', default=None)
    return (
        pos_key,
        None if temp is None else round(float(temp), 3),
        None if gas_a is None else round(float(gas_a), 3),
        None if gas_b is None else round(float(gas_b), 3),
    )


def _row_uses_adaptive_runtime(row):
    label = str(row.get('Label', '')).strip()
    return label.upper().startswith('ADAPT')


def _extract_electrode_id(label):
    import re

    match = re.search(r'(?:^|_)E(\d+)(?:_|$)', str(label))
    if not match:
        return None
    return int(match.group(1))


def _adaptive_regime_key(row):
    label = str(row.get('Label', ''))
    return (
        _extract_electrode_id(label),
        _parse_optional_float(row.get('Temperature_C'), default=None),
        _row_gas_setting(row, 'A', default=None),
        _row_gas_setting(row, 'B', default=None),
    )


def _build_adaptive_point_metadata(row, sequence_index):
    label = str(row.get('Label', f'row{sequence_index}')).strip() or f'row{sequence_index}'
    return PointMetadata(
        point_id=f'row{sequence_index:04d}_{label}',
        label=label,
        electrode_id=_extract_electrode_id(label),
        temperature_c=_parse_optional_float(row.get('Temperature_C'), default=None),
        # adaptive_types keeps legacy field names; values here are raw DMFC settings.
        gas_a_sccm=_row_gas_setting(row, 'A', default=None),
        gas_b_sccm=_row_gas_setting(row, 'B', default=None),
        voltage_v=float(row['V_dc']),
        sequence_index=sequence_index,
    )


def _build_measurement_execution(row, measurement_mode):
    n_pts = None
    try:
        n_pts = int(float(row.get('PEIS_nPts')))
    except Exception:
        pass

    cp_duration = None
    if measurement_mode != 'normal_eis':
        cp_duration = _parse_optional_float(row.get('CA_duration_s'), default=None)

    return MeasurementExecution(
        measurement_mode=measurement_mode,
        peis_lowest_freq_hz=_parse_optional_float(row.get('PEIS_fLow'), default=None),
        cp_duration_s=cp_duration,
        planned_pre_peis_hold_s=_parse_optional_float(row.get('HoldTime_s'), default=None),
        planned_post_peis_hold_s=_parse_optional_float(row.get('PostPEIS_HoldTime_s'), default=None),
        actual_peis_high_freq_hz=_parse_optional_float(row.get('PEIS_fHigh'), default=None),
        actual_peis_npts=n_pts,
        dv_v=_parse_optional_float(row.get('dV'), default=None),
        pre_hold_time_s=_parse_optional_float(row.get('HoldTime_s'), default=None),
        post_peis_hold_time_s=_parse_optional_float(row.get('PostPEIS_HoldTime_s'), default=None),
    )


def _build_measurement_files(sequence_result):
    return MeasurementFiles(
        pre_ca_path=getattr(sequence_result, 'pre_ca_path', None),
        peis_path=getattr(sequence_result, 'peis_path', None),
        post_ca_path=getattr(sequence_result, 'post_ca_path', None),
    )


def _create_adaptive_runtime(df, *, normal_eis_floor_hz=0.01, engine_factory=None, log_fn=print):
    adaptive_rows = df.apply(_row_uses_adaptive_runtime, axis=1)
    if not adaptive_rows.any():
        return None

    if engine_factory is None:
        engine = AdaptiveMeasurementEngine(
            engine_settings=AdaptiveEngineSettings(
                normal_eis_floor_hz=normal_eis_floor_hz,
            )
        )
    else:
        try:
            engine = engine_factory(normal_eis_floor_hz=normal_eis_floor_hz)
        except TypeError:
            engine = engine_factory()

    runtime = {
        'engine': engine,
        'regime_counts': {},
        'normal_eis_floor_hz': normal_eis_floor_hz,
        'summary': None,
        'summary_path': None,
    }
    log_fn(
        f"[Adaptive] Runtime enabled for {int(adaptive_rows.sum())} ADAPT rows "
        f"(normal EIS floor {normal_eis_floor_hz:g} Hz)"
    )
    return runtime


def _apply_adaptive_recommendation(row, recommendation):
    updated = row.copy()
    if recommendation.peis_lowest_freq_hz is not None:
        updated['PEIS_fLow'] = float(recommendation.peis_lowest_freq_hz)
    if recommendation.cp_duration_s is not None:
        updated['CA_duration_s'] = float(recommendation.cp_duration_s)
    if recommendation.planned_pre_peis_hold_s is not None:
        updated['HoldTime_s'] = float(recommendation.planned_pre_peis_hold_s)
    if recommendation.planned_post_peis_hold_s is not None:
        updated['PostPEIS_HoldTime_s'] = float(recommendation.planned_post_peis_hold_s)
    return updated


def _prepare_adaptive_row(runtime, row, row_index, *, log_fn=print):
    if runtime is None or not _row_uses_adaptive_runtime(row):
        return row, None

    regime_key = _adaptive_regime_key(row)
    prior_count = runtime['regime_counts'].get(regime_key, 0)
    if prior_count <= 0:
        return row, None

    point = _build_adaptive_point_metadata(row, row_index + 1)
    recommendation = runtime['engine'].recommend_next_point(point)
    updated = _apply_adaptive_recommendation(row, recommendation)
    ca_text = (
        'n/a'
        if recommendation.measurement_mode == 'normal_eis'
        else f"{recommendation.cp_duration_s:g}s"
    )
    log_fn(
        f"[Adaptive] {point.label}: runtime plan -> mode={recommendation.measurement_mode}, "
        f"f_low={recommendation.peis_lowest_freq_hz:g} Hz, CA={ca_text}, "
        f"seed={recommendation.analysis_source}, "
        f"policy={recommendation.current_run_policy_action or 'none'}"
    )
    if recommendation.notes:
        log_fn(
            f"  [Adaptive notes] {' | '.join(str(note) for note in recommendation.notes[:3])}"
        )
    return updated, recommendation


def _adaptive_summary_point_entry(point, row, row_index, measurement_mode, recommendation, finalize, sequence_result):
    files = _build_measurement_files(sequence_result)
    return {
        'index': row_index + 1,
        'point_id': point.point_id,
        'label': point.label,
        'bias_v': float(row['V_dc']),
        'measurement_mode': measurement_mode,
        'used_parameters': {
            'peis_f_low_hz': _parse_optional_float(row.get('PEIS_fLow'), default=None),
            'hold_time_s': _parse_optional_float(row.get('HoldTime_s'), default=None),
            'post_peis_hold_time_s': _parse_optional_float(row.get('PostPEIS_HoldTime_s'), default=None),
            'ca_duration_s': _parse_optional_float(row.get('CA_duration_s'), default=None)
            if measurement_mode != 'normal_eis' else None,
        },
        'recommendation_used': None if recommendation is None else recommendation.to_dict(),
        'consumed_current_run_policy': _adaptive_summary_consumed_policy(recommendation),
        'consumed_current_run_policy_text': _format_adaptive_policy_text(recommendation=recommendation),
        'analysis_finalize': finalize,
        'completed_point_current_run_policy': _adaptive_summary_finalize_policy(finalize),
        'completed_point_current_run_policy_text': _format_adaptive_policy_text(finalize=finalize),
        'files': {
            'pre_ca_path': files.pre_ca_path,
            'peis_path': files.peis_path,
            'post_ca_path': files.post_ca_path,
        },
    }


def _adaptive_summary_consumed_policy(recommendation):
    if recommendation is None:
        return None
    action = getattr(recommendation, 'current_run_policy_action', None)
    if not action:
        return None
    return {
        'action': action,
        'consumed': bool(getattr(recommendation, 'current_run_policy_consumed', False)),
        'analysis_point_id': getattr(recommendation, 'current_run_policy_analysis_point_id', None),
        'runtime_lf_hz': getattr(recommendation, 'current_run_policy_runtime_lf_hz', None),
    }


def _adaptive_summary_finalize_policy(finalize):
    if not finalize:
        return None
    policy = finalize.get('current_run_policy_decision')
    if not policy:
        return None
    return {
        'action': policy.get('action'),
        'measurement_mode': policy.get('measurement_mode'),
        'runtime_lf_hz': policy.get('runtime_lf_hz'),
        'runtime_target_lf_hz': policy.get('runtime_target_lf_hz'),
        'runtime_vs_target_relation': policy.get('runtime_vs_target_relation'),
    }


def _format_adaptive_policy_text(*, recommendation=None, finalize=None):
    if recommendation is not None:
        action = getattr(recommendation, 'current_run_policy_action', None)
        if action:
            runtime_lf = (
                getattr(recommendation, 'current_run_policy_runtime_lf_hz', None)
                or getattr(recommendation, 'peis_lowest_freq_hz', None)
            )
            runtime_lf_text = (
                "n/a" if runtime_lf is None
                else f"{float(runtime_lf):.3g} Hz"
            )
            if action == 'handoff_to_normal_lf':
                if getattr(recommendation, 'current_run_policy_consumed', False):
                    return (
                        f"Recommendation: adaptive handoff to normal EIS "
                        f"(runtime LF ~{runtime_lf_text})."
                    )
                return (
                    f"Recommendation: adaptive normal handoff is stored, "
                    f"but runtime guard kept rapid mode for now "
                    f"(candidate LF ~{runtime_lf_text})."
                )
            if action == 'keep_exploratory_seed_for_hybrid':
                return (
                    f"Recommendation: adaptive hybrid plan kept the exploratory LF seed "
                    f"(runtime LF ~{runtime_lf_text})."
                )

    if finalize is not None:
        decision = finalize.get('current_run_policy_decision') or {}
        action = decision.get('action')
        runtime_lf = decision.get('runtime_lf_hz')
        runtime_lf_text = (
            "n/a" if runtime_lf is None
            else f"{float(runtime_lf):.3g} Hz"
        )
        if action == 'handoff_to_normal_lf':
            return (
                f"Recommendation: completed-point policy stored a normal handoff "
                f"(runtime LF ~{runtime_lf_text}) for later rows."
            )
        if action == 'keep_exploratory_seed_for_hybrid':
            return (
                f"Recommendation: completed-point policy stored exploratory-seed "
                f"preservation for hybrid mode (runtime LF ~{runtime_lf_text})."
            )
    return None


def _write_adaptive_summary(runtime):
    if not runtime:
        return
    summary_path = runtime.get('summary_path')
    summary = runtime.get('summary')
    if not summary_path or summary is None:
        return
    with open(summary_path, 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)


def _init_adaptive_summary(runtime, *, result_root, df):
    if not runtime:
        return
    adaptive_rows = int(df.apply(_row_uses_adaptive_runtime, axis=1).sum())
    runtime['summary_path'] = os.path.join(result_root, 'adaptive_runtime_summary.json')
    runtime['summary'] = {
        'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'result_root': result_root,
        'normal_eis_floor_hz': runtime.get('normal_eis_floor_hz'),
        'adaptive_row_count': adaptive_rows,
        'points': [],
        'mode_counts': {},
    }
    _write_adaptive_summary(runtime)


def _finalize_adaptive_row(runtime, row, row_index, measurement_mode, sequence_result, *, recommendation=None, log_fn=print):
    if runtime is None or not _row_uses_adaptive_runtime(row):
        return

    point = _build_adaptive_point_metadata(row, row_index + 1)
    measurement = _build_measurement_execution(row, measurement_mode)
    files = _build_measurement_files(sequence_result)
    engine = runtime['engine']
    engine.register_point(point, measurement, files)

    regime_key = _adaptive_regime_key(row)
    runtime['regime_counts'][regime_key] = runtime['regime_counts'].get(regime_key, 0) + 1
    finalize = {
        'point_id': point.point_id,
        'analysis_ready_within_wait': False,
        'state': 'analysis_not_started',
        'analysis_result': None,
        'full_processing_state': None,
        'remeasurement_request': None,
    }

    if measurement_mode == 'rapid_eis' and files.peis_path and (files.post_ca_path or files.pre_ca_path):
        engine.start_post_measurement_pipeline(point.point_id)
        finalize = engine.finalize_completed_point(point.point_id)
        analysis = finalize.get('analysis_result') or {}
        if finalize.get('analysis_ready_within_wait'):
            next_lf = analysis.get('recommended_peis_lowest_freq_hz')
            next_cp = analysis.get('recommended_peis_conservative_cp_time_s')
            log_fn(
                f"[Adaptive] {point.label}: analysis ready "
                f"(sufficient={analysis.get('data_sufficient')}, "
                f"next f_low={next_lf if next_lf is not None else 'n/a'}, "
                f"next CA={next_cp if next_cp is not None else 'n/a'})"
            )
        else:
            log_fn(
                f"[Adaptive] {point.label}: analysis still running in background; "
                f"the next row may temporarily use fallback planning"
            )
    else:
        log_fn(
            f"[Adaptive] {point.label}: measurement_mode={measurement_mode}; "
            f"no hybrid post-analysis started for this point"
        )

    summary = runtime.get('summary')
    if summary is not None:
        point_summary = _adaptive_summary_point_entry(
            point=point,
            row=row,
            row_index=row_index,
            measurement_mode=measurement_mode,
            recommendation=recommendation,
            finalize=finalize,
            sequence_result=sequence_result,
        )
        summary['points'].append(point_summary)
        summary['mode_counts'][measurement_mode] = summary['mode_counts'].get(measurement_mode, 0) + 1
        _write_adaptive_summary(runtime)

    for request in engine.collect_pending_remeasurements():
        log_fn(
            f"[Adaptive] Remeasure suggested for {request.point_id}: "
            f"mode={request.measurement_mode}, "
            f"f_low={request.peis_lowest_freq_hz:g} Hz, "
            f"CA={request.cp_duration_s:g}s"
        )


def _backfill_adaptive_summary(runtime, *, timeout_s=15.0):
    if not runtime:
        return 0
    engine = runtime.get('engine')
    summary = runtime.get('summary')
    if engine is None or summary is None:
        return 0

    updated = 0
    deadline = time.time() + max(float(timeout_s), 0.0)
    for point_summary in summary.get('points', []):
        point_id = point_summary.get('point_id')
        if not point_id:
            continue
        previous_finalize = point_summary.get('analysis_finalize') or {}
        remaining = max(0.0, deadline - time.time())
        refreshed_finalize = engine.finalize_completed_point(point_id, timeout_s=remaining)
        point_summary['analysis_finalize'] = refreshed_finalize
        point_summary['completed_point_current_run_policy'] = _adaptive_summary_finalize_policy(refreshed_finalize)
        point_summary['completed_point_current_run_policy_text'] = _format_adaptive_policy_text(finalize=refreshed_finalize)
        if previous_finalize != refreshed_finalize:
            updated += 1
    if updated:
        _write_adaptive_summary(runtime)
    return updated


def _shutdown_adaptive_runtime(runtime):
    if not runtime:
        return
    engine = runtime.get('engine')
    if engine is None:
        return
    try:
        engine.shutdown()
    except Exception:
        pass


def _run_conditions(
    df,
    *,
    result_root,
    bl,
    motor=None,
    tc=None,
    mfc=None,
    rapid_sequence=rapid_eis_sequence,
    normal_sequence=normal_eis_sequence,
    adaptive_engine_factory=None,
    gas_mode='require_auto',
    log_fn=print,
):
    os.makedirs(result_root, exist_ok=True)

    prev_temp = None
    prev_gas = None
    prev_pos = None
    contact_z_cache = {}
    exact_contact_z_cache = {}
    results = []
    adaptive_runtime = _create_adaptive_runtime(
        df,
        engine_factory=adaptive_engine_factory,
        log_fn=log_fn,
    )
    _init_adaptive_summary(adaptive_runtime, result_root=result_root, df=df)

    try:
        for idx, row in df.iterrows():
            label = str(row.get('Label', f'row{idx+1}'))
            log_fn(f"\n{'#'*60}")
            log_fn(f"Row {idx+1}/{len(df)}: {label}")
            log_fn(f"{'#'*60}")

            row, adaptive_recommendation = _prepare_adaptive_row(
                adaptive_runtime,
                row,
                idx,
                log_fn=log_fn,
            )
            measurement_mode = (
                adaptive_recommendation.measurement_mode
                if adaptive_recommendation is not None else 'rapid_eis'
            )
            label = str(row.get('Label', label))
            if _parse_boolish(row.get('AutoContactZ'), default=False):
                cache_key = _contact_position_key(row)
                if cache_key in contact_z_cache:
                    row = row.copy()
                    row['Z_mm'] = contact_z_cache[cache_key]
                    log_fn(
                        f"  AutoContactZ seed updated from previous same-XY measurement: "
                        f"Z={float(contact_z_cache[cache_key]):.3f} mm"
                    )

            gas_a = _row_gas_setting(row, 'A', default=None)
            gas_b = _row_gas_setting(row, 'B', default=None)
            gas_now = (gas_a, gas_b)
            gas_set_started_at = None
            gas_changed = prev_gas != gas_now
            gas_wait = (
                _parse_optional_float(row.get('GasStableTime_s', 600), default=600)
                if any(v is not None for v in gas_now) and gas_changed else 0.0
            )
            if mfc and any(v is not None for v in gas_now):
                if gas_changed:
                    if gas_mode == 'manual_skip':
                        log_fn(
                            "  Gas setting write skipped by manual_skip mode "
                            f"(requested A={gas_a}, B={gas_b})"
                        )
                    elif gas_mode == 'require_auto' and not MFC_WRITE_ENABLED:
                        raise RuntimeError('Gas write requested but MFC_WRITE_ENABLED=False in config.py')
                    else:
                        set_gas(mfc, row)
                        gas_set_started_at = time.time()
                        log_fn(
                            f"  Gas target set first (A={gas_a}, B={gas_b}); "
                            f"required gas stabilization {gas_wait:.0f}s"
                        )
                    prev_gas = gas_now
                else:
                    log_fn("  Gas conditions unchanged - skipping gas stabilization")
            elif mfc:
                log_fn("  Gas step skipped for this row")

            target_temp = _parse_optional_float(row.get('Temperature_C'), default=None)
            stable_time = _parse_optional_float(row.get('StableTime_s', 120), default=120)
            if tc and target_temp is not None:
                temp_changed = prev_temp != target_temp
                if temp_changed:
                    tc.set_temperature(target_temp)
                    log_fn(
                        f"  Waiting for temperature stabilization at {target_temp:.0f} C; "
                        "gas stabilization overlaps this wait"
                    )
                    tc.wait_stable(target_temp, tol=2.0, stable_time=stable_time)
                    prev_temp = target_temp
                else:
                    log_fn(f"  Temperature already at {target_temp:.0f} C - skipping ramp")
            elif tc:
                log_fn("  Temperature step skipped for this row")

            if gas_set_started_at is not None and gas_wait > 0:
                elapsed = max(0.0, time.time() - gas_set_started_at)
                remaining = max(0.0, float(gas_wait) - elapsed)
                if remaining > 0:
                    log_fn(
                        f"  Waiting remaining gas stabilization "
                        f"({remaining:.0f}s of {gas_wait:.0f}s; "
                        f"{elapsed:.0f}s already overlapped)"
                    )
                    time.sleep(remaining)
                else:
                    log_fn(
                        f"  Gas stabilization covered by prior wait "
                        f"({elapsed:.0f}s >= {gas_wait:.0f}s)"
                    )

            pos_now = tuple(
                _parse_optional_float(row.get(f'{ax}_mm'), default=None)
                for ax in ['X', 'Y', 'Z']
            )
            if motor and any(v is not None for v in pos_now):
                if prev_pos != pos_now:
                    move_stage(motor, row)
                    prev_pos = pos_now
                else:
                    log_fn("  Stage position unchanged - skipping move")
            elif motor:
                log_fn("  Stage move skipped for this row")

            if motor and bl and _parse_boolish(row.get('AutoContactZ'), default=False):
                cache_key = _contact_position_key(row)
                exact_key = _contact_exact_key(row)
                if exact_key is not None and exact_key in exact_contact_z_cache:
                    measurement_z = float(exact_contact_z_cache[exact_key])
                    log_fn(
                        f"  AutoContactZ exact cached contact at Z={measurement_z:.3f} mm "
                        f"reused for same XY/temp/gas"
                    )
                else:
                    if cache_key is not None and cache_key in contact_z_cache:
                        row = row.copy()
                        row['Z_mm'] = contact_z_cache[cache_key]
                        log_fn(
                            f"  AutoContactZ seed updated from previous same-XY measurement: "
                            f"Z={float(contact_z_cache[cache_key]):.3f} mm"
                        )
                    measurement_z = find_contact_z(motor, bl, row, log_fn=log_fn)
                if cache_key is not None:
                    contact_z_cache[cache_key] = measurement_z
                if exact_key is not None:
                    exact_contact_z_cache[exact_key] = measurement_z
                row = row.copy()
                row['Z_mm'] = measurement_z
                pos_now = (
                    _parse_optional_float(row.get('X_mm'), default=None),
                    _parse_optional_float(row.get('Y_mm'), default=None),
                    measurement_z,
                )
                prev_pos = pos_now

            save_dir = result_root
            if measurement_mode == 'normal_eis':
                sequence_result = normal_sequence(
                    biologic=bl,
                    v_dc=float(row['V_dc']),
                    peis_f_high=float(row.get('PEIS_fHigh', 1e5)),
                    peis_f_low=float(row.get('PEIS_fLow', 0.1)),
                    peis_npts=int(float(row.get('PEIS_nPts', 60))),
                    amplitude_mv=float(row.get('dV', 0.03)) * 1000.0,
                    save_dir=save_dir,
                    label=label,
                )
            else:
                if _row_uses_adaptive_runtime(row):
                    log_fn("  [Full-auto] using live CA/FFT seeded PEIS protocol")
                    sequence_result = live_seeded_rapid_eis_sequence(
                        biologic=bl,
                        v_dc=float(row['V_dc']),
                        dv=float(row.get('dV', 0.03)),
                        pre_min=float(row.get('HoldTime_s', 60)),
                        pre_max=max(900.0, float(row.get('HoldTime_s', 60))),
                        scout_min=float(row.get('CA_duration_s', 120)),
                        scout_max=max(1200.0, float(row.get('CA_duration_s', 120))),
                        ca_dt=max(0.1, float(row.get('CA_dt', 0.1))),
                        channel=1,
                        peis_f_high=float(row.get('PEIS_fHigh', 1e6)),
                        peis_floor=0.5,
                        peis_deep_limit=0.05,
                        peis_npts=int(float(row.get('PEIS_nPts', 60))),
                        peis_amplitude_mv=10.0,
                        bandwidth='BW4',
                        current_range='AUTO',
                        save_dir=save_dir,
                        label=label,
                    )
                    applied_lf = getattr(sequence_result, 'applied_peis_lf_hz', None)
                    raw_lf = getattr(sequence_result, 'raw_recommended_lf_hz', None)
                    if applied_lf is not None:
                        row = row.copy()
                        row['PEIS_fLow'] = float(applied_lf)
                        if raw_lf is not None:
                            log_fn(
                                f"  [Full-auto] FFT LF raw={float(raw_lf):.4g} Hz, "
                                f"applied PEIS LF={float(applied_lf):.4g} Hz"
                            )
                        else:
                            log_fn(f"  [Full-auto] applied PEIS LF={float(applied_lf):.4g} Hz")
                else:
                    sequence_result = rapid_sequence(
                        biologic=bl,
                        v_dc=float(row['V_dc']),
                        dv=float(row.get('dV', 0.03)),
                        hold_time=float(row.get('HoldTime_s', 60)),
                        post_peis_hold_time=float(row.get('PostPEIS_HoldTime_s', 30)),
                        peis_f_high=float(row.get('PEIS_fHigh', 1e5)),
                        peis_f_low=float(row.get('PEIS_fLow', 0.1)),
                        peis_npts=int(row.get('PEIS_nPts', 60)),
                        ca_duration=float(row.get('CA_duration_s', 200)),
                        ca_dt=float(row.get('CA_dt', 0.1)),
                        save_dir=save_dir,
                        label=label,
                    )

            _finalize_adaptive_row(
                adaptive_runtime,
                row,
                idx,
                measurement_mode,
                sequence_result,
                recommendation=adaptive_recommendation,
                log_fn=log_fn,
            )

            results.append({
                'Label': label,
                'Temperature': target_temp if target_temp is not None else '',
                'GasA_setting': gas_now[0] if gas_now[0] is not None else '',
                'GasB_setting': gas_now[1] if gas_now[1] is not None else '',
                'V_dc': float(row['V_dc']),
                'MeasurementMode': measurement_mode,
                'Status': 'OK',
            })
            log_fn(f"  Row {idx+1} done.")

        refreshed = _backfill_adaptive_summary(adaptive_runtime)
        if refreshed:
            log_fn(
                f"[Adaptive] Backfilled {refreshed} runtime summary entr"
                f"{'y' if refreshed == 1 else 'ies'} after delayed analysis completion"
            )
        if adaptive_runtime and adaptive_runtime.get('summary') is not None:
            adaptive_runtime['summary']['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            _write_adaptive_summary(adaptive_runtime)
    finally:
        _shutdown_adaptive_runtime(adaptive_runtime)

    return results


def main():
    parser = argparse.ArgumentParser(description='Microprobe Automated Measurement')
    parser.add_argument('csv', help='Path to conditions CSV file')
    parser.add_argument('--result-dir', default='./results', help='Output directory for raw data')
    parser.add_argument(
        '--gas-mode',
        choices=['require_auto', 'manual_skip'],
        default='require_auto',
        help='require_auto stops if MFC writes are disabled; manual_skip assumes gas was set manually',
    )
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    print(f"Loaded {len(df)} condition row(s) from {args.csv}")

    if 'Skip' in df.columns:
        df = df[df['Skip'] != 1].reset_index(drop=True)
        print(f"  {len(df)} row(s) after Skip filter")

    result_root = args.result_dir
    os.makedirs(result_root, exist_ok=True)

    needs_temp = 'Temperature_C' in df.columns and _series_has_value(df['Temperature_C'])
    needs_mfc = any(
        col in df.columns and _series_has_value(df[col])
        for cols in GAS_SETTING_COLUMNS.values()
        for col in cols
    )
    needs_motor = any(
        col in df.columns and _series_has_value(df[col])
        for col in ['X_mm', 'Y_mm', 'Z_mm']
    ) or ('AutoContactZ' in df.columns and df['AutoContactZ'].apply(lambda v: _parse_boolish(v, default=False)).any())

    print("\nConnecting hardware ...")
    bl = BioLogicController()
    motor = MDriveMotor() if needs_motor else None
    tc = WatlowController() if needs_temp else None
    mfc = AeraMFC() if needs_mfc else None

    bl.connect()
    if motor:
        motor.connect()
    if tc:
        tc.connect()
    if mfc:
        mfc.connect()
    print("Requested hardware connected.\n")

    try:
        results = _run_conditions(
            df,
            result_root=result_root,
            bl=bl,
            motor=motor,
            tc=tc,
            mfc=mfc,
            rapid_sequence=rapid_eis_sequence,
            normal_sequence=normal_eis_sequence,
            gas_mode=args.gas_mode,
            log_fn=print,
        )

    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
    except Exception:
        print("\n[!] Error during run:")
        traceback.print_exc()
        if 'results' in locals() and results:
            results[-1]['Status'] = 'ERROR'
    finally:
        if 'results' in locals() and results:
            df_res = pd.DataFrame(results)
            summary_path = os.path.join(result_root, 'measurement_log.csv')
            df_res.to_csv(summary_path, index=False)
            print(f"\nMeasurement log saved: {summary_path}")

        try:
            bl.disconnect()
            if motor:
                motor.disconnect()
            if tc:
                tc.disconnect()
            if mfc:
                mfc.disconnect()
        except Exception:
            pass
        print("Done.")


if __name__ == '__main__':
    main()
