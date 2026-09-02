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
  ContactStartOffset_mm, ContactStep_mm, ContactMaxBeyondSeed_mm,
  ContactOCVThreshold_V, ContactSettle_s, ContactEngage_mm:
                     optional Z contact search parameters. ContactMaxBeyondSeed_mm
                     both bounds how far the search looks for contact and is
                     enforced as a hard safety limit on every step (driving the
                     tip even ~0.5 mm too far into an electrode can damage it,
                     so there is deliberately only one distance limit, not a
                     separate search budget plus a safety bound).
  ContactSkipMemory: 0/1 (default 0) - if 1, always run a real OCV
                     contact search for this row, even if an earlier row in
                     the same run already confirmed contact at the exact
                     same X_mm/Y_mm/Temperature_C/gas ("exact cache" --
                     normally reused directly, skipping the search).
  AutoTrackXY      : 0/1 - if 1, correct this row's X_mm/Y_mm from periodic
                     headless vision electrode-drift tracking, for the
                     electrode encoded in Label (E<N>). Requires
                     config.VISION_AUTO_TRACK_XY_ENABLED=True and both the
                     pixel<->stage calibration and electrode alignment
                     artifacts (produced by the GUI's Image Monitor tab) to
                     be present; otherwise falls back silently to the row's
                     own CSV X_mm/Y_mm. Even when available, a row's CSV
                     position is used until at least one vision drift-check
                     has succeeded for that electrode during this run.
                     A correction is only trusted if it's within
                     config.VISION_DRIFT_MAX_CORRECTION_MM of the last
                     position this run itself trusted for that electrode
                     (or the row's own CSV position, for that electrode's
                     first touch this run) -- so a single bad detection gets
                     rejected, but slow, genuine, cumulative drift keeps
                     being tracked for the whole run instead of eventually
                     exceeding a fixed original bound.
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
import math

import pandas as pd

from adaptive_engine import AdaptiveMeasurementEngine, AdaptiveEngineSettings
from adaptive_types import MeasurementExecution, MeasurementFiles, PointMetadata
from driver_biologic import BioLogicController
from driver_motor import MDriveMotor
from driver_temp import WatlowController
from driver_mfc import AeraMFC
from live_seeded_sequence import live_seeded_rapid_eis_sequence
from measurement_sequence import rapid_eis_sequence, normal_eis_sequence
from config import (
    STAGE_SAFE_MOVE,
    MFC_WRITE_ENABLED,
    VISION_AUTO_TRACK_XY_ENABLED,
    VISION_CAMERA_BACKEND,
    VISION_CAMERA_INDEX,
    VISION_PIXEL_STAGE_CALIBRATION_PATH,
    VISION_ELECTRODE_LAYOUT_PATH,
    VISION_ELECTRODE_ALIGNMENT_PATH,
    VISION_DRIFT_RECHECK_EVERY_N_ROWS,
    VISION_DRIFT_RECHECK_EVERY_S,
    VISION_DRIFT_MAX_CORRECTION_MM,
    VISION_DRIFT_MIN_CONFIDENT_ELECTRODES,
    VISION_DRIFT_MIN_CONFIDENT_ELECTRODES_FRACTION,
    VISION_DRIFT_MIN_INLIER_FRACTION,
    VISION_ELECTRODE_Z_SEED_PATH,
    VISION_Z_PARALLAX_MAX_EXTRAPOLATION_MM,
    VISION_PARALLAX_MIN_SAMPLE_PIXEL_DELTA_PX,
    VISION_DRIFT_MAX_PROJECTED_DEVIATION_RADII,
    VISION_DRIFT_EMA_ALPHA,
    VISION_PROBE_OCCLUSION_X_MARGIN_MM,
)


_SKIP_TOKENS = {'', 'nan', 'none', 'non', 'skip', '-', 'na', 'n/a'}
GAS_SETTING_COLUMNS = {
    'A': ('GasA_setting', 'GasA_sccm'),
    'B': ('GasB_setting', 'GasB_sccm'),
}
CONTACT_CONFIRM_DURATION_S = 10.0
CONTACT_CONFIRM_POLL_S = 1.0
# Tolerance on the Z-contact search's safety-lock comparison (beyond_seed >
# max_beyond_seed_mm): the search's own step math can land a step's
# computed position a few ULPs past an exact mm-scale boundary purely from
# binary floating-point representation (e.g. 50 * 0.005 != 0.25 exactly),
# which would otherwise spuriously trip the safety lock on an intended,
# in-bounds step. 1e-6 mm (1 nm) is far larger than that noise and far
# smaller than anything that matters mechanically.
CONTACT_SAFETY_LOCK_EPSILON_MM = 1e-6


class ContactNotConfirmedError(RuntimeError):
    """Raised specifically when a Z-contact search's OCV never came within
    threshold and held through confirmation -- as opposed to a hardware/
    safety-lock failure, which stay plain RuntimeError and still abort the
    run. Carries last_z, the Z the search's final step reached, so a
    caller that wants to proceed anyway has a usable height."""
    def __init__(self, message, last_z):
        super().__init__(message)
        self.last_z = last_z


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
    if text in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    # A sparse 0/1 column (blank in some rows) gets upcast to float64 by
    # pandas as soon as any row is NaN, so a genuinely-set value can arrive
    # here as '1.0' rather than '1' -- fall back to numeric truthiness
    # before giving up and treating it as false.
    try:
        return float(text) != 0.0
    except ValueError:
        return False


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


def contact_search_start_z(motor, row):
    """
    The Z height AutoContactZ's search begins from -- the row's seed Z
    (Z_mm, or the stage's current Z if the row omits it) offset away from
    the electrode by ContactStartOffset_mm, in the safe/away-from-contact
    direction. Used to route the initial move-to-electrode Z move straight
    here instead of to the seed Z itself, so the tip never travels through/
    near the electrode surface at full move speed before find_contact_z's
    own careful step-wise search begins. Mirrors the start_z formula inside
    find_contact_z.
    """
    base_z = _parse_optional_float(row.get('Z_mm'), default=None)
    if base_z is None:
        base_z = motor.get_position('Z')
    start_offset = _parse_optional_float(row.get('ContactStartOffset_mm'), default=0.200)
    positive_z_is_up = bool(STAGE_SAFE_MOVE.get('positive_z_is_up', True))
    approach_sign = -1.0 if positive_z_is_up else 1.0
    return float(base_z) - approach_sign * abs(float(start_offset))


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


def find_contact_z(motor, biologic, row, *, channel=1, log_fn=print, probe_pixel_sample_fn=None):
    """
    Find OCV contact from a seed Z and return the measurement Z.

    probe_pixel_sample_fn, if given, is a zero-arg callable returning the
    probe tip's current detected (pixel_x, pixel_y) or None on detection
    failure. It is called once right after moving to the search-start
    height (before the step-down loop) and once right after contact is
    confirmed (before the engage overtravel move). XY is never touched
    during this search, so any difference between those two samples is
    attributable to the Z change alone -- a direct Z-parallax measurement.
    Returns (measure_z, parallax_sample), where parallax_sample is
    (start_z, start_pixel, contact_z, contact_pixel) if both samples were
    obtained, else None (including when probe_pixel_sample_fn is None).
    """
    base_z = _parse_optional_float(row.get('Z_mm'), default=None)
    if base_z is None:
        base_z = motor.get_position('Z')
    start_offset = _parse_optional_float(row.get('ContactStartOffset_mm'), default=0.200)
    step_mm = abs(_parse_optional_float(row.get('ContactStep_mm'), default=0.005))
    max_beyond_seed_mm = abs(_parse_optional_float(row.get('ContactMaxBeyondSeed_mm'), default=0.200))
    ocv_threshold = _parse_optional_float(row.get('ContactOCVThreshold_V'), default=0.200)
    settle_s = _parse_optional_float(row.get('ContactSettle_s'), default=0.30)
    engage_mm = _parse_optional_float(row.get('ContactEngage_mm'), default=0.01)
    if step_mm <= 0:
        raise RuntimeError('Contact step must be > 0 mm')

    positive_z_is_up = bool(STAGE_SAFE_MOVE.get('positive_z_is_up', True))
    approach_sign = -1.0 if positive_z_is_up else 1.0
    start_z = float(base_z) - approach_sign * abs(float(start_offset))
    log_fn(f"  AutoContactZ: seed Z={float(base_z):.3f} mm, start Z={start_z:.3f} mm")
    motor.move_abs_wait('Z', start_z)

    start_pixel = None
    if probe_pixel_sample_fn is not None:
        try:
            start_pixel = probe_pixel_sample_fn()
        except Exception as exc:
            log_fn(f"  AutoContactZ: probe-tip pixel sample (start) failed ({exc})")

    # max_beyond_seed_mm both bounds how many steps are tried and is
    # enforced as a hard safety limit on every step below -- driving the
    # tip even ~0.5 mm too far into an electrode can damage it, so there is
    # deliberately only one distance limit to configure, not a separate
    # search budget plus a safety bound.
    max_steps = max(1, int(round((abs(float(start_offset)) + max_beyond_seed_mm) / step_mm)))
    found_contact = None
    for idx in range(max_steps + 1):
        z_here = start_z + approach_sign * idx * step_mm
        beyond_seed = approach_sign * (z_here - float(base_z))
        if beyond_seed > max_beyond_seed_mm + CONTACT_SAFETY_LOCK_EPSILON_MM:
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
        raise ContactNotConfirmedError(
            'AutoContactZ did not find a valid OCV threshold', last_z=z_here,
        )

    contact_pixel = None
    if probe_pixel_sample_fn is not None:
        try:
            contact_pixel = probe_pixel_sample_fn()
        except Exception as exc:
            log_fn(f"  AutoContactZ: probe-tip pixel sample (contact) failed ({exc})")

    measure_z = found_contact + approach_sign * abs(float(engage_mm))
    motor.move_abs_wait('Z', measure_z)
    log_fn(f"  AutoContactZ: contact Z={found_contact:.3f} mm, measurement Z={measure_z:.3f} mm")

    parallax_sample = None
    if start_pixel is not None and contact_pixel is not None:
        delta_px = contact_pixel[0] - start_pixel[0]
        delta_py = contact_pixel[1] - start_pixel[1]
        delta_mag = (delta_px ** 2 + delta_py ** 2) ** 0.5
        if delta_mag < VISION_PARALLAX_MIN_SAMPLE_PIXEL_DELTA_PX:
            log_fn(
                f"  AutoContactZ: parallax sample rejected -- displacement "
                f"{delta_mag:.2f}px < {VISION_PARALLAX_MIN_SAMPLE_PIXEL_DELTA_PX:.2f}px"
            )
        elif delta_px > 0 or delta_py > 0:
            # As the probe moves from search-start height down to contact,
            # its detected pixel position always moves up-and-left in image
            # coordinates (negative x, negative y -- image y increases
            # downward, opposite orientation from stage/motor Y).
            log_fn(
                f"  AutoContactZ: parallax sample rejected -- wrong direction "
                f"delta=({delta_px:.2f}, {delta_py:.2f})px, expected both <= 0"
            )
        else:
            parallax_sample = (start_z, start_pixel, found_contact, contact_pixel)
    return measure_z, parallax_sample


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


# ── AutoTrackXY: headless vision electrode-drift tracking ──────────────────
#
# Electrode numbering convention: the E<N> electrode number parsed from a
# row's Label (1-based, see _extract_electrode_id) is mapped to the DXF
# layout's `layout_index` (0-based, the position in the layout JSON's
# "positions" array) as `layout_index = electrode_id - 1`. This assumes the
# DXF's circle-extraction order matches the physical E1..EN numbering used
# elsewhere in this project (e.g. the GUI's Full-auto generator) -- verify
# this once against a known layout before trusting AutoTrackXY on a new
# sample; a mismatched DXF layer/insert order would silently apply the wrong
# electrode's correction.

def _open_vision_camera(backend, index):
    """Open a cv2.VideoCapture the same way gui.py's Image Monitor tab does,
    but headless (no Tkinter state). Raises on failure; never returns a
    capture that failed to open."""
    import cv2

    backend_code = {
        'dshow': cv2.CAP_DSHOW,
        'any': cv2.CAP_ANY,
        'msmf': cv2.CAP_MSMF,
    }.get(str(backend).lower(), cv2.CAP_DSHOW)
    cap = cv2.VideoCapture(int(index), backend_code)
    if not cap.isOpened():
        raise RuntimeError(f'Could not open camera index={index} backend={backend}')
    time.sleep(0.3)
    return cap


def _init_vision_tracking_runtime(log_fn=print):
    """
    Bootstrap headless probe/electrode vision tracking for an AutoTrackXY
    run: load the saved pixel<->stage calibration and electrode alignment
    artifacts (produced by the GUI's Image Monitor workflow), open the
    camera, and seed an in-memory tracked-electrode list.

    Returns None on ANY failure (never raises), so a run with AutoTrackXY
    rows simply falls back to using each row's own CSV X_mm/Y_mm untouched.
    """
    try:
        import numpy as np
        from vision_stage_mapper import PixelStageReference, solve_stage_affine_calibration
        from vision.layout_alignment import LayoutModel, Circle
    except Exception as exc:
        log_fn(
            f"[WARN] AutoTrackXY: vision dependencies unavailable ({exc}); "
            f"all AutoTrackXY rows will fall back to their CSV X_mm/Y_mm."
        )
        return None

    try:
        with open(VISION_PIXEL_STAGE_CALIBRATION_PATH) as f:
            calib_payload = json.load(f)
        refs = [PixelStageReference(**ref) for ref in calib_payload['refs']]
        calibration = solve_stage_affine_calibration(refs)
        # The base affine is only valid at the single Z it was captured at
        # (gui.py enforces this at solve time). z_ref_mm is that reference
        # height, used by the drift-correction loop to Z-parallax-correct
        # electrode pixels via each electrode's own known Z seed. None if
        # this calibration predates per-point Z capture -- the correction
        # simply stays inactive in that case (see _maybe_refresh_vision_drift_correction).
        z_values = [ref.z_mm for ref in refs]
        z_ref_mm = None if any(z is None for z in z_values) else sum(z_values) / len(z_values)
    except Exception as exc:
        log_fn(
            f"[WARN] AutoTrackXY: could not load pixel<->stage calibration from "
            f"{VISION_PIXEL_STAGE_CALIBRATION_PATH} ({exc}); "
            f"all AutoTrackXY rows will fall back to their CSV X_mm/Y_mm."
        )
        return None

    try:
        with open(VISION_ELECTRODE_ALIGNMENT_PATH) as f:
            alignment_payload = json.load(f)
        layout = LayoutModel.from_json(VISION_ELECTRODE_LAYOUT_PATH)
        layout.transform = np.asarray(alignment_payload['transform'], dtype=np.float32)
        tracked = []
        for entry in alignment_payload['electrodes']:
            circle = Circle(
                x=float(entry['x_px']),
                y=float(entry['y_px']),
                radius=float(entry['radius_px']),
                layout_index=int(entry['layout_index']),
                on_image=bool(entry.get('on_image', True)),
            )
            tracked.append(circle)
    except Exception as exc:
        log_fn(
            f"[WARN] AutoTrackXY: could not load electrode layout/alignment "
            f"({exc}); all AutoTrackXY rows will fall back to their CSV X_mm/Y_mm."
        )
        return None

    try:
        cap = _open_vision_camera(VISION_CAMERA_BACKEND, VISION_CAMERA_INDEX)
        ok, _frame = cap.read()
        if not ok:
            raise RuntimeError('camera opened but returned no frame')
    except Exception as exc:
        log_fn(
            f"[WARN] AutoTrackXY: could not open camera ({exc}); "
            f"all AutoTrackXY rows will fall back to their CSV X_mm/Y_mm."
        )
        return None

    probe_detector = None
    try:
        from vision.probe_detector import ProbeDetector
        import config as _config

        probe_detector = ProbeDetector(clahe_clip=getattr(_config, 'VISION_PROBE_CLAHE_CLIP', 4.0))
    except Exception as exc:
        log_fn(
            f"[WARN] AutoTrackXY: probe-tip detector unavailable ({exc}); "
            f"AutoContactZ will still run and seed electrode Z, but without "
            f"Z-parallax samples during this run."
        )

    log_fn(
        f"[AutoTrackXY] vision tracking initialized: {len(tracked)} electrode(s), "
        f"camera {VISION_CAMERA_BACKEND}:{VISION_CAMERA_INDEX}"
    )
    return {
        'calibration': calibration,
        'z_ref_mm': z_ref_mm,
        'layout': layout,
        'tracked': tracked,
        'cap': cap,
        'probe_detector': probe_detector,
        'last_check_row_idx': None,
        'last_check_time': None,
        'electrode_stage_cache': {},
        # Last live-tracked position actually applied to a row for each
        # electrode (layout_index -> (stage_x, stage_y)) -- the sanity-bound
        # check in the main row loop compares against this, not the row's
        # static CSV X_mm/Y_mm, so genuine gradual drift keeps being trusted
        # over a long run instead of eventually exceeding a fixed original
        # baseline. Unlike electrode_stage_cache (refreshed every drift-check
        # cycle regardless of trust), this only updates when a correction is
        # actually accepted.
        'trusted_electrode_positions': {},
    }


def _probe_occluded_layout_indices(vision_runtime, motor):
    """
    layout_index set for electrodes the probe arm is currently over (their
    last-known stage X sits more than VISION_PROBE_OCCLUSION_X_MARGIN_MM to
    the "covered" side of the probe tip's current stage X) -- skipped
    entirely from this cycle's redetection, since the arm occludes/confuses
    the Hough search there. Uses vision_runtime['electrode_stage_cache']
    (populated at the end of the previous drift-check cycle) as each
    electrode's "current" stage X -- a cycle of staleness here is fine,
    this is a coarse geometric exclusion, not a precision measurement.
    Returns None (no exclusion) on any failure, matching this module's
    other vision fallbacks.
    """
    if motor is None:
        return None
    try:
        probe_x_mm = float(motor.get_position('X'))
    except Exception:
        return None
    cache = vision_runtime.get('electrode_stage_cache') or {}
    excluded = set()
    for layout_index, stage_xy in cache.items():
        if stage_xy is None:
            continue
        if float(stage_xy[0]) < probe_x_mm - VISION_PROBE_OCCLUSION_X_MARGIN_MM:
            excluded.add(layout_index)
    return excluded


def _maybe_refresh_vision_drift_correction(vision_runtime, row_idx, motor=None, log_fn=print):
    """
    Run a drift-correction cycle if due (by row-count or elapsed-time
    cadence), updating vision_runtime['electrode_stage_cache'] in place.
    Any failure anywhere in this cycle is caught and logged; it never raises
    and never blocks the row -- callers just keep whatever cache they had.

    motor, if given, is used to skip redetection for electrodes the probe
    arm is currently over (see _probe_occluded_layout_indices) -- omitted
    entirely (no exclusion) if motor is None.
    """
    last_idx = vision_runtime['last_check_row_idx']
    last_time = vision_runtime['last_check_time']
    now = time.time()
    due = (
        last_idx is None
        or (row_idx - last_idx) >= VISION_DRIFT_RECHECK_EVERY_N_ROWS
        or last_time is None
        or (now - last_time) >= VISION_DRIFT_RECHECK_EVERY_S
    )
    if not due:
        return

    vision_runtime['last_check_row_idx'] = row_idx
    vision_runtime['last_check_time'] = now

    try:
        from vision.electrode_drift import detect_and_refit_frame
        from vision_stage_mapper import pixel_to_stage_xy

        cap = vision_runtime['cap']
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError('camera returned no frame')

        min_confident = max(
            VISION_DRIFT_MIN_CONFIDENT_ELECTRODES,
            math.ceil(vision_runtime['layout'].n * VISION_DRIFT_MIN_CONFIDENT_ELECTRODES_FRACTION),
        )
        result = detect_and_refit_frame(
            frame,
            vision_runtime['layout'],
            vision_runtime['tracked'],
            min_confident=min_confident,
            ema_alpha=VISION_DRIFT_EMA_ALPHA,
            max_projected_deviation_radii=VISION_DRIFT_MAX_PROJECTED_DEVIATION_RADII,
            # Electrodes near the probe's current stage X are no longer
            # excluded from redetection here -- that used to hard-skip the
            # Hough search there entirely (detect_and_refit_frame's
            # excluded_layout_indices), preventing a legitimate detection
            # from ever being attempted in that region at all.
            # _probe_occluded_layout_indices is kept (and still tested) for
            # any future diagnostic use, just not wired in as a gate here.
        )
        log_fn(f"  AutoTrackXY: drift-check cycle -> {'; '.join(result.notes)}")

        if not result.refit_performed:
            return
        if (
            result.inlier_fraction is not None
            and result.inlier_fraction < VISION_DRIFT_MIN_INLIER_FRACTION
        ):
            log_fn(
                f"  AutoTrackXY: refit inlier fraction {result.inlier_fraction:.2f} "
                f"< {VISION_DRIFT_MIN_INLIER_FRACTION}; not trusting this cycle's positions"
            )
            return

        from vision_stage_mapper import correct_pixel_for_z_parallax, parallax_within_trusted_range

        calibration = vision_runtime['calibration']
        z_ref_mm = vision_runtime.get('z_ref_mm')
        z_store = vision_runtime.get('z_store')
        layout = vision_runtime.get('layout')
        parallax = None if z_store is None else z_store.z_parallax
        for circle in vision_runtime['tracked']:
            if circle.layout_index is None:
                continue
            layout_index = int(circle.layout_index)
            pixel_x, pixel_y = circle.smoothed_x, circle.smoothed_y
            if parallax is not None and z_ref_mm is not None:
                xy_mm = _layout_xy_mm_for_layout_index(layout, layout_index)
                seed_z = z_store.get_seed_or_estimate(layout_index, xy_mm=xy_mm)
                if seed_z is not None:
                    delta_z = seed_z - z_ref_mm
                    if parallax_within_trusted_range(parallax, delta_z, VISION_Z_PARALLAX_MAX_EXTRAPOLATION_MM):
                        pixel_x, pixel_y = correct_pixel_for_z_parallax(parallax, pixel_x, pixel_y, delta_z)
            stage_x, stage_y = pixel_to_stage_xy(calibration, pixel_x, pixel_y)
            vision_runtime['electrode_stage_cache'][layout_index] = (stage_x, stage_y)

    except Exception as exc:
        log_fn(f"  AutoTrackXY: drift-correction cycle failed ({exc}); keeping last-known positions")


def _vision_probe_pixel_sample_fn(vision_runtime):
    """
    Build a zero-arg probe-tip pixel sampler closure for the contact-search
    hooks (see find_contact_z's probe_pixel_sample_fn), backed by the same
    camera vision_runtime already has open for AutoTrackXY. Returns None if
    vision_runtime/its camera/detector aren't available -- contact search
    still runs and still seeds electrode Z, just without a parallax sample
    for that event.
    """
    if vision_runtime is None:
        return None
    cap = vision_runtime.get('cap')
    detector = vision_runtime.get('probe_detector')
    if cap is None or detector is None:
        return None

    def _sample():
        try:
            ok, frame_bgr = cap.read()
            if not ok:
                return None
            tip = detector.detect(frame_bgr)
            if tip is None or not tip.detected:
                return None
            return float(tip.x), float(tip.y)
        except Exception:
            return None

    return _sample


def _layout_xy_mm_for_layout_index(layout, layout_index):
    """This electrode's physical position in the DXF layout's own template
    coordinate frame, or None if no layout is available or the index is
    out of range. Feeds the Z-surface plane fit -- see
    vision/electrode_z_seed.py::ZPlaneFit."""
    if layout is None or layout_index is None:
        return None
    try:
        if not (0 <= int(layout_index) < layout.n):
            return None
        u, v = layout.template[int(layout_index)]
        return float(u), float(v)
    except Exception:
        return None


def _record_electrode_z_contact(z_store, row, measurement_z, parallax_sample, layout=None, log_fn=print):
    """
    Common glue for a successful AutoContactZ search: derive the electrode
    identity from the row's Label (E<N> convention, layout_index =
    electrode_id - 1, matching the rest of AutoTrackXY), update its Z seed
    in z_store, and contribute a parallax sample if one was captured. Never
    raises -- a failure here is logged and the row proceeds regardless,
    same as every other vision fallback in this module.
    """
    if z_store is None:
        return
    electrode_id = _extract_electrode_id(row.get('Label', ''))
    if electrode_id is None:
        return
    layout_index = electrode_id - 1
    try:
        fit_happened = z_store.record_contact(
            layout_index, float(measurement_z), source='auto_contact', parallax_sample=parallax_sample,
            xy_mm=_layout_xy_mm_for_layout_index(layout, layout_index),
        )
        z_store.save(VISION_ELECTRODE_Z_SEED_PATH)
    except Exception as exc:
        log_fn(f"  AutoContactZ: could not record electrode Z seed ({exc})")
        return
    n_samples = len(z_store.parallax_samples)
    if fit_happened:
        log_fn(
            f"  AutoContactZ: electrode #{layout_index + 1} Z seed = {float(measurement_z):.3f} mm; "
            f"Z-parallax slope (re)fit from {n_samples} samples"
        )
    else:
        log_fn(
            f"  AutoContactZ: electrode #{layout_index + 1} Z seed = {float(measurement_z):.3f} mm "
            f"({n_samples} parallax sample(s) so far)"
        )


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
    vision_runtime=None,
):
    os.makedirs(result_root, exist_ok=True)

    prev_temp = None
    prev_gas = None
    prev_pos = None
    contact_z_cache = {}
    exact_contact_z_cache = {}
    # Persistent, disk-backed per-electrode Z-height store + pooled Z-parallax
    # slope (see vision/electrode_z_seed.py). Independent of vision_runtime --
    # AutoContactZ's own OCV-confirmed Z seeds it regardless of whether
    # AutoTrackXY vision tracking is active; only the parallax-sample half
    # needs a camera (see _vision_probe_pixel_sample_fn).
    try:
        from vision.electrode_z_seed import ElectrodeZCalibrationStore

        z_store = ElectrodeZCalibrationStore.load(VISION_ELECTRODE_Z_SEED_PATH)
    except Exception as exc:
        log_fn(f"[WARN] AutoContactZ: electrode Z-seed store unavailable ({exc}); contact searches will not be seeded/persisted.")
        z_store = None
    if vision_runtime is not None and z_store is not None:
        vision_runtime['z_store'] = z_store
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

            if vision_runtime is not None and _parse_boolish(row.get('AutoTrackXY'), default=False):
                electrode_id = _extract_electrode_id(label)
                if electrode_id is None:
                    log_fn("  AutoTrackXY: no E<N> electrode id in Label; using CSV X_mm/Y_mm")
                else:
                    _maybe_refresh_vision_drift_correction(vision_runtime, idx, motor=motor, log_fn=log_fn)
                    layout_index = electrode_id - 1
                    corrected_xy = vision_runtime['electrode_stage_cache'].get(layout_index)
                    if corrected_xy is None:
                        log_fn(
                            f"  AutoTrackXY: no vision-corrected position yet for E{electrode_id}; "
                            f"using CSV X_mm/Y_mm"
                        )
                    else:
                        csv_x = _parse_optional_float(row.get('X_mm'), default=None)
                        csv_y = _parse_optional_float(row.get('Y_mm'), default=None)
                        # Compare against the last position this run actually
                        # trusted for this electrode, not the static CSV
                        # value -- a fixed original baseline would eventually
                        # reject genuine, gradual, cumulative drift once
                        # enough of it has accumulated, even though each
                        # individual step was a small, plausible continuation
                        # of the last. Falls back to the CSV value as the
                        # comparison baseline only for this electrode's first
                        # touch this run, when no trusted position exists yet.
                        trusted_xy = vision_runtime['trusted_electrode_positions'].get(layout_index)
                        reference_xy = trusted_xy if trusted_xy is not None else (csv_x, csv_y)
                        dist_mm = (
                            ((corrected_xy[0] - reference_xy[0]) ** 2 + (corrected_xy[1] - reference_xy[1]) ** 2) ** 0.5
                            if reference_xy[0] is not None and reference_xy[1] is not None else None
                        )
                        if dist_mm is not None and dist_mm > VISION_DRIFT_MAX_CORRECTION_MM:
                            log_fn(
                                f"  AutoTrackXY: vision correction for E{electrode_id} is "
                                f"{dist_mm:.3f} mm from "
                                f"{'last-trusted' if trusted_xy is not None else 'CSV'} position "
                                f"(> {VISION_DRIFT_MAX_CORRECTION_MM} mm sanity bound); "
                                f"falling back to CSV X_mm/Y_mm"
                            )
                        else:
                            row = row.copy()
                            row['X_mm'] = corrected_xy[0]
                            row['Y_mm'] = corrected_xy[1]
                            vision_runtime['trusted_electrode_positions'][layout_index] = corrected_xy
                            log_fn(
                                f"  AutoTrackXY: E{electrode_id} position corrected to "
                                f"X={corrected_xy[0]:.3f} mm, Y={corrected_xy[1]:.3f} mm (vision-tracked)"
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

            auto_contact_z = bool(bl) and _parse_boolish(row.get('AutoContactZ'), default=False)
            exact_key = _contact_exact_key(row) if auto_contact_z else None
            skip_memory = _parse_boolish(row.get('ContactSkipMemory'), default=False)
            exact_cache_z = (
                None if skip_memory
                else exact_contact_z_cache.get(exact_key) if exact_key is not None else None
            )
            pos_now = tuple(
                _parse_optional_float(row.get(f'{ax}_mm'), default=None)
                for ax in ['X', 'Y', 'Z']
            )
            if motor and any(v is not None for v in pos_now):
                if prev_pos != pos_now:
                    move_row = row
                    if auto_contact_z and pos_now[2] is not None:
                        move_row = row.copy()
                        if exact_cache_z is not None:
                            # An exact-conditions contact was already
                            # confirmed earlier in this run -- find_contact_z
                            # won't be called at all below, so go straight to
                            # that known-good height rather than hovering at
                            # the search-start offset with nothing left to
                            # bring it down the rest of the way.
                            move_row['Z_mm'] = float(exact_cache_z)
                        else:
                            # Go straight to the search-start height (seed +
                            # offset) instead of the raw seed -- moving to
                            # the seed first would send the tip down to/
                            # through the electrode surface at full move
                            # speed before find_contact_z's own careful
                            # step-wise approach even begins.
                            move_row['Z_mm'] = contact_search_start_z(motor, row)
                    move_stage(motor, move_row)
                    prev_pos = pos_now
                else:
                    log_fn("  Stage position unchanged - skipping move")
            elif motor:
                log_fn("  Stage move skipped for this row")

            if motor and auto_contact_z:
                cache_key = _contact_position_key(row)
                contact_confirmed = True
                if exact_cache_z is not None:
                    measurement_z = float(exact_cache_z)
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
                    try:
                        measurement_z, parallax_sample = find_contact_z(
                            motor, bl, row, log_fn=log_fn,
                            probe_pixel_sample_fn=_vision_probe_pixel_sample_fn(vision_runtime),
                        )
                    except ContactNotConfirmedError as exc:
                        measurement_z = exc.last_z
                        parallax_sample = None
                        contact_confirmed = False
                        log_fn(
                            f"  AutoContactZ: contact NOT confirmed (OCV never stabilized); "
                            f"collecting measurement anyway at last-probed Z={measurement_z:.3f} mm"
                        )
                    if contact_confirmed:
                        _record_electrode_z_contact(
                            z_store, row, measurement_z, parallax_sample,
                            layout=None if vision_runtime is None else vision_runtime.get('layout'),
                            log_fn=log_fn,
                        )
                if cache_key is not None and contact_confirmed:
                    contact_z_cache[cache_key] = measurement_z
                if exact_key is not None and contact_confirmed:
                    exact_contact_z_cache[exact_key] = measurement_z
                row = row.copy()
                row['Z_mm'] = measurement_z
                row['ContactConfirmed'] = contact_confirmed
                pos_now = (
                    _parse_optional_float(row.get('X_mm'), default=None),
                    _parse_optional_float(row.get('Y_mm'), default=None),
                    measurement_z,
                )
                prev_pos = pos_now

            peis_bandwidth = row.get('PEIS_Bandwidth')
            if peis_bandwidth is not None and (pd.isna(peis_bandwidth) or str(peis_bandwidth).strip() == ''):
                peis_bandwidth = None
            peis_n_average = int(_parse_optional_float(row.get('PEIS_NAverage'), default=1))
            skip_ca = _parse_boolish(row.get('SkipCA'), default=False)

            save_dir = result_root
            if skip_ca and _row_uses_adaptive_runtime(row):
                # SkipCA must be an absolute guarantee, not just something
                # rapid_eis_sequence honors -- the adaptive live-seeded
                # protocol is fundamentally CA/FFT-seeded (it has no meaning
                # without a CA trace to analyze), so it can't "skip CA"
                # internally. Route around it entirely and run a guaranteed
                # CA-free PEIS-only measurement instead.
                log_fn("  [SkipCA] CA disabled for this row -- running PEIS-only instead of the adaptive CA-seeded protocol")
                sequence_result = normal_sequence(
                    biologic=bl,
                    v_dc=float(row['V_dc']),
                    peis_f_high=float(row.get('PEIS_fHigh', 1e5)),
                    peis_f_low=float(row.get('PEIS_fLow', 0.1)),
                    peis_npts=int(float(row.get('PEIS_nPts', 60))),
                    amplitude_mv=float(row.get('dV', 0.03)) * 1000.0,
                    bandwidth=peis_bandwidth,
                    n_average=peis_n_average,
                    save_dir=save_dir,
                    label=label,
                )
                measurement_mode = 'normal_eis'
            elif measurement_mode == 'normal_eis':
                sequence_result = normal_sequence(
                    biologic=bl,
                    v_dc=float(row['V_dc']),
                    peis_f_high=float(row.get('PEIS_fHigh', 1e5)),
                    peis_f_low=float(row.get('PEIS_fLow', 0.1)),
                    peis_npts=int(float(row.get('PEIS_nPts', 60))),
                    amplitude_mv=float(row.get('dV', 0.03)) * 1000.0,
                    bandwidth=peis_bandwidth,
                    n_average=peis_n_average,
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
                        skip_ca=skip_ca,
                        bandwidth=peis_bandwidth,
                        n_average=peis_n_average,
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
                'ContactConfirmed': row.get('ContactConfirmed', ''),
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
    except Exception:
        # Record which row was actually in progress when a genuine failure
        # (hardware disconnected, safety lock, anything unexpected -- the
        # OCV-not-confirmed case above no longer reaches here) aborted the
        # run, rather than leaving main() to guess by mutating results[-1]
        # (which is always the PREVIOUS, already-successful row, since a
        # failed row is never appended here).
        results.append({
            'Label': label if 'label' in locals() else 'unknown',
            'Temperature': '', 'GasA_setting': '', 'GasB_setting': '',
            'V_dc': '', 'MeasurementMode': '', 'Status': 'ERROR',
            'ContactConfirmed': '',
        })
        raise
    finally:
        _shutdown_adaptive_runtime(adaptive_runtime)
        # Written here (not by main(), which never receives `results` at
        # all when _run_conditions raises, since the assignment
        # `results = _run_conditions(...)` never completes) so the log is
        # still saved -- with the error row above -- when a run aborts
        # partway through, not just on a fully successful run.
        if results:
            df_res = pd.DataFrame(results)
            summary_path = os.path.join(result_root, 'measurement_log.csv')
            df_res.to_csv(summary_path, index=False)
            log_fn(f"\nMeasurement log saved: {summary_path}")

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
    needs_auto_track_xy = (
        'AutoTrackXY' in df.columns and df['AutoTrackXY'].apply(lambda v: _parse_boolish(v, default=False)).any()
    )
    needs_motor = any(
        col in df.columns and _series_has_value(df[col])
        for col in ['X_mm', 'Y_mm', 'Z_mm']
    ) or (
        'AutoContactZ' in df.columns and df['AutoContactZ'].apply(lambda v: _parse_boolish(v, default=False)).any()
    ) or needs_auto_track_xy

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

    vision_runtime = None
    if needs_auto_track_xy:
        if not VISION_AUTO_TRACK_XY_ENABLED:
            print(
                "[WARN] AutoTrackXY rows present in CSV but "
                "config.VISION_AUTO_TRACK_XY_ENABLED=False; "
                "all AutoTrackXY rows will use their CSV X_mm/Y_mm untouched."
            )
        else:
            vision_runtime = _init_vision_tracking_runtime(log_fn=print)
            if vision_runtime is None:
                print(
                    "[WARN] AutoTrackXY requested but vision tracking could not be "
                    "initialized; all AutoTrackXY rows will fall back to their CSV X_mm/Y_mm."
                )

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
            vision_runtime=vision_runtime,
        )

    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        # _run_conditions's own finally block already saved
        # measurement_log.csv with whatever rows completed before the
        # interrupt.
    except Exception:
        print("\n[!] Error during run:")
        traceback.print_exc()
        # _run_conditions's own except/finally already appended an ERROR
        # row for whichever row was in progress and saved
        # measurement_log.csv -- results = _run_conditions(...) above
        # never completed its assignment on this path, so there is
        # nothing further to do with `results` here (it was never bound).

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
        if vision_runtime is not None:
            try:
                vision_runtime['cap'].release()
            except Exception:
                pass
        print("Done.")


if __name__ == '__main__':
    main()
