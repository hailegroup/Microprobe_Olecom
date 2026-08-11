import csv
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from driver_biologic import BioLogicController  # noqa: E402


@dataclass
class TechniqueRun:
    name: str
    result: Any = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


def _run_in_thread(target, run: TechniqueRun):
    def _wrapper():
        run.started_at = time.perf_counter()
        try:
            run.result = target()
        except Exception as exc:  # pragma: no cover - hardware/runtime guard
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            run.finished_at = time.perf_counter()

    thread = threading.Thread(target=_wrapper, name=f"{run.name}_thread", daemon=True)
    thread.start()
    return thread


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def poll_live_values(bl: BioLogicController, thread: threading.Thread, target_interval_s: float):
    samples: list[dict[str, Any]] = []
    errors: list[str] = []
    t0 = time.perf_counter()
    next_deadline = t0
    while thread.is_alive():
        now = time.perf_counter()
        if now < next_deadline:
            time.sleep(next_deadline - now)
        poll_started = time.perf_counter()
        try:
            snapshot = bl.get_live_values(channel=1)
        except Exception as exc:  # pragma: no cover - hardware/runtime guard
            errors.append(f"{type(exc).__name__}: {exc}")
            snapshot = {}
        poll_finished = time.perf_counter()
        samples.append(
            {
                "t_rel_s": poll_started - t0,
                "poll_duration_ms": (poll_finished - poll_started) * 1000.0,
                "elapsed_s": _safe_float(snapshot.get("elapsed_s")),
                "frequency_hz": _safe_float(snapshot.get("frequency_hz")),
                "ewe_v": _safe_float(snapshot.get("ewe_v")),
                "current_a": _safe_float(snapshot.get("current_a")),
                "buffer_bytes": snapshot.get("buffer_bytes"),
            }
        )
        next_deadline = poll_started + target_interval_s

    # One final sample after the program thread exits helps capture the last state.
    try:
        snapshot = bl.get_live_values(channel=1)
        samples.append(
            {
                "t_rel_s": time.perf_counter() - t0,
                "poll_duration_ms": None,
                "elapsed_s": _safe_float(snapshot.get("elapsed_s")),
                "frequency_hz": _safe_float(snapshot.get("frequency_hz")),
                "ewe_v": _safe_float(snapshot.get("ewe_v")),
                "current_a": _safe_float(snapshot.get("current_a")),
                "buffer_bytes": snapshot.get("buffer_bytes"),
            }
        )
    except Exception as exc:  # pragma: no cover - hardware/runtime guard
        errors.append(f"final_sample:{type(exc).__name__}: {exc}")
    return samples, errors


def summarize_samples(samples: list[dict[str, Any]], errors: list[str]):
    if not samples:
        return {"sample_count": 0, "errors": errors}

    rel_times = np.array([row["t_rel_s"] for row in samples if row.get("t_rel_s") is not None], dtype=float)
    poll_ms = np.array(
        [row["poll_duration_ms"] for row in samples if row.get("poll_duration_ms") is not None],
        dtype=float,
    )
    intervals = np.diff(rel_times) if len(rel_times) >= 2 else np.array([], dtype=float)

    reset_like_count = 0
    filtered_rows = []
    for row in samples:
        elapsed = row.get("elapsed_s")
        ewe = row.get("ewe_v")
        current = row.get("current_a")
        if (
            elapsed is not None
            and ewe is not None
            and current is not None
            and abs(float(elapsed)) < 1e-9
            and abs(float(ewe) - 1.0) < 1e-9
            and abs(float(current) - 1.0) < 1e-9
        ):
            reset_like_count += 1
            continue
        filtered_rows.append(row)

    freq_values = [
        float(row["frequency_hz"])
        for row in filtered_rows
        if row.get("frequency_hz") is not None and np.isfinite(float(row["frequency_hz"]))
    ]
    current_values = [
        float(row["current_a"])
        for row in filtered_rows
        if row.get("current_a") is not None and np.isfinite(float(row["current_a"]))
    ]
    elapsed_values = [
        float(row["elapsed_s"])
        for row in filtered_rows
        if row.get("elapsed_s") is not None and np.isfinite(float(row["elapsed_s"]))
    ]

    return {
        "sample_count": len(samples),
        "poll_error_count": len(errors),
        "poll_errors": errors[:10],
        "achieved_interval_ms_mean": float(np.mean(intervals) * 1000.0) if len(intervals) else None,
        "achieved_interval_ms_median": float(np.median(intervals) * 1000.0) if len(intervals) else None,
        "achieved_interval_ms_p95": float(np.percentile(intervals * 1000.0, 95)) if len(intervals) else None,
        "achieved_interval_ms_max": float(np.max(intervals) * 1000.0) if len(intervals) else None,
        "poll_duration_ms_mean": float(np.mean(poll_ms)) if len(poll_ms) else None,
        "poll_duration_ms_median": float(np.median(poll_ms)) if len(poll_ms) else None,
        "poll_duration_ms_p95": float(np.percentile(poll_ms, 95)) if len(poll_ms) else None,
        "poll_duration_ms_max": float(np.max(poll_ms)) if len(poll_ms) else None,
        "poll_duration_count_gt_100ms": int(np.sum(poll_ms > 100.0)) if len(poll_ms) else 0,
        "poll_duration_count_gt_500ms": int(np.sum(poll_ms > 500.0)) if len(poll_ms) else 0,
        "reset_like_sample_count": reset_like_count,
        "elapsed_updates": len({round(v, 6) for v in elapsed_values}),
        "frequency_updates": len({round(v, 6) for v in freq_values}),
        "current_abs_std_a": float(np.std(current_values)) if len(current_values) else None,
        "current_abs_range_a": (
            float(np.max(current_values) - np.min(current_values)) if len(current_values) else None
        ),
        "first_frequency_hz": float(freq_values[0]) if freq_values else None,
        "last_frequency_hz": float(freq_values[-1]) if freq_values else None,
    }


def save_samples_csv(path: Path, samples: list[dict[str, Any]]):
    if not samples:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(samples[0].keys()))
        writer.writeheader()
        writer.writerows(samples)


def run_benchmark():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"live_polling_benchmark_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    bl = BioLogicController()
    bl.connect()
    summary: dict[str, Any] = {
        "timestamp": timestamp,
        "target_poll_interval_s": 0.05,
        "cases": {},
    }

    try:
        target_interval_s = 0.05

        ca_run = TechniqueRun("ca_hold")
        ca_requested_duration_s = 8.0
        ca_thread = _run_in_thread(
            lambda: bl.run_ca_hold(v_dc=0.0, duration=ca_requested_duration_s, dt_record=0.05, read_interval=0.1),
            ca_run,
        )
        ca_samples, ca_errors = poll_live_values(bl, ca_thread, target_interval_s=target_interval_s)
        ca_thread.join()
        save_samples_csv(out_dir / "ca_hold_live_samples.csv", ca_samples)
        ca_case = summarize_samples(ca_samples, ca_errors)
        ca_case["technique_error"] = ca_run.error
        ca_case["returned_rows"] = int(len(ca_run.result)) if isinstance(ca_run.result, np.ndarray) else None
        ca_case["technique_runtime_s"] = (
            float(ca_run.finished_at - ca_run.started_at)
            if ca_run.finished_at is not None and ca_run.started_at is not None
            else None
        )
        ca_case["requested_duration_s"] = ca_requested_duration_s
        ca_case["runtime_overhead_s"] = (
            None
            if ca_case["technique_runtime_s"] is None
            else float(ca_case["technique_runtime_s"] - ca_requested_duration_s)
        )
        summary["cases"]["ca_hold"] = ca_case

        peis_run = TechniqueRun("peis")
        peis_requested = {"f_high_hz": 1e4, "f_low_hz": 10.0, "n_pts": 12}
        peis_thread = _run_in_thread(
            lambda: bl.run_peis(v_dc=0.0, f_high=peis_requested["f_high_hz"], f_low=peis_requested["f_low_hz"], n_pts=peis_requested["n_pts"], read_interval=0.1),
            peis_run,
        )
        peis_samples, peis_errors = poll_live_values(bl, peis_thread, target_interval_s=target_interval_s)
        peis_thread.join()
        save_samples_csv(out_dir / "peis_live_samples.csv", peis_samples)
        peis_case = summarize_samples(peis_samples, peis_errors)
        peis_case["technique_error"] = peis_run.error
        peis_case["returned_rows"] = int(len(peis_run.result)) if isinstance(peis_run.result, np.ndarray) else None
        peis_case["technique_runtime_s"] = (
            float(peis_run.finished_at - peis_run.started_at)
            if peis_run.finished_at is not None and peis_run.started_at is not None
            else None
        )
        peis_case["requested_program"] = peis_requested
        summary["cases"]["peis"] = peis_case

    finally:
        bl.disconnect()

    summary_path = out_dir / "live_polling_benchmark_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved live polling benchmark to: {summary_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    run_benchmark()
