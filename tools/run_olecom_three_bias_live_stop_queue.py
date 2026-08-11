from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = PROJECT_DIR / "results"

try:
    from run_olecom_pre_scout_post_hybrid import OleComController, _cleanup_eclab_processes
    from run_olecom_pre_scout_live_stop_hybrid import run_once as _run_live_stop_once
    _HAS_OLECOM_RECOVERY = True
    _OLECOM_RECOVERY_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - shown in recovery_status.json
    OleComController = None
    _cleanup_eclab_processes = None
    _run_live_stop_once = None
    _HAS_OLECOM_RECOVERY = False
    _OLECOM_RECOVERY_IMPORT_ERROR = repr(exc)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_json_error": str(exc)}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _eclab_process_snapshot() -> list[dict]:
    """Return visible/hidden EC-Lab process ids without requiring psutil."""
    ps = (
        "$procs = Get-Process EClab -ErrorAction SilentlyContinue; "
        "foreach ($p in $procs) { "
        "  $title = $p.MainWindowTitle; "
        "  if ($title -eq $null) { $title = '' }; "
        "  [Console]::WriteLine(($p.Id.ToString()) + '|' + ($p.MainWindowHandle.ToString()) + '|' + $title) "
        "}"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            out = []
            for line in (result.stdout or "").splitlines():
                parts = line.split("|", 2)
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[0])
                    handle = int(float(parts[1]))
                except Exception:
                    continue
                title = parts[2] if len(parts) > 2 else ""
                out.append(
                    {
                        "image": "EClab.exe",
                        "pid": pid,
                        "main_window_handle": handle,
                        "hidden": handle == 0,
                        "title": title,
                    }
                )
            return out
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq EClab.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return [{"error": f"tasklist failed: {exc!r}"}]
    text = (result.stdout or "").strip()
    if not text or text.upper().startswith("INFO:"):
        return []
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        try:
            pid = int(str(row[1]).strip())
        except Exception:
            continue
        out.append({"image": row[0], "pid": pid, "session": row[2] if len(row) > 2 else "", "hidden": None})
    return out


def _hidden_eclab_count(snapshot: list[dict]) -> int:
    return sum(1 for item in snapshot if item.get("hidden") is True)


def _annotate_eclab_counts(row: dict, suffix: str) -> list[dict]:
    snapshot = _eclab_process_snapshot()
    row[f"eclab_process_count_{suffix}"] = len(snapshot)
    row[f"eclab_hidden_count_{suffix}"] = _hidden_eclab_count(snapshot)
    return snapshot


def _record_process_guard_event(campaign_dir: Path, event: dict) -> None:
    path = campaign_dir / "eclab_process_guard.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(history, list):
            history = [history]
    except Exception:
        history = []
    history.append(event)
    path.write_text(json.dumps(history, indent=2, default=str), encoding="utf-8")


def _guard_eclab_processes_between_conditions(args, campaign_dir: Path, stage: str) -> dict:
    """Clean stale hidden EC-Lab sessions only between measurements, never during a run."""
    before = _eclab_process_snapshot()
    hidden_before = _hidden_eclab_count(before)
    event = {
        "stage": stage,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "processes_before": before,
        "hidden_before": hidden_before,
        "action": "none",
    }
    if (
        not bool(getattr(args, "simulate", False))
        and bool(getattr(args, "cleanup_stale_between_conditions", True))
        and hidden_before > int(getattr(args, "max_hidden_eclab_processes", 1))
    ):
        if _cleanup_eclab_processes is None:
            event["action"] = "cleanup_unavailable"
            event["error"] = _OLECOM_RECOVERY_IMPORT_ERROR or "cleanup helper unavailable"
        else:
            try:
                killed = _cleanup_eclab_processes(include_visible=False)
                time.sleep(max(0.0, float(getattr(args, "recover_settle_s", 2.0))))
                event["action"] = "cleanup_hidden"
                event["killed_pids"] = killed
            except Exception as exc:
                event["action"] = "cleanup_hidden_failed"
                event["error"] = repr(exc)
    after = _eclab_process_snapshot()
    event["processes_after"] = after
    event["hidden_after"] = _hidden_eclab_count(after)
    _record_process_guard_event(campaign_dir, event)
    return event


def _safe_float(value, default=None):
    try:
        out = float(value)
    except Exception:
        return default
    if out != out:
        return default
    return out


def _add_bool_arg(parser, name, *, default, help_text=""):
    """Python 3.7-compatible replacement for BooleanOptionalAction."""
    dest = name.lstrip("-").replace("-", "_")
    parser.add_argument(name, dest=dest, action="store_true", default=default, help=help_text)
    parser.add_argument(f"--no-{name.lstrip('-')}", dest=dest, action="store_false")


def _clamp(value: float, low: float, high: float) -> float:
    return max(float(low), min(float(high), float(value)))


def _percentile(values, pct: float):
    vals = sorted(float(v) for v in values if _safe_float(v) is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * _clamp(float(pct), 0.0, 100.0) / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def _pre_category(index: int, label_prefix: str = "") -> str:
    if label_prefix:
        return "repeat_same_bias"
    return "first_at_site" if int(index) == 1 else "subsequent_bias"


def _condition_label(index: int, bias: float, label_prefix: str = "") -> str:
    sign = "p" if bias >= 0 else "m"
    prefix = f"{label_prefix}_" if label_prefix else ""
    return f"{index:02d}_{prefix}bias_{sign}{abs(bias):.3f}V".replace(".", "p")


def _row_condition_index(row: dict) -> Optional[int]:
    label = str(row.get("label", ""))
    head = label.split("_", 1)[0]
    if head.isdigit():
        return int(head)
    return None


def _load_completed_resume_rows(csv_path: Path) -> dict[int, dict]:
    if not csv_path.exists():
        return {}
    completed: dict[int, dict] = {}
    try:
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                idx = _row_condition_index(row)
                if idx is None:
                    continue
                if str(row.get("status", "")).startswith("complete"):
                    completed[idx] = dict(row)
    except Exception:
        return {}
    return completed


def _category_default_pre_s(args, category: str) -> float:
    if category == "first_at_site" and args.first_pre_s is not None:
        return float(args.first_pre_s)
    if category == "subsequent_bias" and args.subsequent_pre_s is not None:
        return float(args.subsequent_pre_s)
    if category == "repeat_same_bias":
        if args.repeat_pre_s is not None:
            return float(args.repeat_pre_s)
        if args.subsequent_pre_s is not None:
            return float(args.subsequent_pre_s)
    return float(args.pre_s)


def _choose_pre_s(args, pre_learning: dict, category: str) -> float:
    if not bool(args.adaptive_pre):
        return float(args.pre_s)
    observations = list(pre_learning.get(category, []))
    if category == "subsequent_bias" and not observations:
        # The first bias at a site teaches the next bias how long pre-hold
        # actually needed to stabilize. Use the detected stability time, not
        # the full pre_s_used duration, so the learned hold does not inflate.
        observations = list(pre_learning.get("first_at_site", []))
    if category == "repeat_same_bias" and not observations:
        observations = list(pre_learning.get("subsequent_bias", []))
    learning_mode = str(getattr(args, "pre_learning_mode", "last") or "last").lower()
    if observations and learning_mode == "last":
        learned = float(observations[-1])
    else:
        learned = _percentile(observations, float(args.pre_learning_percentile))
    if learned is None:
        return _category_default_pre_s(args, category)
    margin_mode = str(getattr(args, "pre_margin_mode", "multiply") or "multiply").lower()
    if margin_mode == "multiply":
        learned_target = float(learned) * float(getattr(args, "pre_margin_factor", 1.3))
    else:
        learned_target = float(learned) + float(args.pre_margin_s)
    return _clamp(float(learned_target), float(args.pre_min_s), float(args.pre_max_s))


def _estimate_pre_learning_observation(row: dict, args) -> Optional[float]:
    if not bool(args.adaptive_pre):
        return None
    stable = _safe_float(row.get("pre_stability_time_s"))
    learned = stable if stable is not None and stable > 0 else None
    if learned is None or learned <= 0:
        return None
    return float(learned)


def _learn_pre_s(pre_learning: dict, row: dict, category: str, args) -> Optional[float]:
    learned = _safe_float(row.get("pre_learned_observation_s"))
    if learned is None:
        learned = _estimate_pre_learning_observation(row, args)
    if learned is None or learned <= 0:
        return None
    row["pre_learned_observation_s"] = float(learned)
    pre_learning.setdefault(category, []).append(float(learned))
    max_n = int(max(1, args.pre_learning_max_points))
    pre_learning[category] = pre_learning[category][-max_n:]
    return float(learned)


def _append_csv(path: Path, row: dict) -> None:
    fields = [
        "timestamp",
        "label",
        "attempt",
        "retry_of",
        "bias_v",
        "status",
        "recovery_status",
        "run_dir",
        "pre_category",
        "pre_s_used",
        "eclab_process_count_before",
        "eclab_hidden_count_before",
        "eclab_process_count_after",
        "eclab_hidden_count_after",
        "pre_stability_detected",
        "pre_stability_time_s",
        "pre_stability_rel_limit",
        "pre_tail_shift_rel",
        "pre_tail_rel_change_window",
        "pre_learned_observation_s",
        "raw_lf_hz",
        "applied_peis_lf_hz",
        "scout_stop_reason",
        "scout_guard_ready",
        "scout_guard_reason",
        "fft_ready_duration_s",
        "peis_points",
        "peis_sanity_status",
        "peis_sanity_reason",
        "peis_sanity_max_adjacent_re_ratio",
        "peis_sanity_negative_neg_im_fraction",
        "peis_score",
        "full_arc_score",
        "postprocess_status",
        "onepage_png",
        "full_arc_png",
        "error",
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _read_measured_peis(path: Path) -> list[tuple[float, float, float]]:
    rows: list[tuple[float, float, float]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.lower().startswith("freq"):
                continue
            parts = line.replace(",", "\t").split()
            if len(parts) < 3:
                continue
            try:
                freq = float(parts[0])
                rez = float(parts[1])
                neg_imz = float(parts[2])
            except Exception:
                continue
            if math.isfinite(freq) and math.isfinite(rez) and math.isfinite(neg_imz):
                rows.append((freq, rez, neg_imz))
    rows.sort(key=lambda item: item[0])
    return rows


def _peis_sanity_check(run_dir: Path, summary: dict, args) -> dict:
    """Fast PEIS-only guard used before full fitting/plotting.

    This catches channel/EC-Lab-state failures where PEIS points jump by orders
    of magnitude even though the technique technically completed and saved MPR.
    """
    peis_path = Path(str(summary.get("peis_txt") or (run_dir / "measured_peis.txt")))
    rows = _read_measured_peis(peis_path)
    reasons: list[str] = []
    finite_count = len(rows)
    min_points = int(getattr(args, "peis_sanity_min_points", 8))
    max_adjacent_re_ratio_limit = float(getattr(args, "peis_sanity_max_adjacent_re_ratio", 8.0))
    max_negative_neg_im_fraction = float(getattr(args, "peis_sanity_max_negative_neg_im_fraction", 0.25))
    max_neg_im_sign_flips = int(getattr(args, "peis_sanity_max_neg_im_sign_flips", 2))
    neg_im_deadband_ohm = float(getattr(args, "peis_sanity_neg_im_deadband_ohm", 100.0))
    max_re_to_median_ratio_limit = float(getattr(args, "peis_sanity_max_re_to_median_ratio", 12.0))
    if finite_count < min_points:
        reasons.append(f"too_few_peis_points:{finite_count}")

    re_values = [abs(r[1]) for r in rows if abs(r[1]) > 0]
    neg_im_values = [r[2] for r in rows]
    nonpositive_re_count = sum(1 for _, rez, _ in rows if rez <= 0)
    if nonpositive_re_count:
        reasons.append(f"nonpositive_re_count:{nonpositive_re_count}")

    adjacent_ratios: list[float] = []
    for (_, re0, _), (_, re1, _) in zip(rows, rows[1:]):
        a = abs(re0)
        b = abs(re1)
        if a > 0 and b > 0:
            adjacent_ratios.append(max(a, b) / max(min(a, b), 1e-30))
    max_adjacent_re_ratio = max(adjacent_ratios) if adjacent_ratios else 1.0
    if max_adjacent_re_ratio > max_adjacent_re_ratio_limit:
        reasons.append(f"adjacent_re_jump:{max_adjacent_re_ratio:.3g}x")

    negative_neg_im_count = sum(1 for value in neg_im_values if value < 0)
    negative_neg_im_fraction = negative_neg_im_count / max(1, len(neg_im_values))
    if negative_neg_im_fraction > max_negative_neg_im_fraction:
        reasons.append(f"negative_neg_im_fraction:{negative_neg_im_fraction:.3g}")

    signs = []
    for value in neg_im_values:
        if abs(value) <= neg_im_deadband_ohm:
            continue
        signs.append(1 if value > 0 else -1)
    sign_flips = sum(1 for left, right in zip(signs, signs[1:]) if left != right)
    if sign_flips > max_neg_im_sign_flips:
        reasons.append(f"neg_im_sign_flips:{sign_flips}")

    median_re = None
    max_re_to_median = None
    if re_values:
        sorted_re = sorted(re_values)
        median_re = sorted_re[len(sorted_re) // 2]
        if median_re > 0:
            max_re_to_median = max(re_values) / median_re
            if max_re_to_median > max_re_to_median_ratio_limit:
                reasons.append(f"re_outlier_to_median:{max_re_to_median:.3g}x")

    result = {
        "status": "passed" if not reasons else "failed",
        "reason": "ok" if not reasons else ";".join(reasons),
        "peis_txt": str(peis_path),
        "finite_points": finite_count,
        "nonpositive_re_count": nonpositive_re_count,
        "max_adjacent_re_ratio": max_adjacent_re_ratio,
        "negative_neg_im_count": negative_neg_im_count,
        "negative_neg_im_fraction": negative_neg_im_fraction,
        "neg_im_sign_flips": sign_flips,
        "median_abs_re_ohm": median_re,
        "max_re_to_median_ratio": max_re_to_median,
        "thresholds": {
            "min_points": min_points,
            "max_adjacent_re_ratio": max_adjacent_re_ratio_limit,
            "max_negative_neg_im_fraction": max_negative_neg_im_fraction,
            "max_neg_im_sign_flips": max_neg_im_sign_flips,
            "max_re_to_median_ratio": max_re_to_median_ratio_limit,
        },
    }
    _write_json(run_dir / "peis_sanity.json", result)
    return result


def _public_postprocess_jobs(jobs: list[dict]) -> list[dict]:
    public = []
    for job in jobs:
        public.append({k: v for k, v in job.items() if k != "proc"})
    return public


def _launch_postprocess(args, campaign_dir: Path, row: dict, jobs: list[dict]) -> None:
    if not bool(args.defer_postprocess) or not bool(args.async_postprocess):
        return
    if not str(row.get("status", "")).startswith("complete"):
        return
    run_dir = Path(str(row.get("run_dir", "")))
    if not run_dir.exists():
        return
    existing = run_dir / "full_arc_summary.json"
    if existing.exists():
        summary = _read_json(existing)
        row["full_arc_score"] = summary.get("fit_score")
        row["full_arc_png"] = summary.get("full_arc_png")
        row["postprocess_status"] = "already_complete"
        return
    stdout_path = run_dir / "full_arc_stdout.log"
    stderr_path = run_dir / "full_arc_stderr.log"
    cmd = [str(Path(args.python)), str(SCRIPT_DIR / "make_olecom_full_arc_from_run.py"), str(run_dir)]
    out = stdout_path.open("w", encoding="utf-8")
    err = stderr_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(PROJECT_DIR), stdout=out, stderr=err, text=True)
    out.close()
    err.close()
    job = {
        "label": row.get("label"),
        "run_dir": str(run_dir),
        "pid": proc.pid,
        "status": "running",
        "started": datetime.now().isoformat(timespec="seconds"),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "proc": proc,
    }
    jobs.append(job)
    row["postprocess_status"] = "running"
    _write_json(campaign_dir / "postprocess_jobs.json", _public_postprocess_jobs(jobs))


def _run_postprocess_for_row(args, row: dict) -> dict:
    run_dir = Path(str(row.get("run_dir", "")))
    event = {
        "label": row.get("label"),
        "run_dir": str(run_dir),
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    if not str(row.get("status", "")).startswith("complete"):
        event["status"] = "skipped_incomplete_row"
        return event
    if not run_dir.exists():
        event["status"] = "skipped_missing_run_dir"
        row["postprocess_status"] = event["status"]
        return event
    existing = run_dir / "full_arc_summary.json"
    if existing.exists():
        summary = _read_json(existing)
        row["full_arc_score"] = summary.get("fit_score")
        row["full_arc_png"] = summary.get("full_arc_png")
        row["postprocess_status"] = "already_complete"
        event["status"] = "already_complete"
        event["full_arc_png"] = row.get("full_arc_png")
        event["fit_score"] = row.get("full_arc_score")
        return event
    stdout_path = run_dir / "full_arc_stdout.log"
    stderr_path = run_dir / "full_arc_stderr.log"
    cmd = [str(Path(args.python)), str(SCRIPT_DIR / "make_olecom_full_arc_from_run.py"), str(run_dir)]
    try:
        with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
            proc = subprocess.run(
                cmd,
                cwd=str(PROJECT_DIR),
                stdout=out,
                stderr=err,
                text=True,
                timeout=float(args.full_arc_timeout_s),
            )
        event["returncode"] = int(proc.returncode)
        if proc.returncode == 0:
            summary = _read_json(run_dir / "full_arc_summary.json")
            row["postprocess_status"] = "complete"
            row["full_arc_png"] = summary.get("full_arc_png")
            row["full_arc_score"] = summary.get("fit_score")
            event["status"] = "complete"
            event["full_arc_png"] = row.get("full_arc_png")
            event["fit_score"] = row.get("full_arc_score")
        else:
            err_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:] if stderr_path.exists() else ""
            row["postprocess_status"] = "failed"
            row["error"] = (str(row.get("error") or "") + "\npostprocess: " + err_tail).strip()
            event["status"] = "failed"
            event["error"] = err_tail
    except Exception as exc:
        row["postprocess_status"] = "failed_exception"
        row["error"] = (str(row.get("error") or "") + "\npostprocess: " + repr(exc)).strip()
        event["status"] = "failed_exception"
        event["error"] = repr(exc)
    event["finished"] = datetime.now().isoformat(timespec="seconds")
    return event


def _run_serial_postprocess_for_rows(args, campaign_dir: Path, rows: list[dict]) -> list[dict]:
    if not bool(args.defer_postprocess):
        return []
    events = []
    for row in rows:
        if not str(row.get("status", "")).startswith("complete"):
            continue
        if row.get("full_arc_png") and Path(str(row.get("full_arc_png"))).exists():
            continue
        events.append(_run_postprocess_for_row(args, row))
        _write_json(campaign_dir / "serial_postprocess_jobs.json", events)
    return events


def _poll_postprocess_jobs(campaign_dir: Path, rows: list[dict], jobs: list[dict]) -> None:
    by_run_dir = {str(row.get("run_dir")): row for row in rows}
    changed = False
    for job in jobs:
        if job.get("status") not in ("running",):
            continue
        proc = job.get("proc")
        if proc is None:
            continue
        rc = proc.poll()
        if rc is None:
            continue
        job["returncode"] = int(rc)
        job["finished"] = datetime.now().isoformat(timespec="seconds")
        run_dir = Path(str(job.get("run_dir")))
        row = by_run_dir.get(str(run_dir))
        if rc == 0:
            summary = _read_json(run_dir / "full_arc_summary.json")
            job["status"] = "complete"
            job["full_arc_png"] = summary.get("full_arc_png")
            job["fit_score"] = summary.get("fit_score")
            if row is not None:
                row["postprocess_status"] = "complete"
                row["full_arc_png"] = summary.get("full_arc_png")
                row["full_arc_score"] = summary.get("fit_score")
        else:
            err_path = Path(str(job.get("stderr", "")))
            err_tail = err_path.read_text(encoding="utf-8", errors="replace")[-4000:] if err_path.exists() else ""
            job["status"] = "failed"
            job["error"] = err_tail
            if row is not None:
                row["postprocess_status"] = "failed"
                row["error"] = (str(row.get("error") or "") + "\npostprocess: " + err_tail).strip()
        changed = True
    if changed:
        _write_json(campaign_dir / "postprocess_jobs.json", _public_postprocess_jobs(jobs))


def _wait_postprocess_jobs(campaign_dir: Path, rows: list[dict], jobs: list[dict], timeout_s: float) -> None:
    deadline = time.time() + max(1.0, float(timeout_s))
    while any(job.get("status") == "running" for job in jobs) and time.time() < deadline:
        _poll_postprocess_jobs(campaign_dir, rows, jobs)
        if any(job.get("status") == "running" for job in jobs):
            time.sleep(1.0)
    _poll_postprocess_jobs(campaign_dir, rows, jobs)


def _simulate_condition(
    args,
    campaign_dir: Path,
    bias: float,
    index: int,
    total: int,
    csv_path: Path,
    label_prefix: str = "",
    pre_s: Optional[float] = None,
    pre_category: str = "",
    attempt: int = 0,
    retry_of: str = "",
) -> dict:
    label = _condition_label(index, bias, label_prefix)
    run_dir = campaign_dir / label
    run_dir.mkdir(parents=True, exist_ok=True)
    pre_used = float(pre_s if pre_s is not None else args.pre_s)
    sleep_s = max(0.0, float(getattr(args, "simulate_sleep_s", 0.0)))
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "attempt": int(attempt),
        "retry_of": str(retry_of or ""),
        "bias_v": bias,
        "status": "complete_simulated",
        "recovery_status": "",
        "run_dir": str(run_dir),
        "pre_category": pre_category,
        "pre_s_used": pre_used,
        "pre_stability_detected": True,
        "pre_stability_time_s": max(1.0, min(pre_used, pre_used * 0.65)),
        "pre_stability_rel_limit": 0.02,
        "pre_tail_shift_rel": 0.004,
        "pre_tail_rel_change_window": 0.003,
        "raw_lf_hz": 3.5,
        "applied_peis_lf_hz": min(float(args.peis_overlap_limit), max(float(args.peis_deep_limit), 0.5)),
        "scout_stop_reason": "simulated_live_saturation",
        "scout_guard_ready": True,
        "scout_guard_reason": "simulated_ready",
        "fft_ready_duration_s": float(args.min_fft_duration_s),
        "peis_points": int(max(1, args.peis_points_per_decade)),
        "peis_score": 0.0,
        "full_arc_score": 0.0,
        "postprocess_status": "simulated",
        "onepage_png": "",
        "full_arc_png": "",
        "error": "",
    }
    row["pre_learned_observation_s"] = _estimate_pre_learning_observation(row, args)
    summary = {
        "simulated": True,
        "bias_v": bias,
        "raw_recommended_lf_hz": row["raw_lf_hz"],
        "applied_peis_lf_hz": row["applied_peis_lf_hz"],
        "pre_stability": {
            "stable_detected": True,
            "stable_time_s": row["pre_stability_time_s"],
            "rel_limit": row["pre_stability_rel_limit"],
            "tail_metrics": {
                "tail_mean_shift_rel": row["pre_tail_shift_rel"],
                "tail_rel_change_window": row["pre_tail_rel_change_window"],
            },
        },
        "scout_live_guard": {
            "ready": True,
            "reason": row["scout_guard_reason"],
        },
        "scout_stop_reason": row["scout_stop_reason"],
        "fit_score": row["peis_score"],
        "peis_points": row["peis_points"],
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    full_summary = {
        "simulated": True,
        "fit_score": row["full_arc_score"],
        "full_arc_png": "",
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "full_arc_summary.json", full_summary)
    _write_json(
        campaign_dir / "campaign_status.json",
        {
            "stage": "running_condition_simulated",
            "condition_index": index,
            "condition_total": total,
            **row,
            "updated": datetime.now().isoformat(timespec="seconds"),
        },
    )
    if sleep_s > 0:
        time.sleep(sleep_s)
    _append_csv(csv_path, row)
    return row


def _run_condition(
    args,
    campaign_dir: Path,
    bias: float,
    index: int,
    total: int,
    csv_path: Path,
    label_prefix: str = "",
    pre_s: Optional[float] = None,
    pre_category: str = "",
    attempt: int = 0,
    retry_of: str = "",
    ctrl_holder: Optional[dict] = None,
) -> dict:
    if bool(getattr(args, "simulate", False)):
        return _simulate_condition(
            args,
            campaign_dir,
            bias,
            index,
            total,
            csv_path,
            label_prefix=label_prefix,
            pre_s=pre_s,
            pre_category=pre_category,
            attempt=attempt,
            retry_of=retry_of,
        )
    label = _condition_label(index, bias, label_prefix)
    run_dir = campaign_dir / label
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "queue_stdout.log"
    stderr_path = run_dir / "queue_stderr.log"
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "attempt": int(attempt),
        "retry_of": str(retry_of or ""),
        "bias_v": bias,
        "status": "started",
        "recovery_status": "",
        "run_dir": str(run_dir),
        "pre_category": pre_category,
        "pre_s_used": float(pre_s if pre_s is not None else args.pre_s),
        "error": "",
    }
    _annotate_eclab_counts(row, "before")
    _write_json(
        campaign_dir / "campaign_status.json",
        {
            "stage": "running_condition",
            "condition_index": index,
            "condition_total": total,
            **row,
            "updated": datetime.now().isoformat(timespec="seconds"),
        },
    )
    cmd = [
        str(Path(args.python)),
        str(SCRIPT_DIR / "run_olecom_pre_scout_live_stop_hybrid.py"),
        "--ip",
        args.ip,
        "--channel",
        str(args.channel),
        "--bias",
        str(bias),
        "--dv",
        str(args.dv),
        "--pre-s",
        str(pre_s if pre_s is not None else args.pre_s),
        "--scout-min-s",
        str(args.scout_min_s),
        "--scout-max-s",
        str(args.scout_max_s),
        "--post-s",
        str(args.post_s),
        "--dt",
        str(args.dt),
        "--bandwidth",
        str(args.bandwidth),
        "--peis-high",
        str(args.peis_high),
        "--peis-deep-limit",
        str(args.peis_deep_limit),
        "--peis-overlap-limit",
        str(args.peis_overlap_limit),
        "--peis-points-per-decade",
        str(args.peis_points_per_decade),
        "--min-fft-duration-s",
        str(args.min_fft_duration_s),
        "--scout-tail-window-s",
        str(args.scout_tail_window_s),
        "--scout-tail-rel-shift-limit",
        str(args.scout_tail_rel_shift_limit),
        "--scout-tail-rel-slope-limit",
        str(args.scout_tail_rel_slope_limit),
        "--trust-test-connection",
        "--output-dir",
        str(run_dir),
    ]
    if args.defer_postprocess:
        cmd.append("--defer-postprocess")
    if args.auto_clean_first and index == 1:
        cmd.append("--auto-clean-eclab")
        if args.auto_clean_visible_first:
            cmd.append("--auto-clean-visible-eclab")
    try:
        if bool(getattr(args, "persistent_olecom_session", True)):
            if _run_live_stop_once is None or OleComController is None:
                raise RuntimeError(_OLECOM_RECOVERY_IMPORT_ERROR or "OLE-COM live-stop helper unavailable")
            if ctrl_holder is None:
                ctrl_holder = {}
            if args.auto_clean_first and index == 1 and not ctrl_holder.get("ctrl"):
                _cleanup_eclab_processes(include_visible=bool(args.auto_clean_visible_first))
            ctrl = ctrl_holder.get("ctrl")
            if ctrl is None:
                ctrl = OleComController(
                    ip=args.ip,
                    channel=args.channel,
                    create_if_missing=False,
                    trust_test_connection=True,
                )
                ctrl.connect()
                ctrl_holder["ctrl"] = ctrl
            live_args = argparse.Namespace(
                ip=args.ip,
                channel=args.channel,
                bias=float(bias),
                dv=float(args.dv),
                pre_s=float(pre_s if pre_s is not None else args.pre_s),
                scout_min_s=float(args.scout_min_s),
                scout_max_s=float(args.scout_max_s),
                post_s=float(args.post_s),
                dt=float(args.dt),
                bandwidth=int(args.bandwidth),
                poll_s=float(getattr(args, "poll_s", 0.5)),
                live_status_interval_s=float(getattr(args, "live_status_interval_s", 5.0)),
                live_plot_interval_s=float(getattr(args, "live_plot_interval_s", 0.0)),
                disable_live_ca_png=bool(getattr(args, "disable_live_ca_png", False)),
                buffer_stable_s=float(getattr(args, "buffer_stable_s", 5.0)),
                buffer_timeout_s=float(getattr(args, "buffer_timeout_s", 180.0)),
                ca_startup_grace_s=float(getattr(args, "ca_startup_grace_s", 30.0)),
                peis_high=float(args.peis_high),
                peis_deep_limit=float(args.peis_deep_limit),
                peis_overlap_limit=float(args.peis_overlap_limit),
                peis_points_per_decade=int(args.peis_points_per_decade),
                peis_timeout_s=float(getattr(args, "peis_timeout_s", 1800.0)),
                peis_startup_grace_s=float(getattr(args, "peis_startup_grace_s", 30.0)),
                fft_pre_tail_s=float(getattr(args, "fft_pre_tail_s", 10.0)),
                fft_eval_interval_s=float(getattr(args, "fft_eval_interval_s", 5.0)),
                min_fft_duration_s=float(args.min_fft_duration_s),
                scout_tail_window_s=float(args.scout_tail_window_s),
                scout_tail_rel_shift_limit=float(args.scout_tail_rel_shift_limit),
                scout_tail_rel_slope_limit=float(args.scout_tail_rel_slope_limit),
                defer_postprocess=bool(args.defer_postprocess),
                allow_create_eclab=True,
                trust_test_connection=True,
                auto_clean_eclab=False,
                auto_clean_visible_eclab=False,
                disconnect_on_exit=False,
                output_dir=str(run_dir),
            )
            with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    _run_live_stop_once(live_args, ctrl=ctrl)
        else:
            with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
                proc = subprocess.run(cmd, cwd=str(PROJECT_DIR), stdout=out, stderr=err, text=True, timeout=args.condition_timeout_s)
            if proc.returncode != 0:
                err_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                row.update({"status": "failed", "error": f"returncode={proc.returncode}; {err_tail}"})
                _annotate_eclab_counts(row, "after")
                _append_csv(csv_path, row)
                return row
        summary = _read_json(run_dir / "summary.json")
        pre_stability = summary.get("pre_stability") or {}
        pre_tail_metrics = pre_stability.get("tail_metrics") or {}
        scout_guard = summary.get("scout_live_guard") or {}
        peis_sanity = (
            _peis_sanity_check(run_dir, summary, args)
            if bool(getattr(args, "peis_sanity_guard", True))
            else {"status": "skipped", "reason": "guard_disabled"}
        )
        full_error = ""
        full_summary = {}
        if peis_sanity.get("status") == "failed":
            row.update(
                {
                    "status": "failed_peis_sanity",
                    "pre_stability_detected": pre_stability.get("stable_detected"),
                    "pre_stability_time_s": pre_stability.get("stable_time_s"),
                    "pre_stability_rel_limit": pre_stability.get("rel_limit"),
                    "pre_tail_shift_rel": pre_tail_metrics.get("tail_mean_shift_rel"),
                    "pre_tail_rel_change_window": pre_tail_metrics.get("tail_rel_change_window"),
                    "raw_lf_hz": summary.get("raw_recommended_lf_hz"),
                    "applied_peis_lf_hz": summary.get("applied_peis_lf_hz"),
                    "scout_stop_reason": summary.get("scout_stop_reason"),
                    "scout_guard_ready": scout_guard.get("ready"),
                    "scout_guard_reason": scout_guard.get("reason"),
                    "fft_ready_duration_s": summary.get("fft_ready_duration_s"),
                    "peis_points": summary.get("peis_points"),
                    "peis_sanity_status": peis_sanity.get("status"),
                    "peis_sanity_reason": peis_sanity.get("reason"),
                    "peis_sanity_max_adjacent_re_ratio": peis_sanity.get("max_adjacent_re_ratio"),
                    "peis_sanity_negative_neg_im_fraction": peis_sanity.get("negative_neg_im_fraction"),
                    "peis_score": summary.get("fit_score"),
                    "onepage_png": summary.get("onepage_png"),
                    "error": f"PEIS sanity failed: {peis_sanity.get('reason')}",
                }
            )
            row["pre_learned_observation_s"] = _estimate_pre_learning_observation(row, args)
            return row
        if not args.defer_postprocess:
            full_cmd = [str(Path(args.python)), str(SCRIPT_DIR / "make_olecom_full_arc_from_run.py"), str(run_dir)]
            with (run_dir / "full_arc_stdout.log").open("w", encoding="utf-8") as out, (run_dir / "full_arc_stderr.log").open("w", encoding="utf-8") as err:
                full_proc = subprocess.run(full_cmd, cwd=str(PROJECT_DIR), stdout=out, stderr=err, text=True, timeout=args.full_arc_timeout_s)
            full_summary = _read_json(run_dir / "full_arc_summary.json")
            if full_proc.returncode != 0:
                full_error = (run_dir / "full_arc_stderr.log").read_text(encoding="utf-8", errors="replace")[-4000:]
        row.update(
            {
                "status": "complete_deferred_postprocess" if args.defer_postprocess else ("complete" if not full_error else "complete_full_arc_failed"),
                "pre_stability_detected": pre_stability.get("stable_detected"),
                "pre_stability_time_s": pre_stability.get("stable_time_s"),
                "pre_stability_rel_limit": pre_stability.get("rel_limit"),
                "pre_tail_shift_rel": pre_tail_metrics.get("tail_mean_shift_rel"),
                "pre_tail_rel_change_window": pre_tail_metrics.get("tail_rel_change_window"),
                "raw_lf_hz": summary.get("raw_recommended_lf_hz"),
                "applied_peis_lf_hz": summary.get("applied_peis_lf_hz"),
                "scout_stop_reason": summary.get("scout_stop_reason"),
                "scout_guard_ready": scout_guard.get("ready"),
                "scout_guard_reason": scout_guard.get("reason"),
                "fft_ready_duration_s": summary.get("fft_ready_duration_s"),
                "peis_points": summary.get("peis_points"),
                "peis_sanity_status": peis_sanity.get("status"),
                "peis_sanity_reason": peis_sanity.get("reason"),
                "peis_sanity_max_adjacent_re_ratio": peis_sanity.get("max_adjacent_re_ratio"),
                "peis_sanity_negative_neg_im_fraction": peis_sanity.get("negative_neg_im_fraction"),
                "peis_score": summary.get("fit_score"),
                "full_arc_score": full_summary.get("fit_score"),
                "onepage_png": summary.get("onepage_png"),
                "full_arc_png": full_summary.get("full_arc_png"),
                "error": full_error,
            }
        )
        row["pre_learned_observation_s"] = _estimate_pre_learning_observation(row, args)
    except Exception as exc:
        row.update({"status": "failed_exception", "error": repr(exc)})
    _annotate_eclab_counts(row, "after")
    _append_csv(csv_path, row)
    return row


def _recover_between_attempts(args, campaign_dir: Path, failed_row: dict, next_attempt: int) -> dict:
    """Try to restore EC-Lab/OLE-COM ownership before retrying the same row."""
    started = datetime.now().isoformat(timespec="seconds")
    status = {
        "stage": "recovery_between_attempts",
        "started": started,
        "failed_label": failed_row.get("label"),
        "failed_run_dir": failed_row.get("run_dir"),
        "failed_status": failed_row.get("status"),
        "failed_error_tail": str(failed_row.get("error", ""))[-1200:],
        "next_attempt": int(next_attempt),
        "processes_before": _eclab_process_snapshot(),
        "actions": [],
    }
    _write_json(campaign_dir / "recovery_status.json", status)

    if bool(getattr(args, "simulate", False)):
        status["actions"].append({"action": "simulate_recovery", "ok": True})
        status["finished"] = datetime.now().isoformat(timespec="seconds")
        status["processes_after"] = _eclab_process_snapshot()
        _write_json(campaign_dir / "recovery_status.json", status)
        return status

    if bool(args.recover_clean_eclab):
        if _cleanup_eclab_processes is None:
            status["actions"].append(
                {
                    "action": "cleanup_eclab_processes",
                    "ok": False,
                    "error": _OLECOM_RECOVERY_IMPORT_ERROR or "cleanup helper unavailable",
                }
            )
        else:
            try:
                killed = _cleanup_eclab_processes(include_visible=bool(args.recover_clean_visible_eclab))
                status["actions"].append({"action": "cleanup_eclab_processes", "ok": True, "killed_pids": killed})
            except Exception as exc:
                status["actions"].append({"action": "cleanup_eclab_processes", "ok": False, "error": repr(exc)})
        time.sleep(max(0.0, float(args.recover_settle_s)))

    if bool(getattr(args, "recover_reconnect_device", True)):
        if bool(getattr(args, "persistent_olecom_session", False)):
            status["actions"].append(
                {
                    "action": "persistent_recovery_process_reset_begin",
                    "ok": True,
                    "reason": "avoid blocking OLE-COM reconnect/disconnect calls after a failed condition",
                }
            )
            _write_json(campaign_dir / "recovery_status.json", status)
            if _cleanup_eclab_processes is None:
                status["actions"].append(
                    {
                        "action": "persistent_recovery_process_reset",
                        "ok": False,
                        "error": _OLECOM_RECOVERY_IMPORT_ERROR or "cleanup helper unavailable",
                    }
                )
            else:
                try:
                    killed = _cleanup_eclab_processes(include_visible=True)
                    status["actions"].append(
                        {
                            "action": "persistent_recovery_process_reset",
                            "ok": True,
                            "killed_pids": killed,
                        }
                    )
                except Exception as exc:
                    status["actions"].append(
                        {
                            "action": "persistent_recovery_process_reset",
                            "ok": False,
                            "error": repr(exc),
                        }
                    )
            time.sleep(max(0.5, float(args.recover_settle_s)))
            status["finished"] = datetime.now().isoformat(timespec="seconds")
            status["processes_after"] = _eclab_process_snapshot()
            ok_actions = [a for a in status["actions"] if a.get("ok")]
            status["status"] = "attempted" if ok_actions else "failed_or_unavailable"
            _write_json(campaign_dir / "recovery_status.json", status)
            return status
        if OleComController is None:
            status["actions"].append(
                {
                    "action": "disconnect_connect_device",
                    "ok": False,
                    "error": _OLECOM_RECOVERY_IMPORT_ERROR or "OLE-COM helper unavailable",
                }
            )
        else:
            status["actions"].append({"action": "recover_reconnect_begin", "ok": True})
            _write_json(campaign_dir / "recovery_status.json", status)
            if bool(getattr(args, "recover_reset_hidden_eclab", True)):
                snapshot = _eclab_process_snapshot()
                hidden = [p for p in snapshot if p.get("hidden")]
                visible = [p for p in snapshot if not p.get("hidden")]
                if hidden and not visible:
                    if _cleanup_eclab_processes is None:
                        status["actions"].append(
                            {
                                "action": "reset_hidden_eclab_before_reconnect",
                                "ok": False,
                                "error": _OLECOM_RECOVERY_IMPORT_ERROR or "cleanup helper unavailable",
                            }
                        )
                    else:
                        try:
                            killed = _cleanup_eclab_processes(include_visible=False)
                            status["actions"].append(
                                {
                                    "action": "reset_hidden_eclab_before_reconnect",
                                    "ok": True,
                                    "killed_pids": killed,
                                }
                            )
                            time.sleep(max(0.5, float(args.recover_settle_s)))
                        except Exception as exc:
                            status["actions"].append(
                                {
                                    "action": "reset_hidden_eclab_before_reconnect",
                                    "ok": False,
                                    "error": repr(exc),
                                }
                            )
                    _write_json(campaign_dir / "recovery_status.json", status)
            ctrl = OleComController(
                ip=args.ip,
                channel=args.channel,
                create_if_missing=False,
                trust_test_connection=True,
            )
            try:
                ctrl.connect()
                try:
                    if ctrl.is_running():
                        ctrl.stop()
                        ctrl.wait_until_stopped(timeout_s=15.0)
                except Exception:
                    pass
                if bool(getattr(args, "recover_disconnect_device", True)):
                    try:
                        ctrl.disconnect()
                        time.sleep(max(0.5, float(args.recover_settle_s)))
                    except Exception as exc:
                        status["actions"].append(
                            {"action": "disconnect_device_before_retry", "ok": False, "error": repr(exc)}
                        )
                    ctrl = OleComController(
                        ip=args.ip,
                        channel=args.channel,
                        create_if_missing=False,
                        trust_test_connection=True,
                    )
                    ctrl.connect()
                    status["actions"].append({"action": "disconnect_connect_device", "ok": True})
                else:
                    time.sleep(max(0.5, float(args.recover_settle_s)))
                    status["actions"].append({"action": "soft_reconnect_device", "ok": True})
                status["actions"].append({"action": "keep_connected_for_retry", "ok": True})
            except Exception as exc:
                status["actions"].append({"action": "disconnect_connect_device", "ok": False, "error": repr(exc)})
        time.sleep(max(0.0, float(args.recover_settle_s)))

    status["finished"] = datetime.now().isoformat(timespec="seconds")
    status["processes_after"] = _eclab_process_snapshot()
    ok_actions = [a for a in status["actions"] if a.get("ok")]
    status["status"] = "attempted" if ok_actions else "failed_or_unavailable"
    _write_json(campaign_dir / "recovery_status.json", status)
    return status


def _clear_persistent_ctrl(ctrl_holder: Optional[dict], campaign_dir: Path, stage: str) -> dict:
    """Release a shared OLE-COM controller only at safe boundaries."""
    event = {
        "stage": stage,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": "none",
    }
    if not ctrl_holder:
        event["reason"] = "no_holder"
        return event
    ctrl = ctrl_holder.get("ctrl")
    if ctrl is None:
        event["reason"] = "no_controller"
        return event
    if "before_recovery" in str(stage):
        ctrl_holder["ctrl"] = None
        event["action"] = "drop_controller_reference_before_recovery"
        event["reason"] = "hard recovery will reset EC-Lab process state before retry"
        try:
            _record_process_guard_event(campaign_dir, event)
        except Exception:
            pass
        return event
    try:
        try:
            if ctrl.is_running():
                ctrl.stop()
                ctrl.wait_until_stopped(timeout_s=20.0)
                event["stopped_running_channel"] = True
        except Exception as exc:
            event["stop_warning"] = repr(exc)
        try:
            ctrl.disconnect()
            event["action"] = "disconnect"
        finally:
            ctrl_holder["ctrl"] = None
    except Exception as exc:
        event["action"] = "disconnect_failed"
        event["error"] = repr(exc)
        ctrl_holder["ctrl"] = None
    try:
        _record_process_guard_event(campaign_dir, event)
    except Exception:
        pass
    return event


def _run_condition_with_retries(
    args,
    campaign_dir: Path,
    bias: float,
    index: int,
    total: int,
    csv_path: Path,
    label_prefix: str = "",
    pre_s: Optional[float] = None,
    pre_category: str = "",
    ctrl_holder: Optional[dict] = None,
) -> dict:
    base_label = _condition_label(index, bias, label_prefix)
    previous_attempts = []
    max_attempt = max(0, int(args.recovery_retries))
    for attempt in range(0, max_attempt + 1):
        attempt_prefix = label_prefix
        if attempt > 0:
            attempt_prefix = f"{label_prefix}_retry{attempt}" if label_prefix else f"retry{attempt}"
        row = _run_condition(
            args,
            campaign_dir,
            bias,
            index,
            total,
            csv_path,
            label_prefix=attempt_prefix,
            pre_s=pre_s,
            pre_category=pre_category,
            attempt=attempt,
            retry_of=base_label if attempt > 0 else "",
            ctrl_holder=ctrl_holder,
        )
        if previous_attempts:
            row["previous_attempts"] = previous_attempts
        if str(row.get("status", "")).startswith("complete"):
            return row
        previous_attempts.append(
            {
                "label": row.get("label"),
                "run_dir": row.get("run_dir"),
                "status": row.get("status"),
                "error_tail": str(row.get("error", ""))[-1200:],
            }
        )
        if attempt >= max_attempt:
            return row
        _clear_persistent_ctrl(ctrl_holder, campaign_dir, f"before_recovery_condition_{index:03d}_attempt_{attempt + 1}")
        recovery = _recover_between_attempts(args, campaign_dir, row, attempt + 1)
        row["recovery_status"] = recovery.get("status", "")
        _write_json(
            campaign_dir / "campaign_status.json",
            {
                "stage": "retrying_condition_after_recovery",
                "condition_index": index,
                "condition_total": total,
                "bias_v": bias,
                "failed_row": row,
                "next_attempt": attempt + 1,
                "recovery": recovery,
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OLE-COM live-stop bias validation and optional repeat monitoring.")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--biases", default="0.1,0,-0.1")
    parser.add_argument("--dv", type=float, default=0.03)
    parser.add_argument("--pre-s", type=float, default=60.0)
    parser.add_argument("--first-pre-s", type=float, default=None, help="Pre-hold default for the first condition at a site before any learning exists.")
    parser.add_argument("--subsequent-pre-s", type=float, default=None, help="Pre-hold default for later bias rows before same-category learning exists.")
    parser.add_argument("--repeat-pre-s", type=float, default=None, help="Pre-hold default for repeated same-bias rows before repeat learning exists.")
    parser.add_argument("--pre-min-s", type=float, default=30.0)
    parser.add_argument("--pre-max-s", type=float, default=300.0)
    parser.add_argument("--pre-margin-s", type=float, default=15.0)
    parser.add_argument(
        "--pre-margin-mode",
        choices=["add", "multiply"],
        default="multiply",
        help="How to convert learned pre stability time into the next pre hold. Default multiply uses --pre-margin-factor.",
    )
    parser.add_argument(
        "--pre-margin-factor",
        type=float,
        default=1.3,
        help="Multiplier for learned pre stability time when --pre-margin-mode=multiply.",
    )
    parser.add_argument("--pre-learning-mode", choices=["last", "percentile"], default="last")
    parser.add_argument("--pre-learning-percentile", type=float, default=80.0)
    parser.add_argument("--pre-learning-max-points", type=int, default=8)
    _add_bool_arg(parser, "--adaptive-pre", default=True)
    _add_bool_arg(
        parser,
        "--learn-pre-from-unstable",
        default=False,
        help_text="Deprecated safety flag. Pre learning now uses only detected stability time, never the full used pre duration.",
    )
    parser.add_argument("--scout-min-s", type=float, default=90.0)
    parser.add_argument("--scout-max-s", type=float, default=300.0)
    parser.add_argument("--post-s", type=float, default=10.0)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument("--bandwidth", type=int, default=4)
    parser.add_argument("--peis-high", type=float, default=100.0)
    parser.add_argument("--peis-deep-limit", type=float, default=0.1)
    parser.add_argument(
        "--peis-overlap-limit",
        type=float,
        default=0.1,
        help=(
            "Highest PEIS lowest-frequency cutoff allowed from FFT recommendation. "
            "If FFT recommends a higher cutoff, still measure PEIS down to this value "
            "for overlap with the FFT arc."
        ),
    )
    parser.add_argument("--peis-points-per-decade", type=int, default=10)
    _add_bool_arg(
        parser,
        "--peis-sanity-guard",
        default=True,
        help_text="Check measured PEIS immediately after saving and retry the same condition if points look physically broken.",
    )
    parser.add_argument("--peis-sanity-min-points", type=int, default=8)
    parser.add_argument("--peis-sanity-max-adjacent-re-ratio", type=float, default=8.0)
    parser.add_argument("--peis-sanity-max-negative-neg-im-fraction", type=float, default=0.25)
    parser.add_argument("--peis-sanity-max-neg-im-sign-flips", type=int, default=2)
    parser.add_argument("--peis-sanity-neg-im-deadband-ohm", type=float, default=100.0)
    parser.add_argument("--peis-sanity-max-re-to-median-ratio", type=float, default=12.0)
    parser.add_argument("--min-fft-duration-s", type=float, default=100.0)
    parser.add_argument("--scout-tail-window-s", type=float, default=20.0)
    parser.add_argument("--scout-tail-rel-shift-limit", type=float, default=0.03)
    parser.add_argument("--scout-tail-rel-slope-limit", type=float, default=0.03)
    parser.add_argument("--condition-timeout-s", type=float, default=3600.0)
    parser.add_argument("--full-arc-timeout-s", type=float, default=600.0)
    _add_bool_arg(
        parser,
        "--defer-postprocess",
        default=True,
        help_text="Defer one-page/full-arc fitting until after live measurement transitions; use --no-defer-postprocess for legacy immediate plots.",
    )
    _add_bool_arg(
        parser,
        "--async-postprocess",
        default=False,
        help_text="When postprocess is deferred, build the previous run's full-arc PNG in a background process while the next measurement runs.",
    )
    _add_bool_arg(
        parser,
        "--wait-postprocess-at-end",
        default=True,
        help_text="After all measurements finish, wait for background full-arc postprocessing so final summaries contain PNG paths/scores.",
    )
    _add_bool_arg(parser, "--auto-clean-first", default=True)
    _add_bool_arg(parser, "--auto-clean-visible-first", default=False)
    _add_bool_arg(
        parser,
        "--cleanup-stale-between-conditions",
        default=False,
        help_text="Between conditions only, clean stale hidden EC-Lab sessions if more than max-hidden-eclab-processes are present.",
    )
    parser.add_argument(
        "--max-hidden-eclab-processes",
        type=int,
        default=1,
        help="Maximum hidden EC-Lab sessions tolerated between measurements before automatic hidden-session cleanup.",
    )
    _add_bool_arg(
        parser,
        "--cleanup-hidden-eclab-after-campaign",
        default=False,
        help_text="After the campaign is complete, remove hidden EC-Lab sessions left by OLE-COM while preserving visible EC-Lab windows.",
    )
    _add_bool_arg(
        parser,
        "--resume-existing",
        default=True,
        help_text="When output-dir already has campaign_results.csv, skip completed row numbers and resume at the first failed/missing row.",
    )
    parser.add_argument("--recovery-retries", type=int, default=1, help="Retry the same condition after OLE-COM recovery.")
    _add_bool_arg(
        parser,
        "--recover-reconnect-device",
        default=True,
        help_text="Between retries, recover the OLE-COM device/channel and keep it connected for the retry.",
    )
    _add_bool_arg(
        parser,
        "--recover-disconnect-device",
        default=True,
        help_text="During retry recovery, call DisconnectDevice then ConnectDevice. The reconnected session is kept alive for the retry.",
    )
    _add_bool_arg(
        parser,
        "--recover-reset-hidden-eclab",
        default=True,
        help_text="If only hidden EC-Lab COM servers remain during recovery, terminate those stale hidden sessions before reconnecting.",
    )
    _add_bool_arg(
        parser,
        "--recover-clean-eclab",
        default=False,
        help_text="Between retries, terminate stale hidden EC-Lab processes before reconnecting. Visible EC-Lab is not killed unless recover-clean-visible-eclab is set.",
    )
    _add_bool_arg(parser, "--recover-clean-visible-eclab", default=False)
    parser.add_argument("--recover-settle-s", type=float, default=2.0)
    parser.add_argument("--simulate", action="store_true", help="Dry-run queue transitions without EC-Lab/OLE-COM hardware.")
    parser.add_argument("--simulate-sleep-s", type=float, default=0.0, help="Optional per-condition delay during --simulate.")
    parser.add_argument("--repeat-bias", type=float, default=None, help="Optional bias to repeat after the initial bias list completes.")
    parser.add_argument("--repeat-hours", type=float, default=0.0, help="Repeat duration in hours after the initial bias list completes.")
    parser.add_argument("--repeat-interval-s", type=float, default=600.0, help="Approximate repeat start-to-start interval.")
    parser.add_argument("--repeat-label-prefix", default="repeat0V")
    _add_bool_arg(
        parser,
        "--persistent-olecom-session",
        default=True,
        help_text="Reuse one OLE-COM controller across the whole bias queue; only reconnect on recovery/final cleanup.",
    )
    args = parser.parse_args()

    campaign_dir = Path(args.output_dir) if args.output_dir else RESULTS_DIR / f"olecom_three_bias_live_stop_{_stamp()}"
    campaign_dir.mkdir(parents=True, exist_ok=True)
    _write_json(campaign_dir / "campaign_settings.json", vars(args))
    biases = [float(part.strip()) for part in str(args.biases).split(",") if part.strip()]
    csv_path = campaign_dir / "campaign_results.csv"
    rows = []
    postprocess_jobs = []
    pre_learning = {
        "first_at_site": [],
        "subsequent_bias": [],
        "repeat_same_bias": [],
    }
    ctrl_holder = {} if bool(args.persistent_olecom_session) and not bool(args.simulate) else None
    resume_rows = _load_completed_resume_rows(csv_path) if bool(args.resume_existing) else {}
    for idx, bias in enumerate(biases, start=1):
        _poll_postprocess_jobs(campaign_dir, rows, postprocess_jobs)
        category = _pre_category(idx)
        if idx in resume_rows:
            row = dict(resume_rows[idx])
            row["status"] = row.get("status") or "complete_resumed"
            row["resume_existing"] = True
            rows.append(row)
            _learn_pre_s(pre_learning, row, category, args)
            _write_json(
                campaign_dir / "campaign_summary.json",
                {
                    "output_dir": str(campaign_dir),
                    "rows": rows,
                    "pre_learning": pre_learning,
                    "resume_existing": True,
                    "postprocess_jobs": _public_postprocess_jobs(postprocess_jobs),
                    "updated": datetime.now().isoformat(timespec="seconds"),
                },
            )
            continue
        pre_s = _choose_pre_s(args, pre_learning, category)
        guard_event = _guard_eclab_processes_between_conditions(args, campaign_dir, f"before_condition_{idx:03d}")
        if guard_event.get("action") == "cleanup_hidden":
            _clear_persistent_ctrl(ctrl_holder, campaign_dir, f"after_process_guard_condition_{idx:03d}")
        row = _run_condition_with_retries(
            args,
            campaign_dir,
            bias,
            idx,
            len(biases),
            csv_path,
            pre_s=pre_s,
            pre_category=category,
            ctrl_holder=ctrl_holder,
        )
        rows.append(row)
        _learn_pre_s(pre_learning, row, category, args)
        _launch_postprocess(args, campaign_dir, row, postprocess_jobs)
        _poll_postprocess_jobs(campaign_dir, rows, postprocess_jobs)
        _write_json(
            campaign_dir / "campaign_summary.json",
            {
                "output_dir": str(campaign_dir),
                "rows": rows,
                "pre_learning": pre_learning,
                "postprocess_jobs": _public_postprocess_jobs(postprocess_jobs),
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
        if not str(row.get("status", "")).startswith("complete"):
            break
    initial_passed = all(str(r.get("status", "")).startswith("complete") for r in rows)
    if initial_passed and args.repeat_bias is not None and float(args.repeat_hours) > 0:
        repeat_started = datetime.now()
        repeat_until = repeat_started + timedelta(hours=float(args.repeat_hours))
        repeat_idx = 1
        while datetime.now() < repeat_until:
            scheduled = repeat_started + timedelta(seconds=float(args.repeat_interval_s) * (repeat_idx - 1))
            wait_s = (scheduled - datetime.now()).total_seconds()
            if wait_s > 0:
                _poll_postprocess_jobs(campaign_dir, rows, postprocess_jobs)
                _write_json(
                    campaign_dir / "campaign_status.json",
                    {
                        "stage": "waiting_repeat",
                        "repeat_index": repeat_idx,
                        "repeat_bias_v": args.repeat_bias,
                        "scheduled": scheduled.isoformat(timespec="seconds"),
                        "wait_s": wait_s,
                        "repeat_until": repeat_until.isoformat(timespec="seconds"),
                        "output_dir": str(campaign_dir),
                        "rows": rows,
                        "postprocess_jobs": _public_postprocess_jobs(postprocess_jobs),
                        "updated": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                time.sleep(wait_s)
            guard_event = _guard_eclab_processes_between_conditions(args, campaign_dir, f"before_repeat_{repeat_idx:03d}")
            if guard_event.get("action") == "cleanup_hidden":
                _clear_persistent_ctrl(ctrl_holder, campaign_dir, f"after_process_guard_repeat_{repeat_idx:03d}")
            row = _run_condition_with_retries(
                args,
                campaign_dir,
                float(args.repeat_bias),
                len(rows) + 1,
                len(biases) + 1,
                csv_path,
                label_prefix=f"{args.repeat_label_prefix}{repeat_idx:03d}",
                pre_s=_choose_pre_s(args, pre_learning, "repeat_same_bias"),
                pre_category="repeat_same_bias",
                ctrl_holder=ctrl_holder,
            )
            rows.append(row)
            _learn_pre_s(pre_learning, row, "repeat_same_bias", args)
            _launch_postprocess(args, campaign_dir, row, postprocess_jobs)
            _poll_postprocess_jobs(campaign_dir, rows, postprocess_jobs)
            _write_json(
                campaign_dir / "campaign_summary.json",
                {
                    "output_dir": str(campaign_dir),
                    "repeat_started": repeat_started.isoformat(timespec="seconds"),
                    "repeat_until": repeat_until.isoformat(timespec="seconds"),
                    "rows": rows,
                    "pre_learning": pre_learning,
                    "postprocess_jobs": _public_postprocess_jobs(postprocess_jobs),
                    "updated": datetime.now().isoformat(timespec="seconds"),
                },
            )
            if not str(row.get("status", "")).startswith("complete"):
                break
            repeat_idx += 1
    _clear_persistent_ctrl(ctrl_holder, campaign_dir, "after_measurement_sequence")
    if bool(args.wait_postprocess_at_end):
        _wait_postprocess_jobs(campaign_dir, rows, postprocess_jobs, float(args.full_arc_timeout_s))
        serial_postprocess = _run_serial_postprocess_for_rows(args, campaign_dir, rows)
    else:
        serial_postprocess = []
    final_cleanup = {}
    if not bool(getattr(args, "simulate", False)) and bool(args.cleanup_hidden_eclab_after_campaign):
        before = _eclab_process_snapshot()
        final_cleanup = {
            "stage": "after_campaign",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "processes_before": before,
            "hidden_before": _hidden_eclab_count(before),
            "action": "none",
        }
        if _hidden_eclab_count(before) > 0 and _cleanup_eclab_processes is not None:
            try:
                final_cleanup["killed_pids"] = _cleanup_eclab_processes(include_visible=False)
                final_cleanup["action"] = "cleanup_hidden"
            except Exception as exc:
                final_cleanup["action"] = "cleanup_hidden_failed"
                final_cleanup["error"] = repr(exc)
        final_cleanup["processes_after"] = _eclab_process_snapshot()
        final_cleanup["hidden_after"] = _hidden_eclab_count(final_cleanup["processes_after"])
        _record_process_guard_event(campaign_dir, final_cleanup)
    _write_json(
        campaign_dir / "campaign_status.json",
        {
            "stage": "complete" if all(str(r.get("status", "")).startswith("complete") for r in rows) else "stopped_on_failure",
            "output_dir": str(campaign_dir),
            "rows": rows,
            "pre_learning": pre_learning,
            "postprocess_jobs": _public_postprocess_jobs(postprocess_jobs),
            "serial_postprocess": serial_postprocess,
            "final_process_guard": final_cleanup,
            "updated": datetime.now().isoformat(timespec="seconds"),
        },
    )
    print(json.dumps({"output_dir": str(campaign_dir), "rows": rows}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
