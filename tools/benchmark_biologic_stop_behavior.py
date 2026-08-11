import csv
import json
import sys
import threading
import time
from dataclasses import dataclass
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


def _save_samples_csv(path: Path, samples: list[dict[str, Any]]):
    if not samples:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(samples[0].keys()))
        writer.writeheader()
        writer.writerows(samples)


def _poll_until_finished(
    bl: BioLogicController,
    thread: threading.Thread,
    *,
    target_interval_s: float,
    stop_after_s: float,
):
    samples: list[dict[str, Any]] = []
    errors: list[str] = []
    t0 = time.perf_counter()
    next_deadline = t0
    stop_requested_at = None

    while thread.is_alive():
        now = time.perf_counter()
        if stop_requested_at is None and (now - t0) >= stop_after_s:
            stop_requested_at = time.perf_counter()
            try:
                bl.stop_measurement(channel=1)
            except Exception as exc:  # pragma: no cover - hardware/runtime guard
                errors.append(f"stop:{type(exc).__name__}: {exc}")
        if now < next_deadline:
            time.sleep(next_deadline - now)

        poll_started = time.perf_counter()
        try:
            snapshot = bl.get_live_values(channel=1)
        except Exception as exc:  # pragma: no cover - hardware/runtime guard
            errors.append(f"poll:{type(exc).__name__}: {exc}")
            snapshot = {}
        poll_finished = time.perf_counter()
        samples.append(
            {
                "t_rel_s": poll_started - t0,
                "poll_duration_ms": (poll_finished - poll_started) * 1000.0,
                "stop_requested": stop_requested_at is not None,
                "elapsed_s": _safe_float(snapshot.get("elapsed_s")),
                "frequency_hz": _safe_float(snapshot.get("frequency_hz")),
                "ewe_v": _safe_float(snapshot.get("ewe_v")),
                "current_a": _safe_float(snapshot.get("current_a")),
                "buffer_bytes": snapshot.get("buffer_bytes"),
            }
        )
        next_deadline = poll_started + target_interval_s

    thread.join(timeout=5.0)
    finished_at = time.perf_counter()
    stop_latency_s = None
    if stop_requested_at is not None:
        stop_latency_s = finished_at - stop_requested_at

    return {
        "samples": samples,
        "errors": errors,
        "stop_requested_at_rel_s": None if stop_requested_at is None else stop_requested_at - t0,
        "stop_latency_s": stop_latency_s,
    }


def _summarize_case(run: TechniqueRun, poll_result: dict, *, requested_stop_after_s: float):
    samples = poll_result["samples"]
    poll_ms = np.array(
        [row["poll_duration_ms"] for row in samples if row.get("poll_duration_ms") is not None],
        dtype=float,
    )
    rel_times = np.array([row["t_rel_s"] for row in samples if row.get("t_rel_s") is not None], dtype=float)
    intervals = np.diff(rel_times) if len(rel_times) >= 2 else np.array([], dtype=float)
    return {
        "technique_error": run.error,
        "returned_rows": int(len(run.result)) if isinstance(run.result, np.ndarray) else None,
        "runtime_s": (
            None
            if run.started_at is None or run.finished_at is None
            else float(run.finished_at - run.started_at)
        ),
        "requested_stop_after_s": requested_stop_after_s,
        "stop_requested_at_rel_s": poll_result["stop_requested_at_rel_s"],
        "stop_latency_s": poll_result["stop_latency_s"],
        "sample_count": len(samples),
        "poll_error_count": len(poll_result["errors"]),
        "poll_errors": poll_result["errors"][:10],
        "poll_duration_ms_median": float(np.median(poll_ms)) if len(poll_ms) else None,
        "poll_duration_ms_p95": float(np.percentile(poll_ms, 95)) if len(poll_ms) else None,
        "interval_ms_median": float(np.median(intervals) * 1000.0) if len(intervals) else None,
        "interval_ms_p95": float(np.percentile(intervals * 1000.0, 95)) if len(intervals) else None,
    }


def run_stop_benchmark():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"live_stop_benchmark_{timestamp}"
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

        ca_requested_duration_s = 20.0
        ca_stop_after_s = 3.0
        ca_run = TechniqueRun("ca_hold_stop")
        ca_thread = _run_in_thread(
            lambda: bl.run_ca_hold(
                v_dc=0.0,
                duration=ca_requested_duration_s,
                dt_record=0.05,
                read_interval=0.1,
            ),
            ca_run,
        )
        ca_poll = _poll_until_finished(
            bl,
            ca_thread,
            target_interval_s=target_interval_s,
            stop_after_s=ca_stop_after_s,
        )
        _save_samples_csv(out_dir / "ca_hold_stop_samples.csv", ca_poll["samples"])
        ca_case = _summarize_case(ca_run, ca_poll, requested_stop_after_s=ca_stop_after_s)
        ca_case["requested_duration_s"] = ca_requested_duration_s
        summary["cases"]["ca_hold_stop"] = ca_case

        peis_stop_after_s = 3.0
        peis_run = TechniqueRun("peis_stop")
        peis_thread = _run_in_thread(
            lambda: bl.run_peis(
                v_dc=0.0,
                f_high=1e4,
                f_low=0.1,
                n_pts=60,
                read_interval=0.5,
            ),
            peis_run,
        )
        peis_poll = _poll_until_finished(
            bl,
            peis_thread,
            target_interval_s=target_interval_s,
            stop_after_s=peis_stop_after_s,
        )
        _save_samples_csv(out_dir / "peis_stop_samples.csv", peis_poll["samples"])
        peis_case = _summarize_case(peis_run, peis_poll, requested_stop_after_s=peis_stop_after_s)
        peis_case["requested_program"] = {"f_high_hz": 1e4, "f_low_hz": 0.1, "n_pts": 60}
        summary["cases"]["peis_stop"] = peis_case

    finally:
        bl.disconnect()

    summary_path = out_dir / "live_stop_benchmark_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved live-stop benchmark to: {summary_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    run_stop_benchmark()
