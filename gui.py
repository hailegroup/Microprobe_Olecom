# -*- coding: utf-8 -*-
"""
Microprobe Automated Measurement — GUI
Run: python gui.py
"""

import os
import platform
import re
import subprocess
import sys
import time
import threading
import traceback
import queue
import socket
import json

import numpy as np
import pandas as pd
import serial.tools.list_ports
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

VISION_IMPORT_ERRORS = []

try:
    import cv2
except Exception as exc:
    cv2 = None
    VISION_IMPORT_ERRORS.append(f"opencv-python/cv2: {exc}")

try:
    from PIL import Image, ImageTk
except Exception as exc:
    Image = None
    ImageTk = None
    VISION_IMPORT_ERRORS.append(f"Pillow: {exc}")

import config as _config

COM_PORTS = getattr(_config, 'COM_PORTS', {})
BIOLOGIC_IP = getattr(_config, 'BIOLOGIC_IP', '192.109.209.128')
BIOLOGIC_BACKEND = getattr(_config, 'BIOLOGIC_BACKEND', 'olecom')
BIOLOGIC_ALLOW_EASY_FALLBACK = getattr(_config, 'BIOLOGIC_ALLOW_EASY_FALLBACK', False)
BIOLOGIC_OLECOM_BANDWIDTH = getattr(_config, 'BIOLOGIC_OLECOM_BANDWIDTH', 4)
BIOLOGIC_OLECOM_MIN_FFT_DURATION_S = getattr(_config, 'BIOLOGIC_OLECOM_MIN_FFT_DURATION_S', 100.0)
BIOLOGIC_OLECOM_PRE_MAX_S = getattr(_config, 'BIOLOGIC_OLECOM_PRE_MAX_S', 300.0)
BIOLOGIC_OLECOM_SCOUT_MAX_S = getattr(_config, 'BIOLOGIC_OLECOM_SCOUT_MAX_S', 300.0)
BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ = getattr(_config, 'BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ', 0.1)
BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ = getattr(_config, 'BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ', 0.5)
TEMP_SAFETY_ENABLED = getattr(_config, 'TEMP_SAFETY_ENABLED', True)
TEMP_SAFETY_ACTIVE_TARGET_C = getattr(_config, 'TEMP_SAFETY_ACTIVE_TARGET_C', 150.0)
TEMP_SAFETY_MIN_VALID_C = getattr(_config, 'TEMP_SAFETY_MIN_VALID_C', 50.0)
TEMP_SAFETY_MAX_DROP_C = getattr(_config, 'TEMP_SAFETY_MAX_DROP_C', 80.0)
TEMP_SAFETY_POLL_S = getattr(_config, 'TEMP_SAFETY_POLL_S', 5.0)
TEMP_SAFETY_SHUTDOWN_SETPOINT_C = getattr(_config, 'TEMP_SAFETY_SHUTDOWN_SETPOINT_C', 25.0)
STAGE_SAFE_MOVE = getattr(_config, 'STAGE_SAFE_MOVE', {})
MFC_WRITE_ENABLED = getattr(_config, 'MFC_WRITE_ENABLED', False)


def _vision_unavailable(*_args, **_kwargs):
    detail = "; ".join(VISION_IMPORT_ERRORS) or "vision dependencies were not loaded"
    raise RuntimeError(
        "Vision/OM tracking is unavailable on this runner. "
        "Install the optional vision packages or run without camera features. "
        f"Details: {detail}"
    )


try:
    from vision.electrode_mapper import (
        annotate_detections,
        compute_ecc_affine_registration,
        detect_electrode_map,
        detect_electrode_map_rgb,
        detect_live_microscope_electrode_map_rgb,
        detect_markup_electrode_map_rgb,
        detect_reference_microscope_electrode_map_rgb,
        filter_live_overlay_detections,
        refine_circular_electrode_map_rgb,
        scale_detection_table,
        strip_red_markup_from_rgb,
        transform_detection_table,
    )
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.electrode_mapper: {exc}")
    annotate_detections = _vision_unavailable
    compute_ecc_affine_registration = _vision_unavailable
    detect_electrode_map = _vision_unavailable
    detect_electrode_map_rgb = _vision_unavailable
    detect_live_microscope_electrode_map_rgb = _vision_unavailable
    detect_markup_electrode_map_rgb = _vision_unavailable
    detect_reference_microscope_electrode_map_rgb = _vision_unavailable
    filter_live_overlay_detections = _vision_unavailable
    refine_circular_electrode_map_rgb = _vision_unavailable
    scale_detection_table = _vision_unavailable
    strip_red_markup_from_rgb = _vision_unavailable
    transform_detection_table = _vision_unavailable

try:
    from vision.roi_verifier import verify_roi_revisit
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.roi_verifier: {exc}")
    verify_roi_revisit = _vision_unavailable

try:
    from vision_stage_mapper import (
        SampleStageReference,
        solve_sample_to_stage_affine_calibration,
        sample_to_stage_xy,
    )
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision_stage_mapper: {exc}")

    class SampleStageReference:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    solve_sample_to_stage_affine_calibration = _vision_unavailable
    sample_to_stage_xy = _vision_unavailable

VISION_AVAILABLE = (
    cv2 is not None
    and Image is not None
    and ImageTk is not None
    and not any(err.startswith("vision.") for err in VISION_IMPORT_ERRORS)
)

# ── 측정 관련 import (연결 실패해도 GUI는 뜨도록) ───────────────────────────
def _safe_import():
    mods = {}
    try:
        backend = str(BIOLOGIC_BACKEND).strip().lower()
        if backend == 'olecom':
            from driver_biologic_olecom import BioLogicController
            print("[INFO] BioLogic backend: EC-Lab OLE-COM")
        else:
            from driver_biologic import BioLogicController
            print("[INFO] BioLogic backend: easy-biologic legacy")
        mods['biologic'] = BioLogicController
    except Exception as e:
        mods['biologic'] = None
        print(f"[WARN] BioLogic import failed for backend={BIOLOGIC_BACKEND!r}: {e}")
        if str(BIOLOGIC_BACKEND).strip().lower() == 'olecom' and BIOLOGIC_ALLOW_EASY_FALLBACK:
            try:
                from driver_biologic import BioLogicController
                mods['biologic'] = BioLogicController
                print("[WARN] Falling back to easy-biologic; power-cycle may be needed before OLE-COM works again")
            except Exception as fallback_exc:
                print(f"[WARN] easy-biologic fallback import also failed: {fallback_exc}")
    try:
        from driver_motor import MDriveMotor
        mods['motor'] = MDriveMotor
    except Exception as e:
        mods['motor'] = None
    try:
        from driver_temp import WatlowController
        mods['temp'] = WatlowController
    except Exception as e:
        mods['temp'] = None
    try:
        from driver_mfc import AeraMFC
        mods['mfc'] = AeraMFC
    except Exception as e:
        mods['mfc'] = None
    try:
        from measurement_sequence import rapid_eis_sequence, normal_eis_sequence
        mods['sequence'] = rapid_eis_sequence
        mods['rapid_sequence'] = rapid_eis_sequence
        mods['normal_sequence'] = normal_eis_sequence
    except Exception as e:
        mods['sequence'] = None
        mods['rapid_sequence'] = None
        mods['normal_sequence'] = None
    return mods

MODS = _safe_import()

# ── 색상 상수 ───────────────────────────────────────────────────────────────
CLR_BG      = '#f5f5f5'
CLR_HEADER  = '#2c3e50'
CLR_GREEN   = '#27ae60'
CLR_RED     = '#e74c3c'
CLR_ORANGE  = '#e67e22'
CLR_BLUE    = '#2980b9'
CLR_LGRAY   = '#ecf0f1'
CLR_GOLD    = '#f1c40f'
SKIP_TOKENS = {'', 'nan', 'none', 'non', 'skip', '-', 'na', 'n/a'}
GAS_SETTING_COLUMNS = {
    'A': ('GasA_setting', 'GasA_sccm'),
    'B': ('GasB_setting', 'GasB_sccm'),
}
MANUAL_QUICK_F_HIGH_HZ = 100.0
MANUAL_QUICK_F_LOW_HZ = 0.1
MANUAL_QUICK_CA_DT_S = 0.1
MANUAL_RECOMMENDATION_MAX_LF_HZ = 1.0
MONITOR_TIME_SERIES_MAX_POINTS = 5000
MONITOR_NYQUIST_MAX_POINTS = 1200
MONITOR_MAX_ABS_PLOT_VALUE = 1.0e15
MONITOR_QUEUE_BATCH_LIMIT = 120
MONITOR_REDRAW_INTERVAL_MS = 250
MONITOR_DROP_LIVE_QUEUE_ABOVE = 200
MANUAL_OCV_POLL_INTERVAL_MS = 1000
ENABLE_BIOLOGIC_LIVE_SCALAR_POLL = False
CONTACT_CONFIRM_DURATION_S = 10.0
CONTACT_CONFIRM_POLL_S = 1.0


def _parse_optional_float(value, default=None):
    if value is None:
        return default
    if pd.isna(value):
        return default
    text = str(value).strip().lower()
    if text in SKIP_TOKENS:
        return default
    return float(value)


def _parse_boolish(value, default=False):
    if value is None:
        return default
    if pd.isna(value):
        return default
    text = str(value).strip().lower()
    if text in SKIP_TOKENS:
        return default
    return text in {'1', 'true', 'yes', 'y', 'on'}


def _row_gas_setting(row, channel, default=None):
    for column in GAS_SETTING_COLUMNS[channel]:
        if column in row:
            value = _parse_optional_float(row.get(column), default=None)
            if value is not None:
                return value
    return default


def _label_number(value, *, digits=3, signed=False):
    if value is None:
        return 'NA'
    try:
        number = float(value)
    except Exception:
        return 'NA'
    if abs(number) < (0.5 * (10 ** -int(digits))):
        number = 0.0
    text = f"{number:+.{digits}f}" if signed else f"{number:.{digits}f}"
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    if text in {'-0', '+0'}:
        text = '+0' if signed else '0'
    return text.replace('.', 'p')


def _condition_label(
    *,
    prefix=None,
    temperature=None,
    gas_a=None,
    gas_b=None,
    electrode=None,
    x_mm=None,
    y_mm=None,
    z_mm=None,
    voltage=None,
):
    parts = []
    if prefix:
        parts.append(str(prefix))
    parts.extend([
        f"T{_label_number(temperature, digits=1)}",
        f"GA{_label_number(gas_a, digits=2)}",
        f"GB{_label_number(gas_b, digits=2)}",
    ])
    parts.append(f"E{int(electrode)}" if electrode is not None else "ENA")
    parts.extend([
        f"X{_label_number(x_mm, digits=3)}",
        f"Y{_label_number(y_mm, digits=3)}",
        f"Z{_label_number(z_mm, digits=3)}",
        f"V{_label_number(voltage, digits=3, signed=True)}",
    ])
    return '_'.join(parts)


def _safe_path_part(value, fallback='row'):
    text = str(value or '').strip()
    safe = ''.join(ch if ch.isalnum() or ch in '._+-' else '_' for ch in text)
    safe = safe.strip('._-')
    return safe or fallback


def _json_safe(value):
    if hasattr(value, 'to_dict') and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    return value


def _detect_md_cc4xx_bridge():
    """Return True when the IMS USB bridge is present but not enumerated as a COM port."""
    if platform.system() != 'Windows':
        return False
    try:
        result = subprocess.run(
            [
                'powershell',
                '-NoProfile',
                '-Command',
                "Get-PnpDevice | Where-Object { $_.InstanceId -like 'USB\\VID_10C4&PID_806F*' } | "
                "Select-Object -ExpandProperty InstanceId",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return 'USB\\VID_10C4&PID_806F' in (result.stdout or '')
    except Exception:
        return False


class MicroprobGUI(tk.Tk):
    def __init__(self, *, enable_background_polls=True):
        super().__init__()
        self.title("Microprobe Automation")
        self.geometry("1100x750")
        self.configure(bg=CLR_BG)
        self.resizable(True, True)

        # Hardware objects
        self.bl    = None
        self.motor = None
        self.tc    = None
        self.mfc   = None

        # State
        self.running      = False
        self.stop_flag    = threading.Event()
        self.log_queue    = queue.Queue()
        self.monitor_queue = queue.Queue()
        self.condition_df = pd.DataFrame()
        self._monitor_label = ''
        self._monitor_step = 'Idle'
        self._monitor_dc_points = []
        self._monitor_eis_points = []
        self._monitor_eis_overlay_points = []
        self._monitor_dc_title = 'CA / CP Current Monitor'
        self._monitor_eis_title = 'Impedance Monitor'
        self._monitor_dc_axes = ('Time (s)', 'Current (A)')
        self._monitor_eis_axes = ('Re(Z) (Ohm)', '-Im(Z) (Ohm)')
        self._monitor_last_current = None
        self._monitor_last_voltage = None
        self._monitor_last_impedance = None
        self._monitor_live_values = None
        self._monitor_row_text = 'idle'
        self._monitor_dc_series = None
        self._monitor_eis_series = None
        self._monitor_redraw_pending = False
        self._monitor_last_redraw_s = 0.0
        self._active_run_result_root = None
        self._active_temperature_target_c = None
        self._temp_safety_monitor_stop = None
        self._temp_safety_last_pv_c = None
        self._temp_safety_trip_payload = None
        self._enable_background_polls = bool(enable_background_polls)
        self._available_ports = []
        self._serial_port_var = {
            'motor': tk.StringVar(value=COM_PORTS['motor']),
            'temp': tk.StringVar(value=COM_PORTS['temp']),
            'mfc': tk.StringVar(value=COM_PORTS['mfc']),
        }
        self._biologic_channel_var = tk.StringVar(value='1')
        self._biologic_channel_info = []
        self._active_biologic_channel = 1
        self._olecom_postprocess_queue = queue.Queue()
        self._olecom_postprocess_thread = None
        self._olecom_postprocess_lock = threading.Lock()
        self._olecom_postprocess_hold = False
        self._olecom_pre_learning_factor = 1.30
        self._olecom_pre_learning_min_s = 20.0
        self._olecom_pre_learning_first_default_s = 120.0
        self._run_gas_mode_var = tk.StringVar(value='require_auto')
        self._run_contact_fail_policy_var = tk.StringVar(value='stop')
        self._run_confirm_preflight_var = tk.BooleanVar(value=True)
        self._run_retract_tip_on_done_var = tk.BooleanVar(value=True)
        self._run_retract_tip_mm_var = tk.StringVar(value='1.000')
        self._tree_active_cell = (None, 0)
        self._manual_target = {
            'temp': tk.StringVar(value='600'),
            'temp_ramp': tk.StringVar(value='5.0'),
            'x': tk.StringVar(value='0.000'),
            'y': tk.StringVar(value='0.000'),
            'z': tk.StringVar(value='0.000'),
            'gas_a': tk.StringVar(value='0'),
            'gas_b': tk.StringVar(value='0'),
        }
        self._quick_eis = {
            'label': tk.StringVar(value='manual_eis'),
            'v_dc': tk.StringVar(value='0.300'),
            'amp_mv': tk.StringVar(value='10'),
            'n_pts': tk.StringVar(value='60'),
            'peis_f_high': tk.StringVar(value=f'{MANUAL_QUICK_F_HIGH_HZ:.0f}'),
            'peis_f_low': tk.StringVar(value=f'{MANUAL_QUICK_F_LOW_HZ:.1f}'),
            'cycles': tk.StringVar(value='1'),
        }
        self._quick_rapid = {
            'label': tk.StringVar(value='manual_rapid'),
            'v_dc': tk.StringVar(value='0.300'),
            'dv_mv': tk.StringVar(value='10'),
            'hold_time': tk.StringVar(value='5'),
            'post_hold_time': tk.StringVar(value='3'),
            'ca_duration': tk.StringVar(value='20'),
            'n_pts': tk.StringVar(value='60'),
            'peis_f_high': tk.StringVar(value=f'{MANUAL_QUICK_F_HIGH_HZ:.0f}'),
            'peis_f_low': tk.StringVar(value=f'{MANUAL_QUICK_F_LOW_HZ:.1f}'),
            'cycles': tk.StringVar(value='1'),
        }
        self._contact_search = {
            'start_offset': tk.StringVar(value='0.200'),
            'step_mm': tk.StringVar(value='0.010'),
            'max_drop_mm': tk.StringVar(value='0.400'),
            'max_beyond_seed_mm': tk.StringVar(value='0.200'),
            'ocv_threshold': tk.StringVar(value='0.100'),
            'settle_s': tk.StringVar(value='1.00'),
            'engage_mm': tk.StringVar(value='0.050'),
        }
        self._full_auto = {
            'temperatures': tk.StringVar(value='600, 550, 500'),
            'gas_pairs': tk.StringVar(value='10:30; 30:10'),
            'voltages': tk.StringVar(value='0.0, 0.1, 0.2'),
            'electrode_start': tk.StringVar(value='1'),
            'electrode_end': tk.StringVar(value='8'),
            'x1': tk.StringVar(value='0.000'),
            'y1': tk.StringVar(value='0.000'),
            'xn': tk.StringVar(value='7.000'),
            'yn': tk.StringVar(value='0.000'),
            'z1': tk.StringVar(value='0.000'),
            'zn': tk.StringVar(value='0.000'),
            'auto_contact_z': tk.StringVar(value='1'),
            'contact_start_offset': tk.StringVar(value='0.200'),
            'contact_step': tk.StringVar(value='0.010'),
            'contact_max_drop': tk.StringVar(value='0.400'),
            'contact_max_beyond_seed': tk.StringVar(value='0.200'),
            'contact_ocv_threshold': tk.StringVar(value='0.100'),
            'contact_settle': tk.StringVar(value='1.00'),
            'contact_engage': tk.StringVar(value='0.050'),
            'dv': tk.StringVar(value='0.03'),
            'hold_time': tk.StringVar(value='120'),
            'post_peis_hold_time': tk.StringVar(value='10'),
            'peis_f_high': tk.StringVar(value='100'),
            'peis_f_low': tk.StringVar(value='0.1'),
            'peis_n_pts': tk.StringVar(value='60'),
            'ca_duration': tk.StringVar(value=str(int(BIOLOGIC_OLECOM_SCOUT_MAX_S))),
            'ca_dt': tk.StringVar(value='0.1'),
            'temp_ramp_rate': tk.StringVar(value='5.0'),
            'stable_time': tk.StringVar(value='120'),
            'gas_stable_time': tk.StringVar(value='600'),
        }
        self._full_auto_use = {
            'temperature': tk.BooleanVar(value=True),
            'gas': tk.BooleanVar(value=True),
            'tip': tk.BooleanVar(value=True),
        }
        self._full_auto_entries = {}
        self._full_auto_summary = tk.StringVar(
            value='Semi-auto generator ready'
        )
        self._adaptive_full_auto = {
            'temperatures': tk.StringVar(value='600, 550, 500'),
            'gas_pairs': tk.StringVar(value='10:30; 30:10'),
            'voltages': tk.StringVar(value='0.0, 0.1, 0.2'),
            'electrode_start': tk.StringVar(value='1'),
            'electrode_end': tk.StringVar(value='8'),
            'x1': tk.StringVar(value='0.000'),
            'y1': tk.StringVar(value='0.000'),
            'xn': tk.StringVar(value='7.000'),
            'yn': tk.StringVar(value='0.000'),
            'z1': tk.StringVar(value='0.000'),
            'zn': tk.StringVar(value='0.000'),
            'auto_contact_z': tk.StringVar(value='1'),
            'contact_start_offset': tk.StringVar(value='0.200'),
            'contact_step': tk.StringVar(value='0.010'),
            'contact_max_drop': tk.StringVar(value='0.400'),
            'contact_max_beyond_seed': tk.StringVar(value='0.200'),
            'contact_ocv_threshold': tk.StringVar(value='0.100'),
            'contact_settle': tk.StringVar(value='1.00'),
            'contact_engage': tk.StringVar(value='0.050'),
            'dv': tk.StringVar(value='0.03'),
            'hold_time': tk.StringVar(value='60'),
            'post_peis_hold_time': tk.StringVar(value='10'),
            'peis_f_high': tk.StringVar(value='100'),
            'peis_f_low': tk.StringVar(value='0.1'),
            'peis_n_pts': tk.StringVar(value='60'),
            'ca_duration': tk.StringVar(value='200'),
            'ca_dt': tk.StringVar(value='0.1'),
            'temp_ramp_rate': tk.StringVar(value='5.0'),
            'stable_time': tk.StringVar(value='120'),
            'gas_stable_time': tk.StringVar(value='600'),
            'normal_eis_floor_hz': tk.StringVar(value='0.01'),
        }
        self._adaptive_full_auto_use = {
            'temperature': tk.BooleanVar(value=True),
            'gas': tk.BooleanVar(value=True),
            'tip': tk.BooleanVar(value=True),
        }
        self._adaptive_full_auto_entries = {}
        self._adaptive_full_auto_summary = tk.StringVar(
            value='Full-auto adaptive planner ready'
        )
        self._manual_current = {
            'temp': tk.StringVar(value='-'),
            'x': tk.StringVar(value='-'),
            'y': tk.StringVar(value='-'),
            'z': tk.StringVar(value='-'),
            'gas_a': tk.StringVar(value='-'),
            'gas_b': tk.StringVar(value='-'),
            'gas_a_sp': tk.StringVar(value='-'),
            'gas_b_sp': tk.StringVar(value='-'),
        }
        self._manual_status_var = tk.StringVar(value='Manual control ready')
        self._manual_ocv_var = tk.StringVar(value='OCV: -')
        self._manual_recommendation_var = tk.StringVar(value='Recommendation: -')
        self._monitor_recommendation_var = tk.StringVar(value='Recommendation: -')
        self._manual_measurement_running = False
        self._manual_measurement_stop_event = None
        self._manual_gas_seq = 0
        self._manual_gas_seq_lock = threading.Lock()
        self._manual_ocv_poll_shutdown = False
        self._manual_ocv_poll_inflight = False
        self._adaptive_engine_factory = None
        self._image_backend_var = tk.StringVar(value='dshow')
        self._image_index_var = tk.StringVar(value='0')
        self._image_detector_var = tk.StringVar(value='live')
        self._image_expected_count_var = tk.StringVar(value='')
        self._image_status_var = tk.StringVar(value='Camera idle')
        self._image_detection_var = tk.StringVar(value='Electrodes: -')
        self._image_hint_var = tk.StringVar(value='Hint: if the live overlay looks unreliable, use a pre-shot microscope image or attach the design image for assisted setup.')
        self._image_design_path_var = tk.StringVar(value='')
        self._image_design_status_var = tk.StringVar(value='Design: not loaded')
        self._image_markup_path_var = tk.StringVar(value='')
        self._image_markup_status_var = tk.StringVar(value='Markup: not loaded')
        self._image_target_status_var = tk.StringVar(value='Target: not locked')
        self._image_roi_verify_status_var = tk.StringVar(value='ROI verify: idle')
        self._image_move_gate_status_var = tk.StringVar(value='Move gate: clear')
        self._image_stage_anchor_status_var = tk.StringVar(value='Stage anchor: not set')
        self._image_stage_affine_status_var = tk.StringVar(value='Stage calibration: not solved')
        self._image_stage_swap_xy_var = tk.BooleanVar(value=False)
        self._image_stage_invert_x_var = tk.BooleanVar(value=False)
        self._image_stage_invert_y_var = tk.BooleanVar(value=False)
        self._image_allow_stale_high_override_var = tk.BooleanVar(value=False)
        self._image_monitor_cap = None
        self._image_monitor_running = False
        self._image_monitor_photo = None
        self._image_monitor_frame_idx = 0
        self._image_monitor_detect_every = 8
        self._image_monitor_last_overlay_bgr = None
        self._image_move_gate_label = None
        self._image_move_target_button = None
        self._image_override_weak_roi_button = None
        self._image_design_map = None
        self._image_markup_map = None
        self._image_markup_reference_rgb = None
        self._image_seed_map = None
        self._image_seed_reference_rgb = None
        self._image_monitor_last_frame_rgb = None
        self._image_monitor_last_frame_time_s = None
        self._image_tracking_map = None
        self._image_selected_target = None
        self._image_selected_design_target = None
        self._image_stage_anchor = None
        self._image_stage_calibration_refs = []
        self._image_stage_affine_calibration = None
        self._image_pending_roi_verification = None
        self._image_pending_roi_seed_promotion = False
        self._image_move_requires_review_after_weak_roi = False
        self._image_allow_one_safe_move_after_weak_roi_override = False
        self._image_last_roi_verify_result = None
        self._image_render_shape = None
        self._image_render_size = None
        self._image_render_offset = (0, 0)

        self._build_ui()
        self._image_refresh_move_gate_status()
        if self._enable_background_polls:
            self._poll_log()
            self._poll_monitor()
            self._schedule_manual_ocv_poll()
        self._refresh_port_choices()

    def destroy(self):
        self._manual_ocv_poll_shutdown = True
        try:
            self._image_monitor_stop()
        finally:
            super().destroy()

    # ══════════════════════════════════════════════════════════════════════
    # UI 구성
    # ══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        # ── 상단 헤더 ────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=CLR_HEADER, height=45)
        hdr.pack(fill='x')
        tk.Label(hdr, text="Microprobe Automated Measurement System",
                 bg=CLR_HEADER, fg='white',
                 font=('Segoe UI', 13, 'bold')).pack(side='left', padx=15, pady=10)

        # ── 탭 ───────────────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=True, padx=8, pady=8)

        self.tab_hw   = ttk.Frame(nb)
        self.tab_cond = ttk.Frame(nb)
        self.tab_full = ttk.Frame(nb)
        self.tab_auto = ttk.Frame(nb)
        self.tab_run  = ttk.Frame(nb)
        self.tab_image = ttk.Frame(nb)
        self.tab_live = ttk.Frame(nb)
        self.tab_manual = ttk.Frame(nb)

        nb.add(self.tab_hw,   text='  Hardware  ')
        nb.add(self.tab_cond, text='  CSV List  ')
        nb.add(self.tab_full, text='  Semi-auto  ')
        nb.add(self.tab_auto, text='  Full-auto  ')
        nb.add(self.tab_run,  text='  Run / Monitor  ')
        nb.add(self.tab_image, text='  Image Monitor  ')
        nb.add(self.tab_live, text='  EIS Monitor  ')
        nb.add(self.tab_manual, text='  Manual Control  ')

        self._build_tab_hardware()
        self._build_tab_conditions()
        self._build_tab_full_auto()
        self._build_tab_adaptive_full_auto()
        self._build_tab_run()
        self._build_tab_image()
        self._build_tab_live()
        self._build_tab_manual()

    # ── Tab 1: Hardware ────────────────────────────────────────────────
    def _build_tab_hardware(self):
        f = self.tab_hw
        pad = {'padx': 10, 'pady': 6}

        self._hw_status = {}
        self._hw_btn    = {}

        tk.Label(f, text="Hardware Connections",
                 font=('Segoe UI', 11, 'bold'), bg=CLR_BG).grid(
                 row=0, column=0, columnspan=5, sticky='w', **pad)

        headers = ['Device', 'Interface', '', 'Status', '']
        widths  = [18, 22, 10, 18, 12]
        for c, (h, w) in enumerate(zip(headers, widths)):
            tk.Label(f, text=h, font=('Segoe UI', 9, 'bold'),
                     bg=CLR_LGRAY, width=w,
                     relief='flat', anchor='w').grid(row=1, column=c, sticky='ew', padx=4, pady=2)

        # ── BioLogic row (with editable IP + Auto-detect) ──────────────
        backend_label = 'BioLogic SP-200 (OLE-COM)' if str(BIOLOGIC_BACKEND).strip().lower() == 'olecom' else 'BioLogic SP-200'
        tk.Label(f, text=backend_label, anchor='w', bg=CLR_BG,
                 font=('Segoe UI', 10)).grid(row=2, column=0, sticky='w', **pad)

        ip_frame = tk.Frame(f, bg=CLR_BG)
        ip_frame.grid(row=2, column=1, sticky='w', padx=10, pady=6)
        tk.Label(ip_frame, text='IP:', bg=CLR_BG,
                 font=('Courier', 9), fg='#555').pack(side='left')
        self._biologic_ip = tk.StringVar(value=BIOLOGIC_IP)
        ip_entry = tk.Entry(ip_frame, textvariable=self._biologic_ip,
                            width=16, font=('Courier', 9))
        ip_entry.pack(side='left', padx=2)
        tk.Label(ip_frame, text='Ch:', bg=CLR_BG,
                 font=('Courier', 9), fg='#555').pack(side='left', padx=(8, 0))
        channel_box = ttk.Combobox(
            ip_frame,
            textvariable=self._biologic_channel_var,
            width=4,
            values=['1'],
            state='readonly',
        )
        channel_box.pack(side='left', padx=2)
        channel_box.bind('<<ComboboxSelected>>', lambda _evt: self._selected_biologic_channel())
        self._biologic_channel_box = channel_box

        ttk.Button(f, text='Auto-detect',
                   command=self._autodetect_biologic).grid(row=2, column=2, padx=4, pady=6)

        bl_status = tk.Label(f, text='● Not connected', fg=CLR_RED,
                             bg=CLR_BG, font=('Segoe UI', 10))
        bl_status.grid(row=2, column=3, sticky='w', **pad)
        self._hw_status['biologic'] = bl_status

        bl_btn = ttk.Button(f, text='Connect',
                            command=lambda: self._toggle_connect('biologic'))
        bl_btn.grid(row=2, column=4, **pad)
        self._hw_btn['biologic'] = bl_btn

        # ── Other devices ──────────────────────────────────────────────
        other_devices = [
            (3, 'IMS MDrive Motor',  'motor', 'COM9  9600 bps'),
            (4, 'Watlow EZ-ZONE',    'temp',  'COM3  9600 bps  Modbus'),
            (5, 'Aera MFC (×2)',     'mfc',   'COM5  9600 bps'),
        ]
        self._hw_port_box = {}
        for r, name, key, iface in other_devices:
            tk.Label(f, text=name, anchor='w', bg=CLR_BG,
                     font=('Segoe UI', 10)).grid(row=r, column=0, sticky='w', **pad)
            port_frame = tk.Frame(f, bg=CLR_BG)
            port_frame.grid(row=r, column=1, sticky='w', padx=10, pady=6)
            box = ttk.Combobox(
                port_frame,
                textvariable=self._serial_port_var[key],
                width=12,
                state='readonly'
            )
            box.pack(side='left')
            tk.Label(port_frame, text=iface, anchor='w', bg=CLR_BG,
                     font=('Courier', 9), fg='#555').pack(side='left', padx=(6, 0))
            self._hw_port_box[key] = box

            lbl = tk.Label(f, text='● Not connected', fg=CLR_RED,
                           bg=CLR_BG, font=('Segoe UI', 10))
            lbl.grid(row=r, column=3, sticky='w', **pad)
            self._hw_status[key] = lbl

            btn = ttk.Button(f, text='Connect',
                             command=lambda k=key: self._toggle_connect(k))
            btn.grid(row=r, column=4, **pad)
            self._hw_btn[key] = btn

        # Connect All / Disconnect All
        btn_frame = tk.Frame(f, bg=CLR_BG)
        btn_frame.grid(row=10, column=0, columnspan=5, pady=20, sticky='w', padx=10)
        ttk.Button(btn_frame, text='Connect All',
                   command=self._connect_all).pack(side='left', padx=5)
        ttk.Button(btn_frame, text='Disconnect All',
                   command=self._disconnect_all).pack(side='left', padx=5)
        ttk.Button(btn_frame, text='Refresh COM Ports',
                   command=self._refresh_port_choices).pack(side='left', padx=5)
        ttk.Button(btn_frame, text='Auto Detect Serial',
                   command=self._autodetect_serial_devices).pack(side='left', padx=5)

        # Live readback
        tk.Label(f, text="Live Readback",
                 font=('Segoe UI', 11, 'bold'), bg=CLR_BG).grid(
                 row=11, column=0, columnspan=5, sticky='w', padx=10, pady=(20,4))

        rb_frame = tk.Frame(f, bg=CLR_LGRAY, relief='groove', bd=1)
        rb_frame.grid(row=12, column=0, columnspan=5, sticky='ew', padx=10, pady=4)

        self._rb_temp = self._readback_row(rb_frame, 0, 'Temperature (°C)')
        self._rb_fa   = self._readback_row(rb_frame, 1, 'Gas A output RFX (0-100)')
        self._rb_fb   = self._readback_row(rb_frame, 2, 'Gas B output RFX (0-100)')
        self._rb_x    = self._readback_row(rb_frame, 3, 'Motor X (mm)')
        self._rb_y    = self._readback_row(rb_frame, 4, 'Motor Y (mm)')
        self._rb_z    = self._readback_row(rb_frame, 5, 'Motor Z (mm)')

        ttk.Button(f, text='Read now',
                   command=lambda: threading.Thread(
                       target=self._do_readback, daemon=True).start()
                   ).grid(row=13, column=0, padx=10, pady=6, sticky='w')
        self._port_status_var = tk.StringVar(value='Serial ports: not scanned yet')
        tk.Label(f, textvariable=self._port_status_var, bg=CLR_BG,
                 fg='#555', anchor='w', font=('Segoe UI', 9)).grid(
                 row=14, column=0, columnspan=5, sticky='w', padx=10, pady=(0, 8))

    def _readback_row(self, parent, row, label):
        tk.Label(parent, text=label, bg=CLR_LGRAY, width=25,
                 anchor='w', font=('Segoe UI', 10)).grid(
                 row=row, column=0, padx=10, pady=4)
        var = tk.StringVar(value='—')
        tk.Label(parent, textvariable=var, bg=CLR_LGRAY, width=15,
                 anchor='w', font=('Courier', 10)).grid(
                 row=row, column=1, padx=10)
        return var

    # ── Tab 2: Conditions ──────────────────────────────────────────────
    def _build_tab_conditions(self):
        f = self.tab_cond

        btn_row = tk.Frame(f, bg=CLR_BG)
        btn_row.pack(fill='x', padx=8, pady=6)
        ttk.Button(btn_row, text='Load CSV',
                   command=self._load_csv).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Save CSV',
                   command=self._save_csv).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Add row',
                   command=self._add_row).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Delete row',
                   command=self._del_row).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Copy',
                   command=self._copy_tree_selection).pack(side='left', padx=(14, 4))
        ttk.Button(btn_row, text='Paste',
                   command=self._paste_tree_clipboard).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Load template',
                   command=self._load_template).pack(side='left', padx=4)

        # Treeview table
        cols = ['Label', 'Temperature_C', 'RampRate_C_per_min', 'GasA_setting', 'GasB_setting',
                'X_mm', 'Y_mm', 'Z_mm',
                'AutoContactZ', 'ContactStartOffset_mm', 'ContactStep_mm',
                'ContactMaxDrop_mm', 'ContactMaxBeyondSeed_mm',
                'ContactOCVThreshold_V', 'ContactSettle_s', 'ContactEngage_mm',
                'V_dc', 'dV', 'HoldTime_s', 'PostPEIS_HoldTime_s',
                'PEIS_fHigh', 'PEIS_fLow', 'PEIS_nPts',
                'CA_duration_s', 'CA_dt',
                'StableTime_s', 'GasStableTime_s', 'Skip']
        self._tree_cols = cols

        tree_frame = tk.Frame(f)
        tree_frame.pack(fill='both', expand=True, padx=8, pady=4)

        vsb = ttk.Scrollbar(tree_frame, orient='vertical')
        hsb = ttk.Scrollbar(tree_frame, orient='horizontal')
        self.tree = ttk.Treeview(tree_frame, columns=cols, show='headings',
                                 yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)

        for c in cols:
            w = 120 if c == 'Label' else 80
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, minwidth=50, anchor='center')

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.bind('<Button-1>', self._remember_tree_cell)
        self.tree.bind('<Double-1>', self._edit_cell)
        self.tree.bind('<Control-c>', self._copy_tree_selection)
        self.tree.bind('<Control-C>', self._copy_tree_selection)
        self.tree.bind('<Control-v>', self._paste_tree_clipboard)
        self.tree.bind('<Control-V>', self._paste_tree_clipboard)

    def _build_tab_full_auto(self):
        f = self.tab_full

        intro = tk.LabelFrame(f, text='Semi-auto Condition Generator',
                              bg=CLR_BG, padx=10, pady=10)
        intro.pack(fill='x', padx=8, pady=(8, 6))
        header = tk.Frame(intro, bg=CLR_BG)
        header.pack(fill='x')
        tk.Label(
            header,
            text=('Generate a condition table from temperature, gas, voltage, '
                  'and electrode ranges. Generated rows are sent to the '
                  'CSV List table for review before running.'),
            bg=CLR_BG,
            justify='left',
            anchor='w',
            font=('Segoe UI', 10),
        ).pack(side='left', fill='x', expand=True)
        option_box = tk.LabelFrame(header, text='Disable Unused Hardware', bg=CLR_BG, padx=8, pady=6)
        option_box.pack(side='right', padx=(12, 0))
        ttk.Checkbutton(
            option_box, text='Use temperature',
            variable=self._full_auto_use['temperature'],
            command=self._update_full_auto_field_states
        ).grid(row=0, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use gas',
            variable=self._full_auto_use['gas'],
            command=self._update_full_auto_field_states
        ).grid(row=1, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use tip position',
            variable=self._full_auto_use['tip'],
            command=self._update_full_auto_field_states
        ).grid(row=2, column=0, sticky='w', padx=4)

        grid = tk.Frame(f, bg=CLR_BG)
        grid.pack(fill='x', padx=8, pady=4)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        fields = [
            ('Temperatures (C)', 'temperatures', 0, 0),
            ('Gas pairs A:B setting (0-100)', 'gas_pairs', 0, 2),
            ('Voltages (V)', 'voltages', 1, 0),
            ('Electrode 1 Z seed (mm)', 'z1', 1, 2),
            ('Electrode start', 'electrode_start', 2, 0),
            ('Electrode end', 'electrode_end', 2, 2),
            ('Electrode 1 X (mm)', 'x1', 3, 0),
            ('Electrode 1 Y (mm)', 'y1', 3, 2),
            ('Electrode N X (mm)', 'xn', 4, 0),
            ('Electrode N Y (mm)', 'yn', 4, 2),
            ('Electrode N Z seed (mm)', 'zn', 5, 0),
            ('AutoContactZ (0/1)', 'auto_contact_z', 5, 2),
            ('Contact start offset (mm)', 'contact_start_offset', 6, 0),
            ('Contact step (mm)', 'contact_step', 6, 2),
            ('Contact max drop (mm)', 'contact_max_drop', 7, 0),
            ('Contact max beyond seed (mm)', 'contact_max_beyond_seed', 7, 2),
            ('Contact OCV threshold (V)', 'contact_ocv_threshold', 8, 0),
            ('Contact settle (s)', 'contact_settle', 8, 2),
            ('Contact engage (mm)', 'contact_engage', 9, 0),
            ('dV (V)', 'dv', 10, 0),
            ('Pre-PEIS hold (s)', 'hold_time', 10, 2),
            ('Post-PEIS hold (s)', 'post_peis_hold_time', 11, 0),
            ('PEIS f high (Hz)', 'peis_f_high', 11, 2),
            ('PEIS f low (Hz)', 'peis_f_low', 12, 0),
            ('PEIS n pts', 'peis_n_pts', 12, 2),
            ('CA duration (s)', 'ca_duration', 13, 0),
            ('CA dt (s)', 'ca_dt', 13, 2),
            ('Temp ramp rate (C/min)', 'temp_ramp_rate', 14, 0),
            ('Temp stable time (s)', 'stable_time', 14, 2),
            ('Gas stable time (s)', 'gas_stable_time', 15, 0),
        ]
        for label, key, row, col in fields:
            tk.Label(
                grid, text=label, bg=CLR_BG, anchor='w',
                font=('Segoe UI', 10)
            ).grid(row=row, column=col, sticky='w', padx=(0, 8), pady=4)
            entry = tk.Entry(
                grid, textvariable=self._full_auto[key],
                font=('Courier New', 10), width=28
            )
            entry.grid(row=row, column=col + 1, sticky='ew', padx=(0, 18), pady=4)
            self._full_auto_entries[key] = entry

        help_box = tk.LabelFrame(f, text='Input Format', bg=CLR_BG, padx=10, pady=10)
        help_box.pack(fill='x', padx=8, pady=(4, 6))
        help_lines = [
            'Temperatures / Voltages: comma-separated, for example 600, 550, 500',
            'Gas pairs: semicolon-separated raw DMFC setting pairs (0-100), for example 10:30; 30:10',
            'Tip positions: XY are linearly interpolated from electrode 1 to electrode N',
            'Z is a seed value. AutoContactZ=1 starts 0.2 mm above seed, steps toward contact, then engages by the configured amount.',
            'Uncheck temperature / gas / tip position above to disable those inputs and generate None values for that hardware step.',
            'During runs, gas is set before a temperature change; gas/temp stabilization waits overlap, and unchanged conditions skip their wait.',
            'Post-PEIS CA uses one CA technique with two sequences: Vdc short hold, then Vdc+dV long CA.',
        ]
        for line in help_lines:
            tk.Label(help_box, text=line, bg=CLR_BG, anchor='w',
                     justify='left', font=('Segoe UI', 10)).pack(fill='x', pady=1)

        btn_row = tk.Frame(f, bg=CLR_BG)
        btn_row.pack(fill='x', padx=8, pady=(2, 6))
        ttk.Button(
            btn_row, text='Generate to CSV List',
            command=self._generate_full_auto_conditions
        ).pack(side='left', padx=4)
        ttk.Button(
            btn_row, text='Append to CSV List',
            command=lambda: self._generate_full_auto_conditions(append=True)
        ).pack(side='left', padx=4)

        tk.Label(
            f, textvariable=self._full_auto_summary, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='sunken'
        ).pack(fill='x', padx=8, pady=(0, 8))
        self._update_full_auto_field_states()

    def _build_tab_adaptive_full_auto(self):
        f = self.tab_auto

        intro = tk.LabelFrame(f, text='Full-auto Adaptive Planner',
                              bg=CLR_BG, padx=10, pady=10)
        intro.pack(fill='x', padx=8, pady=(8, 6))
        header = tk.Frame(intro, bg=CLR_BG)
        header.pack(fill='x')
        tk.Label(
            header,
            text=('Prepare the future fully automatic workflow that will use '
                  'optimized analysis results to choose the next measurement '
                  'parameters. For now, this planner generates the same base '
                  'CSV rows while keeping adaptive settings visible in the UI.'),
            bg=CLR_BG,
            justify='left',
            anchor='w',
            font=('Segoe UI', 10),
        ).pack(side='left', fill='x', expand=True)
        option_box = tk.LabelFrame(header, text='Disable Unused Hardware', bg=CLR_BG, padx=8, pady=6)
        option_box.pack(side='right', padx=(12, 0))
        ttk.Checkbutton(
            option_box, text='Use temperature',
            variable=self._adaptive_full_auto_use['temperature'],
            command=self._update_adaptive_full_auto_field_states
        ).grid(row=0, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use gas',
            variable=self._adaptive_full_auto_use['gas'],
            command=self._update_adaptive_full_auto_field_states
        ).grid(row=1, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use tip position',
            variable=self._adaptive_full_auto_use['tip'],
            command=self._update_adaptive_full_auto_field_states
        ).grid(row=2, column=0, sticky='w', padx=4)

        grid = tk.Frame(f, bg=CLR_BG)
        grid.pack(fill='x', padx=8, pady=4)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        fields = [
            ('Temperatures (C)', 'temperatures', 0, 0),
            ('Gas pairs A:B setting (0-100)', 'gas_pairs', 0, 2),
            ('Voltages (V)', 'voltages', 1, 0),
            ('Electrode 1 Z seed (mm)', 'z1', 1, 2),
            ('Electrode start', 'electrode_start', 2, 0),
            ('Electrode end', 'electrode_end', 2, 2),
            ('Electrode 1 X (mm)', 'x1', 3, 0),
            ('Electrode 1 Y (mm)', 'y1', 3, 2),
            ('Electrode N X (mm)', 'xn', 4, 0),
            ('Electrode N Y (mm)', 'yn', 4, 2),
            ('Electrode N Z seed (mm)', 'zn', 5, 0),
            ('AutoContactZ (0/1)', 'auto_contact_z', 5, 2),
            ('Contact start offset (mm)', 'contact_start_offset', 6, 0),
            ('Contact step (mm)', 'contact_step', 6, 2),
            ('Contact max drop (mm)', 'contact_max_drop', 7, 0),
            ('Contact max beyond seed (mm)', 'contact_max_beyond_seed', 7, 2),
            ('Contact OCV threshold (V)', 'contact_ocv_threshold', 8, 0),
            ('Contact settle (s)', 'contact_settle', 8, 2),
            ('Contact engage (mm)', 'contact_engage', 9, 0),
            ('dV scout step (V)', 'dv', 10, 0),
            ('Pre-CA first seed/max (s)', 'hold_time', 10, 2),
            ('Seeded PEIS high (Hz)', 'peis_f_high', 11, 0),
            ('Seeded PEIS n pts', 'peis_n_pts', 11, 2),
            ('dV scout live max (s)', 'ca_duration', 12, 0),
            ('CA/FFT dt (s)', 'ca_dt', 12, 2),
            ('Temp ramp rate (C/min)', 'temp_ramp_rate', 13, 0),
            ('Temp stable time (s)', 'stable_time', 13, 2),
            ('Gas stable time (s)', 'gas_stable_time', 14, 0),
        ]
        for label, key, row, col in fields:
            tk.Label(
                grid, text=label, bg=CLR_BG, anchor='w',
                font=('Segoe UI', 10)
            ).grid(row=row, column=col, sticky='w', padx=(0, 8), pady=4)
            entry = tk.Entry(
                grid, textvariable=self._adaptive_full_auto[key],
                font=('Courier New', 10), width=28
            )
            entry.grid(row=row, column=col + 1, sticky='ew', padx=(0, 18), pady=4)
            self._adaptive_full_auto_entries[key] = entry

        help_box = tk.LabelFrame(f, text='Adaptive Planning Notes', bg=CLR_BG, padx=10, pady=10)
        help_box.pack(fill='x', padx=8, pady=(4, 6))
        help_lines = [
            'Adaptive rows run: learned pre-CA seed -> dV scout CA live-stop -> FFT LF recommendation -> PEIS.',
            'OLE-COM full-auto policy: PEIS high 100 Hz, overlap cap 0.5 Hz, deep limit 0.1 Hz, Nd=10, Na=1, BW4, Auto current range.',
            'PEIS amplitude follows dV scout step, so dV=0.03 V uses 30 mV PEIS amplitude.',
            'Pre-CA learning uses the detected stable decision time x1.3, capped at 300 s; scout/stout is not learned and uses live-stop.',
            'Full-arc/onepage fitting is deferred during voltage ladders, then runs during site/gas/temp transitions or at run end.',
            'Uncheck temperature / gas / tip position above to disable those inputs and generate None values for that hardware step.',
            'During runs, gas is set before a temperature change; gas/temp stabilization waits overlap, and unchanged conditions skip their wait.',
            'OCV-based Z contact finding uses the seed Z and per-row contact settings; analysis-driven next-point updates run inside the live loop.',
        ]
        for line in help_lines:
            tk.Label(help_box, text=line, bg=CLR_BG, anchor='w',
                     justify='left', font=('Segoe UI', 10)).pack(fill='x', pady=1)

        btn_row = tk.Frame(f, bg=CLR_BG)
        btn_row.pack(fill='x', padx=8, pady=(2, 6))
        ttk.Button(
            btn_row, text='Generate to CSV List',
            command=self._generate_adaptive_full_auto_conditions
        ).pack(side='left', padx=4)
        ttk.Button(
            btn_row, text='Append to CSV List',
            command=lambda: self._generate_adaptive_full_auto_conditions(append=True)
        ).pack(side='left', padx=4)

        tk.Label(
            f, textvariable=self._adaptive_full_auto_summary, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='sunken'
        ).pack(fill='x', padx=8, pady=(0, 8))
        self._update_adaptive_full_auto_field_states()

    # ── Tab 3: Run / Monitor ───────────────────────────────────────────
    def _build_tab_run(self):
        f = self.tab_run

        # Controls row
        ctrl = tk.Frame(f, bg=CLR_BG)
        ctrl.pack(fill='x', padx=8, pady=6)

        tk.Label(ctrl, text='Result folder:', bg=CLR_BG).pack(side='left')
        self._result_dir = tk.StringVar(value=os.path.join(os.getcwd(), 'results'))
        tk.Entry(ctrl, textvariable=self._result_dir, width=40).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Browse',
                   command=self._browse_result).pack(side='left', padx=2)

        self._btn_start = ttk.Button(ctrl, text='▶  Start',
                                     command=self._start_run)
        self._btn_start.pack(side='left', padx=(20, 4))
        self._btn_stop = ttk.Button(ctrl, text='■  Stop',
                                    command=self._stop_run, state='disabled')
        self._btn_stop.pack(side='left', padx=4)

        safety = tk.LabelFrame(f, text='Automation Safety / Preflight', bg=CLR_BG, padx=8, pady=6)
        safety.pack(fill='x', padx=8, pady=(0, 6))

        tk.Label(safety, text='Gas handling:', bg=CLR_BG).grid(row=0, column=0, sticky='w', padx=(0, 4), pady=2)
        ttk.Combobox(
            safety,
            textvariable=self._run_gas_mode_var,
            values=('require_auto', 'manual_skip'),
            width=16,
            state='readonly',
        ).grid(row=0, column=1, sticky='w', padx=(0, 12), pady=2)
        tk.Label(
            safety,
            text='require_auto stops if MFC writes are disabled; manual_skip assumes gas was set manually.',
            bg=CLR_BG,
            anchor='w',
            font=('Segoe UI', 9),
        ).grid(row=0, column=2, sticky='w', padx=(0, 8), pady=2)

        tk.Label(safety, text='Contact fail:', bg=CLR_BG).grid(row=1, column=0, sticky='w', padx=(0, 4), pady=2)
        ttk.Combobox(
            safety,
            textvariable=self._run_contact_fail_policy_var,
            values=('stop', 'skip_row'),
            width=16,
            state='readonly',
        ).grid(row=1, column=1, sticky='w', padx=(0, 12), pady=2)
        ttk.Checkbutton(
            safety,
            text='Confirm preflight before run',
            variable=self._run_confirm_preflight_var,
        ).grid(row=1, column=2, sticky='w', pady=2)

        ttk.Checkbutton(
            safety,
            text='Retract tip after successful run (mm)',
            variable=self._run_retract_tip_on_done_var,
        ).grid(row=2, column=0, columnspan=2, sticky='w', padx=(0, 4), pady=2)
        tk.Entry(
            safety,
            textvariable=self._run_retract_tip_mm_var,
            width=8,
            font=('Courier New', 9),
        ).grid(row=2, column=2, sticky='w', pady=2)
        tk.Label(
            safety,
            text='Default 1 mm. With this stage, smaller Z lifts the tip.',
            bg=CLR_BG,
            anchor='w',
            font=('Segoe UI', 9),
        ).grid(row=2, column=3, sticky='w', padx=(8, 0), pady=2)

        btns = tk.Frame(safety, bg=CLR_BG)
        btns.grid(row=0, column=4, rowspan=3, sticky='e', padx=(12, 0))
        ttk.Button(btns, text='Preflight Check', command=self._preflight_check_dialog).pack(side='left', padx=3)
        ttk.Button(btns, text='Preview Plan', command=self._preview_run_plan).pack(side='left', padx=3)

        # Progress
        prog_frame = tk.Frame(f, bg=CLR_BG)
        prog_frame.pack(fill='x', padx=8, pady=4)

        tk.Label(prog_frame, text='Progress:', bg=CLR_BG).pack(side='left')
        self._progress_var = tk.DoubleVar()
        self._progress_bar = ttk.Progressbar(prog_frame, variable=self._progress_var,
                                              maximum=100, length=400)
        self._progress_bar.pack(side='left', padx=8)
        self._progress_lbl = tk.Label(prog_frame, text='0 / 0', bg=CLR_BG)
        self._progress_lbl.pack(side='left')

        # Status line
        self._status_var = tk.StringVar(value='Ready')
        tk.Label(f, textvariable=self._status_var, bg=CLR_LGRAY,
                 anchor='w', font=('Segoe UI', 10), relief='sunken').pack(
                 fill='x', padx=8, pady=2)

        # Log text
        log_frame = tk.Frame(f)
        log_frame.pack(fill='both', expand=True, padx=8, pady=4)

        self.log_text = tk.Text(log_frame, bg='#1e1e1e', fg='#d4d4d4',
                                font=('Courier New', 9), wrap='word',
                                state='disabled')
        log_sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        log_sb.pack(side='right', fill='y')

        ttk.Button(f, text='Clear log', command=self._clear_log).pack(
            anchor='e', padx=8, pady=2)

    def _build_tab_live(self):
        f = self.tab_live

        header = tk.Frame(f, bg=CLR_BG)
        header.pack(fill='x', padx=8, pady=8)

        self._monitor_run_var = tk.StringVar(value='Run: idle')
        self._monitor_step_var = tk.StringVar(value='Step: idle')
        self._monitor_dc_var = tk.StringVar(value='Current: -')
        self._monitor_eis_var = tk.StringVar(value='Impedance: -')

        for text_var in (self._monitor_run_var, self._monitor_step_var,
                         self._monitor_dc_var, self._monitor_eis_var):
            tk.Label(
                header, textvariable=text_var, bg=CLR_LGRAY, anchor='w',
                font=('Segoe UI', 10), relief='groove', padx=8, pady=6
            ).pack(fill='x', pady=3)

        charts = tk.Frame(f, bg=CLR_BG)
        charts.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        charts.columnconfigure(0, weight=1)
        charts.columnconfigure(1, weight=1)
        charts.rowconfigure(0, weight=1)

        left = tk.Frame(charts, bg=CLR_BG)
        right = tk.Frame(charts, bg=CLR_BG)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
        right.grid(row=0, column=1, sticky='nsew', padx=(4, 0))
        left.rowconfigure(1, weight=1)
        right.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        tk.Label(left, text='CA / CP Current Monitor',
                 bg=CLR_BG, font=('Segoe UI', 11, 'bold')).grid(
                 row=0, column=0, sticky='w', pady=(0, 4))
        self._dc_canvas = tk.Canvas(left, bg='white', highlightthickness=1,
                                    highlightbackground='#cfd8dc')
        self._dc_canvas.grid(row=1, column=0, sticky='nsew')

        tk.Label(right, text='Impedance Monitor (Nyquist)',
                 bg=CLR_BG, font=('Segoe UI', 11, 'bold')).grid(
                 row=0, column=0, sticky='w', pady=(0, 4))
        self._eis_canvas = tk.Canvas(right, bg='white', highlightthickness=1,
                                     highlightbackground='#cfd8dc')
        self._eis_canvas.grid(row=1, column=0, sticky='nsew')

        for canvas in (self._dc_canvas, self._eis_canvas):
            canvas.bind('<Configure>', lambda _e: self._schedule_monitor_redraw(50))

        tk.Label(
            f,
            textvariable=self._monitor_recommendation_var,
            bg=CLR_BG,
            anchor='w',
            font=('Segoe UI', 10),
            fg='#37474f',
        ).pack(fill='x', padx=8, pady=(0, 6))

        ttk.Button(f, text='Reset monitor',
                   command=self._monitor_reset).pack(anchor='e', padx=8, pady=(0, 6))

    def _build_tab_image(self):
        f = self.tab_image

        ctrl = tk.Frame(f, bg=CLR_BG)
        ctrl.pack(fill='x', padx=8, pady=8)

        tk.Label(ctrl, text='Backend', bg=CLR_BG).pack(side='left')
        ttk.Combobox(
            ctrl,
            textvariable=self._image_backend_var,
            values=('dshow', 'any', 'msmf'),
            width=10,
            state='readonly',
        ).pack(side='left', padx=(4, 10))

        tk.Label(ctrl, text='Index', bg=CLR_BG).pack(side='left')
        tk.Entry(ctrl, textvariable=self._image_index_var, width=5).pack(side='left', padx=(4, 10))

        tk.Label(ctrl, text='Detector', bg=CLR_BG).pack(side='left')
        ttk.Combobox(
            ctrl,
            textvariable=self._image_detector_var,
            values=('live', 'reference', 'generic'),
            width=10,
            state='readonly',
        ).pack(side='left', padx=(4, 10))

        tk.Label(ctrl, text='Expected visible electrodes', bg=CLR_BG).pack(side='left')
        tk.Entry(ctrl, textvariable=self._image_expected_count_var, width=6).pack(side='left', padx=(4, 10))

        ttk.Button(ctrl, text='Start Camera', command=self._image_monitor_start).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Stop Camera', command=self._image_monitor_stop).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Freeze Current as Seed', command=self._image_freeze_current_seed).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Clear Seed', command=self._image_clear_seed).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Clear Target', command=self._image_clear_target).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Use Current XY as Anchor', command=self._image_set_stage_anchor_from_current_xy).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Clear Anchor', command=self._image_clear_stage_anchor).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Add Cal Point', command=self._image_add_stage_calibration_point_from_current_target).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Solve Affine', command=self._image_solve_stage_affine_calibration).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Clear Cal', command=self._image_clear_stage_affine_calibration).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Save Cal', command=self._image_save_stage_affine_calibration).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Load Cal', command=self._image_load_stage_affine_calibration).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Projected XY -> Target', command=self._image_apply_projected_stage_xy_to_manual_target).pack(side='left', padx=4)
        self._image_move_target_button = ttk.Button(
            ctrl,
            text='Move Target (Safe)',
            command=lambda: self._run_manual_action(self._image_move_target_safe),
        )
        self._image_move_target_button.pack(side='left', padx=4)
        self._image_override_weak_roi_button = ttk.Button(
            ctrl,
            text='Override Weak ROI Block',
            command=self._image_override_weak_roi_block,
        )
        self._image_override_weak_roi_button.pack(side='left', padx=4)
        tk.Checkbutton(
            ctrl,
            text='Allow stale/high override',
            variable=self._image_allow_stale_high_override_var,
            bg=CLR_BG,
            command=self._image_refresh_move_gate_status,
        ).pack(side='left', padx=(4, 0))

        calib_row = tk.Frame(f, bg=CLR_BG)
        calib_row.pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(calib_row, text='Stage mapping', bg=CLR_BG).pack(side='left')
        tk.Checkbutton(
            calib_row,
            text='Swap XY',
            variable=self._image_stage_swap_xy_var,
            bg=CLR_BG,
            command=self._image_on_stage_projection_controls_changed,
        ).pack(side='left', padx=(8, 6))
        tk.Checkbutton(
            calib_row,
            text='Invert X',
            variable=self._image_stage_invert_x_var,
            bg=CLR_BG,
            command=self._image_on_stage_projection_controls_changed,
        ).pack(side='left', padx=6)
        tk.Checkbutton(
            calib_row,
            text='Invert Y',
            variable=self._image_stage_invert_y_var,
            bg=CLR_BG,
            command=self._image_on_stage_projection_controls_changed,
        ).pack(side='left', padx=6)

        design_row = tk.Frame(f, bg=CLR_BG)
        design_row.pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(design_row, text='Design image', bg=CLR_BG).pack(side='left')
        tk.Entry(design_row, textvariable=self._image_design_path_var, width=70).pack(side='left', padx=(6, 6), fill='x', expand=True)
        ttk.Button(design_row, text='Browse', command=self._image_browse_design).pack(side='left', padx=2)
        ttk.Button(design_row, text='Load Design', command=self._image_load_design).pack(side='left', padx=2)

        markup_row = tk.Frame(f, bg=CLR_BG)
        markup_row.pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(markup_row, text='Markup image', bg=CLR_BG).pack(side='left')
        tk.Entry(markup_row, textvariable=self._image_markup_path_var, width=70).pack(side='left', padx=(6, 6), fill='x', expand=True)
        ttk.Button(markup_row, text='Browse', command=self._image_browse_markup).pack(side='left', padx=2)
        ttk.Button(markup_row, text='Load Markup', command=self._image_load_markup).pack(side='left', padx=2)

        tk.Label(
            f,
            text='Swift/OpenCV live preview for tip/electrode monitoring. This is the first GUI bridge for future move-after-ROI verification and electrode selection.',
            bg=CLR_BG,
            anchor='w',
            justify='left',
            font=('Segoe UI', 10),
        ).pack(fill='x', padx=8, pady=(0, 6))

        tk.Label(
            f, textvariable=self._image_status_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        ).pack(fill='x', padx=8, pady=3)
        tk.Label(
            f, textvariable=self._image_detection_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        ).pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(
            f, textvariable=self._image_design_status_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        ).pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(
            f, textvariable=self._image_markup_status_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        ).pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(
            f, textvariable=self._image_target_status_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        ).pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(
            f, textvariable=self._image_roi_verify_status_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        ).pack(fill='x', padx=8, pady=(0, 6))
        self._image_move_gate_label = tk.Label(
            f, textvariable=self._image_move_gate_status_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        )
        self._image_move_gate_label.pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(
            f, textvariable=self._image_stage_anchor_status_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        ).pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(
            f, textvariable=self._image_stage_affine_status_var, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=6
        ).pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(
            f, textvariable=self._image_hint_var, bg=CLR_BG,
            anchor='w', justify='left', font=('Segoe UI', 10), fg='#546e7a'
        ).pack(fill='x', padx=8, pady=(0, 6))

        self._image_monitor_label = tk.Label(
            f,
            bg='black',
            anchor='center',
            text='Camera preview will appear here',
            fg='white',
            font=('Segoe UI', 11),
        )
        self._image_monitor_label.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self._image_monitor_label.bind('<Button-1>', self._image_monitor_click_select_target)

    def _build_tab_manual(self):
        host = tk.Frame(self.tab_manual, bg=CLR_BG)
        host.pack(fill='both', expand=True)

        self._manual_scroll_canvas = tk.Canvas(
            host,
            bg=CLR_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        manual_scrollbar = ttk.Scrollbar(
            host,
            orient='vertical',
            command=self._manual_scroll_canvas.yview,
        )
        self._manual_scroll_canvas.configure(yscrollcommand=manual_scrollbar.set)
        self._manual_scroll_canvas.pack(side='left', fill='both', expand=True)
        manual_scrollbar.pack(side='right', fill='y')

        f = tk.Frame(self._manual_scroll_canvas, bg=CLR_BG)
        self._manual_scroll_window = self._manual_scroll_canvas.create_window(
            (0, 0), window=f, anchor='nw'
        )

        def _sync_manual_scrollregion(_event=None):
            self._manual_scroll_canvas.configure(
                scrollregion=self._manual_scroll_canvas.bbox('all')
            )

        def _sync_manual_canvas_width(event):
            self._manual_scroll_canvas.itemconfigure(
                self._manual_scroll_window,
                width=event.width,
            )

        f.bind('<Configure>', _sync_manual_scrollregion)
        self._manual_scroll_canvas.bind('<Configure>', _sync_manual_canvas_width)

        def _manual_mousewheel(event):
            delta = getattr(event, 'delta', 0)
            if delta:
                self._manual_scroll_canvas.yview_scroll(int(-delta / 120), 'units')

        self._manual_scroll_canvas.bind_all('<MouseWheel>', _manual_mousewheel)

        top = tk.Frame(f, bg=CLR_BG)
        top.pack(fill='both', expand=True, padx=8, pady=8)
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        current = tk.LabelFrame(top, text='Current State', bg=CLR_BG, padx=10, pady=10)
        target = tk.LabelFrame(top, text='Target State', bg=CLR_BG, padx=10, pady=10)
        current.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        target.grid(row=0, column=1, sticky='nsew', padx=(6, 0))

        current_fields = [
            ('Temperature (C)', self._manual_current['temp']),
            ('Motor X (mm)', self._manual_current['x']),
            ('Motor Y (mm)', self._manual_current['y']),
            ('Motor Z (mm)', self._manual_current['z']),
            ('Gas A setpoint RFD', self._manual_current['gas_a_sp']),
            ('Gas A output RFX', self._manual_current['gas_a']),
            ('Gas B setpoint RFD', self._manual_current['gas_b_sp']),
            ('Gas B output RFX', self._manual_current['gas_b']),
        ]
        for row, (label, var) in enumerate(current_fields):
            tk.Label(current, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=row, column=0, sticky='w', pady=4)
            tk.Label(current, textvariable=var, bg=CLR_LGRAY, anchor='w', width=14,
                     font=('Courier New', 10), relief='groove').grid(
                     row=row, column=1, sticky='ew', padx=(10, 0), pady=4)

        target_fields = [
            ('Temperature (C)', 'temp'),
            ('Ramp Rate (C/min)', 'temp_ramp'),
            ('Motor X (mm)', 'x'),
            ('Motor Y (mm)', 'y'),
            ('Motor Z (mm)', 'z'),
            ('Gas A setting (0-100)', 'gas_a'),
            ('Gas B setting (0-100)', 'gas_b'),
        ]
        for row, (label, key) in enumerate(target_fields):
            tk.Label(target, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=row, column=0, sticky='w', pady=4)
            tk.Entry(target, textvariable=self._manual_target[key], width=16,
                     font=('Courier New', 10)).grid(
                     row=row, column=1, sticky='ew', padx=(10, 0), pady=4)

        btns = tk.Frame(f, bg=CLR_BG)
        btns.pack(fill='x', padx=8, pady=(0, 8))
        ttk.Button(btns, text='Refresh Current State',
                   command=lambda: self._run_manual_action(self._manual_refresh_state)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Copy Current -> Target',
                   command=self._copy_current_to_target).pack(side='left', padx=4)
        ttk.Button(btns, text='Apply Temperature',
                   command=lambda: self._run_manual_action(self._manual_apply_temperature)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Move Tip',
                   command=lambda: self._run_manual_action(self._manual_move_stage)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Set Gas',
                   command=lambda: self._run_manual_action(self._manual_set_gas)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Apply All',
                   command=lambda: self._run_manual_action(self._manual_apply_all)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Stop Tip',
                   command=lambda: self._run_manual_action(self._manual_stop_stage)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Find Contact Z',
                   command=lambda: self._run_manual_action(self._manual_find_contact_z)
                   ).pack(side='left', padx=4)

        tk.Label(f, textvariable=self._manual_status_var, bg=CLR_LGRAY,
                 anchor='w', font=('Segoe UI', 10), relief='sunken').pack(
                 fill='x', padx=8, pady=(0, 8))
        tk.Label(f, textvariable=self._manual_ocv_var, bg=CLR_BG,
                 anchor='w', font=('Courier New', 10)).pack(
                 fill='x', padx=8, pady=(0, 6))

        quick_row = tk.Frame(f, bg=CLR_BG)
        quick_row.pack(fill='x', padx=8, pady=(0, 8))
        quick_row.columnconfigure(0, weight=1)
        quick_row.columnconfigure(1, weight=1)

        quick = tk.LabelFrame(quick_row, text='Quick EIS', bg=CLR_BG, padx=10, pady=10)
        quick.grid(row=0, column=0, sticky='nsew', padx=(0, 6))

        quick_fields = [
            ('Label', 'label'),
            ('Vdc (V)', 'v_dc'),
            ('Amplitude (mV)', 'amp_mv'),
            ('Points', 'n_pts'),
            ('PEIS HF (Hz)', 'peis_f_high'),
            ('PEIS LF (Hz)', 'peis_f_low'),
            ('Cycles', 'cycles'),
        ]
        for row, (label, key) in enumerate(quick_fields):
            col = (row % 2) * 2
            line = row // 2
            tk.Label(quick, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=line, column=col, sticky='w', pady=4, padx=(0, 6))
            tk.Entry(quick, textvariable=self._quick_eis[key], width=16,
                     font=('Courier New', 10)).grid(
                     row=line, column=col + 1, sticky='w', pady=4, padx=(0, 14))

        tk.Label(
            quick,
            text='Saved under Run / Monitor result folder -> Manual EIS.',
            bg=CLR_BG,
            anchor='w',
            font=('Segoe UI', 9),
            fg='#555',
        ).grid(row=4, column=0, columnspan=4, sticky='w', pady=(2, 6))
        ttk.Button(quick, text='Run Quick EIS',
                   command=lambda: self._run_manual_action(self._manual_run_quick_eis)
                   ).grid(row=5, column=0, sticky='w', pady=(4, 0))

        quick_rapid = tk.LabelFrame(quick_row, text='Quick Rapid EIS', bg=CLR_BG, padx=10, pady=10)
        quick_rapid.grid(row=0, column=1, sticky='nsew', padx=(6, 0))

        quick_rapid_fields = [
            ('Label', 'label'),
            ('Vdc (V)', 'v_dc'),
            ('dV (mV)', 'dv_mv'),
            ('PEIS points', 'n_pts'),
            ('Pre-hold (s)', 'hold_time'),
            ('Post-hold (s)', 'post_hold_time'),
            ('CA duration (s)', 'ca_duration'),
            ('PEIS HF (Hz)', 'peis_f_high'),
            ('PEIS LF (Hz)', 'peis_f_low'),
            ('Cycles', 'cycles'),
        ]
        for row, (label, key) in enumerate(quick_rapid_fields):
            col = (row % 2) * 2
            line = row // 2
            tk.Label(quick_rapid, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=line, column=col, sticky='w', pady=4, padx=(0, 6))
            tk.Entry(quick_rapid, textvariable=self._quick_rapid[key], width=16,
                     font=('Courier New', 10)).grid(
                     row=line, column=col + 1, sticky='w', pady=4, padx=(0, 14))

        tk.Label(
            quick_rapid,
            text='Saved under Run / Monitor result folder -> Manual Rapid EIS.',
            bg=CLR_BG,
            anchor='w',
            font=('Segoe UI', 9),
            fg='#555',
        ).grid(row=5, column=0, columnspan=4, sticky='w', pady=(2, 6))
        ttk.Button(quick_rapid, text='Run Quick Rapid EIS',
                   command=lambda: self._run_manual_action(self._manual_run_quick_rapid_eis)
                   ).grid(row=6, column=0, sticky='w', pady=(4, 0))

        manual_measure_btns = tk.Frame(f, bg=CLR_BG)
        manual_measure_btns.pack(fill='x', padx=8, pady=(0, 6))
        self._manual_stop_measurement_btn = ttk.Button(
            manual_measure_btns,
            text='Stop Measurement',
            command=self._manual_stop_measurement,
        )
        self._manual_stop_measurement_btn.pack(anchor='w')

        tk.Label(f, textvariable=self._manual_recommendation_var, bg=CLR_BG,
                 anchor='w', font=('Segoe UI', 10), fg='#37474f').pack(
                 fill='x', padx=8, pady=(0, 6))

        contact = tk.LabelFrame(f, text='Z Contact Search', bg=CLR_BG, padx=10, pady=10)
        contact.pack(fill='x', padx=8, pady=(0, 8))

        contact_fields = [
            ('Start offset (mm)', 'start_offset'),
            ('Step (mm)', 'step_mm'),
            ('Max drop (mm)', 'max_drop_mm'),
            ('Max beyond seed (mm)', 'max_beyond_seed_mm'),
            ('OCV threshold (V)', 'ocv_threshold'),
            ('Settle per step (s)', 'settle_s'),
            ('Engage extra (mm)', 'engage_mm'),
        ]
        for row, (label, key) in enumerate(contact_fields):
            col = (row % 3) * 2
            line = row // 3
            tk.Label(contact, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=line, column=col, sticky='w', pady=4, padx=(0, 6))
            tk.Entry(contact, textvariable=self._contact_search[key], width=12,
                     font=('Courier New', 10)).grid(
                     row=line, column=col + 1, sticky='w', pady=4, padx=(0, 14))

    # ══════════════════════════════════════════════════════════════════════
    # Hardware connect/disconnect
    # ══════════════════════════════════════════════════════════════════════
    def _toggle_connect(self, key):
        obj = {'biologic': self.bl, 'motor': self.motor,
               'temp': self.tc, 'mfc': self.mfc}[key]
        if obj is not None:
            self._do_disconnect(key)
        else:
            self._do_connect(key)

    def _refresh_port_choices(self):
        ports = sorted(p.device for p in serial.tools.list_ports.comports())
        self._available_ports = ports
        motor_bridge_only = _detect_md_cc4xx_bridge() and COM_PORTS['motor'] not in ports
        for key in ['motor', 'temp', 'mfc']:
            box = getattr(self, '_hw_port_box', {}).get(key)
            current = self._serial_port_var[key].get()
            values = list(ports)
            if current and current not in values:
                values.insert(0, current)
            if box is not None:
                box['values'] = values if values else ['']
            if not current and ports:
                self._serial_port_var[key].set(ports[0])
        if hasattr(self, '_port_status_var'):
            if motor_bridge_only:
                self._port_status_var.set(
                    "Serial ports: "
                    f"{', '.join(ports) if ports else 'none'}"
                    " | Motor bridge detected as MD-CC4xx but no motor COM port exists yet"
                )
            else:
                self._port_status_var.set(
                    f"Serial ports: {', '.join(ports)}" if ports else 'Serial ports: none detected'
                )

    def _autodetect_serial_devices(self):
        self._refresh_port_choices()
        ports = list(self._available_ports)
        if not ports:
            messagebox.showwarning("Auto detect", "No COM ports are available.")
            return
        self._port_status_var.set('Serial autodetect running...')

        def worker():
            detected = {}
            for port in ports:
                if 'motor' not in detected and self._probe_motor_port(port):
                    detected['motor'] = port
                if 'mfc' not in detected and self._probe_mfc_port(port):
                    detected['mfc'] = port
                if 'temp' not in detected and self._probe_temp_port(port):
                    detected['temp'] = port
            self.after(0, lambda: self._finish_serial_autodetect(detected))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_serial_autodetect(self, detected):
        if detected:
            for key, port in detected.items():
                self._serial_port_var[key].set(port)
            found = ', '.join(f"{key}={port}" for key, port in detected.items())
            self._port_status_var.set(f'Autodetect found: {found}')
            self._log(f"[Auto-detect] Serial devices found: {found}")
        else:
            if _detect_md_cc4xx_bridge():
                msg = (
                    'Autodetect did not find a motor COM port. '
                    'MD-CC4xx is present, so the motor cable driver likely needs to be installed.'
                )
            else:
                msg = 'Autodetect did not find matching devices'
            self._port_status_var.set(msg)
            self._log(f"[Auto-detect] {msg}")

    def _probe_motor_port(self, port):
        cls = MODS.get('motor')
        if cls is None:
            return False
        motor = None
        try:
            motor = cls(port=port, timeout=0.25)
            motor.connect()
            pos = motor.get_position('X')
            return not np.isnan(pos)
        except Exception:
            return False
        finally:
            if motor is not None:
                try:
                    motor.disconnect()
                except Exception:
                    pass

    def _probe_mfc_port(self, port):
        cls = MODS.get('mfc')
        if cls is None:
            return False
        mfc = None
        try:
            mfc = cls(port=port, timeout=0.4)
            mfc.connect()
            value = mfc.get_setpoint('A')
            return not np.isnan(value)
        except Exception:
            return False
        finally:
            if mfc is not None:
                try:
                    mfc.disconnect()
                except Exception:
                    pass

    def _probe_temp_port(self, port):
        cls = MODS.get('temp')
        if cls is None:
            return False
        tc = None
        try:
            tc = cls(port=port)
            tc.connect()
            value = tc.get_temperature()
            return np.isfinite(value)
        except Exception:
            return False
        finally:
            if tc is not None:
                try:
                    tc.disconnect()
                except Exception:
                    pass

    def _candidate_biologic_scan_prefixes(self):
        prefixes = []

        def add_ip(ip):
            parts = str(ip).strip().split('.')
            if len(parts) != 4:
                return
            if parts[0] in {'0', '127'}:
                return
            prefix = '.'.join(parts[:3]) + '.'
            if prefix not in prefixes:
                prefixes.append(prefix)

        add_ip(self._biologic_ip.get())
        try:
            cmd = (
                "Get-NetIPConfiguration | ForEach-Object { "
                "$hasGateway = [bool]$_.IPv4DefaultGateway; "
                "foreach ($addr in $_.IPv4Address) { "
                "if (-not $hasGateway) { $addr.IPAddress } "
                "} }"
            )
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', cmd],
                capture_output=True,
                text=True,
                timeout=3,
            )
            for line in result.stdout.splitlines():
                add_ip(line)
        except Exception:
            pass
        add_ip(BIOLOGIC_IP)
        return prefixes or ['192.168.1.']

    def _autodetect_biologic(self):
        """Scan local /24 candidates for a BioLogic device listening on port 5000."""
        if str(BIOLOGIC_BACKEND).strip().lower() == 'olecom':
            current_ip = self._biologic_ip.get().strip() or BIOLOGIC_IP
            self._biologic_ip.set(current_ip)
            self._hw_status['biologic'].config(text='● OLE-COM uses EC-Lab session', fg=CLR_ORANGE)
            self._log(
                "[Auto-detect] OLE-COM mode does not use EC-Lib socket discovery; "
                f"keeping IP={current_ip}. Open EC-Lab and use Connect."
            )
            return
        prefixes = self._candidate_biologic_scan_prefixes()

        self._hw_status['biologic'].config(text='● Scanning...', fg=CLR_ORANGE)
        self.update_idletasks()

        def probe(ip):
            try:
                with socket.create_connection((ip, 5000), timeout=0.5):
                    return ip
            except OSError:
                return None

        def scan():
            cls = MODS.get('biologic')
            if cls is not None and hasattr(cls, 'discover_devices'):
                try:
                    devices = cls.discover_devices('eth')
                except Exception as exc:
                    devices = []
                    self._log(f"[Auto-detect] EC-Lib discovery warning: {exc}")
                if devices:
                    device_text = ', '.join(
                        f"{d.get('kind', 'BioLogic')} {d.get('address')} SN={d.get('serial', '')}"
                        for d in devices
                    )
                    self._log(f"[Auto-detect] EC-Lib found: {device_text}")
                    found_ip = devices[0].get('address')
                    self.after(0, lambda ip=found_ip: self._autodetect_done(ip))
                    return

            pending = queue.Queue()
            for prefix in prefixes:
                for last in range(1, 255):
                    pending.put(f"{prefix}{last}")
            result = {'ip': None}
            result_lock = threading.Lock()

            def worker():
                while True:
                    with result_lock:
                        if result['ip'] is not None:
                            return
                    try:
                        ip = pending.get_nowait()
                    except queue.Empty:
                        return
                    found = probe(ip)
                    if found:
                        with result_lock:
                            if result['ip'] is None:
                                result['ip'] = found
                    pending.task_done()

            threads = []
            for _ in range(min(96, pending.qsize())):
                t = threading.Thread(target=worker, daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=8.0)
            found = result['ip']
            self.after(0, lambda: self._autodetect_done(found))

        self._log(f"[Auto-detect] BioLogic scan prefixes: {', '.join(prefixes)}")
        threading.Thread(target=scan, daemon=True).start()

    def _autodetect_done(self, ip):
        if ip:
            self._biologic_ip.set(ip)
            self._hw_status['biologic'].config(text=f'● Found: {ip}', fg=CLR_BLUE)
            self._log(f"[Auto-detect] BioLogic found at {ip}")
        else:
            self._hw_status['biologic'].config(text='● Not connected', fg=CLR_RED)
            messagebox.showwarning("Auto-detect", "서브넷에서 BioLogic 장비를 찾지 못했습니다.")

    def _selected_biologic_channel(self):
        try:
            channel = int(float(self._biologic_channel_var.get()))
        except Exception:
            channel = 1
        channel = max(1, channel)
        if self._biologic_channel_var.get() != str(channel):
            self._biologic_channel_var.set(str(channel))
        self._active_biologic_channel = channel
        return channel

    def _set_biologic_channel_choices(self, channels=None, *, prompt_if_multiple=False):
        channel_infos = channels or [{'channel': 1, 'label': 'CH 1', 'plugged': True, 'info': {}}]
        values = []
        for entry in channel_infos:
            try:
                channel = int(entry.get('channel', 0))
            except Exception:
                continue
            if channel > 0 and str(channel) not in values:
                values.append(str(channel))
        if not values:
            values = ['1']
            channel_infos = [{'channel': 1, 'label': 'CH 1', 'plugged': True, 'info': {}}]

        self._biologic_channel_info = channel_infos
        current = str(self._selected_biologic_channel())
        if current not in values:
            current = '1' if '1' in values else values[0]
        self._biologic_channel_var.set(current)

        box = getattr(self, '_biologic_channel_box', None)
        if box is not None:
            box['values'] = values
            box.config(state='readonly')

        if prompt_if_multiple and len(values) > 1:
            selected = simpledialog.askinteger(
                "BioLogic channel",
                "BioLogic has multiple available channels.\n"
                f"Detected channels: {', '.join(values)}\n"
                "Which channel should this GUI use?",
                parent=self,
                initialvalue=int(current),
                minvalue=min(int(v) for v in values),
                maxvalue=max(int(v) for v in values),
            )
            if selected is not None and str(selected) in values:
                self._biologic_channel_var.set(str(selected))
            elif selected is not None:
                messagebox.showwarning(
                    "BioLogic channel",
                    f"Channel {selected} is not reported as available; keeping CH {current}.",
                )

        return self._selected_biologic_channel()

    def _do_connect(self, key):
        cls_map = {'biologic': 'biologic', 'motor': 'motor',
                   'temp': 'temp', 'mfc': 'mfc'}
        cls = MODS.get(cls_map[key])
        if cls is None:
            messagebox.showerror("Import error", f"Module for '{key}' failed to import.")
            return
        existing = {'biologic': self.bl, 'motor': self.motor,
                    'temp': self.tc, 'mfc': self.mfc}[key]
        if existing is not None:
            self._log(f"[HW] {key} already has a live object; closing it before reconnect")
            self._do_disconnect(key)
            time.sleep(0.3)
        obj = None
        try:
            if key == 'motor':
                selected_port = self._serial_port_var[key].get()
                if selected_port not in self._available_ports and _detect_md_cc4xx_bridge():
                    raise RuntimeError(
                        "Motor cable is present as MD-CC4xx, but Windows has not created a motor COM port yet. "
                        "Install the MD-CC4xx / CP210x driver first."
                    )
            if key == 'biologic':
                obj = cls(ip=self._biologic_ip.get())
            elif key in self._serial_port_var:
                obj = cls(port=self._serial_port_var[key].get())
            else:
                obj = cls()
            obj.connect()
            channel_note = ''
            if key == 'biologic':
                self.bl = obj
                try:
                    channels = obj.list_channels() if hasattr(obj, 'list_channels') else None
                except Exception as exc:
                    channels = None
                    self._log(f"[HW] biologic channel detection warning: {exc}")
                selected_channel = self._set_biologic_channel_choices(
                    channels,
                    prompt_if_multiple=True,
                )
                channel_note = f" CH {selected_channel}"
            elif key == 'motor':    self.motor  = obj
            elif key == 'temp':     self.tc     = obj
            elif key == 'mfc':      self.mfc    = obj
            self._hw_status[key].config(text=f'● Connected{channel_note}', fg=CLR_GREEN)
            self._hw_btn[key].config(text='Disconnect')
            port_note = f" on {self._serial_port_var[key].get()}" if key in self._serial_port_var else ''
            self._log(f"[HW] {key} connected{port_note}{channel_note}")
        except Exception as e:
            if obj is not None:
                try:
                    obj.disconnect()
                except Exception:
                    pass
            messagebox.showerror("Connection error", str(e))
            self._log(f"[HW] {key} connect failed: {e}")

    def _do_disconnect(self, key):
        obj = {'biologic': self.bl, 'motor': self.motor,
               'temp': self.tc, 'mfc': self.mfc}[key]
        if obj is None:
            return
        try:
            obj.disconnect()
        except Exception:
            pass
        if   key == 'biologic':
            self.bl = None
            self._set_biologic_channel_choices()
        elif key == 'motor':    self.motor  = None
        elif key == 'temp':     self.tc     = None
        elif key == 'mfc':      self.mfc    = None
        self._hw_status[key].config(text='● Not connected', fg=CLR_RED)
        self._hw_btn[key].config(text='Connect')
        self._log(f"[HW] {key} disconnected")

    def _connect_all(self):
        for key in ['biologic', 'motor', 'temp', 'mfc']:
            obj = {'biologic': self.bl, 'motor': self.motor,
                   'temp': self.tc, 'mfc': self.mfc}[key]
            if obj is not None:
                self._log(f"[HW] {key} already connected - skipping Connect All open")
                continue
            self._do_connect(key)

    def _disconnect_all(self):
        for key in ['biologic', 'motor', 'temp', 'mfc']:
            obj = {'biologic': self.bl, 'motor': self.motor,
                   'temp': self.tc, 'mfc': self.mfc}[key]
            if obj is not None:
                self._do_disconnect(key)

    def _do_readback(self):
        try:
            if self.tc:
                self._rb_temp.set(f"{self.tc.get_temperature():.1f}")
        except Exception as e:
            self._rb_temp.set(f"ERR: {e}")
        try:
            if self.mfc:
                self._rb_fa.set(f"{self.mfc.get_flow('A'):.2f}")
                self._rb_fb.set(f"{self.mfc.get_flow('B'):.2f}")
        except Exception as e:
            self._rb_fa.set("ERR"); self._rb_fb.set("ERR")
        for ax, var in [('X', self._rb_x), ('Y', self._rb_y), ('Z', self._rb_z)]:
            try:
                if self.motor:
                    var.set(f"{self.motor.get_position(ax):.3f}")
                else:
                    var.set('?')
            except Exception:
                var.set('ERR')

    def _update_manual_current_state(self):
        if self.tc:
            try:
                self._manual_current['temp'].set(f"{self.tc.get_temperature():.1f}")
            except Exception:
                self._manual_current['temp'].set('ERR')
        else:
            self._manual_current['temp'].set('-')

        if self.mfc:
            try:
                self._manual_current['gas_a_sp'].set(f"{self.mfc.get_setpoint('A'):.2f}")
                self._manual_current['gas_a'].set(f"{self.mfc.get_flow('A'):.2f}")
                self._manual_current['gas_b_sp'].set(f"{self.mfc.get_setpoint('B'):.2f}")
                self._manual_current['gas_b'].set(f"{self.mfc.get_flow('B'):.2f}")
            except Exception:
                self._manual_current['gas_a_sp'].set('ERR')
                self._manual_current['gas_a'].set('ERR')
                self._manual_current['gas_b_sp'].set('ERR')
                self._manual_current['gas_b'].set('ERR')
        else:
            self._manual_current['gas_a_sp'].set('-')
            self._manual_current['gas_a'].set('-')
            self._manual_current['gas_b_sp'].set('-')
            self._manual_current['gas_b'].set('-')

        for axis in ['X', 'Y', 'Z']:
            key = axis.lower()
            if self.motor:
                try:
                    self._manual_current[key].set(f"{self.motor.get_position(axis):.3f}")
                except Exception:
                    self._manual_current[key].set('ERR')
            else:
                self._manual_current[key].set('-')

    def _run_manual_action(self, action):
        if self.running:
            messagebox.showwarning("Manual control", "Automatic run is active. Stop it before manual control.")
            return
        self._active_biologic_channel = self._selected_biologic_channel()
        def _worker():
            try:
                action()
            except Exception as exc:
                self._manual_status_var.set(f'Manual command failed: {exc}')
                self._log(f"[Manual] command failed: {exc}")
        threading.Thread(target=_worker, daemon=True).start()

    def _copy_current_to_target(self):
        self._update_manual_current_state()
        mapping = [
            ('temp', 'temp'),
            ('x', 'x'),
            ('y', 'y'),
            ('z', 'z'),
            ('gas_a_sp', 'gas_a'),
            ('gas_b_sp', 'gas_b'),
        ]
        for src, dst in mapping:
            value = self._manual_current[src].get()
            if value not in ('-', 'ERR', '?'):
                self._manual_target[dst].set(value)
        self._manual_status_var.set('Copied current state into target settings')

    def _manual_refresh_state(self):
        self._manual_status_var.set('Refreshing current state...')
        self._do_readback()
        self._update_manual_current_state()
        ocv = self._manual_update_ocv()
        if self.bl is not None and ocv is None:
            self._manual_status_var.set('Current state updated; BioLogic OCV unavailable')
        else:
            self._manual_status_var.set('Current state updated')

    def _manual_apply_temperature(self):
        if self.tc is None:
            self._manual_status_var.set('Temperature controller is not connected')
            return
        target = float(self._manual_target['temp'].get())
        ramp_rate = float(self._manual_target['temp_ramp'].get())
        self._manual_status_var.set(
            f'Setting temperature to {target:.1f} C at {ramp_rate:.2f} C/min'
        )
        self.tc.set_ramp_rate(ramp_rate)
        self.tc.set_temperature(target)
        self._manual_refresh_state()
        self._manual_status_var.set(
            f'Temperature setpoint sent: {target:.1f} C at {ramp_rate:.2f} C/min'
        )

    def _manual_move_stage(self):
        if self.motor is None:
            self._manual_status_var.set('Motor controller is not connected')
            return
        self._manual_status_var.set('Moving tip to target position')
        for axis in ['X', 'Y', 'Z']:
            target = float(self._manual_target[axis.lower()].get())
            self.motor.move_abs_wait(axis, target)
        self._manual_refresh_state()
        self._manual_status_var.set('Tip move complete')

    def _manual_set_gas(self):
        if self.mfc is None:
            self._manual_status_var.set('MFC is not connected')
            return
        with self._manual_gas_seq_lock:
            self._manual_gas_seq += 1
            request_seq = self._manual_gas_seq
        gas_a = float(self._manual_target['gas_a'].get())
        gas_b = float(self._manual_target['gas_b'].get())
        print(
            f"[Manual gas] request #{request_seq}: parsed targets A={gas_a:.2f}, B={gas_b:.2f}",
            flush=True,
        )
        self._manual_status_var.set(
            f'Sending gas request #{request_seq}: A={gas_a:.2f}, B={gas_b:.2f} raw DMFC setting'
        )
        results = []
        errors = []
        for channel, target in (('A', gas_a), ('B', gas_b)):
            try:
                with self._manual_gas_seq_lock:
                    if request_seq != self._manual_gas_seq:
                        print(
                            f"[Manual gas] request #{request_seq}: stale before Ch{channel}; skipped",
                            flush=True,
                        )
                        self._manual_status_var.set(
                            f'Gas request #{request_seq} skipped because a newer request exists'
                        )
                        return
                print(
                    f"[Manual gas] request #{request_seq}: sending Ch{channel} target={target:.2f}",
                    flush=True,
                )
                setter = getattr(self.mfc, 'set_setting_fast', self.mfc.set_setting)
                setter(channel, target)
                results.append(f'{channel}=sent({target:.2f})')
            except Exception as exc:
                errors.append(f'{channel}: {exc}')
                self._log(f"[Manual gas] Ch{channel} set failed: {exc}")
        with self._manual_gas_seq_lock:
            is_latest = request_seq == self._manual_gas_seq
        if not is_latest:
            print(f"[Manual gas] request #{request_seq}: stale after send; not refreshing UI", flush=True)
            return
        self._manual_refresh_state()
        if errors:
            ok_text = ', '.join(results) if results else 'none'
            self._manual_status_var.set(
                f"Gas request #{request_seq} partial/fail. OK: {ok_text}. Errors: {' | '.join(errors)}"
            )
        else:
            self._manual_status_var.set(
                f'Gas request #{request_seq} sent; compare RFD setpoint vs RFX output'
            )

    def _manual_apply_all(self):
        self._manual_status_var.set('Applying all manual targets...')
        self._manual_apply_temperature()
        self._manual_set_gas()
        self._manual_move_stage()
        self._manual_status_var.set('All manual targets applied')

    def _manual_stop_stage(self):
        if self.motor is None:
            self._manual_status_var.set('Motor controller is not connected')
            return
        for axis in ['X', 'Y', 'Z']:
            try:
                self.motor.stop(axis)
            except Exception:
                pass
        self._manual_refresh_state()
        self._manual_status_var.set('Tip stop command sent')

    def _manual_stop_measurement(self):
        if not self._manual_measurement_running:
            self._manual_status_var.set('No manual measurement is running')
            return
        if self._manual_measurement_stop_event is not None:
            self._manual_measurement_stop_event.set()
        if self.bl is not None and hasattr(self.bl, 'stop_measurement'):
            try:
                self.bl.stop_measurement(channel=self._active_biologic_channel)
            except Exception as exc:
                self._log(f"[Manual measurement] stop warning: {exc}")
        self._manual_status_var.set('Stop requested for manual measurement')

    def _manual_set_recommendation(self, text):
        self._manual_recommendation_var.set(text)
        if hasattr(self, '_monitor_recommendation_var'):
            self._monitor_recommendation_var.set(text)

    def _set_monitor_recommendation(self, text):
        if hasattr(self, '_monitor_recommendation_var'):
            self._monitor_recommendation_var.set(text)

    def _set_manual_visual_overlay(self, analysis, visuals=None):
        visuals = visuals or {}
        overlay_points = list(visuals.get('recovered_fft_nyquist_points') or [])
        if analysis is None or analysis.peis_only_sufficient or not overlay_points:
            self._monitor_eis_overlay_points = []
            return
        self._monitor_eis_overlay_points = overlay_points

    def _format_manual_numeric(self, value, decimals=3):
        if value is None:
            return None
        try:
            numeric = float(value)
        except Exception:
            return None
        if not np.isfinite(numeric):
            return None
        return f"{numeric:.{decimals}f}"

    def _cap_manual_recommendation_lf(self, value):
        if value is None:
            return None
        try:
            numeric = float(value)
        except Exception:
            return value
        if not np.isfinite(numeric):
            return numeric
        return min(numeric, MANUAL_RECOMMENDATION_MAX_LF_HZ)

    def _apply_manual_recommendation_to_controls(self, analysis, measured_settings=None):
        measured_settings = measured_settings or {}
        normal_lf = self._cap_manual_recommendation_lf(
            analysis.recommended_normal_peis_lowest_freq_hz
            or analysis.recommended_peis_lowest_freq_hz
        )
        general_lf = self._cap_manual_recommendation_lf(
            analysis.recommended_peis_lowest_freq_hz
            or analysis.recommended_normal_peis_lowest_freq_hz
        )
        recommended_cp = (
            analysis.recommended_peis_conservative_cp_time_s
            or analysis.minimum_valid_cp_duration_s
            or analysis.saturation_recommended_cp_duration_s
        )

        if 'v_dc' in measured_settings:
            formatted = self._format_manual_numeric(measured_settings.get('v_dc'))
            if formatted is not None:
                self._quick_eis['v_dc'].set(formatted)
                self._quick_rapid['v_dc'].set(formatted)
        if 'amp_mv' in measured_settings:
            formatted = self._format_manual_numeric(measured_settings.get('amp_mv'), decimals=1)
            if formatted is not None:
                self._quick_eis['amp_mv'].set(formatted)
        if 'dv_mv' in measured_settings:
            formatted = self._format_manual_numeric(measured_settings.get('dv_mv'), decimals=1)
            if formatted is not None:
                self._quick_rapid['dv_mv'].set(formatted)
        if 'n_pts' in measured_settings:
            pts = str(int(float(measured_settings.get('n_pts'))))
            self._quick_eis['n_pts'].set(pts)
            self._quick_rapid['n_pts'].set(pts)
        if 'peis_f_high' in measured_settings:
            formatted = self._format_manual_numeric(measured_settings.get('peis_f_high'), decimals=0)
            if formatted is not None:
                self._quick_eis['peis_f_high'].set(formatted)
                self._quick_rapid['peis_f_high'].set(formatted)
        if 'hold_time' in measured_settings:
            formatted = self._format_manual_numeric(measured_settings.get('hold_time'))
            if formatted is not None:
                self._quick_rapid['hold_time'].set(formatted)
        if 'post_hold_time' in measured_settings:
            formatted = self._format_manual_numeric(measured_settings.get('post_hold_time'))
            if formatted is not None:
                self._quick_rapid['post_hold_time'].set(formatted)

        normal_lf_text = self._format_manual_numeric(normal_lf)
        if normal_lf_text is not None:
            self._quick_eis['peis_f_low'].set(normal_lf_text)

        general_lf_text = self._format_manual_numeric(general_lf)
        if general_lf_text is not None:
            self._quick_rapid['peis_f_low'].set(general_lf_text)

        recommended_cp_text = self._format_manual_numeric(recommended_cp)
        if recommended_cp_text is not None:
            self._quick_rapid['ca_duration'].set(recommended_cp_text)

    def _format_manual_recommendation_text(self, analysis, current_low_hz=MANUAL_QUICK_F_LOW_HZ):
        raw_normal_lf = (
            analysis.recommended_normal_peis_lowest_freq_hz
            or analysis.recommended_peis_lowest_freq_hz
        )
        raw_general_lf = (
            analysis.recommended_peis_lowest_freq_hz
        )
        normal_lf = self._cap_manual_recommendation_lf(raw_normal_lf)
        general_lf = self._cap_manual_recommendation_lf(raw_general_lf)

        if analysis.peis_only_sufficient:
            if normal_lf is not None and normal_lf > current_low_hz * 1.2:
                return f"Recommendation: normal EIS is sufficient; higher LF (~{normal_lf:.3g} Hz) should be enough."
            if normal_lf is not None:
                return f"Recommendation: normal EIS is sufficient (target LF ~{normal_lf:.3g} Hz)."
            return "Recommendation: normal EIS is sufficient."

        if (
            analysis.recommended_peis_conservative_cp_time_s is None
            and raw_general_lf is not None
            and current_low_hz
        ):
            ratio = float(raw_general_lf) / float(current_low_hz)
            if 0.95 <= ratio <= 1.25:
                return f"Recommendation: normal EIS is close; the current LF (~{current_low_hz:.3g} Hz) is already near the estimated target."
            if 0.75 <= ratio < 0.95:
                return (
                    f"Recommendation: normal EIS is close; try a slightly lower LF "
                    f"(~{general_lf:.3g} Hz) before switching to rapid EIS."
                )
            if ratio < 0.75:
                return f"Recommendation: try a lower-LF normal EIS (~{general_lf:.3g} Hz)."

        if general_lf is not None and general_lf < current_low_hz * 0.95:
            return f"Recommendation: rapid EIS or lower-LF normal EIS (~{general_lf:.3g} Hz)."
        return "Recommendation: rapid EIS."

    def _format_adaptive_policy_text(self, recommendation=None, finalize=None):
        if recommendation is not None and getattr(recommendation, 'current_run_policy_action', None):
            runtime_lf = (
                getattr(recommendation, 'current_run_policy_runtime_lf_hz', None)
                or getattr(recommendation, 'peis_lowest_freq_hz', None)
            )
            runtime_lf_text = (
                "n/a" if runtime_lf is None or not np.isfinite(float(runtime_lf))
                else f"{float(runtime_lf):.3g} Hz"
            )
            action = recommendation.current_run_policy_action
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
                "n/a" if runtime_lf is None or not np.isfinite(float(runtime_lf))
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

    def _analyze_manual_sequence_result(
        self,
        sequence_result,
        current_low_hz=MANUAL_QUICK_F_LOW_HZ,
        measured_settings=None,
    ):
        try:
            from analysis_adapter import analyze_measurement_files_with_visuals
        except Exception as exc:
            self._manual_set_recommendation(f'Recommendation: unavailable ({exc})')
            return

        peis_path = getattr(sequence_result, 'peis_path', None)
        dc_path = getattr(sequence_result, 'post_ca_path', None)
        if not peis_path:
            self._manual_set_recommendation('Recommendation: unavailable (no PEIS file)')
            return

        try:
            analysis, visuals = analyze_measurement_files_with_visuals(dc_path, peis_path)
        except Exception as exc:
            self._manual_set_recommendation(f'Recommendation: unavailable ({exc})')
            self._log(f"[Manual recommendation] analysis failed: {exc}")
            return

        text = self._format_manual_recommendation_text(analysis, current_low_hz=current_low_hz)
        self._manual_set_recommendation(text)
        self._apply_manual_recommendation_to_controls(analysis, measured_settings=measured_settings)
        self._set_manual_visual_overlay(analysis, visuals=visuals)
        self._redraw_monitor()
        self._log(f"[Manual recommendation] {text}")

    def _manual_quick_cycle_count(self, control_dict):
        try:
            cycles = int(float(control_dict.get('cycles').get()))
        except Exception:
            cycles = 1
        return max(1, cycles)

    def _write_manual_run_summary(self, run_dir, filename, payload):
        os.makedirs(run_dir, exist_ok=True)
        path = os.path.join(run_dir, filename)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(_json_safe(payload), fh, ensure_ascii=False, indent=2)
        return path

    def _manual_run_quick_eis(self):
        if self.bl is None:
            self._manual_status_var.set('BioLogic is not connected')
            return

        label = self._quick_eis['label'].get().strip() or 'manual_eis'
        cycles = self._manual_quick_cycle_count(self._quick_eis)
        channel = int(getattr(self, '_active_biologic_channel', 1))
        v_dc = float(self._quick_eis['v_dc'].get())
        amp_mv = float(self._quick_eis['amp_mv'].get())
        n_pts = int(float(self._quick_eis['n_pts'].get()))
        peis_f_high = float(self._quick_eis['peis_f_high'].get())
        peis_f_low = float(self._quick_eis['peis_f_low'].get())
        measured_settings = {
            'v_dc': v_dc,
            'amp_mv': amp_mv,
            'n_pts': n_pts,
            'peis_f_high': peis_f_high,
            'peis_f_low': peis_f_low,
            'channel': channel,
            'cycles': cycles,
        }
        stop_event = threading.Event()
        self._manual_measurement_running = True
        self._manual_measurement_stop_event = stop_event
        self._manual_set_recommendation('Recommendation: pending...')

        self._monitor_reset()
        self._queue_monitor_event('step', label=label, step='manual_peis',
                                  message=f'Quick EIS running ({cycles} cycle(s))')
        self._manual_status_var.set(f'Running Quick EIS: {label} ({cycles} cycle(s))')
        live_poll_stop = None if self._biologic_backend_is_olecom() else self._start_biologic_live_poll(label)

        try:
            result_root = self._result_dir.get() if hasattr(self, '_result_dir') else os.path.join(os.getcwd(), 'results')
            run_stamp = time.strftime('%Y%m%d_%H%M%S')
            run_dir = os.path.join(
                result_root,
                'Manual EIS',
                f"{_safe_path_part(label)}_quick_eis_{run_stamp}",
            )
            os.makedirs(run_dir, exist_ok=True)
            run_summary = {
                'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'mode': 'quick_eis',
                'label': label,
                'settings': measured_settings,
                'run_dir': run_dir,
                'cycles': [],
            }
            self._write_manual_run_summary(run_dir, 'manual_quick_eis_summary.json', run_summary)

            completed = 0
            for cycle_idx in range(1, cycles + 1):
                if stop_event.is_set():
                    break
                cycle_label = label if cycles == 1 else f"{label}_cycle{cycle_idx:03d}"
                cycle_dir = os.path.join(run_dir, f"cycle{cycle_idx:03d}")
                os.makedirs(cycle_dir, exist_ok=True)
                self._queue_monitor_event('row_start', label=cycle_label, row_index=cycle_idx, total=cycles)
                self._manual_status_var.set(
                    f'Running Quick EIS {cycle_idx}/{cycles}: {cycle_label}'
                )
                eis_data = self.bl.run_peis(
                    v_dc=v_dc,
                    f_high=peis_f_high,
                    f_low=peis_f_low,
                    n_pts=n_pts,
                    amplitude_mv=amp_mv,
                    channel=channel,
                    stop_event=stop_event,
                    on_segment=lambda data, _segment, _label=cycle_label: self._queue_monitor_event(
                        'eis_data', label=_label, step='manual_peis', data=data
                    ),
                )
                self._queue_monitor_event(
                    'eis_data', label=cycle_label, step='manual_peis_done', data=eis_data
                )
                ts = time.strftime('%Y%m%d_%H%M%S')
                eis_path = os.path.join(cycle_dir, f"{cycle_label}_PEIS_{ts}.txt")
                np.savetxt(eis_path, eis_data, header='freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm', comments='')
                sequence_result = type(
                    'ManualSequenceResult',
                    (),
                    {'peis_path': eis_path, 'post_ca_path': None, 'pre_ca_path': None},
                )()
                row = {
                    'Label': cycle_label,
                    'V_dc': v_dc,
                    'dV': amp_mv / 1000.0,
                    'PEIS_fHigh': peis_f_high,
                    'PEIS_fLow': peis_f_low,
                    'PEIS_nPts': n_pts,
                    'ManualMode': 'quick_eis',
                    'Cycle': cycle_idx,
                    'Cycles': cycles,
                }
                self._postprocess_csv_measurement(
                    row=row,
                    row_index=cycle_idx,
                    measurement_mode='manual_quick_eis',
                    sequence_result=sequence_result,
                    row_output_dir=cycle_dir,
                )
                self._analyze_manual_sequence_result(
                    sequence_result,
                    current_low_hz=peis_f_low,
                    measured_settings=measured_settings,
                )
                completed += 1
                run_summary['cycles'].append({
                    'cycle': cycle_idx,
                    'label': cycle_label,
                    'status': 'completed',
                    'cycle_dir': cycle_dir,
                    'peis_path': eis_path,
                    'points': int(len(eis_data)),
                })
                self._write_manual_run_summary(run_dir, 'manual_quick_eis_summary.json', run_summary)
                self._queue_monitor_event('sequence_done', label=cycle_label)
                self._queue_monitor_event('row_done', label=cycle_label)
                self._log(
                    f"[Manual EIS] {cycle_label}: CH {channel}, Vdc={v_dc:.3f} V, "
                    f"amp={amp_mv:.1f} mV, range={peis_f_high:.4g}->{peis_f_low:.4g} Hz, "
                    f"{len(eis_data)} pts, saved={eis_path}"
                )

            run_summary['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            run_summary['completed_cycles'] = completed
            run_summary['stopped'] = bool(stop_event.is_set())
            summary_path = self._write_manual_run_summary(
                run_dir, 'manual_quick_eis_summary.json', run_summary
            )
            status = 'stopped' if stop_event.is_set() else 'complete'
            self._manual_status_var.set(
                f'Quick EIS {status}: {completed}/{cycles} cycle(s), summary={summary_path}'
            )
        except Exception as exc:
            self._queue_monitor_event('error', label=label, message='Quick EIS failed')
            self._manual_status_var.set(f'Quick EIS failed: {exc}')
            self._log(f"[Manual EIS] {label} failed: {exc}")
            self._manual_set_recommendation('Recommendation: unavailable (measurement failed)')
        finally:
            if live_poll_stop is not None:
                live_poll_stop.set()
            self._manual_measurement_running = False
            self._manual_measurement_stop_event = None

    def _manual_run_quick_rapid_eis(self):
        if self.bl is None:
            self._manual_status_var.set('BioLogic is not connected')
            return

        label = self._quick_rapid['label'].get().strip() or 'manual_rapid'
        cycles = self._manual_quick_cycle_count(self._quick_rapid)
        channel = int(getattr(self, '_active_biologic_channel', 1))
        v_dc = float(self._quick_rapid['v_dc'].get())
        dv_v = float(self._quick_rapid['dv_mv'].get()) / 1000.0
        hold_time = float(self._quick_rapid['hold_time'].get())
        post_hold_time = float(self._quick_rapid['post_hold_time'].get())
        ca_duration = float(self._quick_rapid['ca_duration'].get())
        n_pts = int(float(self._quick_rapid['n_pts'].get()))
        peis_f_high = float(self._quick_rapid['peis_f_high'].get())
        peis_f_low = float(self._quick_rapid['peis_f_low'].get())
        measured_settings = {
            'v_dc': v_dc,
            'dv_mv': dv_v * 1000.0,
            'hold_time': hold_time,
            'post_hold_time': post_hold_time,
            'ca_duration': ca_duration,
            'n_pts': n_pts,
            'peis_f_high': peis_f_high,
            'peis_f_low': peis_f_low,
            'channel': channel,
            'cycles': cycles,
        }
        stop_event = threading.Event()
        self._manual_measurement_running = True
        self._manual_measurement_stop_event = stop_event
        self._manual_set_recommendation('Recommendation: pending...')

        self._monitor_reset()
        self._queue_monitor_event('step', label=label, step='manual_rapid',
                                  message=f'Quick Rapid EIS running ({cycles} cycle(s))')
        self._manual_status_var.set(f'Running Quick Rapid EIS: {label} ({cycles} cycle(s))')
        live_poll_stop = None if self._biologic_backend_is_olecom() else self._start_biologic_live_poll(label)

        try:
            result_root = self._result_dir.get() if hasattr(self, '_result_dir') else os.path.join(os.getcwd(), 'results')
            run_stamp = time.strftime('%Y%m%d_%H%M%S')
            run_dir = os.path.join(
                result_root,
                'Manual Rapid EIS',
                f"{_safe_path_part(label)}_quick_rapid_{run_stamp}",
            )
            os.makedirs(run_dir, exist_ok=True)
            run_summary = {
                'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'mode': 'quick_rapid_eis',
                'label': label,
                'settings': measured_settings,
                'run_dir': run_dir,
                'cycles': [],
            }
            self._write_manual_run_summary(run_dir, 'manual_quick_rapid_summary.json', run_summary)

            completed = 0
            for cycle_idx in range(1, cycles + 1):
                if stop_event.is_set():
                    break
                cycle_label = label if cycles == 1 else f"{label}_cycle{cycle_idx:03d}"
                cycle_dir = os.path.join(run_dir, f"cycle{cycle_idx:03d}")
                os.makedirs(cycle_dir, exist_ok=True)
                self._queue_monitor_event('row_start', label=cycle_label, row_index=cycle_idx, total=cycles)
                self._manual_status_var.set(
                    f'Running Quick Rapid EIS {cycle_idx}/{cycles}: {cycle_label}'
                )
                row = {
                    'Label': cycle_label,
                    'V_dc': v_dc,
                    'dV': dv_v,
                    'HoldTime_s': hold_time,
                    'PostPEIS_HoldTime_s': post_hold_time,
                    'CA_duration_s': ca_duration,
                    'CA_dt': MANUAL_QUICK_CA_DT_S,
                    'PEIS_fHigh': peis_f_high,
                    'PEIS_fLow': peis_f_low,
                    'PEIS_nPts': n_pts,
                    'ManualMode': 'quick_rapid_eis',
                    'Cycle': cycle_idx,
                    'Cycles': cycles,
                }
                if self._biologic_backend_is_olecom():
                    sequence_result = self._run_olecom_hybrid_sequence(
                        row=row,
                        label=cycle_label,
                        save_dir=cycle_dir,
                        channel=channel,
                        stop_event=stop_event,
                        dynamic_lf=False,
                    )
                else:
                    from measurement_sequence import rapid_eis_sequence

                    sequence_result = rapid_eis_sequence(
                        biologic=self.bl,
                        v_dc=v_dc,
                        dv=dv_v,
                        hold_time=hold_time,
                        post_peis_hold_time=post_hold_time,
                        peis_f_high=peis_f_high,
                        peis_f_low=peis_f_low,
                        peis_npts=n_pts,
                        ca_duration=ca_duration,
                        ca_dt=MANUAL_QUICK_CA_DT_S,
                        channel=channel,
                        save_dir=cycle_dir,
                        label=cycle_label,
                        monitor_callback=self._queue_monitor_event,
                        stop_event=stop_event,
                    )
                self._postprocess_csv_measurement(
                    row=row,
                    row_index=cycle_idx,
                    measurement_mode='manual_quick_rapid_eis',
                    sequence_result=sequence_result,
                    row_output_dir=cycle_dir,
                )
                self._analyze_manual_sequence_result(
                    sequence_result,
                    current_low_hz=peis_f_low,
                    measured_settings=measured_settings,
                )
                completed += 1
                run_summary['cycles'].append({
                    'cycle': cycle_idx,
                    'label': cycle_label,
                    'status': 'completed',
                    'cycle_dir': cycle_dir,
                    'pre_ca_path': getattr(sequence_result, 'pre_ca_path', None),
                    'peis_path': getattr(sequence_result, 'peis_path', None),
                    'post_ca_path': getattr(sequence_result, 'post_ca_path', None),
                })
                self._write_manual_run_summary(run_dir, 'manual_quick_rapid_summary.json', run_summary)
                self._queue_monitor_event('row_done', label=cycle_label)
                self._log(
                    f"[Manual Rapid EIS] {cycle_label}: CH {channel}, Vdc={v_dc:.3f} V, "
                    f"dV={dv_v*1000:.1f} mV, range={peis_f_high:.4g}->{peis_f_low:.4g} Hz, "
                    f"hold={hold_time:.1f}s, post={post_hold_time:.1f}s, CA={ca_duration:.1f}s, "
                    f"saved={cycle_dir}"
                )

            run_summary['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            run_summary['completed_cycles'] = completed
            run_summary['stopped'] = bool(stop_event.is_set())
            summary_path = self._write_manual_run_summary(
                run_dir, 'manual_quick_rapid_summary.json', run_summary
            )
            status = 'stopped' if stop_event.is_set() else 'complete'
            self._manual_status_var.set(
                f'Quick Rapid EIS {status}: {completed}/{cycles} cycle(s), summary={summary_path}'
            )
        except Exception as exc:
            self._queue_monitor_event('error', label=label, message='Quick Rapid EIS failed')
            self._manual_status_var.set(f'Quick Rapid EIS failed: {exc}')
            self._log(f"[Manual Rapid EIS] {label} failed: {exc}")
            self._manual_set_recommendation('Recommendation: unavailable (measurement failed)')
        finally:
            if live_poll_stop is not None:
                live_poll_stop.set()
            self._manual_measurement_running = False
            self._manual_measurement_stop_event = None

    def _manual_update_ocv(self):
        if self.bl is None:
            self._manual_ocv_var.set(self._format_manual_ocv_text(None))
            return None
        try:
            ocv = self.bl.get_ocv(channel=self._active_biologic_channel)
            self._manual_ocv_var.set(self._format_manual_ocv_text(ocv))
            return ocv
        except Exception as exc:
            self._manual_ocv_var.set(self._format_manual_ocv_text(None))
            if self._biologic_backend_is_olecom():
                self._log(f"[OCV] OLE-COM read failed: {exc}")
            return None

    def _format_manual_ocv_text(self, ocv):
        if ocv is None:
            return 'OCV: -'
        try:
            return f'OCV: {float(ocv):+.4f} V'
        except Exception:
            return 'OCV: -'

    def _schedule_manual_ocv_poll(self):
        if self._manual_ocv_poll_shutdown:
            return
        if self.running or self._manual_measurement_running:
            self._manual_ocv_var.set('OCV: paused during measurement')
            self.after(MANUAL_OCV_POLL_INTERVAL_MS, self._schedule_manual_ocv_poll)
            return
        if self._biologic_backend_is_olecom():
            # OLE-COM MeasureStatus polling can interfere with LoadSettings on
            # Win7.  Keep OCV reads explicit: Refresh Current State / contact
            # search may read it, but the idle GUI does not poll in the
            # background.
            if self.bl is None:
                self._manual_ocv_var.set(self._format_manual_ocv_text(None))
            self.after(MANUAL_OCV_POLL_INTERVAL_MS, self._schedule_manual_ocv_poll)
            return
        if not self._manual_ocv_poll_inflight:
            self._manual_ocv_poll_inflight = True
            threading.Thread(
                target=self._manual_ocv_poll_worker,
                name='manual_ocv_poll',
                daemon=True,
            ).start()
        self.after(MANUAL_OCV_POLL_INTERVAL_MS, self._schedule_manual_ocv_poll)

    def _manual_ocv_poll_worker(self):
        ocv = None
        try:
            biologic = self.bl
            if biologic is not None and hasattr(biologic, 'get_ocv'):
                ocv = biologic.get_ocv(channel=self._active_biologic_channel)
        except Exception:
            ocv = None
        finally:
            try:
                self.after(0, lambda: self._manual_ocv_poll_complete(ocv))
            except Exception:
                self._manual_ocv_poll_inflight = False

    def _manual_ocv_poll_complete(self, ocv):
        self._manual_ocv_poll_inflight = False
        self._manual_ocv_var.set(self._format_manual_ocv_text(ocv))

    def _confirm_contact_candidate(
        self,
        *,
        ocv_threshold,
        status_prefix,
        stop_event=None,
        confirm_s=CONTACT_CONFIRM_DURATION_S,
        poll_s=CONTACT_CONFIRM_POLL_S,
    ):
        deadline = time.time() + float(confirm_s)
        samples = 0
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError('Contact search stopped')
            ocv = self._manual_update_ocv()
            samples += 1
            remaining = max(0.0, deadline - time.time())
            if ocv is None:
                self._manual_status_var.set(
                    f'{status_prefix}: candidate rejected during confirm; OCV unavailable'
                )
                return False
            if abs(float(ocv)) > float(ocv_threshold):
                self._manual_status_var.set(
                    f'{status_prefix}: candidate rejected during confirm; '
                    f'OCV={float(ocv):+.4f} V'
                )
                return False
            self._manual_status_var.set(
                f'{status_prefix}: candidate stable, OCV={float(ocv):+.4f} V, '
                f'{remaining:.0f}s remaining'
            )
            time.sleep(min(float(poll_s), remaining))
        return samples > 0

    def _execute_contact_z_search(
        self,
        *,
        base_z,
        start_offset,
        step_mm,
        max_drop_mm,
        ocv_threshold,
        settle_s,
        engage_mm,
        max_beyond_seed_mm=None,
        status_prefix='Contact search',
        stop_event=None,
    ):
        if self.motor is None:
            raise RuntimeError('Motor controller is not connected')
        if self.bl is None:
            raise RuntimeError('BioLogic is not connected')
        step_mm = abs(float(step_mm))
        if step_mm <= 0:
            raise RuntimeError('Contact step must be > 0 mm')

        positive_z_is_up = bool(STAGE_SAFE_MOVE.get('positive_z_is_up', True))
        approach_sign = -1.0 if positive_z_is_up else 1.0
        start_z = base_z - approach_sign * abs(start_offset)
        self._manual_status_var.set(f'{status_prefix}: starting from Z={start_z:.3f} mm')
        self.motor.move_abs_wait('Z', start_z)
        self._manual_target['z'].set(f'{start_z:.3f}')
        self._manual_refresh_state()

        max_steps = max(1, int(round(max_drop_mm / step_mm)))
        max_beyond_seed_mm = (
            None if max_beyond_seed_mm is None
            else abs(float(max_beyond_seed_mm))
        )
        found_contact = None

        for idx in range(max_steps + 1):
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError('Contact search stopped')
            z_here = start_z + approach_sign * idx * abs(step_mm)
            if max_beyond_seed_mm is not None:
                beyond_seed = approach_sign * (z_here - float(base_z))
                if beyond_seed > max_beyond_seed_mm:
                    raise RuntimeError(
                        f'Contact safety lock: Z would pass seed by {beyond_seed:.3f} mm '
                        f'(limit {max_beyond_seed_mm:.3f} mm)'
                    )
            self.motor.move_abs_wait('Z', z_here)
            time.sleep(settle_s)
            ocv = self._manual_update_ocv()
            self._manual_status_var.set(
                f'{status_prefix}: Z={z_here:.3f} mm, OCV={ocv:+.4f} V' if ocv is not None
                else f'{status_prefix}: Z={z_here:.3f} mm, OCV unavailable'
            )
            if ocv is not None and abs(ocv) <= ocv_threshold:
                self._manual_status_var.set(
                    f'{status_prefix}: contact candidate at Z={z_here:.3f} mm; '
                    f'confirming for {CONTACT_CONFIRM_DURATION_S:.0f}s'
                )
                if self._confirm_contact_candidate(
                    ocv_threshold=ocv_threshold,
                    status_prefix=status_prefix,
                    stop_event=stop_event,
                ):
                    found_contact = z_here
                    break

        if found_contact is None:
            raise RuntimeError('Contact search did not find a valid OCV threshold')

        measure_z = found_contact + approach_sign * abs(engage_mm)
        self.motor.move_abs_wait('Z', measure_z)
        self._manual_target['z'].set(f'{measure_z:.3f}')
        self._manual_refresh_state()
        self._manual_status_var.set(
            f'Contact found at Z={found_contact:.3f} mm, measurement Z set to {measure_z:.3f} mm'
        )
        return found_contact, measure_z

    def _manual_find_contact_z(self):
        try:
            self._execute_contact_z_search(
            base_z=float(self._manual_target['z'].get()),
            start_offset=float(self._contact_search['start_offset'].get()),
            step_mm=float(self._contact_search['step_mm'].get()),
            max_drop_mm=float(self._contact_search['max_drop_mm'].get()),
                max_beyond_seed_mm=float(self._contact_search['max_beyond_seed_mm'].get()),
                ocv_threshold=float(self._contact_search['ocv_threshold'].get()),
                settle_s=float(self._contact_search['settle_s'].get()),
                engage_mm=float(self._contact_search['engage_mm'].get()),
            )
        except Exception as exc:
            self._manual_status_var.set(str(exc))
            self._manual_refresh_state()

    # ══════════════════════════════════════════════════════════════════════
    # Conditions table
    # ══════════════════════════════════════════════════════════════════════
    def _read_csv_compat(self, path):
        last_exc = None
        for kwargs in (
            {'encoding': 'utf-8-sig'},
            {},
            {'encoding': 'latin1'},
        ):
            try:
                return pd.read_csv(path, **kwargs)
            except Exception as exc:
                last_exc = exc
        raise last_exc

    def _load_csv(self):
        path = filedialog.askopenfilename(filetypes=[('CSV', '*.csv'), ('All', '*.*')])
        if not path:
            return
        try:
            self.condition_df = self._read_csv_compat(path)
            self._refresh_tree()
            self._log(f"[CSV] Loaded: {path}  ({len(self.condition_df)} rows)")
        except Exception as e:
            messagebox.showerror("Load error", str(e))

    def _load_template(self):
        tpl = os.path.join(os.path.dirname(__file__), 'conditions_template.csv')
        if os.path.exists(tpl):
            self.condition_df = self._read_csv_compat(tpl)
            self._refresh_tree()
            self._log(f"[CSV] Template loaded ({len(self.condition_df)} rows)")
        else:
            messagebox.showwarning("Not found", "conditions_template.csv not found.")

    def _save_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        self._tree_to_df()
        self.condition_df.to_csv(path, index=False)
        self._log(f"[CSV] Saved: {path}")

    def _normalize_condition_df(self, df):
        """Promote legacy gas-flow columns into raw DMFC setting columns."""
        df = df.copy()
        for _, (new_col, old_col) in GAS_SETTING_COLUMNS.items():
            if new_col not in df.columns and old_col in df.columns:
                df[new_col] = df[old_col]
            elif new_col in df.columns and old_col in df.columns:
                new_values = []
                for new_value, old_value in zip(df[new_col], df[old_col]):
                    parsed_new = _parse_optional_float(new_value, default=None)
                    parsed_old = _parse_optional_float(old_value, default=None)
                    new_values.append(old_value if parsed_new is None and parsed_old is not None else new_value)
                df[new_col] = new_values
        for col in self._tree_cols:
            if col not in df.columns:
                df[col] = ''
        return df

    def _refresh_tree(self):
        self.condition_df = self._normalize_condition_df(self.condition_df)
        self.tree.delete(*self.tree.get_children())
        for _, row in self.condition_df.iterrows():
            vals = [str(row.get(c, '')) for c in self._tree_cols]
            self.tree.insert('', 'end', values=vals)

    def _tree_to_df(self):
        rows = []
        for iid in self.tree.get_children():
            vals = self.tree.item(iid, 'values')
            rows.append(dict(zip(self._tree_cols, vals)))
        self.condition_df = pd.DataFrame(rows)

    def _parse_number_list(self, text, cast=float):
        items = []
        for token in str(text).replace('\n', ',').split(','):
            token = token.strip()
            if not token:
                continue
            items.append(cast(token))
        return items

    def _parse_gas_pairs(self, text):
        pairs = []
        raw = str(text).replace('\n', ';')
        for token in raw.split(';'):
            token = token.strip()
            if not token:
                continue
            parts = [p.strip() for p in token.split(':')]
            if len(parts) != 2:
                raise ValueError(f"Invalid gas pair '{token}'. Use A:B format.")
            pairs.append((float(parts[0]), float(parts[1])))
        return pairs

    def _generate_full_auto_conditions(self, append=False):
        try:
            use_temp = self._full_auto_use['temperature'].get()
            use_gas = self._full_auto_use['gas'].get()
            use_tip = self._full_auto_use['tip'].get()
            temperatures = (
                self._parse_number_list(self._full_auto['temperatures'].get(), float)
                if use_temp else [None]
            )
            voltages = self._parse_number_list(
                self._full_auto['voltages'].get(), float
            )
            gas_pairs = self._parse_gas_pairs(self._full_auto['gas_pairs'].get()) if use_gas else [(None, None)]
            e_start = int(float(self._full_auto['electrode_start'].get())) if use_tip else 1
            e_end = int(float(self._full_auto['electrode_end'].get())) if use_tip else 1
            x1 = float(self._full_auto['x1'].get()) if use_tip else None
            y1 = float(self._full_auto['y1'].get()) if use_tip else None
            xn = float(self._full_auto['xn'].get()) if use_tip else None
            yn = float(self._full_auto['yn'].get()) if use_tip else None
            z1 = float(self._full_auto['z1'].get()) if use_tip else None
            zn = float(self._full_auto['zn'].get()) if use_tip else None
            auto_contact_z = 1 if (
                use_tip and _parse_boolish(self._full_auto['auto_contact_z'].get(), default=True)
            ) else 0
            contact_start_offset = float(self._full_auto['contact_start_offset'].get()) if use_tip else 0.200
            contact_step = float(self._full_auto['contact_step'].get()) if use_tip else 0.010
            contact_max_drop = float(self._full_auto['contact_max_drop'].get()) if use_tip else 0.400
            contact_max_beyond_seed = float(self._full_auto['contact_max_beyond_seed'].get()) if use_tip else 0.200
            contact_ocv_threshold = float(self._full_auto['contact_ocv_threshold'].get()) if use_tip else 0.100
            contact_settle = float(self._full_auto['contact_settle'].get()) if use_tip else 1.00
            contact_engage = float(self._full_auto['contact_engage'].get()) if use_tip else 0.050
            dv = float(self._full_auto['dv'].get())
            hold_time = float(self._full_auto['hold_time'].get())
            post_peis_hold_time = float(self._full_auto['post_peis_hold_time'].get())
            peis_f_high = float(self._full_auto['peis_f_high'].get())
            peis_f_low = float(self._full_auto['peis_f_low'].get())
            peis_n_pts = int(float(self._full_auto['peis_n_pts'].get()))
            ca_duration = float(self._full_auto['ca_duration'].get())
            ca_dt = float(self._full_auto['ca_dt'].get())
            temp_ramp_rate = float(self._full_auto['temp_ramp_rate'].get()) if use_temp else None
            stable_time = float(self._full_auto['stable_time'].get()) if use_temp else None
            gas_stable_time = float(self._full_auto['gas_stable_time'].get()) if use_gas else None
        except ValueError as exc:
            messagebox.showerror("Full-auto input error", str(exc))
            return

        if not voltages:
            messagebox.showwarning(
                "Full-auto input",
                "Voltage list must not be empty."
            )
            return
        if use_tip and e_end < e_start:
            messagebox.showwarning(
                "Electrode range",
                "Electrode end must be greater than or equal to electrode start."
            )
            return

        electrode_ids = list(range(e_start, e_end + 1))
        count = len(electrode_ids)
        rows = []
        for temp in temperatures:
            for gas_a, gas_b in gas_pairs:
                for electrode in electrode_ids:
                    if use_tip:
                        frac = 0.0 if count == 1 else (electrode - e_start) / (e_end - e_start)
                        x_pos = x1 + frac * (xn - x1)
                        y_pos = y1 + frac * (yn - y1)
                        z_pos = z1 + frac * (zn - z1)
                    else:
                        x_pos = None
                        y_pos = None
                        z_pos = None
                    for v_dc in voltages:
                        label = _condition_label(
                            temperature=temp,
                            gas_a=gas_a,
                            gas_b=gas_b,
                            electrode=electrode if use_tip else None,
                            x_mm=x_pos,
                            y_mm=y_pos,
                            z_mm=z_pos,
                            voltage=v_dc,
                        )
                        rows.append({
                            'Label': label,
                            'Temperature_C': temp if temp is not None else 'None',
                            'RampRate_C_per_min': temp_ramp_rate if temp_ramp_rate is not None else 'None',
                            'GasA_setting': gas_a if gas_a is not None else 'None',
                            'GasB_setting': gas_b if gas_b is not None else 'None',
                            'X_mm': round(x_pos, 6) if x_pos is not None else 'None',
                            'Y_mm': round(y_pos, 6) if y_pos is not None else 'None',
                            'Z_mm': round(z_pos, 6) if z_pos is not None else 'None',
                            'AutoContactZ': auto_contact_z,
                            'ContactStartOffset_mm': contact_start_offset,
                            'ContactStep_mm': contact_step,
                            'ContactMaxDrop_mm': contact_max_drop,
                            'ContactMaxBeyondSeed_mm': contact_max_beyond_seed,
                            'ContactOCVThreshold_V': contact_ocv_threshold,
                            'ContactSettle_s': contact_settle,
                            'ContactEngage_mm': contact_engage,
                            'V_dc': v_dc,
                            'dV': dv,
                            'HoldTime_s': hold_time,
                            'PostPEIS_HoldTime_s': post_peis_hold_time,
                            'PEIS_fHigh': peis_f_high,
                            'PEIS_fLow': peis_f_low,
                            'PEIS_nPts': peis_n_pts,
                            'CA_duration_s': ca_duration,
                            'CA_dt': ca_dt,
                            'StableTime_s': stable_time if stable_time is not None else 'None',
                            'GasStableTime_s': gas_stable_time if gas_stable_time is not None else 'None',
                            'Skip': 0,
                        })

        new_df = pd.DataFrame(rows, columns=self._tree_cols)
        if append and not self.condition_df.empty:
            self._tree_to_df()
            new_df = pd.concat([self.condition_df, new_df], ignore_index=True)

        self.condition_df = new_df
        self._refresh_tree()
        self._full_auto_summary.set(
            f'Generated {len(rows)} rows from '
            f'{len(temperatures)} temperatures × {len(gas_pairs)} gas pairs × '
            f'{len(electrode_ids)} electrodes × {len(voltages)} voltages'
        )
        self._log(f"[Full-auto] Generated {len(rows)} rows into Semi-auto table")

    def _generate_adaptive_full_auto_conditions(self, append=False):
        try:
            use_temp = self._adaptive_full_auto_use['temperature'].get()
            use_gas = self._adaptive_full_auto_use['gas'].get()
            use_tip = self._adaptive_full_auto_use['tip'].get()
            temperatures = (
                self._parse_number_list(self._adaptive_full_auto['temperatures'].get(), float)
                if use_temp else [None]
            )
            voltages = self._parse_number_list(
                self._adaptive_full_auto['voltages'].get(), float
            )
            gas_pairs = (
                self._parse_gas_pairs(self._adaptive_full_auto['gas_pairs'].get())
                if use_gas else [(None, None)]
            )
            e_start = int(float(self._adaptive_full_auto['electrode_start'].get())) if use_tip else 1
            e_end = int(float(self._adaptive_full_auto['electrode_end'].get())) if use_tip else 1
            x1 = float(self._adaptive_full_auto['x1'].get()) if use_tip else None
            y1 = float(self._adaptive_full_auto['y1'].get()) if use_tip else None
            xn = float(self._adaptive_full_auto['xn'].get()) if use_tip else None
            yn = float(self._adaptive_full_auto['yn'].get()) if use_tip else None
            z1 = float(self._adaptive_full_auto['z1'].get()) if use_tip else None
            zn = float(self._adaptive_full_auto['zn'].get()) if use_tip else None
            auto_contact_z = 1 if (
                use_tip and _parse_boolish(self._adaptive_full_auto['auto_contact_z'].get(), default=True)
            ) else 0
            contact_start_offset = float(self._adaptive_full_auto['contact_start_offset'].get()) if use_tip else 0.200
            contact_step = float(self._adaptive_full_auto['contact_step'].get()) if use_tip else 0.010
            contact_max_drop = float(self._adaptive_full_auto['contact_max_drop'].get()) if use_tip else 0.400
            contact_max_beyond_seed = float(self._adaptive_full_auto['contact_max_beyond_seed'].get()) if use_tip else 0.200
            contact_ocv_threshold = float(self._adaptive_full_auto['contact_ocv_threshold'].get()) if use_tip else 0.100
            contact_settle = float(self._adaptive_full_auto['contact_settle'].get()) if use_tip else 1.00
            contact_engage = float(self._adaptive_full_auto['contact_engage'].get()) if use_tip else 0.050
            dv = float(self._adaptive_full_auto['dv'].get())
            hold_time = float(self._adaptive_full_auto['hold_time'].get())
            post_peis_hold_time = float(self._adaptive_full_auto['post_peis_hold_time'].get())
            peis_f_high = float(self._adaptive_full_auto['peis_f_high'].get())
            peis_f_low = float(self._adaptive_full_auto['peis_f_low'].get())
            peis_n_pts = int(float(self._adaptive_full_auto['peis_n_pts'].get()))
            ca_duration = float(self._adaptive_full_auto['ca_duration'].get())
            ca_dt = float(self._adaptive_full_auto['ca_dt'].get())
            temp_ramp_rate = float(self._adaptive_full_auto['temp_ramp_rate'].get()) if use_temp else None
            stable_time = float(self._adaptive_full_auto['stable_time'].get()) if use_temp else None
            gas_stable_time = float(self._adaptive_full_auto['gas_stable_time'].get()) if use_gas else None
            normal_eis_floor_hz = float(self._adaptive_full_auto['normal_eis_floor_hz'].get())
        except ValueError as exc:
            messagebox.showerror("Adaptive full-auto input error", str(exc))
            return

        if not voltages:
            messagebox.showwarning(
                "Adaptive full-auto input",
                "Voltage list must not be empty."
            )
            return
        if use_tip and e_end < e_start:
            messagebox.showwarning(
                "Electrode range",
                "Electrode end must be greater than or equal to electrode start."
            )
            return

        electrode_ids = list(range(e_start, e_end + 1))
        count = len(electrode_ids)
        rows = []
        for temp in temperatures:
            for gas_a, gas_b in gas_pairs:
                for electrode in electrode_ids:
                    if use_tip:
                        frac = 0.0 if count == 1 else (electrode - e_start) / (e_end - e_start)
                        x_pos = x1 + frac * (xn - x1)
                        y_pos = y1 + frac * (yn - y1)
                        z_pos = z1 + frac * (zn - z1)
                    else:
                        x_pos = None
                        y_pos = None
                        z_pos = None
                    for v_dc in voltages:
                        label = _condition_label(
                            prefix='ADAPT',
                            temperature=temp,
                            gas_a=gas_a,
                            gas_b=gas_b,
                            electrode=electrode if use_tip else None,
                            x_mm=x_pos,
                            y_mm=y_pos,
                            z_mm=z_pos,
                            voltage=v_dc,
                        )
                        rows.append({
                            'Label': label,
                            'Temperature_C': temp if temp is not None else 'None',
                            'RampRate_C_per_min': temp_ramp_rate if temp_ramp_rate is not None else 'None',
                            'GasA_setting': gas_a if gas_a is not None else 'None',
                            'GasB_setting': gas_b if gas_b is not None else 'None',
                            'X_mm': round(x_pos, 6) if x_pos is not None else 'None',
                            'Y_mm': round(y_pos, 6) if y_pos is not None else 'None',
                            'Z_mm': round(z_pos, 6) if z_pos is not None else 'None',
                            'AutoContactZ': auto_contact_z,
                            'ContactStartOffset_mm': contact_start_offset,
                            'ContactStep_mm': contact_step,
                            'ContactMaxDrop_mm': contact_max_drop,
                            'ContactMaxBeyondSeed_mm': contact_max_beyond_seed,
                            'ContactOCVThreshold_V': contact_ocv_threshold,
                            'ContactSettle_s': contact_settle,
                            'ContactEngage_mm': contact_engage,
                            'V_dc': v_dc,
                            'dV': dv,
                            'HoldTime_s': hold_time,
                            'PostPEIS_HoldTime_s': post_peis_hold_time,
                            'PEIS_fHigh': peis_f_high,
                            'PEIS_fLow': peis_f_low,
                            'PEIS_nPts': peis_n_pts,
                            'CA_duration_s': ca_duration,
                            'CA_dt': ca_dt,
                            'StableTime_s': stable_time if stable_time is not None else 'None',
                            'GasStableTime_s': gas_stable_time if gas_stable_time is not None else 'None',
                            'Skip': 0,
                        })

        new_df = pd.DataFrame(rows, columns=self._tree_cols)
        if append and not self.condition_df.empty:
            self._tree_to_df()
            new_df = pd.concat([self.condition_df, new_df], ignore_index=True)

        self.condition_df = new_df
        self._refresh_tree()
        self._adaptive_full_auto_summary.set(
            f'Generated {len(rows)} adaptive rows '
            f'(normal EIS floor {normal_eis_floor_hz:g} Hz) from '
            f'{len(temperatures)} temperatures x {len(gas_pairs)} gas pairs x '
            f'{len(electrode_ids)} electrodes x {len(voltages)} voltages'
        )
        self._log(
            f"[Adaptive Full-auto] Generated {len(rows)} rows into Semi-auto table "
            f"(normal EIS floor {normal_eis_floor_hz:g} Hz; runtime will update later same-regime rows)"
        )

    def _update_full_auto_field_states(self):
        tip_fields = [
            'z1', 'zn', 'auto_contact_z',
            'contact_start_offset', 'contact_step', 'contact_max_drop',
            'contact_max_beyond_seed', 'contact_ocv_threshold',
            'contact_settle', 'contact_engage',
            'electrode_start', 'electrode_end', 'x1', 'y1', 'xn', 'yn',
        ]
        groups = {
            'temperature': ['temperatures', 'temp_ramp_rate', 'stable_time'],
            'gas': ['gas_pairs', 'gas_stable_time'],
            'tip': tip_fields,
        }
        for key, fields in groups.items():
            enabled = self._full_auto_use[key].get()
            state = 'normal' if enabled else 'disabled'
            for field in fields:
                entry = self._full_auto_entries.get(field)
                if entry is not None:
                    entry.config(state=state)

    def _update_adaptive_full_auto_field_states(self):
        tip_fields = [
            'z1', 'zn', 'auto_contact_z',
            'contact_start_offset', 'contact_step', 'contact_max_drop',
            'contact_max_beyond_seed', 'contact_ocv_threshold',
            'contact_settle', 'contact_engage',
            'electrode_start', 'electrode_end', 'x1', 'y1', 'xn', 'yn',
        ]
        groups = {
            'temperature': ['temperatures', 'temp_ramp_rate', 'stable_time'],
            'gas': ['gas_pairs', 'gas_stable_time'],
            'tip': tip_fields,
        }
        for key, fields in groups.items():
            enabled = self._adaptive_full_auto_use[key].get()
            state = 'normal' if enabled else 'disabled'
            for field in fields:
                entry = self._adaptive_full_auto_entries.get(field)
                if entry is not None:
                    entry.config(state=state)

    def _row_uses_adaptive_runtime(self, row):
        label = str(row.get('Label', '')).strip()
        return label.upper().startswith('ADAPT')

    def _extract_electrode_id(self, label):
        match = re.search(r'(?:^|_)E(\d+)(?:_|$)', str(label))
        if not match:
            return None
        return int(match.group(1))

    def _adaptive_regime_key(self, row):
        label = str(row.get('Label', ''))
        return (
            self._extract_electrode_id(label),
            _parse_optional_float(row.get('Temperature_C'), default=None),
            _row_gas_setting(row, 'A', default=None),
            _row_gas_setting(row, 'B', default=None),
        )

    @staticmethod
    def _rounded_key(value, digits=4):
        try:
            if value is None:
                return None
            value = float(value)
            if not np.isfinite(value):
                return None
            return round(value, int(digits))
        except Exception:
            return None

    def _adaptive_site_key(self, row):
        electrode = self._extract_electrode_id(str(row.get('Label', '')))
        if electrode is not None:
            return ('E', int(electrode))
        pos_key = self._contact_position_key(row)
        if pos_key is not None:
            return ('XY', pos_key[0], pos_key[1])
        label = str(row.get('Label', '')).strip()
        return ('LABEL', label or 'unknown')

    def _adaptive_temp_key(self, row):
        return self._rounded_key(_parse_optional_float(row.get('Temperature_C'), default=None), digits=3)

    def _adaptive_gas_key(self, row):
        return (
            self._rounded_key(_row_gas_setting(row, 'A', default=None), digits=3),
            self._rounded_key(_row_gas_setting(row, 'B', default=None), digits=3),
        )

    def _adaptive_voltage_key(self, row):
        return self._rounded_key(_parse_optional_float(row.get('V_dc'), default=None), digits=4)

    def _olecom_new_pre_learning_state(self):
        return {
            'observations': [],
            'last_by_site_regime': {},
            'first_by_site_regime': {},
            'first_by_temp_gas': {},
            'first_by_site_temp': {},
            'first_by_site_gas': {},
            'latest_first': None,
        }

    def _olecom_pre_learning_keys(self, row):
        site = self._adaptive_site_key(row)
        temp = self._adaptive_temp_key(row)
        gas = self._adaptive_gas_key(row)
        voltage = self._adaptive_voltage_key(row)
        return {
            'site': site,
            'temp': temp,
            'gas': gas,
            'voltage': voltage,
            'site_regime': (site, temp, gas),
            'temp_gas': (temp, gas),
            'site_temp': (site, temp),
            'site_gas': (site, gas),
        }

    def _olecom_plan_pre_from_observation(self, observed_s):
        try:
            observed_s = float(observed_s)
        except Exception:
            return None
        if not np.isfinite(observed_s) or observed_s <= 0:
            return None
        planned = observed_s * float(self._olecom_pre_learning_factor)
        planned = max(float(self._olecom_pre_learning_min_s), planned)
        planned = min(float(BIOLOGIC_OLECOM_PRE_MAX_S), planned)
        return float(planned)

    def _olecom_select_pre_learning_observation(self, runtime, row):
        learning = runtime.get('olecom_pre_learning') if runtime else None
        if not learning:
            return None, None
        keys = self._olecom_pre_learning_keys(row)

        # Same XY/electrode + same temperature/gas: use the immediately previous
        # voltage at this site. This is the normal within-ladder pre learning path.
        obs = learning['last_by_site_regime'].get(keys['site_regime'])
        if obs is not None:
            return obs, 'same_site_temp_gas_previous_voltage'

        # New site under same gas/temp: seed from the previous/neighbor site first
        # voltage under that same gas/temp before falling back to older gas/temp memory.
        obs = learning['first_by_temp_gas'].get(keys['temp_gas'])
        if obs is not None:
            return obs, 'neighbor_site_same_temp_gas_first_voltage'

        # Gas changed at a known site: reuse that site's first-voltage decision
        # under the same temperature, but only the decision time, not the actual wait.
        obs = learning['first_by_site_temp'].get(keys['site_temp'])
        if obs is not None:
            return obs, 'same_site_previous_gas_first_voltage'

        # Temperature changed at a known site/gas: reuse the same site/gas first
        # voltage decision from the previous temperature.
        obs = learning['first_by_site_gas'].get(keys['site_gas'])
        if obs is not None:
            return obs, 'same_site_same_gas_previous_temperature_first_voltage'

        obs = learning.get('latest_first')
        if obs is not None:
            return obs, 'latest_first_voltage_fallback'
        return None, None

    def _olecom_apply_pre_learning(self, runtime, row):
        if not self._biologic_backend_is_olecom() or not self._row_uses_adaptive_runtime(row):
            return row, None
        updated = row.copy()
        obs, source = self._olecom_select_pre_learning_observation(runtime, row)
        if obs is None:
            try:
                base = float(row.get('HoldTime_s', self._olecom_pre_learning_first_default_s))
            except Exception:
                base = float(self._olecom_pre_learning_first_default_s)
            planned = min(
                float(BIOLOGIC_OLECOM_PRE_MAX_S),
                max(float(self._olecom_pre_learning_first_default_s), float(base)),
            )
            source = 'bootstrap_first_voltage_default'
            observed = None
            source_label = None
        else:
            observed = obs.get('stable_time_s')
            planned = self._olecom_plan_pre_from_observation(observed)
            source_label = obs.get('label')
            if planned is None:
                return updated, None
        updated['HoldTime_s'] = float(planned)
        updated['_OlecomPreLearningSource'] = source
        updated['_OlecomPreLearningStableTime_s'] = observed
        updated['_OlecomPreLearningPlannedPre_s'] = float(planned)
        note = {
            'source': source,
            'source_label': source_label,
            'source_stable_time_s': observed,
            'planned_pre_s': float(planned),
        }
        return updated, note

    def _olecom_extract_pre_stable_time(self, sequence_result):
        summary = getattr(sequence_result, 'summary', {}) or {}
        pre = summary.get('pre_stability') or {}
        try:
            stable_time = pre.get('stable_time_s')
        except AttributeError:
            stable_time = None
        try:
            stable_time = float(stable_time)
        except Exception:
            stable_time = None
        if stable_time is None or not np.isfinite(stable_time) or stable_time <= 0:
            return None
        return float(stable_time)

    def _olecom_update_pre_learning(self, runtime, row, sequence_result):
        if not runtime or not self._biologic_backend_is_olecom() or not self._row_uses_adaptive_runtime(row):
            return
        learning = runtime.setdefault('olecom_pre_learning', self._olecom_new_pre_learning_state())
        stable_time = self._olecom_extract_pre_stable_time(sequence_result)
        if stable_time is None:
            self._log("  [OLE-COM pre learning] no valid pre stable_time_s; not updating pre memory")
            return

        keys = self._olecom_pre_learning_keys(row)
        obs = {
            'label': str(row.get('Label', '')),
            'stable_time_s': float(stable_time),
            'planned_pre_s': _parse_optional_float(row.get('HoldTime_s'), default=None),
            'site': keys['site'],
            'temp': keys['temp'],
            'gas': keys['gas'],
            'voltage': keys['voltage'],
        }
        learning['observations'].append(obs)
        learning['last_by_site_regime'][keys['site_regime']] = obs

        if keys['site_regime'] not in learning['first_by_site_regime']:
            learning['first_by_site_regime'][keys['site_regime']] = obs
            learning['first_by_temp_gas'][keys['temp_gas']] = obs
            learning['first_by_site_temp'][keys['site_temp']] = obs
            learning['first_by_site_gas'][keys['site_gas']] = obs
            learning['latest_first'] = obs

        summary = runtime.get('summary') if runtime else None
        if summary is not None:
            summary['olecom_pre_learning_factor'] = float(self._olecom_pre_learning_factor)
            summary['olecom_pre_learning_observations'] = list(learning.get('observations', []))

        next_pre = self._olecom_plan_pre_from_observation(stable_time)
        self._log(
            f"  [OLE-COM pre learning] stable at {stable_time:.1f} s; "
            f"future pre seed={next_pre:.1f} s (x{self._olecom_pre_learning_factor:g})"
            if next_pre is not None else
            f"  [OLE-COM pre learning] stable at {stable_time:.1f} s"
        )

    def _build_adaptive_point_metadata(self, row, sequence_index):
        from adaptive_types import PointMetadata

        label = str(row.get('Label', f'row{sequence_index}')).strip() or f'row{sequence_index}'
        return PointMetadata(
            point_id=f'row{sequence_index:04d}_{label}',
            label=label,
            electrode_id=self._extract_electrode_id(label),
            temperature_c=_parse_optional_float(row.get('Temperature_C'), default=None),
            # adaptive_types keeps legacy field names, but GUI/CSV values are raw settings.
            gas_a_sccm=_row_gas_setting(row, 'A', default=None),
            gas_b_sccm=_row_gas_setting(row, 'B', default=None),
            voltage_v=float(row['V_dc']),
            sequence_index=sequence_index,
        )

    def _build_measurement_execution(self, row, measurement_mode):
        from adaptive_types import MeasurementExecution

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

    def _build_measurement_files(self, sequence_result):
        from adaptive_types import MeasurementFiles

        return MeasurementFiles(
            pre_ca_path=getattr(sequence_result, 'pre_ca_path', None),
            peis_path=getattr(sequence_result, 'peis_path', None),
            post_ca_path=getattr(sequence_result, 'post_ca_path', None),
        )

    def _biologic_backend_is_olecom(self):
        backend = str(BIOLOGIC_BACKEND).strip().lower()
        if backend == 'olecom':
            return True
        active_backend = str(getattr(self.bl, 'backend_name', '')).strip().lower()
        return active_backend == 'olecom'

    def _is_olecom_transient_error(self, exc):
        text = str(exc).lower()
        patterns = (
            'loadsettings',
            'buffer',
            'operation unavailable',
            '-2147221008',
            '-2147221005',
            'no active ec-lab ole-com session',
            'stale com',
            'ole-com hybrid runner failed',
        )
        return any(p in text for p in patterns)

    def _recover_olecom_after_error(self, reason):
        """Release only our Python COM proxy; do not kill EC-Lab or delete files."""
        self._log(f"  [OLE-COM recovery] releasing stale COM handle after: {reason}")
        try:
            if self.bl is not None and hasattr(self.bl, 'ctrl'):
                self.bl.ctrl = None
            if self.bl is not None and hasattr(self.bl, '_ctrl_thread_id'):
                self.bl._ctrl_thread_id = None
        except Exception as exc:
            self._log(f"  [OLE-COM recovery] handle release warning: {exc}")
        time.sleep(1.0)

    def _enqueue_olecom_postprocess(self, run_dir, label='', fit_model='RRQRQ'):
        if not run_dir:
            return
        self._olecom_postprocess_queue.put((str(run_dir), str(label or ''), str(fit_model or 'RRQRQ')))
        self._log(f"  [OLE-COM] full-arc postprocess queued: {label or run_dir}")
        if self._olecom_postprocess_hold:
            return
        self._start_olecom_postprocess_worker()

    def _start_olecom_postprocess_worker(self):
        with self._olecom_postprocess_lock:
            worker = self._olecom_postprocess_thread
            if worker is None or not worker.is_alive():
                self._olecom_postprocess_thread = threading.Thread(
                    target=self._olecom_postprocess_worker,
                    name='olecom_deferred_postprocess',
                    daemon=True,
                )
                self._olecom_postprocess_thread.start()

    def _start_olecom_postprocess_transition_window(self, reason):
        if not self._biologic_backend_is_olecom():
            return
        if self._olecom_postprocess_queue.empty():
            return
        self._olecom_postprocess_hold = False
        self._log(f"  [OLE-COM] starting deferred full-arc postprocess during {reason}")
        self._start_olecom_postprocess_worker()

    def _olecom_postprocess_worker(self):
        while True:
            try:
                run_dir, label, fit_model = self._olecom_postprocess_queue.get_nowait()
            except queue.Empty:
                with self._olecom_postprocess_lock:
                    if self._olecom_postprocess_queue.empty():
                        self._olecom_postprocess_thread = None
                        return
                    continue
            try:
                self._run_olecom_deferred_postprocess(run_dir, label=label, fit_model=fit_model)
            except Exception as exc:
                self._log(f"  [OLE-COM postprocess ERROR] {label or run_dir}: {exc}")
            finally:
                self._olecom_postprocess_queue.task_done()
            if self._olecom_postprocess_hold:
                with self._olecom_postprocess_lock:
                    self._olecom_postprocess_thread = None
                return

    def _run_olecom_deferred_postprocess(self, run_dir, *, label='', fit_model='RRQRQ'):
        run_dir = os.path.abspath(str(run_dir))
        script = os.path.join(os.path.dirname(__file__), 'tools', 'make_olecom_full_arc_from_run.py')
        stdout_path = os.path.join(run_dir, 'gui_deferred_full_arc_stdout.log')
        stderr_path = os.path.join(run_dir, 'gui_deferred_full_arc_stderr.log')
        cmd = [sys.executable, script, run_dir, '--fit-model', str(fit_model or 'RRQRQ')]
        with open(stdout_path, 'w', encoding='utf-8') as fout, open(stderr_path, 'w', encoding='utf-8') as ferr:
            completed = subprocess.run(cmd, stdout=fout, stderr=ferr)
        summary_path = os.path.join(run_dir, 'summary.json')
        full_arc_summary_path = os.path.join(run_dir, 'full_arc_summary.json')
        summary = {}
        if os.path.exists(summary_path):
            try:
                with open(summary_path, 'r', encoding='utf-8') as fh:
                    summary = json.load(fh) or {}
            except Exception:
                summary = {}
        if completed.returncode != 0:
            summary['deferred_full_arc_completed'] = False
            summary['full_arc_postprocess_warning'] = (
                f"make_olecom_full_arc_from_run failed with return code {completed.returncode}; "
                f"see {stderr_path}"
            )
            with open(summary_path, 'w', encoding='utf-8') as fh:
                json.dump(summary, fh, indent=2, default=str)
            raise RuntimeError(summary['full_arc_postprocess_warning'])
        full_arc = {}
        if os.path.exists(full_arc_summary_path):
            with open(full_arc_summary_path, 'r', encoding='utf-8') as fh:
                full_arc = json.load(fh) or {}
        summary.update({
            'postprocess_deferred': False,
            'deferred_full_arc_completed': True,
            'full_arc_summary_json': full_arc_summary_path,
            'full_arc_png': full_arc.get('recommended_full_arc_png') or full_arc.get('full_arc_png'),
            'full_arc_txt': full_arc.get('recommended_full_arc_txt') or full_arc.get('full_arc_txt'),
            'nyquist_equal_aspect_png': full_arc.get('recommended_nyquist_equal_aspect_png') or full_arc.get('nyquist_equal_aspect_png'),
            'full_arc_fit_model': full_arc.get('fit_model') or fit_model,
            'full_arc_fit_score': full_arc.get('recommended_fit_score', full_arc.get('fit_score')),
            'full_arc_recommended_variant': full_arc.get('recommended_full_arc_variant'),
            'full_arc_recommended_reason': full_arc.get('recommended_full_arc_reason'),
        })
        with open(summary_path, 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, indent=2, default=str)
        score = summary.get('full_arc_fit_score')
        if score is not None:
            self._log(f"  [OLE-COM postprocess] {label or os.path.basename(run_dir)} full-arc score={float(score):.4g}")
        else:
            self._log(f"  [OLE-COM postprocess] {label or os.path.basename(run_dir)} full-arc completed")

    def _run_olecom_hybrid_sequence(
        self,
        *,
        row,
        label,
        save_dir,
        channel,
        stop_event,
        dynamic_lf=True,
        defer_postprocess=None,
        max_attempts=2,
    ):
        if self.bl is None or not hasattr(self.bl, 'run_hybrid_live_stop'):
            raise RuntimeError("OLE-COM BioLogic controller is not connected or does not support hybrid live-stop")

        v_dc = float(row.get('V_dc', 0.0))
        dv = float(row.get('dV', 0.03))
        pre_s = min(max(0.0, float(row.get('HoldTime_s', 60))), float(BIOLOGIC_OLECOM_PRE_MAX_S))
        if dynamic_lf:
            # In OLE-COM adaptive mode the scout/stout duration is not learned.
            # We reserve a generous max and let the live FFT/tail guard stop it.
            scout_max_s = max(1.0, float(BIOLOGIC_OLECOM_SCOUT_MAX_S))
            scout_min_s = min(
                max(1.0, float(BIOLOGIC_OLECOM_MIN_FFT_DURATION_S)),
                scout_max_s,
            )
        else:
            requested_scout_min_s = max(1.0, float(row.get('CA_duration_s', 120)))
            scout_max_s = max(requested_scout_min_s, float(BIOLOGIC_OLECOM_SCOUT_MAX_S))
            scout_min_s = min(requested_scout_min_s, scout_max_s)
        requested_post_s = max(0.0, float(row.get('PostPEIS_HoldTime_s', 10)))
        post_s = min(requested_post_s, 10.0) if dynamic_lf else requested_post_s
        dt_s = max(0.1, float(row.get('CA_dt', MANUAL_QUICK_CA_DT_S)))
        peis_high = float(row.get('PEIS_fHigh', 100.0))
        n_pts = int(float(row.get('PEIS_nPts', 60)))
        requested_low = float(row.get('PEIS_fLow', BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ))

        if dynamic_lf:
            peis_deep_limit = float(BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ)
            peis_overlap_limit = float(BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ)
        else:
            # Manual Quick Rapid should honor the typed PEIS low frequency.
            peis_deep_limit = requested_low
            peis_overlap_limit = requested_low

        if defer_postprocess is None:
            defer_postprocess = bool(dynamic_lf)

        attempts = max(1, int(max_attempts))
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                result = self.bl.run_hybrid_live_stop(
                    v_dc=v_dc,
                    dv=dv,
                    pre_s=pre_s,
                    scout_min_s=scout_min_s,
                    scout_max_s=scout_max_s,
                    post_s=post_s,
                    dt=dt_s,
                    channel=channel,
                    peis_high=peis_high,
                    peis_deep_limit=peis_deep_limit,
                    peis_overlap_limit=peis_overlap_limit,
                    peis_npts=n_pts,
                    bandwidth=BIOLOGIC_OLECOM_BANDWIDTH,
                    save_dir=save_dir,
                    label=label,
                    stop_event=stop_event,
                    defer_postprocess=bool(defer_postprocess),
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts or not self._is_olecom_transient_error(exc):
                    raise
                self._log(f"  [OLE-COM recovery] retrying condition {attempt + 1}/{attempts}")
                self._recover_olecom_after_error(str(exc))
        else:
            raise last_exc

        summary = getattr(result, 'summary', {}) or {}
        raw_lf = summary.get('raw_recommended_lf_hz', getattr(result, 'raw_recommended_lf_hz', None))
        applied_lf = summary.get('applied_peis_lf_hz', getattr(result, 'applied_peis_lf_hz', None))
        if applied_lf is not None:
            self._log(
                f"  [OLE-COM] FFT LF raw={float(raw_lf):.4g} Hz, "
                f"applied PEIS LF={float(applied_lf):.4g} Hz"
                if raw_lf is not None else
                f"  [OLE-COM] applied PEIS LF={float(applied_lf):.4g} Hz"
            )
        if defer_postprocess:
            self._enqueue_olecom_postprocess(
                getattr(result, 'output_dir', save_dir),
                label=label,
                fit_model='RRQRQ',
            )
        return result

    def _csv_row_result_dir(self, result_root, row_index, label, run_stamp):
        safe_label = _safe_path_part(label, fallback=f'row{row_index:03d}')
        folder = os.path.join(
            result_root,
            f"row{int(row_index):03d}_{safe_label}_{run_stamp}",
        )
        os.makedirs(folder, exist_ok=True)
        return folder

    def _write_csv_measurement_record(
        self,
        *,
        row,
        row_index,
        measurement_mode,
        sequence_result,
        row_output_dir,
        analysis_payload=None,
    ):
        files = self._build_measurement_files(sequence_result)
        record = {
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'row_index': int(row_index),
            'label': str(row.get('Label', f'row{row_index}')),
            'measurement_mode': measurement_mode,
            'row_settings': {str(k): _json_safe(v) for k, v in row.items()},
            'row_output_dir': row_output_dir,
            'sequence_output_dir': getattr(sequence_result, 'output_dir', None),
            'sequence_summary_path': getattr(sequence_result, 'summary_path', None),
            'files': {
                'pre_ca_path': files.pre_ca_path,
                'peis_path': files.peis_path,
                'post_ca_path': files.post_ca_path,
            },
            'analysis': analysis_payload or {},
        }
        path = os.path.join(row_output_dir, 'measurement_record.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(_json_safe(record), fh, ensure_ascii=False, indent=2)
        return path

    def _postprocess_csv_measurement(
        self,
        *,
        row,
        row_index,
        measurement_mode,
        sequence_result,
        row_output_dir,
    ):
        os.makedirs(row_output_dir, exist_ok=True)
        label = str(row.get('Label', f'row{row_index}'))
        files = self._build_measurement_files(sequence_result)
        analysis_payload = {
            'status': 'not_started',
            'analysis_result_path': None,
            'visuals_path': None,
            'error': None,
            'full_processing': {
                'status': 'not_started',
                'result_path': None,
                'error': None,
            },
        }

        peis_path = files.peis_path
        dc_path = files.post_ca_path or files.pre_ca_path
        sequence_has_own_summary = bool(getattr(sequence_result, 'summary_path', None))
        skip_legacy_analysis = bool(getattr(sequence_result, 'skip_legacy_analysis', False))
        if peis_path and skip_legacy_analysis:
            analysis_payload.update({
                'status': 'skipped',
                'error': 'sequence already wrote OLE-COM summary/plots',
            })
            analysis_payload['full_processing'].update({
                'status': 'skipped',
                'error': 'sequence already wrote OLE-COM summary/plots',
            })
            self._log("  [CSV postprocess] skipped legacy analysis: OLE-COM runner owns summary/plots")
        elif peis_path and dc_path:
            try:
                from analysis_adapter import analyze_measurement_files_with_visuals

                analysis, visuals = analyze_measurement_files_with_visuals(
                    dc_path,
                    peis_path,
                    sample_name=label,
                )
                analysis_path = os.path.join(row_output_dir, 'analysis_result.json')
                visuals_path = os.path.join(row_output_dir, 'analysis_visuals.json')
                with open(analysis_path, 'w', encoding='utf-8') as fh:
                    json.dump(_json_safe(analysis), fh, ensure_ascii=False, indent=2)
                with open(visuals_path, 'w', encoding='utf-8') as fh:
                    json.dump(_json_safe(visuals), fh, ensure_ascii=False, indent=2)
                analysis_payload.update({
                    'status': 'completed',
                    'analysis_result_path': analysis_path,
                    'visuals_path': visuals_path,
                    'data_sufficient': getattr(analysis, 'data_sufficient', None),
                    'peis_only_sufficient': getattr(analysis, 'peis_only_sufficient', None),
                    'recommended_peis_lowest_freq_hz': getattr(
                        analysis, 'recommended_peis_lowest_freq_hz', None
                    ),
                    'recommended_cp_time_s': getattr(
                        analysis, 'recommended_peis_conservative_cp_time_s', None
                    ),
                    'fit_quality_score': getattr(analysis, 'fit_quality_score', None),
                })
                self._log(
                    f"  [CSV postprocess] analysis saved: "
                    f"{os.path.basename(analysis_path)}"
                )
            except Exception as exc:
                analysis_payload.update({
                    'status': 'failed',
                    'error': str(exc),
                })
                self._log(f"  [CSV postprocess] analysis failed: {exc}")
        elif peis_path:
            analysis_payload.update({
                'status': 'skipped',
                'error': 'PEIS-only run has no CA/CP file for hybrid analysis',
            })
            self._log("  [CSV postprocess] skipped hybrid analysis: PEIS-only run")
        else:
            analysis_payload.update({
                'status': 'skipped',
                'error': 'no PEIS file was saved',
            })
            self._log("  [CSV postprocess] skipped: no PEIS file")

        if peis_path and dc_path and not sequence_has_own_summary and not skip_legacy_analysis:
            try:
                from full_processing_adapter import (
                    FullProcessingSettings,
                    run_full_processing,
                    write_full_processing_result_json,
                )

                full_result = run_full_processing(
                    dc_path,
                    peis_path,
                    sample_name=_safe_path_part(label, fallback=f'row{row_index:03d}'),
                    settings=FullProcessingSettings(
                        output_root=os.path.join(row_output_dir, 'full_processing'),
                        auto_trim=True,
                    ),
                )
                full_path = os.path.join(row_output_dir, 'full_processing_result.json')
                write_full_processing_result_json(full_result, full_path)
                analysis_payload['full_processing'].update({
                    'status': 'completed',
                    'result_path': full_path,
                    'output_dir': full_result.output_dir,
                    'excel_path': full_result.excel_path,
                })
                self._log(
                    f"  [CSV postprocess] full processing saved: "
                    f"{os.path.basename(full_path)}"
                )
            except Exception as exc:
                analysis_payload['full_processing'].update({
                    'status': 'failed',
                    'error': str(exc),
                })
                self._log(f"  [CSV postprocess] full processing failed: {exc}")
        elif peis_path and sequence_has_own_summary:
            analysis_payload['full_processing'].update({
                'status': 'skipped',
                'error': 'sequence already wrote its own summary/plots',
            })
        elif peis_path:
            analysis_payload['full_processing'].update({
                'status': 'skipped',
                'error': 'no CA/CP file for full processing',
            })

        record_path = self._write_csv_measurement_record(
            row=row,
            row_index=row_index,
            measurement_mode=measurement_mode,
            sequence_result=sequence_result,
            row_output_dir=row_output_dir,
            analysis_payload=analysis_payload,
        )
        self._log(f"  [CSV postprocess] record saved: {record_path}")
        return analysis_payload

    def _create_adaptive_engine(self, normal_eis_floor_hz):
        factory = self._adaptive_engine_factory
        if factory is not None:
            try:
                return factory(normal_eis_floor_hz=normal_eis_floor_hz)
            except TypeError:
                return factory()

        from adaptive_engine import AdaptiveMeasurementEngine, AdaptiveEngineSettings

        return AdaptiveMeasurementEngine(
            engine_settings=AdaptiveEngineSettings(
                normal_eis_floor_hz=normal_eis_floor_hz,
            )
        )

    def _create_adaptive_runtime(self, df):
        adaptive_rows = df.apply(self._row_uses_adaptive_runtime, axis=1)
        if not adaptive_rows.any():
            return None

        try:
            normal_eis_floor_hz = float(self._adaptive_full_auto['normal_eis_floor_hz'].get())
        except Exception:
            normal_eis_floor_hz = 0.01

        runtime = {
            'engine': self._create_adaptive_engine(normal_eis_floor_hz),
            'regime_counts': {},
            'olecom_pre_learning': self._olecom_new_pre_learning_state(),
            'normal_eis_floor_hz': normal_eis_floor_hz,
            'summary': None,
            'summary_path': None,
        }
        self._log(
            f"[Adaptive] Runtime enabled for {int(adaptive_rows.sum())} ADAPT rows "
            f"(normal EIS floor {normal_eis_floor_hz:g} Hz)"
        )
        return runtime

    def _adaptive_summary_point_entry(
        self,
        point,
        row,
        row_index,
        measurement_mode,
        recommendation,
        finalize,
        sequence_result,
    ):
        files = self._build_measurement_files(sequence_result)
        return {
            'index': row_index + 1,
            'point_id': point.point_id,
            'label': point.label,
            'bias_v': float(row['V_dc']),
            'measurement_mode': measurement_mode,
            'used_parameters': {
                'peis_f_low_hz': _parse_optional_float(row.get('PEIS_fLow'), default=None),
                'hold_time_s': _parse_optional_float(row.get('HoldTime_s'), default=None),
                'olecom_pre_learning_source': row.get('_OlecomPreLearningSource'),
                'olecom_pre_learning_stable_time_s': _parse_optional_float(
                    row.get('_OlecomPreLearningStableTime_s'), default=None
                ),
                'post_peis_hold_time_s': _parse_optional_float(row.get('PostPEIS_HoldTime_s'), default=None),
                'ca_duration_s': _parse_optional_float(row.get('CA_duration_s'), default=None)
                if measurement_mode != 'normal_eis' else None,
            },
            'recommendation_used': None if recommendation is None else recommendation.to_dict(),
            'consumed_current_run_policy': self._adaptive_summary_consumed_policy(recommendation),
            'consumed_current_run_policy_text': self._format_adaptive_policy_text(recommendation=recommendation),
            'analysis_finalize': finalize,
            'completed_point_current_run_policy': self._adaptive_summary_finalize_policy(finalize),
            'completed_point_current_run_policy_text': self._format_adaptive_policy_text(finalize=finalize),
            'files': {
                'pre_ca_path': files.pre_ca_path,
                'peis_path': files.peis_path,
                'post_ca_path': files.post_ca_path,
            },
        }

    @staticmethod
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

    @staticmethod
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

    def _write_adaptive_summary(self, runtime):
        if not runtime:
            return
        summary_path = runtime.get('summary_path')
        summary = runtime.get('summary')
        if not summary_path or summary is None:
            return
        with open(summary_path, 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)

    def _init_adaptive_summary(self, runtime, *, result_root, df):
        if not runtime:
            return
        adaptive_rows = int(df.apply(self._row_uses_adaptive_runtime, axis=1).sum())
        runtime['summary_path'] = os.path.join(result_root, 'adaptive_runtime_summary.json')
        runtime['summary'] = {
            'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'result_root': result_root,
            'normal_eis_floor_hz': runtime.get('normal_eis_floor_hz'),
            'adaptive_row_count': adaptive_rows,
            'points': [],
            'mode_counts': {},
        }
        self._write_adaptive_summary(runtime)

    def _backfill_adaptive_summary(self, runtime, *, timeout_s=15.0):
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
            point_summary['completed_point_current_run_policy'] = self._adaptive_summary_finalize_policy(refreshed_finalize)
            point_summary['completed_point_current_run_policy_text'] = self._format_adaptive_policy_text(finalize=refreshed_finalize)
            if previous_finalize != refreshed_finalize:
                updated += 1
        if updated:
            self._write_adaptive_summary(runtime)
        return updated

    def _shutdown_adaptive_runtime(self, runtime):
        if not runtime:
            return
        engine = runtime.get('engine')
        if engine is None:
            return
        try:
            engine.shutdown()
        except Exception:
            pass

    def _apply_adaptive_recommendation(self, row, recommendation, *, update_timing=True):
        updated = row.copy()
        if recommendation.peis_lowest_freq_hz is not None:
            updated['PEIS_fLow'] = float(recommendation.peis_lowest_freq_hz)
        if update_timing and recommendation.cp_duration_s is not None:
            updated['CA_duration_s'] = float(recommendation.cp_duration_s)
        if update_timing and recommendation.planned_pre_peis_hold_s is not None:
            updated['HoldTime_s'] = float(recommendation.planned_pre_peis_hold_s)
        if update_timing and recommendation.planned_post_peis_hold_s is not None:
            updated['PostPEIS_HoldTime_s'] = float(recommendation.planned_post_peis_hold_s)
        return updated

    def _prepare_adaptive_row(self, runtime, row, row_index):
        if runtime is None or not self._row_uses_adaptive_runtime(row):
            return row, None

        row, pre_learning_note = self._olecom_apply_pre_learning(runtime, row)
        if pre_learning_note:
            src = pre_learning_note.get('source')
            planned = pre_learning_note.get('planned_pre_s')
            observed = pre_learning_note.get('source_stable_time_s')
            detail = (
                f" from stable_time={float(observed):.1f}s"
                if observed is not None else
                ""
            )
            self._log(
                f"[Adaptive/OLE-COM] pre plan -> {float(planned):.1f}s "
                f"({src}{detail})"
            )

        regime_key = self._adaptive_regime_key(row)
        prior_count = runtime['regime_counts'].get(regime_key, 0)
        if prior_count <= 0:
            return row, None

        point = self._build_adaptive_point_metadata(row, row_index + 1)
        recommendation = runtime['engine'].recommend_next_point(point)
        updated = self._apply_adaptive_recommendation(
            row,
            recommendation,
            update_timing=not self._biologic_backend_is_olecom(),
        )
        if self._biologic_backend_is_olecom():
            updated, pre_learning_note = self._olecom_apply_pre_learning(runtime, updated)
        if self._biologic_backend_is_olecom() and recommendation.measurement_mode != 'normal_eis':
            ca_text = f"live-stop max={float(BIOLOGIC_OLECOM_SCOUT_MAX_S):g}s"
        else:
            ca_text = (
                'n/a'
                if recommendation.measurement_mode == 'normal_eis'
                else f"{recommendation.cp_duration_s:g}s"
            )
        self._log(
            f"[Adaptive] {point.label}: runtime plan -> mode={recommendation.measurement_mode}, "
            f"f_low={recommendation.peis_lowest_freq_hz:g} Hz, CA={ca_text}, "
            f"seed={recommendation.analysis_source}, "
            f"policy={recommendation.current_run_policy_action or 'none'}"
        )
        if recommendation.notes:
            self._log(
                f"  [Adaptive notes] {' | '.join(str(note) for note in recommendation.notes[:3])}"
            )
        adaptive_policy_text = self._format_adaptive_policy_text(recommendation=recommendation)
        if adaptive_policy_text:
            self._set_monitor_recommendation(adaptive_policy_text)
        self._queue_monitor_event(
            'step',
            label=point.label,
            step='adaptive_plan',
            message=(
                f"Adaptive plan: {recommendation.measurement_mode}, "
                f"f_low={recommendation.peis_lowest_freq_hz:g} Hz"
            ),
        )
        return updated, recommendation

    def _finalize_adaptive_row(self, runtime, row, row_index, measurement_mode, sequence_result, recommendation=None):
        if runtime is None or not self._row_uses_adaptive_runtime(row):
            return

        point = self._build_adaptive_point_metadata(row, row_index + 1)
        measurement = self._build_measurement_execution(row, measurement_mode)
        files = self._build_measurement_files(sequence_result)
        engine = runtime['engine']
        engine.register_point(point, measurement, files)
        self._olecom_update_pre_learning(runtime, row, sequence_result)

        regime_key = self._adaptive_regime_key(row)
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
                self._log(
                    f"[Adaptive] {point.label}: analysis ready "
                    f"(sufficient={analysis.get('data_sufficient')}, "
                    f"next f_low={next_lf if next_lf is not None else 'n/a'}, "
                    f"next CA={next_cp if next_cp is not None else 'n/a'})"
                )
            else:
                self._log(
                    f"[Adaptive] {point.label}: analysis still running in background; "
                    f"the next row may temporarily use fallback planning"
                )
        else:
            self._log(
                f"[Adaptive] {point.label}: measurement_mode={measurement_mode}; "
                f"no hybrid post-analysis started for this point"
            )
        finalize_policy_text = self._format_adaptive_policy_text(finalize=finalize)
        if finalize_policy_text:
            self._set_monitor_recommendation(finalize_policy_text)

        summary = runtime.get('summary')
        if summary is not None:
            point_summary = self._adaptive_summary_point_entry(
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
            self._write_adaptive_summary(runtime)

        for request in engine.collect_pending_remeasurements():
            self._log(
                f"[Adaptive] Remeasure suggested for {request.point_id}: "
                f"mode={request.measurement_mode}, "
                f"f_low={request.peis_lowest_freq_hz:g} Hz, "
                f"CA={request.cp_duration_s:g}s"
            )

    def _start_biologic_live_poll(self, label, interval_s=0.05):
        if not ENABLE_BIOLOGIC_LIVE_SCALAR_POLL:
            self._log(
                f"[Live poll] {label}: disabled; using measurement-owned data only"
            )
            return None
        if self._biologic_backend_is_olecom():
            self._log(
                f"[Live poll] {label}: skipped for OLE-COM; using measurement-owned data only"
            )
            return None
        if self.bl is None or not hasattr(self.bl, 'get_live_values'):
            return None

        stop_event = threading.Event()
        channel = int(getattr(self, '_active_biologic_channel', 1))

        def _poll():
            consecutive_failures = 0
            while not stop_event.is_set():
                try:
                    values = self.bl.get_live_values(channel=channel)
                except Exception as exc:
                    consecutive_failures += 1
                    if consecutive_failures <= 3 or consecutive_failures % 10 == 0:
                        self._log(
                            f"[Live poll] {label}: transient failure "
                            f"#{consecutive_failures} ({exc})"
                        )
                    if consecutive_failures >= 20:
                        self._log(
                            f"[Live poll] {label}: stopped after "
                            f"{consecutive_failures} consecutive failures ({exc})"
                        )
                        break
                    stop_event.wait(interval_s)
                    continue
                consecutive_failures = 0
                self._queue_monitor_event('device_live', label=label, values=values)
                stop_event.wait(interval_s)

        threading.Thread(target=_poll, daemon=True).start()
        return stop_event

    def _add_row(self):
        defaults = {'Label': 'new_row', 'Temperature_C': 600, 'RampRate_C_per_min': 5.0,
                    'GasA_setting': 100, 'GasB_setting': 0,
                    'X_mm': 0, 'Y_mm': 0, 'Z_mm': 0,
                    'V_dc': 0.3, 'dV': 0.03, 'HoldTime_s': 120, 'PostPEIS_HoldTime_s': 10,
                    'PEIS_fHigh': 100, 'PEIS_fLow': 0.1, 'PEIS_nPts': 60,
                    'CA_duration_s': int(BIOLOGIC_OLECOM_SCOUT_MAX_S), 'CA_dt': 0.1,
                    'StableTime_s': 120, 'GasStableTime_s': 600, 'Skip': 0}
        vals = [str(defaults.get(c, '')) for c in self._tree_cols]
        self.tree.insert('', 'end', values=vals)

    def _del_row(self):
        sel = self.tree.selection()
        for iid in sel:
            self.tree.delete(iid)

    def _remember_tree_cell(self, event=None):
        if event is None:
            return
        if self.tree.identify_region(event.x, event.y) != 'cell':
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or not col_id:
            return
        try:
            col_idx = int(col_id.replace('#', '')) - 1
        except Exception:
            return
        if 0 <= col_idx < len(self._tree_cols):
            self._tree_active_cell = (row_id, col_idx)

    def _tree_start_cell(self):
        row_id, col_idx = getattr(self, '_tree_active_cell', (None, 0))
        children = list(self.tree.get_children())
        if row_id not in children:
            sel = list(self.tree.selection())
            row_id = sel[0] if sel else (children[0] if children else None)
            col_idx = 0
        if row_id is None:
            row_id = self.tree.insert('', 'end', values=[''] * len(self._tree_cols))
            col_idx = 0
        return row_id, max(0, min(int(col_idx), len(self._tree_cols) - 1))

    def _copy_tree_selection(self, event=None):
        try:
            selected = list(self.tree.selection())
            active_row, active_col = self._tree_start_cell()
            if len(selected) > 1:
                lines = [
                    '\t'.join(str(value) for value in self.tree.item(iid, 'values'))
                    for iid in selected
                ]
                text = '\n'.join(lines)
            else:
                values = list(self.tree.item(active_row, 'values'))
                text = str(values[active_col]) if active_col < len(values) else ''
            self.clipboard_clear()
            self.clipboard_append(text)
            self._log("[CSV List] Copied selection to clipboard")
        except Exception as exc:
            self._log(f"[CSV List] Copy failed: {exc}")
        return 'break'

    def _clipboard_table(self):
        text = self.clipboard_get()
        lines = [line for line in str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n')]
        while lines and lines[-1] == '':
            lines.pop()
        if not lines:
            return []
        return [line.split('\t') for line in lines]

    def _paste_tree_clipboard(self, event=None):
        try:
            table = self._clipboard_table()
            if not table:
                return 'break'
            start_row, start_col = self._tree_start_cell()
            children = list(self.tree.get_children())
            start_index = children.index(start_row) if start_row in children else len(children)
            for r_offset, pasted_row in enumerate(table):
                target_index = start_index + r_offset
                children = list(self.tree.get_children())
                if target_index < len(children):
                    iid = children[target_index]
                    values = list(self.tree.item(iid, 'values'))
                else:
                    values = [''] * len(self._tree_cols)
                    iid = self.tree.insert('', 'end', values=values)
                if len(values) < len(self._tree_cols):
                    values.extend([''] * (len(self._tree_cols) - len(values)))
                for c_offset, value in enumerate(pasted_row):
                    col_idx = start_col + c_offset
                    if 0 <= col_idx < len(self._tree_cols):
                        values[col_idx] = value
                self.tree.item(iid, values=values)
            self._tree_to_df()
            self._log(
                f"[CSV List] Pasted {len(table)} row(s) x "
                f"{max(len(row) for row in table)} column(s)"
            )
        except Exception as exc:
            self._log(f"[CSV List] Paste failed: {exc}")
        return 'break'

    def _edit_cell(self, event):
        """더블클릭으로 셀 편집."""
        region = self.tree.identify_region(event.x, event.y)
        if region != 'cell':
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        col_idx = int(col_id.replace('#', '')) - 1
        self._tree_active_cell = (row_id, col_idx)
        x, y, w, h = self.tree.bbox(row_id, col_id)
        cur_val = self.tree.item(row_id, 'values')[col_idx]

        entry = tk.Entry(self.tree, font=('Segoe UI', 10))
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, cur_val)
        entry.focus()

        def save(e=None):
            new_val = entry.get()
            vals = list(self.tree.item(row_id, 'values'))
            vals[col_idx] = new_val
            self.tree.item(row_id, values=vals)
            entry.destroy()

        entry.bind('<Return>', save)
        entry.bind('<FocusOut>', save)

    # ══════════════════════════════════════════════════════════════════════
    # Run automation
    # ══════════════════════════════════════════════════════════════════════
    def _browse_result(self):
        d = filedialog.askdirectory()
        if d:
            self._result_dir.set(d)

    def _conditions_for_run(self):
        self._tree_to_df()
        df = self.condition_df.copy()
        if 'Skip' in df.columns:
            df = df[df['Skip'].astype(str) != '1'].reset_index(drop=True)
        return df

    def _column_has_value(self, df, column):
        if column not in df.columns:
            return False
        return any(_parse_optional_float(value, default=None) is not None for value in df[column])

    def _condition_uses_positions(self, df):
        return any(self._column_has_value(df, col) for col in ('X_mm', 'Y_mm', 'Z_mm'))

    def _condition_uses_temperature(self, df):
        return self._column_has_value(df, 'Temperature_C')

    def _condition_uses_gas(self, df):
        return any(
            self._column_has_value(df, col)
            for cols in GAS_SETTING_COLUMNS.values()
            for col in cols
        )

    def _condition_uses_auto_contact(self, df):
        if 'AutoContactZ' not in df.columns:
            return False
        return any(_parse_boolish(value, default=False) for value in df['AutoContactZ'])

    def _gas_values_change(self, df):
        if not self._condition_uses_gas(df):
            return False
        values = []
        for _, row in df.iterrows():
            values.append((
                _row_gas_setting(row, 'A', default=None),
                _row_gas_setting(row, 'B', default=None),
            ))
        return len(set(values)) > 1

    def _preflight_conditions(self, df):
        errors = []
        warnings = []
        info = []

        if df.empty:
            errors.append('No runnable condition rows.')
            return errors, warnings, info

        if self.bl is None:
            errors.append('BioLogic is not connected.')
        if self._condition_uses_positions(df) and self.motor is None:
            errors.append('Motor is required by X/Y/Z columns but is not connected.')
        if self._condition_uses_auto_contact(df):
            if self.motor is None:
                errors.append('AutoContactZ requires motor connection.')
            if self.bl is None:
                errors.append('AutoContactZ requires BioLogic connection for OCV readback.')
        if self._condition_uses_temperature(df) and self.tc is None:
            errors.append('Temperature rows exist but temperature controller is not connected.')

        if self._condition_uses_gas(df):
            gas_mode = self._run_gas_mode_var.get()
            if gas_mode == 'require_auto':
                if self.mfc is None:
                    errors.append('Gas rows exist but MFC is not connected.')
                if not MFC_WRITE_ENABLED:
                    errors.append(
                        'Gas rows exist but MFC digital writes are disabled in config.py. '
                        'Enable MFC_WRITE_ENABLED after hardware validation, or choose manual_skip.'
                    )
            elif gas_mode == 'manual_skip':
                warnings.append('Gas rows will be treated as labels only; GUI will not change MFC setpoints.')
                if self._gas_values_change(df):
                    warnings.append('Gas values vary across rows while manual_skip is selected.')

        if self._condition_uses_auto_contact(df):
            missing_seed = []
            contact_param_errors = []
            for idx, row in df.iterrows():
                if _parse_boolish(row.get('AutoContactZ'), default=False):
                    label = str(row.get('Label', f'row{idx+1}'))
                    try:
                        seed_z = _parse_optional_float(row.get('Z_mm'), default=None)
                    except Exception as exc:
                        contact_param_errors.append(f'{label}: invalid Z_mm seed ({exc})')
                        seed_z = None
                    if seed_z is None:
                        missing_seed.append(label)
                    try:
                        start_offset = _parse_optional_float(row.get('ContactStartOffset_mm'), default=0.200)
                        step_mm = _parse_optional_float(row.get('ContactStep_mm'), default=0.010)
                        max_drop = _parse_optional_float(row.get('ContactMaxDrop_mm'), default=0.400)
                        max_beyond = _parse_optional_float(row.get('ContactMaxBeyondSeed_mm'), default=0.200)
                        threshold = _parse_optional_float(row.get('ContactOCVThreshold_V'), default=0.100)
                        settle_s = _parse_optional_float(row.get('ContactSettle_s'), default=1.00)
                        engage_mm = _parse_optional_float(row.get('ContactEngage_mm'), default=0.050)
                    except Exception as exc:
                        contact_param_errors.append(f'{label}: invalid AutoContactZ parameter ({exc})')
                        continue
                    if step_mm is None or step_mm <= 0:
                        contact_param_errors.append(f'{label}: ContactStep_mm must be > 0')
                    if max_drop is None or max_drop <= 0:
                        contact_param_errors.append(f'{label}: ContactMaxDrop_mm must be > 0')
                    if start_offset is None or start_offset < 0:
                        contact_param_errors.append(f'{label}: ContactStartOffset_mm must be >= 0')
                    if max_beyond is not None and max_beyond < 0:
                        contact_param_errors.append(f'{label}: ContactMaxBeyondSeed_mm must be >= 0')
                    if threshold is None or threshold < 0:
                        contact_param_errors.append(f'{label}: ContactOCVThreshold_V must be >= 0')
                    if settle_s is None or settle_s < 0:
                        contact_param_errors.append(f'{label}: ContactSettle_s must be >= 0')
                    if engage_mm is None or engage_mm < 0:
                        contact_param_errors.append(f'{label}: ContactEngage_mm must be >= 0')
            if missing_seed:
                warnings.append(
                    'Some AutoContactZ rows have no Z seed; current stage Z will be used: '
                    + ', '.join(missing_seed[:5])
                )
            if contact_param_errors:
                errors.extend(contact_param_errors[:10])
                if len(contact_param_errors) > 10:
                    errors.append(f'... plus {len(contact_param_errors) - 10} more AutoContactZ parameter errors.')

        if self._condition_uses_positions(df):
            if not bool(STAGE_SAFE_MOVE.get('enabled', False)):
                errors.append('STAGE_SAFE_MOVE is disabled; XY moves could drag the tip across the sample.')
            elif bool(STAGE_SAFE_MOVE.get('positive_z_is_up', True)):
                warnings.append('STAGE_SAFE_MOVE says positive Z is up; confirm this matches the stage.')
            else:
                info.append('Safe XY travel enabled: Z decreases before XY moves, then returns to target/contact.')

        if self._condition_uses_auto_contact(df):
            info.append('AutoContactZ active: seed Z -> start above seed -> approach in +Z -> OCV contact -> engage.')

        return errors, warnings, info

    def _format_preflight_report(self, errors, warnings, info, df):
        lines = [f'Rows to run: {len(df)}']
        if errors:
            lines.append('\nERRORS:')
            lines.extend(f'- {msg}' for msg in errors)
        if warnings:
            lines.append('\nWARNINGS:')
            lines.extend(f'- {msg}' for msg in warnings)
        if info:
            lines.append('\nINFO:')
            lines.extend(f'- {msg}' for msg in info)
        if not errors and not warnings:
            lines.append('\nPreflight passed with no warnings.')
        return '\n'.join(lines)

    def _preflight_check_dialog(self):
        df = self._conditions_for_run()
        errors, warnings, info = self._preflight_conditions(df)
        report = self._format_preflight_report(errors, warnings, info, df)
        self._log('\n[Preflight]\n' + report)
        if errors:
            messagebox.showerror('Preflight failed', report)
        elif warnings:
            messagebox.showwarning('Preflight warnings', report)
        else:
            messagebox.showinfo('Preflight passed', report)
        return not errors

    def _preview_run_plan(self):
        df = self._conditions_for_run()
        if df.empty:
            messagebox.showwarning('Preview Plan', 'No runnable condition rows.')
            return
        lines = [f'Previewing first {min(len(df), 20)} of {len(df)} runnable rows:']
        for idx, row in df.head(20).iterrows():
            label = str(row.get('Label', f'row{idx+1}'))
            xyz = (
                _parse_optional_float(row.get('X_mm'), default=None),
                _parse_optional_float(row.get('Y_mm'), default=None),
                _parse_optional_float(row.get('Z_mm'), default=None),
            )
            contact = 'AutoContactZ' if _parse_boolish(row.get('AutoContactZ'), default=False) else 'fixed Z'
            lines.append(
                f"{idx+1:03d}. {label}: T={row.get('Temperature_C', '')}, "
                f"gas setting=({_row_gas_setting(row, 'A', default='')},"
                f"{_row_gas_setting(row, 'B', default='')}), "
                f"V={row.get('V_dc', '')}, XYZ={xyz}, {contact}"
            )
        if self._run_retract_tip_on_done_var.get():
            lines.append(
                f"After successful completion: retract tip by "
                f"{self._run_retract_tip_mm_var.get()} mm."
            )
        report = '\n'.join(lines)
        self._log('\n[Preview]\n' + report)
        messagebox.showinfo('Run Plan Preview', report)

    def _start_run(self):
        df = self._conditions_for_run()
        if df.empty:
            messagebox.showwarning("No conditions", "먼저 조건 CSV를 불러오세요.")
            return

        errors, warnings, info = self._preflight_conditions(df)
        report = self._format_preflight_report(errors, warnings, info, df)
        self._log('\n[Preflight]\n' + report)
        if errors:
            messagebox.showerror('Preflight failed', report)
            return
        if self._run_confirm_preflight_var.get():
            if not messagebox.askyesno('Start automated run?', report + '\n\nStart run now?'):
                return

        self._active_biologic_channel = self._selected_biologic_channel()
        self.stop_flag.clear()
        self.running = True
        self._monitor_reset()
        self._btn_start.config(state='disabled')
        self._btn_stop.config(state='normal')

        t = threading.Thread(target=self._run_worker, daemon=True)
        t.start()

    def _stop_run(self):
        self.stop_flag.set()
        if self.bl is not None and hasattr(self.bl, 'stop_measurement'):
            try:
                self.bl.stop_measurement(channel=self._active_biologic_channel)
            except Exception as exc:
                self._log(f"[RUN] stop warning: {exc}")
        self._log("[RUN] Stop requested — will stop after current step.")
        self._status_var.set("Stopping...")

    def _evaluate_temperature_safety_trip(self, pv_c, previous_pv_c, active_target_c):
        if not TEMP_SAFETY_ENABLED:
            return None
        if active_target_c is None or active_target_c < TEMP_SAFETY_ACTIVE_TARGET_C:
            return None
        if pv_c is None or not np.isfinite(pv_c):
            return None
        if pv_c <= TEMP_SAFETY_MIN_VALID_C:
            return (
                f"Temperature safety trip: furnace PV dropped to {pv_c:.1f} C while "
                f"target was {active_target_c:.1f} C. This looks like a bad TC/contact read."
            )
        if (
            previous_pv_c is not None and np.isfinite(previous_pv_c) and
            previous_pv_c >= TEMP_SAFETY_ACTIVE_TARGET_C and
            (previous_pv_c - pv_c) >= TEMP_SAFETY_MAX_DROP_C
        ):
            return (
                f"Temperature safety trip: furnace PV dropped by "
                f"{previous_pv_c - pv_c:.1f} C ({previous_pv_c:.1f} -> {pv_c:.1f} C) "
                f"while target was {active_target_c:.1f} C. This looks like a TC/contact fault."
            )
        return None

    def _write_temperature_safety_trip_record(self, payload):
        result_root = self._active_run_result_root or self._result_dir.get()
        if not result_root:
            return
        try:
            os.makedirs(result_root, exist_ok=True)
            path = os.path.join(result_root, 'temperature_safety_trip.json')
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._log(f"[Temp safety] failed to write trip record: {exc}")

    def _show_temperature_safety_error(self, message):
        try:
            messagebox.showerror("Temperature Safety Trip", message)
        except Exception as exc:
            self._log(f"[Temp safety] failed to show dialog: {exc}")

    def _trigger_temperature_safety_trip(self, message, pv_c, previous_pv_c):
        if self._temp_safety_trip_payload is not None:
            return
        payload = {
            'triggered_at_utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),
            'message': message,
            'pv_c': None if pv_c is None else float(pv_c),
            'previous_pv_c': None if previous_pv_c is None else float(previous_pv_c),
            'target_c': (
                None if self._active_temperature_target_c is None
                else float(self._active_temperature_target_c)
            ),
            'shutdown_setpoint_c': float(TEMP_SAFETY_SHUTDOWN_SETPOINT_C),
        }
        self._temp_safety_trip_payload = payload
        self.stop_flag.set()
        if self.bl is not None and hasattr(self.bl, 'stop_measurement'):
            try:
                self.bl.stop_measurement(channel=self._active_biologic_channel)
            except Exception as exc:
                self._log(f"[Temp safety] BioLogic stop warning: {exc}")
        if self.tc is not None and hasattr(self.tc, 'safe_shutdown'):
            try:
                self.tc.safe_shutdown(TEMP_SAFETY_SHUTDOWN_SETPOINT_C)
            except Exception as exc:
                payload['shutdown_error'] = str(exc)
                self._log(f"[Temp safety] furnace shutdown warning: {exc}")
        self._write_temperature_safety_trip_record(payload)
        try:
            self.after(0, lambda: self._status_var.set("Temperature safety trip"))
        except Exception:
            pass
        self._queue_monitor_event('error', label=self._monitor_label, message='Temperature safety trip')
        self._log(f"[Temp safety] {message}")
        try:
            self.after(0, lambda: self._show_temperature_safety_error(message))
        except Exception:
            pass

    def _start_temperature_safety_monitor(self):
        if self.tc is None or not TEMP_SAFETY_ENABLED:
            return None
        stop_event = threading.Event()
        self._temp_safety_last_pv_c = None

        def _monitor():
            while not stop_event.wait(TEMP_SAFETY_POLL_S):
                if not self.running or self.stop_flag.is_set():
                    break
                try:
                    pv_c = float(self.tc.get_temperature())
                except Exception as exc:
                    self._log(f"[Temp safety] monitor read warning: {exc}")
                    continue
                reason = self._evaluate_temperature_safety_trip(
                    pv_c=pv_c,
                    previous_pv_c=self._temp_safety_last_pv_c,
                    active_target_c=self._active_temperature_target_c,
                )
                previous_pv_c = self._temp_safety_last_pv_c
                self._temp_safety_last_pv_c = pv_c
                if reason:
                    self._trigger_temperature_safety_trip(reason, pv_c, previous_pv_c)
                    break

        threading.Thread(target=_monitor, daemon=True).start()
        self._temp_safety_monitor_stop = stop_event
        return stop_event

    def _contact_param_from_row(self, row, column, manual_key, default):
        manual_default = _parse_optional_float(
            self._contact_search[manual_key].get(),
            default=default,
        )
        return _parse_optional_float(row.get(column), default=manual_default)

    def _contact_position_key(self, row):
        x = _parse_optional_float(row.get('X_mm'), default=None)
        y = _parse_optional_float(row.get('Y_mm'), default=None)
        if x is None or y is None:
            return None
        return (round(float(x), 5), round(float(y), 5))

    def _contact_exact_key(self, row):
        pos_key = self._contact_position_key(row)
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

    def _auto_contact_z_for_row(self, row, label, contact_z_cache=None, exact_contact_z_cache=None):
        if not _parse_boolish(row.get('AutoContactZ'), default=False):
            return None
        if self.motor is None or self.bl is None:
            raise RuntimeError('AutoContactZ requires both motor and BioLogic connections')
        base_z = _parse_optional_float(row.get('Z_mm'), default=None)
        cache_key = self._contact_position_key(row)
        exact_key = self._contact_exact_key(row)
        if exact_contact_z_cache is not None and exact_key in exact_contact_z_cache:
            measure_z = float(exact_contact_z_cache[exact_key])
            self._log(
                f"  AutoContactZ exact cached contact at Z={measure_z:.3f} mm "
                f"reused for same XY/temp/gas"
            )
            return measure_z
        if contact_z_cache is not None and cache_key in contact_z_cache:
            base_z = contact_z_cache[cache_key]
            self._log(
                f"  AutoContactZ seed updated from previous same-XY measurement: "
                f"Z={float(base_z):.3f} mm"
            )
        if base_z is None:
            base_z = self.motor.get_position('Z')
        self._queue_monitor_event('step', label=label, step='contact_z',
                                  message='OCV contact search')
        self._log(f"  AutoContactZ enabled: seed Z={float(base_z):.3f} mm")
        found_z, measure_z = self._execute_contact_z_search(
            base_z=float(base_z),
            start_offset=self._contact_param_from_row(row, 'ContactStartOffset_mm', 'start_offset', 0.200),
            step_mm=self._contact_param_from_row(row, 'ContactStep_mm', 'step_mm', 0.010),
            max_drop_mm=self._contact_param_from_row(row, 'ContactMaxDrop_mm', 'max_drop_mm', 0.400),
            max_beyond_seed_mm=self._contact_param_from_row(row, 'ContactMaxBeyondSeed_mm', 'max_beyond_seed_mm', 0.200),
            ocv_threshold=self._contact_param_from_row(row, 'ContactOCVThreshold_V', 'ocv_threshold', 0.100),
            settle_s=self._contact_param_from_row(row, 'ContactSettle_s', 'settle_s', 1.00),
            engage_mm=self._contact_param_from_row(row, 'ContactEngage_mm', 'engage_mm', 0.050),
            status_prefix=f'Auto contact {label}',
            stop_event=self.stop_flag,
        )
        self._log(
            f"  AutoContactZ found contact at Z={found_z:.3f} mm; "
            f"measurement Z={measure_z:.3f} mm"
        )
        if contact_z_cache is not None and cache_key is not None:
            contact_z_cache[cache_key] = measure_z
        if exact_contact_z_cache is not None and exact_key is not None:
            exact_contact_z_cache[exact_key] = measure_z
        return measure_z

    def _retract_tip_after_successful_run(self):
        if not self._run_retract_tip_on_done_var.get():
            return
        if self.motor is None:
            self._log("  End-of-run tip retract skipped: motor is not connected")
            return
        try:
            distance_mm = abs(float(self._run_retract_tip_mm_var.get()))
        except Exception:
            self._log("  End-of-run tip retract skipped: invalid retract distance")
            return
        if distance_mm <= 0:
            return
        try:
            current_z = float(self.motor.get_position('Z'))
            if not np.isfinite(current_z):
                raise RuntimeError("current Z readback is not finite")
            z_up_sign = 1.0 if bool(STAGE_SAFE_MOVE.get('positive_z_is_up', True)) else -1.0
            target_z = current_z + z_up_sign * distance_mm
            self._queue_monitor_event(
                'step',
                label='End of run',
                step='tip_retract',
                message=f'Retracting tip {distance_mm:.3f} mm',
            )
            self._log(
                f"  End-of-run tip retract: Z {current_z:.3f} -> {target_z:.3f} mm "
                f"({distance_mm:.3f} mm up)"
            )
            self.motor.move_abs_wait('Z', target_z)
        except Exception as exc:
            self._log(f"  End-of-run tip retract warning: {exc}")

    def _run_worker(self):
        df = self.condition_df.copy()
        if 'Skip' in df.columns:
            df = df[df['Skip'].astype(str) != '1'].reset_index(drop=True)

        total = len(df)
        result_root = self._result_dir.get()
        os.makedirs(result_root, exist_ok=True)
        run_stamp = time.strftime('%Y%m%d_%H%M%S')
        try:
            conditions_snapshot = os.path.join(result_root, f'csv_run_conditions_{run_stamp}.csv')
            df.to_csv(conditions_snapshot, index=False)
            self._log(f"[RUN] CSV condition snapshot saved: {conditions_snapshot}")
        except Exception as exc:
            self._log(f"[RUN] CSV condition snapshot warning: {exc}")
        self._active_run_result_root = result_root
        self._active_temperature_target_c = None
        self._temp_safety_trip_payload = None
        self._olecom_postprocess_hold = self._biologic_backend_is_olecom()
        temp_safety_stop = self._start_temperature_safety_monitor()
        prev_temp = None
        prev_gas = None
        prev_pos = None
        rapid_sequence = MODS.get('rapid_sequence') or MODS.get('sequence')
        normal_sequence = MODS.get('normal_sequence')
        biologic_channel = int(getattr(self, '_active_biologic_channel', 1))
        adaptive_runtime = self._create_adaptive_runtime(df)
        self._init_adaptive_summary(adaptive_runtime, result_root=result_root, df=df)
        contact_z_cache = {}
        exact_contact_z_cache = {}
        run_failed = False

        try:
            for idx, row in df.iterrows():
                if self.stop_flag.is_set():
                    self._log("[RUN] Stopped by user.")
                    break

                label = str(row.get('Label', f'row{idx+1}'))
                self._log(f"\n{'='*50}")
                self._log(f"[{idx+1}/{total}] {label}")
                self._queue_monitor_event('row_start', label=label, row_index=idx + 1, total=total)
                self._status_var.set(f"Row {idx+1}/{total}: {label}")
                self._progress_var.set((idx / total) * 100)
                self._progress_lbl.config(text=f"{idx+1} / {total}")

                try:
                    row, adaptive_recommendation = self._prepare_adaptive_row(adaptive_runtime, row, idx)
                    measurement_mode = (
                        adaptive_recommendation.measurement_mode
                        if adaptive_recommendation is not None else 'rapid_eis'
                    )
                    label = str(row.get('Label', label))
                    row_output_dir = self._csv_row_result_dir(
                        result_root,
                        idx + 1,
                        label,
                        run_stamp,
                    )
                    self._log(f"  Row result folder: {row_output_dir}")
                    if _parse_boolish(row.get('AutoContactZ'), default=False):
                        cache_key = self._contact_position_key(row)
                        if cache_key in contact_z_cache:
                            row = row.copy()
                            row['Z_mm'] = contact_z_cache[cache_key]
                            self._log(
                                f"  AutoContactZ seed updated from previous same-XY measurement: "
                                f"Z={float(contact_z_cache[cache_key]):.3f} mm"
                            )

                    gas_a = _row_gas_setting(row, 'A', default=None)
                    gas_b = _row_gas_setting(row, 'B', default=None)
                    gas_now = (gas_a, gas_b)
                    gas_changed = prev_gas != gas_now
                    gas_mode = self._run_gas_mode_var.get()
                    gas_set_started_at = None
                    gas_wait = (
                        _parse_optional_float(row.get('GasStableTime_s', 600), default=600)
                        if any(v is not None for v in gas_now) and gas_changed else 0.0
                    )
                    if any(v is not None for v in gas_now) and gas_mode == 'manual_skip':
                        if gas_changed:
                            self._log(
                                "  Gas setting write skipped by manual_skip mode "
                                f"(requested A={gas_a}, B={gas_b})"
                            )
                            prev_gas = gas_now
                    elif any(v is not None for v in gas_now) and gas_mode == 'require_auto' and not MFC_WRITE_ENABLED:
                        raise RuntimeError('Gas write requested but MFC_WRITE_ENABLED=False in config.py')
                    elif self.mfc and any(v is not None for v in gas_now) and gas_changed:
                        self._start_olecom_postprocess_transition_window('gas change/stabilization')
                        self._queue_monitor_event('step', label=label, step='gas',
                                                  message='Gas target set before stabilization')
                        if gas_a is not None:
                            setter = getattr(self.mfc, 'set_setting', self.mfc.set_flow)
                            setter('A', gas_a)
                        if gas_b is not None:
                            setter = getattr(self.mfc, 'set_setting', self.mfc.set_flow)
                            setter('B', gas_b)
                        gas_set_started_at = time.time()
                        self._log(
                            f"  Gas target set first (A={gas_a}, B={gas_b}); "
                            f"required gas stabilization {gas_wait:.0f} s"
                        )
                        prev_gas = gas_now
                    elif self.mfc and any(v is not None for v in gas_now):
                        self._log("  Gas conditions unchanged - skipping gas stabilization")
                    elif self.mfc:
                        self._log("  Gas step skipped for this row")

                    target_temp = _parse_optional_float(row.get('Temperature_C'), default=None)
                    self._active_temperature_target_c = target_temp
                    temp_changed = prev_temp != target_temp
                    if self.tc and target_temp is not None and temp_changed:
                        self._start_olecom_postprocess_transition_window('temperature stabilization')
                        ramp_rate = _parse_optional_float(row.get('RampRate_C_per_min', 5.0), default=5.0)
                        self._queue_monitor_event('step', label=label, step='temperature',
                                                  message=f'Temperature ramp to {target_temp:.0f} C')
                        self.tc.set_ramp_rate(ramp_rate)
                        self.tc.set_temperature(target_temp)
                        stable_s = _parse_optional_float(row.get('StableTime_s', 120), default=120)
                        self._log(
                            f"  Waiting for temperature stabilization "
                            f"({target_temp:.0f} C, ramp {ramp_rate:.2f} C/min, {stable_s:.0f} s); "
                            "gas stabilization overlaps this wait ..."
                        )
                        self.tc.wait_stable(target_temp, tol=2.0,
                                            stable_time=stable_s,
                                            poll=10)
                        prev_temp = target_temp
                    elif self.tc and target_temp is not None:
                        self._log(f"  Temperature unchanged ({target_temp:.0f} C) - skipping stabilization wait")
                    elif self.tc:
                        self._log("  Temperature step skipped for this row")

                    if gas_set_started_at is not None and gas_wait > 0:
                        elapsed = max(0.0, time.time() - gas_set_started_at)
                        remaining = max(0.0, float(gas_wait) - elapsed)
                        if remaining > 0:
                            self._log(
                                f"  Waiting remaining gas stabilization "
                                f"({remaining:.0f} s of {gas_wait:.0f} s; "
                                f"{elapsed:.0f} s already overlapped) ..."
                            )
                            time.sleep(remaining)
                        else:
                            self._log(
                                f"  Gas stabilization covered by prior wait "
                                f"({elapsed:.0f} s >= {gas_wait:.0f} s)"
                            )

                    pos_now = tuple(
                        _parse_optional_float(row.get(f'{ax}_mm'), default=None)
                        for ax in ['X', 'Y', 'Z']
                    )
                    pos_changed = prev_pos != pos_now
                    if self.motor and any(v is not None for v in pos_now) and pos_changed:
                        self._start_olecom_postprocess_transition_window('tip move/contact transition')
                        self._queue_monitor_event('step', label=label, step='tip_position',
                                                  message='Tip moving')
                        current_pos = {
                            ax: (
                                self.motor.get_position(ax)
                                if _parse_optional_float(row.get(f'{ax}_mm'), default=None) is not None
                                else None
                            )
                            for ax in ['X', 'Y', 'Z']
                        }
                        target_pos = {
                            ax: _parse_optional_float(row.get(f'{ax}_mm'), default=None)
                            for ax in ['X', 'Y', 'Z']
                        }
                        moves = self.motor.move_xyz_safe(
                            x_mm=target_pos['X'],
                            y_mm=target_pos['Y'],
                            z_mm=target_pos['Z'],
                            current_positions=current_pos,
                            log_fn=self._log,
                        )
                        prev_pos = pos_now
                    elif self.motor and any(v is not None for v in pos_now):
                        self._log("  Tip position unchanged - skipping move")
                    elif self.motor:
                        self._log("  Tip move skipped for this row")

                    measurement_z = self._auto_contact_z_for_row(
                        row,
                        label,
                        contact_z_cache,
                        exact_contact_z_cache,
                    )
                    if measurement_z is not None:
                        row = row.copy()
                        row['Z_mm'] = measurement_z
                        pos_now = (
                            _parse_optional_float(row.get('X_mm'), default=None),
                            _parse_optional_float(row.get('Y_mm'), default=None),
                            measurement_z,
                        )
                        prev_pos = pos_now

                    if self._biologic_backend_is_olecom():
                        self._olecom_postprocess_hold = True

                    if self.bl and (self._biologic_backend_is_olecom() or rapid_sequence):
                        save_dir = row_output_dir
                        live_poll_stop = None if self._biologic_backend_is_olecom() else self._start_biologic_live_poll(label)
                        try:
                            if self._biologic_backend_is_olecom():
                                if measurement_mode == 'normal_eis':
                                    self._log(
                                        "  [OLE-COM] normal_eis request converted to hybrid live-stop; "
                                        "FFT/PEIS LF policy remains active"
                                    )
                                measurement_mode = 'rapid_eis'
                                self._log("  [Full-auto] using OLE-COM CA/FFT seeded PEIS protocol")
                                sequence_result = self._run_olecom_hybrid_sequence(
                                    row=row,
                                    label=label,
                                    save_dir=save_dir,
                                    channel=biologic_channel,
                                    stop_event=self.stop_flag,
                                    dynamic_lf=True,
                                )
                                summary = getattr(sequence_result, 'summary', {}) or {}
                                applied_lf = summary.get(
                                    'applied_peis_lf_hz',
                                    getattr(sequence_result, 'applied_peis_lf_hz', None),
                                )
                                if applied_lf is not None:
                                    row = row.copy()
                                    row['PEIS_fLow'] = float(applied_lf)
                            elif measurement_mode == 'normal_eis' and normal_sequence:
                                sequence_result = normal_sequence(
                                    biologic=self.bl,
                                    v_dc=float(row['V_dc']),
                                    peis_f_high=float(row.get('PEIS_fHigh', 1e5)),
                                    peis_f_low=float(row.get('PEIS_fLow', 0.1)),
                                    peis_npts=int(float(row.get('PEIS_nPts', 60))),
                                    amplitude_mv=float(row.get('dV', 0.03)) * 1000.0,
                                    channel=biologic_channel,
                                    save_dir=save_dir,
                                    label=label,
                                    monitor_callback=self._queue_monitor_event,
                                    stop_event=self.stop_flag,
                                )
                            else:
                                if measurement_mode == 'normal_eis' and not normal_sequence:
                                    self._log("  [Adaptive] normal_eis requested but no normal sequence is available; falling back to rapid_eis")
                                    measurement_mode = 'rapid_eis'
                                if self._row_uses_adaptive_runtime(row):
                                    from live_seeded_sequence import live_seeded_rapid_eis_sequence

                                    self._log("  [Full-auto] using live CA/FFT seeded PEIS protocol")
                                    sequence_result = live_seeded_rapid_eis_sequence(
                                        biologic=self.bl,
                                        v_dc=float(row['V_dc']),
                                        dv=float(row.get('dV', 0.03)),
                                        pre_min=float(row.get('HoldTime_s', 60)),
                                        pre_max=max(900.0, float(row.get('HoldTime_s', 60))),
                                        scout_min=float(row.get('CA_duration_s', 120)),
                                        scout_max=max(1200.0, float(row.get('CA_duration_s', 120))),
                                        ca_dt=max(0.1, float(row.get('CA_dt', 0.1))),
                                        channel=biologic_channel,
                                        peis_f_high=float(row.get('PEIS_fHigh', 1e6)),
                                        peis_floor=0.5,
                                        peis_deep_limit=0.05,
                                        peis_npts=int(float(row.get('PEIS_nPts', 60))),
                                        peis_amplitude_mv=float(row.get('dV', 0.03)) * 1000.0,
                                        bandwidth='BW4',
                                        current_range='AUTO',
                                        save_dir=save_dir,
                                        label=label,
                                        monitor_callback=self._queue_monitor_event,
                                        stop_event=self.stop_flag,
                                    )
                                    applied_lf = getattr(sequence_result, 'applied_peis_lf_hz', None)
                                    raw_lf = getattr(sequence_result, 'raw_recommended_lf_hz', None)
                                    if applied_lf is not None:
                                        row = row.copy()
                                        row['PEIS_fLow'] = float(applied_lf)
                                        self._log(
                                            f"  [Full-auto] FFT LF raw={float(raw_lf):.4g} Hz, "
                                            f"applied PEIS LF={float(applied_lf):.4g} Hz"
                                            if raw_lf is not None else
                                            f"  [Full-auto] applied PEIS LF={float(applied_lf):.4g} Hz"
                                        )
                                else:
                                    sequence_result = rapid_sequence(
                                        biologic=self.bl,
                                        v_dc=float(row['V_dc']),
                                        dv=float(row.get('dV', 0.03)),
                                        hold_time=float(row.get('HoldTime_s', 60)),
                                        post_peis_hold_time=float(row.get('PostPEIS_HoldTime_s', 30)),
                                        peis_f_high=float(row.get('PEIS_fHigh', 1e5)),
                                        peis_f_low=float(row.get('PEIS_fLow', 0.1)),
                                        peis_npts=int(float(row.get('PEIS_nPts', 60))),
                                        ca_duration=float(row.get('CA_duration_s', 200)),
                                        ca_dt=float(row.get('CA_dt', 0.1)),
                                        channel=biologic_channel,
                                        save_dir=save_dir,
                                        label=label,
                                        monitor_callback=self._queue_monitor_event,
                                        stop_event=self.stop_flag,
                                    )
                        finally:
                            if live_poll_stop is not None:
                                live_poll_stop.set()
                        self._finalize_adaptive_row(
                            adaptive_runtime, row, idx, measurement_mode, sequence_result,
                            recommendation=adaptive_recommendation,
                        )
                        self._postprocess_csv_measurement(
                            row=row,
                            row_index=idx + 1,
                            measurement_mode=measurement_mode,
                            sequence_result=sequence_result,
                            row_output_dir=row_output_dir,
                        )
                        self._log("  Measurement completed")
                        self._queue_monitor_event('row_done', label=label)

                except Exception as exc:
                    error_text = str(exc)
                    is_contact_error = (
                        'contact' in error_text.lower()
                        or 'autocontactz' in error_text.lower()
                    )
                    self._log(f"  [ERROR] {traceback.format_exc()}")
                    self._queue_monitor_event('error', label=label, message='Measurement step failed')
                    if is_contact_error and self._run_contact_fail_policy_var.get() == 'skip_row':
                        self._log("  Contact failure policy is skip_row; continuing to next row.")
                        continue
                    run_failed = True
                    self.stop_flag.set()
                    self._log("  Stopping run because row failed.")
                    break
        finally:
            if temp_safety_stop is not None:
                temp_safety_stop.set()
            self._active_temperature_target_c = None
            self._active_run_result_root = None
            refreshed = self._backfill_adaptive_summary(adaptive_runtime)
            if refreshed:
                self._log(
                    f"[Adaptive] Backfilled {refreshed} runtime summary entr"
                    f"{'y' if refreshed == 1 else 'ies'} after delayed analysis completion"
                )
            if adaptive_runtime and adaptive_runtime.get('summary') is not None:
                adaptive_runtime['summary']['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                self._write_adaptive_summary(adaptive_runtime)
            self._shutdown_adaptive_runtime(adaptive_runtime)
            self._olecom_postprocess_hold = False
            self._start_olecom_postprocess_worker()

        if not run_failed and not self.stop_flag.is_set():
            self._retract_tip_after_successful_run()

        self._progress_var.set(100)
        self._progress_lbl.config(text=f"{total} / {total}")
        if run_failed:
            self._status_var.set("Error - stopped")
            self._log("\nRun stopped due to error.")
        elif self.stop_flag.is_set():
            self._status_var.set("Stopped")
            self._log("\nRun stopped.")
        else:
            self._status_var.set("Done")
            self._log("\nRun completed.")
        self.running = False
        self._btn_start.config(state='normal')
        self._btn_stop.config(state='disabled')
        self._queue_monitor_event('run_done')

    def _queue_monitor_event(self, event, **payload):
        if event == 'device_live':
            try:
                if self.monitor_queue.qsize() > MONITOR_DROP_LIVE_QUEUE_ABOVE:
                    return
            except Exception:
                pass
        self.monitor_queue.put((event, payload))

    def _monitor_reset(self):
        self._monitor_label = ''
        self._monitor_step = 'Idle'
        self._monitor_dc_points = []
        self._monitor_eis_points = []
        self._monitor_eis_overlay_points = []
        self._monitor_dc_title = 'CA / CP Current Monitor'
        self._monitor_eis_title = 'Impedance Monitor'
        self._monitor_last_current = None
        self._monitor_last_voltage = None
        self._monitor_last_impedance = None
        self._monitor_live_values = None
        self._monitor_row_text = 'idle'
        self._monitor_dc_series = None
        self._monitor_eis_series = None
        self._monitor_live_elapsed_origin = None
        self._monitor_redraw_pending = False
        self._monitor_last_redraw_s = 0.0
        if hasattr(self, '_monitor_run_var'):
            self._monitor_run_var.set('Run: idle')
            self._monitor_step_var.set('Step: idle')
            self._monitor_dc_var.set('Current: -')
            self._monitor_eis_var.set('Impedance: -')
        if hasattr(self, '_monitor_recommendation_var'):
            self._monitor_recommendation_var.set(
                self._manual_recommendation_var.get()
                if hasattr(self, '_manual_recommendation_var')
                else 'Recommendation: -'
            )
        self._redraw_monitor()

    def _poll_monitor(self):
        processed = 0
        try:
            while processed < MONITOR_QUEUE_BATCH_LIMIT:
                event, payload = self.monitor_queue.get_nowait()
                try:
                    self._apply_monitor_event(event, payload)
                except Exception as exc:
                    self._log(f"[MONITOR] Failed to apply event '{event}': {exc}")
                processed += 1
        except queue.Empty:
            pass
        next_delay_ms = 20 if processed >= MONITOR_QUEUE_BATCH_LIMIT else 100
        self.after(next_delay_ms, self._poll_monitor)

    def _open_image_monitor_camera(self):
        if cv2 is None:
            _vision_unavailable()
        backend_name = (self._image_backend_var.get() or 'dshow').lower()
        backend_code = {
            'dshow': cv2.CAP_DSHOW,
            'any': cv2.CAP_ANY,
            'msmf': cv2.CAP_MSMF,
        }.get(backend_name, cv2.CAP_DSHOW)
        index = int((self._image_index_var.get() or '0').strip())
        cap = cv2.VideoCapture(index, backend_code)
        if not cap.isOpened():
            raise RuntimeError(f'Could not open camera index={index} backend={backend_name}')
        time.sleep(0.3)
        return cap, backend_name, index

    def _image_monitor_start(self):
        try:
            self._image_monitor_stop()
            self._image_monitor_cap, backend_name, index = self._open_image_monitor_camera()
            self._image_monitor_running = True
            self._image_monitor_frame_idx = 0
            self._image_monitor_last_overlay_bgr = None
            self._image_monitor_last_frame_rgb = None
            self._image_monitor_last_frame_time_s = None
            self._image_seed_map = None
            self._image_seed_reference_rgb = None
            self._image_tracking_map = None
            self._image_status_var.set(f'Camera running: {backend_name}:{index}')
            self._image_detection_var.set('Electrodes: detecting...')
            self.after(50, self._image_monitor_tick)
        except Exception as exc:
            self._image_monitor_running = False
            self._image_status_var.set(f'Camera start failed: {exc}')

    def _image_monitor_stop(self):
        self._image_monitor_running = False
        if self._image_monitor_cap is not None:
            try:
                self._image_monitor_cap.release()
            except Exception:
                pass
            self._image_monitor_cap = None
        self._image_monitor_last_overlay_bgr = None
        self._image_monitor_last_frame_rgb = None
        self._image_monitor_last_frame_time_s = None
        self._image_seed_map = None
        self._image_seed_reference_rgb = None
        self._image_tracking_map = None
        self._image_pending_roi_verification = None
        self._image_pending_roi_seed_promotion = False
        self._image_move_requires_review_after_weak_roi = False
        self._image_allow_one_safe_move_after_weak_roi_override = False
        self._image_allow_stale_high_override_var.set(False)
        self._image_last_roi_verify_result = None
        self._image_selected_target = None
        self._image_render_shape = None
        self._image_render_size = None
        self._image_render_offset = (0, 0)
        if hasattr(self, '_image_monitor_label'):
            self._image_monitor_label.configure(
                image='',
                text='Camera preview will appear here',
                fg='white',
                bg='black',
            )
        if hasattr(self, '_image_status_var'):
            self._image_status_var.set('Camera idle')
        if hasattr(self, '_image_detection_var'):
            self._image_detection_var.set('Electrodes: -')
        if hasattr(self, '_image_target_status_var'):
            self._image_target_status_var.set('Target: not locked')
        if hasattr(self, '_image_roi_verify_status_var'):
            self._image_roi_verify_status_var.set('ROI verify: idle')
        self._image_refresh_move_gate_status()

    def _image_monitor_tick(self):
        if not self._image_monitor_running or self._image_monitor_cap is None:
            return

        ok, frame_bgr = self._image_monitor_cap.read()
        if not ok or frame_bgr is None:
            self._image_status_var.set('Camera read failed')
            self.after(200, self._image_monitor_tick)
            return

        self._image_monitor_frame_idx += 1
        display_bgr = frame_bgr.copy()
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        self._image_monitor_last_frame_rgb = frame_rgb.copy()
        self._image_monitor_last_frame_time_s = float(time.time())
        self._image_process_pending_roi_verification(frame_rgb)
        detector_name = (self._image_detector_var.get() or 'live').lower()
        expected_count = self._parse_image_expected_count()

        if (
            self._image_monitor_last_overlay_bgr is None
            or self._image_monitor_frame_idx % max(self._image_monitor_detect_every, 1) == 1
        ):
            try:
                if (
                    detector_name == 'live'
                    and self._image_seed_map is not None
                    and not self._image_seed_map.empty
                    and self._image_seed_reference_rgb is not None
                ):
                    transform, cc = compute_ecc_affine_registration(
                        self._image_seed_reference_rgb,
                        frame_rgb,
                    )
                    projected = transform_detection_table(
                        self._image_seed_map,
                        transform,
                        source_tag='frozen_seed_ecc_projection',
                    )
                    overlay_detections = refine_circular_electrode_map_rgb(
                        frame_rgb,
                        projected,
                        search_radius_px=32.0,
                        radius_tolerance=0.35,
                    )
                    raw_count = len(self._image_seed_map)
                    source_suffix = f' | frozen seed, ECC={cc:.3f}'
                elif (
                    detector_name == 'live'
                    and self._image_markup_map is not None
                    and not self._image_markup_map.empty
                    and self._image_markup_reference_rgb is not None
                ):
                    seed = scale_detection_table(
                        self._image_markup_map,
                        source_shape=self._image_markup_reference_rgb.shape[:2],
                        target_shape=frame_rgb.shape[:2],
                    )
                    transform, cc = compute_ecc_affine_registration(
                        self._image_markup_reference_rgb,
                        frame_rgb,
                    )
                    projected = transform_detection_table(
                        seed,
                        transform,
                        source_tag='markup_ecc_projection',
                    )
                    overlay_detections = refine_circular_electrode_map_rgb(
                        frame_rgb,
                        projected,
                        search_radius_px=32.0,
                        radius_tolerance=0.35,
                    )
                    raw_count = len(self._image_markup_map)
                    source_suffix = f' | markup seed, ECC={cc:.3f}'
                elif detector_name == 'live':
                    detections = detect_live_microscope_electrode_map_rgb(
                        frame_rgb,
                        sample_side_mm=10.0,
                        size_group='all',
                    )
                    overlay_detections = filter_live_overlay_detections(
                        detections,
                        max_candidates=expected_count,
                    )
                    raw_count = len(detections)
                    source_suffix = ''
                elif detector_name == 'reference':
                    detections = detect_reference_microscope_electrode_map_rgb(
                        frame_rgb,
                        sample_side_mm=10.0,
                        size_group='all',
                    )
                    overlay_detections = detections
                    raw_count = len(detections)
                    source_suffix = ' | reference preset'
                else:
                    detections = detect_electrode_map_rgb(
                        frame_rgb,
                        sample_side_mm=10.0,
                        size_group='all',
                        min_radius_px=6,
                        max_radius_px=80,
                    )
                    overlay_detections = detections
                    raw_count = len(detections)
                    source_suffix = ''
                annotated_rgb = annotate_detections(
                    frame_rgb,
                    overlay_detections,
                    annotate_labels=(detector_name != 'live'),
                )
                display_bgr = cv2.cvtColor(annotated_rgb, cv2.COLOR_RGB2BGR)
                self._image_monitor_last_overlay_bgr = display_bgr.copy()
                self._image_tracking_map = overlay_detections.copy()
                self._image_update_selected_target_from_overlay()
                expected_suffix = (
                    f' | target≈{expected_count}'
                    if expected_count is not None else ''
                )
                self._image_detection_var.set(
                    f'Electrodes: {len(overlay_detections)} primary candidates from {raw_count} raw circles ({detector_name} detector){expected_suffix}{source_suffix}'
                )
                if detector_name == 'live':
                    if self._image_seed_map is not None and not self._image_seed_map.empty:
                        self._image_hint_var.set(
                            'Hint: frozen-seed tracking is active. The current live frame and overlay were locked as the tracking seed, so later frames are being aligned against that seed.'
                        )
                    elif self._image_markup_map is not None and not self._image_markup_map.empty:
                        self._image_hint_var.set(
                            'Hint: markup-guided tracking is active. The red-circle markup is used only as the initial visible-electrode seed; later frames are tracked from that seed.'
                        )
                    else:
                        self._image_hint_var.set(
                            'Hint: live view shows conservative primary candidates only. If you know roughly how many electrodes should be visible, fill in Expected visible electrodes. If the overlay still looks wrong, use a pre-shot microscope image or attach the design image for assisted setup.'
                        )
                self._image_apply_pending_roi_seed_promotion()
            except Exception as exc:
                self._image_monitor_last_overlay_bgr = None
                self._image_tracking_map = None
                self._image_detection_var.set(f'Electrodes: detection failed ({exc})')
        elif detector_name == 'live' and self._image_tracking_map is not None and not self._image_tracking_map.empty:
            try:
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                tracked = refine_circular_electrode_map_rgb(
                    frame_rgb,
                    self._image_tracking_map,
                    search_radius_px=28.0,
                    radius_tolerance=0.30,
                )
                annotated_rgb = annotate_detections(
                    frame_rgb,
                    tracked,
                    annotate_labels=False,
                )
                display_bgr = cv2.cvtColor(annotated_rgb, cv2.COLOR_RGB2BGR)
                self._image_monitor_last_overlay_bgr = display_bgr.copy()
                self._image_tracking_map = tracked.copy()
                self._image_update_selected_target_from_overlay()
                self._image_detection_var.set(
                    f'Electrodes: tracking {len(tracked)} candidates from prior live frame'
                )
                self._image_apply_pending_roi_seed_promotion()
            except Exception:
                if self._image_monitor_last_overlay_bgr is not None:
                    display_bgr = self._image_monitor_last_overlay_bgr.copy()
        elif self._image_monitor_last_overlay_bgr is not None:
            display_bgr = self._image_monitor_last_overlay_bgr.copy()

        self._render_image_monitor_frame(display_bgr)
        self.after(120, self._image_monitor_tick)

    def _render_image_monitor_frame(self, frame_bgr):
        if frame_bgr is None or not hasattr(self, '_image_monitor_label'):
            return
        if cv2 is None or Image is None or ImageTk is None:
            _vision_unavailable()
        frame_bgr = self._image_draw_selected_target(frame_bgr)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        self._image_render_shape = frame_rgb.shape[:2]
        image = Image.fromarray(frame_rgb)
        max_w = max(self._image_monitor_label.winfo_width(), 640)
        max_h = max(self._image_monitor_label.winfo_height(), 480)
        image.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        self._image_render_size = image.size
        self._image_render_offset = (
            max((max_w - image.size[0]) // 2, 0),
            max((max_h - image.size[1]) // 2, 0),
        )
        photo = ImageTk.PhotoImage(image)
        self._image_monitor_photo = photo
        self._image_monitor_label.configure(image=photo, text='')

    def _parse_image_expected_count(self):
        raw = (self._image_expected_count_var.get() or '').strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except Exception:
            return None
        if value <= 0:
            return None
        return value

    def _image_browse_design(self):
        path = filedialog.askopenfilename(
            title='Select design image',
            filetypes=[
                ('Image files', '*.png *.jpg *.jpeg *.bmp *.tif *.tiff'),
                ('All files', '*.*'),
            ],
        )
        if path:
            self._image_design_path_var.set(path)

    def _image_browse_markup(self):
        path = filedialog.askopenfilename(
            title='Select markup image',
            filetypes=[
                ('Image files', '*.png *.jpg *.jpeg *.bmp *.tif *.tiff'),
                ('All files', '*.*'),
            ],
        )
        if path:
            self._image_markup_path_var.set(path)

    def _image_load_design(self):
        path = (self._image_design_path_var.get() or '').strip()
        if not path:
            self._image_design_map = None
            self._image_reset_stale_high_override_for_context_change()
            self._image_design_status_var.set('Design: not loaded')
            return
        try:
            detections = detect_electrode_map(
                path,
                sample_side_mm=10.0,
                size_group='all',
            )
            self._image_design_map = detections
            self._image_reset_stale_high_override_for_context_change()
            self._image_design_status_var.set(
                f'Design: loaded {len(detections)} candidates from {os.path.basename(path)}'
            )
            self._image_hint_var.set(
                'Hint: when the live image is partial or ambiguous, keep the design loaded and use it as assisted context for later target-electrode setup.'
            )
        except Exception as exc:
            self._image_design_map = None
            self._image_reset_stale_high_override_for_context_change()
            self._image_design_status_var.set(f'Design load failed: {exc}')

    def _image_load_markup(self):
        path = (self._image_markup_path_var.get() or '').strip()
        if not path:
            self._image_markup_map = None
            self._image_markup_reference_rgb = None
            self._image_reset_stale_high_override_for_context_change()
            self._image_markup_status_var.set('Markup: choose an image file first')
            return
        try:
            if cv2 is None:
                _vision_unavailable()
            image_rgb = cv2.cvtColor(cv2.imread(path, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            detections = detect_markup_electrode_map_rgb(
                image_rgb,
                sample_side_mm=10.0,
                size_group='all',
            )
            self._image_markup_map = detections
            self._image_markup_reference_rgb = strip_red_markup_from_rgb(image_rgb)
            self._image_reset_stale_high_override_for_context_change()
            rows = int(detections['row_index'].nunique()) if not detections.empty else 0
            cols = int(detections['col_index'].max()) if not detections.empty else 0
            self._image_markup_status_var.set(
                f'Markup: loaded {len(detections)} electrodes ({rows} rows, up to {cols} cols)'
            )
            self._image_hint_var.set(
                'Hint: markup image loaded. Use this as the trusted visible-electrode layout when live auto-detection looks unreliable.'
            )
        except Exception as exc:
            self._image_markup_map = None
            self._image_markup_reference_rgb = None
            self._image_reset_stale_high_override_for_context_change()
            self._image_markup_status_var.set(f'Markup load failed: {exc}')

    def _image_freeze_current_seed(self):
        if self._image_monitor_last_frame_rgb is None or self._image_tracking_map is None or self._image_tracking_map.empty:
            self._image_status_var.set('Freeze seed failed: no current live frame/overlay to lock')
            return
        self._image_promote_current_overlay_to_seed(
            status_text=None,
            hint_text='Hint: frozen-seed tracking is active. If the layout drifts too far or the wrong candidates were frozen, use Clear Seed and reacquire.',
        )

    def _image_clear_seed(self):
        self._image_seed_reference_rgb = None
        self._image_seed_map = None
        self._image_allow_stale_high_override_var.set(False)
        self._image_refresh_move_gate_status()
        self._image_status_var.set('Cleared frozen tracking seed')

    def _image_clear_stage_anchor(self):
        self._image_stage_anchor = None
        self._image_stage_anchor_status_var.set('Stage anchor: not set')
        self._image_allow_stale_high_override_var.set(False)
        self._image_refresh_move_gate_status()
        if self._image_selected_target is not None:
            self._image_update_target_status()

    def _image_reset_stale_high_override_for_context_change(self):
        self._image_allow_stale_high_override_var.set(False)
        self._image_refresh_move_gate_status()

    def _image_on_stage_projection_controls_changed(self):
        self._image_reset_stale_high_override_for_context_change()
        self._image_refresh_stage_projection_status()

    def _image_refresh_move_gate_status(self):
        risk_context = self._image_build_weak_roi_override_risk_context()
        high_risk_without_opt_in = bool(
            risk_context
            and risk_context.get('override_risk') == 'high'
            and not bool(self._image_allow_stale_high_override_var.get())
        )
        if self._image_move_requires_review_after_weak_roi:
            if self._image_allow_one_safe_move_after_weak_roi_override:
                status_text = 'Move gate: weak ROI blocked (one-shot override armed)'
                bg = '#fdf0e6'
                fg = CLR_ORANGE
                move_state = 'normal'
                override_state = 'disabled'
            else:
                status_text = 'Move gate: weak ROI blocked'
                bg = '#fdecea'
                fg = CLR_RED
                move_state = 'disabled'
                override_state = 'disabled' if high_risk_without_opt_in else 'normal'
        else:
            status_text = 'Move gate: clear'
            bg = '#eafaf1'
            fg = CLR_GREEN
            move_state = 'normal'
            override_state = 'disabled'
        if hasattr(self, '_image_move_gate_status_var'):
            self._image_move_gate_status_var.set(status_text)
        if self._image_move_gate_label is not None:
            self._image_move_gate_label.configure(bg=bg, fg=fg)
        if self._image_move_target_button is not None:
            self._image_move_target_button.configure(state=move_state)
        if self._image_override_weak_roi_button is not None:
            self._image_override_weak_roi_button.configure(state=override_state)

    def _image_build_weak_roi_override_risk_context(self):
        last_roi = self._image_last_roi_verify_result
        current_frame_time_s = getattr(self, '_image_monitor_last_frame_time_s', None)
        if not isinstance(last_roi, dict) or current_frame_time_s is None:
            return None
        score = last_roi.get('score')
        dx_px = last_roi.get('dx_px')
        dy_px = last_roi.get('dy_px')
        verify_time_s = last_roi.get('verify_time_s')
        if score is None or dx_px is None or dy_px is None or verify_time_s is None:
            return None
        try:
            frame_gap_s = abs(float(current_frame_time_s) - float(verify_time_s))
        except Exception:
            return None
        freshness_bucket = 'stale'
        freshness_hint = 'visual lock may be too old'
        override_risk = 'high'
        override_risk_hint = 'reacquire the seed unless the live overlay is unmistakably correct'
        recommended_action = 'refresh the seed first unless the current overlay is obviously correct'
        if frame_gap_s <= 10.0:
            freshness_bucket = 'fresh'
            freshness_hint = 'good for visual comparison'
            override_risk = 'low'
            override_risk_hint = 'live frame and weak verify are still closely aligned'
            recommended_action = 'override is reasonable if the locked target still looks right by eye'
        elif frame_gap_s <= 30.0:
            freshness_bucket = 'aging'
            freshness_hint = 'review with caution'
            override_risk = 'moderate'
            override_risk_hint = 'overlay may still be usable, but operator review matters'
            recommended_action = 'inspect the overlay carefully before overriding'
        return {
            'frame_gap_s': frame_gap_s,
            'freshness_bucket': freshness_bucket,
            'freshness_hint': freshness_hint,
            'override_risk': override_risk,
            'override_risk_hint': override_risk_hint,
            'recommended_action': recommended_action,
        }

    def _image_override_weak_roi_block(self):
        if not self._image_move_requires_review_after_weak_roi:
            self._manual_status_var.set('Weak ROI block is not active')
            return
        self._image_refresh_move_gate_status()
        move_gate_context = ''
        current_move_gate = str(self._image_move_gate_status_var.get() or '').strip()
        if current_move_gate:
            move_gate_context = f'Current move gate: {current_move_gate}.\n\n'
        live_frame_context = ''
        current_frame_time_s = getattr(self, '_image_monitor_last_frame_time_s', None)
        if current_frame_time_s is not None:
            try:
                live_frame_context = (
                    f"Current live frame: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(float(current_frame_time_s)))}.\n\n"
                )
            except Exception:
                live_frame_context = ''
        target_context = ''
        if self._image_selected_target is not None:
            target = self._image_selected_target
            target_lines = [
                f"Locked target: R{int(target['row_index'])}C{int(target['col_index'])} at "
                f"({float(target['x_px']):.1f}, {float(target['y_px']):.1f}) px."
            ]
            design_target = self._image_match_target_to_design()
            if design_target is not None:
                sample_x = _parse_optional_float(design_target.get('sample_x_mm'))
                sample_y = _parse_optional_float(design_target.get('sample_y_mm'))
                if sample_x is not None and sample_y is not None:
                    target_lines.append(
                        f"Design sample: ({float(sample_x):.2f}, {float(sample_y):.2f}) mm."
                    )
                projected_stage_xy = self._image_compute_stage_xy_for_design_target(design_target)
                if projected_stage_xy is not None:
                    target_lines.append(
                        f"Projected stage XY: ({float(projected_stage_xy[0]):.3f}, {float(projected_stage_xy[1]):.3f}) mm."
                    )
                    projection_source = self._image_stage_projection_provenance_label()
                    if projection_source:
                        target_lines.append(
                            f"Projection source: {projection_source}."
                        )
            target_context = '\n'.join(target_lines) + '\n\n'
        last_roi_context = ''
        frame_gap_text = ''
        high_risk_warning_text = ''
        high_risk_requires_second_confirm = False
        risk_context = self._image_build_weak_roi_override_risk_context()
        if (
            risk_context
            and risk_context.get('override_risk') == 'high'
            and not bool(self._image_allow_stale_high_override_var.get())
        ):
            self._manual_status_var.set(
                'Stale/high weak ROI override is disabled until Allow stale/high override is enabled'
            )
            self._image_hint_var.set(
                'Hint: stale/high weak ROI evidence is blocked by default. Enable Allow stale/high override only if the live overlay is clearly trustworthy by eye.'
            )
            self._image_refresh_move_gate_status()
            return
        last_roi = self._image_last_roi_verify_result
        if isinstance(last_roi, dict):
            score = last_roi.get('score')
            dx_px = last_roi.get('dx_px')
            dy_px = last_roi.get('dy_px')
            row_index = last_roi.get('row_index')
            col_index = last_roi.get('col_index')
            verify_time_s = last_roi.get('verify_time_s')
            if score is not None and dx_px is not None and dy_px is not None:
                label = 'the last weak ROI verify'
                if row_index is not None and col_index is not None:
                    label = f'the last weak ROI verify for R{int(row_index)}C{int(col_index)}'
                recency_text = ''
                absolute_time_text = ''
                if verify_time_s is not None:
                    try:
                        age_s = max(0.0, float(time.time()) - float(verify_time_s))
                        recency_text = f' ({age_s:.1f} s ago)'
                        absolute_time_text = (
                            f" at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(float(verify_time_s)))}"
                        )
                    except Exception:
                        recency_text = ''
                        absolute_time_text = ''
                if risk_context is not None:
                    try:
                        if risk_context['override_risk'] == 'high':
                            high_risk_requires_second_confirm = True
                            high_risk_warning_text = (
                                'WARNING: the current weak-ROI evidence is stale enough that refreshing the seed '
                                'is strongly preferred before overriding.\n\n'
                            )
                        frame_gap_text = (
                            f"Frame vs weak ROI verify gap: {float(risk_context['frame_gap_s']):.1f} s.\n"
                            f"Frame freshness bucket: {risk_context['freshness_bucket']} ({risk_context['freshness_hint']}).\n\n"
                            f"Override risk: {risk_context['override_risk']} ({risk_context['override_risk_hint']}).\n"
                            f"Recommended action: {risk_context['recommended_action']}.\n\n"
                        )
                    except Exception:
                        frame_gap_text = ''
                last_roi_context = (
                    f'Last weak ROI context: {label}{recency_text}{absolute_time_text} had '
                    f'score={float(score):.3f}, dx={float(dx_px):+.1f}, dy={float(dy_px):+.1f} px.\n\n'
                )
        confirmed = messagebox.askyesno(
            'Override weak ROI block?',
            'This allows exactly one guarded move even though the last ROI verification was weak.\n\n'
            f'{move_gate_context}'
            f'{live_frame_context}'
            f'{target_context}'
            f'{high_risk_warning_text}'
            f'{frame_gap_text}'
            f'{last_roi_context}'
            'Use this only if the current overlay still looks trustworthy by eye.\n\n'
            'Continue with a one-shot override?',
            parent=self,
        )
        if not confirmed:
            self._manual_status_var.set('Weak ROI block override cancelled')
            return
        if high_risk_requires_second_confirm:
            second_confirmed = messagebox.askyesno(
                'Confirm stale high-risk override',
                'The latest weak ROI evidence is still classified as stale/high risk.\n\n'
                'Only continue if the live overlay clearly looks correct and you deliberately want to spend '
                'the one-shot override on this move.\n\n'
                'Arm the one-shot override anyway?',
                parent=self,
            )
            if not second_confirmed:
                self._manual_status_var.set('Weak ROI block override cancelled at stale high-risk confirmation')
                return
        self._image_allow_one_safe_move_after_weak_roi_override = True
        self._image_refresh_move_gate_status()
        self._manual_status_var.set(
            'Weak ROI block overridden: the next safe move only is allowed without refreshing the seed'
        )
        self._image_hint_var.set(
            'Hint: weak ROI block was manually overridden for one safe move only. Use the next move carefully, or refresh the seed first if the overlay still looks unreliable.'
        )

    def _image_promote_current_overlay_to_seed(self, *, status_text=None, hint_text=None):
        if (
            self._image_monitor_last_frame_rgb is None
            or self._image_tracking_map is None
            or self._image_tracking_map.empty
        ):
            return False
        self._image_seed_reference_rgb = self._image_monitor_last_frame_rgb.copy()
        self._image_seed_map = self._image_tracking_map.copy()
        if status_text is None:
            status_text = f'Frozen current frame as tracking seed ({len(self._image_seed_map)} electrodes)'
        if hint_text is None:
            hint_text = (
                'Hint: frozen-seed tracking is active. If the layout drifts too far or the wrong candidates were frozen, use Clear Seed and reacquire.'
            )
        self._image_move_requires_review_after_weak_roi = False
        self._image_allow_one_safe_move_after_weak_roi_override = False
        self._image_allow_stale_high_override_var.set(False)
        self._image_refresh_move_gate_status()
        self._image_status_var.set(status_text)
        self._image_hint_var.set(hint_text)
        return True

    def _image_apply_pending_roi_seed_promotion(self):
        if not self._image_pending_roi_seed_promotion:
            return False
        promoted = self._image_promote_current_overlay_to_seed(
            status_text=(
                f'ROI-verified frame promoted to tracking seed '
                f'({0 if self._image_tracking_map is None else len(self._image_tracking_map)} electrodes)'
            ),
            hint_text=(
                'Hint: post-move ROI verification succeeded, so the current live overlay was promoted to the new frozen seed for downstream tracking.'
            ),
        )
        self._image_pending_roi_seed_promotion = False
        return promoted

    def _image_arm_post_move_roi_verification(self, reference_rgb, target, *, wait_frames=1):
        if reference_rgb is None or target is None:
            return False
        self._image_pending_roi_verification = {
            'reference_rgb': reference_rgb.copy(),
            'expected_center_x_px': float(target['x_px']),
            'expected_center_y_px': float(target['y_px']),
            'half_size_px': int(max(24, round(float(target.get('radius_px', 12.0)) * 2.0))),
            'search_radius_px': int(max(18, round(float(target.get('radius_px', 12.0)) * 2.0))),
            'wait_frames': int(max(wait_frames, 0)),
            'row_index': int(target.get('row_index', 0)),
            'col_index': int(target.get('col_index', 0)),
        }
        self._image_roi_verify_status_var.set(
            f"ROI verify: armed for R{int(target.get('row_index', 0))}C{int(target.get('col_index', 0))} after safe move"
        )
        return True

    def _image_process_pending_roi_verification(self, frame_rgb):
        pending = self._image_pending_roi_verification
        if pending is None or frame_rgb is None:
            return False
        wait_frames = int(pending.get('wait_frames', 0))
        if wait_frames > 0:
            pending['wait_frames'] = wait_frames - 1
            return False
        try:
            result = verify_roi_revisit(
                pending['reference_rgb'],
                frame_rgb,
                expected_center_x_px=float(pending['expected_center_x_px']),
                expected_center_y_px=float(pending['expected_center_y_px']),
                half_size_px=int(pending['half_size_px']),
                search_radius_px=int(pending['search_radius_px']),
            )
            ok = (
                float(result.score) >= 0.35
                and abs(float(result.dx_px)) <= float(pending['search_radius_px'])
                and abs(float(result.dy_px)) <= float(pending['search_radius_px'])
            )
            state = 'OK' if ok else 'weak'
            self._image_last_roi_verify_result = {
                'state': state,
                'row_index': int(pending['row_index']),
                'col_index': int(pending['col_index']),
                'score': float(result.score),
                'dx_px': float(result.dx_px),
                'dy_px': float(result.dy_px),
                'search_radius_px': int(pending['search_radius_px']),
                'verify_time_s': float(time.time()),
            }
            self._image_roi_verify_status_var.set(
                f"ROI verify: {state} for R{int(pending['row_index'])}C{int(pending['col_index'])} "
                f"(score={float(result.score):.3f}, dx={float(result.dx_px):+.1f}, dy={float(result.dy_px):+.1f} px)"
            )
            if ok:
                self._image_hint_var.set(
                    'Hint: post-move ROI verification matched the expected neighborhood, so the locked electrode remained visually consistent after motion.'
                )
                self._image_pending_roi_seed_promotion = True
                self._image_move_requires_review_after_weak_roi = False
                self._image_allow_one_safe_move_after_weak_roi_override = False
            else:
                self._image_move_requires_review_after_weak_roi = True
                self._image_allow_one_safe_move_after_weak_roi_override = False
                self._image_hint_var.set(
                    'Hint: post-move ROI verification was weak. Recheck the locked electrode overlay or reacquire a frozen seed before trusting the next projection; further safe moves are blocked until you refresh the seed.'
                )
            self._image_refresh_move_gate_status()
        except Exception as exc:
            self._image_last_roi_verify_result = None
            self._image_roi_verify_status_var.set(f'ROI verify failed: {exc}')
        finally:
            self._image_pending_roi_verification = None
        return True

    def _image_clear_stage_affine_calibration(self):
        self._image_stage_calibration_refs = []
        self._image_stage_affine_calibration = None
        self._image_stage_affine_status_var.set('Stage calibration: not solved')
        self._image_reset_stale_high_override_for_context_change()
        if self._image_selected_target is not None:
            self._image_update_target_status()

    def _image_stage_calibration_payload(self):
        refs = [
            {
                'sample_x_mm': float(ref.sample_x_mm),
                'sample_y_mm': float(ref.sample_y_mm),
                'stage_x_mm': float(ref.stage_x_mm),
                'stage_y_mm': float(ref.stage_y_mm),
            }
            for ref in self._image_stage_calibration_refs
        ]
        payload = {
            'refs': refs,
            'swap_xy': bool(self._image_stage_swap_xy_var.get()),
            'invert_x': bool(self._image_stage_invert_x_var.get()),
            'invert_y': bool(self._image_stage_invert_y_var.get()),
            'anchor': None,
        }
        if self._image_stage_anchor is not None:
            payload['anchor'] = {
                'stage_x_mm': float(self._image_stage_anchor['stage_x_mm']),
                'stage_y_mm': float(self._image_stage_anchor['stage_y_mm']),
                'sample_x_mm': float(self._image_stage_anchor['sample_x_mm']),
                'sample_y_mm': float(self._image_stage_anchor['sample_y_mm']),
                'row_index': int(self._image_stage_anchor['row_index']),
                'col_index': int(self._image_stage_anchor['col_index']),
            }
        return payload

    def _image_save_stage_affine_calibration(self, path=None):
        if path is None:
            path = filedialog.asksaveasfilename(
                title='Save stage calibration',
                defaultextension='.json',
                filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            )
        if not path:
            return False
        payload = self._image_stage_calibration_payload()
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._image_stage_affine_status_var.set(f'Stage calibration save failed: {exc}')
            return False
        self._image_stage_affine_status_var.set(
            f"Stage calibration: saved {len(self._image_stage_calibration_refs)} refs to {os.path.basename(path)}"
        )
        if self._image_selected_target is not None:
            self._image_update_target_status()
        return True

    def _image_load_stage_affine_calibration(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title='Load stage calibration',
                filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            )
        if not path:
            return False
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
            refs_payload = payload.get('refs', [])
            refs = [
                SampleStageReference(
                    sample_x_mm=float(item['sample_x_mm']),
                    sample_y_mm=float(item['sample_y_mm']),
                    stage_x_mm=float(item['stage_x_mm']),
                    stage_y_mm=float(item['stage_y_mm']),
                )
                for item in refs_payload
            ]
            swap_xy = bool(payload.get('swap_xy', False))
            invert_x = bool(payload.get('invert_x', False))
            invert_y = bool(payload.get('invert_y', False))
            anchor_payload = payload.get('anchor')
            if anchor_payload:
                anchor = {
                    'stage_x_mm': float(anchor_payload['stage_x_mm']),
                    'stage_y_mm': float(anchor_payload['stage_y_mm']),
                    'sample_x_mm': float(anchor_payload['sample_x_mm']),
                    'sample_y_mm': float(anchor_payload['sample_y_mm']),
                    'row_index': int(anchor_payload['row_index']),
                    'col_index': int(anchor_payload['col_index']),
                }
            else:
                anchor = None
            affine = None
            if len(refs) >= 3:
                affine = solve_sample_to_stage_affine_calibration(refs)
                loaded_status = (
                    f"Stage calibration: loaded affine from {len(refs)} refs ({os.path.basename(path)})"
                )
            else:
                loaded_status = (
                    f"Stage calibration: loaded {len(refs)} refs ({os.path.basename(path)})"
                )
            self._image_stage_calibration_refs = refs
            self._image_stage_affine_calibration = affine
            self._image_stage_swap_xy_var.set(swap_xy)
            self._image_stage_invert_x_var.set(invert_x)
            self._image_stage_invert_y_var.set(invert_y)
            self._image_stage_anchor = anchor
            self._image_reset_stale_high_override_for_context_change()
            self._image_refresh_stage_projection_status()
            self._image_stage_affine_status_var.set(loaded_status)
            if self._image_selected_target is not None:
                self._image_update_target_status()
        except Exception as exc:
            self._image_stage_affine_calibration = None
            self._image_stage_affine_status_var.set(f'Stage calibration load failed: {exc}')
            if self._image_selected_target is not None:
                self._image_update_target_status()
            return False
        return True

    def _image_transform_sample_delta_to_stage_delta(self, dx_mm, dy_mm):
        stage_dx = float(dx_mm)
        stage_dy = float(dy_mm)
        if self._image_stage_swap_xy_var.get():
            stage_dx, stage_dy = stage_dy, stage_dx
        if self._image_stage_invert_x_var.get():
            stage_dx = -stage_dx
        if self._image_stage_invert_y_var.get():
            stage_dy = -stage_dy
        return float(stage_dx), float(stage_dy)

    def _image_refresh_stage_projection_status(self):
        if self._image_stage_anchor is not None:
            anchor = self._image_stage_anchor
            mapping_terms = []
            if self._image_stage_swap_xy_var.get():
                mapping_terms.append('swap xy')
            if self._image_stage_invert_x_var.get():
                mapping_terms.append('invert x')
            if self._image_stage_invert_y_var.get():
                mapping_terms.append('invert y')
            mapping_text = ', '.join(mapping_terms) if mapping_terms else 'aligned axes'
            self._image_stage_anchor_status_var.set(
                'Stage anchor: '
                f"R{int(anchor['row_index'])}C{int(anchor['col_index'])} "
                f"sample ({float(anchor['sample_x_mm']):.2f}, {float(anchor['sample_y_mm']):.2f}) mm "
                f"-> stage ({float(anchor['stage_x_mm']):.3f}, {float(anchor['stage_y_mm']):.3f}) mm "
                f"[{mapping_text}]"
            )
        if self._image_stage_affine_calibration is not None:
            self._image_stage_affine_status_var.set(
                f'Stage calibration: affine solved from {len(self._image_stage_calibration_refs)} points'
            )
        if self._image_selected_target is not None:
            self._image_update_target_status()

    def _image_clear_target(self):
        self._image_selected_target = None
        self._image_selected_design_target = None
        self._image_allow_stale_high_override_var.set(False)
        self._image_target_status_var.set('Target: not locked')
        self._image_refresh_move_gate_status()

    def _image_get_current_stage_xy(self):
        stage_x = _parse_optional_float(self._manual_current['x'].get())
        stage_y = _parse_optional_float(self._manual_current['y'].get())
        if stage_x is None or stage_y is None:
            return None
        return float(stage_x), float(stage_y)

    def _image_compute_stage_xy_for_design_target(self, design_target):
        if design_target is None:
            return None
        sample_x = _parse_optional_float(design_target.get('sample_x_mm'))
        sample_y = _parse_optional_float(design_target.get('sample_y_mm'))
        if sample_x is None or sample_y is None:
            return None
        if self._image_stage_affine_calibration is not None:
            return sample_to_stage_xy(
                self._image_stage_affine_calibration,
                float(sample_x),
                float(sample_y),
            )
        if self._image_stage_anchor is None:
            return None
        anchor = self._image_stage_anchor
        stage_dx, stage_dy = self._image_transform_sample_delta_to_stage_delta(
            float(sample_x) - float(anchor['sample_x_mm']),
            float(sample_y) - float(anchor['sample_y_mm']),
        )
        return (
            float(anchor['stage_x_mm']) + float(stage_dx),
            float(anchor['stage_y_mm']) + float(stage_dy),
        )

    def _image_stage_projection_provenance_label(self):
        if self._image_stage_affine_calibration is not None:
            return 'affine calibration'
        if self._image_stage_anchor is not None:
            return 'stage anchor mapping'
        return None

    def _image_set_stage_anchor(self, design_target, stage_x, stage_y):
        sample_x = _parse_optional_float(design_target.get('sample_x_mm'))
        sample_y = _parse_optional_float(design_target.get('sample_y_mm'))
        if sample_x is None or sample_y is None:
            return False
        self._image_stage_anchor = {
            'stage_x_mm': float(stage_x),
            'stage_y_mm': float(stage_y),
            'sample_x_mm': float(sample_x),
            'sample_y_mm': float(sample_y),
            'row_index': int(design_target['row_index']),
            'col_index': int(design_target['col_index']),
        }
        self._image_refresh_stage_projection_status()
        if self._image_selected_target is not None:
            self._image_update_target_status()
        return True

    def _image_set_stage_anchor_from_current_xy(self):
        design_target = self._image_match_target_to_design()
        if design_target is None:
            self._image_stage_anchor_status_var.set(
                'Stage anchor failed: lock a target with a loaded design first'
            )
            return
        sample_x = _parse_optional_float(design_target.get('sample_x_mm'))
        sample_y = _parse_optional_float(design_target.get('sample_y_mm'))
        if sample_x is None or sample_y is None:
            self._image_stage_anchor_status_var.set(
                'Stage anchor failed: selected design target has no sample-mm coordinates'
            )
            return
        current_stage_xy = self._image_get_current_stage_xy()
        if current_stage_xy is None:
            self._image_stage_anchor_status_var.set(
                'Stage anchor failed: refresh current Motor X/Y first'
            )
            return
        stage_x, stage_y = current_stage_xy
        self._image_set_stage_anchor(design_target, stage_x, stage_y)
        self._image_reset_stale_high_override_for_context_change()

    def _image_add_stage_calibration_point_from_current_target(self):
        design_target = self._image_match_target_to_design()
        if design_target is None:
            self._image_stage_affine_status_var.set(
                'Stage calibration failed: lock a target with a loaded design first'
            )
            return
        sample_x = _parse_optional_float(design_target.get('sample_x_mm'))
        sample_y = _parse_optional_float(design_target.get('sample_y_mm'))
        if sample_x is None or sample_y is None:
            self._image_stage_affine_status_var.set(
                'Stage calibration failed: selected design target has no sample-mm coordinates'
            )
            return
        current_stage_xy = self._image_get_current_stage_xy()
        if current_stage_xy is None:
            self._image_stage_affine_status_var.set(
                'Stage calibration failed: refresh current Motor X/Y first'
            )
            return
        stage_x, stage_y = current_stage_xy
        row_index = int(design_target['row_index'])
        col_index = int(design_target['col_index'])
        new_ref = SampleStageReference(
            sample_x_mm=float(sample_x),
            sample_y_mm=float(sample_y),
            stage_x_mm=float(stage_x),
            stage_y_mm=float(stage_y),
        )
        replaced = False
        for idx, existing in enumerate(self._image_stage_calibration_refs):
            if (
                abs(existing.sample_x_mm - new_ref.sample_x_mm) < 1e-9
                and abs(existing.sample_y_mm - new_ref.sample_y_mm) < 1e-9
            ):
                self._image_stage_calibration_refs[idx] = new_ref
                replaced = True
                break
        if not replaced:
            self._image_stage_calibration_refs.append(new_ref)
        self._image_stage_affine_calibration = None
        action = 'updated' if replaced else 'added'
        self._image_stage_affine_status_var.set(
            f'Stage calibration: {action} R{row_index}C{col_index} '
            f'sample ({float(sample_x):.2f}, {float(sample_y):.2f}) -> '
            f'stage ({float(stage_x):.3f}, {float(stage_y):.3f}) mm '
            f'[{len(self._image_stage_calibration_refs)} refs]'
        )

    def _image_apply_projected_stage_xy_to_manual_target(self):
        design_target = self._image_match_target_to_design()
        if design_target is None:
            self._image_target_status_var.set(
                'Target apply failed: lock a target with a loaded design first'
            )
            return
        stage_xy = self._image_compute_stage_xy_for_design_target(design_target)
        if stage_xy is None:
            self._image_target_status_var.set(
                'Target apply failed: solve affine calibration or set a stage anchor first'
            )
            return
        stage_x, stage_y = stage_xy
        self._manual_target['x'].set(f'{float(stage_x):.3f}')
        self._manual_target['y'].set(f'{float(stage_y):.3f}')
        self._manual_status_var.set(
            f'Image Monitor projected stage XY copied to target: X={float(stage_x):.3f} mm, Y={float(stage_y):.3f} mm'
        )
        self._image_target_status_var.set(
            f"{self._image_target_status_var.get()} [copied to target X/Y]"
        )

    def _image_move_target_safe(self):
        if self.motor is None:
            self._manual_status_var.set('Motor controller is not connected')
            return
        if self._image_move_requires_review_after_weak_roi and not self._image_allow_one_safe_move_after_weak_roi_override:
            self._manual_status_var.set(
                'Safe move blocked: previous ROI verify was weak. Reacquire/freeze the current seed before moving again.'
            )
            self._image_hint_var.set(
                'Hint: a weak post-move ROI verify blocks the next safe move until you refresh the seed or obtain a successful ROI-verified promotion.'
            )
            return
        if self._image_allow_one_safe_move_after_weak_roi_override:
            self._image_allow_one_safe_move_after_weak_roi_override = False
            self._image_refresh_move_gate_status()
        reference_rgb = None if self._image_monitor_last_frame_rgb is None else self._image_monitor_last_frame_rgb.copy()
        selected_target = None if self._image_selected_target is None else self._image_selected_target.copy()
        design_target = self._image_match_target_to_design()
        if design_target is None:
            self._manual_status_var.set('Safe move failed: lock a target with a loaded design first')
            return
        stage_xy = self._image_compute_stage_xy_for_design_target(design_target)
        if stage_xy is None:
            self._manual_status_var.set(
                'Safe move failed: solve affine calibration or set a stage anchor first'
            )
            return
        current_positions = {}
        for axis in ('X', 'Y', 'Z'):
            try:
                current_positions[axis] = float(self.motor.get_position(axis))
            except Exception:
                current_positions[axis] = None
        current_z = current_positions.get('Z')
        self._manual_target['x'].set(f'{float(stage_xy[0]):.3f}')
        self._manual_target['y'].set(f'{float(stage_xy[1]):.3f}')
        if current_z is not None:
            self._manual_target['z'].set(f'{float(current_z):.3f}')
        self._manual_status_var.set(
            f"Moving to locked target via safe path: X={float(stage_xy[0]):.3f} mm, Y={float(stage_xy[1]):.3f} mm"
        )
        self.motor.move_xyz_safe(
            x_mm=float(stage_xy[0]),
            y_mm=float(stage_xy[1]),
            z_mm=None if current_z is None else float(current_z),
            current_positions=current_positions,
            log_fn=self._log,
        )
        self._image_set_stage_anchor(design_target, float(stage_xy[0]), float(stage_xy[1]))
        self._manual_refresh_state()
        self._manual_status_var.set(
            f"Safe target move complete: X={float(stage_xy[0]):.3f} mm, Y={float(stage_xy[1]):.3f} mm"
        )
        self._image_arm_post_move_roi_verification(reference_rgb, selected_target)
        self._image_allow_stale_high_override_var.set(False)
        self._image_refresh_move_gate_status()
        self._image_target_status_var.set(
            f"{self._image_target_status_var.get()} [safe move sent; anchor updated]"
        )

    def _image_solve_stage_affine_calibration(self):
        if len(self._image_stage_calibration_refs) < 3:
            self._image_stage_affine_calibration = None
            self._image_stage_affine_status_var.set(
                f'Stage calibration failed: need at least 3 reference points ({len(self._image_stage_calibration_refs)} present)'
            )
            if self._image_selected_target is not None:
                self._image_update_target_status()
            return
        try:
            self._image_stage_affine_calibration = solve_sample_to_stage_affine_calibration(
                self._image_stage_calibration_refs
            )
        except Exception as exc:
            self._image_stage_affine_calibration = None
            self._image_stage_affine_status_var.set(f'Stage calibration solve failed: {exc}')
            if self._image_selected_target is not None:
                self._image_update_target_status()
            return
        self._image_stage_affine_status_var.set(
            f'Stage calibration: affine solved from {len(self._image_stage_calibration_refs)} points'
        )
        self._image_reset_stale_high_override_for_context_change()
        if self._image_selected_target is not None:
            self._image_update_target_status()

    def _image_match_target_to_design(self):
        if (
            self._image_selected_target is None
            or self._image_design_map is None
            or self._image_design_map.empty
        ):
            self._image_selected_design_target = None
            return None

        target = self._image_selected_target
        design = self._image_design_map

        row_index = int(target.get('row_index', 0))
        col_index = int(target.get('col_index', 0))
        exact = design[
            (design['row_index'].astype(int) == row_index) &
            (design['col_index'].astype(int) == col_index)
        ]
        if not exact.empty:
            matched = exact.iloc[0].copy()
            self._image_selected_design_target = matched
            return matched

        def _normalized_position(df, row, col):
            max_row = max(float(df['row_index'].max()), 1.0)
            max_col = max(float(df['col_index'].max()), 1.0)
            row_norm = 0.0 if max_row <= 1.0 else (float(row) - 1.0) / (max_row - 1.0)
            col_norm = 0.0 if max_col <= 1.0 else (float(col) - 1.0) / (max_col - 1.0)
            return row_norm, col_norm

        target_row_norm, target_col_norm = _normalized_position(
            self._image_tracking_map,
            target.get('row_index', 1),
            target.get('col_index', 1),
        )
        design_norm = design[['row_index', 'col_index']].copy()
        max_design_row = max(float(design_norm['row_index'].max()), 1.0)
        max_design_col = max(float(design_norm['col_index'].max()), 1.0)
        design_norm['row_norm'] = (
            0.0 if max_design_row <= 1.0
            else (design_norm['row_index'].astype(float) - 1.0) / (max_design_row - 1.0)
        )
        design_norm['col_norm'] = (
            0.0 if max_design_col <= 1.0
            else (design_norm['col_index'].astype(float) - 1.0) / (max_design_col - 1.0)
        )
        scores = np.sqrt(
            (design_norm['row_norm'].to_numpy(dtype=float) - float(target_row_norm)) ** 2 +
            (design_norm['col_norm'].to_numpy(dtype=float) - float(target_col_norm)) ** 2
        )
        idx = int(np.argmin(scores))
        matched = design.iloc[idx].copy()
        self._image_selected_design_target = matched
        return matched

    def _image_update_target_status(self):
        if self._image_selected_target is None:
            self._image_target_status_var.set('Target: not locked')
            return
        target = self._image_selected_target
        message = (
            f"Target: locked R{int(target['row_index'])}C{int(target['col_index'])} "
            f"at ({target['x_px']:.1f}, {target['y_px']:.1f}) px"
        )
        design_target = self._image_match_target_to_design()
        if design_target is not None:
            message += (
                f" -> Design R{int(design_target['row_index'])}C{int(design_target['col_index'])}"
            )
            sample_x = design_target.get('sample_x_mm')
            sample_y = design_target.get('sample_y_mm')
            if sample_x is not None and sample_y is not None:
                try:
                    message += f" sample (~{float(sample_x):.2f}, {float(sample_y):.2f}) mm"
                except Exception:
                    pass
            stage_xy = self._image_compute_stage_xy_for_design_target(design_target)
            if stage_xy is not None:
                message += f" -> Stage (~{float(stage_xy[0]):.3f}, {float(stage_xy[1]):.3f}) mm"
        self._image_target_status_var.set(message)

    def _image_select_target_from_frame_xy(self, x_px, y_px):
        if self._image_tracking_map is None or self._image_tracking_map.empty:
            self._image_target_status_var.set('Target lock failed: no current electrode overlay')
            return False
        coords = self._image_tracking_map[['x_px', 'y_px']].to_numpy(dtype=float)
        deltas = coords - np.array([float(x_px), float(y_px)], dtype=float)
        dists = np.sqrt(np.sum(deltas * deltas, axis=1))
        idx = int(np.argmin(dists))
        target = self._image_tracking_map.iloc[idx].copy()
        self._image_selected_target = target
        self._image_allow_stale_high_override_var.set(False)
        self._image_refresh_move_gate_status()
        self._image_update_target_status()
        return True

    def _image_monitor_click_select_target(self, event):
        if self._image_render_shape is None or self._image_render_size is None:
            self._image_target_status_var.set('Target lock failed: no rendered frame yet')
            return
        render_w, render_h = self._image_render_size
        offset_x, offset_y = self._image_render_offset
        local_x = event.x - offset_x
        local_y = event.y - offset_y
        if local_x < 0 or local_y < 0 or local_x >= render_w or local_y >= render_h:
            return
        frame_h, frame_w = self._image_render_shape
        frame_x = float(local_x) * float(frame_w) / max(float(render_w), 1.0)
        frame_y = float(local_y) * float(frame_h) / max(float(render_h), 1.0)
        self._image_select_target_from_frame_xy(frame_x, frame_y)

    def _image_update_selected_target_from_overlay(self):
        if (
            self._image_selected_target is None
            or self._image_tracking_map is None
            or self._image_tracking_map.empty
        ):
            return
        coords = self._image_tracking_map[['x_px', 'y_px']].to_numpy(dtype=float)
        old_xy = np.array(
            [float(self._image_selected_target['x_px']), float(self._image_selected_target['y_px'])],
            dtype=float,
        )
        deltas = coords - old_xy
        dists = np.sqrt(np.sum(deltas * deltas, axis=1))
        idx = int(np.argmin(dists))
        target = self._image_tracking_map.iloc[idx].copy()
        self._image_selected_target = target
        self._image_update_target_status()

    def _image_draw_selected_target(self, frame_bgr):
        if self._image_selected_target is None:
            return frame_bgr
        canvas = frame_bgr.copy()
        x = int(round(float(self._image_selected_target['x_px'])))
        y = int(round(float(self._image_selected_target['y_px'])))
        r = int(round(float(self._image_selected_target.get('radius_px', 12.0))))
        cv2.circle(canvas, (x, y), max(r + 6, 10), (0, 255, 255), 3)
        cv2.drawMarker(
            canvas,
            (x, y),
            (255, 255, 0),
            markerType=cv2.MARKER_CROSS,
            markerSize=max(r + 12, 18),
            thickness=2,
        )
        return canvas

    def _apply_monitor_event(self, event, payload):
        label = payload.get('label')
        if label:
            self._monitor_label = label

        if event == 'row_start':
            self._monitor_dc_points = []
            self._monitor_eis_points = []
            self._monitor_eis_overlay_points = []
            self._monitor_dc_series = None
            self._monitor_eis_series = None
            self._monitor_live_elapsed_origin = None
            self._monitor_dc_axes = ('Time (s)', 'Current (A)')
            self._monitor_eis_axes = ('Re(Z) (Ohm)', '-Im(Z) (Ohm)')
            self._monitor_live_values = None
            self._monitor_step = 'Preparing row'
            self._monitor_row_text = (
                f"{payload.get('row_index', '?')} / {payload.get('total', '?')}"
            )
            if hasattr(self, '_monitor_recommendation_var'):
                self._monitor_recommendation_var.set(
                    'Recommendation: pending for current row...'
                )
        elif event == 'step':
            self._monitor_step = payload.get('message', payload.get('step', 'Running'))
        elif event == 'device_live':
            values = payload.get('values') or {}
            if isinstance(values, dict):
                self._monitor_live_values = values
                if 'ewe_v' in values:
                    self._monitor_last_voltage = float(values['ewe_v'])
                if 'current_a' in values:
                    self._monitor_last_current = float(values['current_a'])
                elapsed = values.get('elapsed_s')
                current = values.get('current_a')
                step_lower = str(self._monitor_step or '').lower()
                is_active_measurement = (
                    step_lower
                    and 'done' not in step_lower
                    and step_lower not in {'idle', 'row complete', 'run complete'}
                )
                if (
                    is_active_measurement
                    and
                    elapsed is not None and current is not None
                ):
                    if 'peis' in step_lower or 'eis' in step_lower:
                        live_series_name = 'device_live_current_peis'
                        live_title = 'PEIS Live Current Monitor'
                    elif 'scout' in step_lower:
                        live_series_name = 'device_live_current_scout'
                        live_title = 'dV Scout Live Current Monitor'
                    elif 'pre' in step_lower or 'ca' in step_lower:
                        live_series_name = 'device_live_current_ca'
                        live_title = 'Live CA Current Monitor'
                    else:
                        live_series_name = 'device_live_current'
                        live_title = 'Live Current Monitor'
                    if self._monitor_dc_series != live_series_name:
                        self._monitor_live_elapsed_origin = None
                    elapsed = float(elapsed)
                    if self._monitor_live_elapsed_origin is None:
                        self._monitor_live_elapsed_origin = elapsed
                    elapsed = max(0.0, elapsed - self._monitor_live_elapsed_origin)
                    self._monitor_dc_title = live_title
                    self._monitor_dc_axes = ('Time (s)', 'Current (A)')
                    self._monitor_dc_points = self._merge_monitor_points(
                        existing=self._monitor_dc_points,
                        data=np.array([[elapsed, float(current)]], dtype=float),
                        x_col=0,
                        y_col=1,
                        series_kind='dc',
                        series_name=live_series_name,
                        replace=False,
                        append_on_series_change=False,
                    )
        elif event == 'dc_data':
            data = self._normalize_monitor_matrix(payload.get('data'))
            if data.size:
                step_name = payload.get('step', self._monitor_step)
                append_from_existing = (
                    step_name.startswith('post_peis_ca_sequence')
                    and bool(self._monitor_dc_points)
                )
                self._monitor_dc_title = (
                    'Measurement Current Timeline'
                    if append_from_existing else
                    self._monitor_title_for_dc(step_name)
                )
                self._monitor_dc_axes = ('Time (s)', 'Current (A)')
                self._monitor_dc_points = self._merge_monitor_points(
                    existing=self._monitor_dc_points,
                    data=data,
                    x_col=0,
                    y_col=2,
                    series_kind='dc',
                    series_name=step_name,
                    replace=step_name.endswith('_done') and not append_from_existing,
                    append_on_series_change=append_from_existing,
                )
                self._monitor_last_voltage = float(data[-1, 1])
                self._monitor_last_current = float(data[-1, 2])
                self._monitor_step = step_name
        elif event == 'eis_data':
            data = self._normalize_monitor_matrix(payload.get('data'))
            if data.size:
                step_name = payload.get('step', self._monitor_step)
                self._monitor_eis_title = 'Impedance Monitor (Nyquist)'
                self._monitor_eis_axes = ('Re(Z) (Ohm)', '-Im(Z) (Ohm)')
                self._monitor_eis_points = self._merge_monitor_points(
                    existing=self._monitor_eis_points,
                    data=data,
                    x_col=1,
                    y_col=2,
                    series_kind='eis',
                    series_name=step_name,
                    replace=step_name.endswith('_done'),
                )
                self._monitor_last_impedance = (
                    float(data[-1, 1]), float(data[-1, 2]), float(data[-1, 0])
                )
                self._monitor_step = step_name
        elif event == 'row_done':
            self._monitor_step = 'Row complete'
        elif event == 'sequence_done':
            self._monitor_step = 'Measurement sequence complete'
        elif event == 'error':
            self._monitor_step = payload.get('message', 'Error')
        elif event == 'run_done':
            self._monitor_step = 'Run complete'

        self._refresh_monitor_labels()
        self._schedule_monitor_redraw()

    def _schedule_monitor_redraw(self, delay_ms=None):
        if getattr(self, '_monitor_redraw_pending', False):
            return
        if delay_ms is None:
            elapsed_ms = (
                time.time() - float(getattr(self, '_monitor_last_redraw_s', 0.0) or 0.0)
            ) * 1000.0
            delay_ms = max(0, int(MONITOR_REDRAW_INTERVAL_MS - elapsed_ms))
        self._monitor_redraw_pending = True
        self.after(int(delay_ms), self._redraw_monitor_from_schedule)

    def _redraw_monitor_from_schedule(self):
        self._monitor_redraw_pending = False
        self._redraw_monitor()

    def _normalize_monitor_matrix(self, data):
        if data is None:
            return np.empty((0, 0))
        try:
            arr = np.asarray(data, dtype=float)
        except Exception:
            return np.empty((0, 0))
        if arr.size == 0:
            return np.empty((0, 0))
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.ndim != 2:
            return np.empty((0, 0))
        return arr

    def _merge_monitor_points(self, existing, data, x_col, y_col,
                              series_kind, series_name, replace=False,
                              append_on_series_change=False):
        points = [(float(row[x_col]), float(row[y_col])) for row in data]
        if not points:
            return existing

        current_series = (
            self._monitor_dc_series if series_kind == 'dc'
            else self._monitor_eis_series
        )
        if replace or not existing:
            if series_kind == 'dc':
                self._monitor_dc_series = series_name
            else:
                self._monitor_eis_series = series_name
            return self._limit_monitor_points(points, series_kind)

        if current_series != series_name:
            if series_kind == 'dc' and append_on_series_change and existing:
                offset = existing[-1][0]
                shifted = [(pt[0] + offset, pt[1]) for pt in points]
                self._monitor_dc_series = series_name
                return self._limit_monitor_points(list(existing) + shifted, series_kind)
            if series_kind == 'dc':
                self._monitor_dc_series = series_name
            else:
                self._monitor_eis_series = series_name
            return self._limit_monitor_points(points, series_kind)

        merged = list(existing)
        if series_kind == 'dc':
            last_x = merged[-1][0]
            new_points = [pt for pt in points if pt[0] > last_x]
            if new_points:
                merged.extend(new_points)
                return self._limit_monitor_points(merged, series_kind)
        else:
            tail = merged[-len(points):] if len(merged) >= len(points) else merged
            if tail != points:
                merged.extend(points)
                return self._limit_monitor_points(merged, series_kind)

        if len(points) > len(existing):
            return self._limit_monitor_points(points, series_kind)
        return self._limit_monitor_points(merged, series_kind)

    def _limit_monitor_points(self, points, series_kind):
        max_points = (
            MONITOR_TIME_SERIES_MAX_POINTS
            if series_kind == 'dc'
            else MONITOR_NYQUIST_MAX_POINTS
        )
        if len(points) <= max_points:
            return points
        return self._plot_points_for_canvas(points, max_points=max_points)

    def _monitor_title_for_dc(self, step_name):
        if step_name.startswith('ca_hold'):
            return 'Pre-PEIS Hold Current'
        if step_name.startswith('post_peis_ca_sequence'):
            return 'Post-PEIS CA Current'
        return 'CA / CP Current Monitor'

    def _refresh_monitor_labels(self):
        run_label = self._monitor_label or 'idle'
        if self._monitor_row_text and self._monitor_row_text != 'idle':
            self._monitor_run_var.set(f"Run: {self._monitor_row_text}  |  {run_label}")
        else:
            self._monitor_run_var.set(f"Run: {run_label}")
        step_text = self._monitor_step
        live = self._monitor_live_values or {}
        live_extras = []
        if 'peis' in str(self._monitor_step).lower():
            freq = live.get('frequency_hz')
            if freq:
                live_extras.append(f"{float(freq):.4g} Hz")
        elapsed = live.get('elapsed_s')
        if elapsed is not None and str(self._monitor_step).lower() not in {'idle', 'row complete', 'run complete'}:
            live_extras.append(f"{float(elapsed):.1f}s")
        if live_extras:
            step_text = f"{step_text} | {' | '.join(live_extras)}"
        self._monitor_step_var.set(f"Step: {step_text}")
        if self._monitor_last_current is None:
            self._monitor_dc_var.set('Current: -')
        else:
            volts = self._monitor_last_voltage if self._monitor_last_voltage is not None else float('nan')
            self._monitor_dc_var.set(
                f"Current: {self._monitor_last_current:.4e} A at {volts:.4f} V"
            )
        if self._monitor_last_impedance is None:
            step_lower = str(self._monitor_step or '').lower()
            freq = live.get('frequency_hz')
            buffer_bytes = live.get('buffer_bytes')
            elapsed = live.get('elapsed_s')
            if freq and ('peis' in step_lower or 'eis' in step_lower):
                parts = [f"live freq {float(freq):.4g} Hz"]
                if elapsed is not None:
                    parts.append(f"t={float(elapsed):.1f}s")
                if buffer_bytes is not None:
                    parts.append(f"buffer={int(buffer_bytes)} B")
                self._monitor_eis_var.set(f"Impedance: {' | '.join(parts)}")
            elif step_lower not in {'idle', 'row complete', 'run complete'}:
                self._monitor_eis_var.set('Impedance: waiting for PEIS')
            else:
                self._monitor_eis_var.set('Impedance: -')
        else:
            rez, imz, freq = self._monitor_last_impedance
            self._monitor_eis_var.set(
                f"Impedance: Re={rez:.4g} Ohm, -Im={imz:.4g} Ohm at {freq:.4g} Hz"
            )

    def _redraw_monitor(self):
        if hasattr(self, '_dc_canvas'):
            dc_points = self._monitor_dc_points
            if (
                self._monitor_dc_series == 'device_live_current'
                and dc_points
                and dc_points[0][0] > 0
            ):
                origin = dc_points[0][0]
                dc_points = [(x - origin, y) for x, y in dc_points]
            self._draw_line_plot(
                self._dc_canvas,
                dc_points,
                title=self._monitor_dc_title,
                x_label=self._monitor_dc_axes[0],
                y_label=self._monitor_dc_axes[1],
                line_color=CLR_BLUE,
                max_points=MONITOR_TIME_SERIES_MAX_POINTS,
            )
        if hasattr(self, '_eis_canvas'):
            self._draw_line_plot(
                self._eis_canvas,
                self._monitor_eis_points,
                title=self._monitor_eis_title,
                x_label=self._monitor_eis_axes[0],
                y_label=self._monitor_eis_axes[1],
                line_color=CLR_GOLD,
                overlay_points=self._monitor_eis_overlay_points,
                overlay_color=CLR_BLUE,
                legend_entries=(
                    [('PEIS', CLR_GOLD)] +
                    ([('CP FFT recovered', CLR_BLUE)] if self._monitor_eis_overlay_points else [])
                ),
                max_points=MONITOR_NYQUIST_MAX_POINTS,
                empty_text=self._monitor_eis_placeholder_text(),
            )

    def _monitor_eis_placeholder_text(self):
        live = self._monitor_live_values or {}
        step = str(self._monitor_step or '').lower()
        if 'peis' in step:
            buffer_bytes = live.get('buffer_bytes')
            freq = live.get('frequency_hz')
            if buffer_bytes:
                freq_text = f" around {float(freq):.3g} Hz" if freq else ""
                return (
                    f"BioLogic is buffering PEIS data{freq_text}.\n"
                    "Nyquist points will appear when the next chunk completes."
                )
            return "PEIS is running.\nWaiting for the first Nyquist chunk..."
        return 'Waiting for live data...'

    def _compute_plot_bounds(self, points, x_label, y_label):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        if min_x == max_x:
            min_x -= 1.0
            max_x += 1.0
        if min_y == max_y:
            delta = abs(min_y) * 0.1 if min_y else 1.0
            min_y -= delta
            max_y += delta

        x_span = max_x - min_x
        y_span = max_y - min_y
        x_pad = x_span * 0.05
        y_pad = y_span * 0.08
        min_x -= x_pad
        max_x += x_pad
        min_y -= y_pad
        max_y += y_pad

        if x_label.lower().startswith('time') and min(xs) >= 0:
            min_x = 0.0

        return min_x, max_x, min_y, max_y

    def _sanitize_plot_points(self, points):
        sanitized = []
        dropped = 0
        for point in points or []:
            try:
                x_val = float(point[0])
                y_val = float(point[1])
            except Exception:
                dropped += 1
                continue
            if not (np.isfinite(x_val) and np.isfinite(y_val)):
                dropped += 1
                continue
            if max(abs(x_val), abs(y_val)) > MONITOR_MAX_ABS_PLOT_VALUE:
                dropped += 1
                continue
            sanitized.append((x_val, y_val))
        return sanitized, dropped

    def _plot_points_for_canvas(self, points, max_points=800):
        if len(points) <= max_points:
            return list(points)
        if max_points < 2:
            return [points[0]]
        last_index = len(points) - 1
        step = last_index / float(max_points - 1)
        sampled = []
        seen = set()
        for idx in range(max_points):
            point_index = int(round(idx * step))
            point_index = min(last_index, point_index)
            if point_index in seen:
                continue
            seen.add(point_index)
            sampled.append(points[point_index])
        if sampled[-1] != points[-1]:
            sampled[-1] = points[-1]
        return sampled

    def _draw_line_plot(self, canvas, points, title, x_label, y_label, line_color,
                        empty_text='Waiting for live data...', overlay_points=None,
                        overlay_color=CLR_BLUE, legend_entries=None,
                        max_points=800):
        canvas.delete('all')
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 240)
        pad_l, pad_r, pad_t, pad_b = 58, 18, 24, 42
        x0, y0 = pad_l, height - pad_b
        x1, y1 = width - pad_r, pad_t

        canvas.create_rectangle(x0, y1, x1, y0, outline='#b0bec5')
        canvas.create_text(x0, 8, text=title, anchor='nw', fill='#37474f',
                           font=('Segoe UI', 10, 'bold'))
        canvas.create_text((x0 + x1) / 2, height - 14, text=x_label,
                           fill='#546e7a', font=('Segoe UI', 9))
        canvas.create_text(14, (y0 + y1) / 2, text=y_label,
                           fill='#546e7a', font=('Segoe UI', 9), angle=90)

        points, dropped_points = self._sanitize_plot_points(points)
        overlay_points, dropped_overlay = self._sanitize_plot_points(overlay_points)
        if dropped_points or dropped_overlay:
            dropped_total = dropped_points + dropped_overlay
            empty_text = (
                f"{empty_text}\nFiltered {dropped_total} invalid plot point"
                f"{'s' if dropped_total != 1 else ''}."
            )
        if len(points) == 0 and len(overlay_points) == 0:
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                               text=empty_text,
                               fill='#90a4ae', font=('Segoe UI', 10))
            return

        plotted_points = self._plot_points_for_canvas(points, max_points=max_points)
        plotted_overlay = self._plot_points_for_canvas(overlay_points, max_points=max_points)

        all_points = list(plotted_points) + list(plotted_overlay)
        min_x, max_x, min_y, max_y = self._compute_plot_bounds(all_points, x_label, y_label)

        def map_x(val):
            return x0 + (val - min_x) / (max_x - min_x) * (x1 - x0)

        def map_y(val):
            return y0 - (val - min_y) / (max_y - min_y) * (y0 - y1)

        for frac in (0.0, 0.5, 1.0):
            gx = x0 + frac * (x1 - x0)
            gy = y0 - frac * (y0 - y1)
            canvas.create_line(gx, y1, gx, y0, fill='#eceff1')
            canvas.create_line(x0, gy, x1, gy, fill='#eceff1')

        canvas.create_text(x0, y0 + 14, text=f"{min_x:.3g}", anchor='w',
                           fill='#607d8b', font=('Courier New', 8))
        canvas.create_text(x1, y0 + 14, text=f"{max_x:.3g}", anchor='e',
                           fill='#607d8b', font=('Courier New', 8))
        canvas.create_text(x0 - 6, y0, text=f"{min_y:.3g}", anchor='e',
                           fill='#607d8b', font=('Courier New', 8))
        canvas.create_text(x0 - 6, y1, text=f"{max_y:.3g}", anchor='e',
                           fill='#607d8b', font=('Courier New', 8))

        def _draw_series(series_points, color):
            if not series_points:
                return
            coords = []
            for px, py in series_points:
                coords.extend((map_x(px), map_y(py)))
            if len(series_points) >= 2:
                canvas.create_line(*coords, fill=color, width=2, smooth=False)
            canvas.create_oval(coords[-2] - 3, coords[-1] - 3,
                               coords[-2] + 3, coords[-1] + 3,
                               fill=color, outline=color)

        _draw_series(plotted_points, line_color)
        _draw_series(plotted_overlay, overlay_color)

        if legend_entries:
            legend_x = x1 - 8
            legend_y = y1 + 10
            for idx, (label, color) in enumerate(legend_entries):
                y = legend_y + idx * 16
                canvas.create_line(legend_x - 70, y, legend_x - 54, y, fill=color, width=3)
                canvas.create_text(legend_x - 50, y, text=label, anchor='w',
                                   fill='#455a64', font=('Segoe UI', 8))

    # ══════════════════════════════════════════════════════════════════════
    # Log
    # ══════════════════════════════════════════════════════════════════════
    def _log(self, msg: str):
        ts  = time.strftime('%H:%M:%S')
        self.log_queue.put(f"[{ts}] {msg}\n")

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state='normal')
                self.log_text.insert('end', msg)
                self.log_text.see('end')
                self.log_text.config(state='disabled')
        except queue.Empty:
            pass
        self.after(200, self._poll_log)

    def _clear_log(self):
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

    def on_close(self):
        if self.running:
            if not messagebox.askyesno("종료", "측정 중입니다. 강제 종료하시겠습니까?"):
                return
            self.stop_flag.set()
        try:
            self._disconnect_all()
        finally:
            try:
                self.quit()
            except Exception:
                pass
            try:
                self.destroy()
            except Exception:
                pass
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)


if __name__ == '__main__':
    app = MicroprobGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
