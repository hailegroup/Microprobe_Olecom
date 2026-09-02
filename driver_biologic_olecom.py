# -*- coding: utf-8 -*-
"""BioLogic controller adapter that uses EC-Lab OLE-COM only.

This module is the production BioLogic path for the Windows 7 runner. It is
intentionally separate from ``driver_biologic.py`` because easy-biologic and
EC-Lab OLE-COM should not be mixed in the same instrument session.
"""

from __future__ import annotations

import json
import os
import contextlib
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from config import (
    BIOLOGIC_IP,
    BIOLOGIC_OLECOM_BANDWIDTH,
    BIOLOGIC_OLECOM_CA_BANDWIDTH,
    BIOLOGIC_OLECOM_CA_I_RANGE,
    BIOLOGIC_OLECOM_CREATE_ECLAB_IF_MISSING,
    BIOLOGIC_OLECOM_DEVICE_INDEX,
    BIOLOGIC_OLECOM_DISCONNECT_DEVICE_ON_GUI_DISCONNECT,
    BIOLOGIC_OLECOM_MIN_FFT_DURATION_S,
    BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ,
    BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ,
    BIOLOGIC_OLECOM_PEIS_POINTS_PER_DECADE,
    BIOLOGIC_OLECOM_SCOUT_TAIL_REL_SHIFT_LIMIT,
    BIOLOGIC_OLECOM_SCOUT_TAIL_REL_SLOPE_LIMIT,
    BIOLOGIC_OLECOM_SCOUT_TAIL_WINDOW_S,
    BIOLOGIC_OLECOM_TRUST_TEST_CONNECTION,
)


PROJECT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = PROJECT_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    from run_olecom_pre_scout_post_hybrid import (
        OleComController,
        _parse_mpr_eis,
        _require_mps_fields,
        _wait_for_point_count_stable,
        _write_peis_mps,
    )
    from run_olecom_pre_scout_live_stop_hybrid import (
        _estimate_peis_points,
        run_once as _run_olecom_live_stop_once,
        _wait_peis_completion_or_stable,
    )
    _HAS_OLECOM = True
    _OLECOM_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - displayed by GUI diagnostics
    OleComController = None
    _HAS_OLECOM = False
    _OLECOM_IMPORT_ERROR = exc


def _ensure_com_initialized():
    """Initialize COM on the current thread when comtypes is available.

    EC-Lab OLE-COM is apartment/thread sensitive.  The GUI connects BioLogic
    from button callbacks but runs measurements from worker threads, so each
    thread must initialize COM and use its own attached controller.
    """
    try:
        import comtypes

        try:
            comtypes.CoInitialize()
        except Exception:
            pass
    except Exception:
        pass


class OleComSequenceResult:
    """Small result object compatible with the GUI post-processing hooks."""

    def __init__(self, summary, run_dir):
        self.summary = dict(summary or {})
        self.output_dir = str(run_dir)
        self.summary_path = str(Path(run_dir) / "summary.json")
        self.measurement_mode = "olecom_hybrid"
        self.pre_ca_path = self.summary.get("ca_fft_txt") or self.summary.get("ca_txt")
        self.post_ca_path = self.summary.get("ca_fft_txt") or self.summary.get("ca_txt")
        self.peis_path = self.summary.get("peis_txt")
        self.eis_data = self._load_txt(self.peis_path)
        self.ca_data = self._load_txt(self.post_ca_path)
        self.pre_ca_data = self.ca_data
        self.skip_legacy_analysis = True
        self.raw_recommended_lf_hz = self.summary.get("raw_recommended_lf_hz")
        self.applied_peis_lf_hz = self.summary.get("applied_peis_lf_hz")
        self.fit_quality_score = self.summary.get("fit_quality_score")

    @staticmethod
    def _load_txt(path):
        if not path:
            return np.empty((0, 3))
        try:
            data = np.loadtxt(path, skiprows=1)
            if data.ndim == 1:
                data = data.reshape(1, -1)
            return data
        except Exception:
            return np.empty((0, 3))

    def __iter__(self):
        yield self.eis_data
        yield self.ca_data


class BioLogicOleComController:
    """High-level GUI-facing BioLogic controller backed by EC-Lab OLE-COM."""

    backend_name = "olecom"

    def __init__(
        self,
        ip=None,
        port=None,
        channel=1,
        device=None,
        create_if_missing=None,
        trust_test_connection=None,
    ):
        if not _HAS_OLECOM:
            raise ImportError(
                "OLE-COM backend import failed. Install comtypes/galvani and "
                "make sure EC-Lab OLE-COM is registered. "
                f"Original error: {_OLECOM_IMPORT_ERROR}"
            )
        self.ip = ip or BIOLOGIC_IP
        self.port = port
        self.device = BIOLOGIC_OLECOM_DEVICE_INDEX if device is None else int(device)
        self.channel = int(channel)
        self.create_if_missing = (
            BIOLOGIC_OLECOM_CREATE_ECLAB_IF_MISSING
            if create_if_missing is None else bool(create_if_missing)
        )
        self.trust_test_connection = (
            BIOLOGIC_OLECOM_TRUST_TEST_CONNECTION
            if trust_test_connection is None else bool(trust_test_connection)
        )
        self.ctrl = None
        self._ctrl_thread_id = None
        self._active_stop_event = None
        self._last_peis_mpr_path = None

    def connect(self):
        _ensure_com_initialized()
        self.ctrl = OleComController(
            ip=self.ip,
            channel=self.channel,
            device=self.device,
            create_if_missing=self.create_if_missing,
            trust_test_connection=self.trust_test_connection,
        )
        self.ctrl.connect()
        if getattr(self.ctrl, "obj", None) is None:
            self.ctrl = None
            raise RuntimeError(
                "OLE-COM connected without a valid EC-Lab COM object. "
                "Re-open EC-Lab, then reconnect BioLogic."
            )
        self._ctrl_thread_id = threading.get_ident()
        print(f"[BioLogic OLE-COM] Connected to {self.ip} CH {self.channel}")

    def disconnect(self):
        _ensure_com_initialized()
        same_thread = self._ctrl_thread_id in (None, threading.get_ident())
        if (
            self.ctrl is not None
            and BIOLOGIC_OLECOM_DISCONNECT_DEVICE_ON_GUI_DISCONNECT
            and same_thread
        ):
            self.ctrl.disconnect()
        self.ctrl = None
        self._ctrl_thread_id = None
        print("[BioLogic OLE-COM] GUI handle released")

    def _drop_controller_if_cross_thread(self):
        if self.ctrl is None:
            return
        if self._ctrl_thread_id in (None, threading.get_ident()):
            return
        # Do not call DisconnectDevice from the wrong COM apartment.  Drop the
        # Python proxy only; the next call will attach to the already-open
        # EC-Lab session from the current worker thread.
        self.ctrl = None
        self._ctrl_thread_id = None

    def _ensure_connected(self):
        _ensure_com_initialized()
        self._drop_controller_if_cross_thread()
        if self.ctrl is not None:
            dead = getattr(self.ctrl, "obj", None) is None
            if not dead and hasattr(self.ctrl, "_connected"):
                try:
                    dead = not bool(self.ctrl._connected())
                except Exception:
                    dead = True
            if dead:
                # EC-Lab can be manually removed/re-added outside Python. Drop
                # the stale proxy so the next operation attaches to the live
                # EC-Lab session instead of failing at LoadSettings.
                self.ctrl = None
                self._ctrl_thread_id = None
        if self.ctrl is None:
            self.connect()
        return self.ctrl

    def _ensure_channel(self, channel=None):
        _ensure_com_initialized()
        self._drop_controller_if_cross_thread()
        if channel is None:
            return
        channel = int(channel)
        if channel == self.channel:
            return
        self.channel = channel
        if self.ctrl is not None:
            self.ctrl.user_channel = channel
            self.ctrl.com_channel = channel - 1
            if getattr(self.ctrl, "obj", None) is None:
                self.ctrl = None
                self._ctrl_thread_id = None
                self.connect()
            self.ctrl.obj.SelectChannel(self.ctrl.device, self.ctrl.com_channel)

    @staticmethod
    def discover_devices(connection="eth"):
        # OLE-COM discovery is EC-Lab-session based, not EC-Lib socket based.
        return []

    def list_channels(self, max_channels=4):
        # OLE-COM does not expose the same convenient plugged-channel list as
        # easy-biologic here.  The Win7 SP-200 runner is single-channel in
        # practice, so returning only the selected channel avoids a misleading
        # "choose from 1,2,3,4" prompt during GUI connect.
        ch = int(self.channel or 1)
        return [
            {
                "channel": ch,
                "index": ch - 1,
                "plugged": True,
                "label": f"CH {ch} (OLE-COM)",
                "info": {"backend": "olecom"},
            }
        ]

    def get_ocv(self, channel=1):
        self._ensure_channel(channel)
        return self._ensure_connected().get_ocv()

    def stop_measurement(self, channel=1):
        self._ensure_channel(channel)
        ctrl = self._ensure_connected()
        return ctrl.stop()

    def _point_count_from_total(self, f_high, f_low, n_pts):
        try:
            decades = abs(np.log10(float(f_high) / float(f_low)))
            if decades <= 0 or not np.isfinite(decades):
                return max(6, min(24, int(n_pts)))
            return max(6, min(24, int(round(float(n_pts) / decades))))
        except Exception:
            return 12

    def run_peis(
        self,
        v_dc,
        f_high,
        f_low,
        n_pts,
        amplitude_mv=10.0,
        channel=1,
        save_dir=None,
        label="olecom_peis",
        stop_event=None,
        on_segment=None,
        bandwidth=None,
        n_average=1,
    ):
        self._ensure_channel(channel)
        ctrl = self._ensure_connected()
        out_dir = Path(save_dir or PROJECT_DIR / "results" / ("olecom_quick_eis_" + time.strftime("%Y%m%d_%H%M%S")))
        out_dir.mkdir(parents=True, exist_ok=True)
        points_per_decade = self._point_count_from_total(f_high, f_low, n_pts)
        mps = out_dir / "olecom_quick_peis.mps"
        base = out_dir / "olecom_quick_peis"
        _write_peis_mps(
            mps,
            bias_v=float(v_dc),
            f_high_hz=float(f_high),
            f_low_hz=float(f_low),
            points_per_decade=points_per_decade,
            amplitude_mv=float(amplitude_mv),
            bandwidth=int(bandwidth or BIOLOGIC_OLECOM_BANDWIDTH),
            n_average=max(1, int(n_average)),
        )
        _require_mps_fields(
            mps,
            [
                ("PEIS bias", f"E (V)               {float(v_dc):.4f}"),
                ("PEIS I range", "I Range             Auto"),
                ("PEIS bandwidth", f"Bandwidth           {int(bandwidth or BIOLOGIC_OLECOM_BANDWIDTH)}"),
                ("PEIS N average", f"Na                  {max(1, int(n_average))}"),
            ],
        )
        run = ctrl.load_and_run(mps, base)
        expected = _estimate_peis_points(float(f_high), float(f_low), points_per_decade)
        _wait_peis_completion_or_stable(
            ctrl,
            run.mpr_path,
            timeout_s=1800.0,
            startup_grace_s=30.0,
            expected_points=expected,
            target_low_hz=float(f_low),
            stable_s=5.0,
            poll_s=0.5,
        )
        _wait_for_point_count_stable(
            ctrl,
            run.mpr_path,
            stable_s=5.0,
            timeout_s=180.0,
            poll_s=0.5,
            label="quick PEIS flush",
            verify_row_count_fn=lambda p: len(_parse_mpr_eis(p)),
        )
        peis = _parse_mpr_eis(run.mpr_path)
        # No file writing here -- every caller (measurement_sequence.py,
        # assorted tools/ scripts) already does its own explicit save from
        # the returned array under its own filename convention. Just record
        # where the raw EC-Lab .mpr for this call landed so a caller that
        # wants it (e.g. measurement_sequence.py, to preserve it alongside
        # its own .txt) can copy it under a matching name -- run.mpr_path
        # lives at a fixed name derived from `base` above and gets
        # overwritten by the very next PEIS call in this same save_dir, so
        # it must be copied out promptly by whoever wants to keep it.
        self._last_peis_mpr_path = str(run.mpr_path)
        if on_segment:
            on_segment(peis, None)
        return peis

    def run_hybrid_live_stop(
        self,
        *,
        v_dc,
        dv,
        pre_s,
        scout_min_s,
        scout_max_s,
        post_s,
        dt,
        channel=1,
        peis_high=100.0,
        peis_deep_limit=None,
        peis_overlap_limit=None,
        peis_npts=60,
        bandwidth=None,
        peis_n_average=1,
        ca_bandwidth=None,
        ca_i_range=None,
        save_dir=None,
        label="olecom_hybrid",
        stop_event=None,
        defer_postprocess=False,
        monitor_callback=None,
    ):
        def _emit(event, **payload):
            if monitor_callback:
                monitor_callback(event=event, label=label, **payload)

        self._ensure_channel(channel)
        out_dir = Path(save_dir or PROJECT_DIR / "results" / ("olecom_hybrid_" + time.strftime("%Y%m%d_%H%M%S")))
        out_dir.mkdir(parents=True, exist_ok=True)
        deep = float(peis_deep_limit if peis_deep_limit is not None else BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ)
        overlap = float(peis_overlap_limit if peis_overlap_limit is not None else BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ)
        # The recent validated OLE-COM protocol uses EC-Lab's PEIS
        # points-per-decade setting directly (Nd=10, Na=1).  Do not reinterpret
        # the GUI "PEIS n pts" value as total points for the hybrid path, because
        # that silently changes the PEIS density versus the validated tests.
        ppd = int(BIOLOGIC_OLECOM_PEIS_POINTS_PER_DECADE)
        stdout_path = out_dir / "gui_olecom_stdout.log"
        stderr_path = out_dir / "gui_olecom_stderr.log"
        args = SimpleNamespace(
            ip=str(self.ip),
            channel=int(channel),
            bias=float(v_dc),
            dv=float(dv),
            pre_s=float(pre_s),
            scout_min_s=float(scout_min_s),
            scout_max_s=float(scout_max_s),
            post_s=float(post_s),
            dt=float(dt),
            bandwidth=int(bandwidth or BIOLOGIC_OLECOM_BANDWIDTH),
            peis_n_average=max(1, int(peis_n_average or 1)),
            ca_bandwidth=int(ca_bandwidth or BIOLOGIC_OLECOM_CA_BANDWIDTH),
            ca_i_range=str(ca_i_range or BIOLOGIC_OLECOM_CA_I_RANGE),
            poll_s=0.5,
            live_plot_interval_s=0.0,
            live_status_interval_s=2.0,
            disable_live_ca_png=True,
            buffer_stable_s=5.0,
            buffer_timeout_s=180.0,
            ca_startup_grace_s=15.0,
            peis_high=float(peis_high),
            peis_deep_limit=deep,
            peis_overlap_limit=overlap,
            peis_points_per_decade=int(ppd),
            peis_timeout_s=1800.0,
            peis_startup_grace_s=30.0,
            fft_pre_tail_s=10.0,
            fft_eval_interval_s=5.0,
            min_fft_duration_s=float(BIOLOGIC_OLECOM_MIN_FFT_DURATION_S),
            scout_tail_window_s=float(BIOLOGIC_OLECOM_SCOUT_TAIL_WINDOW_S),
            scout_tail_rel_shift_limit=float(BIOLOGIC_OLECOM_SCOUT_TAIL_REL_SHIFT_LIMIT),
            scout_tail_rel_slope_limit=float(BIOLOGIC_OLECOM_SCOUT_TAIL_REL_SLOPE_LIMIT),
            defer_postprocess=bool(defer_postprocess),
            allow_create_eclab=False,
            trust_test_connection=bool(self.trust_test_connection),
            auto_clean_eclab=False,
            auto_clean_visible_eclab=False,
            disconnect_on_exit=False,
            output_dir=str(out_dir),
        )
        with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
            try:
                ctrl = self._ensure_connected()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    summary = _run_olecom_live_stop_once(args, ctrl=ctrl, monitor_callback=_emit)
            except Exception as exc:
                tail = ""
                try:
                    tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                except Exception:
                    pass
                raise RuntimeError(f"OLE-COM hybrid runner failed: {exc}\n{tail}")
        summary["label"] = label
        return OleComSequenceResult(summary, out_dir)


BioLogicController = BioLogicOleComController
