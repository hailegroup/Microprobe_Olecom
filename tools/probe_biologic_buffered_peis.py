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


def _write_csv(path: Path, rows: list[dict[str, Any]]):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def poll_buffered_peis(bl: BioLogicController, thread: threading.Thread, target_interval_s: float):
    scalar_rows: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    nyquist_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    t0 = time.perf_counter()
    next_deadline = t0

    while thread.is_alive():
        now = time.perf_counter()
        if now < next_deadline:
            time.sleep(next_deadline - now)
        poll_started = time.perf_counter()

        try:
            live = bl.get_live_values(channel=1)
        except Exception as exc:  # pragma: no cover - hardware/runtime guard
            live = {}
            errors.append(f"live:{type(exc).__name__}: {exc}")

        try:
            payload = bl.get_buffered_data(channel=1)
        except Exception as exc:  # pragma: no cover - hardware/runtime guard
            payload = {
                "technique": None,
                "process_index": None,
                "nb_rows": 0,
                "nb_cols": 0,
                "values": {},
                "parsed_array": np.empty((0, 0)),
            }
            errors.append(f"buffered:{type(exc).__name__}: {exc}")

        poll_finished = time.perf_counter()
        scalar_rows.append(
            {
                "t_rel_s": poll_started - t0,
                "poll_duration_ms": (poll_finished - poll_started) * 1000.0,
                "elapsed_s": _safe_float(live.get("elapsed_s")),
                "frequency_hz": _safe_float(live.get("frequency_hz")),
                "ewe_v": _safe_float(live.get("ewe_v")),
                "current_a": _safe_float(live.get("current_a")),
                "buffer_bytes": live.get("buffer_bytes"),
            }
        )

        chunk_rows.append(
            {
                "t_rel_s": poll_started - t0,
                "technique": payload.get("technique"),
                "process_index": payload.get("process_index"),
                "nb_rows": payload.get("nb_rows"),
                "nb_cols": payload.get("nb_cols"),
                "buffer_bytes": payload.get("values", {}).get("buffer_bytes"),
                "live_frequency_hz": payload.get("values", {}).get("frequency_hz"),
                "parsed_point_count": int(
                    payload.get("parsed_array").shape[0]
                    if isinstance(payload.get("parsed_array"), np.ndarray) and payload.get("parsed_array").ndim >= 1
                    else 0
                ),
            }
        )

        parsed_array = payload.get("parsed_array")
        if isinstance(parsed_array, np.ndarray) and parsed_array.size:
            for freq_hz, re_z, neg_im_z in parsed_array:
                nyquist_rows.append(
                    {
                        "t_rel_s": poll_started - t0,
                        "frequency_hz": float(freq_hz),
                        "re_z_ohm": float(re_z),
                        "neg_im_z_ohm": float(neg_im_z),
                    }
                )

        next_deadline = poll_started + target_interval_s

    return scalar_rows, chunk_rows, nyquist_rows, errors


def summarize_probe(run: TechniqueRun, scalar_rows, chunk_rows, nyquist_rows, errors):
    unique_freqs = {
        round(float(row["frequency_hz"]), 6)
        for row in nyquist_rows
        if row.get("frequency_hz") is not None and np.isfinite(float(row["frequency_hz"]))
    }
    chunk_with_rows = sum(1 for row in chunk_rows if int(row.get("nb_rows") or 0) > 0)
    chunk_with_points = sum(1 for row in chunk_rows if int(row.get("parsed_point_count") or 0) > 0)
    return {
        "technique_error": run.error,
        "technique_runtime_s": (
            None
            if run.started_at is None or run.finished_at is None
            else float(run.finished_at - run.started_at)
        ),
        "scalar_sample_count": len(scalar_rows),
        "chunk_poll_count": len(chunk_rows),
        "chunk_with_rows_count": chunk_with_rows,
        "chunk_with_parsed_points_count": chunk_with_points,
        "nyquist_point_count": len(nyquist_rows),
        "unique_nyquist_frequency_count": len(unique_freqs),
        "first_nyquist_frequency_hz": min(unique_freqs) if unique_freqs else None,
        "last_nyquist_frequency_hz": max(unique_freqs) if unique_freqs else None,
        "errors": errors[:20],
    }


def run_probe():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"buffered_peis_probe_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    bl = BioLogicController()
    bl.connect()
    summary: dict[str, Any] = {
        "timestamp": timestamp,
        "target_poll_interval_s": 0.2,
        "requested_program": {
            "v_dc": 0.0,
            "f_high_hz": 1.0e4,
            "f_low_hz": 1.0,
            "n_pts": 20,
            "amplitude_mv": 10.0,
        },
    }

    try:
        run = TechniqueRun("peis_buffered_probe")
        probe_thread = _run_in_thread(
            lambda: bl.run_peis(
                v_dc=summary["requested_program"]["v_dc"],
                f_high=summary["requested_program"]["f_high_hz"],
                f_low=summary["requested_program"]["f_low_hz"],
                n_pts=summary["requested_program"]["n_pts"],
                amplitude_mv=summary["requested_program"]["amplitude_mv"],
                read_interval=0.5,
            ),
            run,
        )
        scalar_rows, chunk_rows, nyquist_rows, errors = poll_buffered_peis(
            bl,
            probe_thread,
            target_interval_s=summary["target_poll_interval_s"],
        )
        probe_thread.join()

        _write_csv(out_dir / "scalar_live_samples.csv", scalar_rows)
        _write_csv(out_dir / "buffered_chunk_samples.csv", chunk_rows)
        _write_csv(out_dir / "buffered_nyquist_points.csv", nyquist_rows)
        summary["probe"] = summarize_probe(run, scalar_rows, chunk_rows, nyquist_rows, errors)
    finally:
        bl.disconnect()

    summary_path = out_dir / "buffered_peis_probe_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved buffered PEIS probe to: {summary_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    run_probe()
