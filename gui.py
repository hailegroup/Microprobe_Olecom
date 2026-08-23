# -*- coding: utf-8 -*-
"""
Microprobe Automated Measurement — GUI
Run: python gui.py
"""

import faulthandler
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import threading
import traceback
import queue
import socket
import json
import math
import csv

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
BIOLOGIC_OLECOM_CA_BANDWIDTH = getattr(_config, 'BIOLOGIC_OLECOM_CA_BANDWIDTH', 4)
BIOLOGIC_OLECOM_CA_I_RANGE = getattr(_config, 'BIOLOGIC_OLECOM_CA_I_RANGE', 'Auto')
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
        detect_live_microscope_electrode_map_rgb,
        filter_live_overlay_detections,
        refine_circular_electrode_map_rgb,
    )
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.electrode_mapper: {exc}")
    annotate_detections = _vision_unavailable
    detect_live_microscope_electrode_map_rgb = _vision_unavailable
    filter_live_overlay_detections = _vision_unavailable
    refine_circular_electrode_map_rgb = _vision_unavailable

try:
    from vision.circle_detector import load_hough_params
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.circle_detector: {exc}")
    load_hough_params = _vision_unavailable

try:
    from vision.electrode_drift import detect_and_refit_frame
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.electrode_drift: {exc}")
    detect_and_refit_frame = _vision_unavailable

try:
    from vision.probe_detector import ProbeDetector, ProbeTip, load_probe_params
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.probe_detector: {exc}")

    class ProbeDetector:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    class ProbeTip:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    load_probe_params = _vision_unavailable

try:
    from vision.tuning_gui.tuning_gui import CircleTuningWindow
    from vision.tuning_gui.probe_gui import ProbeTuningWindow
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.tuning_gui: {exc}")

    class CircleTuningWindow:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    class ProbeTuningWindow:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

try:
    from vision.layout_alignment import (
        Circle,
        LayoutModel,
        apply_manual_nudge,
        estimate_proj_radius,
        fit_layout_affine,
        project_all_circles,
    )
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.layout_alignment: {exc}")

    class Circle:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    class LayoutModel:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    apply_manual_nudge = _vision_unavailable
    estimate_proj_radius = _vision_unavailable
    fit_layout_affine = _vision_unavailable
    project_all_circles = _vision_unavailable

try:
    from vision.dxf_layout import (
        extract_circles_from_dxf,
        shift_to_origin,
        warn_if_spacing_implausible,
        write_json as write_layout_json,
    )
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.dxf_layout: {exc}")
    extract_circles_from_dxf = _vision_unavailable
    shift_to_origin = _vision_unavailable
    warn_if_spacing_implausible = _vision_unavailable
    write_layout_json = _vision_unavailable

try:
    from vision_stage_mapper import (
        PixelStageReference,
        ProbeXYBiasCalibration,
        ProbeZParallaxCalibration,
        correct_pixel_for_xy_bias,
        correct_pixel_for_z_parallax,
        parallax_within_trusted_range,
        solve_stage_affine_calibration,
        pixel_to_stage_xy,
        stage_to_pixel_xy,
    )
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision_stage_mapper: {exc}")

    class PixelStageReference:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    class ProbeXYBiasCalibration:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    class ProbeZParallaxCalibration:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

    correct_pixel_for_xy_bias = _vision_unavailable
    correct_pixel_for_z_parallax = _vision_unavailable
    parallax_within_trusted_range = _vision_unavailable
    solve_stage_affine_calibration = _vision_unavailable
    pixel_to_stage_xy = _vision_unavailable
    stage_to_pixel_xy = _vision_unavailable

try:
    from vision.electrode_z_seed import ElectrodeZCalibrationStore
except Exception as exc:
    VISION_IMPORT_ERRORS.append(f"vision.electrode_z_seed: {exc}")

    class ElectrodeZCalibrationStore:
        def __init__(self, *args, **kwargs):
            _vision_unavailable(*args, **kwargs)

        @classmethod
        def load(cls, *args, **kwargs):
            return cls()

VISION_AVAILABLE = (
    cv2 is not None
    and Image is not None
    and ImageTk is not None
    # vision.dxf_layout depends on the separate optional `ezdxf` package and
    # only backs the DXF-layout-alignment workflow; its absence should not
    # disable core camera/electrode/probe tracking.
    and not any(
        err.startswith("vision.") and not err.startswith("vision.dxf_layout")
        for err in VISION_IMPORT_ERRORS
    )
)
VISION_DXF_LAYOUT_AVAILABLE = not any(err.startswith("vision.dxf_layout") for err in VISION_IMPORT_ERRORS)

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
# Cosmetic-only radius for DXF-layout-alignment click markers -- the affine
# fit only uses the clicked center, not this radius.
_IMAGE_LAYOUT_DRAWN_MARKER_RADIUS_PX = 10
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
MANUAL_HISTORY_POLL_INTERVAL_MS = 5000
ENABLE_BIOLOGIC_LIVE_SCALAR_POLL = False
CONTACT_CONFIRM_DURATION_S = 10.0
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
    safety-lock/stop failure, which stay plain RuntimeError and still
    abort the run. Carries last_z, the Z the search's final step reached,
    so a caller that wants to proceed anyway has a usable height."""
    def __init__(self, message, last_z):
        super().__init__(message)
        self.last_z = last_z


CONTACT_CONFIRM_POLL_S = 1.0
# How closely a "Move Tip" move's driven XY must match a locked Image
# Monitor target's freshly-projected stage XY to count as having moved to
# that target (Search Z's "moved to" gate). Move Tip is the only move path,
# used for both generic jogs and driving to a locked target, so this is what
# tells them apart after the fact.
IMAGE_TARGET_MOVE_MATCH_TOL_MM = 0.3


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


class _ThreadSafeVariableMixin:
    """
    Makes a Tkinter Variable's .set() safe to call from any thread.

    Tkinter/Tcl is not thread-safe -- calling .set() on a Variable from a
    thread other than the Tkinter mainloop thread is undefined behavior:
    it can corrupt Tcl's internal C interpreter state and crash the whole
    process with an access violation (0xc0000005), unpredictably and with
    no Python-catchable traceback. This is not hypothetical -- it is the
    confirmed root cause of a real, reproducible-independent-of-Python-
    version crash on this project's Win7 instrument PC (see gui.py's own
    Manual Control / AutoContactZ code, which historically called .set()
    on status/target Variables directly from background worker threads
    throughout _execute_contact_z_search and friends). A single scalar
    Tcl variable write is small and fast, so the race window is narrow --
    it corrupts state *rarely* rather than *never*, which is exactly why
    this went unnoticed for a long time ("the status text still updates
    fine" was mistaken for evidence of safety, when it was really just a
    race that hadn't been hit yet).

    self._root is set by Variable.__init__ to the owning Tk root/toplevel
    (via master._root()), which has .after() like any widget. When called
    from the mainloop thread this behaves identically to the plain
    Variable -- zero behavior change for ordinary UI-thread code.
    """

    def set(self, value):
        if threading.current_thread() is threading.main_thread():
            super().set(value)
        else:
            self._root.after(0, super().set, value)


class _ThreadSafeStringVar(_ThreadSafeVariableMixin, tk.StringVar):
    pass


class _ThreadSafeBooleanVar(_ThreadSafeVariableMixin, tk.BooleanVar):
    pass


class _ThreadSafeIntVar(_ThreadSafeVariableMixin, tk.IntVar):
    pass


class _ThreadSafeDoubleVar(_ThreadSafeVariableMixin, tk.DoubleVar):
    pass


class MicroprobGUI(tk.Tk):
    def _enforce_light_theme(self):
        """
        Force a light appearance regardless of the OS dark-mode setting.
        Native ttk themes ('aqua' on macOS, 'vista'/'xpnative' on Windows)
        actively follow OS dark mode, and even classic (non-ttk) Tk widgets
        can pick up a dark-mode default text color from some Tk builds --
        both can leave light-on-light or dark-on-dark text since this file
        hardcodes light background colors (CLR_BG, CLR_LGRAY, white) but
        mostly leaves foreground unset. Must run before any widgets are
        built: the option database (option_add) only applies to widgets
        created afterward, and ttk styles must be configured before use.
        """
        self.option_add('*Background', CLR_BG)
        self.option_add('*Foreground', 'black')
        self.option_add('*insertBackground', 'black')
        self.option_add('*selectBackground', CLR_BLUE)
        self.option_add('*selectForeground', 'white')
        self.option_add('*Entry.Background', 'white')
        self.option_add('*Entry.Foreground', 'black')
        self.option_add('*Text.Background', 'white')
        self.option_add('*Text.Foreground', 'black')
        self.option_add('*Listbox.Background', 'white')
        self.option_add('*Listbox.Foreground', 'black')

        style = ttk.Style(self)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('.', background=CLR_BG, foreground='black')
        style.configure('TFrame', background=CLR_BG)
        style.configure('TLabel', background=CLR_BG, foreground='black')
        style.configure('TButton', background=CLR_LGRAY, foreground='black')
        style.map('TButton', background=[('active', CLR_LGRAY), ('disabled', CLR_BG)])
        style.configure('TCheckbutton', background=CLR_BG, foreground='black')
        style.configure('TRadiobutton', background=CLR_BG, foreground='black')
        style.configure('TCombobox', fieldbackground='white', foreground='black', background=CLR_LGRAY)
        style.map('TCombobox', fieldbackground=[('readonly', 'white')], foreground=[('readonly', 'black')])
        style.configure('TNotebook', background=CLR_BG)
        style.configure('TNotebook.Tab', background=CLR_LGRAY, foreground='black')
        style.map('TNotebook.Tab', background=[('selected', CLR_BG)], foreground=[('selected', 'black')])
        style.configure('TScale', background=CLR_BG)
        style.configure('TProgressbar', background=CLR_BLUE, troughcolor=CLR_LGRAY)

    def __init__(self, *, enable_background_polls=True, enable_file_logging=True, enable_xy_correction_csv_log=True):
        super().__init__()
        self._enforce_light_theme()
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
        # _log() only ever fed the on-screen log_text widget -- closing the
        # GUI (or a crash) lost the whole run's log with no way to review it
        # afterward. Mirror every message to a per-launch file too.
        self._log_file_lock = threading.Lock()
        self._log_file = None
        if enable_file_logging:
            try:
                os.makedirs('run_logs', exist_ok=True)
                log_file_path = os.path.join('run_logs', f'gui_{time.strftime("%Y%m%d_%H%M%S")}.log')
                self._log_file = open(log_file_path, 'a', encoding='utf-8')
            except Exception:
                self._log_file = None
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
            'motor': _ThreadSafeStringVar(value=COM_PORTS['motor']),
            'temp': _ThreadSafeStringVar(value=COM_PORTS['temp']),
            'mfc': _ThreadSafeStringVar(value=COM_PORTS['mfc']),
        }
        self._biologic_channel_var = _ThreadSafeStringVar(value='1')
        self._biologic_channel_info = []
        self._active_biologic_channel = 1
        self._olecom_postprocess_queue = queue.Queue()
        self._olecom_postprocess_thread = None
        self._olecom_postprocess_lock = threading.Lock()
        self._olecom_postprocess_hold = False
        self._olecom_pre_learning_factor = 1.30
        self._olecom_pre_learning_min_s = 20.0
        self._olecom_pre_learning_first_default_s = 120.0
        self._run_gas_mode_var = _ThreadSafeStringVar(value='require_auto')
        self._run_contact_fail_policy_var = _ThreadSafeStringVar(value='collect_anyway')
        self._run_retract_tip_on_done_var = _ThreadSafeBooleanVar(value=True)
        self._run_retract_tip_mm_var = _ThreadSafeStringVar(value='1.000')
        self._run_ramp_down_on_done_var = _ThreadSafeBooleanVar(value=True)
        self._run_ramp_down_end_temp_c_var = _ThreadSafeStringVar(value='25.0')
        self._run_ramp_down_end_ramp_rate_var = _ThreadSafeStringVar(value='5.0')
        self._tree_active_cell = (None, 0)
        self._manual_target = {
            'temp': _ThreadSafeStringVar(value='600'),
            'temp_ramp': _ThreadSafeStringVar(value='5.0'),
            'x': _ThreadSafeStringVar(value='0.000'),
            'y': _ThreadSafeStringVar(value='0.000'),
            'z': _ThreadSafeStringVar(value='0.000'),
            'gas_a': _ThreadSafeStringVar(value='0'),
            'gas_b': _ThreadSafeStringVar(value='0'),
        }
        self._quick_eis = {
            'label': _ThreadSafeStringVar(value='manual_eis'),
            'v_dc': _ThreadSafeStringVar(value='0.300'),
            'amp_mv': _ThreadSafeStringVar(value='10'),
            'n_pts': _ThreadSafeStringVar(value='60'),
            'peis_f_high': _ThreadSafeStringVar(value=f'{MANUAL_QUICK_F_HIGH_HZ:.0f}'),
            'peis_f_low': _ThreadSafeStringVar(value=f'{MANUAL_QUICK_F_LOW_HZ:.1f}'),
            'cycles': _ThreadSafeStringVar(value='1'),
        }
        self._quick_rapid = {
            'label': _ThreadSafeStringVar(value='manual_rapid'),
            'v_dc': _ThreadSafeStringVar(value='0.300'),
            'dv_mv': _ThreadSafeStringVar(value='10'),
            'hold_time': _ThreadSafeStringVar(value='5'),
            'post_hold_time': _ThreadSafeStringVar(value='3'),
            'ca_duration': _ThreadSafeStringVar(value='20'),
            'ca_dt': _ThreadSafeStringVar(value=str(MANUAL_QUICK_CA_DT_S)),
            'ca_i_range': _ThreadSafeStringVar(value=str(BIOLOGIC_OLECOM_CA_I_RANGE)),
            'ca_bandwidth': _ThreadSafeStringVar(value=str(BIOLOGIC_OLECOM_CA_BANDWIDTH)),
            'n_pts': _ThreadSafeStringVar(value='60'),
            'peis_f_high': _ThreadSafeStringVar(value=f'{MANUAL_QUICK_F_HIGH_HZ:.0f}'),
            'peis_f_low': _ThreadSafeStringVar(value=f'{MANUAL_QUICK_F_LOW_HZ:.1f}'),
            'cycles': _ThreadSafeStringVar(value='1'),
        }
        self._contact_search = {
            'start_offset': _ThreadSafeStringVar(value='0.200'),
            'step_mm': _ThreadSafeStringVar(value='0.010'),
            'max_beyond_seed_mm': _ThreadSafeStringVar(value='0.20'),
            'ocv_threshold': _ThreadSafeStringVar(value='0.010'),
            'settle_s': _ThreadSafeStringVar(value='0.30'),
            'engage_mm': _ThreadSafeStringVar(value='0.00'),
            'target_xy_tolerance_mm': _ThreadSafeStringVar(value=f'{IMAGE_TARGET_MOVE_MATCH_TOL_MM:.3f}'),
        }
        self._semi_auto = {
            'temperatures': _ThreadSafeStringVar(value='600, 550, 500'),
            'gas_pairs': _ThreadSafeStringVar(value='10:30; 30:10'),
            'voltages': _ThreadSafeStringVar(value='0.0, 0.1, 0.2'),
            'electrode_start': _ThreadSafeStringVar(value='1'),
            'electrode_end': _ThreadSafeStringVar(value='8'),
            'x1': _ThreadSafeStringVar(value='0.000'),
            'y1': _ThreadSafeStringVar(value='0.000'),
            'xn': _ThreadSafeStringVar(value='7.000'),
            'yn': _ThreadSafeStringVar(value='0.000'),
            'z1': _ThreadSafeStringVar(value='0.000'),
            'zn': _ThreadSafeStringVar(value='0.000'),
            'auto_contact_z': _ThreadSafeStringVar(value='1'),
            'contact_start_offset': _ThreadSafeStringVar(value='0.200'),
            'contact_step': _ThreadSafeStringVar(value='0.005'),
            'contact_max_beyond_seed': _ThreadSafeStringVar(value='0.200'),
            'contact_ocv_threshold': _ThreadSafeStringVar(value='0.100'),
            'contact_settle': _ThreadSafeStringVar(value='0.30'),
            'contact_engage': _ThreadSafeStringVar(value='0.01'),
            'dv': _ThreadSafeStringVar(value='0.03'),
            'hold_time': _ThreadSafeStringVar(value='120'),
            'post_peis_hold_time': _ThreadSafeStringVar(value='10'),
            'peis_f_high': _ThreadSafeStringVar(value='100'),
            'peis_f_low': _ThreadSafeStringVar(value='0.1'),
            'peis_n_pts': _ThreadSafeStringVar(value='60'),
            'peis_bandwidth': _ThreadSafeStringVar(value='4'),
            'peis_n_average': _ThreadSafeStringVar(value='1'),
            'ca_duration': _ThreadSafeStringVar(value=str(int(BIOLOGIC_OLECOM_SCOUT_MAX_S))),
            'ca_dt': _ThreadSafeStringVar(value='0.1'),
            'ca_i_range': _ThreadSafeStringVar(value=str(BIOLOGIC_OLECOM_CA_I_RANGE)),
            'ca_bandwidth': _ThreadSafeStringVar(value=str(BIOLOGIC_OLECOM_CA_BANDWIDTH)),
            'temp_ramp_rate': _ThreadSafeStringVar(value='5.0'),
            'stable_time': _ThreadSafeStringVar(value='120'),
            'gas_stable_time': _ThreadSafeStringVar(value='600'),
        }
        self._semi_auto_use = {
            'temperature': _ThreadSafeBooleanVar(value=True),
            'gas': _ThreadSafeBooleanVar(value=True),
            'tip': _ThreadSafeBooleanVar(value=True),
            'ca': _ThreadSafeBooleanVar(value=True),
        }
        # 'manual': the X1/Y1/XN/YN interpolation fields below.
        # 'image_monitor': every DXF-layout electrode with a Z seed (exact
        # or Z-plane estimate), sourced from the Image Monitor tab -- see
        # _image_seeded_electrode_rows_source.
        self._semi_auto_tip_source_var = _ThreadSafeStringVar(value='manual')
        # 'image_monitor' mode only: restrict the seeded electrodes used to
        # this subset (1-based, comma/hyphen-range syntax e.g. "1,3,5-8");
        # blank means every seeded electrode (unchanged default behavior).
        self._semi_auto_image_monitor_omit_electrodes_var = _ThreadSafeStringVar(value='')
        self._semi_auto_entries = {}
        self._semi_auto_summary = _ThreadSafeStringVar(
            value='Semi-auto generator ready'
        )
        self._full_auto = {
            'temperatures': _ThreadSafeStringVar(value='600, 550, 500'),
            'gas_pairs': _ThreadSafeStringVar(value='10:30; 30:10'),
            'voltages': _ThreadSafeStringVar(value='0.0, 0.1, 0.2'),
            'electrode_start': _ThreadSafeStringVar(value='1'),
            'electrode_end': _ThreadSafeStringVar(value='8'),
            'x1': _ThreadSafeStringVar(value='0.000'),
            'y1': _ThreadSafeStringVar(value='0.000'),
            'xn': _ThreadSafeStringVar(value='7.000'),
            'yn': _ThreadSafeStringVar(value='0.000'),
            'z1': _ThreadSafeStringVar(value='0.000'),
            'zn': _ThreadSafeStringVar(value='0.000'),
            'auto_contact_z': _ThreadSafeStringVar(value='1'),
            'contact_start_offset': _ThreadSafeStringVar(value='0.200'),
            'contact_step': _ThreadSafeStringVar(value='0.005'),
            'contact_max_beyond_seed': _ThreadSafeStringVar(value='0.200'),
            'contact_ocv_threshold': _ThreadSafeStringVar(value='0.100'),
            'contact_settle': _ThreadSafeStringVar(value='0.30'),
            'contact_engage': _ThreadSafeStringVar(value='0.01'),
            'dv': _ThreadSafeStringVar(value='0.03'),
            'hold_time': _ThreadSafeStringVar(value='60'),
            'post_peis_hold_time': _ThreadSafeStringVar(value='10'),
            'peis_f_high': _ThreadSafeStringVar(value='100'),
            'peis_f_low': _ThreadSafeStringVar(value='0.1'),
            'peis_n_pts': _ThreadSafeStringVar(value='60'),
            'peis_bandwidth': _ThreadSafeStringVar(value='4'),
            'peis_n_average': _ThreadSafeStringVar(value='1'),
            'ca_duration': _ThreadSafeStringVar(value='200'),
            'ca_dt': _ThreadSafeStringVar(value='0.1'),
            'ca_i_range': _ThreadSafeStringVar(value=str(BIOLOGIC_OLECOM_CA_I_RANGE)),
            'ca_bandwidth': _ThreadSafeStringVar(value=str(BIOLOGIC_OLECOM_CA_BANDWIDTH)),
            'temp_ramp_rate': _ThreadSafeStringVar(value='5.0'),
            'stable_time': _ThreadSafeStringVar(value='120'),
            'gas_stable_time': _ThreadSafeStringVar(value='600'),
            'normal_eis_floor_hz': _ThreadSafeStringVar(value='0.01'),
        }
        self._full_auto_use = {
            'temperature': _ThreadSafeBooleanVar(value=True),
            'gas': _ThreadSafeBooleanVar(value=True),
            'tip': _ThreadSafeBooleanVar(value=True),
        }
        self._full_auto_tip_source_var = _ThreadSafeStringVar(value='manual')
        self._full_auto_image_monitor_omit_electrodes_var = _ThreadSafeStringVar(value='')
        self._full_auto_entries = {}
        self._full_auto_summary = _ThreadSafeStringVar(
            value='Full-auto adaptive planner ready'
        )
        self._manual_current = {
            'temp': _ThreadSafeStringVar(value='-'),
            'x': _ThreadSafeStringVar(value='-'),
            'y': _ThreadSafeStringVar(value='-'),
            'z': _ThreadSafeStringVar(value='-'),
            'gas_a': _ThreadSafeStringVar(value='-'),
            'gas_b': _ThreadSafeStringVar(value='-'),
            'gas_a_sp': _ThreadSafeStringVar(value='-'),
            'gas_b_sp': _ThreadSafeStringVar(value='-'),
        }
        self._manual_status_var = _ThreadSafeStringVar(value='Manual control ready')
        self._manual_ocv_var = _ThreadSafeStringVar(value='OCV: -')
        self._manual_recommendation_var = _ThreadSafeStringVar(value='Recommendation: -')
        self._monitor_recommendation_var = _ThreadSafeStringVar(value='Recommendation: -')
        self._manual_measurement_running = False
        self._manual_measurement_stop_event = None
        self._manual_gas_seq = 0
        self._manual_gas_seq_lock = threading.Lock()
        self._manual_ocv_poll_shutdown = False
        self._manual_ocv_poll_inflight = False
        # -- Temperature / gas-flow history graphs (Manual Control tab) --
        self._manual_history_poll_shutdown = False
        self._manual_history_poll_inflight = False
        self._manual_history_start_time = None
        self._manual_history_points = {'temp': [], 'gas_a': [], 'gas_b': []}
        self._adaptive_engine_factory = None
        self._image_backend_var = _ThreadSafeStringVar(value='any')
        self._image_index_var = _ThreadSafeStringVar(value='0')
        self._image_expected_count_var = _ThreadSafeStringVar(value='')
        self._image_status_var = _ThreadSafeStringVar(value='Camera idle')
        self._image_detection_var = _ThreadSafeStringVar(value='Electrodes: -')
        self._image_hint_var = _ThreadSafeStringVar(value='Hint: if the live overlay looks unreliable, align a DXF layout for a trusted tracking seed.')
        self._image_target_status_var = _ThreadSafeStringVar(value='Target: not locked')
        self._image_show_circles_var = _ThreadSafeBooleanVar(value=True)
        # When True (default), locking a new target electrode leaves the
        # "Move to: Z" field untouched instead of auto-filling it from that
        # electrode's known/estimated Z seed -- see
        # _image_sync_manual_target_from_selected.
        self._image_lock_z_var = _ThreadSafeBooleanVar(value=True)
        self._image_monitor_cap = None
        self._image_monitor_running = False
        self._image_monitor_photo = None
        self._image_monitor_frame_idx = 0
        self._image_monitor_detect_every = 8
        self._image_monitor_last_overlay_bgr = None
        self._image_seed_map = None
        self._image_seed_layout = None
        self._image_seed_tracked = None
        self._image_layout_seed_map = None
        self._image_layout_tracked = None
        # _image_monitor_tick (main thread) mutates the Circle objects in
        # _image_layout_tracked/_image_seed_tracked in place every tick
        # (detect_and_refit_frame's own docstring: "tracked is mutated in
        # place"), while AutoContactZ's background thread concurrently
        # reads individual circles' .smoothed_x/.smoothed_y/.radius
        # (_image_probe_tip_pixel_now, _image_recalibrate_xy_bias_from_contact,
        # _image_resolve_live_tracked_xy) with no synchronization -- a real
        # data race between the two threads that were both confirmed active
        # (via a faulthandler crash dump) at the moment of a Win7 access
        # violation. Guards every cross-thread touch of these two
        # attributes' Circle objects.
        self._image_tracked_lock = threading.Lock()
        self._image_monitor_last_frame_rgb = None
        self._image_monitor_last_frame_time_s = None
        self._image_tracking_map = None
        self._image_selected_target = None
        self._image_render_shape = None
        self._image_render_size = None
        self._image_render_offset = (0, 0)

        # -- Probe-based pixel<->stage calibration --
        self._image_pixel_stage_calibration_refs = []
        self._image_pixel_stage_affine_calibration = None
        self._image_pixel_stage_z_ref_mm = None
        self._image_pixel_stage_affine_status_var = _ThreadSafeStringVar(value='Probe calibration: not solved')
        self._image_probe_detector = None
        self._image_probe_tip_candidate = None
        self._image_probe_roi = None
        self._image_probe_roi_select_mode = False
        self._image_probe_roi_drag_start = None
        self._image_probe_roi_drag_current = None
        self._image_probe_status_var = _ThreadSafeStringVar(value='Probe: not detected')
        # Button widgets recolored to reflect an active mode/process --
        # see _image_set_mode_button_active. Assigned in _build_tab_image.
        self._image_probe_roi_button = None
        self._image_draw_circles_button = None
        self._image_search_z_button = None

        # -- Persistent per-electrode Z seeds + pooled Z-parallax slope --
        self._image_electrode_z_store = ElectrodeZCalibrationStore.load(
            getattr(_config, 'VISION_ELECTRODE_Z_SEED_PATH', 'vision_calibration/electrode_z_seed.json')
        )
        self._image_z_seed_status_var = _ThreadSafeStringVar(value='Z seed: lock a target and move to it first')
        self._image_z_seed_ocv_var = _ThreadSafeStringVar(value='OCV: --')
        # Diagnostic snapshot of the probe-tip detection driving the parallax
        # samples collected during Z Contact Search -- see
        # _image_stash_probe_roi_debug / _image_update_parallax_debug_display.
        self._image_last_probe_roi_debug = None
        self._image_last_parallax_debug = None
        self._image_parallax_debug_var = _ThreadSafeStringVar(value='Last parallax sample: none yet')
        self._image_parallax_panel_photo = None
        # Per-run cache of the last live-tracked position actually trusted
        # for each electrode (layout_index -> (stage_x, stage_y)); reset at
        # the top of _run_worker. See _image_resolve_live_tracked_xy.
        self._image_run_trusted_positions = {}
        # layout_index -> the debug_out dict from the most recent
        # _image_project_pixel_to_stage_xy call that targeted it (parallax
        # shift, XY-bias applied, temperature used) -- read back by
        # _image_log_xy_correction_sample when that electrode's own contact
        # produces a fresh XY-bias sample, so the CSV/log record can show
        # both what correction was applied and what was actually measured.
        # Not reset per-run (unlike _image_run_trusted_positions): a stale
        # entry just means "no correction was computed this touch", which
        # the logger already treats as an absent value, same as None.
        self._image_last_applied_correction = {}
        self._image_xy_correction_csv_log_enabled = bool(enable_xy_correction_csv_log)

        # -- DXF-layout-driven semi-manual electrode alignment --
        self._image_layout_model = None
        self._image_layout_status_var = _ThreadSafeStringVar(value='Layout: not loaded')
        self._image_circle_params = None  # None => live detector/drift refit use their own built-in defaults
        self._image_layout_drawn_circles = []       # [{"center":[x,y],"radius":r}, ...]
        self._image_layout_alignment_pairs = []      # [(drawn_idx, layout_idx), ...]
        self._image_layout_draw_mode = False
        self._image_layout_selected_drawn_idx = None
        self._image_layout_base_transform = None
        self._image_layout_nudge_scale_x_var = _ThreadSafeIntVar(value=0)
        self._image_layout_nudge_scale_y_var = _ThreadSafeIntVar(value=0)
        self._image_layout_nudge_angle_var = _ThreadSafeIntVar(value=0)
        self._image_layout_nudge_translate_x_var = _ThreadSafeIntVar(value=0)
        self._image_layout_nudge_translate_y_var = _ThreadSafeIntVar(value=0)
        self._image_layout_nudge_shear_x_var = _ThreadSafeIntVar(value=0)
        self._image_layout_nudge_shear_y_var = _ThreadSafeIntVar(value=0)
        self._image_layout_alignment_status_var = _ThreadSafeStringVar(value='Alignment: not fit')

        self._build_ui()
        if self._enable_background_polls:
            self._poll_log()
            self._poll_monitor()
            self._schedule_manual_ocv_poll()
            self._schedule_manual_history_poll()
        self._refresh_port_choices()
        self._load_condition_generator_settings()

    def destroy(self):
        self._manual_ocv_poll_shutdown = True
        self._manual_history_poll_shutdown = True
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
        self.tab_semi = ttk.Frame(nb)
        self.tab_full = ttk.Frame(nb)
        self.tab_run  = ttk.Frame(nb)
        self.tab_image = ttk.Frame(nb)
        self.tab_live = ttk.Frame(nb)
        self.tab_manual = ttk.Frame(nb)

        nb.add(self.tab_hw,   text='  Hardware  ')
        nb.add(self.tab_cond, text='  CSV List  ')
        nb.add(self.tab_semi, text='  Semi-auto  ')
        nb.add(self.tab_full, text='  Full-auto  ')
        nb.add(self.tab_run,  text='  Run / Monitor  ')
        nb.add(self.tab_image, text='  Image Monitor  ')
        nb.add(self.tab_live, text='  EIS Monitor  ')
        nb.add(self.tab_manual, text='  Manual Control  ')

        self._build_tab_hardware()
        self._build_tab_conditions()
        self._build_tab_semi_auto()
        self._build_tab_full_auto()
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
        self._biologic_ip = _ThreadSafeStringVar(value=BIOLOGIC_IP)
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
        self._port_status_var = _ThreadSafeStringVar(value='Serial ports: not scanned yet')
        tk.Label(f, textvariable=self._port_status_var, bg=CLR_BG,
                 fg='#555', anchor='w', font=('Segoe UI', 9)).grid(
                 row=14, column=0, columnspan=5, sticky='w', padx=10, pady=(0, 8))

    def _readback_row(self, parent, row, label):
        tk.Label(parent, text=label, bg=CLR_LGRAY, width=25,
                 anchor='w', font=('Segoe UI', 10)).grid(
                 row=row, column=0, padx=10, pady=4)
        var = _ThreadSafeStringVar(value='—')
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
                'ContactMaxBeyondSeed_mm',
                'ContactOCVThreshold_V', 'ContactSettle_s', 'ContactEngage_mm',
                'V_dc', 'dV', 'HoldTime_s', 'PostPEIS_HoldTime_s',
                'PEIS_fHigh', 'PEIS_fLow', 'PEIS_nPts', 'PEIS_Bandwidth', 'PEIS_NAverage',
                'SkipCA', 'CA_duration_s', 'CA_dt', 'CA_IRange', 'CA_Bandwidth',
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

    def _build_tab_semi_auto(self):
        host = tk.Frame(self.tab_semi, bg=CLR_BG)
        host.pack(fill='both', expand=True)

        self._semi_auto_scroll_canvas = tk.Canvas(
            host, bg=CLR_BG, highlightthickness=0, borderwidth=0,
        )
        semi_auto_scrollbar = ttk.Scrollbar(
            host, orient='vertical', command=self._semi_auto_scroll_canvas.yview,
        )
        self._semi_auto_scroll_canvas.configure(yscrollcommand=semi_auto_scrollbar.set)
        self._semi_auto_scroll_canvas.pack(side='left', fill='both', expand=True)
        semi_auto_scrollbar.pack(side='right', fill='y')

        f = tk.Frame(self._semi_auto_scroll_canvas, bg=CLR_BG)
        self._semi_auto_scroll_window = self._semi_auto_scroll_canvas.create_window(
            (0, 0), window=f, anchor='nw'
        )

        def _sync_semi_auto_scrollregion(_event=None):
            self._semi_auto_scroll_canvas.configure(
                scrollregion=self._semi_auto_scroll_canvas.bbox('all')
            )

        def _sync_semi_auto_canvas_width(event):
            self._semi_auto_scroll_canvas.itemconfigure(
                self._semi_auto_scroll_window, width=event.width,
            )

        f.bind('<Configure>', _sync_semi_auto_scrollregion)
        self._semi_auto_scroll_canvas.bind('<Configure>', _sync_semi_auto_canvas_width)

        def _semi_auto_mousewheel(event):
            delta = getattr(event, 'delta', 0)
            if delta:
                self._semi_auto_scroll_canvas.yview_scroll(int(-delta / 120), 'units')

        def _semi_auto_canvas_enter(_event):
            self._semi_auto_scroll_canvas.bind_all('<MouseWheel>', _semi_auto_mousewheel)

        def _semi_auto_canvas_leave(_event):
            self._semi_auto_scroll_canvas.unbind_all('<MouseWheel>')

        self._semi_auto_scroll_canvas.bind('<Enter>', _semi_auto_canvas_enter)
        self._semi_auto_scroll_canvas.bind('<Leave>', _semi_auto_canvas_leave)

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
        ).pack(fill='x')
        options_row = tk.Frame(header, bg=CLR_BG)
        options_row.pack(fill='x', pady=(8, 0))
        option_box = tk.LabelFrame(options_row, text='Disable Unused Hardware', bg=CLR_BG, padx=8, pady=6)
        option_box.pack(side='left')
        ttk.Checkbutton(
            option_box, text='Use temperature',
            variable=self._semi_auto_use['temperature'],
            command=self._update_semi_auto_field_states
        ).grid(row=0, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use gas',
            variable=self._semi_auto_use['gas'],
            command=self._update_semi_auto_field_states
        ).grid(row=1, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use tip position',
            variable=self._semi_auto_use['tip'],
            command=self._update_semi_auto_field_states
        ).grid(row=2, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use CA',
            variable=self._semi_auto_use['ca'],
            command=self._update_semi_auto_field_states
        ).grid(row=3, column=0, sticky='w', padx=4)

        tip_source_box = tk.LabelFrame(options_row, text='Tip Position Source', bg=CLR_BG, padx=8, pady=6)
        tip_source_box.pack(side='left', padx=(12, 0))
        ttk.Radiobutton(
            tip_source_box, text='Manual range', value='manual',
            variable=self._semi_auto_tip_source_var,
            command=self._update_semi_auto_field_states,
        ).grid(row=0, column=0, sticky='w', padx=4)
        ttk.Radiobutton(
            tip_source_box, text='Image Monitor seeded electrodes', value='image_monitor',
            variable=self._semi_auto_tip_source_var,
            command=self._update_semi_auto_field_states,
        ).grid(row=1, column=0, sticky='w', padx=4)
        tk.Label(tip_source_box, text='Electrodes to omit (blank = none)', bg=CLR_BG).grid(
            row=2, column=0, sticky='w', padx=4, pady=(4, 0),
        )
        self._semi_auto_image_monitor_omit_electrodes_entry = tk.Entry(
            tip_source_box, textvariable=self._semi_auto_image_monitor_omit_electrodes_var, width=18,
        )
        self._semi_auto_image_monitor_omit_electrodes_entry.grid(row=3, column=0, sticky='w', padx=4, pady=(0, 2))

        diameter_row = tk.Frame(tip_source_box, bg=CLR_BG)
        diameter_row.grid(row=4, column=0, sticky='w', padx=4, pady=(4, 0))
        tk.Label(diameter_row, text='Exclude by diameter:', bg=CLR_BG).pack(side='left')
        ttk.Button(
            diameter_row, text='Refresh', command=self._image_refresh_semi_auto_exclude_diameter_checkboxes,
        ).pack(side='left', padx=(4, 0))
        self._semi_auto_exclude_diameter_frame = tk.Frame(tip_source_box, bg=CLR_BG)
        self._semi_auto_exclude_diameter_frame.grid(row=5, column=0, sticky='w', padx=4, pady=(0, 2))
        self._semi_auto_exclude_diameter_vars = {}

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
            ('Contact max beyond seed (mm)', 'contact_max_beyond_seed', 7, 0),
            ('Contact OCV threshold (V)', 'contact_ocv_threshold', 7, 2),
            ('Contact settle (s)', 'contact_settle', 8, 0),
            ('Contact engage (mm)', 'contact_engage', 8, 2),
            ('PEIS amplitude (V)', 'dv', 9, 0),
            ('PEIS f high (Hz)', 'peis_f_high', 9, 2),
            ('PEIS f low (Hz)', 'peis_f_low', 10, 0),
            ('PEIS n pts', 'peis_n_pts', 10, 2),
            ('PEIS bandwidth', 'peis_bandwidth', 11, 0),
            ('PEIS N average', 'peis_n_average', 11, 2),
            ('Pre-step duration (s)', 'hold_time', 12, 0),
            ('CA dt (s)', 'ca_dt', 12, 2),
            ('Step duration (s)', 'ca_duration', 13, 0),
            ('CA I Range', 'ca_i_range', 13, 2),
            ('Post-step duration (s)', 'post_peis_hold_time', 14, 0),
            ('CA bandwidth', 'ca_bandwidth', 14, 2),
            ('Temp ramp rate (C/min)', 'temp_ramp_rate', 15, 0),
            ('Temp stable time (s)', 'stable_time', 15, 2),
            ('Gas stable time (s)', 'gas_stable_time', 16, 0),
        ]
        for label, key, row, col in fields:
            tk.Label(
                grid, text=label, bg=CLR_BG, anchor='w',
                font=('Segoe UI', 10)
            ).grid(row=row, column=col, sticky='w', padx=(0, 8), pady=4)
            entry = tk.Entry(
                grid, textvariable=self._semi_auto[key],
                font=('Courier New', 10), width=28
            )
            entry.grid(row=row, column=col + 1, sticky='ew', padx=(0, 18), pady=4)
            self._semi_auto_entries[key] = entry

        help_box = tk.LabelFrame(f, text='Input Format', bg=CLR_BG, padx=10, pady=10)
        help_box.pack(fill='x', padx=8, pady=(4, 6))
        help_lines = [
            'Temperatures / Voltages: comma-separated, for example 600, 550, 500',
            'Gas pairs: semicolon-separated raw DMFC setting pairs (0-100), for example 10:30; 30:10',
            'Tip positions: XY are linearly interpolated from electrode 1 to electrode N',
            'Z is a seed value. AutoContactZ=1 starts 0.2 mm above seed, steps toward contact, then engages by the configured amount.',
            'Contact max beyond seed both limits the search and is a hard tip-safety stop; keep it small (default 0.200 mm) to avoid driving the tip too far into an electrode.',
            'Uncheck temperature / gas / tip position above to disable those inputs and generate None values for that hardware step.',
            'During runs, gas is set before a temperature change; gas/temp stabilization waits overlap, and unchanged conditions skip their wait.',
            'CA sequence per electrode: pre-step hold at Vdc, then the step at Vdc+dV (seeds PEIS), then the post-step hold back at Vdc immediately before PEIS runs.',
            'Image Monitor seeded electrodes mode: the Electrodes field restricts which seeded electrodes are used, e.g. "1,3,5-8" -- blank uses every seeded electrode.',
        ]
        for line in help_lines:
            tk.Label(help_box, text=line, bg=CLR_BG, anchor='w',
                     justify='left', font=('Segoe UI', 10)).pack(fill='x', pady=1)

        btn_row = tk.Frame(f, bg=CLR_BG)
        btn_row.pack(fill='x', padx=8, pady=(2, 6))
        ttk.Button(
            btn_row, text='Generate to CSV List',
            command=self._generate_semi_auto_conditions
        ).pack(side='left', padx=4)
        ttk.Button(
            btn_row, text='Append to CSV List',
            command=lambda: self._generate_semi_auto_conditions(append=True)
        ).pack(side='left', padx=4)

        tk.Label(
            f, textvariable=self._semi_auto_summary, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='sunken'
        ).pack(fill='x', padx=8, pady=(0, 8))
        self._update_semi_auto_field_states()

    def _build_tab_full_auto(self):
        host = tk.Frame(self.tab_full, bg=CLR_BG)
        host.pack(fill='both', expand=True)

        self._full_auto_scroll_canvas = tk.Canvas(
            host, bg=CLR_BG, highlightthickness=0, borderwidth=0,
        )
        full_auto_scrollbar = ttk.Scrollbar(
            host, orient='vertical', command=self._full_auto_scroll_canvas.yview,
        )
        self._full_auto_scroll_canvas.configure(yscrollcommand=full_auto_scrollbar.set)
        self._full_auto_scroll_canvas.pack(side='left', fill='both', expand=True)
        full_auto_scrollbar.pack(side='right', fill='y')

        f = tk.Frame(self._full_auto_scroll_canvas, bg=CLR_BG)
        self._full_auto_scroll_window = self._full_auto_scroll_canvas.create_window(
            (0, 0), window=f, anchor='nw'
        )

        def _sync_full_auto_scrollregion(_event=None):
            self._full_auto_scroll_canvas.configure(
                scrollregion=self._full_auto_scroll_canvas.bbox('all')
            )

        def _sync_full_auto_canvas_width(event):
            self._full_auto_scroll_canvas.itemconfigure(
                self._full_auto_scroll_window, width=event.width,
            )

        f.bind('<Configure>', _sync_full_auto_scrollregion)
        self._full_auto_scroll_canvas.bind('<Configure>', _sync_full_auto_canvas_width)

        def _full_auto_mousewheel(event):
            delta = getattr(event, 'delta', 0)
            if delta:
                self._full_auto_scroll_canvas.yview_scroll(int(-delta / 120), 'units')

        def _full_auto_canvas_enter(_event):
            self._full_auto_scroll_canvas.bind_all('<MouseWheel>', _full_auto_mousewheel)

        def _full_auto_canvas_leave(_event):
            self._full_auto_scroll_canvas.unbind_all('<MouseWheel>')

        self._full_auto_scroll_canvas.bind('<Enter>', _full_auto_canvas_enter)
        self._full_auto_scroll_canvas.bind('<Leave>', _full_auto_canvas_leave)

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
        ).pack(fill='x')
        options_row = tk.Frame(header, bg=CLR_BG)
        options_row.pack(fill='x', pady=(8, 0))
        option_box = tk.LabelFrame(options_row, text='Disable Unused Hardware', bg=CLR_BG, padx=8, pady=6)
        option_box.pack(side='left')
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

        tip_source_box = tk.LabelFrame(options_row, text='Tip Position Source', bg=CLR_BG, padx=8, pady=6)
        tip_source_box.pack(side='left', padx=(12, 0))
        ttk.Radiobutton(
            tip_source_box, text='Manual range', value='manual',
            variable=self._full_auto_tip_source_var,
            command=self._update_full_auto_field_states,
        ).grid(row=0, column=0, sticky='w', padx=4)
        ttk.Radiobutton(
            tip_source_box, text='Image Monitor seeded electrodes', value='image_monitor',
            variable=self._full_auto_tip_source_var,
            command=self._update_full_auto_field_states,
        ).grid(row=1, column=0, sticky='w', padx=4)
        tk.Label(tip_source_box, text='Electrodes to omit (blank = none)', bg=CLR_BG).grid(
            row=2, column=0, sticky='w', padx=4, pady=(4, 0),
        )
        self._full_auto_image_monitor_omit_electrodes_entry = tk.Entry(
            tip_source_box, textvariable=self._full_auto_image_monitor_omit_electrodes_var, width=18,
        )
        self._full_auto_image_monitor_omit_electrodes_entry.grid(row=3, column=0, sticky='w', padx=4, pady=(0, 2))

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
            ('Contact max beyond seed (mm)', 'contact_max_beyond_seed', 7, 0),
            ('Contact OCV threshold (V)', 'contact_ocv_threshold', 7, 2),
            ('Contact settle (s)', 'contact_settle', 8, 0),
            ('Contact engage (mm)', 'contact_engage', 8, 2),
            ('dV scout step (V)', 'dv', 9, 0),
            ('Pre-CA first seed/max (s)', 'hold_time', 9, 2),
            ('Seeded PEIS high (Hz)', 'peis_f_high', 10, 0),
            ('Seeded PEIS n pts', 'peis_n_pts', 10, 2),
            ('PEIS bandwidth', 'peis_bandwidth', 11, 0),
            ('PEIS N average', 'peis_n_average', 11, 2),
            ('dV scout live max (s)', 'ca_duration', 12, 0),
            ('CA/FFT dt (s)', 'ca_dt', 12, 2),
            ('CA I Range', 'ca_i_range', 13, 0),
            ('CA bandwidth', 'ca_bandwidth', 13, 2),
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
            'Image Monitor seeded electrodes mode: the Electrodes field restricts which seeded electrodes are used, e.g. "1,3,5-8" -- blank uses every seeded electrode.',
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

    # ── Tab 3: Run / Monitor ───────────────────────────────────────────
    def _build_tab_run(self):
        f = self.tab_run

        # Controls row
        ctrl = tk.Frame(f, bg=CLR_BG)
        ctrl.pack(fill='x', padx=8, pady=6)

        tk.Label(ctrl, text='Result folder:', bg=CLR_BG).pack(side='left')
        self._result_dir = _ThreadSafeStringVar(value=os.path.join(os.getcwd(), 'results'))
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
            values=('collect_anyway', 'stop', 'skip_row'),
            width=16,
            state='readonly',
        ).grid(row=1, column=1, sticky='w', padx=(0, 12), pady=2)

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

        ttk.Checkbutton(
            safety,
            text='Ramp down after run to (deg C)',
            variable=self._run_ramp_down_on_done_var,
        ).grid(row=3, column=0, columnspan=2, sticky='w', padx=(0, 4), pady=2)
        tk.Entry(
            safety,
            textvariable=self._run_ramp_down_end_temp_c_var,
            width=8,
            font=('Courier New', 9),
        ).grid(row=3, column=2, sticky='w', pady=2)
        tk.Label(safety, text='at (deg C/min)', bg=CLR_BG, font=('Segoe UI', 9)).grid(
            row=3, column=3, sticky='w', padx=(8, 2), pady=2
        )
        tk.Entry(
            safety,
            textvariable=self._run_ramp_down_end_ramp_rate_var,
            width=6,
            font=('Courier New', 9),
        ).grid(row=3, column=4, sticky='w', pady=2)
        tk.Label(
            safety,
            text='Fires no matter how the run ends (success, stop, or error).',
            bg=CLR_BG,
            anchor='w',
            font=('Segoe UI', 9),
        ).grid(row=4, column=0, columnspan=5, sticky='w', padx=(0, 8), pady=(0, 2))

        # Progress
        prog_frame = tk.Frame(f, bg=CLR_BG)
        prog_frame.pack(fill='x', padx=8, pady=4)

        tk.Label(prog_frame, text='Progress:', bg=CLR_BG).pack(side='left')
        self._progress_var = _ThreadSafeDoubleVar()
        self._progress_bar = ttk.Progressbar(prog_frame, variable=self._progress_var,
                                              maximum=100, length=400)
        self._progress_bar.pack(side='left', padx=8)
        self._progress_lbl = tk.Label(prog_frame, text='0 / 0', bg=CLR_BG)
        self._progress_lbl.pack(side='left')

        # Status line
        self._status_var = _ThreadSafeStringVar(value='Ready')
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

        self._monitor_run_var = _ThreadSafeStringVar(value='Run: idle')
        self._monitor_step_var = _ThreadSafeStringVar(value='Step: idle')
        self._monitor_dc_var = _ThreadSafeStringVar(value='Current: -')
        self._monitor_eis_var = _ThreadSafeStringVar(value='Impedance: -')

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
        host = tk.Frame(self.tab_image, bg=CLR_BG)
        host.pack(fill='both', expand=True)

        self._image_scroll_canvas = tk.Canvas(
            host,
            bg=CLR_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        image_scrollbar = ttk.Scrollbar(
            host,
            orient='vertical',
            command=self._image_scroll_canvas.yview,
        )
        self._image_scroll_canvas.configure(yscrollcommand=image_scrollbar.set)
        self._image_scroll_canvas.pack(side='left', fill='both', expand=True)
        image_scrollbar.pack(side='right', fill='y')

        f = tk.Frame(self._image_scroll_canvas, bg=CLR_BG)
        self._image_scroll_window = self._image_scroll_canvas.create_window(
            (0, 0), window=f, anchor='nw'
        )

        def _sync_image_scrollregion(_event=None):
            self._image_scroll_canvas.configure(
                scrollregion=self._image_scroll_canvas.bbox('all')
            )

        def _sync_image_canvas_width(event):
            self._image_scroll_canvas.itemconfigure(
                self._image_scroll_window,
                width=event.width,
            )

        f.bind('<Configure>', _sync_image_scrollregion)
        self._image_scroll_canvas.bind('<Configure>', _sync_image_canvas_width)

        def _image_mousewheel(event):
            delta = getattr(event, 'delta', 0)
            if delta:
                self._image_scroll_canvas.yview_scroll(int(-delta / 120), 'units')

        # Scope the wheel binding to only be active while the cursor is over
        # this tab's canvas -- a plain bind_all here would otherwise silently
        # steal scroll-wheel input from the Manual Control tab's own
        # scrollable canvas (bind_all is global and last-registered wins).
        def _image_canvas_enter(_event):
            self._image_scroll_canvas.bind_all('<MouseWheel>', _image_mousewheel)

        def _image_canvas_leave(_event):
            self._image_scroll_canvas.unbind_all('<MouseWheel>')

        self._image_scroll_canvas.bind('<Enter>', _image_canvas_enter)
        self._image_scroll_canvas.bind('<Leave>', _image_canvas_leave)

        f.grid_columnconfigure(0, weight=1, uniform='image_col')
        f.grid_columnconfigure(1, weight=1, uniform='image_col')

        def _section(title, *, row, column, columnspan=1):
            box = tk.LabelFrame(f, text=title, bg=CLR_BG, padx=8, pady=8)
            box.grid(row=row, column=column, columnspan=columnspan, sticky='nsew', padx=6, pady=6)
            return box

        def _status_label(parent, var):
            tk.Label(
                parent, textvariable=var, bg=CLR_LGRAY,
                anchor='w', font=('Segoe UI', 10), relief='groove', padx=8, pady=4,
            ).pack(fill='x', padx=6, pady=(2, 6))

        # A distinct style for a button whose mode/process is currently
        # active (Select Probe ROI while dragging, Draw Electrode Circles
        # while placing, Search Z while running) -- see
        # _image_set_mode_button_active. Foreground changes render on every
        # platform; background may not on themes that ignore it for ttk
        # buttons (e.g. macOS aqua), so this isn't the only signal. No font
        # change here -- a bold variant measures wider than regular and
        # would resize the button when toggled; each of these buttons also
        # gets an explicit width= below so its size never depends on style.
        try:
            ttk.Style(self).configure(
                'ImageModeActive.TButton',
                background=CLR_GOLD, foreground='#000000',
            )
        except Exception:
            pass

        # -- Camera & stage position (shared with Manual Control), plus
        # target-electrode lock & move (merged in: locking a target updates
        # the "Move to:" fields below, so both live in one place). ---------
        stage_section = _section('Camera & Stage', row=0, column=0)
        cam_row = tk.Frame(stage_section, bg=CLR_BG)
        cam_row.pack(fill='x', padx=6, pady=(6, 2))
        ttk.Button(cam_row, text='Start Camera', command=self._image_monitor_start).pack(side='left', padx=4)
        ttk.Button(cam_row, text='Stop Camera', command=self._image_monitor_stop).pack(side='left', padx=4)
        cam_frame_row = tk.Frame(stage_section, bg=CLR_BG)
        cam_frame_row.pack(fill='x', padx=6, pady=2)
        ttk.Button(cam_frame_row, text='Freeze Current Frame', command=self._image_freeze_current_seed).pack(side='left', padx=4)
        ttk.Button(cam_frame_row, text='Clear Frame', command=self._image_clear_seed).pack(side='left', padx=4)
        stage_row_cur = tk.Frame(stage_section, bg=CLR_BG)
        stage_row_cur.pack(fill='x', padx=6, pady=2)
        tk.Label(stage_row_cur, text='Current:', bg=CLR_BG).pack(side='left')
        for axis_label, key in (('X', 'x'), ('Y', 'y'), ('Z', 'z')):
            tk.Label(stage_row_cur, text=f'{axis_label}', bg=CLR_BG).pack(side='left', padx=(6, 0))
            tk.Label(
                stage_row_cur, textvariable=self._manual_current[key],
                bg=CLR_LGRAY, font=('Courier New', 10), relief='groove', width=8,
            ).pack(side='left', padx=(2, 0))
        stage_row_tgt = tk.Frame(stage_section, bg=CLR_BG)
        stage_row_tgt.pack(fill='x', padx=6, pady=2)
        tk.Label(stage_row_tgt, text='Move to:', bg=CLR_BG).pack(side='left')
        for axis_label, key in (('X', 'x'), ('Y', 'y'), ('Z', 'z')):
            tk.Label(stage_row_tgt, text=f'{axis_label}', bg=CLR_BG).pack(side='left', padx=(6, 0))
            tk.Entry(
                stage_row_tgt, textvariable=self._manual_target[key],
                width=8, font=('Courier New', 10),
            ).pack(side='left', padx=(2, 0))
        ttk.Checkbutton(
            stage_row_tgt, text='Lock Z', variable=self._image_lock_z_var,
        ).pack(side='left', padx=(12, 0))
        stage_row_btns = tk.Frame(stage_section, bg=CLR_BG)
        stage_row_btns.pack(fill='x', padx=6, pady=2)
        ttk.Button(
            stage_row_btns, text='Refresh Current State',
            command=lambda: self._run_manual_action(self._manual_refresh_state),
        ).pack(side='left', padx=4)
        self._image_move_tip_button = ttk.Button(
            stage_row_btns, text='Move Tip',
            command=lambda: self._run_manual_action(self._manual_move_stage),
        )
        self._image_move_tip_button.pack(side='left', padx=4)
        _status_label(stage_section, self._image_status_var)
        _status_label(stage_section, self._image_detection_var)
        _status_label(stage_section, self._image_target_status_var)

        # -- Live camera preview (top-right, spans the Camera & Stage +
        # Pixel-Probe Calibration rows so it's visible without scrolling). --
        f.grid_rowconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)
        live_view_col = tk.Frame(f, bg=CLR_BG)
        live_view_col.grid(row=0, column=1, rowspan=2, sticky='nsew', padx=6, pady=6)
        live_view_col.grid_columnconfigure(0, weight=1)
        live_view_col.grid_rowconfigure(1, weight=1)
        circles_toggle_row = tk.Frame(live_view_col, bg=CLR_BG)
        circles_toggle_row.grid(row=0, column=0, sticky='w', pady=(0, 4))
        ttk.Checkbutton(
            circles_toggle_row, text='Show Electrode Circles',
            variable=self._image_show_circles_var,
        ).pack(side='left')
        self._image_monitor_label = tk.Label(
            live_view_col,
            bg='black',
            anchor='center',
            text='Camera preview will appear here',
            fg='white',
            font=('Segoe UI', 11),
        )
        self._image_monitor_label.grid(row=1, column=0, sticky='nsew')
        self._image_monitor_label.bind('<ButtonPress-1>', self._image_monitor_press)
        self._image_monitor_label.bind('<B1-Motion>', self._image_monitor_drag)
        self._image_monitor_label.bind('<ButtonRelease-1>', self._image_monitor_release)

        # -- Pixel-probe calibration ------------------------------------------
        probe_section = _section('Pixel-Probe Calibration', row=1, column=0)
        probe_row1 = tk.Frame(probe_section, bg=CLR_BG)
        probe_row1.pack(fill='x', padx=6, pady=(6, 2))
        self._image_probe_roi_button = ttk.Button(
            probe_row1, text='Select Probe ROI', width=16,
            command=self._image_toggle_probe_roi_select_mode,
        )
        self._image_probe_roi_button.pack(side='left', padx=4)
        ttk.Button(probe_row1, text='Clear Probe ROI', command=self._image_clear_probe_roi).pack(side='left', padx=4)
        ttk.Button(probe_row1, text='Detect Probe Tip', command=self._image_detect_probe_tip).pack(side='left', padx=4)
        ttk.Button(probe_row1, text='Capture Probe Cal Point', command=self._image_add_pixel_stage_calibration_point).pack(side='left', padx=4)
        probe_row2 = tk.Frame(probe_section, bg=CLR_BG)
        probe_row2.pack(fill='x', padx=6, pady=2)
        ttk.Button(probe_row2, text='Solve Probe Affine', command=self._image_solve_pixel_stage_affine_calibration).pack(side='left', padx=4)
        ttk.Button(probe_row2, text='Clear Probe Cal', command=self._image_clear_pixel_stage_affine_calibration).pack(side='left', padx=4)
        ttk.Button(probe_row2, text='Save Probe Cal', command=self._image_save_pixel_stage_affine_calibration).pack(side='left', padx=4)
        ttk.Button(probe_row2, text='Load Probe Cal', command=self._image_load_pixel_stage_affine_calibration).pack(side='left', padx=4)
        probe_row3 = tk.Frame(probe_section, bg=CLR_BG)
        probe_row3.pack(fill='x', padx=6, pady=2)
        ttk.Button(probe_row3, text='Tune Probe Params', command=self._image_open_probe_tuning_window).pack(side='left', padx=4)
        ttk.Button(probe_row3, text='Load Probe Params', command=self._image_load_probe_params_file).pack(side='left', padx=4)
        _status_label(probe_section, self._image_probe_status_var)
        _status_label(probe_section, self._image_pixel_stage_affine_status_var)


        # -- DXF-layout alignment, plus a schematic plot of the raw loaded
        # layout geometry (to the right of the controls, independent of
        # whether a camera transform has been fit yet). ---------------------
        layout_section = _section('DXF-Layout Alignment', row=2, column=0, columnspan=2)
        layout_section.columnconfigure(0, weight=2)
        layout_section.columnconfigure(1, weight=1)
        layout_controls = tk.Frame(layout_section, bg=CLR_BG)
        layout_controls.grid(row=0, column=0, sticky='nsew')

        layout_row = tk.Frame(layout_controls, bg=CLR_BG)
        layout_row.pack(fill='x', padx=6, pady=(6, 2))
        ttk.Button(layout_row, text='Load DXF Layout', command=self._image_load_dxf_layout).pack(side='left', padx=2)
        ttk.Button(layout_row, text='Load Circle Params', command=self._image_load_circle_params_file).pack(side='left', padx=2)
        ttk.Button(layout_row, text='Tune Circle Params', command=self._image_open_circle_tuning_window).pack(side='left', padx=2)

        align_row = tk.Frame(layout_controls, bg=CLR_BG)
        align_row.pack(fill='x', padx=6, pady=2)
        self._image_draw_circles_button = ttk.Button(
            align_row, text='Draw Electrode Circles', width=20,
            command=self._image_toggle_layout_draw_mode,
        )
        self._image_draw_circles_button.pack(side='left', padx=4)
        ttk.Button(align_row, text='Undo Last Circle', command=self._image_undo_last_drawn_circle).pack(side='left', padx=4)
        ttk.Button(align_row, text='Clear Circles', command=self._image_clear_drawn_circles).pack(side='left', padx=4)

        fit_row = tk.Frame(layout_controls, bg=CLR_BG)
        fit_row.pack(fill='x', padx=6, pady=2)
        ttk.Button(fit_row, text='Fit Transform', command=self._image_fit_layout_alignment).pack(side='left', padx=4)
        ttk.Button(fit_row, text='Accept Alignment', command=self._image_accept_layout_alignment).pack(side='left', padx=4)
        ttk.Button(fit_row, text='Save Alignment', command=self._image_save_electrode_alignment).pack(side='left', padx=4)
        ttk.Button(fit_row, text='Load Alignment', command=self._image_load_electrode_alignment).pack(side='left', padx=4)

        pair_row = tk.Frame(layout_controls, bg=CLR_BG)
        pair_row.pack(fill='x', padx=6, pady=2)
        tk.Label(pair_row, text='Pair: drawn circle #', bg=CLR_BG).pack(side='left')
        self._image_layout_pair_drawn_var = _ThreadSafeStringVar(value='')
        tk.Entry(pair_row, textvariable=self._image_layout_pair_drawn_var, width=5).pack(side='left', padx=(4, 10))
        tk.Label(pair_row, text='<-> layout electrode #', bg=CLR_BG).pack(side='left')
        self._image_layout_pair_combo = ttk.Combobox(pair_row, values=(), width=28, state='readonly')
        self._image_layout_pair_combo.pack(side='left', padx=(4, 6))
        ttk.Button(pair_row, text='Confirm Pair', command=self._image_confirm_layout_pair).pack(side='left', padx=4)
        ttk.Button(pair_row, text='Undo Last Pair', command=self._image_undo_last_layout_pair).pack(side='left', padx=4)

        nudge_row = tk.Frame(layout_controls, bg=CLR_BG)
        nudge_row.pack(fill='x', padx=6, pady=2)
        tk.Label(nudge_row, text='scale x', bg=CLR_BG).pack(side='left')
        tk.Scale(
            nudge_row, from_=-50, to=50, orient='horizontal', length=110,
            variable=self._image_layout_nudge_scale_x_var, command=lambda _v: self._image_apply_layout_nudge(),
        ).pack(side='left', padx=(2, 8))
        tk.Label(nudge_row, text='scale y', bg=CLR_BG).pack(side='left')
        tk.Scale(
            nudge_row, from_=-50, to=50, orient='horizontal', length=110,
            variable=self._image_layout_nudge_scale_y_var, command=lambda _v: self._image_apply_layout_nudge(),
        ).pack(side='left', padx=(2, 8))
        tk.Label(nudge_row, text='angle', bg=CLR_BG).pack(side='left')
        tk.Scale(
            nudge_row, from_=-100, to=100, orient='horizontal', length=110,
            variable=self._image_layout_nudge_angle_var, command=lambda _v: self._image_apply_layout_nudge(),
        ).pack(side='left', padx=(2, 8))
        tk.Label(nudge_row, text='translate x', bg=CLR_BG).pack(side='left')
        tk.Scale(
            nudge_row, from_=-100, to=100, orient='horizontal', length=110,
            variable=self._image_layout_nudge_translate_x_var, command=lambda _v: self._image_apply_layout_nudge(),
        ).pack(side='left', padx=(2, 8))
        tk.Label(nudge_row, text='translate y', bg=CLR_BG).pack(side='left')
        tk.Scale(
            nudge_row, from_=-100, to=100, orient='horizontal', length=110,
            variable=self._image_layout_nudge_translate_y_var, command=lambda _v: self._image_apply_layout_nudge(),
        ).pack(side='left', padx=(2, 8))

        nudge_row2 = tk.Frame(layout_controls, bg=CLR_BG)
        nudge_row2.pack(fill='x', padx=6, pady=2)
        tk.Label(nudge_row2, text='shear x', bg=CLR_BG).pack(side='left')
        tk.Scale(
            nudge_row2, from_=-100, to=100, orient='horizontal', length=110,
            variable=self._image_layout_nudge_shear_x_var, command=lambda _v: self._image_apply_layout_nudge(),
        ).pack(side='left', padx=(2, 8))
        tk.Label(nudge_row2, text='shear y', bg=CLR_BG).pack(side='left')
        tk.Scale(
            nudge_row2, from_=-100, to=100, orient='horizontal', length=110,
            variable=self._image_layout_nudge_shear_y_var, command=lambda _v: self._image_apply_layout_nudge(),
        ).pack(side='left', padx=(2, 8))
        _status_label(layout_controls, self._image_layout_status_var)
        _status_label(layout_controls, self._image_layout_alignment_status_var)

        self._image_dxf_layout_preview_canvas = tk.Canvas(
            layout_section, bg='white', highlightthickness=1, highlightbackground='#b0bec5',
            width=400, height=220,
        )
        self._image_dxf_layout_preview_canvas.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self._image_dxf_layout_preview_canvas.bind(
            '<Configure>', lambda _e: self._image_draw_dxf_layout_preview()
        )

        # -- Z Contact Search parameters, plus Electrode Z-Seed controls
        # (merged in: both Seed Z buttons run this same search, reading the
        # same self._contact_search StringVars, so they belong with the
        # parameters they use rather than in a separate box). --------------
        # Mirrors layout_section's own layout_controls (left, weight=2) /
        # preview canvas (right, weight=1) split exactly: a single left
        # controls frame holding every row (fields, buttons, AND the status
        # bars, all pack(fill='x') so they're sized to the controls
        # column's own width rather than spanning the whole section), and
        # the preview canvas gridded directly into the section at column=1
        # so it fills its own column instead of floating at a fixed size.
        contact_section = _section('Z Contact Search', row=3, column=0, columnspan=2)
        contact_section.columnconfigure(0, weight=2)
        contact_section.columnconfigure(1, weight=1)
        contact_left = tk.Frame(contact_section, bg=CLR_BG)
        contact_left.grid(row=0, column=0, sticky='nsew')

        contact_fields_frame = tk.Frame(contact_left, bg=CLR_BG)
        contact_fields_frame.pack(fill='x', padx=6, pady=(6, 2))
        contact_fields = [
            ('Start offset (mm)', 'start_offset'),
            ('Step (mm)', 'step_mm'),
            ('Max beyond seed (mm)', 'max_beyond_seed_mm'),
            ('OCV threshold (V)', 'ocv_threshold'),
            ('Settle per step (s)', 'settle_s'),
            ('Engage extra (mm)', 'engage_mm'),
            ('Target XY tolerance (mm)', 'target_xy_tolerance_mm'),
        ]
        for row, (label, key) in enumerate(contact_fields):
            col = (row % 3) * 2
            line = row // 3
            tk.Label(contact_fields_frame, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=line, column=col, sticky='w', pady=4, padx=(0, 6))
            tk.Entry(contact_fields_frame, textvariable=self._contact_search[key], width=12,
                     font=('Courier New', 10)).grid(
                     row=line, column=col + 1, sticky='w', pady=4, padx=(0, 14))

        contact_btn_col = tk.Frame(contact_fields_frame, bg=CLR_BG)
        contact_btn_col.grid(row=0, column=6, rowspan=3, sticky='ns', padx=(14, 0))
        self._image_search_z_button = ttk.Button(
            contact_btn_col, text='Search Z', width=10,
            command=lambda: self._run_manual_action(self._image_seed_z_from_contact),
        )
        self._image_search_z_button.pack(side='top', fill='x', padx=4, pady=2)

        z_seed_controls_row = tk.Frame(contact_left, bg=CLR_BG)
        z_seed_controls_row.pack(fill='x', padx=6, pady=2)
        ttk.Button(
            z_seed_controls_row, text='Save Z Seed', command=self._image_save_z_seed_store,
        ).pack(side='left', padx=4)
        ttk.Button(
            z_seed_controls_row, text='Load Z Seed', command=self._image_load_z_seed_store,
        ).pack(side='left', padx=4)
        ttk.Button(
            z_seed_controls_row, text='Clear Z Seed', command=self._image_clear_z_seed_store,
        ).pack(side='left', padx=4)

        _status_label(contact_left, self._image_z_seed_status_var)
        _status_label(contact_left, self._image_z_seed_ocv_var)
        _status_label(contact_left, self._image_parallax_debug_var)

        # Same bordered-box-with-centered-placeholder-text style as
        # self._image_dxf_layout_preview_canvas ("No layout loaded"), and
        # gridded the same way -- directly into the section at column=1,
        # sticky='nsew', so it fills/stretches with its column instead of
        # sitting at a fixed size inside a wrapper frame.
        self._image_parallax_panel_canvas = tk.Canvas(
            contact_section, bg='white', highlightthickness=1, highlightbackground='#b0bec5',
            width=400, height=190,
        )
        self._image_parallax_panel_canvas.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self._image_parallax_panel_canvas.bind(
            '<Configure>', lambda _e: self._image_update_parallax_debug_display()
        )
        self._image_update_parallax_debug_display()

        tk.Label(
            f, textvariable=self._image_hint_var, bg=CLR_BG,
            anchor='w', justify='left', font=('Segoe UI', 10), fg='#546e7a'
        ).grid(row=4, column=0, columnspan=2, sticky='ew', padx=8, pady=(8, 6))

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

        # Scope the wheel binding to only be active while the cursor is over
        # this tab's canvas -- see the matching comment in _build_tab_image;
        # a plain bind_all would otherwise have the two tabs' scroll canvases
        # fight over the single global <MouseWheel> binding.
        def _manual_canvas_enter(_event):
            self._manual_scroll_canvas.bind_all('<MouseWheel>', _manual_mousewheel)

        def _manual_canvas_leave(_event):
            self._manual_scroll_canvas.unbind_all('<MouseWheel>')

        self._manual_scroll_canvas.bind('<Enter>', _manual_canvas_enter)
        self._manual_scroll_canvas.bind('<Leave>', _manual_canvas_leave)

        top = tk.Frame(f, bg=CLR_BG)
        top.pack(fill='both', expand=True, padx=8, pady=8)
        top.columnconfigure(0, weight=2)
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
        for row in range(len(current_fields)):
            current.rowconfigure(row, weight=1)

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
        # current now grows taller than target's own field rows need (the
        # history graphs to its right), and grid stretches target's outer
        # LabelFrame to match that row height -- give target's own rows
        # weight so their extra vertical space is distributed evenly across
        # all fields instead of leaving one large gap below the last entry.
        for row in range(len(target_fields)):
            target.rowconfigure(row, weight=1)

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
        self._manual_move_tip_button = ttk.Button(
            btns, text='Move Tip',
            command=lambda: self._run_manual_action(self._manual_move_stage)
        )
        self._manual_move_tip_button.pack(side='left', padx=4)
        ttk.Button(btns, text='Set Gas',
                   command=lambda: self._run_manual_action(self._manual_set_gas)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Apply All',
                   command=lambda: self._run_manual_action(self._manual_apply_all)
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

        quick = tk.LabelFrame(quick_row, text='EIS', bg=CLR_BG, padx=10, pady=10)
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
        ttk.Button(quick, text='Run EIS',
                   command=lambda: self._run_manual_action(self._manual_run_quick_eis)
                   ).grid(row=5, column=0, sticky='w', pady=(4, 0))

        quick_rapid = tk.LabelFrame(quick_row, text='Hybrid CA/EIS', bg=CLR_BG, padx=10, pady=10)
        quick_rapid.grid(row=0, column=1, sticky='nsew', padx=(6, 0))

        quick_rapid_fields = [
            ('Label', 'label'),
            ('Vdc (V)', 'v_dc'),
            ('dV (mV)', 'dv_mv'),
            ('PEIS points', 'n_pts'),
            ('Pre-hold (s)', 'hold_time'),
            ('Post-hold (s)', 'post_hold_time'),
            ('CA duration (s)', 'ca_duration'),
            ('CA dt (s)', 'ca_dt'),
            ('CA I Range', 'ca_i_range'),
            ('CA bandwidth', 'ca_bandwidth'),
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

        quick_rapid_next_row = (len(quick_rapid_fields) + 1) // 2
        tk.Label(
            quick_rapid,
            text='Saved under Run / Monitor result folder -> Manual Rapid EIS.',
            bg=CLR_BG,
            anchor='w',
            font=('Segoe UI', 9),
            fg='#555',
        ).grid(row=quick_rapid_next_row, column=0, columnspan=4, sticky='w', pady=(2, 6))
        ttk.Button(quick_rapid, text='Run Hybrid CA/EIS',
                   command=lambda: self._run_manual_action(self._manual_run_quick_rapid_eis)
                   ).grid(row=quick_rapid_next_row + 1, column=0, sticky='w', pady=(4, 0))

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

        graphs_row = tk.Frame(f, bg=CLR_BG)
        graphs_row.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self._manual_temp_canvas = tk.Canvas(
            graphs_row, bg='white', highlightthickness=1, highlightbackground='#b0bec5',
            height=260,
        )
        self._manual_temp_canvas.pack(side='left', fill='both', expand=True, padx=(0, 6))
        self._manual_gas_canvas = tk.Canvas(
            graphs_row, bg='white', highlightthickness=1, highlightbackground='#b0bec5',
            height=260,
        )
        self._manual_gas_canvas.pack(side='left', fill='both', expand=True, padx=(6, 0))

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
        # Gold-highlighted while a move is in progress, same as Search Z --
        # there's no reliable way to interrupt a move once started (see
        # _manual_stop_stage's removal), so seeing that one is still
        # running is the only feedback available. Both tabs' copies of the
        # button reflect this, since either can trigger the same move.
        self._image_set_mode_button_active(self._manual_move_tip_button, True)
        self._image_set_mode_button_active(self._image_move_tip_button, True)
        try:
            self._manual_status_var.set('Moving tip to target position')
            current_positions = {}
            for axis in ('X', 'Y', 'Z'):
                try:
                    current_positions[axis] = float(self.motor.get_position(axis))
                except Exception:
                    current_positions[axis] = None
            self.motor.move_xyz_safe(
                x_mm=float(self._manual_target['x'].get()),
                y_mm=float(self._manual_target['y'].get()),
                z_mm=float(self._manual_target['z'].get()),
                current_positions=current_positions,
                log_fn=self._log,
            )
            self._manual_refresh_state()
            self._manual_status_var.set('Tip move complete')
            # "Move Tip" is the only move path now (the separate "Move Tip to
            # Target" button was redundant with it). If an Image Monitor target
            # is locked, this move only counts as having moved to it when the
            # driven XY still matches that target's position -- if the Move to:
            # fields were edited away from the locked target before clicking
            # Move Tip, the lock is stale, so clear it automatically instead of
            # needing a separate Clear Target button.
            if getattr(self, '_image_selected_target', None) is not None:
                self._image_note_manual_move_target_match(
                    float(self._manual_target['x'].get()),
                    float(self._manual_target['y'].get()),
                )
        finally:
            self._image_set_mode_button_active(self._manual_move_tip_button, False)
            self._image_set_mode_button_active(self._image_move_tip_button, False)

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
                                  message=f'EIS running ({cycles} cycle(s))')
        self._manual_status_var.set(f'Running EIS: {label} ({cycles} cycle(s))')
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
                    f'Running EIS {cycle_idx}/{cycles}: {cycle_label}'
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
                f'EIS {status}: {completed}/{cycles} cycle(s), summary={summary_path}'
            )
        except Exception as exc:
            self._queue_monitor_event('error', label=label, message='EIS failed')
            self._manual_status_var.set(f'EIS failed: {exc}')
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
        ca_dt = float(self._quick_rapid['ca_dt'].get())
        ca_i_range = self._quick_rapid['ca_i_range'].get().strip() or BIOLOGIC_OLECOM_CA_I_RANGE
        ca_bandwidth = self._quick_rapid['ca_bandwidth'].get().strip() or str(BIOLOGIC_OLECOM_CA_BANDWIDTH)
        n_pts = int(float(self._quick_rapid['n_pts'].get()))
        peis_f_high = float(self._quick_rapid['peis_f_high'].get())
        peis_f_low = float(self._quick_rapid['peis_f_low'].get())
        measured_settings = {
            'v_dc': v_dc,
            'dv_mv': dv_v * 1000.0,
            'hold_time': hold_time,
            'post_hold_time': post_hold_time,
            'ca_duration': ca_duration,
            'ca_dt': ca_dt,
            'ca_i_range': ca_i_range,
            'ca_bandwidth': ca_bandwidth,
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
                                  message=f'Hybrid CA/EIS running ({cycles} cycle(s))')
        self._manual_status_var.set(f'Running Hybrid CA/EIS: {label} ({cycles} cycle(s))')
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
                    f'Running Hybrid CA/EIS {cycle_idx}/{cycles}: {cycle_label}'
                )
                row = {
                    'Label': cycle_label,
                    'V_dc': v_dc,
                    'dV': dv_v,
                    'HoldTime_s': hold_time,
                    'PostPEIS_HoldTime_s': post_hold_time,
                    'CA_duration_s': ca_duration,
                    'CA_dt': ca_dt,
                    'CA_IRange': ca_i_range,
                    'CA_Bandwidth': ca_bandwidth,
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
                    f"hold={hold_time:.1f}s, post={post_hold_time:.1f}s, "
                    f"CA={ca_duration:.1f}s (dt={ca_dt:.4g}s, IRange={ca_i_range}, BW={ca_bandwidth}), "
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
                f'Hybrid CA/EIS {status}: {completed}/{cycles} cycle(s), summary={summary_path}'
            )
        except Exception as exc:
            self._queue_monitor_event('error', label=label, message='Hybrid CA/EIS failed')
            self._manual_status_var.set(f'Hybrid CA/EIS failed: {exc}')
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

    def _schedule_manual_history_poll(self):
        """
        Periodically samples temperature and gas flow into
        self._manual_history_points, feeding the Manual Control tab's
        Current State history graphs. Runs continuously in the background
        (including during automated runs -- unlike the OCV poll, this reads
        the temperature controller/MFC, not the BioLogic channel, so it
        can't interfere with an in-progress measurement) so history is
        already built up whenever the tab is viewed.
        """
        if self._manual_history_poll_shutdown:
            return
        if not self._manual_history_poll_inflight and (self.tc is not None or self.mfc is not None):
            self._manual_history_poll_inflight = True
            threading.Thread(
                target=self._manual_history_poll_worker,
                name='manual_history_poll',
                daemon=True,
            ).start()
        self.after(MANUAL_HISTORY_POLL_INTERVAL_MS, self._schedule_manual_history_poll)

    def _manual_history_poll_worker(self):
        temp = None
        gas_a = None
        gas_b = None
        try:
            if self.tc:
                temp = float(self.tc.get_temperature())
        except Exception:
            temp = None
        try:
            if self.mfc:
                gas_a = float(self.mfc.get_flow('A'))
                gas_b = float(self.mfc.get_flow('B'))
        except Exception:
            gas_a = None
            gas_b = None
        try:
            self.after(0, lambda: self._manual_history_poll_complete(temp, gas_a, gas_b))
        except Exception:
            self._manual_history_poll_inflight = False

    def _manual_history_poll_complete(self, temp, gas_a, gas_b):
        self._manual_history_poll_inflight = False
        now = time.time()
        if self._manual_history_start_time is None:
            self._manual_history_start_time = now
        elapsed = now - self._manual_history_start_time
        for key, value in (('temp', temp), ('gas_a', gas_a), ('gas_b', gas_b)):
            if value is None:
                continue
            points = self._manual_history_points[key]
            points.append((elapsed, value))
            if len(points) > MONITOR_TIME_SERIES_MAX_POINTS:
                del points[0]
        self._redraw_manual_history()

    def _redraw_manual_history(self):
        if hasattr(self, '_manual_temp_canvas'):
            self._draw_line_plot(
                self._manual_temp_canvas,
                self._manual_history_points['temp'],
                title='Temperature',
                x_label='Time (s)',
                y_label='Temperature (C)',
                line_color=CLR_BLUE,
                empty_text='Waiting for temperature readings...',
            )
        if hasattr(self, '_manual_gas_canvas'):
            # mfc.get_flow returns the raw 0-100 DMFC output setting, not a
            # calibrated sccm value -- see driver_mfc.py -- so the axis label
            # matches the "RFX output" wording already used on the current-
            # state readouts above, not a flow-rate unit.
            self._draw_line_plot(
                self._manual_gas_canvas,
                self._manual_history_points['gas_a'],
                title='Gas Output',
                x_label='Time (s)',
                y_label='Output setting (0-100)',
                line_color=CLR_GOLD,
                overlay_points=self._manual_history_points['gas_b'],
                overlay_color=CLR_GREEN,
                legend_entries=[('Gas A', CLR_GOLD), ('Gas B', CLR_GREEN)],
                empty_text='Waiting for gas flow readings...',
            )

    def _confirm_contact_candidate(
        self,
        *,
        ocv_threshold,
        status_prefix,
        stop_event=None,
        confirm_s=CONTACT_CONFIRM_DURATION_S,
        poll_s=CONTACT_CONFIRM_POLL_S,
        ocv_status_var=None,
    ):
        deadline = time.time() + float(confirm_s)
        samples = 0
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError('Contact search stopped')
            ocv = self._manual_update_ocv()
            samples += 1
            remaining = max(0.0, deadline - time.time())
            if ocv_status_var is not None:
                ocv_status_var.set(
                    f'OCV: {float(ocv):+.4f} V' if ocv is not None else 'OCV: unavailable'
                )
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
        max_beyond_seed_mm,
        ocv_threshold,
        settle_s,
        engage_mm,
        status_prefix='Contact search',
        stop_event=None,
        probe_pixel_sample_fn=None,
        ocv_status_var=None,
        z_status_var=None,
        track_manual_target_z=True,
    ):
        """
        probe_pixel_sample_fn, if given, is a zero-arg callable returning the
        probe tip's current detected (pixel_x, pixel_y) or None on detection
        failure. Called once right after moving to the search-start height
        and once right after contact is confirmed -- XY is never touched
        during this search, so the displacement between those two samples is
        a direct Z-parallax measurement. See run_automation.find_contact_z
        for the headless equivalent.

        track_manual_target_z controls whether the "Move to: Z" field
        (self._manual_target['z'], shared with Manual Control) is updated
        to reflect the search's own start/measurement heights as it runs.
        Default True matches every existing caller (Manual Control's "Find
        Contact Z" has no reason to ever suppress this). The Image Monitor
        tab's "Search Z" passes False when its "Lock Z" checkbox is on --
        this method has no notion of that checkbox itself (it's a target-lock
        concept specific to that tab, not something this shared search
        method should know about), so the caller decides.

        max_beyond_seed_mm both bounds how many steps the search tries (how
        far past the seed it's willing to look for contact) and is enforced
        as a hard safety limit on every step (how far past the seed it's
        ever allowed to actually move) -- these used to be two separately
        tunable numbers (a "max drop" search budget plus this safety bound),
        but driving the tip even ~0.5 mm too far into an electrode can
        damage it, so there is deliberately only one number to get right now.

        ocv_status_var, if given, is kept updated with the live OCV reading
        on every step and during the confirm phase -- used by the Image
        Monitor tab's Z Contact Search box, which otherwise shows nothing
        while a search is running (self._manual_status_var, updated below
        regardless, is only visible on the Manual Control tab).

        z_status_var, if given, is kept updated with the current step's Z
        position on every step -- lets the Image Monitor tab's "Z seed:"
        label (self._image_z_seed_status_var) show the search actively
        progressing instead of a static "searching..." for however long the
        search takes.
        """
        if self.motor is None:
            raise RuntimeError('Motor controller is not connected')
        if self.bl is None:
            raise RuntimeError('BioLogic is not connected')
        step_mm = abs(float(step_mm))
        if step_mm <= 0:
            raise RuntimeError('Contact step must be > 0 mm')
        max_beyond_seed_mm = abs(float(max_beyond_seed_mm))

        positive_z_is_up = bool(STAGE_SAFE_MOVE.get('positive_z_is_up', True))
        approach_sign = -1.0 if positive_z_is_up else 1.0
        start_z = base_z - approach_sign * abs(start_offset)
        self._manual_status_var.set(f'{status_prefix}: starting from Z={start_z:.3f} mm')
        self.motor.move_abs_wait('Z', start_z)
        if track_manual_target_z:
            self._manual_target['z'].set(f'{start_z:.3f}')
        self._manual_refresh_state()

        start_pixel = None
        start_roi_debug = None
        if probe_pixel_sample_fn is not None:
            try:
                start_pixel = probe_pixel_sample_fn()
            except Exception:
                start_pixel = None
            start_roi_debug = self._image_last_probe_roi_debug

        max_steps = max(1, int(round((abs(start_offset) + max_beyond_seed_mm) / step_mm)))
        found_contact = None

        for idx in range(max_steps + 1):
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError('Contact search stopped')
            z_here = start_z + approach_sign * idx * abs(step_mm)
            beyond_seed = approach_sign * (z_here - float(base_z))
            if beyond_seed > max_beyond_seed_mm + CONTACT_SAFETY_LOCK_EPSILON_MM:
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
            if ocv_status_var is not None:
                ocv_status_var.set(
                    f'OCV: {ocv:+.4f} V' if ocv is not None else 'OCV: unavailable'
                )
            if z_status_var is not None:
                z_status_var.set(f'Z seed: searching (Z={z_here:.3f} mm)')
            if ocv is not None and abs(ocv) <= ocv_threshold:
                self._manual_status_var.set(
                    f'{status_prefix}: contact candidate at Z={z_here:.3f} mm; '
                    f'confirming for {CONTACT_CONFIRM_DURATION_S:.0f}s'
                )
                if self._confirm_contact_candidate(
                    ocv_threshold=ocv_threshold,
                    status_prefix=status_prefix,
                    stop_event=stop_event,
                    ocv_status_var=ocv_status_var,
                ):
                    found_contact = z_here
                    break

        if found_contact is None:
            if probe_pixel_sample_fn is not None:
                self._image_last_parallax_debug = {
                    'start_roi': start_roi_debug, 'contact_roi': None,
                    'start_pixel': start_pixel, 'contact_pixel': None,
                    'start_z': start_z, 'contact_z': None,
                    'accepted': False, 'reject_reason': 'contact not confirmed',
                }
                # _execute_contact_z_search runs on a background worker
                # thread (via _run_manual_action) -- Tkinter widget/image
                # updates must be marshaled back onto the main thread via
                # self.after, same as every other cross-thread UI update in
                # this file. Without this the PhotoImage silently fails to
                # render (the plain StringVar status text still updates
                # fine, which is why only the image looked broken).
                self.after(0, self._image_update_parallax_debug_display)
            raise ContactNotConfirmedError(
                'Contact search did not find a valid OCV threshold', last_z=z_here,
            )

        contact_pixel = None
        contact_roi_debug = None
        if probe_pixel_sample_fn is not None:
            try:
                contact_pixel = probe_pixel_sample_fn()
            except Exception:
                contact_pixel = None
            contact_roi_debug = self._image_last_probe_roi_debug

        measure_z = found_contact + approach_sign * abs(engage_mm)
        self.motor.move_abs_wait('Z', measure_z)
        if track_manual_target_z:
            self._manual_target['z'].set(f'{measure_z:.3f}')
        self._manual_refresh_state()
        self._manual_status_var.set(
            f'Contact found at Z={found_contact:.3f} mm, measurement Z set to {measure_z:.3f} mm'
        )
        parallax_sample = None
        reject_reason = None
        if start_pixel is None or contact_pixel is None:
            reject_reason = 'probe tip not detected at start and/or contact'
        else:
            delta_px = contact_pixel[0] - start_pixel[0]
            delta_py = contact_pixel[1] - start_pixel[1]
            delta_mag = (delta_px ** 2 + delta_py ** 2) ** 0.5
            min_delta = getattr(_config, 'VISION_PARALLAX_MIN_SAMPLE_PIXEL_DELTA_PX', 2.0)
            if delta_mag < min_delta:
                reject_reason = f'displacement {delta_mag:.2f}px < {min_delta:.2f}px'
            else:
                # No fixed expected sign here -- which way the tracked pixel
                # shifts with Z depends on camera orientation (e.g. flips
                # sign under a 180-degree camera rotation), not just probe
                # geometry, so the origin-constrained slope fit downstream
                # (solve_parallax_slope_from_samples) is the thing that
                # should discover the true sign from the data, not a
                # hardcoded gate here rejecting whichever sign doesn't match
                # a specific past setup.
                parallax_sample = (start_z, start_pixel, found_contact, contact_pixel)
        if probe_pixel_sample_fn is not None:
            self._image_last_parallax_debug = {
                'start_roi': start_roi_debug, 'contact_roi': contact_roi_debug,
                'start_pixel': start_pixel, 'contact_pixel': contact_pixel,
                'start_z': start_z, 'contact_z': found_contact,
                'accepted': parallax_sample is not None, 'reject_reason': reject_reason,
            }
            # See the ContactNotConfirmedError branch above for why this
            # must go through self.after rather than being called directly.
            self.after(0, self._image_update_parallax_debug_display)
        return found_contact, measure_z, parallax_sample

    def _manual_find_contact_z(self):
        try:
            self._execute_contact_z_search(
            base_z=float(self._manual_target['z'].get()),
            start_offset=float(self._contact_search['start_offset'].get()),
            step_mm=float(self._contact_search['step_mm'].get()),
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

    def _parse_int_ranges(self, text):
        """
        Parse a comma/newline-separated list of ints and inclusive
        hyphen-ranges (e.g. "1, 3, 5-8" -> {1, 3, 5, 6, 7, 8}) -- same
        tokenizing style as _parse_number_list, extended with range
        expansion. Returns an empty set for blank/whitespace-only input
        (callers treat that as "no filter", not "select nothing").
        Raises ValueError on a malformed token.
        """
        result = set()
        for token in str(text).replace('\n', ',').split(','):
            token = token.strip()
            if not token:
                continue
            if '-' in token[1:]:
                # token[1:] so a leading '-' (a negative number, not used
                # here but keeps this robust) isn't mistaken for a range.
                lo_text, _, hi_text = token.partition('-')
                lo, hi = int(lo_text.strip()), int(hi_text.strip())
                if hi < lo:
                    raise ValueError(f"Invalid range '{token}': end is before start.")
                result.update(range(lo, hi + 1))
            else:
                result.add(int(token))
        return result

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

    def _image_refresh_semi_auto_exclude_diameter_checkboxes(self):
        """
        Rebuilds the "Exclude by diameter" checkboxes from the currently
        loaded DXF layout -- one checkbox per distinct electrode diameter
        present (electrode diameters cluster into a handful of size
        classes, e.g. large sensing vs. small reference electrodes, so a
        fixed small set of checkboxes is a better fit than a free-text
        field the user would have to know exact values for). User-
        triggered (not auto-refreshed on layout load) so it's obvious when
        the list might be stale after loading a different layout.
        """
        for child in self._semi_auto_exclude_diameter_frame.winfo_children():
            child.destroy()
        layout = self._image_layout_model
        if layout is None or layout.template_radii is None:
            self._semi_auto_exclude_diameter_vars = {}
            tk.Label(
                self._semi_auto_exclude_diameter_frame,
                text='(load a DXF layout with per-circle radii first)',
                bg=CLR_BG, fg='#888',
            ).pack(side='left')
            return
        diameters_um = sorted({
            round(float(r) * 2.0 * 1000.0, 1) for r in layout.template_radii
        })
        new_vars = {}
        for diameter_um in diameters_um:
            var = self._semi_auto_exclude_diameter_vars.get(diameter_um) or _ThreadSafeBooleanVar(value=False)
            new_vars[diameter_um] = var
            label = f'{diameter_um:g} um'
            ttk.Checkbutton(
                self._semi_auto_exclude_diameter_frame, text=label, variable=var,
            ).pack(side='left', padx=(0, 8))
        self._semi_auto_exclude_diameter_vars = new_vars

    def _image_layout_indices_for_excluded_diameters(self):
        """0-based layout indices whose diameter matches a checked box in
        the "Exclude by diameter" row, or an empty set if none are
        checked/no layout is loaded."""
        layout = self._image_layout_model
        if layout is None or layout.template_radii is None:
            return set()
        checked_diameters = {
            diameter_um for diameter_um, var in self._semi_auto_exclude_diameter_vars.items()
            if var.get()
        }
        if not checked_diameters:
            return set()
        excluded = set()
        for layout_index, r in enumerate(layout.template_radii):
            diameter_um = round(float(r) * 2.0 * 1000.0, 1)
            if diameter_um in checked_diameters:
                excluded.add(layout_index)
        return excluded

    def _generate_semi_auto_conditions(self, append=False):
        try:
            use_temp = self._semi_auto_use['temperature'].get()
            use_gas = self._semi_auto_use['gas'].get()
            use_tip = self._semi_auto_use['tip'].get()
            use_ca = self._semi_auto_use['ca'].get()
            tip_source = self._semi_auto_tip_source_var.get()
            temperatures = (
                self._parse_number_list(self._semi_auto['temperatures'].get(), float)
                if use_temp else [None]
            )
            voltages = self._parse_number_list(
                self._semi_auto['voltages'].get(), float
            )
            gas_pairs = self._parse_gas_pairs(self._semi_auto['gas_pairs'].get()) if use_gas else [(None, None)]
            e_start = int(float(self._semi_auto['electrode_start'].get())) if use_tip else 1
            e_end = int(float(self._semi_auto['electrode_end'].get())) if use_tip else 1
            x1 = float(self._semi_auto['x1'].get()) if use_tip else None
            y1 = float(self._semi_auto['y1'].get()) if use_tip else None
            xn = float(self._semi_auto['xn'].get()) if use_tip else None
            yn = float(self._semi_auto['yn'].get()) if use_tip else None
            z1 = float(self._semi_auto['z1'].get()) if use_tip else None
            zn = float(self._semi_auto['zn'].get()) if use_tip else None
            auto_contact_z = 1 if (
                use_tip and (
                    tip_source == 'image_monitor'
                    or _parse_boolish(self._semi_auto['auto_contact_z'].get(), default=True)
                )
            ) else 0
            contact_start_offset = float(self._semi_auto['contact_start_offset'].get()) if use_tip else 0.200
            contact_step = float(self._semi_auto['contact_step'].get()) if use_tip else 0.005
            contact_max_beyond_seed = float(self._semi_auto['contact_max_beyond_seed'].get()) if use_tip else 0.200
            contact_ocv_threshold = float(self._semi_auto['contact_ocv_threshold'].get()) if use_tip else 0.100
            contact_settle = float(self._semi_auto['contact_settle'].get()) if use_tip else 0.30
            contact_engage = float(self._semi_auto['contact_engage'].get()) if use_tip else 0.01
            dv = float(self._semi_auto['dv'].get())
            hold_time = float(self._semi_auto['hold_time'].get()) if use_ca else None
            post_peis_hold_time = float(self._semi_auto['post_peis_hold_time'].get()) if use_ca else None
            peis_f_high = float(self._semi_auto['peis_f_high'].get())
            peis_f_low = float(self._semi_auto['peis_f_low'].get())
            peis_n_pts = int(float(self._semi_auto['peis_n_pts'].get()))
            peis_bandwidth = self._semi_auto['peis_bandwidth'].get().strip()
            peis_n_average = int(float(self._semi_auto['peis_n_average'].get()))
            ca_duration = float(self._semi_auto['ca_duration'].get()) if use_ca else None
            ca_dt = float(self._semi_auto['ca_dt'].get()) if use_ca else None
            ca_i_range = self._semi_auto['ca_i_range'].get().strip()
            ca_bandwidth = self._semi_auto['ca_bandwidth'].get().strip()
            temp_ramp_rate = float(self._semi_auto['temp_ramp_rate'].get()) if use_temp else None
            stable_time = float(self._semi_auto['stable_time'].get()) if use_temp else None
            gas_stable_time = float(self._semi_auto['gas_stable_time'].get()) if use_gas else None
        except ValueError as exc:
            messagebox.showerror("Full-auto input error", str(exc))
            return

        if not voltages:
            messagebox.showwarning(
                "Full-auto input",
                "Voltage list must not be empty."
            )
            return
        if use_tip and tip_source == 'manual' and e_end < e_start:
            messagebox.showwarning(
                "Electrode range",
                "Electrode end must be greater than or equal to electrode start."
            )
            return

        if use_tip and tip_source == 'image_monitor':
            try:
                omit_1based = self._parse_int_ranges(
                    self._semi_auto_image_monitor_omit_electrodes_var.get()
                )
            except ValueError as exc:
                messagebox.showerror("Full-auto input error", f"Invalid electrode selection: {exc}")
                return
            omit_layout_indices = {i - 1 for i in omit_1based} if omit_1based else set()
            omit_layout_indices = omit_layout_indices | self._image_layout_indices_for_excluded_diameters()
            omit_layout_indices = omit_layout_indices or None
            try:
                seeded = self._image_seeded_electrode_rows_source(omit_layout_indices=omit_layout_indices)
            except RuntimeError as exc:
                messagebox.showerror("Full-auto input error", str(exc))
                return
            electrode_positions = [(layout_index + 1, x, y, z) for layout_index, x, y, z in seeded]
        elif use_tip:
            electrode_ids = list(range(e_start, e_end + 1))
            count = len(electrode_ids)
            electrode_positions = []
            for electrode in electrode_ids:
                frac = 0.0 if count == 1 else (electrode - e_start) / (e_end - e_start)
                electrode_positions.append((
                    electrode,
                    x1 + frac * (xn - x1),
                    y1 + frac * (yn - y1),
                    z1 + frac * (zn - z1),
                ))
        else:
            electrode_positions = [(None, None, None, None)]

        rows = []
        for temp in temperatures:
            for gas_a, gas_b in gas_pairs:
                for electrode, x_pos, y_pos, z_pos in electrode_positions:
                    for v_dc in voltages:
                        label = _condition_label(
                            temperature=temp,
                            gas_a=gas_a,
                            gas_b=gas_b,
                            electrode=electrode,
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
                            'ContactMaxBeyondSeed_mm': contact_max_beyond_seed,
                            'ContactOCVThreshold_V': contact_ocv_threshold,
                            'ContactSettle_s': contact_settle,
                            'ContactEngage_mm': contact_engage,
                            'V_dc': v_dc,
                            'dV': dv,
                            'HoldTime_s': hold_time if hold_time is not None else 'None',
                            'PostPEIS_HoldTime_s': post_peis_hold_time if post_peis_hold_time is not None else 'None',
                            'PEIS_fHigh': peis_f_high,
                            'PEIS_fLow': peis_f_low,
                            'PEIS_nPts': peis_n_pts,
                            'PEIS_Bandwidth': peis_bandwidth if peis_bandwidth else 'None',
                            'PEIS_NAverage': peis_n_average,
                            'SkipCA': 0 if use_ca else 1,
                            'CA_duration_s': ca_duration if ca_duration is not None else 'None',
                            'CA_dt': ca_dt if ca_dt is not None else 'None',
                            'CA_IRange': ca_i_range if ca_i_range else 'None',
                            'CA_Bandwidth': ca_bandwidth if ca_bandwidth else 'None',
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
        self._semi_auto_summary.set(
            f'Generated {len(rows)} rows from '
            f'{len(temperatures)} temperatures × {len(gas_pairs)} gas pairs × '
            f'{len(electrode_positions)} electrodes × {len(voltages)} voltages'
        )
        self._log(f"[Full-auto] Generated {len(rows)} rows into Semi-auto table")

    def _generate_full_auto_conditions(self, append=False):
        try:
            use_temp = self._full_auto_use['temperature'].get()
            use_gas = self._full_auto_use['gas'].get()
            use_tip = self._full_auto_use['tip'].get()
            tip_source = self._full_auto_tip_source_var.get()
            temperatures = (
                self._parse_number_list(self._full_auto['temperatures'].get(), float)
                if use_temp else [None]
            )
            voltages = self._parse_number_list(
                self._full_auto['voltages'].get(), float
            )
            gas_pairs = (
                self._parse_gas_pairs(self._full_auto['gas_pairs'].get())
                if use_gas else [(None, None)]
            )
            e_start = int(float(self._full_auto['electrode_start'].get())) if use_tip else 1
            e_end = int(float(self._full_auto['electrode_end'].get())) if use_tip else 1
            x1 = float(self._full_auto['x1'].get()) if use_tip else None
            y1 = float(self._full_auto['y1'].get()) if use_tip else None
            xn = float(self._full_auto['xn'].get()) if use_tip else None
            yn = float(self._full_auto['yn'].get()) if use_tip else None
            z1 = float(self._full_auto['z1'].get()) if use_tip else None
            zn = float(self._full_auto['zn'].get()) if use_tip else None
            auto_contact_z = 1 if (
                use_tip and (
                    tip_source == 'image_monitor'
                    or _parse_boolish(self._full_auto['auto_contact_z'].get(), default=True)
                )
            ) else 0
            contact_start_offset = float(self._full_auto['contact_start_offset'].get()) if use_tip else 0.200
            contact_step = float(self._full_auto['contact_step'].get()) if use_tip else 0.005
            contact_max_beyond_seed = float(self._full_auto['contact_max_beyond_seed'].get()) if use_tip else 0.200
            contact_ocv_threshold = float(self._full_auto['contact_ocv_threshold'].get()) if use_tip else 0.100
            contact_settle = float(self._full_auto['contact_settle'].get()) if use_tip else 0.30
            contact_engage = float(self._full_auto['contact_engage'].get()) if use_tip else 0.01
            dv = float(self._full_auto['dv'].get())
            hold_time = float(self._full_auto['hold_time'].get())
            post_peis_hold_time = float(self._full_auto['post_peis_hold_time'].get())
            peis_f_high = float(self._full_auto['peis_f_high'].get())
            peis_f_low = float(self._full_auto['peis_f_low'].get())
            peis_n_pts = int(float(self._full_auto['peis_n_pts'].get()))
            peis_bandwidth = self._full_auto['peis_bandwidth'].get().strip()
            peis_n_average = int(float(self._full_auto['peis_n_average'].get()))
            ca_duration = float(self._full_auto['ca_duration'].get())
            ca_dt = float(self._full_auto['ca_dt'].get())
            ca_i_range = self._full_auto['ca_i_range'].get().strip()
            ca_bandwidth = self._full_auto['ca_bandwidth'].get().strip()
            temp_ramp_rate = float(self._full_auto['temp_ramp_rate'].get()) if use_temp else None
            stable_time = float(self._full_auto['stable_time'].get()) if use_temp else None
            gas_stable_time = float(self._full_auto['gas_stable_time'].get()) if use_gas else None
            normal_eis_floor_hz = float(self._full_auto['normal_eis_floor_hz'].get())
        except ValueError as exc:
            messagebox.showerror("Adaptive full-auto input error", str(exc))
            return

        if not voltages:
            messagebox.showwarning(
                "Adaptive full-auto input",
                "Voltage list must not be empty."
            )
            return
        if use_tip and tip_source == 'manual' and e_end < e_start:
            messagebox.showwarning(
                "Electrode range",
                "Electrode end must be greater than or equal to electrode start."
            )
            return

        if use_tip and tip_source == 'image_monitor':
            try:
                omit_1based = self._parse_int_ranges(
                    self._full_auto_image_monitor_omit_electrodes_var.get()
                )
            except ValueError as exc:
                messagebox.showerror("Adaptive full-auto input error", f"Invalid electrode selection: {exc}")
                return
            omit_layout_indices = {i - 1 for i in omit_1based} if omit_1based else None
            try:
                seeded = self._image_seeded_electrode_rows_source(omit_layout_indices=omit_layout_indices)
            except RuntimeError as exc:
                messagebox.showerror("Adaptive full-auto input error", str(exc))
                return
            electrode_positions = [(layout_index + 1, x, y, z) for layout_index, x, y, z in seeded]
        elif use_tip:
            electrode_ids = list(range(e_start, e_end + 1))
            count = len(electrode_ids)
            electrode_positions = []
            for electrode in electrode_ids:
                frac = 0.0 if count == 1 else (electrode - e_start) / (e_end - e_start)
                electrode_positions.append((
                    electrode,
                    x1 + frac * (xn - x1),
                    y1 + frac * (yn - y1),
                    z1 + frac * (zn - z1),
                ))
        else:
            electrode_positions = [(None, None, None, None)]

        rows = []
        for temp in temperatures:
            for gas_a, gas_b in gas_pairs:
                for electrode, x_pos, y_pos, z_pos in electrode_positions:
                    for v_dc in voltages:
                        label = _condition_label(
                            prefix='ADAPT',
                            temperature=temp,
                            gas_a=gas_a,
                            gas_b=gas_b,
                            electrode=electrode,
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
                            'PEIS_Bandwidth': peis_bandwidth if peis_bandwidth else 'None',
                            'PEIS_NAverage': peis_n_average,
                            'CA_duration_s': ca_duration,
                            'CA_dt': ca_dt,
                            'CA_IRange': ca_i_range if ca_i_range else 'None',
                            'CA_Bandwidth': ca_bandwidth if ca_bandwidth else 'None',
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
            f'Generated {len(rows)} adaptive rows '
            f'(normal EIS floor {normal_eis_floor_hz:g} Hz) from '
            f'{len(temperatures)} temperatures x {len(gas_pairs)} gas pairs x '
            f'{len(electrode_positions)} electrodes x {len(voltages)} voltages'
        )
        self._log(
            f"[Adaptive Full-auto] Generated {len(rows)} rows into Semi-auto table "
            f"(normal EIS floor {normal_eis_floor_hz:g} Hz; runtime will update later same-regime rows)"
        )

    # Fields that only apply to the "Manual range" tip-position source --
    # meaningless (and disabled) when "Image Monitor seeded electrodes" is
    # selected instead, since that mode walks every seeded layout electrode
    # rather than interpolating between two manually-typed endpoints, and
    # always forces AutoContactZ on (Z-seeding is required, not optional,
    # for that mode).
    _MANUAL_TIP_SOURCE_ONLY_FIELDS = [
        'electrode_start', 'electrode_end', 'x1', 'y1', 'xn', 'yn', 'z1', 'zn', 'auto_contact_z',
    ]

    def _update_semi_auto_field_states(self):
        tip_fields = [
            'z1', 'zn', 'auto_contact_z',
            'contact_start_offset', 'contact_step',
            'contact_max_beyond_seed', 'contact_ocv_threshold',
            'contact_settle', 'contact_engage',
            'electrode_start', 'electrode_end', 'x1', 'y1', 'xn', 'yn',
        ]
        groups = {
            'temperature': ['temperatures', 'temp_ramp_rate', 'stable_time'],
            'gas': ['gas_pairs', 'gas_stable_time'],
            'tip': tip_fields,
            'ca': ['hold_time', 'post_peis_hold_time', 'ca_duration', 'ca_dt', 'ca_i_range', 'ca_bandwidth'],
        }
        for key, fields in groups.items():
            enabled = self._semi_auto_use[key].get()
            state = 'normal' if enabled else 'disabled'
            for field in fields:
                entry = self._semi_auto_entries.get(field)
                if entry is not None:
                    entry.config(state=state)
        image_monitor_mode = self._semi_auto_tip_source_var.get() == 'image_monitor'
        if image_monitor_mode:
            self._semi_auto['auto_contact_z'].set('1')
            for field in self._MANUAL_TIP_SOURCE_ONLY_FIELDS:
                entry = self._semi_auto_entries.get(field)
                if entry is not None:
                    entry.config(state='disabled')
        self._semi_auto_image_monitor_omit_electrodes_entry.config(
            state='normal' if image_monitor_mode else 'disabled'
        )

    def _update_full_auto_field_states(self):
        tip_fields = [
            'z1', 'zn', 'auto_contact_z',
            'contact_start_offset', 'contact_step',
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
        image_monitor_mode = self._full_auto_tip_source_var.get() == 'image_monitor'
        if image_monitor_mode:
            self._full_auto['auto_contact_z'].set('1')
            for field in self._MANUAL_TIP_SOURCE_ONLY_FIELDS:
                entry = self._full_auto_entries.get(field)
                if entry is not None:
                    entry.config(state='disabled')
        self._full_auto_image_monitor_omit_electrodes_entry.config(
            state='normal' if image_monitor_mode else 'disabled'
        )

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
        # Only guards against zero/negative (which would break the MPS
        # technique) -- previously floored to 0.1s unconditionally, which
        # silently overrode any smaller value the user actually configured.
        dt_s = max(0.001, float(row.get('CA_dt', MANUAL_QUICK_CA_DT_S)))
        ca_bandwidth = row.get('CA_Bandwidth')
        if ca_bandwidth is None or pd.isna(ca_bandwidth) or str(ca_bandwidth).strip() in ('', 'None'):
            ca_bandwidth = BIOLOGIC_OLECOM_CA_BANDWIDTH
        else:
            ca_bandwidth = int(float(ca_bandwidth))
        ca_i_range = row.get('CA_IRange')
        if ca_i_range is None or pd.isna(ca_i_range) or str(ca_i_range).strip() in ('', 'None'):
            ca_i_range = BIOLOGIC_OLECOM_CA_I_RANGE
        else:
            ca_i_range = str(ca_i_range).strip()
        peis_high = float(row.get('PEIS_fHigh', 100.0))
        n_pts = int(float(row.get('PEIS_nPts', 60)))
        requested_low = float(row.get('PEIS_fLow', BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ))
        peis_bandwidth = row.get('PEIS_Bandwidth')
        if peis_bandwidth is None or pd.isna(peis_bandwidth) or str(peis_bandwidth).strip() in ('', 'None'):
            peis_bandwidth = BIOLOGIC_OLECOM_BANDWIDTH
        else:
            peis_bandwidth = int(float(peis_bandwidth))
        peis_n_average = row.get('PEIS_NAverage')
        if peis_n_average is None or pd.isna(peis_n_average) or str(peis_n_average).strip() in ('', 'None'):
            peis_n_average = 1
        else:
            peis_n_average = max(1, int(float(peis_n_average)))

        if dynamic_lf:
            # requested_low (the row's own PEIS_fLow, typed on the
            # Semi-auto/Full-auto tab or in a loaded CSV) is the floor the
            # adaptive engine's own LF recommendation is allowed to reach --
            # previously this always used the fixed BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ
            # default (0.1 Hz) instead, so a user-typed lower PEIS_fLow was
            # silently ignored on every OLE-COM Semi-auto/Full-auto row.
            # overlap_limit is unchanged: it still guarantees PEIS always
            # measures down to at least that frequency, regardless of what
            # the raw FFT recommendation alone would have chosen.
            peis_deep_limit = requested_low
            peis_overlap_limit = float(BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ)
        else:
            # Non-adaptive callers (Manual Quick Rapid, and Semi-auto CSV
            # rows as of the dynamic_lf routing fix above) should honor the
            # typed PEIS low frequency exactly, not just as a floor.
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
                    bandwidth=peis_bandwidth,
                    peis_n_average=peis_n_average,
                    ca_bandwidth=ca_bandwidth,
                    ca_i_range=ca_i_range,
                    save_dir=save_dir,
                    label=label,
                    stop_event=stop_event,
                    defer_postprocess=bool(defer_postprocess),
                    monitor_callback=self._queue_monitor_event,
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

    def _curate_row_result_artifacts(self, *, row, row_index, label, sequence_result, result_root):
        """
        Copies this row's final PEIS .txt/.mpr, plus a matching condition
        .json, out of its own per-row working folder into one flat,
        consistently-named set directly under result_root -- so every
        sample's key results are easy to find in one place regardless of
        which measurement protocol produced them (plain PEIS via
        measurement_sequence.py, or the OLE-COM hybrid CA/FFT-seeded
        protocol, which exposes the same info via sequence_result.summary
        instead of attributes -- see OleComSequenceResult in
        driver_biologic_olecom.py). Only ever copies -- the per-row
        working folder (row_output_dir) and everything already written
        there are untouched, so this is purely additive and safe to fail
        without affecting the run itself.
        """
        try:
            os.makedirs(result_root, exist_ok=True)
            electrode_id = self._extract_electrode_id(label)
            layout_index = electrode_id - 1 if electrode_id is not None else None
            diameter_um = self._image_layout_diameter_um_for_layout_index(layout_index)
            safe_label = _safe_path_part(label, fallback=f'row{row_index:03d}')
            diameter_part = f"_{diameter_um:.0f}um" if diameter_um is not None else ""
            ts = time.strftime('%Y%m%d_%H%M%S')
            base_name = f"{safe_label}{diameter_part}_{ts}"

            summary = getattr(sequence_result, 'summary', None) or {}
            txt_src = getattr(sequence_result, 'peis_path', None) or summary.get('peis_txt')
            mpr_src = getattr(sequence_result, 'peis_mpr_path', None) or summary.get('peis_mpr')

            curated_txt = curated_mpr = None
            if txt_src and os.path.exists(txt_src):
                curated_txt = os.path.join(result_root, f"{base_name}.txt")
                shutil.copy2(txt_src, curated_txt)
            if mpr_src and os.path.exists(mpr_src):
                curated_mpr = os.path.join(result_root, f"{base_name}.mpr")
                shutil.copy2(mpr_src, curated_mpr)

            condition = {
                'label': label,
                'row_index': int(row_index),
                'electrode_diameter_um': diameter_um,
                'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'row_settings': {str(k): _json_safe(v) for k, v in row.items()},
                'curated_peis_txt': curated_txt,
                'curated_peis_mpr': curated_mpr,
            }
            curated_json = os.path.join(result_root, f"{base_name}.json")
            with open(curated_json, 'w', encoding='utf-8') as fh:
                json.dump(_json_safe(condition), fh, ensure_ascii=False, indent=2)

            if curated_txt or curated_mpr:
                self._log(f"  Curated results: {base_name}.(txt/mpr/json)")
            return curated_txt, curated_mpr, curated_json
        except Exception as exc:
            self._log(f"  [WARNING] Could not curate flat result artifacts: {exc}")
            return None, None, None

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
            normal_eis_floor_hz = float(self._full_auto['normal_eis_floor_hz'].get())
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
                        step_mm = _parse_optional_float(row.get('ContactStep_mm'), default=0.005)
                        max_beyond = _parse_optional_float(row.get('ContactMaxBeyondSeed_mm'), default=0.200)
                        threshold = _parse_optional_float(row.get('ContactOCVThreshold_V'), default=0.100)
                        settle_s = _parse_optional_float(row.get('ContactSettle_s'), default=0.30)
                        engage_mm = _parse_optional_float(row.get('ContactEngage_mm'), default=0.01)
                    except Exception as exc:
                        contact_param_errors.append(f'{label}: invalid AutoContactZ parameter ({exc})')
                        continue
                    if step_mm is None or step_mm <= 0:
                        contact_param_errors.append(f'{label}: ContactStep_mm must be > 0')
                    if start_offset is None or start_offset < 0:
                        contact_param_errors.append(f'{label}: ContactStartOffset_mm must be >= 0')
                    if max_beyond is None or max_beyond < 0:
                        contact_param_errors.append(f'{label}: ContactMaxBeyondSeed_mm must be >= 0 (this is now the tip-safety limit -- it is required, not optional)')
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

    def _contact_search_start_z(self, base_z, start_offset):
        """
        The Z height AutoContactZ/_execute_contact_z_search's search begins
        from -- base_z offset away from the electrode by start_offset, in
        the safe/away-from-contact direction. Mirrors the start_z formula
        inside _execute_contact_z_search (and run_automation.find_contact_z's
        headless equivalent) so the initial move-to-electrode Z target can
        be routed straight here instead of to the seed itself.
        """
        positive_z_is_up = bool(STAGE_SAFE_MOVE.get('positive_z_is_up', True))
        approach_sign = -1.0 if positive_z_is_up else 1.0
        return float(base_z) - approach_sign * abs(float(start_offset))

    def _auto_contact_z_for_row(self, row, label, contact_z_cache=None):
        """
        Returns (measurement_z, contact_confirmed): contact_confirmed is
        None if AutoContactZ wasn't enabled for this row at all, True on a
        confirmed OCV contact, False if the search never confirmed contact
        but the Contact fail policy is 'collect_anyway' -- in that case
        measurement_z is wherever the search's last step reached, and
        neither the Z-seed/parallax/XY-bias calibration stores nor
        contact_z_cache are updated (an unconfirmed height must not
        contaminate those). Raises (as before) when the policy is
        'stop'/'skip_row' and the caller's own row-level handling applies.
        """
        if not _parse_boolish(row.get('AutoContactZ'), default=False):
            return None, None
        if self.motor is None or self.bl is None:
            raise RuntimeError('AutoContactZ requires both motor and BioLogic connections')
        base_z = _parse_optional_float(row.get('Z_mm'), default=None)
        cache_key = self._contact_position_key(row)
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
        electrode_id = self._extract_electrode_id(str(row.get('Label', '')))
        layout_index = electrode_id - 1 if electrode_id is not None else None
        try:
            found_z, measure_z, parallax_sample = self._execute_contact_z_search(
                base_z=float(base_z),
                start_offset=self._contact_param_from_row(row, 'ContactStartOffset_mm', 'start_offset', 0.200),
                step_mm=self._contact_param_from_row(row, 'ContactStep_mm', 'step_mm', 0.005),
                max_beyond_seed_mm=self._contact_param_from_row(row, 'ContactMaxBeyondSeed_mm', 'max_beyond_seed_mm', 0.200),
                ocv_threshold=self._contact_param_from_row(row, 'ContactOCVThreshold_V', 'ocv_threshold', 0.100),
                settle_s=self._contact_param_from_row(row, 'ContactSettle_s', 'settle_s', 0.30),
                engage_mm=self._contact_param_from_row(row, 'ContactEngage_mm', 'engage_mm', 0.01),
                status_prefix=f'Auto contact {label}',
                stop_event=self.stop_flag,
                probe_pixel_sample_fn=lambda: self._image_probe_tip_pixel_now(layout_index=layout_index),
                z_status_var=self._image_z_seed_status_var,
            )
        except ContactNotConfirmedError as exc:
            if self._run_contact_fail_policy_var.get() != 'collect_anyway':
                raise
            self._log(
                f"  AutoContactZ: contact NOT confirmed (OCV never stabilized); "
                f"collecting measurement anyway at last-probed Z={exc.last_z:.3f} mm"
            )
            return exc.last_z, False
        self._log(
            f"  AutoContactZ found contact at Z={found_z:.3f} mm; "
            f"measurement Z={measure_z:.3f} mm"
        )
        if contact_z_cache is not None and cache_key is not None:
            contact_z_cache[cache_key] = measure_z
        z_status = self._image_record_electrode_z_contact(row, measure_z, parallax_sample)
        xy_status = None
        if electrode_id is not None:
            xy_status = self._image_recalibrate_xy_bias_from_contact(electrode_id - 1)
        self._image_set_combined_z_seed_status(z_status, xy_status)
        return measure_z, True

    def _retract_tip(self, distance_mm, *, context, monitor_label):
        """
        Move the tip straight up by distance_mm (or down, on a stage where
        positive Z isn't up -- see STAGE_SAFE_MOVE['positive_z_is_up']).
        context is a short label used only for log/error messages (e.g.
        'End-of-run', 'Pre-temperature-change'); monitor_label is what
        shows up in the Run/Monitor step display. Never raises -- a retract
        failure is logged as a warning, not treated as a row/run failure,
        matching the original end-of-run-only behavior this was extracted
        from.
        """
        if self.motor is None:
            self._log(f"  {context} tip retract skipped: motor is not connected")
            return
        try:
            distance_mm = abs(float(distance_mm))
        except Exception:
            self._log(f"  {context} tip retract skipped: invalid retract distance")
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
                label=monitor_label,
                step='tip_retract',
                message=f'Retracting tip {distance_mm:.3f} mm',
            )
            self._log(
                f"  {context} tip retract: Z {current_z:.3f} -> {target_z:.3f} mm "
                f"({distance_mm:.3f} mm up)"
            )
            self.motor.move_abs_wait('Z', target_z)
        except Exception as exc:
            self._log(f"  {context} tip retract warning: {exc}")

    def _retract_tip_after_successful_run(self):
        if not self._run_retract_tip_on_done_var.get():
            return
        self._retract_tip(
            self._run_retract_tip_mm_var.get(),
            context='End-of-run',
            monitor_label='End of run',
        )

    def _ramp_down_furnace_after_run(self, reason):
        """
        Command the furnace toward the configured end-of-run temperature.

        Called from _run_worker's outermost finally, so this fires no
        matter how the run ended -- success, user stop, or an uncaught
        exception partway through a row or even during setup. Never
        raises (same convention as _retract_tip): a failure here is
        logged as a warning, not allowed to mask whatever error actually
        ended the run or block the finally block's remaining cleanup
        (re-enabling Start/Stop).

        Only *commands* the ramp and returns -- does not call
        tc.wait_stable(), since that can block for a long time on a large
        temperature drop and this must not delay re-enabling the UI.
        """
        if not self._run_ramp_down_on_done_var.get():
            return
        if self.tc is None:
            self._log(f"  [Ramp-down] Skipped ({reason}): temperature controller is not connected")
            return
        try:
            end_temp_c = float(self._run_ramp_down_end_temp_c_var.get())
            ramp_rate = float(self._run_ramp_down_end_ramp_rate_var.get())
        except Exception:
            self._log(f"  [Ramp-down] Skipped ({reason}): invalid end temperature or ramp rate")
            return
        try:
            self._log(
                f"  [Ramp-down] {reason}: commanding furnace to {end_temp_c:g} C "
                f"at {ramp_rate:g} C/min"
            )
            self.tc.set_ramp_rate(ramp_rate)
            self.tc.set_temperature(end_temp_c)
            self._active_temperature_target_c = end_temp_c
            self._queue_monitor_event(
                'step', label='Ramp-down', step='furnace_ramp_down',
                message=f'Ramping furnace to {end_temp_c:g} C',
            )
        except Exception as exc:
            self._log(f"  [Ramp-down ERROR] Failed to command furnace ramp-down: {exc}")

    def _run_worker(self):
        """
        Thin, exception-safe wrapper around _run_worker_impl.

        Everything that must happen "no matter how the run ends" --
        furnace ramp-down, clearing self.running, and re-enabling the
        Start/Stop buttons -- lives in this finally, wrapping the ENTIRE
        run (not just the per-row loop _run_worker_impl's own try/finally
        covers) so it still fires even if something raises during setup,
        or if _run_worker_impl's own cleanup itself raises.
        """
        run_failed = False
        try:
            run_failed = self._run_worker_impl()
        except Exception:
            run_failed = True
            self._log(f"[RUN] [FATAL] {traceback.format_exc()}")
            self._status_var.set("Error - stopped")
        finally:
            reason = (
                'run failed' if run_failed
                else 'stopped by user' if self.stop_flag.is_set()
                else 'run completed'
            )
            self._ramp_down_furnace_after_run(reason)
            self.running = False
            self._run_on_main_thread(self._btn_start.config, state='normal')
            self._run_on_main_thread(self._btn_stop.config, state='disabled')
            self._queue_monitor_event('run_done')

    def _run_worker_impl(self):
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
        self._image_run_trusted_positions = {}
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
                self._run_on_main_thread(self._progress_lbl.config, text=f"{idx+1} / {total}")

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
                        # Always raise the tip before ramping to a new
                        # temperature -- not gated by the end-of-run retract
                        # checkbox, since this is a safety measure (avoid
                        # the tip sitting in contact while the furnace/melt
                        # is changing temperature) rather than an optional
                        # convenience. The next row's own move-to-position
                        # step (below, after temperature stabilizes) already
                        # brings the tip back down to the correct XY/Z, so
                        # nothing else needs to lower it back.
                        self._retract_tip(
                            self._run_retract_tip_mm_var.get(),
                            context='Pre-temperature-change',
                            monitor_label=label,
                        )
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
                        target_pos['X'], target_pos['Y'], _xy_source = self._image_resolve_live_tracked_xy(
                            row, target_pos['X'], target_pos['Y'],
                        )
                        if _parse_boolish(row.get('AutoContactZ'), default=False) and target_pos['Z'] is not None:
                            # Go straight to the search-start height (seed +
                            # offset) instead of the raw seed -- moving to
                            # the seed first would send the tip down to/
                            # through the electrode surface at full move
                            # speed before _execute_contact_z_search's own
                            # careful step-wise approach even begins.
                            start_offset = self._contact_param_from_row(
                                row, 'ContactStartOffset_mm', 'start_offset', 0.200,
                            )
                            target_pos['Z'] = self._contact_search_start_z(target_pos['Z'], start_offset)
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

                    measurement_z, contact_confirmed = self._auto_contact_z_for_row(
                        row,
                        label,
                        contact_z_cache,
                    )
                    if measurement_z is not None:
                        row = row.copy()
                        row['Z_mm'] = measurement_z
                        row['ContactConfirmed'] = contact_confirmed
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
                        peis_bandwidth = row.get('PEIS_Bandwidth')
                        if peis_bandwidth is not None and (pd.isna(peis_bandwidth) or str(peis_bandwidth).strip() == ''):
                            peis_bandwidth = None
                        peis_n_average = int(_parse_optional_float(row.get('PEIS_NAverage'), default=1))
                        skip_ca = _parse_boolish(row.get('SkipCA'), default=False)
                        live_poll_stop = None if self._biologic_backend_is_olecom() else self._start_biologic_live_poll(label)
                        # _run_olecom_hybrid_sequence has its own internal
                        # retry-with-recovery for transient OLE-COM errors
                        # (_is_olecom_transient_error/_recover_olecom_after_error),
                        # but that only covered the hybrid branch below --
                        # every other branch here (skip_ca's normal_sequence
                        # in particular, which routes SkipCA rows on the
                        # OLE-COM backend straight to run_peis/LoadSettings,
                        # same as any other row) had no recovery at all: one
                        # transient LoadSettings failure (e.g. RPC_E_SERVERFAULT,
                        # "EC-Lab may still be flushing a previous buffer or
                        # holding a stale COM channel") stopped the whole run,
                        # and nothing ever released the stale self.bl.ctrl
                        # handle -- so the identical failure would recur on
                        # every subsequent attempt against the same broken
                        # connection. _attempt_row_measurement's body keeps
                        # the original branch selection's indentation as-is
                        # (it's defined where that code used to sit directly
                        # under the old try:); only the retry loop below it
                        # is new.
                        def _attempt_row_measurement():
                            # row and measurement_mode are reassigned below
                            # (row = row.copy() after an applied FFT LF;
                            # measurement_mode on protocol fallback/routing)
                            # and both are read again after this function
                            # returns (_finalize_adaptive_row,
                            # _postprocess_csv_measurement, etc.) -- nonlocal
                            # is required so those reassignments update
                            # _run_worker's own row/measurement_mode instead
                            # of shadowing them with function-local names.
                            nonlocal row, measurement_mode
                            if skip_ca and normal_sequence:
                                # SkipCA must be an absolute guarantee, not just
                                # something rapid_eis_sequence honors -- both the
                                # OLE-COM hybrid protocol and the adaptive
                                # live-seeded protocol are fundamentally CA/FFT-
                                # seeded (they have no meaning without a CA trace
                                # to analyze), so neither can "skip CA" internally.
                                # Route around both entirely and run a guaranteed
                                # CA-free PEIS-only measurement instead.
                                if self._biologic_backend_is_olecom() or self._row_uses_adaptive_runtime(row):
                                    self._log(
                                        "  [SkipCA] CA disabled for this row -- running PEIS-only "
                                        "instead of the OLE-COM/adaptive CA-seeded protocol"
                                    )
                                measurement_mode = 'normal_eis'
                                sequence_result = normal_sequence(
                                    biologic=self.bl,
                                    v_dc=float(row['V_dc']),
                                    peis_f_high=float(row.get('PEIS_fHigh', 1e5)),
                                    peis_f_low=float(row.get('PEIS_fLow', 0.1)),
                                    peis_npts=int(float(row.get('PEIS_nPts', 60))),
                                    amplitude_mv=float(row.get('dV', 0.03)) * 1000.0,
                                    channel=biologic_channel,
                                    bandwidth=peis_bandwidth,
                                    n_average=peis_n_average,
                                    save_dir=save_dir,
                                    label=label,
                                    monitor_callback=self._queue_monitor_event,
                                    stop_event=self.stop_flag,
                                )
                            elif self._biologic_backend_is_olecom():
                                if measurement_mode == 'normal_eis':
                                    self._log(
                                        "  [OLE-COM] normal_eis request converted to hybrid live-stop; "
                                        "FFT/PEIS LF policy remains active"
                                    )
                                measurement_mode = 'rapid_eis'
                                row_is_adaptive = self._row_uses_adaptive_runtime(row)
                                self._log(
                                    "  [Full-auto] using OLE-COM CA/FFT seeded PEIS protocol"
                                    if row_is_adaptive else
                                    "  [Semi-auto] using OLE-COM hybrid protocol with fixed "
                                    "CA duration / PEIS low frequency from this row"
                                )
                                sequence_result = self._run_olecom_hybrid_sequence(
                                    row=row,
                                    label=label,
                                    save_dir=save_dir,
                                    channel=biologic_channel,
                                    stop_event=self.stop_flag,
                                    # Only Full-auto (ADAPT-labeled) rows want the
                                    # adaptive FFT-driven LF/scout-duration policy --
                                    # Semi-auto rows specify CA duration and PEIS low
                                    # frequency directly and expect them honored as
                                    # given, which is exactly what dynamic_lf=False's
                                    # branch inside _run_olecom_hybrid_sequence already
                                    # does (previously unreachable from here: this call
                                    # site hardcoded dynamic_lf=True for every OLE-COM
                                    # row regardless of which tab generated it, silently
                                    # overriding Semi-auto's own CA_duration_s/PEIS_fLow
                                    # with the fixed adaptive defaults).
                                    dynamic_lf=row_is_adaptive,
                                    # Decoupled from dynamic_lf on purpose: postprocessing
                                    # deferral is about how/when the full-arc Nyquist plot
                                    # gets built (GUI's own background worker either way),
                                    # not about the LF policy -- must stay True for every
                                    # row here regardless of dynamic_lf, or Semi-auto rows
                                    # would silently switch to synchronous inline
                                    # postprocessing inside run_once instead.
                                    defer_postprocess=True,
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
                                    bandwidth=peis_bandwidth,
                                    n_average=peis_n_average,
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
                                        skip_ca=_parse_boolish(row.get('SkipCA'), default=False),
                                        bandwidth=peis_bandwidth,
                                        n_average=peis_n_average,
                                        save_dir=save_dir,
                                        label=label,
                                        monitor_callback=self._queue_monitor_event,
                                        stop_event=self.stop_flag,
                                    )
                            return sequence_result

                        olecom_row_retry = self._biologic_backend_is_olecom()
                        max_row_attempts = 2 if olecom_row_retry else 1
                        try:
                            for row_attempt in range(1, max_row_attempts + 1):
                                try:
                                    sequence_result = _attempt_row_measurement()
                                    break
                                except Exception as exc:
                                    if row_attempt >= max_row_attempts or not self._is_olecom_transient_error(exc):
                                        raise
                                    self._log(
                                        f"  [OLE-COM recovery] retrying row measurement "
                                        f"{row_attempt + 1}/{max_row_attempts} after transient error: {exc}"
                                    )
                                    self._recover_olecom_after_error(str(exc))
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
                        self._curate_row_result_artifacts(
                            row=row,
                            row_index=idx + 1,
                            label=label,
                            sequence_result=sequence_result,
                            result_root=result_root,
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
        self._run_on_main_thread(self._progress_lbl.config, text=f"{total} / {total}")
        if run_failed:
            self._status_var.set("Error - stopped")
            self._log("\nRun stopped due to error.")
        elif self.stop_flag.is_set():
            self._status_var.set("Stopped")
            self._log("\nRun stopped.")
        else:
            self._status_var.set("Done")
            self._log("\nRun completed.")
        return run_failed

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
            self._image_seed_layout = None
            self._image_seed_tracked = None
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
        self._image_seed_layout = None
        self._image_seed_tracked = None
        self._image_tracking_map = None
        self._image_selected_target = None
        self._image_render_shape = None
        self._image_render_size = None
        self._image_render_offset = (0, 0)
        self._image_probe_tip_candidate = None
        self._image_probe_roi = None
        self._image_probe_roi_select_mode = False
        self._image_probe_roi_drag_start = None
        self._image_probe_roi_drag_current = None
        self._image_layout_draw_mode = False
        self._image_set_mode_button_active(self._image_probe_roi_button, False)
        self._image_set_mode_button_active(self._image_draw_circles_button, False)
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
        self._image_last_probe_roi_debug = None
        self._image_last_parallax_debug = None
        self._image_parallax_debug_var.set('Last parallax sample: none yet')
        if hasattr(self, '_image_parallax_panel_label'):
            self._image_update_parallax_debug_display()

    def _image_probe_occluded_layout_indices(self, tracked):
        """
        layout_index set for electrodes the probe arm is currently over
        (their known stage X sits more than VISION_PROBE_OCCLUSION_X_MARGIN_MM
        to the "covered" side of the probe tip's current stage X) --
        skipped entirely from this cycle's redetection in
        detect_and_refit_frame, since the arm occludes/confuses the Hough
        search there. Best-effort: returns None on any failure (no motor,
        no calibration, etc.), matching every other vision fallback in this
        file -- detect_and_refit_frame treats None the same as an empty set.
        """
        if self.motor is None:
            return None
        try:
            probe_x_mm = float(self.motor.get_position('X'))
        except Exception:
            return None
        margin = getattr(_config, 'VISION_PROBE_OCCLUSION_X_MARGIN_MM', 2.0)
        excluded = set()
        for c in tracked:
            if c.layout_index is None:
                continue
            try:
                stage_xy = self._image_project_pixel_to_stage_xy(
                    c.smoothed_x, c.smoothed_y, layout_index=c.layout_index,
                )
                if stage_xy is None:
                    continue
                if float(stage_xy[0]) < probe_x_mm - margin:
                    excluded.add(c.layout_index)
            except Exception:
                continue
        return excluded

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
        expected_count = self._parse_image_expected_count()

        have_frozen_seed = self._image_seed_tracked is not None and self._image_seed_layout is not None
        have_layout_seed = self._image_layout_tracked is not None and self._image_layout_model is not None

        if have_frozen_seed or have_layout_seed:
            # Per-electrode ROI redetection + confidence-gated affine re-fit
            # (vision/electrode_drift.py, the same mechanism run_automation.py's
            # headless AutoTrackXY uses), not ECC whole-frame registration --
            # an occluding probe over the sample only invalidates the
            # individual circles it covers, not one shared global transform.
            try:
                if have_frozen_seed:
                    layout = self._image_seed_layout
                    tracked = self._image_seed_tracked
                    seed_meta = self._image_seed_map
                    source_label = 'frozen seed'
                    hint_text = (
                        'Hint: frozen-seed tracking is active. The current live frame and overlay were locked as the tracking seed, so later frames are being tracked from that seed.'
                    )
                else:
                    layout = self._image_layout_model
                    tracked = self._image_layout_tracked
                    seed_meta = self._image_layout_seed_map
                    source_label = 'DXF layout seed'
                    hint_text = (
                        'Hint: DXF-layout-guided tracking is active. The accepted layout alignment is used as the initial visible-electrode seed; later frames are tracked from that seed.'
                    )
                occluded_layout_indices = self._image_probe_occluded_layout_indices(tracked)
                min_confident = max(
                    getattr(_config, 'VISION_DRIFT_MIN_CONFIDENT_ELECTRODES', 4),
                    math.ceil(
                        layout.n * getattr(_config, 'VISION_DRIFT_MIN_CONFIDENT_ELECTRODES_FRACTION', 0.2)
                    ),
                )
                # detect_and_refit_frame mutates tracked's Circle objects in
                # place (.smoothed_x/.smoothed_y/.detected/etc.) while a
                # background AutoContactZ thread may concurrently read those
                # same attributes -- see _image_tracked_lock's own comment
                # at its definition for why this needs to be held here.
                with self._image_tracked_lock:
                    result = detect_and_refit_frame(
                        frame_rgb,
                        layout,
                        tracked,
                        min_confident=min_confident,
                        ema_alpha=getattr(_config, 'VISION_DRIFT_EMA_ALPHA', 0.3),
                        max_projected_deviation_radii=getattr(
                            _config, 'VISION_DRIFT_MAX_PROJECTED_DEVIATION_RADII', None
                        ),
                        # Electrodes near the probe's current stage X are no
                        # longer skipped from redetection here -- that used
                        # to hard-skip the Hough search there entirely
                        # (detect_and_refit_frame's excluded_layout_indices),
                        # preventing a legitimate detection from ever being
                        # attempted in that region at all. occluded_layout_indices
                        # is still computed and shown (see the 'occluded'
                        # table column below) as an informational flag, not
                        # a gate.
                        **self._image_resolve_drift_refit_params(),
                    )
                overlay_detections = self._image_tracked_circles_to_table(
                    tracked, seed_meta, occluded_layout_indices=occluded_layout_indices,
                )
                # display_bgr is still the plain current camera frame from
                # the top of this method -- annotate_detections is drawn
                # fresh from self._image_tracking_map at the end of
                # _render_image_monitor_frame instead, so it ends up on top
                # of the alignment/probe overlays drawn there rather than
                # under them.
                self._image_monitor_last_overlay_bgr = display_bgr.copy()
                self._image_tracking_map = overlay_detections.copy()
                self._image_update_selected_target_from_overlay()
                refit_suffix = (
                    f', re-fit from {result.confident_count} redetected'
                    if result.refit_performed
                    else f', {result.confident_count} redetected'
                )
                self._image_detection_var.set(
                    f'Electrodes: {len(overlay_detections)} tracked ({source_label}{refit_suffix})'
                )
                self._image_hint_var.set(hint_text)
            except Exception as exc:
                self._image_monitor_last_overlay_bgr = None
                self._image_tracking_map = None
                self._image_detection_var.set(f'Electrodes: detection failed ({exc})')
        elif (
            self._image_monitor_last_overlay_bgr is None
            or self._image_monitor_frame_idx % max(self._image_monitor_detect_every, 1) == 1
        ):
            try:
                detections = detect_live_microscope_electrode_map_rgb(
                    frame_rgb,
                    sample_side_mm=10.0,
                    size_group='all',
                    **self._image_resolve_live_detector_params(),
                )
                overlay_detections = filter_live_overlay_detections(
                    detections,
                    max_candidates=expected_count,
                )
                raw_count = len(detections)
                self._image_monitor_last_overlay_bgr = display_bgr.copy()
                self._image_tracking_map = overlay_detections.copy()
                self._image_update_selected_target_from_overlay()
                expected_suffix = (
                    f' | target≈{expected_count}'
                    if expected_count is not None else ''
                )
                self._image_detection_var.set(
                    f'Electrodes: {len(overlay_detections)} primary candidates from {raw_count} raw circles (live detector){expected_suffix}'
                )
                self._image_hint_var.set(
                    'Hint: live view shows conservative primary candidates only. If you know roughly how many electrodes should be visible, fill in Expected visible electrodes. If the overlay still looks wrong, align a DXF layout for a trusted tracking seed.'
                )
            except Exception as exc:
                self._image_monitor_last_overlay_bgr = None
                self._image_tracking_map = None
                self._image_detection_var.set(f'Electrodes: detection failed ({exc})')
        elif self._image_tracking_map is not None and not self._image_tracking_map.empty:
            try:
                tracked_df = refine_circular_electrode_map_rgb(
                    frame_rgb,
                    self._image_tracking_map,
                    search_radius_px=28.0,
                    radius_tolerance=0.30,
                )
                self._image_monitor_last_overlay_bgr = display_bgr.copy()
                self._image_tracking_map = tracked_df.copy()
                self._image_update_selected_target_from_overlay()
                self._image_detection_var.set(
                    f'Electrodes: tracking {len(tracked_df)} candidates from prior live frame'
                )
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
        if self._image_show_circles_var.get():
            frame_bgr = self._image_draw_layout_alignment_overlay(frame_bgr)
        frame_bgr = self._image_draw_probe_tip_overlay(frame_bgr)
        frame_bgr = self._image_draw_probe_roi_overlay(frame_bgr)
        frame_bgr = self._image_draw_axis_arrows_overlay(frame_bgr)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        self._image_render_shape = frame_rgb.shape[:2]
        if not self.tab_image.winfo_ismapped():
            # Detection/tracking above already ran and stays current; only
            # skip the PhotoImage conversion + Label bitmap update, which
            # is both pointless and actively harmful while this tab isn't
            # visible -- repeatedly reconfiguring a Label's image inside a
            # Canvas-embedded window item while its Notebook tab is
            # unmapped is what caused stray camera-frame artifacts to bleed
            # onto other tabs (Manual Control in particular, which uses the
            # same Canvas+create_window scroll pattern).
            return
        if (
            self._image_show_circles_var.get()
            and self._image_tracking_map is not None
            and not self._image_tracking_map.empty
        ):
            # Drawn last, on top of the alignment overlay/probe tip/probe
            # ROI above, so the detected (orange/green) vs. extrapolated
            # (gray) color-coding from annotate_detections stays visible
            # instead of being covered by the alignment overlay's amber
            # projected circles.
            frame_rgb = annotate_detections(frame_rgb, self._image_tracking_map, annotate_labels=False)
        image = Image.fromarray(frame_rgb)
        # Only fall back to the 640x480 default before the label has been
        # laid out at least once (winfo_width/height report ~1 pre-map) --
        # max(actual, 640) instead of this would force the render to at
        # least 640x480 even once the label is genuinely laid out smaller
        # than that (any non-maximized window), so the rendered image ends
        # up bigger than what's actually visible in the label. That desync
        # is exactly what made _image_frame_xy_from_event's cursor->frame
        # mapping (which assumes the render exactly fills the label) drift
        # unless the window was maximized/fullscreen, where the label
        # naturally exceeds 640x480 and the mismatch disappears.
        label_w = self._image_monitor_label.winfo_width()
        label_h = self._image_monitor_label.winfo_height()
        max_w = label_w if label_w > 10 else 640
        max_h = label_h if label_h > 10 else 480
        image.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        self._image_render_size = image.size
        self._image_render_offset = (
            max((max_w - image.size[0]) // 2, 0),
            max((max_h - image.size[1]) // 2, 0),
        )
        photo = ImageTk.PhotoImage(image)
        self._image_monitor_photo = photo
        self._image_monitor_label.configure(image=photo, text='')

    def _image_force_live_view_redraw(self):
        """
        Re-render the live view immediately from the last composited frame,
        instead of waiting for the next scheduled ~120ms
        _image_monitor_tick. Called by the tuning windows
        (CircleTuningWindow/ProbeTuningWindow) right after opening and on
        close -- both are separate floating Toplevel windows that can
        overlap the live camera preview, and this project's
        Canvas+create_window+Label live-view pattern is already known to
        be fragile to overlapping-window redraw glitches on macOS (see the
        tab-visibility guard above in _render_image_monitor_frame for the
        earlier, related fix). Forcing an immediate re-render shortens the
        window during which a stale compositing artifact could stay
        visible from "up to 120ms" to "immediately," without depending on
        exactly reproducing the underlying platform quirk. No-op if the
        camera isn't running or no frame has been rendered yet.
        """
        if not self._image_monitor_running or self._image_monitor_last_overlay_bgr is None:
            return
        self._render_image_monitor_frame(self._image_monitor_last_overlay_bgr.copy())

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

    def _image_freeze_current_seed(self):
        if self._image_monitor_last_frame_rgb is None or self._image_tracking_map is None or self._image_tracking_map.empty:
            self._image_status_var.set('Freeze seed failed: no current live frame/overlay to lock')
            return
        self._image_promote_current_overlay_to_seed(
            status_text=None,
            hint_text='Hint: frozen-seed tracking is active. If the layout drifts too far or the wrong candidates were frozen, use Clear Seed and reacquire.',
        )

    def _image_clear_seed(self):
        self._image_seed_map = None
        self._image_seed_layout = None
        self._image_seed_tracked = None
        self._image_status_var.set('Cleared frozen tracking seed')

    def _image_promote_current_overlay_to_seed(self, *, status_text=None, hint_text=None):
        if self._image_tracking_map is None or self._image_tracking_map.empty:
            return False
        seed_table = self._image_tracking_map.copy()
        self._image_seed_map = seed_table
        # Template space is the seed frame's own pixel coordinates -- there's
        # no CAD-derived canonical geometry for a frozen-overlay seed, so the
        # transform starts as identity and vision.electrode_drift.detect_and_refit_frame
        # re-fits it (seed-frame pixels -> current-frame pixels) from
        # confidently-redetected electrodes each tick, same as DXF-layout
        # tracking below.
        self._image_seed_layout = LayoutModel(
            seed_table[['x_px', 'y_px']].to_numpy(dtype=np.float32),
            seed_table['radius_px'].to_numpy(dtype=np.float32),
        )
        self._image_seed_layout.transform = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32
        )
        self._image_seed_tracked = [
            Circle(
                x=float(row.x_px),
                y=float(row.y_px),
                radius=float(row.radius_px),
                layout_index=i,
                on_image=True,
            )
            for i, row in enumerate(seed_table.itertuples())
        ]
        if status_text is None:
            status_text = f'Frozen current frame as tracking seed ({len(self._image_seed_map)} electrodes)'
        if hint_text is None:
            hint_text = (
                'Hint: frozen-seed tracking is active. If the layout drifts too far or the wrong candidates were frozen, use Clear Seed and reacquire.'
            )
        self._image_status_var.set(status_text)
        self._image_hint_var.set(hint_text)
        return True


    def _image_clear_target(self):
        self._image_selected_target = None
        self._image_target_status_var.set('Target: not locked')
        self._image_refresh_z_seed_status()

    def _image_get_current_stage_xy(self):
        stage_x = _parse_optional_float(self._manual_current['x'].get())
        stage_y = _parse_optional_float(self._manual_current['y'].get())
        if stage_x is None or stage_y is None:
            return None
        return float(stage_x), float(stage_y)

    def _image_note_manual_move_target_match(self, moved_x_mm, moved_y_mm):
        """
        Called after "Move Tip" actually drives the stage, while an Image
        Monitor target is locked, to decide whether that move still
        satisfies the lock or invalidates it. The target's expected stage
        position is re-derived fresh here (not read back from the possibly-
        stale Move to: fields) so a target that has drifted since it was
        locked is still checked accurately. If the driven XY doesn't match,
        the fields must have been edited manually after locking, so the
        lock is cleared automatically -- this is what makes a separate
        Clear Target button unnecessary.

        Search Z's own "is the stage at the target" precondition
        (_image_target_within_move_tolerance) is checked fresh, live,
        whenever it's actually needed, rather than relying on a flag set
        here -- so this method's only remaining job is the auto-clear
        above; it no longer gates anything itself.
        """
        target = self._image_selected_target
        if self._image_pixel_stage_affine_calibration is None:
            return
        try:
            expected_xy = self._image_project_pixel_to_stage_xy(
                float(target['x_px']), float(target['y_px']), target=target,
            )
        except Exception:
            return
        try:
            tol = float(self._contact_search['target_xy_tolerance_mm'].get())
        except Exception:
            tol = IMAGE_TARGET_MOVE_MATCH_TOL_MM
        matched = (
            abs(float(expected_xy[0]) - moved_x_mm) <= tol
            and abs(float(expected_xy[1]) - moved_y_mm) <= tol
        )
        if not matched:
            self._image_clear_target()
            return
        self._image_update_target_status()
        self._image_refresh_z_seed_status()

    def _image_target_within_move_tolerance(self):
        """
        True if the stage's CURRENT XY (read live from the motor) is within
        Target XY tolerance (mm) of the locked target's expected stage
        position -- the live replacement for the old "did the last Move Tip
        click happen to match" flag, checked fresh every time it's actually
        needed (Search Z, and the Z-seed status line) instead of trusting a
        snapshot from whenever Move Tip was last clicked. Deliberately
        compares stage positions in mm, not camera pixel positions: the
        electrode's expected stage XY (via _image_project_pixel_to_stage_xy)
        only depends on the electrode's own known height, never on the
        probe's current/target Z, but a PIXEL-space comparison (e.g.
        against a live probe-tip detection) would not have that property --
        the probe's own detected pixel position shifts with its own Z, so
        it and the electrode's expected pixel position would only coincide
        when the probe happens to be at the electrode's exact height, not
        merely above it. Comparing stage mm avoids that entirely.

        False (not an error) if there's no locked target, no motor, no
        pixel<->stage calibration yet, or the projection fails -- all
        "not ready to check this yet," same as every other vision fallback
        in this file.
        """
        target = self._image_selected_target
        if target is None or self.motor is None or self._image_pixel_stage_affine_calibration is None:
            return False
        try:
            expected_xy = self._image_project_pixel_to_stage_xy(
                float(target['x_px']), float(target['y_px']), target=target,
            )
            current_x = float(self.motor.get_position('X'))
            current_y = float(self.motor.get_position('Y'))
        except Exception:
            return False
        try:
            tol = float(self._contact_search['target_xy_tolerance_mm'].get())
        except Exception:
            tol = IMAGE_TARGET_MOVE_MATCH_TOL_MM
        return (
            abs(float(expected_xy[0]) - current_x) <= tol
            and abs(float(expected_xy[1]) - current_y) <= tol
        )

    # ══════════════════════════════════════════════════════════════════════
    # Probe-based pixel<->stage calibration
    # ══════════════════════════════════════════════════════════════════════

    def _image_current_frame_bgr(self):
        if self._image_monitor_last_frame_rgb is None:
            return None
        if cv2 is None:
            _vision_unavailable()
        return cv2.cvtColor(self._image_monitor_last_frame_rgb, cv2.COLOR_RGB2BGR)

    def _image_detect_probe_tip(self):
        frame_bgr = self._image_current_frame_bgr()
        if frame_bgr is None:
            self._image_probe_status_var.set('Probe: no live frame yet')
            return
        if self._image_probe_detector is None:
            clahe_clip = getattr(_config, 'VISION_PROBE_CLAHE_CLIP', 4.0)
            self._image_probe_detector = ProbeDetector(clahe_clip=clahe_clip)
        try:
            tip = self._image_probe_detector.detect(frame_bgr, roi=self._image_probe_roi)
        except Exception as exc:
            self._image_probe_status_var.set(f'Probe detection failed: {exc}')
            return
        if tip is None or not tip.detected:
            self._image_probe_tip_candidate = None
            self._image_probe_status_var.set('Probe: not detected in current frame')
            return
        self._image_probe_tip_candidate = (float(tip.x), float(tip.y))
        self._image_probe_status_var.set(
            f'Probe: detected at ({tip.x:.1f}, {tip.y:.1f}) px'
        )

    def _image_draw_probe_tip_overlay(self, frame_bgr):
        if self._image_probe_tip_candidate is None or cv2 is None:
            return frame_bgr
        canvas = frame_bgr.copy()
        x, y = int(round(self._image_probe_tip_candidate[0])), int(round(self._image_probe_tip_candidate[1]))
        cv2.drawMarker(
            canvas, (x, y), (0, 0, 255),
            markerType=cv2.MARKER_CROSS, markerSize=20, thickness=2,
        )
        return canvas

    def _image_add_pixel_stage_calibration_point(self):
        # Capture whatever tip location is currently displayed rather than
        # re-detecting -- re-detecting here could land on a different pixel
        # than the crosshair the user is looking at (live frame moved on,
        # detector jitter/reflection lock), silently capturing a point that
        # doesn't match what was shown. Require an explicit "Detect Probe
        # Tip" click first.
        if self._image_probe_tip_candidate is None:
            self._image_pixel_stage_affine_status_var.set(
                'Probe calibration failed: detect the probe tip first'
            )
            return
        current_stage_xy = self._image_get_current_stage_xy()
        if current_stage_xy is None:
            self._image_pixel_stage_affine_status_var.set(
                'Probe calibration failed: refresh current Motor X/Y first'
            )
            return
        current_z = _parse_optional_float(self._manual_current['z'].get())
        pixel_x, pixel_y = self._image_probe_tip_candidate
        stage_x, stage_y = current_stage_xy
        self._image_pixel_stage_calibration_refs.append(
            PixelStageReference(
                pixel_x=float(pixel_x),
                pixel_y=float(pixel_y),
                stage_x_mm=float(stage_x),
                stage_y_mm=float(stage_y),
                z_mm=None if current_z is None else float(current_z),
            )
        )
        self._image_pixel_stage_affine_calibration = None
        self._image_pixel_stage_z_ref_mm = None
        self._image_pixel_stage_affine_status_var.set(
            f'Probe calibration: {len(self._image_pixel_stage_calibration_refs)} point(s) captured, not yet solved'
        )

    def _image_solve_pixel_stage_affine_calibration(self):
        refs = self._image_pixel_stage_calibration_refs
        if len(refs) < 3:
            self._image_pixel_stage_affine_calibration = None
            self._image_pixel_stage_z_ref_mm = None
            self._image_pixel_stage_affine_status_var.set(
                f'Probe calibration failed: need at least 3 points '
                f'({len(refs)} present)'
            )
            return
        z_values = [ref.z_mm for ref in refs]
        if any(z is None for z in z_values):
            self._image_pixel_stage_affine_calibration = None
            self._image_pixel_stage_z_ref_mm = None
            self._image_pixel_stage_affine_status_var.set(
                'Probe calibration failed: every point needs a valid Z readback '
                '(refresh current Motor Z before adding each point) -- recapture all points'
            )
            return
        z_tolerance = getattr(_config, 'VISION_PROBE_CALIBRATION_Z_TOLERANCE_MM', 0.05)
        z_spread = max(z_values) - min(z_values)
        if z_spread > z_tolerance:
            self._image_pixel_stage_affine_calibration = None
            self._image_pixel_stage_z_ref_mm = None
            self._image_pixel_stage_affine_status_var.set(
                f'Probe calibration failed: Z varied by {z_spread:.3f} mm across points '
                f'(max allowed {z_tolerance:.3f} mm) -- recapture all points at a consistent Z height'
            )
            return
        try:
            self._image_pixel_stage_affine_calibration = solve_stage_affine_calibration(refs)
        except Exception as exc:
            self._image_pixel_stage_affine_calibration = None
            self._image_pixel_stage_z_ref_mm = None
            self._image_pixel_stage_affine_status_var.set(f'Probe calibration solve failed: {exc}')
            return
        self._image_pixel_stage_z_ref_mm = sum(z_values) / len(z_values)
        self._image_pixel_stage_affine_status_var.set(
            f'Probe calibration: affine solved from {len(refs)} points, Z ref = '
            f'{self._image_pixel_stage_z_ref_mm:.3f} mm (spread {z_spread:.3f} mm)'
        )

    def _image_clear_pixel_stage_affine_calibration(self):
        self._image_pixel_stage_calibration_refs = []
        self._image_pixel_stage_affine_calibration = None
        self._image_pixel_stage_z_ref_mm = None
        self._image_pixel_stage_affine_status_var.set('Probe calibration: not solved')

    def _image_pixel_stage_calibration_payload(self):
        return {
            'refs': [
                {
                    'pixel_x': float(ref.pixel_x),
                    'pixel_y': float(ref.pixel_y),
                    'stage_x_mm': float(ref.stage_x_mm),
                    'stage_y_mm': float(ref.stage_y_mm),
                    'z_mm': None if ref.z_mm is None else float(ref.z_mm),
                }
                for ref in self._image_pixel_stage_calibration_refs
            ],
            'solved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }

    def _image_save_pixel_stage_affine_calibration(self, path=None):
        if path is None:
            path = filedialog.asksaveasfilename(
                title='Save probe pixel<->stage calibration',
                defaultextension='.json',
                initialfile=os.path.basename(
                    getattr(_config, 'VISION_PIXEL_STAGE_CALIBRATION_PATH', 'pixel_stage_calibration.json')
                ),
                filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            )
        if not path:
            return False
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(self._image_pixel_stage_calibration_payload(), fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._image_pixel_stage_affine_status_var.set(f'Probe calibration save failed: {exc}')
            return False
        self._image_pixel_stage_affine_status_var.set(
            f"Probe calibration: saved {len(self._image_pixel_stage_calibration_refs)} refs to {os.path.basename(path)}"
        )
        return True

    def _image_load_pixel_stage_affine_calibration(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title='Load probe pixel<->stage calibration',
                filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            )
        if not path:
            return False
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
            refs = [
                PixelStageReference(
                    pixel_x=float(item['pixel_x']),
                    pixel_y=float(item['pixel_y']),
                    stage_x_mm=float(item['stage_x_mm']),
                    stage_y_mm=float(item['stage_y_mm']),
                    z_mm=None if item.get('z_mm') is None else float(item['z_mm']),
                )
                for item in payload.get('refs', [])
            ]
        except Exception as exc:
            self._image_pixel_stage_affine_status_var.set(f'Probe calibration load failed: {exc}')
            return False
        self._image_pixel_stage_calibration_refs = refs
        if len(refs) >= 3:
            # Reuse the same Z-consistency enforcement as a fresh solve --
            # an old calibration file predating per-point Z capture (no
            # z_mm at all) or one with an inconsistent Z will be refused
            # here just as it would be if solved interactively.
            self._image_solve_pixel_stage_affine_calibration()
            if self._image_pixel_stage_affine_calibration is None:
                return False
            self._image_pixel_stage_affine_status_var.set(
                f"{self._image_pixel_stage_affine_status_var.get()} (loaded from {os.path.basename(path)})"
            )
        else:
            self._image_pixel_stage_affine_calibration = None
            self._image_pixel_stage_z_ref_mm = None
            self._image_pixel_stage_affine_status_var.set(
                f"Probe calibration: loaded {len(refs)} refs ({os.path.basename(path)})"
            )
        return True

    # ══════════════════════════════════════════════════════════════════════
    # Electrode Z-seed store: per-electrode real height + pooled Z-parallax
    # slope, fed by AutoContactZ contact searches (see vision/electrode_z_seed.py)
    # ══════════════════════════════════════════════════════════════════════

    def _image_probe_tip_pixel_now(self, layout_index=None):
        """Probe-tip pixel sampler passed as the contact-search hook (see
        _execute_contact_z_search's probe_pixel_sample_fn) -- grabs the
        current live frame and runs the same detector "Detect Probe Tip"
        already uses. Returns (pixel_x, pixel_y) or None on any failure;
        never raises.

        layout_index, if given and currently tracked, searches a tight ROI
        centered on that electrode's own tracked pixel position and sized
        to its tracked radius -- same idea as
        _image_recalibrate_xy_bias_from_contact's electrode-sized ROI,
        applied here to the parallax samples too (the probe tip should
        already be at that electrode's XY by the time a contact search
        runs, both at the search-start height and at confirmed contact, so
        a tight box around the electrode's tracked position is a more
        reliable search area than the generic user-drawn _image_probe_roi).
        Falls back to _image_probe_roi whenever layout_index isn't given or
        isn't currently tracked -- same best-effort-fallback style as every
        other vision helper in this file.
        """
        self._image_last_probe_roi_debug = None
        try:
            frame_bgr = self._image_current_frame_bgr()
            if frame_bgr is None:
                return None
            if self._image_probe_detector is None:
                clahe_clip = getattr(_config, 'VISION_PROBE_CLAHE_CLIP', 4.0)
                self._image_probe_detector = ProbeDetector(clahe_clip=clahe_clip)
            roi = self._image_probe_roi
            if (
                layout_index is not None
                and self._image_layout_tracked is not None
                and 0 <= layout_index < len(self._image_layout_tracked)
            ):
                # _image_layout_tracked's Circle objects are mutated in
                # place by _image_monitor_tick on the main thread every
                # tick -- see _image_tracked_lock's definition comment.
                with self._image_tracked_lock:
                    circle = self._image_layout_tracked[layout_index]
                    r = float(circle.radius)
                    smoothed_x, smoothed_y = circle.smoothed_x, circle.smoothed_y
                roi = (
                    int(smoothed_x - r), int(smoothed_y - r),
                    int(smoothed_x + r), int(smoothed_y + r),
                )
            tip = self._image_probe_detector.detect(frame_bgr, roi=roi)
            self._image_stash_probe_roi_debug(frame_bgr, roi, tip)
            if tip is None or not tip.detected:
                return None
            return float(tip.x), float(tip.y)
        except Exception:
            return None

    def _image_stash_probe_roi_debug(self, frame_bgr, roi, tip):
        """Side-channel snapshot of the last probe-tip search: the actual
        ROI crop searched and the detected tip's position local to that
        crop (or None if nothing was found there). Purely diagnostic --
        read by _image_update_parallax_debug_display; never affects
        _image_probe_tip_pixel_now's own (pixel_x, pixel_y)/None contract.
        Never raises.
        """
        try:
            h, w = frame_bgr.shape[:2]
            x1, y1 = max(0, int(roi[0])), max(0, int(roi[1]))
            x2, y2 = min(w, int(roi[2])), min(h, int(roi[3]))
            crop = frame_bgr[y1:y2, x1:x2].copy()
            # A zero-width/height crop (roi clamped entirely off-frame, or
            # degenerate to begin with) would otherwise reach _make_panel's
            # scale = cell / w division and blow up -- treat it the same as
            # "no crop" here instead of stashing something that later
            # display code has to guard against.
            if crop.size == 0 or crop.shape[0] == 0 or crop.shape[1] == 0:
                self._image_last_probe_roi_debug = None
                return
            tip_local = None
            if tip is not None and tip.detected:
                tip_local = (float(tip.x) - x1, float(tip.y) - y1)
            self._image_last_probe_roi_debug = {'crop_bgr': crop, 'tip_local': tip_local}
        except Exception:
            self._image_last_probe_roi_debug = None

    # Sized to actually use the right half of the canvas (contact_right)
    # rather than staying small enough to squeeze in next to the fields --
    # see the Z Contact Search section's contact_left/contact_right split.
    _PARALLAX_PANEL_CELL_PX = 200

    def _image_update_parallax_debug_display(self):
        """Refreshes the Z Contact Search panel's Start/Contact ROI +
        crosshair preview and numeric summary from
        self._image_last_parallax_debug. Reuses ProbeTuningWindow's own
        crosshair/panel-compositing code (vision/tuning_gui/probe_gui.py)
        rather than duplicating it here. Best-effort: never raises, and
        silently leaves the display as-is if vision/Tk pieces aren't
        available (matches this file's other vision fallbacks).
        """
        debug = self._image_last_parallax_debug
        if debug is not None:
            start_pixel = debug.get('start_pixel')
            contact_pixel = debug.get('contact_pixel')
            if start_pixel is not None and contact_pixel is not None:
                delta_px = contact_pixel[0] - start_pixel[0]
                delta_py = contact_pixel[1] - start_pixel[1]
                verdict = 'accepted' if debug.get('accepted') else f"rejected: {debug.get('reject_reason')}"
                self._image_parallax_debug_var.set(
                    f"Last parallax sample: Δpixel=({delta_px:+.2f}, {delta_py:+.2f}) {verdict}"
                )
            else:
                self._image_parallax_debug_var.set(
                    f"Last parallax sample: rejected: {debug.get('reject_reason', 'no sample')}"
                )
        if not hasattr(self, '_image_parallax_panel_canvas'):
            return
        if cv2 is None or Image is None or ImageTk is None:
            return
        canvas = self._image_parallax_panel_canvas
        canvas.delete('all')
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 190)
        start_roi = debug.get('start_roi') if debug is not None else None
        contact_roi = debug.get('contact_roi') if debug is not None else None
        if start_roi is None and contact_roi is None:
            # Same bordered-box-with-centered-text placeholder style as
            # self._image_dxf_layout_preview_canvas's "No layout loaded",
            # instead of composing a two-cell panel of blank gray boxes
            # with baked-in captions (which reads as visually inconsistent
            # with the rest of this tab).
            canvas.create_text(
                width / 2, height / 2, text='No parallax sample yet',
                fill='#888', font=('Segoe UI', 10),
            )
            self._image_parallax_panel_photo = None
            return
        try:
            cell = self._PARALLAX_PANEL_CELL_PX
            blank = np.full((cell, cell, 3), 60, dtype=np.uint8)
            imgs_labels = []
            for roi_debug, caption in ((start_roi, 'Start'), (contact_roi, 'Contact')):
                if roi_debug is None or roi_debug.get('crop_bgr') is None:
                    imgs_labels.append((blank.copy(), f'{caption}: no sample'))
                    continue
                crop = roi_debug['crop_bgr']
                # ProbeTuningWindow._make_panel only ever shrinks to fit a
                # cell (its own scale is capped at 1.0), so a real ROI crop
                # -- typically much smaller than the cell -- stays tiny
                # inside it. Upscale here first (aspect-preserving, no cap)
                # so the image actually fills the space, then draw the
                # crosshair on the UPSCALED image at a fixed pixel size --
                # drawing it before scaling would stretch the crosshair
                # itself along with the image. Handing _make_panel an
                # already cell-sized image makes its own resize a no-op.
                ch, cw = crop.shape[:2]
                scale = min(cell / cw, cell / ch) if cw and ch else 1.0
                out_w, out_h = max(int(round(cw * scale)), 1), max(int(round(ch * scale)), 1)
                interp = cv2.INTER_NEAREST if scale >= 1.0 else cv2.INTER_AREA
                crop = cv2.resize(crop, (out_w, out_h), interpolation=interp)
                tip_local = roi_debug.get('tip_local')
                if tip_local is not None:
                    ProbeTuningWindow._draw_crosshair(crop, tip_local[0] * scale, tip_local[1] * scale)
                    label = caption
                else:
                    label = f'{caption}: tip not found'
                imgs_labels.append((crop, label))
            panel_bgr = ProbeTuningWindow._make_panel(imgs_labels, cell_w=cell, cell_h=cell)
            panel_rgb = cv2.cvtColor(panel_bgr, cv2.COLOR_BGR2RGB)
            photo = ImageTk.PhotoImage(Image.fromarray(panel_rgb))
            self._image_parallax_panel_photo = photo
            canvas.create_image(width / 2, height / 2, image=photo, anchor='center')
        except Exception as exc:
            # This panel exists specifically to diagnose probe-detection
            # problems -- a silently-swallowed failure here would defeat
            # that purpose (previously this branch was a bare `pass`,
            # which is exactly why a broken panel gave no clue why).
            self._image_parallax_panel_photo = None
            canvas.create_text(
                width / 2, height / 2, text=f'Panel render failed: {exc}',
                fill='#c0392b', font=('Segoe UI', 9), width=width - 20,
            )

    def _image_layout_index_for_target(self, target):
        if target is None:
            return None
        try:
            if target.get('source') != 'dxf_layout':
                return None
            return int(target['col_index']) - 1
        except Exception:
            return None

    def _image_set_combined_z_seed_status(self, z_status, xy_status):
        """
        One contact-search success feeds two separate calibration stores
        (Z-seed/parallax and XY-bias), but the Z Contact Search panel only
        has a single status line (self._image_z_seed_status_var). Setting
        it twice in a row means only the second .set() is ever actually
        seen -- the first is overwritten before there's any real chance to
        read it. Combine both short fragments into one line instead.
        Either may be None (nothing to report for that half -- e.g.
        XY-bias's own "not enough information yet" early-outs stay
        silent, as before); if both are None, nothing is set at all.
        """
        parts = [p for p in (z_status, xy_status) if p]
        if parts:
            self._image_z_seed_status_var.set(' | '.join(parts))

    def _image_record_electrode_z_contact(self, row, measure_z, parallax_sample):
        """
        Common glue for every contact-search success path (manual "Search
        Z" and the full-auto tab's _auto_contact_z_for_row): derive the
        electrode identity,
        update its Z seed, and contribute a parallax sample if one was
        captured. row is a CSV row dict (electrode identified via Label's
        E<N>, same convention as run_automation.py) or None if the caller
        doesn't have one (see _image_record_electrode_z_contact_for_target
        for the target-lock-based variant). Never raises -- returns a
        short status fragment for the caller to combine with the XY-bias
        fragment (see _image_set_combined_z_seed_status), or None if
        there's no electrode identity to report against at all.
        """
        layout_index = None
        if row is not None:
            electrode_id = self._extract_electrode_id(str(row.get('Label', '')))
            if electrode_id is not None:
                layout_index = electrode_id - 1
        if layout_index is None:
            return None
        return self._image_record_electrode_z_contact_for_layout_index(layout_index, measure_z, parallax_sample)

    def _image_record_electrode_z_contact_for_target(self, target, measure_z, parallax_sample):
        layout_index = self._image_layout_index_for_target(target)
        if layout_index is None:
            return 'Z seed: target is not a DXF-layout electrode -- nothing to seed'
        return self._image_record_electrode_z_contact_for_layout_index(layout_index, measure_z, parallax_sample)

    def _image_layout_xy_mm_for_layout_index(self, layout_index):
        """This electrode's physical position in the DXF layout's own
        template coordinate frame, or None if no layout is loaded or the
        index is out of range. Feeds the Z-surface plane fit -- see
        vision/electrode_z_seed.py::ZPlaneFit."""
        layout = self._image_layout_model
        if layout is None or layout_index is None:
            return None
        try:
            if not (0 <= int(layout_index) < layout.n):
                return None
            u, v = layout.template[int(layout_index)]
            return float(u), float(v)
        except Exception:
            return None

    def _image_layout_diameter_um_for_layout_index(self, layout_index):
        """This electrode's diameter in microns, from the DXF layout's own
        per-circle radii (physical CAD mm units -- see LayoutModel's own
        docstring in vision/layout_alignment.py), or None if no layout is
        loaded, the index is out of range, or the DXF file didn't specify
        per-circle radii at all (template_radii is then None)."""
        layout = self._image_layout_model
        if layout is None or layout_index is None or layout.template_radii is None:
            return None
        try:
            if not (0 <= int(layout_index) < len(layout.template_radii)):
                return None
            radius_mm = float(layout.template_radii[int(layout_index)])
            return radius_mm * 2.0 * 1000.0
        except Exception:
            return None

    def _image_record_electrode_z_contact_for_layout_index(self, layout_index, measure_z, parallax_sample):
        """Never raises -- returns a short status fragment (see
        _image_set_combined_z_seed_status), always non-empty."""
        try:
            fit_happened = self._image_electrode_z_store.record_contact(
                layout_index, float(measure_z), source='manual', parallax_sample=parallax_sample,
                xy_mm=self._image_layout_xy_mm_for_layout_index(layout_index),
            )
            self._image_electrode_z_store.save(
                getattr(_config, 'VISION_ELECTRODE_Z_SEED_PATH', 'vision_calibration/electrode_z_seed.json')
            )
        except Exception as exc:
            return f'Z seed: record failed ({exc})'
        n_samples = len(self._image_electrode_z_store.parallax_samples)
        z_plane = self._image_electrode_z_store.z_plane
        plane_suffix = f', plane({z_plane.n_points})' if z_plane is not None else ''
        parallax_word = 'refit' if fit_happened else 'so far'
        return (
            f'Z seed: E{layout_index + 1}={float(measure_z):.3f}mm'
            f', parallax {parallax_word}({n_samples})'
            + plane_suffix
        )

    def _image_current_temperature_c_for_bias(self, *, allow_live_read=False):
        """
        Best available "current furnace temperature" for tagging/evaluating
        the XY-bias correction -- prefers the active run row's stabilized
        target (self._active_temperature_target_c, set once a row's
        temperature step completes; see _run_worker) over a fresh serial
        read, since this is called from vision-processing hot paths where
        hitting the temperature controller's serial port on every call
        would be both slow and unnecessary (the row already waited for
        stability). Falls back to the temperature-safety monitor's last
        polled reading (self._temp_safety_last_pv_c, updated periodically
        during any run with TEMP_SAFETY_ENABLED) for the gap before a row's
        first temperature step completes.

        Both of those sources are automated-run-only -- outside of
        _run_worker, neither is ever populated. allow_live_read, if True,
        falls back to one real self.tc.get_temperature() serial read in
        that case, so manual "Search Z" (which now also collects XY-bias
        samples, not just Z-seed/parallax -- see
        _image_recalibrate_xy_bias_from_contact) still tags its samples
        with the furnace's actual temperature instead of always recording
        temperature_c=None when used outside a run. Only pass True from a
        call site that isn't a per-frame hot path -- e.g.
        _image_recalibrate_xy_bias_from_contact (once per confirmed
        contact) is fine; _image_project_pixel_to_stage_xy is NOT (called
        every camera tick via _image_probe_occluded_layout_indices), so it
        must keep the default False.

        None if nothing is available (or the live read itself fails) --
        callers must treat that as "temperature unknown," not an error.
        """
        if self._active_temperature_target_c is not None:
            return float(self._active_temperature_target_c)
        if self._temp_safety_last_pv_c is not None:
            return float(self._temp_safety_last_pv_c)
        if allow_live_read and self.tc is not None:
            try:
                return float(self.tc.get_temperature())
            except Exception:
                return None
        return None

    def _image_recalibrate_xy_bias_from_contact(self, layout_index):
        """
        Called right after a confirmed contact for layout_index -- both
        the automated run's AutoContactZ (_auto_contact_z_for_row) and
        manual "Search Z" (_image_seed_z_from_contact) call this, matching
        _image_record_electrode_z_contact's own "common glue for every
        contact-search success path" framing for Z-seed/parallax; XY-bias
        collection now follows that same pattern instead of being
        automated-run-only. At contact, the probe tip is physically
        co-located with the electrode, so a
        tight ROI probe search -- sized to this electrode's own tracked
        radius, not the generic user-drawn _image_probe_roi -- centered on
        its tracked pixel position gives a fresh, trustworthy (observed
        probe pixel, electrode's own tracked pixel) correspondence, both
        measured in the same frame at the same actual Z. (Deliberately NOT
        stage_to_pixel_xy(motor XY): that assumes the electrode's contact Z
        matches whatever Z the pixel<->stage affine calibration was solved
        at, which is Z-parallax-shaped error being folded into what should
        be a pure probe-vs-electrode pixel offset.) Rejected outright if
        the detection is implausibly far from the electrode's tracked
        pixel (see VISION_XY_BIAS_MAX_SAMPLE_PIXEL_DELTA_PX) -- more
        likely a false detection than a real offset, and with small
        per-temperature sample counts (see
        ElectrodeZCalibrationStore.get_xy_bias) one bad sample has more
        leverage than it used to when every temperature was pooled
        together. Otherwise accumulated into that temperature setpoint's
        own XY-bias correction, layered on top of the pixel<->stage affine
        calibration (see vision_stage_mapper.py::ProbeXYBiasCalibration) --
        never overwrites the base calibration itself. Never raises --
        returns a short status fragment for the caller to combine with
        the Z-seed fragment (see _image_set_combined_z_seed_status), or
        None for the "not enough information yet" early-outs below
        (silent, as before this was made returnable).
        """
        if self._image_pixel_stage_affine_calibration is None:
            return None
        if self._image_layout_tracked is None or not (0 <= layout_index < len(self._image_layout_tracked)):
            return None
        frame_bgr = self._image_current_frame_bgr()
        if frame_bgr is None:
            return None
        # _image_layout_tracked's Circle objects are mutated in place by
        # _image_monitor_tick on the main thread every tick -- see
        # _image_tracked_lock's definition comment.
        with self._image_tracked_lock:
            circle = self._image_layout_tracked[layout_index]
            r = float(circle.radius)
            smoothed_x, smoothed_y = circle.smoothed_x, circle.smoothed_y
        roi = (
            int(smoothed_x - r), int(smoothed_y - r),
            int(smoothed_x + r), int(smoothed_y + r),
        )
        try:
            if self._image_probe_detector is None:
                clahe_clip = getattr(_config, 'VISION_PROBE_CLAHE_CLIP', 4.0)
                self._image_probe_detector = ProbeDetector(clahe_clip=clahe_clip)
            tip = self._image_probe_detector.detect(frame_bgr, roi=roi)
            if tip is None or not tip.detected:
                return 'XY bias: probe not found'
            # Raw, as-observed residual -- used for the outlier-plausibility
            # check below, which is deliberately about "is the probe roughly
            # where we expected to find it" (a question about this touch's
            # own observation), not about the reconstructed absolute bias
            # (see stored_delta_x_px/stored_delta_y_px further down).
            raw_delta_x_px = tip.x - smoothed_x
            raw_delta_y_px = tip.y - smoothed_y
            # At true confirmed contact the probe tip and electrode center
            # should coincide almost exactly -- a detection much farther
            # than that is far more likely a false detection (e.g.
            # ProbeDetector locking onto a reflection, a known limitation)
            # than a genuine offset, and would otherwise corrupt this
            # setpoint's whole bucket (small per-temperature sample counts
            # give one bad sample a lot of leverage over the fit). A fixed
            # pixel distance, not scaled by the electrode's own tracked
            # radius (radius varies per electrode/DXF layout/camera zoom,
            # which would make the threshold itself hard to reason about).
            max_dist_px = getattr(_config, 'VISION_XY_BIAS_MAX_SAMPLE_PIXEL_DELTA_PX', None)
            if max_dist_px is not None:
                dist_px = math.hypot(raw_delta_x_px, raw_delta_y_px)
                if dist_px > float(max_dist_px):
                    return f'XY bias: rejected ({dist_px:.0f}px)'
            # Not a hot path (once per confirmed contact) -- safe to fall
            # back to a live temperature read when neither of the cheap
            # automated-run-only sources is available (e.g. manual Search
            # Z used outside of a run), so samples still get tagged with
            # the real furnace temperature instead of always None.
            temperature_c = self._image_current_temperature_c_for_bias(allow_live_read=True)
            bucket_tol_c = getattr(_config, 'XY_BIAS_TEMPERATURE_BUCKET_TOL_C', 1.0)
            # Once >= 3 samples exist for this bucket, the target this
            # electrode was actually aimed at was already shifted by the
            # bias fit active at that time (_image_last_applied_correction)
            # -- so tip.x/tip.y here measures the RESIDUAL left after that
            # correction, not the same targeting-independent quantity the
            # first (uncorrected) samples measured. A converged correction
            # legitimately drives that residual toward 0, and pooling
            # those near-zero residuals into the same median as the
            # original raw samples erodes the fit back toward "no
            # correction" even though the true underlying bias hasn't
            # changed. Back the applied shift back out of the recorded
            # "observed" pixel so every sample -- corrected touch or not --
            # estimates the same constant.
            applied_correction = self._image_last_applied_correction.get(layout_index) or {}
            applied_dx, applied_dy = applied_correction.get('xy_bias_px') or (0.0, 0.0)
            fit_happened = self._image_electrode_z_store.record_xy_bias_sample(
                observed_px=tip.x - applied_dx, observed_py=tip.y - applied_dy,
                expected_px=smoothed_x, expected_py=smoothed_y,
                temperature_c=temperature_c,
                bucket_tol_c=bucket_tol_c,
            )
            self._image_electrode_z_store.save(
                getattr(_config, 'VISION_ELECTRODE_Z_SEED_PATH', 'vision_calibration/electrode_z_seed.json')
            )
        except Exception as exc:
            return f'XY bias: failed ({exc})'
        temp_text = '?C' if temperature_c is None else f'{temperature_c:.0f}C'
        bucket_n = self._image_electrode_z_store.xy_bias_sample_count_near(
            temperature_c, bucket_tol_c=bucket_tol_c,
        )
        fit_word = 'refit' if fit_happened else 'so far'
        self._image_log_xy_correction_sample(
            layout_index=layout_index, temperature_c=temperature_c,
            delta_x_px=raw_delta_x_px - applied_dx, delta_y_px=raw_delta_y_px - applied_dy,
            raw_delta_x_px=raw_delta_x_px, raw_delta_y_px=raw_delta_y_px,
            bucket_n=bucket_n,
        )
        return f'XY bias: {fit_word}({bucket_n}) {temp_text}'

    def _image_log_xy_correction_sample(
        self, *, layout_index, temperature_c, delta_x_px, delta_y_px,
        raw_delta_x_px=None, raw_delta_y_px=None, bucket_n,
    ):
        """
        Append one row to VISION_XY_CORRECTION_LOG_CSV_PATH and one summary
        line to the run log, combining this touch's own XY-bias sample
        (delta_x_px/delta_y_px -- the same values just folded into
        electrode_z_seed.json's xy_bias_samples, i.e. with any already-
        applied correction backed back out -- see the comment at this
        function's call site) with whatever parallax/XY-bias correction
        was actually applied the last time this electrode was targeted
        (stashed by _image_resolve_live_tracked_xy /
        _image_sync_manual_target_from_selected into
        self._image_last_applied_correction). raw_delta_x_px/
        raw_delta_y_px, if given, are the as-observed residual BEFORE that
        back-out -- included so a run can be reviewed to confirm the
        applied correction is actually shrinking the real, raw
        misalignment over time, since sample_delta_x_px/y_px alone
        (the reconstructed, correction-independent value) won't visibly
        change just because targeting is working. Lets a run be reviewed
        afterward to see whether the applied correction tracked the real,
        observed drift over time, or lagged/diverged from it. Missing
        applied-correction data (e.g. this electrode was targeted from raw
        CSV X_mm/Y_mm, never through the live-tracked projection) is logged
        as blank/"none applied", not an error. Never raises -- a logging
        failure must not block XY-bias sample recording, which has already
        succeeded by the time this is called.
        """
        applied = self._image_last_applied_correction.get(layout_index) or {}
        parallax_shift = applied.get('parallax_shift_px')
        parallax_delta_z = applied.get('parallax_delta_z_mm')
        xy_bias_applied = applied.get('xy_bias_px')
        row = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'layout_index': layout_index,
            'electrode_id': layout_index + 1,
            'temperature_c': '' if temperature_c is None else f'{temperature_c:.2f}',
            'parallax_shift_x_px': '' if parallax_shift is None else f'{parallax_shift[0]:.3f}',
            'parallax_shift_y_px': '' if parallax_shift is None else f'{parallax_shift[1]:.3f}',
            'parallax_delta_z_mm': '' if parallax_delta_z is None else f'{parallax_delta_z:.4f}',
            'xy_bias_applied_x_px': '' if xy_bias_applied is None else f'{xy_bias_applied[0]:.3f}',
            'xy_bias_applied_y_px': '' if xy_bias_applied is None else f'{xy_bias_applied[1]:.3f}',
            'xy_bias_bucket_n': bucket_n,
            'sample_delta_x_px': f'{delta_x_px:.3f}',
            'sample_delta_y_px': f'{delta_y_px:.3f}',
            'raw_sample_delta_x_px': '' if raw_delta_x_px is None else f'{raw_delta_x_px:.3f}',
            'raw_sample_delta_y_px': '' if raw_delta_y_px is None else f'{raw_delta_y_px:.3f}',
        }
        if self._image_xy_correction_csv_log_enabled:
            try:
                path = getattr(
                    _config, 'VISION_XY_CORRECTION_LOG_CSV_PATH', 'vision_calibration/xy_correction_log.csv'
                )
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                write_header = not os.path.exists(path)
                with open(path, 'a', newline='', encoding='utf-8') as fh:
                    writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
                    if write_header:
                        writer.writeheader()
                    writer.writerow(row)
            except Exception as exc:
                self._log(f"  [XY correction log] CSV write failed: {exc}")

        parallax_text = (
            f"parallax=({parallax_shift[0]:+.1f}, {parallax_shift[1]:+.1f})px"
            if parallax_shift is not None else "parallax=none applied"
        )
        bias_text = (
            f"bias=({xy_bias_applied[0]:+.1f}, {xy_bias_applied[1]:+.1f})px"
            if xy_bias_applied is not None else "bias=none applied"
        )
        self._log(
            f"  [XY correction] E{layout_index + 1}: {parallax_text}, {bias_text}, "
            f"sample delta=({delta_x_px:+.1f}, {delta_y_px:+.1f})px"
        )

    def _image_seed_z_from_contact(self):
        if self._image_selected_target is None:
            self._image_z_seed_status_var.set('Z seed failed: lock a target first')
            return
        if not self._image_target_within_move_tolerance():
            self._image_z_seed_status_var.set(
                'Z seed failed: stage XY is not within tolerance of the locked target -- '
                'the contact search runs at the stage\'s current XY, so it would '
                'measure the wrong electrode otherwise'
            )
            return
        if self.motor is None:
            self._image_z_seed_status_var.set('Z seed failed: motor is not connected')
            return
        target = self._image_selected_target.copy()
        layout_index = self._image_layout_index_for_target(target)
        self._image_z_seed_status_var.set('Z seed: searching...')
        self._image_set_mode_button_active(self._image_search_z_button, True)
        try:
            found_z, measure_z, parallax_sample = self._execute_contact_z_search(
                base_z=float(self.motor.get_position('Z')),
                start_offset=float(self._contact_search['start_offset'].get()),
                step_mm=float(self._contact_search['step_mm'].get()),
                max_beyond_seed_mm=float(self._contact_search['max_beyond_seed_mm'].get()),
                ocv_threshold=float(self._contact_search['ocv_threshold'].get()),
                settle_s=float(self._contact_search['settle_s'].get()),
                engage_mm=float(self._contact_search['engage_mm'].get()),
                status_prefix='Search Z',
                probe_pixel_sample_fn=lambda: self._image_probe_tip_pixel_now(layout_index=layout_index),
                ocv_status_var=self._image_z_seed_ocv_var,
                z_status_var=self._image_z_seed_status_var,
                track_manual_target_z=not self._image_lock_z_var.get(),
            )
        except Exception as exc:
            self._image_z_seed_status_var.set(f'Z seed failed: {exc}')
            return
        finally:
            self._image_set_mode_button_active(self._image_search_z_button, False)
        z_status = self._image_record_electrode_z_contact_for_target(target, measure_z, parallax_sample)
        # Matches _auto_contact_z_for_row's own call after a confirmed
        # contact -- manual "Search Z" is otherwise a full contact-search
        # success path too (see _image_record_electrode_z_contact's
        # docstring), so it should build up the same XY-bias calibration
        # an automated run would, not just Z-seed/parallax.
        xy_status = None
        if layout_index is not None:
            xy_status = self._image_recalibrate_xy_bias_from_contact(layout_index)
        self._image_set_combined_z_seed_status(z_status, xy_status)

    def _image_save_z_seed_store(self, path=None):
        if path is None:
            path = filedialog.asksaveasfilename(
                title='Save Z seed',
                defaultextension='.json',
                initialfile=os.path.basename(
                    getattr(_config, 'VISION_ELECTRODE_Z_SEED_PATH', 'vision_calibration/electrode_z_seed.json')
                ),
                filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            )
        if not path:
            return False
        try:
            self._image_electrode_z_store.save(path)
            n = len(self._image_electrode_z_store.electrode_z_seeds)
            self._image_z_seed_status_var.set(f'Z seed: saved ({n} electrode(s)) to {os.path.basename(path)}')
        except Exception as exc:
            self._image_z_seed_status_var.set(f'Z seed: save failed ({exc})')
            return False
        return True

    def _image_load_z_seed_store(self, path=None):
        # Browse for a file, matching every other "Load ..." button in
        # this tab (Load DXF Layout, Load Circle Params, Load Alignment,
        # Load probe pixel<->stage calibration) instead of always reading
        # from the fixed default config path. Uses ElectrodeZCalibrationStore.from_dict
        # directly (not the tolerant .load() classmethod, which silently
        # returns an empty store on any failure) so a bad path/corrupt
        # file actually reports "load failed" instead of quietly wiping
        # the current store with an empty one.
        if path is None:
            path = filedialog.askopenfilename(
                title='Load Z seed',
                filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            )
        if not path:
            return False
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
            store = ElectrodeZCalibrationStore.from_dict(payload)
        except Exception as exc:
            self._image_z_seed_status_var.set(f'Z seed: load failed ({exc})')
            return False
        self._image_electrode_z_store = store
        n = len(store.electrode_z_seeds)
        n_parallax = len(store.parallax_samples)
        if n_parallax:
            parallax_suffix = f', {n_parallax} parallax sample(s)'
            parallax_suffix += ' (slope fit)' if store.z_parallax is not None else ' (not enough for a slope fit)'
        else:
            parallax_suffix = ', no parallax samples'
        self._image_z_seed_status_var.set(
            f'Z seed: loaded {os.path.basename(path)} ({n} electrode(s){parallax_suffix})'
        )
        return True

    def _image_clear_z_seed_store(self):
        if not messagebox.askyesno(
            'Clear Z Seed',
            'Clear every recorded electrode Z seed, parallax sample, XY-bias '
            'sample, and Z-surface fit? This cannot be undone.',
        ):
            return
        try:
            self._image_electrode_z_store.clear()
            self._image_electrode_z_store.save(
                getattr(_config, 'VISION_ELECTRODE_Z_SEED_PATH', 'vision_calibration/electrode_z_seed.json')
            )
            self._image_z_seed_status_var.set('Z seed: cleared')
        except Exception as exc:
            self._image_z_seed_status_var.set(f'Z seed: clear failed ({exc})')

    def _image_refit_z_surface(self):
        try:
            store = self._image_electrode_z_store
            fit_ok = store.refit_z_plane()
            store.save(
                getattr(_config, 'VISION_ELECTRODE_Z_SEED_PATH', 'vision_calibration/electrode_z_seed.json')
            )
        except Exception as exc:
            self._image_z_seed_status_var.set(f'Z surface fit failed: {exc}')
            return
        if fit_ok and store.z_plane is not None:
            z = store.z_plane
            self._image_z_seed_status_var.set(
                f'Z surface fit: a={z.a:.5f}, b={z.b:.5f}, c={z.c:.5f} '
                f'from {z.n_points} electrode(s)'
            )
        else:
            n_with_xy = sum(
                1 for seed in store.electrode_z_seeds.values() if seed.xy_mm is not None
            )
            self._image_z_seed_status_var.set(
                f'Z surface fit: not enough non-collinear seeded electrodes '
                f'({n_with_xy} with known XY, need >= 3)'
            )

    def _image_project_pixel_to_stage_xy(self, pixel_x, pixel_y, target=None, layout_index=None, debug_out=None):
        """
        Project an electrode pixel to stage XY, correcting for Z-parallax
        using that specific electrode's own known Z (not the probe's
        current Z -- see the plan history in docs/IMAGE_MONITOR_GUIDE.md for why),
        then for the probe's XY bias (a small constant pixel offset measured
        at AutoContactZ touches -- see
        vision_stage_mapper.py::ProbeXYBiasCalibration).

        layout_index, if given, is used directly instead of being resolved
        from target via _image_layout_index_for_target (callers that already
        know the electrode identity -- e.g. the run-worker row loop, which
        has no `target` Series at all -- can skip that target-shaped-object
        requirement entirely). "Known Z" is the electrode's own contact
        measurement if it has one, else a Z-surface plane estimate (fit from
        3+ OTHER electrodes' measurements across the layout) if one is
        available -- see
        vision/electrode_z_seed.py::ElectrodeZCalibrationStore.get_seed_or_estimate.
        Falls back to today's uncorrected projection whenever a correction
        isn't fitted yet or this electrode has neither an exact seed nor a
        plane estimate -- all "not enough information yet," not errors.

        debug_out, if given a dict, is populated in place with a
        human-readable 'message' key describing whether the Z-parallax
        correction was applied and why/why not -- every one of the gates
        below silently falls through to the uncorrected projection with no
        other visible signal, which made a real "parallax isn't doing
        anything" report impossible to diagnose without this. Callers that
        don't pass debug_out are completely unaffected (unchanged return
        contract).

        debug_out is also populated with structured (non-string) fields for
        callers that want to log/record the actual applied correction, not
        just a human-readable summary: 'parallax_shift_px' (dx, dy) or None,
        'parallax_delta_z_mm' or None, 'xy_bias_px' (bias_x, bias_y) or
        None, 'xy_bias_temperature_c' (the temperature used to select the
        bucket) or None. See _image_log_xy_correction_sample, which reads
        these back (stashed by layout_index) when a touch's own XY-bias
        sample gets recorded, to log what correction was actually in effect
        for that touch.
        """
        corrected_x, corrected_y = float(pixel_x), float(pixel_y)
        if layout_index is None:
            layout_index = self._image_layout_index_for_target(target)
        parallax = self._image_electrode_z_store.z_parallax
        if debug_out is not None:
            debug_out['message'] = 'parallax: no DXF-layout electrode'
            debug_out['parallax_shift_px'] = None
            debug_out['parallax_delta_z_mm'] = None
        if layout_index is not None and parallax is not None and self._image_pixel_stage_z_ref_mm is not None:
            xy_mm = self._image_layout_xy_mm_for_layout_index(layout_index)
            seed_z = self._image_electrode_z_store.get_seed_or_estimate(layout_index, xy_mm=xy_mm)
            if seed_z is not None:
                delta_z = seed_z - self._image_pixel_stage_z_ref_mm
                max_extrapolation = getattr(_config, 'VISION_Z_PARALLAX_MAX_EXTRAPOLATION_MM', 1.0)
                if parallax_within_trusted_range(parallax, delta_z, max_extrapolation):
                    corrected_x, corrected_y = correct_pixel_for_z_parallax(
                        parallax, pixel_x, pixel_y, delta_z,
                    )
                    if debug_out is not None:
                        dx, dy = corrected_x - float(pixel_x), corrected_y - float(pixel_y)
                        debug_out['message'] = (
                            f'parallax: Δz={delta_z:+.3f}mm, shift=({dx:+.1f}, {dy:+.1f})px'
                        )
                        debug_out['parallax_shift_px'] = (dx, dy)
                        debug_out['parallax_delta_z_mm'] = delta_z
                elif debug_out is not None:
                    debug_out['message'] = (
                        f'parallax: Δz={delta_z:+.3f} > '
                        f'max {max_extrapolation:.3f}mm)'
                    )
            elif debug_out is not None:
                debug_out['message'] = 'parallax: no Z seed'
        elif debug_out is not None and layout_index is not None:
            if parallax is None:
                debug_out['message'] = 'parallax: not calibrated'
            elif self._image_pixel_stage_z_ref_mm is None:
                debug_out['message'] = (
                    'parallax: no stage calibration'
                )
        temperature_c = self._image_current_temperature_c_for_bias()
        xy_bias = self._image_electrode_z_store.get_xy_bias(
            temperature_c,
            bucket_tol_c=getattr(_config, 'XY_BIAS_TEMPERATURE_BUCKET_TOL_C', 1.0),
        )
        pre_bias_x, pre_bias_y = corrected_x, corrected_y
        corrected_x, corrected_y = correct_pixel_for_xy_bias(xy_bias, corrected_x, corrected_y)
        if debug_out is not None:
            # The actual signed shift correct_pixel_for_xy_bias applied to
            # the pixel -- NOT the same thing as (bias.bias_x_px,
            # bias.bias_y_px). That raw fitted value never changes sign on
            # its own; only how it combines with the pixel does (see
            # correct_pixel_for_xy_bias's docstring for why it subtracts).
            # Logging the raw bias here previously made a real sign fix in
            # that function invisible in this debug_out/the CSV log, since
            # the logged number stayed identical before and after the fix.
            debug_out['xy_bias_px'] = (
                (corrected_x - pre_bias_x, corrected_y - pre_bias_y) if xy_bias is not None else None
            )
            debug_out['xy_bias_temperature_c'] = temperature_c
        return pixel_to_stage_xy(self._image_pixel_stage_affine_calibration, corrected_x, corrected_y)

    def _image_resolve_live_tracked_xy(self, row, csv_x, csv_y):
        """
        Resolve a run row's target X/Y from the electrode's live-tracked,
        parallax- and bias-corrected pixel position when available and
        plausible, falling back to the row's own CSV X_mm/Y_mm otherwise --
        same gate-and-fallback idiom as run_automation.py's headless
        AutoTrackXY row loop.

        "Plausible" is judged against the LAST position this run actually
        trusted for this electrode (self._image_run_trusted_positions), not
        the static CSV value: comparing against a fixed original baseline
        would eventually reject genuine, gradual, cumulative drift once
        enough of it has accumulated over a long run, even though each
        individual step was a small, plausible continuation of the last.
        Falls back to the CSV value as the comparison baseline only for an
        electrode's first touch this run, when no trusted position exists
        yet.

        Returns (x, y, source) where source is 'live-tracked' or 'csv'.
        Never raises.
        """
        electrode_id = self._extract_electrode_id(str(row.get('Label', '')))
        if electrode_id is None:
            return csv_x, csv_y, 'csv'
        layout_index = electrode_id - 1
        if (
            self._image_layout_tracked is None
            or not (0 <= layout_index < len(self._image_layout_tracked))
            or self._image_pixel_stage_affine_calibration is None
        ):
            return csv_x, csv_y, 'csv'
        try:
            # _image_layout_tracked's Circle objects are mutated in place
            # by _image_monitor_tick on the main thread every tick -- see
            # _image_tracked_lock's definition comment.
            with self._image_tracked_lock:
                circle = self._image_layout_tracked[layout_index]
                smoothed_x, smoothed_y = circle.smoothed_x, circle.smoothed_y
            correction_debug = {}
            tracked_x, tracked_y = self._image_project_pixel_to_stage_xy(
                smoothed_x, smoothed_y, layout_index=layout_index, debug_out=correction_debug,
            )
            self._image_last_applied_correction[layout_index] = correction_debug
        except Exception as exc:
            self._log(f"  Live tracking: projection failed for E{electrode_id} ({exc}); using CSV X_mm/Y_mm")
            return csv_x, csv_y, 'csv'

        trusted_xy = self._image_run_trusted_positions.get(layout_index)
        reference_xy = trusted_xy if trusted_xy is not None else (csv_x, csv_y)
        dist_mm = (
            ((tracked_x - reference_xy[0]) ** 2 + (tracked_y - reference_xy[1]) ** 2) ** 0.5
            if reference_xy[0] is not None and reference_xy[1] is not None else None
        )
        max_correction = getattr(_config, 'VISION_DRIFT_MAX_CORRECTION_MM', 0.5)
        if dist_mm is not None and dist_mm > max_correction:
            self._log(
                f"  Live tracking: E{electrode_id} correction is {dist_mm:.3f} mm from "
                f"{'last-trusted' if trusted_xy is not None else 'CSV'} position "
                f"(> {max_correction} mm sanity bound); falling back to CSV X_mm/Y_mm"
            )
            return csv_x, csv_y, 'csv'

        self._image_run_trusted_positions[layout_index] = (tracked_x, tracked_y)
        self._log(
            f"  Live tracking: E{electrode_id} position corrected to "
            f"X={tracked_x:.3f} mm, Y={tracked_y:.3f} mm (vision-tracked)"
        )
        return tracked_x, tracked_y, 'live-tracked'

    def _image_seeded_electrode_rows_source(self, omit_layout_indices=None):
        """
        List of (layout_index, stage_x_mm, stage_y_mm, seed_z_mm) for every
        electrode in the loaded DXF layout that has at least a Z seed --
        an exact AutoContactZ measurement or a Z-surface-plane estimate
        (ElectrodeZCalibrationStore.get_seed_or_estimate) -- for the
        condition generators' "Image Monitor seeded electrodes" tip-
        position-source mode.

        omit_layout_indices, if given, is a set/collection of 0-based
        layout indices to exclude from the result (electrodes inside it are
        skipped even if seeded) -- lets the generator UI omit a specific
        electrode subset instead of always using every seeded electrode.
        None (the default) means no filter: every seeded electrode, same as
        before this parameter existed.

        Nominal X/Y come from the electrode's current tracked pixel
        position (already parallax+bias corrected via
        _image_project_pixel_to_stage_xy) when live tracking is active,
        falling back to the DXF layout's own projected template position
        otherwise -- either way this is only a *starting* nominal value:
        the run-worker row loop re-corrects it live via
        _image_resolve_live_tracked_xy at move time regardless.

        Raises RuntimeError with a message suitable for showing the user
        directly (messagebox) if the layout/calibration aren't ready, or if
        no (selected) electrode has a seed yet.
        """
        if self._image_layout_model is None:
            raise RuntimeError('Load and accept a DXF layout in the Image Monitor tab first.')
        if self._image_pixel_stage_affine_calibration is None:
            raise RuntimeError('Solve the probe pixel<->stage calibration in the Image Monitor tab first.')
        layout = self._image_layout_model
        projected = None
        rows = []
        for layout_index in range(layout.n):
            if omit_layout_indices is not None and layout_index in omit_layout_indices:
                continue
            xy_mm = self._image_layout_xy_mm_for_layout_index(layout_index)
            seed_z = self._image_electrode_z_store.get_seed_or_estimate(layout_index, xy_mm=xy_mm)
            if seed_z is None:
                continue
            if self._image_layout_tracked is not None and layout_index < len(self._image_layout_tracked):
                circle = self._image_layout_tracked[layout_index]
                px, py = circle.smoothed_x, circle.smoothed_y
            else:
                if projected is None:
                    projected = layout.project_all()
                px, py = projected[layout_index]
            stage_x, stage_y = self._image_project_pixel_to_stage_xy(px, py, layout_index=layout_index)
            rows.append((layout_index, float(stage_x), float(stage_y), float(seed_z)))
        if not rows:
            raise RuntimeError(
                'No (selected) electrodes have a Z seed yet -- touch at least one electrode first '
                '(AutoContactZ or Search Z).'
            )
        return rows

    # ══════════════════════════════════════════════════════════════════════
    # DXF-layout-driven semi-manual electrode alignment
    # ══════════════════════════════════════════════════════════════════════

    def _image_reset_layout_alignment_state(self):
        self._image_layout_drawn_circles = []
        self._image_layout_alignment_pairs = []
        self._image_layout_base_transform = None
        self._image_layout_nudge_scale_x_var.set(0)
        self._image_layout_nudge_scale_y_var.set(0)
        self._image_layout_nudge_angle_var.set(0)
        self._image_layout_nudge_translate_x_var.set(0)
        self._image_layout_nudge_translate_y_var.set(0)
        self._image_layout_nudge_shear_x_var.set(0)
        self._image_layout_nudge_shear_y_var.set(0)
        self._image_layout_alignment_status_var.set('Alignment: not fit')
        # A previously-accepted seed's tracked Circles carry layout_index
        # values into the OLD self._image_layout_model.template -- loading a
        # new DXF replaces that model wholesale, so a stale seed here would
        # feed detect_and_refit_frame indices that no longer match anything.
        self._image_layout_seed_map = None
        self._image_layout_tracked = None
        self._image_populate_layout_pair_combo()

    def _image_load_dxf_layout(self, path=None):
        if not VISION_DXF_LAYOUT_AVAILABLE:
            self._image_layout_status_var.set(
                'Layout load failed: ezdxf is not installed (see requirements-win7-optional-vision.txt)'
            )
            return
        if path is None:
            path = filedialog.askopenfilename(
                title='Select DXF layout file',
                filetypes=[('DXF files', '*.dxf'), ('All files', '*.*')],
            )
        if not path:
            return
        try:
            circles = extract_circles_from_dxf(path)
            circles = shift_to_origin(circles)
            if not circles:
                raise RuntimeError('no circles found in DXF')
            warning = warn_if_spacing_implausible(circles)
        except Exception as exc:
            self._image_layout_model = None
            self._image_layout_status_var.set(f'Layout load failed: {exc}')
            self._image_draw_dxf_layout_preview()
            return
        positions = np.array([[c['x'], c['y']] for c in circles], dtype=np.float32)
        radii = np.array([c['radius'] for c in circles], dtype=np.float32)
        self._image_layout_model = LayoutModel(positions, radii)
        self._image_reset_layout_alignment_state()
        status = f'Layout: loaded {len(circles)} electrode(s) from {os.path.basename(path)}'
        if warning:
            status += f' -- WARNING: {warning}'
        save_error = self._image_auto_save_layout_json()
        if save_error is not None:
            status += f' -- WARNING: auto-save failed ({save_error})'
        self._image_layout_status_var.set(status)
        self._image_draw_dxf_layout_preview()

    def _image_draw_dxf_layout_preview(self):
        """
        Schematic top-down (mm-space) plot of the raw loaded DXF layout
        geometry -- independent of transform/Fit Transform, a fixed
        reference view of what was loaded, shown next to the alignment
        controls so the operator can sanity-check it before ever aligning
        to the camera.
        """
        canvas = getattr(self, '_image_dxf_layout_preview_canvas', None)
        if canvas is None:
            return
        canvas.delete('all')
        width = max(canvas.winfo_width(), 220)
        height = max(canvas.winfo_height(), 220)
        layout = self._image_layout_model
        if layout is None or layout.n == 0:
            canvas.create_text(
                width / 2, height / 2, text='No layout loaded',
                fill='#888', font=('Segoe UI', 10),
            )
            return
        template = layout.template
        if layout.template_radii is not None:
            radii = layout.template_radii
        elif len(template) > 1:
            radii = []
            for i in range(len(template)):
                dists = np.linalg.norm(template - template[i], axis=1)
                dists = dists[dists > 0]
                nn_dist = float(dists.min()) if len(dists) else 1.0
                radii.append(nn_dist * 0.35)
            radii = np.array(radii, dtype=np.float32)
        else:
            radii = np.array([1.0], dtype=np.float32)

        xs, ys = template[:, 0], template[:, 1]
        max_r = float(radii.max())
        x_min, x_max = float(xs.min()) - max_r, float(xs.max()) + max_r
        y_min, y_max = float(ys.min()) - max_r, float(ys.max()) + max_r
        span_x = max(x_max - x_min, 1e-6)
        span_y = max(y_max - y_min, 1e-6)
        scale = 0.9 * min(width / span_x, height / span_y)
        cx_data, cy_data = (x_min + x_max) / 2.0, (y_min + y_max) / 2.0
        cx_canvas, cy_canvas = width / 2.0, height / 2.0

        for i in range(len(template)):
            x, y = float(template[i, 0]), float(template[i, 1])
            # No Y flip here: extract_circles_from_dxf() already negates Y
            # at load time (DXF is Y-up, image/pixel space is Y-down; see
            # its flip_y docstring), so layout.template's Y is already in
            # image convention, same as project_all()/every other consumer
            # of this array. Flipping again here would just re-mirror it.
            px = cx_canvas + (x - cx_data) * scale
            py = cy_canvas + (y - cy_data) * scale
            r = max(float(radii[i]) * scale, 2.0)
            canvas.create_oval(
                px - r, py - r, px + r, py + r, outline='#1976d2', width=1.5,
            )
            # Always label, even a tiny circle -- placed just outside it
            # (not centered inside) so the number stays legible instead of
            # being squeezed into/overflowing a small circle.
            canvas.create_text(
                px + r + 3, py, text=str(i + 1), anchor='w',
                font=('Segoe UI', 7), fill='#1976d2',
            )

    def _image_auto_save_layout_json(self):
        """
        Best-effort write of the just-loaded layout to
        config.VISION_ELECTRODE_LAYOUT_PATH -- headless run_automation.py's
        AutoTrackXY drift correction hard-requires a layout JSON at that
        fixed path (LayoutModel.from_json(VISION_ELECTRODE_LAYOUT_PATH),
        run_automation.py) to reconstruct the layout alongside a saved
        alignment. This replaces a manual "Save Layout JSON" step -- it
        always runs right after a DXF loads successfully, so the file stays
        in sync with whatever layout is currently active. Returns None on
        success, or the exception as a string on failure (never raises --
        a failed auto-save shouldn't block using the layout in this
        session, it only affects the separate headless workflow).
        """
        layout = self._image_layout_model
        if layout is None:
            return 'no layout loaded'
        path = getattr(_config, 'VISION_ELECTRODE_LAYOUT_PATH', 'vision_calibration/electrode_layout.json')
        circles = [
            {'x': float(x), 'y': float(y), 'radius': float(r)}
            for (x, y), r in zip(
                layout.template.tolist(),
                (layout.template_radii.tolist() if layout.template_radii is not None else [10.0] * layout.n),
            )
        ]
        try:
            write_layout_json(circles, path)
        except Exception as exc:
            return str(exc)
        return None

    def _image_load_circle_params_file(self):
        path = filedialog.askopenfilename(
            title='Load Circle params',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
        )
        if not path:
            return
        try:
            self._image_circle_params = load_hough_params(path)
        except Exception as exc:
            self._image_circle_params = None
            self._image_layout_status_var.set(f'Circle params load failed: {exc}')
            return
        self._image_layout_status_var.set(
            f'Layout: Circle params loaded from {os.path.basename(path)}'
        )

    def _image_save_tuning_snapshot(self, frame_bgr, image_filename):
        """
        Write frame_bgr to vision_calibration/ (this project's existing
        convention for calibration artifacts) and return the path. Purely
        for record-keeping/reproducibility -- the tuning windows
        themselves use the in-memory frame directly, they don't need to
        re-read this file. Raises on failure; callers decide how to report
        that.
        """
        frame_dir = os.path.join(os.path.dirname(__file__), 'vision_calibration')
        os.makedirs(frame_dir, exist_ok=True)
        image_path = os.path.join(frame_dir, image_filename)
        if cv2.imwrite(image_path, frame_bgr) is False:
            raise RuntimeError(f'could not write {image_path}')
        return image_path

    def _image_open_circle_tuning_window(self):
        frame_bgr = self._image_current_frame_bgr()
        if frame_bgr is None:
            self._image_layout_status_var.set('Tuning GUI failed: start the camera first')
            return
        try:
            image_path = self._image_save_tuning_snapshot(frame_bgr, 'circle.png')
        except Exception as exc:
            self._image_layout_status_var.set(f'Tuning GUI failed: {exc}')
            return
        CircleTuningWindow(self, frame_bgr, image_path=image_path, status_var=self._image_layout_status_var)

    def _image_open_probe_tuning_window(self):
        frame_bgr = self._image_current_frame_bgr()
        if frame_bgr is None:
            self._image_probe_status_var.set('Tuning GUI failed: start the camera first')
            return
        try:
            image_path = self._image_save_tuning_snapshot(frame_bgr, 'probe.png')
        except Exception as exc:
            self._image_probe_status_var.set(f'Tuning GUI failed: {exc}')
            return
        ProbeTuningWindow(self, frame_bgr, image_path=image_path, status_var=self._image_probe_status_var)

    def _image_load_probe_params_file(self):
        path = filedialog.askopenfilename(
            title='Load probe params',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
        )
        if not path:
            return
        try:
            kwargs = load_probe_params(path)
        except Exception as exc:
            self._image_probe_status_var.set(f'Probe params load failed: {exc}')
            return
        self._image_probe_detector = ProbeDetector(**kwargs)
        self._image_probe_status_var.set(
            f'Probe: params loaded from {os.path.basename(path)}'
        )

    def _image_resolve_live_detector_params(self):
        """
        Kwargs for detect_live_microscope_electrode_map_rgb from the loaded
        circle params (one "Load Circle Params" file retunes both the live
        detection loop and seeded live tracking's redetection search -- see
        _image_resolve_drift_refit_params), or {} to use that function's
        own built-in defaults when nothing has been loaded.

        Deliberately does NOT fall back to any config default when unset --
        there's no longer anywhere else in this file with a validated
        default for these parameters to borrow (the DXF-alignment click
        used to snap to a nearby detected circle via one; it now just uses
        the raw click), so inventing one here would silently change
        live-detection behavior for every user who has never touched Load
        Circle Params.
        """
        if self._image_circle_params is None:
            return {}
        params = self._image_circle_params
        expected_radius = float(params['expected_radius'])
        radius_tolerance = float(params['radius_tolerance'])
        # cv2.HoughCircles (called downstream in
        # detect_live_microscope_electrode_map_rgb) requires minRadius/
        # maxRadius to be actual Python int -- passing a float here (even
        # a whole number like 30.0) raises "Argument 'minRadius' is
        # required to be an integer". expected_radius/radius_tolerance are
        # always floats (see vision/circle_detector.py::load_hough_params),
        # so these must be explicitly rounded to int, not just left as the
        # result of float arithmetic.
        return {
            'clahe_clip': params['clahe_clip'],
            'clahe_tile': params['clahe_tile'],
            'param1': params['param1'],
            'param2': params['param2'],
            'min_radius_px': int(round(max(1, expected_radius - radius_tolerance))),
            'max_radius_px': int(round(expected_radius + radius_tolerance)),
        }

    def _image_resolve_drift_refit_params(self):
        """
        Kwargs for vision.electrode_drift.detect_and_refit_frame's
        preprocessing/ROI-search parameters, from the same loaded circle
        params as _image_resolve_live_detector_params (one "Load Circle
        Params" file retunes seeded live tracking's redetection search
        too), or {} to use that function's own built-in defaults (which
        already match this file's config fallback defaults) when nothing
        has been loaded.
        """
        if self._image_circle_params is None:
            return {}
        params = self._image_circle_params
        return {
            'clahe_clip': params['clahe_clip'],
            'clahe_tile': params['clahe_tile'],
            'bilateral_d': params['bilateral_d'],
            'bilateral_sigma': params['bilateral_sigma'],
            'param1': params['param1'],
            'param2_roi': params['param2'],
        }

    def _image_tracked_circles_to_table(self, tracked, seed_meta, occluded_layout_indices=None):
        """
        Build a detections DataFrame from a vision.electrode_drift-tracked
        Circle list, carrying forward each electrode's row_index/col_index/
        size_group/source unchanged from the seed table tracking started
        from (indexed by layout_index, which always matches seed_meta's row
        order by construction -- see _image_promote_current_overlay_to_seed/
        _image_accept_layout_alignment). This keeps e.g. DXF-layout
        col_index stable for the Z-seed workflow's target->layout_index
        lookup (_image_layout_index_for_target) for the whole tracking
        session, unlike refine_circular_electrode_map_rgb's old behavior of
        appending "_tracked" to source on every redetection.

        occluded_layout_indices, if given, marks electrodes currently
        skipped as probe-occluded (see _image_probe_occluded_layout_indices)
        with their own 'occluded' column so annotate_detections can color
        them distinctly from an ordinary detection failure -- temporary
        visual aid for verifying the occlusion region is computed
        correctly, not meant to be permanent.
        """
        rows = []
        for c in tracked:
            meta_row = seed_meta.iloc[c.layout_index]
            rows.append({
                'x_px': c.smoothed_x,
                'y_px': c.smoothed_y,
                'radius_px': c.radius,
                'row_index': meta_row['row_index'],
                'col_index': meta_row['col_index'],
                'size_group': meta_row['size_group'],
                'source': meta_row['source'],
                # True if Hough actually re-found this electrode this
                # cycle, False if its position was only carried forward
                # from tracking -- lets annotate_detections color
                # genuinely-detected vs. merely-projected circles
                # differently.
                'detected': bool(c.detected),
                'occluded': bool(occluded_layout_indices and c.layout_index in occluded_layout_indices),
            })
        return pd.DataFrame(rows)

    def _image_populate_layout_pair_combo(self):
        if not hasattr(self, '_image_layout_pair_combo'):
            return
        if self._image_layout_model is None:
            self._image_layout_pair_combo.configure(values=())
            return
        # 1-based to match the DXF layout preview's electrode labels
        # (_image_draw_dxf_layout_preview) -- _image_confirm_layout_pair
        # converts back to a 0-based layout_idx when parsing this.
        values = [
            f'{i + 1} ({x:.1f}, {y:.1f})'
            for i, (x, y) in enumerate(self._image_layout_model.template.tolist())
        ]
        self._image_layout_pair_combo.configure(values=values)

    def _run_on_main_thread(self, fn, *args, **kwargs):
        """
        Runs fn(*args, **kwargs) immediately if already on the Tkinter
        mainloop thread, otherwise marshals it via self.after(0, ...).

        Covers the non-Variable half of the cross-thread Tk hazard (see
        _ThreadSafeVariableMixin's docstring for the .set() half):
        Button.configure(), Canvas create_*/delete, PhotoImage, or any
        other direct widget method call is undefined behavior from a
        background thread and can corrupt Tcl's interpreter state.
        Building the check into the callee (here) rather than trusting
        every call site to remember self.after(0, ...) protects future
        callers too.
        """
        if threading.current_thread() is threading.main_thread():
            return fn(*args, **kwargs)
        # tkinter's after(ms, func, *args) only forwards positional args
        # to func -- it has no **kwargs of its own, so kwargs must be
        # bound into the callable first. A plain closure is used instead
        # of functools.partial because tkinter's after() internally does
        # callit.__name__ = func.__name__, which raises AttributeError
        # for a partial object (no __name__ attribute).
        def _call():
            fn(*args, **kwargs)
        self.after(0, _call)

    def _image_set_mode_button_active(self, button, active):
        """
        Recolor a mode/process button to signal it's currently active
        (Select Probe ROI while dragging, Draw Electrode Circles while
        placing, Search Z while a search is running). Best-effort -- a
        missing button reference or a style error should never break the
        mode/process itself.
        """
        if button is None:
            return

        def _apply():
            try:
                button.configure(style='ImageModeActive.TButton' if active else 'TButton')
            except Exception:
                pass

        self._run_on_main_thread(_apply)

    def _image_toggle_layout_draw_mode(self):
        if self._image_layout_model is None:
            self._image_layout_status_var.set('Draw failed: load a DXF/layout first')
            return
        self._image_layout_draw_mode = not self._image_layout_draw_mode
        self._image_layout_alignment_status_var.set(
            f"Alignment: {'draw mode ON -- click the live view to add electrode circles' if self._image_layout_draw_mode else 'draw mode off'}"
        )
        self._image_set_mode_button_active(self._image_draw_circles_button, self._image_layout_draw_mode)
        if self._image_layout_draw_mode:
            self._image_populate_layout_pair_combo()

    def _image_undo_last_drawn_circle(self):
        if self._image_layout_drawn_circles:
            self._image_layout_drawn_circles.pop()
            self._image_layout_alignment_pairs = [
                (d, l) for d, l in self._image_layout_alignment_pairs
                if d < len(self._image_layout_drawn_circles)
            ]

    def _image_clear_drawn_circles(self):
        self._image_layout_drawn_circles = []
        self._image_layout_alignment_pairs = []
        self._image_layout_alignment_status_var.set('Alignment: not fit')

    def _image_confirm_layout_pair(self):
        drawn_text = (self._image_layout_pair_drawn_var.get() or '').strip()
        layout_text = self._image_layout_pair_combo.get() or ''
        if not drawn_text or not layout_text:
            self._image_layout_alignment_status_var.set(
                'Pair failed: enter a drawn circle # and pick a layout electrode #'
            )
            return
        try:
            # Both the drawn-circle # field and the layout electrode #
            # dropdown are 1-based (matching the live overlay's "#N" labels
            # and the DXF layout preview's numbers) -- convert to the
            # 0-based indices used internally.
            drawn_number = int(drawn_text)
            drawn_idx = drawn_number - 1
            layout_idx = int(layout_text.split(' ', 1)[0]) - 1
        except Exception:
            self._image_layout_alignment_status_var.set('Pair failed: could not parse indices')
            return
        if drawn_idx < 0 or drawn_idx >= len(self._image_layout_drawn_circles):
            self._image_layout_alignment_status_var.set(
                f'Pair failed: drawn circle #{drawn_number} does not exist'
            )
            return
        self._image_layout_alignment_pairs = [
            (d, l) for d, l in self._image_layout_alignment_pairs
            if d != drawn_idx and l != layout_idx
        ]
        self._image_layout_alignment_pairs.append((drawn_idx, layout_idx))
        self._image_layout_alignment_status_var.set(
            f'Alignment: {len(self._image_layout_alignment_pairs)} pair(s) confirmed'
        )

    def _image_undo_last_layout_pair(self):
        if self._image_layout_alignment_pairs:
            self._image_layout_alignment_pairs.pop()
            self._image_layout_alignment_status_var.set(
                f'Alignment: {len(self._image_layout_alignment_pairs)} pair(s) confirmed'
            )

    def _image_fit_layout_alignment(self):
        if self._image_layout_model is None:
            self._image_layout_alignment_status_var.set('Fit failed: load a DXF/layout first')
            return
        if len(self._image_layout_alignment_pairs) < 3:
            self._image_layout_alignment_status_var.set(
                f'Fit failed: need at least 3 pairs ({len(self._image_layout_alignment_pairs)} present)'
            )
            return

        image_points = []
        layout_indices = []
        for drawn_idx, layout_idx in self._image_layout_alignment_pairs:
            cx, cy = self._image_layout_drawn_circles[drawn_idx]['center']
            image_points.append((float(cx), float(cy)))
            layout_indices.append(layout_idx)

        fit_result = fit_layout_affine(self._image_layout_model, image_points, layout_indices)
        if fit_result is None:
            self._image_layout_alignment_status_var.set('Fit failed: affine solve did not converge')
            return
        matrix, _inliers = fit_result
        self._image_layout_base_transform = matrix
        self._image_layout_nudge_scale_x_var.set(0)
        self._image_layout_nudge_scale_y_var.set(0)
        self._image_layout_nudge_angle_var.set(0)
        self._image_layout_nudge_translate_x_var.set(0)
        self._image_layout_nudge_translate_y_var.set(0)
        self._image_layout_nudge_shear_x_var.set(0)
        self._image_layout_nudge_shear_y_var.set(0)
        self._image_layout_alignment_status_var.set(
            f'Alignment: fit from {len(image_points)} pair(s); review overlay and nudge if needed, then Accept'
        )

    def _image_apply_layout_nudge(self):
        if self._image_layout_base_transform is None or self._image_layout_model is None:
            return
        frame_shape = self._image_render_shape
        center = (frame_shape[1] / 2.0, frame_shape[0] / 2.0) if frame_shape else (320.0, 240.0)
        nudged = apply_manual_nudge(
            self._image_layout_base_transform,
            scale_x_permille=int(self._image_layout_nudge_scale_x_var.get()),
            scale_y_permille=int(self._image_layout_nudge_scale_y_var.get()),
            angle_deg_x10=int(self._image_layout_nudge_angle_var.get()),
            translate_x_px=int(self._image_layout_nudge_translate_x_var.get()),
            translate_y_px=int(self._image_layout_nudge_translate_y_var.get()),
            shear_x_permille=int(self._image_layout_nudge_shear_x_var.get()),
            shear_y_permille=int(self._image_layout_nudge_shear_y_var.get()),
            center=center,
        )
        self._image_layout_model.transform = nudged

    def _image_layout_circles_to_seed_table(self, projected_circles):
        """Convert project_all_circles() output into the DataFrame schema the
        live-tracking pipeline (_image_tracked_circles_to_table /
        annotate_detections) expects. col_index is 1-based and directly
        matches the E<N> electrode-number convention used elsewhere in this
        project (layout_index + 1)."""
        rows = [
            {
                'x_px': float(entry['center'][0]),
                'y_px': float(entry['center'][1]),
                'radius_px': float(entry['radius']),
                'row_index': 1,
                'col_index': layout_index + 1,
                'size_group': 'dxf_layout',
                'source': 'dxf_layout',
            }
            for layout_index, entry in enumerate(projected_circles)
        ]
        return pd.DataFrame(rows)

    def _image_accept_layout_alignment(self):
        if self._image_layout_model is None or self._image_layout_model.transform is None:
            self._image_layout_alignment_status_var.set('Accept failed: fit a transform first')
            return
        try:
            circles = project_all_circles(self._image_layout_model)
        except Exception as exc:
            self._image_layout_alignment_status_var.set(f'Accept failed: {exc}')
            return
        # Seed live tracking with the DXF-derived, CAD-accurate electrode
        # positions -- no manual annotation or reference frame needed.
        # self._image_layout_model is reused directly (not copied) as the
        # tracking layout, so detect_and_refit_frame's periodic affine
        # re-fit keeps this same model's transform current with observed
        # drift, benefiting every other reader of it (Z-seed projection,
        # the alignment overlay) too.
        self._image_layout_seed_map = self._image_layout_circles_to_seed_table(circles)
        self._image_layout_tracked = [
            Circle(
                x=float(entry['center'][0]),
                y=float(entry['center'][1]),
                radius=float(entry['radius']),
                layout_index=i,
                on_image=True,
            )
            for i, entry in enumerate(circles)
        ]
        # The manual-click markers used to fit/preview the transform are
        # redundant now that live tracking is seeded from the accepted DXF
        # layout itself -- clear them so they stop cluttering the overlay
        # (same pairing _image_clear_drawn_circles uses).
        self._image_layout_drawn_circles = []
        self._image_layout_alignment_pairs = []
        self._image_layout_alignment_status_var.set(
            f'Alignment: accepted, {len(circles)} electrode(s) projected and seeded for live tracking'
        )

    def _image_draw_layout_alignment_overlay(self, frame_bgr):
        if cv2 is None:
            return frame_bgr
        canvas = frame_bgr.copy()
        for idx, entry in enumerate(self._image_layout_drawn_circles):
            cx, cy = int(entry['center'][0]), int(entry['center'][1])
            r = int(entry['radius'])
            cv2.circle(canvas, (cx, cy), r, (0, 220, 80), 2)
            cv2.putText(
                canvas, f'#{idx + 1}', (cx + r + 2, cy),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 220, 80), 1,
            )
        if self._image_layout_model is not None and self._image_layout_model.transform is not None:
            try:
                projected_circles = project_all_circles(self._image_layout_model)
            except Exception:
                projected_circles = None
            if projected_circles is not None:
                for entry in projected_circles:
                    px_i, py_i = int(entry['center'][0]), int(entry['center'][1])
                    r = int(entry['radius'])
                    cv2.circle(canvas, (px_i, py_i), r, (0, 200, 255), 2)
        return canvas

    def _image_electrode_alignment_payload(self):
        layout = self._image_layout_model
        if layout is None or layout.transform is None:
            return None
        projected = project_all_circles(layout)
        return {
            'transform': np.asarray(layout.transform, dtype=float).tolist(),
            'layout_path': getattr(_config, 'VISION_ELECTRODE_LAYOUT_PATH', 'vision_calibration/electrode_layout.json'),
            'electrodes': [
                {
                    'layout_index': i,
                    'x_px': float(entry['center'][0]),
                    'y_px': float(entry['center'][1]),
                    'radius_px': float(entry['radius']),
                    'on_image': True,
                }
                for i, entry in enumerate(projected)
            ],
            'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }

    def _image_save_electrode_alignment(self, path=None):
        payload = self._image_electrode_alignment_payload()
        if payload is None:
            self._image_layout_alignment_status_var.set('Save failed: accept a fitted alignment first')
            return False
        if path is None:
            path = filedialog.asksaveasfilename(
                title='Save electrode alignment',
                defaultextension='.json',
                initialfile=os.path.basename(
                    getattr(_config, 'VISION_ELECTRODE_ALIGNMENT_PATH', 'electrode_alignment.json')
                ),
                filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            )
        if not path:
            return False
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._image_layout_alignment_status_var.set(f'Save failed: {exc}')
            return False
        self._image_layout_alignment_status_var.set(
            f"Alignment: saved {len(payload['electrodes'])} electrode(s) to {os.path.basename(path)}"
        )
        return True

    def _image_load_electrode_alignment(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title='Load electrode alignment',
                filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            )
        if not path:
            return False
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
            if self._image_layout_model is None:
                layout_path = payload.get('layout_path') or ''
                if not layout_path:
                    raise RuntimeError('no layout loaded and alignment has no layout_path')
                self._image_layout_model = LayoutModel.from_json(layout_path)
            self._image_layout_model.transform = np.asarray(payload['transform'], dtype=np.float32)
        except Exception as exc:
            self._image_layout_alignment_status_var.set(f'Load failed: {exc}')
            return False
        self._image_layout_alignment_status_var.set(
            f"Alignment: loaded {len(payload.get('electrodes', []))} electrode(s) from {os.path.basename(path)}"
        )
        return True

    def _image_update_target_status(self):
        if self._image_selected_target is None:
            self._image_target_status_var.set('Target: not locked')
            return
        target = self._image_selected_target
        message = (
            f"Target: locked R{int(target['row_index'])}C{int(target['col_index'])} "
            f"at ({target['x_px']:.1f}, {target['y_px']:.1f}) px"
        )
        if self._image_pixel_stage_affine_calibration is not None:
            try:
                debug_out = {}
                stage_xy = self._image_project_pixel_to_stage_xy(
                    float(target['x_px']),
                    float(target['y_px']),
                    target=target,
                    debug_out=debug_out,
                )
                message += f" -> Stage (~{float(stage_xy[0]):.3f}, {float(stage_xy[1]):.3f}) mm"
                if debug_out.get('message'):
                    message += f" | {debug_out['message']}"
                xy_bias_px = debug_out.get('xy_bias_px')
                if xy_bias_px is not None:
                    temp_c = debug_out.get('xy_bias_temperature_c')
                    temp_text = '?C' if temp_c is None else f'{temp_c:.0f}C'
                    message += f" | xy bias: ({xy_bias_px[0]:+.1f}, {xy_bias_px[1]:+.1f})px, {temp_text})"
                else:
                    message += " | xy bias: not calibrated"
            except Exception:
                pass
        self._image_target_status_var.set(message)

    def _image_sync_manual_target_from_selected(self):
        """
        Fill the "Move to:" X/Y/Z fields (shared with Manual Control, shown
        in the Camera & Stage section) from the just-locked target
        electrode's known position, as an aide for the user -- they can see
        where the tip is about to go before clicking Move Tip, which is now
        the only move path (drives to whatever is in these fields, and
        marks the target "moved to" for Search Z if the driven XY still
        matches it -- see _image_note_manual_move_target_match).
        Best-effort: X/Y only fill in if the pixel<->stage calibration is
        solved, Z only fills in if this target is a DXF-layout electrode
        with a known Z seed or Z-surface estimate AND "Lock Z" is
        unchecked (Lock Z defaults to checked, leaving Z exactly as-is on
        every target lock -- see self._image_lock_z_var) -- leaves
        whatever was there before otherwise, same as every other vision
        fallback in this file.
        """
        target = self._image_selected_target
        if target is None:
            return
        if self._image_pixel_stage_affine_calibration is not None:
            try:
                correction_debug = {}
                stage_x, stage_y = self._image_project_pixel_to_stage_xy(
                    float(target['x_px']),
                    float(target['y_px']),
                    target=target,
                    debug_out=correction_debug,
                )
                layout_index_for_debug = self._image_layout_index_for_target(target)
                if layout_index_for_debug is not None:
                    self._image_last_applied_correction[layout_index_for_debug] = correction_debug
                self._manual_target['x'].set(f'{stage_x:.3f}')
                self._manual_target['y'].set(f'{stage_y:.3f}')
            except Exception:
                pass
        if self._image_lock_z_var.get():
            return
        layout_index = self._image_layout_index_for_target(target)
        if layout_index is not None:
            xy_mm = self._image_layout_xy_mm_for_layout_index(layout_index)
            seed_z = self._image_electrode_z_store.get_seed_or_estimate(layout_index, xy_mm=xy_mm)
            if seed_z is not None:
                self._manual_target['z'].set(f'{float(seed_z):.3f}')

    def _image_refresh_z_seed_status(self):
        """
        Keep the Z-seed status line reflecting whether the CURRENTLY locked
        target is actually safe to seed from right now -- "Search Z"
        searches at the stage's current XY, so it only measures the locked
        electrode's real height if the stage is currently within tolerance
        of it (see _image_target_within_move_tolerance, checked fresh here
        rather than trusting a "did you click Move Tip" flag). Called
        whenever locking, moving, or clearing a target could change that
        answer, not just on a failed seed attempt, so the status is always
        current rather than only updating in response to a mistake.
        """
        if self._image_selected_target is None:
            self._image_z_seed_status_var.set('Z seed: lock a target and move to it first')
        elif not self._image_target_within_move_tolerance():
            self._image_z_seed_status_var.set(
                'Z seed: target locked -- move the stage within tolerance before seeding'
            )
        else:
            self._image_z_seed_status_var.set('Z seed: ready -- stage is at the locked target')

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
        self._image_update_target_status()
        self._image_refresh_z_seed_status()
        self._image_sync_manual_target_from_selected()
        return True

    def _image_frame_xy_from_event(self, event):
        """Map a live-view widget-space mouse event to frame-pixel
        coordinates, or None if there's no rendered frame yet or the event
        fell outside the rendered image (e.g. in the letterboxed margin)."""
        if self._image_render_shape is None or self._image_render_size is None:
            return None
        render_w, render_h = self._image_render_size
        offset_x, offset_y = self._image_render_offset
        local_x = event.x - offset_x
        local_y = event.y - offset_y
        if local_x < 0 or local_y < 0 or local_x >= render_w or local_y >= render_h:
            return None
        frame_h, frame_w = self._image_render_shape
        frame_x = float(local_x) * float(frame_w) / max(float(render_w), 1.0)
        frame_y = float(local_y) * float(frame_h) / max(float(render_h), 1.0)
        return frame_x, frame_y

    def _image_monitor_press(self, event):
        if self._image_probe_roi_select_mode:
            frame_xy = self._image_frame_xy_from_event(event)
            if frame_xy is None:
                return
            self._image_probe_roi_drag_start = frame_xy
            self._image_probe_roi_drag_current = frame_xy
            return
        self._image_monitor_click_select_target(event)

    def _image_monitor_drag(self, event):
        if not self._image_probe_roi_select_mode or self._image_probe_roi_drag_start is None:
            return
        frame_xy = self._image_frame_xy_from_event(event)
        if frame_xy is not None:
            self._image_probe_roi_drag_current = frame_xy

    def _image_monitor_release(self, event):
        if not self._image_probe_roi_select_mode or self._image_probe_roi_drag_start is None:
            return
        frame_xy = self._image_frame_xy_from_event(event) or self._image_probe_roi_drag_current
        if frame_xy is not None:
            x1, y1 = self._image_probe_roi_drag_start
            x2, y2 = frame_xy
            self._image_probe_roi = (
                int(round(min(x1, x2))), int(round(min(y1, y2))),
                int(round(max(x1, x2))), int(round(max(y1, y2))),
            )
            self._image_probe_status_var.set(
                f'Probe ROI set: ({self._image_probe_roi[0]}, {self._image_probe_roi[1]}) - '
                f'({self._image_probe_roi[2]}, {self._image_probe_roi[3]})'
            )
        self._image_probe_roi_drag_start = None
        self._image_probe_roi_drag_current = None

    def _image_toggle_probe_roi_select_mode(self):
        self._image_probe_roi_select_mode = not self._image_probe_roi_select_mode
        self._image_probe_roi_drag_start = None
        self._image_probe_roi_drag_current = None
        self._image_probe_status_var.set(
            'Probe: drag a rectangle on the live view around the probe/electrode area'
            if self._image_probe_roi_select_mode else 'Probe: ROI select mode off'
        )
        self._image_set_mode_button_active(self._image_probe_roi_button, self._image_probe_roi_select_mode)

    def _image_clear_probe_roi(self):
        self._image_probe_roi = None
        self._image_probe_roi_select_mode = False
        self._image_set_mode_button_active(self._image_probe_roi_button, False)
        self._image_probe_roi_drag_start = None
        self._image_probe_roi_drag_current = None
        # Otherwise a stale crosshair from a previous "Detect Probe Tip"
        # keeps being redrawn every frame (_image_draw_probe_tip_overlay is
        # gated purely on this candidate, independent of the ROI).
        self._image_probe_tip_candidate = None
        self._image_probe_status_var.set('Probe: ROI cleared (searching full frame)')

    def _image_draw_probe_roi_overlay(self, frame_bgr):
        rect = None
        if self._image_probe_roi_drag_start is not None and self._image_probe_roi_drag_current is not None:
            x1, y1 = self._image_probe_roi_drag_start
            x2, y2 = self._image_probe_roi_drag_current
            rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        elif self._image_probe_roi is not None:
            rect = self._image_probe_roi
        if rect is None or cv2 is None:
            return frame_bgr
        canvas = frame_bgr.copy()
        x1, y1, x2, y2 = (int(round(v)) for v in rect)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (80, 200, 255), 2)
        return canvas

    def _image_draw_axis_arrows_overlay(self, frame_bgr):
        """
        Compass-style arrows in the bottom-left corner showing which way
        positive stage X/Y point in this camera view -- derived from the
        pixel<->stage affine calibration (not a fixed on-screen direction,
        since that depends on how the camera happens to be mounted).
        Best-effort: no calibration yet -> no arrows, matching every other
        vision fallback in this file.
        """
        calib = self._image_pixel_stage_affine_calibration
        if calib is None or cv2 is None:
            return frame_bgr
        h, w = frame_bgr.shape[:2]
        origin_px = (55.0, h - 55.0)
        length_px = 45.0
        try:
            origin_stage = pixel_to_stage_xy(calib, origin_px[0], origin_px[1])
            # 1mm is only a probe delta to sample direction -- renormalized
            # to a fixed on-screen length below, so its exact size doesn't
            # matter.
            x_tip_stage = (origin_stage[0] + 1.0, origin_stage[1])
            y_tip_stage = (origin_stage[0], origin_stage[1] + 1.0)
            x_tip_px = stage_to_pixel_xy(calib, *x_tip_stage)
            y_tip_px = stage_to_pixel_xy(calib, *y_tip_stage)
        except Exception:
            return frame_bgr

        def _normalized_tip(tip_px):
            dx, dy = tip_px[0] - origin_px[0], tip_px[1] - origin_px[1]
            norm = (dx ** 2 + dy ** 2) ** 0.5
            if norm < 1e-9:
                return None
            scale = length_px / norm
            return (origin_px[0] + dx * scale, origin_px[1] + dy * scale)

        x_tip = _normalized_tip(x_tip_px)
        y_tip = _normalized_tip(y_tip_px)
        if x_tip is None and y_tip is None:
            return frame_bgr
        canvas = frame_bgr.copy()
        origin_i = (int(round(origin_px[0])), int(round(origin_px[1])))
        if x_tip is not None:
            tip_i = (int(round(x_tip[0])), int(round(x_tip[1])))
            cv2.arrowedLine(canvas, origin_i, tip_i, (0, 0, 255), 2, tipLength=0.3)
            cv2.putText(
                canvas, '+X', (tip_i[0] + 4, tip_i[1] + 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA,
            )
        if y_tip is not None:
            tip_i = (int(round(y_tip[0])), int(round(y_tip[1])))
            cv2.arrowedLine(canvas, origin_i, tip_i, (0, 220, 0), 2, tipLength=0.3)
            cv2.putText(
                canvas, '+Y', (tip_i[0] + 4, tip_i[1] - 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 0), 1, cv2.LINE_AA,
            )
        return canvas

    def _image_monitor_click_select_target(self, event):
        frame_xy = self._image_frame_xy_from_event(event)
        if frame_xy is None:
            if self._image_render_shape is None or self._image_render_size is None:
                self._image_target_status_var.set('Target lock failed: no rendered frame yet')
            return
        frame_x, frame_y = frame_xy
        if self._image_layout_draw_mode:
            # Use the raw click position directly -- no Hough re-detection.
            # This used to snap to a nearby detected circle, left over from
            # an earlier rubber-band (two-click) placement process; with a
            # single click placing the marker directly, snapping only risked
            # jumping to a neighbouring electrode's circle instead of the
            # one actually clicked. A fixed radius is used purely for the
            # overlay marker's cosmetic size.
            self._image_layout_drawn_circles.append(
                {'center': [float(frame_x), float(frame_y)], 'radius': _IMAGE_LAYOUT_DRAWN_MARKER_RADIUS_PX}
            )
            self._image_layout_alignment_status_var.set(
                f'Alignment: {len(self._image_layout_drawn_circles)} circle(s) drawn'
            )
            return
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
        cv2.circle(canvas, (x, y), max(r + 6, 10), (255, 255, 0), 3)
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
        if step_name.startswith('ca_pre_scout'):
            return 'OLE-COM Pre+Scout CA Current'
        if step_name.startswith('ca_post_hold'):
            return 'OLE-COM Post Hold Current'
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
        # Reached directly (not via self.after) from the Manual Control
        # tab's quick-EIS background-thread worker -- Canvas mutation from
        # a background thread is undefined behavior for Tcl (see
        # _run_on_main_thread's docstring), so marshal the whole body.
        self._run_on_main_thread(self._redraw_monitor_now)

    def _redraw_monitor_now(self):
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
        line = f"[{ts}] {msg}\n"
        self.log_queue.put(line)
        if self._log_file is not None:
            # _log is called from both the main thread and background
            # measurement/worker threads -- serialize file writes so
            # concurrent calls can't interleave mid-line.
            with self._log_file_lock:
                try:
                    self._log_file.write(line)
                    self._log_file.flush()
                except Exception:
                    pass

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

    def _condition_generator_settings_payload(self):
        """
        Snapshot of every Semi-auto/Full-auto tab field this GUI remembers
        across restarts -- the plain (StringVar-backed) parameter dicts,
        the temperature/gas/tip/CA enable toggles, tip-source mode, and
        omit-electrodes text. Deliberately excludes per-DXF-layout-
        dependent state (e.g. diameter-exclusion checkboxes), which
        wouldn't make sense to carry over to a different layout in a
        future session.
        """
        return {
            'semi_auto': {key: var.get() for key, var in self._semi_auto.items()},
            'semi_auto_use': {key: var.get() for key, var in self._semi_auto_use.items()},
            'semi_auto_tip_source': self._semi_auto_tip_source_var.get(),
            'semi_auto_image_monitor_omit_electrodes': self._semi_auto_image_monitor_omit_electrodes_var.get(),
            'full_auto': {key: var.get() for key, var in self._full_auto.items()},
            'full_auto_use': {key: var.get() for key, var in self._full_auto_use.items()},
            'full_auto_tip_source': self._full_auto_tip_source_var.get(),
            'full_auto_image_monitor_omit_electrodes': self._full_auto_image_monitor_omit_electrodes_var.get(),
            'run_gas_mode': self._run_gas_mode_var.get(),
            'run_contact_fail_policy': self._run_contact_fail_policy_var.get(),
            'run_retract_tip_on_done': self._run_retract_tip_on_done_var.get(),
            'run_retract_tip_mm': self._run_retract_tip_mm_var.get(),
            'run_ramp_down_on_done': self._run_ramp_down_on_done_var.get(),
            'run_ramp_down_end_temp_c': self._run_ramp_down_end_temp_c_var.get(),
            'run_ramp_down_end_ramp_rate': self._run_ramp_down_end_ramp_rate_var.get(),
        }

    def _save_condition_generator_settings(self):
        path = getattr(_config, 'GUI_LAST_SESSION_PATH', 'gui_last_session.json')
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(self._condition_generator_settings_payload(), fh, indent=2)
        except Exception:
            pass

    def _load_condition_generator_settings(self):
        path = getattr(_config, 'GUI_LAST_SESSION_PATH', 'gui_last_session.json')
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
        except Exception:
            return
        for dict_key, var_dict in (
            ('semi_auto', self._semi_auto),
            ('semi_auto_use', self._semi_auto_use),
            ('full_auto', self._full_auto),
            ('full_auto_use', self._full_auto_use),
        ):
            saved = payload.get(dict_key)
            if not isinstance(saved, dict):
                continue
            for key, value in saved.items():
                var = var_dict.get(key)
                if var is not None:
                    try:
                        var.set(value)
                    except Exception:
                        pass
        for var_attr, payload_key in (
            ('_semi_auto_tip_source_var', 'semi_auto_tip_source'),
            ('_semi_auto_image_monitor_omit_electrodes_var', 'semi_auto_image_monitor_omit_electrodes'),
            ('_full_auto_tip_source_var', 'full_auto_tip_source'),
            ('_full_auto_image_monitor_omit_electrodes_var', 'full_auto_image_monitor_omit_electrodes'),
            ('_run_gas_mode_var', 'run_gas_mode'),
            ('_run_contact_fail_policy_var', 'run_contact_fail_policy'),
            ('_run_retract_tip_on_done_var', 'run_retract_tip_on_done'),
            ('_run_retract_tip_mm_var', 'run_retract_tip_mm'),
            ('_run_ramp_down_on_done_var', 'run_ramp_down_on_done'),
            ('_run_ramp_down_end_temp_c_var', 'run_ramp_down_end_temp_c'),
            ('_run_ramp_down_end_ramp_rate_var', 'run_ramp_down_end_ramp_rate'),
        ):
            value = payload.get(payload_key)
            if value is not None:
                try:
                    getattr(self, var_attr).set(value)
                except Exception:
                    pass

    def on_close(self):
        if self.running:
            if not messagebox.askyesno("종료", "측정 중입니다. 강제 종료하시겠습니까?"):
                return
            self.stop_flag.set()
        self._save_condition_generator_settings()
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
            if self._log_file is not None:
                try:
                    self._log_file.close()
                except Exception:
                    pass
            os._exit(0)


if __name__ == '__main__':
    # Diagnostic instrumentation for the still-unresolved Win7 0xc0000005
    # crash: faulthandler installs a Windows unhandled-exception filter
    # that, on a fatal access violation, dumps a Python-level stack trace
    # of every thread to this file before the process dies -- unlike the
    # generic Event Viewer report (module + byte offset only), this shows
    # exactly which Python call was in flight on which thread (e.g. inside
    # a COM call, inside tkinter, inside numpy) at the moment of the
    # crash. The file object is kept referenced at module scope so it
    # can't be garbage-collected out from under the installed handler.
    os.makedirs('run_logs', exist_ok=True)
    _faulthandler_log_path = os.path.join(
        'run_logs', f'faulthandler_{time.strftime("%Y%m%d_%H%M%S")}.log'
    )
    _faulthandler_log_file = open(_faulthandler_log_path, 'w', encoding='utf-8')
    faulthandler.enable(file=_faulthandler_log_file, all_threads=True)

    app = MicroprobGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
