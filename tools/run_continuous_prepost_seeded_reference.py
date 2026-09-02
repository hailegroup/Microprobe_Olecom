from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ANALYSIS_DIR = PROJECT_DIR / "Analysis_Convert_CP_to_EIS"
CONVERT_DIR = PROJECT_DIR.parent / "Convert_CP_to_EIS 1"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))
if str(CONVERT_DIR) not in sys.path:
    sys.path.insert(0, str(CONVERT_DIR))


def _install_matplotlib_compat() -> None:
    """Backfill newer pyplot APIs/kwargs for older Win7 matplotlib installs."""
    if not getattr(plt, "_microprobe_layout_compat_installed", False):
        original_figure = plt.figure
        original_subplots = plt.subplots

        def _normalize_layout_kwargs(kwargs):
            kwargs = dict(kwargs)
            layout = kwargs.pop("layout", None)
            if layout == "constrained" and "constrained_layout" not in kwargs:
                kwargs["constrained_layout"] = True
            return kwargs

        def _figure(*args, **kwargs):
            kwargs = _normalize_layout_kwargs(kwargs)
            try:
                return original_figure(*args, **kwargs)
            except TypeError as exc:
                if "constrained_layout" in kwargs and "unexpected keyword argument" in str(exc):
                    kwargs.pop("constrained_layout", None)
                    return original_figure(*args, **kwargs)
                raise

        def _subplots(*args, **kwargs):
            kwargs = _normalize_layout_kwargs(kwargs)
            try:
                return original_subplots(*args, **kwargs)
            except TypeError as exc:
                if "constrained_layout" in kwargs and "unexpected keyword argument" in str(exc):
                    kwargs.pop("constrained_layout", None)
                    return original_subplots(*args, **kwargs)
                raise

        plt.figure = _figure
        plt.subplots = _subplots
        plt._microprobe_layout_compat_installed = True

    # Backfill pyplot.subplot_mosaic for older Win7 matplotlib installs.
    if hasattr(plt, "subplot_mosaic"):
        return

    def _subplot_mosaic(mosaic, *, figsize=None, layout=None, **kwargs):
        labels = []
        for row in mosaic:
            for label in row:
                if label not in labels and label not in (None, "."):
                    labels.append(label)

        nrows = len(mosaic)
        ncols = max(len(row) for row in mosaic)
        from matplotlib.gridspec import GridSpec

        fig = plt.figure(figsize=figsize, layout=layout)
        try:
            grid = GridSpec(nrows, ncols, figure=fig)
        except TypeError:
            grid = GridSpec(nrows, ncols)
        axes = {}
        for label in labels:
            cells = [
                (r, c)
                for r, row in enumerate(mosaic)
                for c, value in enumerate(row)
                if value == label
            ]
            rows = [cell[0] for cell in cells]
            cols = [cell[1] for cell in cells]
            axes[label] = fig.add_subplot(
                grid[min(rows):max(rows) + 1, min(cols):max(cols) + 1],
                **kwargs,
            )
        return fig, axes

    plt.subplot_mosaic = _subplot_mosaic


_install_matplotlib_compat()

from analysis_adapter import analyze_measurement_files_with_visuals
from driver_biologic import BioLogicController
from stabilization_policy import StabilizationSettings, analyze_pre_peis_stability

from Analysis_Convert_CP_to_EIS import EIS_Fitting as EF
import Load_CP_Data as LD
import compare_full_vs_optimized_fit as OPT
from cp_first_policy import build_fft_ready_ca_trace, recommend_peis_lf_from_cp_txt


CASE7_CA_PATH = CONVERT_DIR / "Input data" / "20260423 LSC as deposited with au paste re_measure" / "[7] 400C rapid_CA first and PEIS 100mV_01_CA_C02.mpr"
CASE7_FULL_ARC_PATH = CONVERT_DIR / "result" / "20260423_re_measure_hybrid_candidates" / "case7_400C_implemented_full_arc.png"


def _timestamp_dir(prefix: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"{prefix}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _save_txt(path: Path, data, header: str) -> None:
    arr = np.asarray(data, dtype=float)
    np.savetxt(path, arr, header="\t".join(str(header).split()), comments="", delimiter="\t")


def _resolve_bandwidth(name: str | None):
    if not name:
        return None
    try:
        from easy_biologic.lib import ec_lib as ecl
    except Exception:
        return None
    bandwidth_enum = getattr(ecl, "Bandwidth", None)
    if bandwidth_enum is None:
        return None
    key = str(name).strip().upper()
    if not key:
        return None
    if not key.startswith("BW"):
        key = f"BW{key}"
    return getattr(bandwidth_enum, key, None)


def _resolve_current_range(name: str | None):
    if not name:
        return None
    try:
        from easy_biologic.lib import ec_lib as ecl
    except Exception:
        return None
    current_range_enum = getattr(ecl, "IRange", None)
    if current_range_enum is None:
        return None
    key = str(name).strip()
    if not key:
        return None
    for candidate in (key, key.upper(), key.lower()):
        value = getattr(current_range_enum, candidate, None)
        if value is not None:
            return value
    aliases = {
        "100PA": "p100",
        "1NA": "n1",
        "10NA": "n10",
        "100NA": "n100",
        "1UA": "u1",
        "10UA": "u10",
        "100UA": "u100",
        "1MA": "m1",
        "10MA": "m10",
        "100MA": "m100",
        "1A": "a1",
        "AUTO": "AUTO",
        "KEEP": "KEEP",
    }
    return getattr(current_range_enum, aliases.get(key.upper(), ""), None)


def _write_stage_marker(out_dir: Path, stage: str, **payload) -> None:
    marker = {
        "stage": stage,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if payload:
        marker.update(_json_safe(payload))
    (out_dir / "stage_marker.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")


def _json_safe(value):
    if isinstance(value, np.ndarray):
        if np.iscomplexobj(value):
            return [[float(v.real), float(v.imag)] for v in value.tolist()]
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _average_trace_time_bins(data: np.ndarray, *, bin_s: float = 1.0) -> np.ndarray:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or len(arr) == 0:
        return arr.copy()
    if arr.shape[1] < 3 or not np.isfinite(bin_s) or bin_s <= 0:
        return arr.copy()
    finite = np.isfinite(arr[:, 0]) & np.isfinite(arr[:, 1]) & np.isfinite(arr[:, 2])
    arr = arr[finite]
    if len(arr) == 0:
        return arr.copy()
    rel_t = np.asarray(arr[:, 0], dtype=float)
    bin_idx = np.floor(rel_t / float(bin_s)).astype(int)
    out = []
    for idx in np.unique(bin_idx):
        mask = bin_idx == idx
        chunk = arr[mask]
        out.append([
            float(np.mean(chunk[:, 0])),
            float(np.mean(chunk[:, 1])),
            float(np.mean(chunk[:, 2])),
        ])
    return np.asarray(out, dtype=float)


def _tail_trend_metrics(
    time_s: np.ndarray,
    current_a: np.ndarray,
    *,
    window_s: float = 40.0,
) -> dict:
    t = np.asarray(time_s, dtype=float)
    i = np.asarray(current_a, dtype=float)
    finite = np.isfinite(t) & np.isfinite(i)
    t = t[finite]
    i = i[finite]
    if len(t) < 6:
        return {
            "trend_ok": False,
            "tail_window_s": float(window_s),
            "tail_half_slope_a_per_s": np.nan,
            "tail_half_rel_slope_per_s": np.nan,
            "tail_mean_shift_a": np.nan,
            "tail_mean_shift_rel": np.nan,
        }

    cutoff = float(t[-1]) - float(window_s)
    mask = t >= cutoff
    if np.sum(mask) < 6:
        mask = np.arange(len(t)) >= max(0, len(t) - 6)
    tt = t[mask]
    ii = i[mask]
    mid = len(tt) // 2
    if mid < 2 or (len(tt) - mid) < 2:
        return {
            "trend_ok": False,
            "tail_window_s": float(window_s),
            "tail_half_slope_a_per_s": np.nan,
            "tail_half_rel_slope_per_s": np.nan,
            "tail_mean_shift_a": np.nan,
            "tail_mean_shift_rel": np.nan,
        }

    first = ii[:mid]
    second = ii[mid:]
    dt = max(float(np.mean(tt[mid:]) - np.mean(tt[:mid])), 1e-9)
    mean_shift = float(abs(np.mean(second) - np.mean(first)))
    mean_abs = float(max(np.mean(np.abs(ii)), 1e-12))
    total_delta = float(abs(ii[0] - ii[-1]))
    total_span = float(np.max(ii) - np.min(ii))
    scale = float(max(mean_abs, total_delta, total_span, 1e-12))
    slope = float(mean_shift / dt)
    rel_slope = float(slope / scale)
    rel_shift = float(mean_shift / scale)
    trend_ok = bool(rel_slope <= 0.01 and rel_shift <= 0.10)
    return {
        "trend_ok": trend_ok,
        "tail_window_s": float(window_s),
        "tail_scale_a": scale,
        "tail_half_slope_a_per_s": slope,
        "tail_half_rel_slope_per_s": rel_slope,
        "tail_mean_shift_a": mean_shift,
        "tail_mean_shift_rel": rel_shift,
    }


def _run_continuous_pre_hold(
    bl: BioLogicController,
    *,
    v_dc: float,
    min_duration_s: float,
    max_duration_s: float,
    dt_record_s: float,
    read_interval_s: float,
    channel: int,
    bandwidth=None,
    current_range=None,
    post_stable_buffer_s: float = 10.0,
) -> dict:
    stop_event = threading.Event()
    latest_assessment = {"value": None}
    stable_detected = {"hold_s": None}
    stable_confirm_polls = {"count": 0}
    max_assessed_hold_s = {"value": -1.0}
    # Polling cadence is far slower than the CA recording dt. Use the live-poll
    # interval to decide how many points a "window" needs, otherwise the
    # stabilization logic effectively requires impossible point counts.
    min_points_live = max(8, int(round(5.0 / max(read_interval_s, 1e-6))))
    live_window_s = max(30.0, min_duration_s / 2.0)
    settings = StabilizationSettings(
        min_hold_s=min_duration_s,
        max_hold_s=max_duration_s,
        extend_step_s=5.0,
        live_window_s=live_window_s,
        min_points_in_window=min_points_live,
        required_stable_windows=2,
        stable_slope_a_per_s=1.0e-12,
        stable_std_a=1.0e-12,
        stable_range_a=1.0e-12,
        stable_rel_slope_per_s=0.01,
        stable_rel_std=0.12,
        stable_rel_range=0.45,
    )

    def _on_poll(combined: np.ndarray) -> None:
        if len(combined) < 3:
            return
        eval_combined = _average_trace_time_bins(combined, bin_s=1.0)
        if len(eval_combined) < 3:
            return
        hold_now = float(eval_combined[-1, 0] - eval_combined[0, 0])
        # Ignore bogus final/live-value resets that jump the elapsed time
        # backwards to ~0 s after the instrument halts or disconnects.
        if hold_now + 1e-9 < max_assessed_hold_s["value"]:
            return
        max_assessed_hold_s["value"] = hold_now
        assessment = analyze_pre_peis_stability(
            eval_combined[:, 0],
            eval_combined[:, 2],
            settings=settings,
            peis_already_started=False,
        )
        trend = _tail_trend_metrics(
            eval_combined[:, 0],
            eval_combined[:, 2],
            window_s=max(40.0, 2.0 * live_window_s),
        )
        if hold_now >= min_duration_s and assessment.should_start_peis and bool(trend.get("trend_ok", False)):
            stable_confirm_polls["count"] += 1
            if stable_detected["hold_s"] is None and stable_confirm_polls["count"] >= 5:
                stable_detected["hold_s"] = hold_now
        elif stable_detected["hold_s"] is None:
            stable_confirm_polls["count"] = 0
        # Once we have seen a stable pre-tail once, keep that checkpoint and only
        # wait for the post-stability buffer. Do not clear it on small regressions.
        if stable_detected["hold_s"] is not None:
            if hold_now >= float(stable_detected["hold_s"]) + float(post_stable_buffer_s):
                stop_event.set()

        assessment_dict = assessment.to_dict()
        assessment_dict["post_stable_buffer_s"] = float(post_stable_buffer_s)
        assessment_dict["stable_detected_hold_s"] = (
            None if stable_detected["hold_s"] is None else float(stable_detected["hold_s"])
        )
        assessment_dict["stable_confirm_polls"] = int(stable_confirm_polls["count"])
        assessment_dict["live_window_s"] = float(live_window_s)
        assessment_dict["poll_bin_s"] = 1.0
        assessment_dict["tail_trend"] = _json_safe(trend)
        latest_assessment["value"] = assessment_dict

    started = time.time()
    data, polled = _run_ca_hold_with_live_poll(
        bl,
        v_dc=v_dc,
        duration=max_duration_s,
        dt_record_s=dt_record_s,
        read_interval_s=read_interval_s,
        channel=channel,
        bandwidth=bandwidth,
        current_range=current_range,
        stop_event=stop_event,
        on_poll=_on_poll,
    )
    if latest_assessment["value"] is None and len(polled) >= 3:
        _on_poll(polled)
    return {
        "data": np.asarray(data, dtype=float),
        "polled_data": np.asarray(polled, dtype=float),
        "runtime_s": float(time.time() - started),
        "stopped_by_stability": bool(stop_event.is_set()),
        "assessment": latest_assessment["value"],
        "post_stable_buffer_s": float(post_stable_buffer_s),
        "stable_detected_hold_s": (
            None if stable_detected["hold_s"] is None else float(stable_detected["hold_s"])
        ),
    }


def _run_continuous_dv_scout(
    bl: BioLogicController,
    *,
    v_dc: float,
    dv_v: float,
    min_duration_s: float,
    max_duration_s: float,
    dt_record_s: float,
    read_interval_s: float,
    channel: int,
    bandwidth=None,
    current_range=None,
) -> dict:
    stop_event = threading.Event()
    latest_assessment = {"value": None}
    max_assessed_hold_s = {"value": -1.0}
    saturation_confirm_polls = {"count": 0}

    def _on_poll(combined: np.ndarray) -> None:
        if len(combined) < 3:
            return
        eval_combined = _average_trace_time_bins(combined, bin_s=1.0)
        if len(eval_combined) < 3:
            return
        hold_now = float(eval_combined[-1, 0] - eval_combined[0, 0])
        if hold_now + 1e-9 < max_assessed_hold_s["value"]:
            return
        max_assessed_hold_s["value"] = hold_now
        assessment = OPT.assess_cp_saturation(eval_combined[:, 0], eval_combined[:, 2])
        latest_assessment["value"] = assessment
        if bool(assessment.get("saturation_reached", False)):
            saturation_confirm_polls["count"] += 1
        else:
            saturation_confirm_polls["count"] = 0

        recommended_duration = assessment.get("recommended_duration_s", None)
        if recommended_duration is None or not np.isfinite(float(recommended_duration)):
            target_hold_s = float(min_duration_s)
        else:
            target_hold_s = max(float(min_duration_s), float(recommended_duration))

        trend = _tail_trend_metrics(
            eval_combined[:, 0],
            eval_combined[:, 2],
            window_s=max(30.0, float(min_duration_s)),
        )
        assessment["live_target_hold_s"] = float(target_hold_s)
        assessment["saturation_confirm_polls"] = int(saturation_confirm_polls["count"])
        assessment["poll_bin_s"] = 1.0
        assessment["tail_trend"] = _json_safe(trend)

        if (
            hold_now >= target_hold_s
            and saturation_confirm_polls["count"] >= 5
            and bool(assessment.get("saturation_reached", False))
            and bool(trend.get("trend_ok", False))
        ):
            stop_event.set()

    started = time.time()
    data, polled = _run_ca_hold_with_live_poll(
        bl,
        v_dc=v_dc + dv_v,
        duration=max_duration_s,
        dt_record_s=dt_record_s,
        read_interval_s=read_interval_s,
        channel=channel,
        bandwidth=bandwidth,
        current_range=current_range,
        stop_event=stop_event,
        on_poll=_on_poll,
    )
    if latest_assessment["value"] is None and len(polled) >= 3:
        _on_poll(polled)
    return {
        "data": np.asarray(data, dtype=float),
        "polled_data": np.asarray(polled, dtype=float),
        "runtime_s": float(time.time() - started),
        "stopped_by_cp_saturation": bool(stop_event.is_set()),
        "assessment": latest_assessment["value"],
    }


def _run_ca_hold_with_live_poll(
    bl: BioLogicController,
    *,
    v_dc: float,
    duration: float,
    dt_record_s: float,
    read_interval_s: float,
    channel: int,
    bandwidth=None,
    current_range=None,
    stop_event: threading.Event,
    on_poll=None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Run CA in a worker thread, but make stop decisions from scalar live polling.

    In practice, easy-biologic segment callbacks have been unreliable for CA.
    Polling `get_live_values()` gives us a direct current/time stream that we
    can use for stop decisions while the technique is still running.
    """
    result: dict = {}
    error: dict = {}
    polled_rows: list[list[float]] = []
    stop_requested = False
    base_elapsed_s: float | None = None
    last_rel_elapsed_s: float | None = None
    started = time.time()

    def _worker() -> None:
        try:
            result["data"] = bl.run_ca_hold(
                v_dc=v_dc,
                duration=duration,
                dt_record=dt_record_s,
                channel=channel,
                read_interval=read_interval_s,
                bandwidth=bandwidth,
                current_range=current_range,
                stop_event=stop_event,
            )
        except Exception as exc:  # pragma: no cover - hardware/runtime path
            error["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    poll_interval_s = max(0.10, min(float(read_interval_s), 1.0))

    while thread.is_alive():
        try:
            live = bl.get_live_values(channel=channel)
            elapsed_s = float(live.get("elapsed_s", np.nan))
            current_a = float(live.get("current_a", np.nan))
            ewe_v = float(live.get("ewe_v", np.nan))
            if np.isfinite(elapsed_s):
                if base_elapsed_s is None:
                    base_elapsed_s = elapsed_s
                rel_elapsed_s = max(0.0, elapsed_s - base_elapsed_s)
            else:
                rel_elapsed_s = max(0.0, time.time() - started)

            # Ignore duplicate or backwards-reset timestamps from scalar polling.
            if last_rel_elapsed_s is not None:
                if rel_elapsed_s < last_rel_elapsed_s - 0.5:
                    thread.join(timeout=poll_interval_s)
                    continue
                if rel_elapsed_s <= last_rel_elapsed_s + 1e-6:
                    thread.join(timeout=poll_interval_s)
                    continue
            last_rel_elapsed_s = rel_elapsed_s

            if np.isfinite(current_a):
                polled_rows.append([rel_elapsed_s, ewe_v, current_a])
                if on_poll is not None and len(polled_rows) >= 3:
                    on_poll(np.asarray(polled_rows, dtype=float))
        except Exception:
            pass

        if stop_event.is_set() and not stop_requested:
            try:
                bl.stop_measurement(channel=channel)
            except Exception:
                pass
            stop_requested = True

        thread.join(timeout=poll_interval_s)

    thread.join()
    if "exc" in error:
        raise error["exc"]

    data = np.asarray(result.get("data", np.empty((0, 3))), dtype=float)
    if data.ndim == 1 and data.size:
        data = data.reshape(1, -1)
    polled = np.asarray(polled_rows, dtype=float)
    if polled.ndim == 1 and polled.size:
        polled = polled.reshape(1, -1)
    return data, polled


def _wait_for_biologic_idle(
    bl: BioLogicController,
    *,
    channel: int,
    timeout_s: float = 20.0,
    settle_s: float = 1.0,
    poll_interval_s: float = 0.2,
) -> dict:
    """
    Wait until BioLogic scalar polling reports idle state for a short settle window.

    After halting a CA technique, immediately launching PEIS can intermittently hang
    on some bias conditions. This helper waits for the controller to look idle
    before the next technique starts.
    """
    started = time.time()
    idle_started = None
    last_live = {}
    while (time.time() - started) < float(timeout_s):
        try:
            live = bl.get_live_values(channel=channel)
            last_live = dict(live)
            state_value = live.get("state", None)
            try:
                state_int = int(state_value) if state_value is not None else None
            except Exception:
                state_int = None
            if state_int == 0:
                if idle_started is None:
                    idle_started = time.time()
                elif (time.time() - idle_started) >= float(settle_s):
                    return {
                        "idle": True,
                        "waited_s": float(time.time() - started),
                        "settle_s": float(settle_s),
                        "last_live": last_live,
                    }
            else:
                idle_started = None
        except Exception:
            idle_started = None
        time.sleep(max(0.05, float(poll_interval_s)))
    return {
        "idle": False,
        "waited_s": float(time.time() - started),
        "settle_s": float(settle_s),
        "last_live": last_live,
    }


def _force_biologic_idle_for_handoff(
    bl: BioLogicController,
    *,
    channel: int,
    initial_timeout_s: float = 5.0,
    reconnect_timeout_s: float = 20.0,
) -> dict:
    """
    Try harder to leave the controller in an idle state before PEIS.

    Some biased/slow-cell runs complete the CA data capture but still leave the
    controller in an active technique state. A single stop+wait is sometimes
    not enough, so we escalate through repeated stop requests and, if needed,
    disconnect/reconnect before attempting PEIS.
    """
    attempts: list[dict] = []

    def _record(label: str, info: dict) -> None:
        attempts.append({"label": label, **_json_safe(info)})

    try:
        bl.stop_measurement(channel=channel)
        _record("stop_request_1", {"ok": True})
    except Exception as exc:
        _record("stop_request_1", {"ok": False, "error": str(exc)})
    time.sleep(0.5)
    idle_1 = _wait_for_biologic_idle(
        bl,
        channel=channel,
        timeout_s=initial_timeout_s,
        settle_s=0.5,
        poll_interval_s=0.2,
    )
    _record("idle_wait_1", idle_1)
    if bool(idle_1.get("idle", False)):
        return {"idle": True, "method": "stop_wait", "attempts": attempts}

    try:
        bl.stop_measurement(channel=channel)
        _record("stop_request_2", {"ok": True})
    except Exception as exc:
        _record("stop_request_2", {"ok": False, "error": str(exc)})
    time.sleep(1.0)
    idle_2 = _wait_for_biologic_idle(
        bl,
        channel=channel,
        timeout_s=initial_timeout_s,
        settle_s=0.5,
        poll_interval_s=0.2,
    )
    _record("idle_wait_2", idle_2)
    if bool(idle_2.get("idle", False)):
        return {"idle": True, "method": "stop_wait_repeat", "attempts": attempts}

    reconnect_ok = False
    try:
        bl.disconnect()
        _record("disconnect", {"ok": True})
    except Exception as exc:
        _record("disconnect", {"ok": False, "error": str(exc)})
    time.sleep(1.0)
    try:
        bl.connect()
        reconnect_ok = True
        _record("reconnect", {"ok": True})
    except Exception as exc:
        _record("reconnect", {"ok": False, "error": str(exc)})

    if reconnect_ok:
        try:
            bl.stop_measurement(channel=channel)
            _record("stop_request_3", {"ok": True})
        except Exception as exc:
            _record("stop_request_3", {"ok": False, "error": str(exc)})
        time.sleep(0.5)
        idle_3 = _wait_for_biologic_idle(
            bl,
            channel=channel,
            timeout_s=reconnect_timeout_s,
            settle_s=0.5,
            poll_interval_s=0.2,
        )
        _record("idle_wait_3", idle_3)
        if bool(idle_3.get("idle", False)):
            return {"idle": True, "method": "reconnect_stop_wait", "attempts": attempts}

    return {"idle": False, "method": "failed", "attempts": attempts}


def _concat_pre_post(pre_data: np.ndarray, post_data: np.ndarray) -> np.ndarray:
    pre_arr = np.asarray(pre_data, dtype=float)
    post_arr = np.asarray(post_data, dtype=float).copy()
    if len(pre_arr) == 0:
        return post_arr
    if len(post_arr) == 0:
        return pre_arr.copy()
    dt_post = float(np.median(np.diff(post_arr[:, 0]))) if len(post_arr) > 1 else 0.0
    post_arr[:, 0] = post_arr[:, 0] - float(post_arr[0, 0]) + float(pre_arr[-1, 0]) + max(dt_post, 1e-6)
    return np.vstack([pre_arr, post_arr])


def _trim_stable_pretail_plus_post(
    pre_data: np.ndarray,
    post_data: np.ndarray,
    *,
    stable_detected_hold_s: float | None = None,
) -> tuple[np.ndarray, float | None, str]:
    pre_arr = np.asarray(pre_data, dtype=float)
    post_arr = np.asarray(post_data, dtype=float)

    if stable_detected_hold_s is not None and np.isfinite(stable_detected_hold_s) and len(pre_arr):
        pre_tail = pre_arr[np.asarray(pre_arr[:, 0], dtype=float) >= float(stable_detected_hold_s)].copy()
        if len(pre_tail) >= 3:
            return _concat_pre_post(pre_tail, post_arr), float(stable_detected_hold_s), "stable_detected_hold_s"

    combined_raw = _concat_pre_post(pre_arr, post_arr)
    trim_start = LD.detect_stable_current_start_time(combined_raw)
    if trim_start is None:
        return combined_raw.copy(), None, "no_trim"
    trimmed = combined_raw[np.asarray(combined_raw[:, 0], dtype=float) >= float(trim_start)].copy()
    return trimmed, float(trim_start), "auto_trim_fallback"


def _load_eis(eis_path: Path) -> np.ndarray:
    return np.asarray(LD.load_eis_from_path(str(eis_path)), dtype=float)


def _resolve_overlap_cutoff(peis_data: np.ndarray, target_cutoff_hz: float | None) -> float:
    peis = np.asarray(peis_data, dtype=float)
    peis = peis[np.isfinite(peis).all(axis=1)]
    peis = peis[np.argsort(peis[:, 0])[::-1]]
    if len(peis) == 0:
        return float(target_cutoff_hz) if target_cutoff_hz is not None else np.nan
    # PEIS is the trusted source for every measured overlap point.
    # FFT only fills in frequencies below the actual PEIS low end.
    return float(np.min(peis[:, 0]))


def _merge_peis_and_fft(
    peis_data: np.ndarray,
    fft_f: np.ndarray,
    fft_z: np.ndarray,
    *,
    cutoff_hz: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    peis = np.asarray(peis_data, dtype=float)
    peis = peis[np.isfinite(peis).all(axis=1)]
    peis = peis[np.argsort(peis[:, 0])[::-1]]
    fft_f = np.asarray(fft_f, dtype=float).reshape(-1)
    fft_z = np.asarray(fft_z, dtype=complex).reshape(-1)
    finite_fft = np.isfinite(fft_f) & np.isfinite(np.real(fft_z)) & np.isfinite(np.imag(fft_z)) & (fft_f > 0)
    fft_f = fft_f[finite_fft]
    fft_z = fft_z[finite_fft]
    order_fft = np.argsort(fft_f)[::-1]
    fft_f = fft_f[order_fft]
    fft_z = fft_z[order_fft]
    if len(peis) == 0:
        return fft_f, fft_z
    peis_cutoff = _resolve_overlap_cutoff(peis, cutoff_hz)
    keep_peis = peis[:, 0] >= peis_cutoff
    keep_fft = fft_f < peis_cutoff * (1.0 - 1e-9)
    merged_f = np.concatenate([peis[keep_peis, 0], fft_f[keep_fft]])
    merged_z = np.concatenate([peis[keep_peis, 1] + 1j * peis[keep_peis, 2], fft_z[keep_fft]])
    order = np.argsort(merged_f)[::-1]
    return merged_f[order], merged_z[order]


def _plot_ca_comparison(
    live_pre: np.ndarray,
    live_post: np.ndarray,
    case7_ca: np.ndarray,
    out_path: Path,
) -> None:
    case7 = np.asarray(case7_ca, dtype=float)
    case7_tail = _extract_post_step_segment(case7)
    live_post_arr = np.asarray(live_post, dtype=float)

    fig, axs = plt.subplots(2, 2, figsize=(11, 7), layout="constrained")
    axs[0, 0].plot(live_pre[:, 0], live_pre[:, 2], color="tab:blue")
    axs[0, 0].set_title("Live pre-CA")
    axs[0, 1].plot(case7[:, 0], case7[:, 2], color="tab:orange")
    axs[0, 1].set_title("Case [7] full CA")
    axs[1, 0].plot(live_post_arr[:, 0], live_post_arr[:, 2], color="tab:blue")
    axs[1, 0].set_title("Live dV-step CA")
    if len(case7_tail):
        axs[1, 1].plot(case7_tail[:, 0], case7_tail[:, 2], color="tab:orange")
    axs[1, 1].set_title("Case [7] post-step tail")
    for ax in axs.ravel():
        ax.grid(True, alpha=0.25)
        ax.set_xlabel("time / s")
        ax.set_ylabel("current / A")
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def _extract_post_step_segment(data: np.ndarray) -> np.ndarray:
    arr = np.asarray(data, dtype=float)
    if len(arr) < 3:
        return arr.copy()
    dv = np.abs(np.diff(arr[:, 1]))
    step_idx = int(np.argmax(dv)) + 1
    tail = arr[step_idx:].copy()
    if len(tail):
        tail[:, 0] = tail[:, 0] - float(tail[0, 0])
    return tail


def _plot_full_fit(freq_hz: np.ndarray, z: np.ndarray, fit_result: dict, out_path: Path, title: str) -> None:
    fig = EF.plot_fit(freq_hz, np.real(z), np.imag(z), fit_result, title=title)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def _plot_full_fit_comparison(live_plot: Path, case7_plot: Path, out_path: Path) -> None:
    live_img = plt.imread(live_plot)
    case7_img = plt.imread(case7_plot)
    fig, axs = plt.subplots(1, 2, figsize=(13, 5.5), layout="constrained")
    axs[0].imshow(live_img)
    axs[0].set_title("Live continuous pre/post seeded")
    axs[1].imshow(case7_img)
    axs[1].set_title("External case [7]")
    for ax in axs:
        ax.axis("off")
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def _plot_measured_peis(peis_data: np.ndarray, out_path: Path, title: str) -> None:
    arr = np.asarray(peis_data, dtype=float)
    order = np.argsort(arr[:, 0])[::-1]
    fig, ax = plt.subplots(figsize=(6.5, 5.2), layout="constrained")
    ax.scatter(arr[order, 1], -arr[order, 2], s=18, color="tab:blue")
    ax.plot(arr[order, 1], -arr[order, 2], color="tab:blue", linewidth=1.0, alpha=0.45)
    ax.set_xlabel("Zre / Ohm")
    ax.set_ylabel("-Zim / Ohm")
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def _plot_fft_input_segment(raw_data: np.ndarray, trimmed_data: np.ndarray, out_path: Path, title: str) -> None:
    raw_arr = np.asarray(raw_data, dtype=float)
    trim_arr = np.asarray(trimmed_data, dtype=float)
    fig, ax = plt.subplots(figsize=(8.0, 4.8), layout="constrained")
    ax.plot(raw_arr[:, 0], raw_arr[:, 2] * 1e6, color="0.75", linewidth=1.0, label="Combined raw")
    ax.plot(trim_arr[:, 0], trim_arr[:, 2] * 1e6, color="tab:red", linewidth=1.3, label="FFT-used segment")
    ax.set_xlabel("time / s")
    ax.set_ylabel("Current / uA")
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous pre/post seeded reference run with stable pre-tail + post-step FFT.")
    parser.add_argument("--ip", default=None, help="BioLogic instrument address. Defaults to config.BIOLOGIC_IP.")
    parser.add_argument("--channel", type=int, default=1, help="1-based BioLogic channel number to use.")
    parser.add_argument("--bias", type=float, default=0.0)
    parser.add_argument("--dv", type=float, default=0.03)
    parser.add_argument("--pre-min", type=float, default=60.0)
    parser.add_argument("--pre-max", type=float, default=300.0)
    parser.add_argument("--scout-min", type=float, default=120.0)
    parser.add_argument("--scout-max", type=float, default=600.0)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument(
        "--peis-floor",
        type=float,
        default=0.5,
        help=(
            "Minimum PEIS depth in Hz. PEIS will measure at least this low; "
            "if FFT recommends an even lower LF, the applied PEIS low end becomes "
            "min(raw recommended LF, this value)."
        ),
    )
    parser.add_argument(
        "--peis-deep-limit",
        type=float,
        default=0.05,
        help=(
            "Deepest allowed PEIS low frequency in Hz. This prevents extremely "
            "long PEIS runs when the FFT seed recommends a value below this limit."
        ),
    )
    parser.add_argument("--peis-npts", type=int, default=60)
    parser.add_argument(
        "--peis-high",
        type=float,
        default=1.0e5,
        help="Highest PEIS frequency in Hz. Default preserves the historical 100 kHz start.",
    )
    parser.add_argument(
        "--peis-amplitude-mv",
        type=float,
        default=30.0,
        help="PEIS sinusoidal perturbation amplitude in mV.",
    )
    parser.add_argument("--post-stable-buffer", type=float, default=10.0)
    parser.add_argument(
        "--bandwidth",
        default=None,
        help="Optional BioLogic bandwidth override for CA/PEIS, e.g. BW4 or BW8. Default uses driver hardware default.",
    )
    parser.add_argument(
        "--current-range",
        default=None,
        help=(
            "Optional BioLogic current range override, e.g. AUTO, p100, n1, n10, "
            "u1. Default uses driver Auto range."
        ),
    )
    parser.add_argument(
        "--fit-model",
        choices=["auto", "RQRQ", "RRQRQ", "RQRQRQ"],
        default="RQRQRQ",
        help="Equivalent-circuit model for merged full-arc fitting. Use auto only as a diagnostic recommender; default preserves the historical RQRQRQ fit.",
    )
    args = parser.parse_args()
    bandwidth_override = _resolve_bandwidth(args.bandwidth)
    if args.bandwidth and bandwidth_override is None:
        raise ValueError(f"Unknown BioLogic bandwidth setting: {args.bandwidth!r}")
    current_range_override = _resolve_current_range(args.current_range)
    if args.current_range and current_range_override is None:
        raise ValueError(f"Unknown BioLogic current range setting: {args.current_range!r}")

    out_dir = _timestamp_dir("continuous_prepost_seeded_reference")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "settings": {
            "bias_v": args.bias,
            "dv_v": args.dv,
            "biologic_ip": args.ip,
            "channel": args.channel,
            "pre_min_s": args.pre_min,
            "pre_max_s": args.pre_max,
            "scout_min_s": args.scout_min,
            "scout_max_s": args.scout_max,
            "ca_dt_s": args.dt,
            "peis_floor_hz": args.peis_floor,
            "peis_minimum_depth_hz": args.peis_floor,
            "peis_deep_limit_hz": args.peis_deep_limit,
            "peis_npts": args.peis_npts,
            "peis_high_hz": args.peis_high,
            "peis_amplitude_mv": args.peis_amplitude_mv,
            "post_stable_buffer_s": args.post_stable_buffer,
            "bandwidth": None if args.bandwidth is None else str(args.bandwidth).upper(),
            "current_range": None if args.current_range is None else str(args.current_range),
            "fit_model": args.fit_model,
        },
    }

    bl = BioLogicController(ip=args.ip)
    bl.connect()
    try:
        _write_stage_marker(out_dir, "connected")
        # A scalar read immediately after connect is not reliable on this setup:
        # we have repeatedly observed EC-Lib INVALIDPARAMETERS from get_values()
        # even though the device itself is connected and a technique can still run.
        # OCV is only informational here, so do not block the measurement on it.
        summary["ocv_before_v"] = None
        try:
            summary["ocv_before_v"] = float(bl.get_ocv(channel=args.channel))
            _write_stage_marker(out_dir, "ocv_read", ocv_v=summary["ocv_before_v"])
        except Exception as exc:
            summary["ocv_read_error"] = repr(exc)
            _write_stage_marker(out_dir, "ocv_read_skipped", error=repr(exc))

        pre = _run_continuous_pre_hold(
            bl,
            v_dc=args.bias,
            min_duration_s=args.pre_min,
            max_duration_s=args.pre_max,
            dt_record_s=args.dt,
            read_interval_s=0.2,
            channel=args.channel,
            bandwidth=bandwidth_override,
            current_range=current_range_override,
            post_stable_buffer_s=args.post_stable_buffer,
        )
        pre_path = out_dir / "pre_ca_continuous.txt"
        _save_txt(pre_path, pre["data"], "time/s  V/V  I/A")
        _write_stage_marker(out_dir, "pre_saved", rows=int(len(pre["data"])), runtime_s=float(pre["runtime_s"]))

        scout = _run_continuous_dv_scout(
            bl,
            v_dc=args.bias,
            dv_v=args.dv,
            min_duration_s=args.scout_min,
            max_duration_s=args.scout_max,
            dt_record_s=args.dt,
            read_interval_s=0.2,
            channel=args.channel,
            bandwidth=bandwidth_override,
            current_range=current_range_override,
        )
        scout_path = out_dir / "dv_scout_continuous.txt"
        _save_txt(scout_path, scout["data"], "time/s  V/V  I/A")
        _write_stage_marker(out_dir, "scout_saved", rows=int(len(scout["data"])), runtime_s=float(scout["runtime_s"]))

        # Force-clear the scout CA technique as soon as we have the buffered data.
        # This keeps the instrument from sitting in an active CA state while the
        # CPU-side FFT/seed analysis runs, which can otherwise leave the next
        # handoff hanging if analysis takes a while or stalls.
        try:
            bl.stop_measurement(channel=args.channel)
            summary["post_scout_stop_request"] = {"performed": True, "error": None}
        except Exception as exc:
            summary["post_scout_stop_request"] = {"performed": False, "error": str(exc)}
        time.sleep(0.5)
        summary["post_scout_idle_wait"] = _json_safe(
            _wait_for_biologic_idle(
                bl,
                channel=args.channel,
                timeout_s=5.0,
                settle_s=0.5,
                poll_interval_s=0.2,
            )
        )
        _write_stage_marker(out_dir, "post_scout_clear_done", post_scout_idle_wait=summary["post_scout_idle_wait"])

        combined_raw = _concat_pre_post(pre["data"], scout["data"])
        combined_raw_path = out_dir / "combined_prepost_raw.txt"
        _save_txt(combined_raw_path, combined_raw, "time/s  V/V  I/A")

        combined_trimmed, trim_start_s, trim_method = _trim_stable_pretail_plus_post(
            pre["data"],
            scout["data"],
            stable_detected_hold_s=pre.get("stable_detected_hold_s"),
        )
        combined_trimmed_path = out_dir / "combined_prepost_trimmed.txt"
        _save_txt(combined_trimmed_path, combined_trimmed, "time/s  V/V  I/A")

        fft_ready = build_fft_ready_ca_trace(
            combined_trimmed,
            average_bin_s=None,
            segmented_smoothing_window_s=None,
        )
        fft_ready_path = out_dir / "combined_prepost_fft_ready.txt"
        _save_txt(fft_ready_path, fft_ready, "time/s  V/V  I/A")
        _write_stage_marker(out_dir, "fft_ready_saved", rows=int(len(fft_ready)), trim_method=trim_method, trim_start_s=trim_start_s)

        seed_rec = recommend_peis_lf_from_cp_txt(str(fft_ready_path), current_in_mA=False)
        _write_stage_marker(out_dir, "seed_recommendation_done")
        raw_seed_lf = float(seed_rec["recommendation"]["recommended_peis_lowest_freq_hz"])
        if not np.isfinite(raw_seed_lf):
            seed_lf = float(args.peis_floor)
        else:
            # PEIS lowest frequency is a "measure at least this low" depth.
            # Smaller Hz means deeper/longer PEIS, so use the lower of the
            # configured depth and the FFT recommendation to preserve overlap,
            # but do not go below the deep-limit because very-low LF PEIS can
            # dominate the entire campaign runtime.
            seed_lf = float(max(min(raw_seed_lf, args.peis_floor), args.peis_deep_limit))
        summary["seed_recommendation"] = {
            "raw_recommended_lf_hz": raw_seed_lf,
            "applied_peis_lf_hz": seed_lf,
            "peis_minimum_depth_hz": float(args.peis_floor),
            "peis_deep_limit_hz": float(args.peis_deep_limit),
            "peis_lowest_freq_cap_hz": None,
            "cp_saturation": _json_safe(seed_rec["cp_saturation"]),
            "smoothed_duration_s": float(seed_rec["smoothed_duration_s"]),
        }

        peis_handoff = _force_biologic_idle_for_handoff(
            bl,
            channel=args.channel,
            initial_timeout_s=5.0,
            reconnect_timeout_s=20.0,
        )
        summary["pre_peis_handoff"] = _json_safe(peis_handoff)
        summary["pre_peis_idle_wait"] = _json_safe(peis_handoff)
        summary["pre_peis_reconnect"] = {
            "performed": peis_handoff.get("method") == "reconnect_stop_wait",
            "reason": None if bool(peis_handoff.get("idle", False)) else "handoff_failed",
        }
        _write_stage_marker(out_dir, "pre_peis_handoff_done", handoff=summary["pre_peis_handoff"])

        _write_stage_marker(out_dir, "starting_peis", seed_lf_hz=seed_lf, high_hz=float(args.peis_high))
        peis_data = bl.run_peis(
            v_dc=args.bias,
            f_high=args.peis_high,
            f_low=seed_lf,
            n_pts=args.peis_npts,
            amplitude_mv=args.peis_amplitude_mv,
            channel=args.channel,
            bandwidth=bandwidth_override,
            current_range=current_range_override,
        )
        peis_path = out_dir / "seeded_peis.txt"
        _save_txt(peis_path, peis_data, "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")
        _write_stage_marker(out_dir, "peis_saved", rows=int(len(peis_data)))

        analysis_result, visuals = analyze_measurement_files_with_visuals(
            str(fft_ready_path),
            str(peis_path),
            sample_name=f"continuous_prepost_seeded_{args.bias:+.3f}V",
        )
        peis_loaded = _load_eis(peis_path)
        merged_cutoff_target = analysis_result.recommended_peis_lowest_freq_hz
        merged_cutoff = _resolve_overlap_cutoff(peis_loaded, merged_cutoff_target)
        merged_f, merged_z = _merge_peis_and_fft(
            peis_loaded,
            np.asarray(seed_rec["analysis_f"], dtype=float),
            np.asarray(seed_rec["analysis_z"], dtype=complex),
            cutoff_hz=merged_cutoff,
        )
        if args.fit_model == "auto":
            merged_fit = EF.fit_equivalent_circuit_auto(merged_f, np.real(merged_z), np.imag(merged_z))
        else:
            merged_fit = EF.fit_circuit_model_stable(merged_f, np.real(merged_z), np.imag(merged_z), model=args.fit_model)
        full_fit_path = out_dir / "continuous_prepost_seeded_full_fit.png"
        _plot_full_fit(merged_f, merged_z, merged_fit, full_fit_path, title=f"bias={args.bias:+.3f} V")
        measured_peis_path = out_dir / "continuous_prepost_seeded_measured_peis.png"
        _plot_measured_peis(peis_loaded, measured_peis_path, title=f"bias={args.bias:+.3f} V measured PEIS")
        fft_input_segment_path = out_dir / "continuous_prepost_seeded_fft_input_segment.png"
        _plot_fft_input_segment(
            combined_raw,
            combined_trimmed,
            fft_input_segment_path,
            title=f"bias={args.bias:+.3f} V FFT input segment",
        )

        case7_ca = LD.load_dc_from_path(str(CASE7_CA_PATH), current_in_mA=True)
        ca_compare_path = out_dir / "live_vs_case7_ca_curves.png"
        _plot_ca_comparison(pre["data"], scout["data"], case7_ca, ca_compare_path)

        full_fit_compare_path = out_dir / "live_vs_case7_full_fit.png"
        _plot_full_fit_comparison(full_fit_path, CASE7_FULL_ARC_PATH, full_fit_compare_path)

        try:
            ocv_after_v = float(bl.get_ocv(channel=args.channel))
        except Exception:
            ocv_after_v = None

        summary.update(
            {
                "ocv_after_v": ocv_after_v,
                "pre": {
                    "runtime_s": pre["runtime_s"],
                    "rows": int(len(pre["data"])),
                    "stopped_by_stability": pre["stopped_by_stability"],
                    "stable_detected_hold_s": pre.get("stable_detected_hold_s"),
                    "assessment": _json_safe(pre["assessment"]),
                    "path": str(pre_path),
                },
                "scout": {
                    "runtime_s": scout["runtime_s"],
                    "rows": int(len(scout["data"])),
                    "stopped_by_cp_saturation": scout["stopped_by_cp_saturation"],
                    "assessment": _json_safe(scout["assessment"]),
                    "path": str(scout_path),
                },
                "combined": {
                    "raw_path": str(combined_raw_path),
                    "trimmed_path": str(combined_trimmed_path),
                    "fft_ready_path": str(fft_ready_path),
                    "trim_start_s": trim_start_s,
                    "trim_method": trim_method,
                    "trimmed_rows": int(len(combined_trimmed)),
                    "fft_ready_rows": int(len(fft_ready)),
                },
                "peis": {
                    "rows": int(len(peis_data)),
                    "path": str(peis_path),
                    "f_low_hz": seed_lf,
                    "f_high_hz": float(args.peis_high),
                },
                "analysis_result": _json_safe(analysis_result.__dict__),
                "visuals": _json_safe(visuals),
                "merged_fit": _json_safe(merged_fit),
                "merged_cutoff_hz": merged_cutoff,
                "plots": {
                    "full_fit": str(full_fit_path),
                    "measured_peis": str(measured_peis_path),
                    "fft_input_segment": str(fft_input_segment_path),
                    "ca_compare": str(ca_compare_path),
                    "full_fit_compare": str(full_fit_compare_path),
                },
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary_path = out_dir / "continuous_prepost_seeded_reference_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(str(summary_path))


if __name__ == "__main__":
    main()
