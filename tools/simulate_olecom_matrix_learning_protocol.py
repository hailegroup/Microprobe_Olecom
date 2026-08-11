#!/usr/bin/env python
"""Simulate the overnight OLE-COM matrix scheduler.

This script deliberately does not touch EC-Lab, motors, MFCs, or temperature.
It validates the orchestration policy for:

* gas condition -> site/electrode -> voltage ladder ordering,
* contact check before each new site/gas block,
* pre-hold learning shared across adjacent/revisited electrodes,
* the same live-stop OLE-COM queue interface used by the real runner.

Use this before enabling a real 5 x 5 x 5 unattended matrix.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = PROJECT_DIR / "results"
QUEUE_SCRIPT = SCRIPT_DIR / "run_olecom_three_bias_live_stop_queue.py"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _append_csv(path: Path, row: dict, fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fields})


def _parse_float_list(text: str) -> List[float]:
    return [float(part.strip()) for part in str(text).split(",") if part.strip()]


def _parse_gas_pairs(text: str) -> List[Tuple[float, float]]:
    pairs: List[Tuple[float, float]] = []
    for token in str(text).split(";"):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError("Gas pairs must use A:B tokens, e.g. 100:0;75:25")
        a, b = token.split(":", 1)
        pairs.append((float(a.strip()), float(b.strip())))
    if not pairs:
        raise ValueError("At least one gas pair is required.")
    return pairs


def _default_sites(count: int) -> List[dict]:
    # Synthetic positions only; these are not written to real hardware.
    return [
        {
            "site_index": idx,
            "site_label": f"E{idx}",
            "x_mm": 3.2 + 0.5 * (idx - 1),
            "y_mm": 10.3,
            "seed_z_mm": -2.50,
        }
        for idx in range(1, count + 1)
    ]


def _read_sites_csv(path: Path) -> List[dict]:
    sites: List[dict] = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        for idx, row in enumerate(csv.DictReader(fh), start=1):
            label = row.get("site_label") or row.get("label") or f"E{idx}"
            sites.append(
                {
                    "site_index": int(float(row.get("site_index") or idx)),
                    "site_label": label,
                    "x_mm": float(row.get("x_mm") or row.get("X_mm")),
                    "y_mm": float(row.get("y_mm") or row.get("Y_mm")),
                    "seed_z_mm": float(row.get("seed_z_mm") or row.get("Z_mm")),
                }
            )
    if not sites:
        raise ValueError(f"No sites found in {path}")
    return sites


def _load_queue_summary(queue_dir: Path) -> dict:
    path = queue_dir / "campaign_summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": repr(exc)}


def _learned_first_observation(queue_summary: dict) -> float | None:
    rows = queue_summary.get("rows") or []
    for row in rows:
        if str(row.get("pre_category")) == "first_at_site":
            value = row.get("pre_learned_observation_s") or row.get("pre_stability_time_s")
            if value not in (None, ""):
                return float(value)
    learning = queue_summary.get("pre_learning") or {}
    vals = learning.get("first_at_site") or []
    if vals:
        return float(vals[-1])
    return None


def _learned_last_observation(queue_summary: dict) -> float | None:
    rows = queue_summary.get("rows") or []
    values: List[float] = []
    for row in rows:
        value = row.get("pre_learned_observation_s") or row.get("pre_stability_time_s")
        if value not in (None, ""):
            values.append(float(value))
    return values[-1] if values else None


def _choose_first_pre_s(
    site: dict,
    site_memory: Dict[int, dict],
    args,
    fallback_site_memory: Dict[int, dict] | None = None,
) -> tuple[float, str]:
    site_idx = int(site["site_index"])
    if site_idx in site_memory and site_memory[site_idx].get("last_pre_observation_s"):
        value = float(site_memory[site_idx]["last_pre_observation_s"]) * float(args.site_revisit_factor)
        return min(value, float(args.pre_max_s)), "same_site_previous_x_factor"

    adjacent_candidates = []
    for adj in (site_idx - 1, site_idx + 1):
        obs = site_memory.get(adj, {}).get("first_pre_observation_s")
        if obs is not None:
            adjacent_candidates.append(float(obs))
    if adjacent_candidates:
        value = max(adjacent_candidates) * float(args.adjacent_first_buffer_factor)
        return min(value, float(args.pre_max_s)), "adjacent_first_pre_x_factor"

    fallback_candidates = []
    fallback_site_memory = fallback_site_memory or {}
    for idx in (site_idx, site_idx - 1, site_idx + 1):
        mem = fallback_site_memory.get(idx) or {}
        obs = mem.get("first_pre_observation_s") or mem.get("last_pre_observation_s")
        if obs is not None:
            fallback_candidates.append(float(obs))
    if fallback_candidates:
        value = max(fallback_candidates) * float(args.gas_change_pre_factor)
        return min(value, float(args.pre_max_s)), "previous_gas_first_pre_x_factor"

    return float(args.first_pre_s), "default_first_site"


def _condition_memory_key(gas_a: float, gas_b: float) -> str:
    return f"gas_A{float(gas_a):.6g}_B{float(gas_b):.6g}"


EVENT_FIELDS = [
    "timestamp",
    "event",
    "gas_index",
    "gas_a",
    "gas_b",
    "site_index",
    "site_label",
    "x_mm",
    "y_mm",
    "seed_z_mm",
    "contact_z_mm",
    "measure_z_mm",
    "detail",
]


def _simulate_gas_stabilization(
    gas_idx: int,
    gas_a: float,
    gas_b: float,
    changed: bool,
    stable_s: float,
    out_csv: Path,
) -> None:
    if not changed:
        _append_csv(
            out_csv,
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "event": "gas_stabilization_skipped",
                "gas_index": gas_idx,
                "gas_a": gas_a,
                "gas_b": gas_b,
                "detail": "Gas condition unchanged; contact/measurement can proceed without gas wait",
            },
            EVENT_FIELDS,
        )
        return

    _append_csv(
        out_csv,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": "gas_set_simulated",
            "gas_index": gas_idx,
            "gas_a": gas_a,
            "gas_b": gas_b,
            "detail": f"Gas target set before contact; stabilization target {stable_s:.1f} s",
        },
        EVENT_FIELDS,
    )
    _append_csv(
        out_csv,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": "gas_stabilization_complete_simulated",
            "gas_index": gas_idx,
            "gas_a": gas_a,
            "gas_b": gas_b,
            "detail": "Contact check is allowed only after this event",
        },
        EVENT_FIELDS,
    )


def _simulate_contact(site: dict, gas_idx: int, gas_a: float, gas_b: float, out_csv: Path) -> dict:
    seed_z = float(site["seed_z_mm"])
    contact_z = seed_z - 0.04
    measure_z = contact_z + 0.05
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event": "contact_check_simulated",
        "gas_index": gas_idx,
        "gas_a": gas_a,
        "gas_b": gas_b,
        "site_index": site["site_index"],
        "site_label": site["site_label"],
        "x_mm": site["x_mm"],
        "y_mm": site["y_mm"],
        "seed_z_mm": seed_z,
        "contact_z_mm": contact_z,
        "measure_z_mm": measure_z,
        "detail": "OCV within threshold for simulated 10 s confirm window",
    }
    _append_csv(
        out_csv,
        row,
        EVENT_FIELDS,
    )
    return row


def _run_simulated_ladder(out_dir: Path, first_pre_s: float, args) -> tuple[int, dict]:
    cmd = [
        sys.executable,
        str(QUEUE_SCRIPT),
        "--output-dir",
        str(out_dir),
        "--ip",
        str(args.ip),
        "--channel",
        str(args.channel),
        "--biases",
        str(args.biases),
        "--dv",
        str(args.dv),
        "--first-pre-s",
        str(first_pre_s),
        "--subsequent-pre-s",
        str(args.subsequent_pre_s),
        "--pre-s",
        str(args.subsequent_pre_s),
        "--pre-min-s",
        str(args.pre_min_s),
        "--pre-max-s",
        str(args.pre_max_s),
        "--pre-margin-s",
        str(args.pre_margin_s),
        "--pre-learning-percentile",
        str(args.pre_learning_percentile),
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
        "--simulate",
        "--simulate-sleep-s",
        str(args.simulate_sleep_s),
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    (out_dir / "queue_stdout.log").write_text(proc.stdout, encoding="utf-8", errors="replace")
    (out_dir / "queue_stderr.log").write_text(proc.stderr, encoding="utf-8", errors="replace")
    return proc.returncode, _load_queue_summary(out_dir)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate 5 gas x 5 site x 5 bias OLE-COM matrix learning.")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--biases", default="0.1,0.05,0,-0.05,-0.1")
    parser.add_argument("--gas-pairs", default="100:0;75:25;50:50;25:75;0:100")
    parser.add_argument("--gas-stable-s", type=float, default=600.0)
    parser.add_argument("--sites-csv", default="")
    parser.add_argument("--site-count", type=int, default=5)
    parser.add_argument("--dv", type=float, default=0.03)
    parser.add_argument("--first-pre-s", type=float, default=120.0)
    parser.add_argument("--subsequent-pre-s", type=float, default=60.0)
    parser.add_argument("--pre-min-s", type=float, default=0.0)
    parser.add_argument("--pre-max-s", type=float, default=300.0)
    parser.add_argument("--pre-margin-s", type=float, default=15.0)
    parser.add_argument("--adjacent-first-buffer-factor", type=float, default=1.3)
    parser.add_argument("--site-revisit-factor", type=float, default=1.3)
    parser.add_argument("--gas-change-pre-factor", type=float, default=1.3)
    parser.add_argument("--pre-learning-percentile", type=float, default=80.0)
    parser.add_argument("--scout-min-s", type=float, default=100.0)
    parser.add_argument("--scout-max-s", type=float, default=300.0)
    parser.add_argument("--post-s", type=float, default=10.0)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument("--bandwidth", type=int, default=4)
    parser.add_argument("--peis-high", type=float, default=100.0)
    parser.add_argument("--peis-deep-limit", type=float, default=0.1)
    parser.add_argument("--peis-overlap-limit", type=float, default=0.5)
    parser.add_argument("--peis-points-per-decade", type=int, default=10)
    parser.add_argument("--min-fft-duration-s", type=float, default=100.0)
    parser.add_argument("--simulate-sleep-s", type=float, default=0.0)
    args = parser.parse_args(list(argv) if argv is not None else None)

    out = Path(args.output_dir) if args.output_dir else RESULTS_DIR / f"olecom_matrix_learning_sim_{_stamp()}"
    out.mkdir(parents=True, exist_ok=True)
    sites = _read_sites_csv(Path(args.sites_csv)) if args.sites_csv else _default_sites(int(args.site_count))
    gas_pairs = _parse_gas_pairs(args.gas_pairs)
    biases = _parse_float_list(args.biases)
    _write_json(out / "matrix_settings.json", vars(args))
    _write_json(out / "matrix_plan.json", {"sites": sites, "gas_pairs": gas_pairs, "biases": biases})

    events_csv = out / "matrix_events.csv"
    rows_csv = out / "matrix_rows.csv"
    row_fields = [
        "timestamp",
        "gas_index",
        "gas_a",
        "gas_b",
        "site_index",
        "site_label",
        "first_pre_s",
        "first_pre_source",
        "returncode",
        "queue_dir",
        "first_pre_observation_s",
        "last_pre_observation_s",
        "status",
    ]
    site_memory_by_condition: Dict[str, Dict[int, dict]] = {}
    completed = []
    prev_gas: Tuple[float, float] | None = None

    total_groups = len(gas_pairs) * len(sites)
    group_idx = 0
    previous_condition_memory: Dict[int, dict] | None = None
    for gas_idx, (gas_a, gas_b) in enumerate(gas_pairs, start=1):
        gas_changed = prev_gas != (gas_a, gas_b)
        _simulate_gas_stabilization(gas_idx, gas_a, gas_b, gas_changed, float(args.gas_stable_s), events_csv)
        prev_gas = (gas_a, gas_b)
        condition_key = _condition_memory_key(gas_a, gas_b)
        site_memory = site_memory_by_condition.setdefault(condition_key, {})
        for site in sites:
            group_idx += 1
            first_pre_s, first_pre_source = _choose_first_pre_s(
                site,
                site_memory,
                args,
                fallback_site_memory=previous_condition_memory if gas_changed else None,
            )
            _simulate_contact(site, gas_idx, gas_a, gas_b, events_csv)
            queue_dir = out / f"gas{gas_idx:02d}_A{gas_a:g}_B{gas_b:g}" / f"site{int(site['site_index']):02d}_{site['site_label']}"
            returncode, queue_summary = _run_simulated_ladder(queue_dir, first_pre_s, args)
            first_obs = _learned_first_observation(queue_summary)
            last_obs = _learned_last_observation(queue_summary)
            site_idx = int(site["site_index"])
            site_memory.setdefault(site_idx, {})
            if first_obs is not None:
                site_memory[site_idx]["first_pre_observation_s"] = first_obs
            if last_obs is not None:
                site_memory[site_idx]["last_pre_observation_s"] = last_obs
            row = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "gas_index": gas_idx,
                "gas_a": gas_a,
                "gas_b": gas_b,
                "site_index": site_idx,
                "site_label": site["site_label"],
                "first_pre_s": first_pre_s,
                "first_pre_source": first_pre_source,
                "returncode": returncode,
                "queue_dir": str(queue_dir),
                "first_pre_observation_s": first_obs,
                "last_pre_observation_s": last_obs,
                "status": "complete" if returncode == 0 else "failed",
            }
            completed.append(row)
            _append_csv(rows_csv, row, row_fields)
            _write_json(
                out / "matrix_status.json",
                {
                    "stage": "running" if group_idx < total_groups else "complete",
                    "completed_groups": group_idx,
                    "total_groups": total_groups,
                    "site_memory": site_memory,
                    "site_memory_by_condition": site_memory_by_condition,
                    "last_row": row,
                    "updated": datetime.now().isoformat(timespec="seconds"),
                },
            )
            if returncode != 0:
                _write_json(
                    out / "matrix_summary.json",
                    {
                        "stage": "failed",
                        "output_dir": str(out),
                        "rows_csv": str(rows_csv),
                        "events_csv": str(events_csv),
                        "completed": completed,
                        "site_memory": site_memory,
                        "site_memory_by_condition": site_memory_by_condition,
                        "updated": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                return returncode
        previous_condition_memory = {
            int(site_idx): dict(memory)
            for site_idx, memory in site_memory.items()
        }

    summary = {
        "stage": "complete",
        "output_dir": str(out),
        "rows_csv": str(rows_csv),
        "events_csv": str(events_csv),
        "groups": len(completed),
        "conditions": len(completed) * len(biases),
        "site_memory": site_memory_by_condition.get(_condition_memory_key(*gas_pairs[-1]), {}),
        "site_memory_by_condition": site_memory_by_condition,
        "completed": completed,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(out / "matrix_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
