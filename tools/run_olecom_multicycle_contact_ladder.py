from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = PROJECT_DIR / "results"

from run_olecom_pre_scout_post_hybrid import OleComController, _cleanup_eclab_processes
from run_olecom_three_bias_live_stop_queue import _eclab_process_snapshot, _hidden_eclab_count


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_json_error": repr(exc)}


def _bool_arg(parser, name: str, *, default: bool, help_text: str = "") -> None:
    dest = name.lstrip("-").replace("-", "_")
    parser.add_argument(name, dest=dest, action="store_true", default=default, help=help_text)
    parser.add_argument(f"--no-{name.lstrip('-')}", dest=dest, action="store_false")


def _parse_optional_float(text: str):
    text = str(text).strip()
    if not text:
        return None
    return float(text)


def _value_token(value: float) -> str:
    text = f"{float(value):g}".replace("-", "m").replace("+", "p").replace(".", "p")
    return text


def _parse_gas_pairs(text: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for chunk in str(text or "").replace(",", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            left, right = chunk.split(":", 1)
        elif "/" in chunk:
            left, right = chunk.split("/", 1)
        else:
            raise ValueError(f"Gas pair must look like A:B, got {chunk!r}")
        pairs.append((float(left), float(right)))
    return pairs


def _build_group_plan(args) -> list[dict]:
    gas_pairs = _parse_gas_pairs(args.gas_pairs)
    if not gas_pairs:
        return [
            {
                "cycle_index": idx,
                "site_index": idx,
                "site_label": f"site{idx:02d}",
                "gas_index": None,
                "gas_a": None,
                "gas_b": None,
                "gas_key": None,
                "suffix": f"cycle{idx:02d}_site{idx:02d}",
                "fake_gas_site": False,
            }
            for idx in range(1, int(args.cycles) + 1)
        ]
    site_count = int(args.site_count or args.cycles or 1)
    plan: list[dict] = []
    cycle_index = 0
    for gas_index, (gas_a, gas_b) in enumerate(gas_pairs, start=1):
        gas_key = f"A{_value_token(gas_a)}_B{_value_token(gas_b)}"
        for site_index in range(1, site_count + 1):
            cycle_index += 1
            site_label = f"E{site_index}"
            plan.append(
                {
                    "cycle_index": cycle_index,
                    "site_index": site_index,
                    "site_label": site_label,
                    "gas_index": gas_index,
                    "gas_a": gas_a,
                    "gas_b": gas_b,
                    "gas_key": gas_key,
                    "suffix": f"gas{gas_index:02d}_{gas_key}_site{site_index:02d}_{site_label}",
                    "fake_gas_site": True,
                }
            )
    return plan


def _choose_group_first_pre_s(
    args,
    group: dict,
    memory_by_gas: dict,
    previous_gas_memory: dict | None,
) -> tuple[float, dict]:
    gas_key = group.get("gas_key")
    site_index = int(group.get("site_index") or group.get("cycle_index") or 1)
    gas_memory = memory_by_gas.get(gas_key, {}) if gas_key is not None else {}
    info = {
        "source": "default_first_pre_s",
        "site_index": site_index,
        "gas_key": gas_key,
        "learned_observation_s": None,
        "factor": None,
        "first_pre_s": float(args.first_pre_s),
    }

    same_site = gas_memory.get(site_index) or {}
    learned = same_site.get("last_pre_observation_s")
    if learned is not None:
        factor = float(args.site_revisit_factor)
        value = float(learned) * factor
        value = max(float(args.pre_min_s), min(float(args.pre_max_s), value))
        info.update(
            {
                "source": "same_gas_same_site_last_stability_x_factor",
                "learned_observation_s": float(learned),
                "factor": factor,
                "first_pre_s": value,
            }
        )
        return value, info

    adjacent = []
    for idx in (site_index - 1, site_index + 1):
        mem = gas_memory.get(idx) or {}
        obs = mem.get("first_pre_observation_s")
        if obs is not None:
            adjacent.append(float(obs))
    if adjacent:
        learned = max(adjacent)
        factor = float(args.adjacent_first_buffer_factor)
        value = learned * factor
        value = max(float(args.pre_min_s), min(float(args.pre_max_s), value))
        info.update(
            {
                "source": "same_gas_adjacent_site_first_stability_x_factor",
                "learned_observation_s": learned,
                "factor": factor,
                "first_pre_s": value,
            }
        )
        return value, info

    fallback = []
    previous_gas_memory = previous_gas_memory or {}
    for idx in (site_index, site_index - 1, site_index + 1):
        mem = previous_gas_memory.get(idx) or {}
        obs = mem.get("first_pre_observation_s") or mem.get("last_pre_observation_s")
        if obs is not None:
            fallback.append(float(obs))
    if fallback:
        learned = max(fallback)
        factor = float(args.gas_change_pre_factor)
        value = learned * factor
        value = max(float(args.pre_min_s), min(float(args.pre_max_s), value))
        info.update(
            {
                "source": "previous_gas_same_or_adjacent_site_stability_x_factor",
                "learned_observation_s": learned,
                "factor": factor,
                "first_pre_s": value,
            }
        )
        return value, info

    return float(args.first_pre_s), info


def _confirm_contact(args, cycle_dir: Path, cycle_index: int) -> dict:
    """Read OCV only before a ladder. Never call this while a measurement is active."""
    started = datetime.now().isoformat(timespec="seconds")
    samples = []
    status = {
        "stage": "contact_check",
        "cycle_index": int(cycle_index),
        "started": started,
        "confirm_s": float(args.contact_confirm_s),
        "poll_s": float(args.contact_poll_s),
        "max_abs_ocv_v": args.contact_max_abs_ocv_v,
        "max_span_v": args.contact_max_span_v,
        "samples": samples,
        "processes_before": _eclab_process_snapshot(),
    }
    _write_json(cycle_dir / "contact_check_status.json", status)
    ctrl = OleComController(
        ip=args.ip,
        channel=args.channel,
        create_if_missing=False,
        trust_test_connection=True,
    )
    try:
        ctrl.connect()
        deadline = time.perf_counter() + max(0.0, float(args.contact_confirm_s))
        while time.perf_counter() <= deadline or len(samples) == 0:
            try:
                value = float(ctrl.get_ocv())
                ok = math.isfinite(value)
                error = ""
            except Exception as exc:
                value = None
                ok = False
                error = repr(exc)
            samples.append(
                {
                    "t_s": round(time.perf_counter(), 3),
                    "ocv_v": value,
                    "ok": ok,
                    "error": error,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
            )
            status["samples"] = samples
            _write_json(cycle_dir / "contact_check_status.json", status)
            if time.perf_counter() > deadline:
                break
            time.sleep(max(0.1, float(args.contact_poll_s)))
    finally:
        try:
            ctrl.disconnect()
        except Exception:
            pass
    values = [float(s["ocv_v"]) for s in samples if s.get("ok") and s.get("ocv_v") is not None]
    status["processes_after"] = _eclab_process_snapshot()
    status["hidden_after"] = _hidden_eclab_count(status["processes_after"])
    if not values:
        status["status"] = "failed"
        status["reason"] = "no_valid_ocv_samples"
    else:
        span = max(values) - min(values)
        max_abs = max(abs(v) for v in values)
        status["mean_ocv_v"] = sum(values) / len(values)
        status["span_v"] = span
        status["max_abs_ocv_v_observed"] = max_abs
        if args.contact_max_abs_ocv_v is not None and max_abs > float(args.contact_max_abs_ocv_v):
            status["status"] = "failed"
            status["reason"] = "abs_ocv_outside_threshold"
        elif args.contact_max_span_v is not None and span > float(args.contact_max_span_v):
            status["status"] = "failed"
            status["reason"] = "ocv_span_unstable"
        else:
            status["status"] = "passed"
            status["reason"] = "ocv_confirmed_between_measurements"
    status["finished"] = datetime.now().isoformat(timespec="seconds")
    _write_json(cycle_dir / "contact_check_status.json", status)
    return status


def _run_ladder(args, cycle_dir: Path, cycle_index: int, attempt: int, first_pre_s: float) -> dict:
    cmd = [
        str(Path(args.python)),
        str(SCRIPT_DIR / "run_olecom_three_bias_live_stop_queue.py"),
        "--output-dir",
        str(cycle_dir),
        "--python",
        str(Path(args.python)),
        "--ip",
        args.ip,
        "--channel",
        str(args.channel),
        "--biases",
        args.biases,
        "--dv",
        str(args.dv),
        "--first-pre-s",
        str(first_pre_s),
        "--subsequent-pre-s",
        str(args.subsequent_pre_s),
        "--pre-min-s",
        str(args.pre_min_s),
        "--pre-max-s",
        str(args.pre_max_s),
        "--pre-margin-s",
        str(args.pre_margin_s),
        "--pre-margin-mode",
        str(args.pre_margin_mode),
        "--pre-margin-factor",
        str(args.pre_margin_factor),
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
        "--condition-timeout-s",
        str(args.condition_timeout_s),
        "--full-arc-timeout-s",
        str(args.full_arc_timeout_s),
        "--recovery-retries",
        str(args.condition_recovery_retries),
        "--max-hidden-eclab-processes",
        str(args.max_hidden_eclab_processes),
        "--persistent-olecom-session",
        "--no-async-postprocess",
        "--wait-postprocess-at-end",
        "--auto-clean-first",
        "--no-auto-clean-visible-first",
        "--cleanup-stale-between-conditions",
        "--recover-clean-eclab",
        "--no-recover-clean-visible-eclab",
        "--recover-reconnect-device",
        "--cleanup-hidden-eclab-after-campaign",
        "--no-resume-existing",
    ]
    stdout_path = cycle_dir / "ladder_stdout.log"
    stderr_path = cycle_dir / "ladder_stderr.log"
    event = {
        "stage": "ladder",
        "cycle_index": int(cycle_index),
        "attempt": int(attempt),
        "first_pre_s": float(first_pre_s),
        "started": datetime.now().isoformat(timespec="seconds"),
        "cmd": cmd,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "processes_before": _eclab_process_snapshot(),
    }
    _write_json(cycle_dir / "ladder_launch_status.json", event)
    try:
        with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
            proc = subprocess.run(
                cmd,
                cwd=str(PROJECT_DIR),
                stdout=out,
                stderr=err,
                text=True,
                timeout=float(args.ladder_timeout_s),
            )
        event["returncode"] = int(proc.returncode)
    except Exception as exc:
        event["returncode"] = -999
        event["error"] = repr(exc)
    summary = _read_json(cycle_dir / "campaign_status.json")
    event["campaign_stage"] = summary.get("stage")
    event["campaign_status_path"] = str(cycle_dir / "campaign_status.json")
    event["processes_after"] = _eclab_process_snapshot()
    event["hidden_after"] = _hidden_eclab_count(event["processes_after"])
    event["finished"] = datetime.now().isoformat(timespec="seconds")
    if event.get("returncode") == 0 and summary.get("stage") == "complete":
        event["status"] = "passed"
    else:
        event["status"] = "failed"
        err_tail = ""
        if stderr_path.exists():
            err_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        event["error_tail"] = err_tail
    _write_json(cycle_dir / "ladder_launch_status.json", event)
    return event


def _learn_next_site_first_pre_s(args, cycle_dir: Path) -> tuple[float | None, dict]:
    csv_path = cycle_dir / "campaign_results.csv"
    info = {
        "source": str(csv_path),
        "status": "missing_csv",
        "next_first_pre_s": None,
    }
    if not csv_path.exists():
        return None, info
    try:
        rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8")))
    except Exception as exc:
        info["status"] = "csv_read_failed"
        info["error"] = repr(exc)
        return None, info
    first_rows = [r for r in rows if (r.get("pre_category") or "") == "first_at_site"]
    if not first_rows:
        first_rows = rows[:1]
    for row in first_rows:
        try:
            learned = float(row.get("pre_learned_observation_s") or "")
        except Exception:
            continue
        if math.isfinite(learned) and learned > 0:
            buffer_mode = str(args.adjacent_first_buffer_mode or "multiply").lower()
            if buffer_mode == "multiply":
                proposed = learned * float(args.adjacent_first_buffer_factor)
            else:
                proposed = learned + float(args.adjacent_first_buffer_s)
            proposed = max(float(args.pre_min_s), min(float(args.pre_max_s), proposed))
            info.update(
                {
                    "status": "learned_from_previous_site_first_condition",
                    "learned_observation_s": learned,
                    "adjacent_first_buffer_mode": buffer_mode,
                    "adjacent_first_buffer_s": float(args.adjacent_first_buffer_s),
                    "adjacent_first_buffer_factor": float(args.adjacent_first_buffer_factor),
                    "next_first_pre_s": proposed,
                    "row_label": row.get("label"),
                }
            )
            return proposed, info
    info["status"] = "no_valid_first_pre_learning_value"
    return None, info


def _learn_site_pre_observations(cycle_dir: Path) -> dict:
    csv_path = cycle_dir / "campaign_results.csv"
    info = {
        "source": str(csv_path),
        "status": "missing_csv",
        "first_pre_observation_s": None,
        "last_pre_observation_s": None,
    }
    if not csv_path.exists():
        return info
    try:
        rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8")))
    except Exception as exc:
        info["status"] = "csv_read_failed"
        info["error"] = repr(exc)
        return info

    first_obs = None
    last_obs = None
    first_rows = [r for r in rows if (r.get("pre_category") or "") == "first_at_site"]
    if not first_rows and rows:
        first_rows = rows[:1]

    for row in first_rows:
        try:
            value = float(row.get("pre_learned_observation_s") or row.get("pre_stability_time_s") or "")
        except Exception:
            continue
        if math.isfinite(value) and value > 0:
            first_obs = value
            break

    for row in rows:
        try:
            value = float(row.get("pre_learned_observation_s") or row.get("pre_stability_time_s") or "")
        except Exception:
            continue
        if math.isfinite(value) and value > 0:
            last_obs = value

    info.update(
        {
            "status": "learned" if first_obs is not None or last_obs is not None else "no_valid_learning_value",
            "first_pre_observation_s": first_obs,
            "last_pre_observation_s": last_obs,
        }
    )
    return info


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run repeated OLE-COM ladders with between-cycle contact checks and automatic recovery."
    )
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument(
        "--biases",
        default="0.21,0.18,0.15,0.12,0.09,0.06,0.03,0,-0.03,-0.06,-0.09,-0.12,-0.15,-0.18,-0.21",
    )
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument(
        "--gas-pairs",
        default="",
        help="Optional fake gas matrix as A:B;A:B. These values are logged only; this runner never writes MFC setpoints.",
    )
    parser.add_argument(
        "--site-count",
        type=int,
        default=0,
        help="Number of fake sites per gas pair when --gas-pairs is used. Sites are logged only; this runner never moves the motor.",
    )
    parser.add_argument("--cycle-retries", type=int, default=1)
    parser.add_argument("--dv", type=float, default=0.03)
    parser.add_argument("--first-pre-s", type=float, default=120.0)
    parser.add_argument("--subsequent-pre-s", type=float, default=60.0)
    parser.add_argument("--adjacent-first-buffer-s", type=float, default=25.0)
    parser.add_argument(
        "--adjacent-first-buffer-mode",
        choices=["add", "multiply"],
        default="multiply",
        help="How to convert previous site/condition first-pre learning into the next site first pre. Default multiply uses --adjacent-first-buffer-factor.",
    )
    parser.add_argument("--adjacent-first-buffer-factor", type=float, default=1.3)
    parser.add_argument("--site-revisit-factor", type=float, default=1.3)
    parser.add_argument("--gas-change-pre-factor", type=float, default=1.3)
    parser.add_argument("--pre-min-s", type=float, default=0.0)
    parser.add_argument("--pre-max-s", type=float, default=300.0)
    parser.add_argument("--pre-margin-s", type=float, default=15.0)
    parser.add_argument(
        "--pre-margin-mode",
        choices=["add", "multiply"],
        default="multiply",
        help="How to convert learned same-site pre stability into later pre holds. Default multiply uses --pre-margin-factor.",
    )
    parser.add_argument("--pre-margin-factor", type=float, default=1.3)
    parser.add_argument("--scout-min-s", type=float, default=100.0)
    parser.add_argument("--scout-max-s", type=float, default=300.0)
    parser.add_argument("--min-fft-duration-s", type=float, default=100.0)
    parser.add_argument("--scout-tail-window-s", type=float, default=20.0)
    parser.add_argument("--post-s", type=float, default=10.0)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument("--bandwidth", type=int, default=4)
    parser.add_argument("--peis-high", type=float, default=100.0)
    parser.add_argument("--peis-deep-limit", type=float, default=0.1)
    parser.add_argument("--peis-overlap-limit", type=float, default=0.5)
    parser.add_argument("--peis-points-per-decade", type=int, default=10)
    parser.add_argument("--condition-recovery-retries", type=int, default=2)
    parser.add_argument("--max-hidden-eclab-processes", type=int, default=1)
    parser.add_argument("--contact-confirm-s", type=float, default=10.0)
    parser.add_argument("--contact-poll-s", type=float, default=1.0)
    parser.add_argument("--contact-max-abs-ocv-v", type=_parse_optional_float, default=None)
    parser.add_argument("--contact-max-span-v", type=_parse_optional_float, default=0.2)
    parser.add_argument("--contact-fail-policy", choices=["stop", "warn_continue"], default="stop")
    parser.add_argument("--condition-timeout-s", type=float, default=3600.0)
    parser.add_argument("--full-arc-timeout-s", type=float, default=600.0)
    parser.add_argument("--ladder-timeout-s", type=float, default=12 * 3600.0)
    _bool_arg(parser, "--clean-start-hidden-eclab", default=False)
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else RESULTS_DIR / f"olecom_multicycle_contact_ladder_{_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "settings.json", vars(args))

    rows = []
    group_plan = _build_group_plan(args)
    _write_json(out_dir / "fake_gas_site_plan.json", {"groups": group_plan})
    next_first_pre_s = float(args.first_pre_s)
    memory_by_gas: dict = {}
    previous_gas_memory: dict | None = None
    last_gas_key = None
    if args.clean_start_hidden_eclab:
        start_processes = _eclab_process_snapshot()
        if _hidden_eclab_count(start_processes) > 0:
            killed = _cleanup_eclab_processes(include_visible=False)
        else:
            killed = []
        _write_json(
            out_dir / "startup_cleanup.json",
            {
                "processes_before": start_processes,
                "killed_pids": killed,
                "processes_after": _eclab_process_snapshot(),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            },
        )

    for group in group_plan:
        cycle_index = int(group["cycle_index"])
        if group.get("fake_gas_site"):
            if group.get("gas_key") != last_gas_key:
                previous_gas_memory = memory_by_gas.get(last_gas_key) if last_gas_key is not None else None
                last_gas_key = group.get("gas_key")
            first_pre_s, first_pre_plan = _choose_group_first_pre_s(args, group, memory_by_gas, previous_gas_memory)
        else:
            first_pre_s = float(next_first_pre_s)
            first_pre_plan = {
                "source": "legacy_next_first_pre_s",
                "first_pre_s": first_pre_s,
                "learned_observation_s": None,
            }
        cycle_ok = False
        for attempt in range(0, int(args.cycle_retries) + 1):
            suffix = str(group["suffix"])
            if attempt > 0:
                suffix += f"_retry{attempt}"
            cycle_dir = out_dir / suffix
            cycle_dir.mkdir(parents=True, exist_ok=True)
            _write_json(
                cycle_dir / "fake_gas_site_state.json",
                {
                    "group": group,
                    "first_pre_plan": first_pre_plan,
                    "note": "Gas and motor/site changes are simulated/logged only; no MFC or motor command is sent by this runner.",
                    "updated": datetime.now().isoformat(timespec="seconds"),
                },
            )
            status = {
                "stage": "cycle_started",
                "cycle_index": cycle_index,
                "attempt": attempt,
                "cycle_dir": str(cycle_dir),
                "group": group,
                "first_pre_plan": first_pre_plan,
                "rows": rows,
                "updated": datetime.now().isoformat(timespec="seconds"),
            }
            _write_json(out_dir / "campaign_status.json", status)
            contact = _confirm_contact(args, cycle_dir, cycle_index)
            if contact.get("status") != "passed" and args.contact_fail_policy == "stop":
                row = {
                    "cycle_index": cycle_index,
                    "attempt": attempt,
                    "cycle_dir": str(cycle_dir),
                    "group": group,
                    "first_pre_plan": first_pre_plan,
                    "status": "failed_contact",
                    "contact": contact,
                }
                rows.append(row)
                if attempt < int(args.cycle_retries):
                    continue
                _write_json(out_dir / "campaign_status.json", {**status, "stage": "stopped_on_contact_failure", "rows": rows})
                _write_json(out_dir / "campaign_summary.json", {"status": "failed", "rows": rows})
                return 2
            ladder = _run_ladder(args, cycle_dir, cycle_index, attempt, first_pre_s)
            learned_next_first_pre_s, pre_learning = _learn_next_site_first_pre_s(args, cycle_dir)
            site_observations = _learn_site_pre_observations(cycle_dir)
            if group.get("fake_gas_site") and ladder.get("status") == "passed":
                gas_key = group.get("gas_key")
                site_index = int(group.get("site_index") or cycle_index)
                memory_by_gas.setdefault(gas_key, {}).setdefault(site_index, {})
                if site_observations.get("first_pre_observation_s") is not None:
                    memory_by_gas[gas_key][site_index]["first_pre_observation_s"] = float(
                        site_observations["first_pre_observation_s"]
                    )
                if site_observations.get("last_pre_observation_s") is not None:
                    memory_by_gas[gas_key][site_index]["last_pre_observation_s"] = float(
                        site_observations["last_pre_observation_s"]
                    )
            row = {
                "cycle_index": cycle_index,
                "attempt": attempt,
                "cycle_dir": str(cycle_dir),
                "group": group,
                "first_pre_plan": first_pre_plan,
                "status": "passed" if ladder.get("status") == "passed" else "failed_ladder",
                "contact": contact,
                "ladder": ladder,
                "site_first_pre_learning": pre_learning,
                "site_pre_observations": site_observations,
            }
            rows.append(row)
            _write_json(
                out_dir / "campaign_status.json",
                {
                    "stage": "cycle_complete" if ladder.get("status") == "passed" else "cycle_failed",
                    "cycle_index": cycle_index,
                    "attempt": attempt,
                    "group": group,
                    "first_pre_plan": first_pre_plan,
                    "memory_by_gas": memory_by_gas,
                    "rows": rows,
                    "updated": datetime.now().isoformat(timespec="seconds"),
                },
            )
            if ladder.get("status") == "passed":
                if not group.get("fake_gas_site") and learned_next_first_pre_s is not None:
                    next_first_pre_s = float(learned_next_first_pre_s)
                cycle_ok = True
                break
        if not cycle_ok:
            _write_json(out_dir / "campaign_summary.json", {"status": "failed", "rows": rows})
            return 3

    summary = {
        "status": "complete",
        "output_dir": str(out_dir),
        "cycles_requested": len(group_plan),
        "group_plan": group_plan,
        "memory_by_gas": memory_by_gas,
        "rows": rows,
        "processes_final": _eclab_process_snapshot(),
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(out_dir / "campaign_status.json", {"stage": "complete", **summary})
    _write_json(out_dir / "campaign_summary.json", summary)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
