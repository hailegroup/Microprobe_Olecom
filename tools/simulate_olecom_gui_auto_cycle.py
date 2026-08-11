#!/usr/bin/env python
"""Dry-run the intended OLE-COM GUI automation cycle.

This does not touch EC-Lab, the potentiostat, motor, MFC, or furnace. It
simulates the orchestration layer we want the GUI to perform:

1. Move to a site/electrode.
2. Search contact by OCV with the configured Z strategy.
3. Run an OLE-COM live-stop bias ladder through the queue in --simulate mode.
4. Move to the next site and prove the sequence can restart cleanly.
"""

from __future__ import print_function

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = PROJECT_DIR / "results"
QUEUE_SCRIPT = SCRIPT_DIR / "run_olecom_three_bias_live_stop_queue.py"


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _append_csv(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    fields = [
        "timestamp",
        "event",
        "site_index",
        "site_label",
        "x_mm",
        "y_mm",
        "seed_z_mm",
        "contact_z_mm",
        "measure_z_mm",
        "detail",
    ]
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fields})


def _simulate_contact(site, args, events_csv):
    seed_z = float(site["z_mm"])
    start_z = seed_z - float(args.contact_start_offset_mm)
    contact_z = seed_z - min(0.06 + 0.01 * int(site["index"]), float(args.contact_max_beyond_seed_mm))
    measure_z = contact_z + float(args.contact_engage_mm)
    rows = [
        ("move_above_seed", start_z, "safe approach start above seed"),
        ("approach_step", start_z + float(args.contact_step_mm), "no contact yet"),
        ("approach_step", contact_z, "OCV entered threshold"),
        ("confirm_contact", contact_z, "OCV stayed within threshold for confirm window"),
        ("engage", measure_z, "final engage after contact"),
    ]
    for event, z_value, detail in rows:
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "site_index": site["index"],
            "site_label": site["label"],
            "x_mm": site["x_mm"],
            "y_mm": site["y_mm"],
            "seed_z_mm": seed_z,
            "contact_z_mm": contact_z,
            "measure_z_mm": measure_z,
            "detail": "%s; z=%.4f" % (detail, z_value),
        }
        _append_csv(events_csv, payload)
    return contact_z, measure_z


def _run_queue_for_site(site, output_dir, args):
    cmd = [
        sys.executable,
        str(QUEUE_SCRIPT),
        "--output-dir",
        str(output_dir),
        "--ip",
        str(args.ip),
        "--channel",
        str(args.channel),
        "--biases",
        str(args.biases),
        "--dv",
        str(args.dv),
        "--pre-s",
        str(args.pre_s),
        "--first-pre-s",
        str(args.first_pre_s),
        "--subsequent-pre-s",
        str(args.subsequent_pre_s),
        "--pre-min-s",
        str(args.pre_min_s),
        "--pre-max-s",
        str(args.pre_max_s),
        "--pre-margin-s",
        str(args.pre_margin_s),
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
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    (output_dir / "simulate_queue_stdout.log").write_text(proc.stdout, encoding="utf-8", errors="replace")
    (output_dir / "simulate_queue_stderr.log").write_text(proc.stderr, encoding="utf-8", errors="replace")
    return {
        "site_index": site["index"],
        "site_label": site["label"],
        "returncode": proc.returncode,
        "elapsed_s": time.time() - started,
        "output_dir": str(output_dir),
    }


def main():
    parser = argparse.ArgumentParser(description="Simulate GUI OLE-COM auto cycle including contact and ladder restart.")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--biases", default="0.2,0.1,0,-0.1,-0.2")
    parser.add_argument("--dv", type=float, default=0.03)
    parser.add_argument("--pre-s", type=float, default=60.0)
    parser.add_argument("--first-pre-s", type=float, default=180.0)
    parser.add_argument("--subsequent-pre-s", type=float, default=45.0)
    parser.add_argument("--pre-min-s", type=float, default=0.0)
    parser.add_argument("--pre-max-s", type=float, default=300.0)
    parser.add_argument("--pre-margin-s", type=float, default=15.0)
    parser.add_argument("--scout-min-s", type=float, default=100.0)
    parser.add_argument("--scout-max-s", type=float, default=300.0)
    parser.add_argument("--post-s", type=float, default=10.0)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument("--bandwidth", type=int, default=4)
    parser.add_argument("--peis-high", type=float, default=100.0)
    parser.add_argument("--peis-deep-limit", type=float, default=0.1)
    parser.add_argument("--peis-overlap-limit", type=float, default=0.1)
    parser.add_argument("--peis-points-per-decade", type=int, default=10)
    parser.add_argument("--min-fft-duration-s", type=float, default=100.0)
    parser.add_argument("--simulate-sleep-s", type=float, default=0.0)
    parser.add_argument("--contact-start-offset-mm", type=float, default=0.10)
    parser.add_argument("--contact-step-mm", type=float, default=0.01)
    parser.add_argument("--contact-max-beyond-seed-mm", type=float, default=0.20)
    parser.add_argument("--contact-engage-mm", type=float, default=0.05)
    parser.add_argument("--contact-confirm-s", type=float, default=10.0)
    parser.add_argument("--sites", type=int, default=2)
    args = parser.parse_args()

    out = Path(args.output_dir) if args.output_dir else RESULTS_DIR / ("olecom_gui_auto_cycle_sim_" + _stamp())
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "simulation_settings.json", vars(args))
    events_csv = out / "simulation_events.csv"

    site_results = []
    for site_idx in range(1, int(args.sites) + 1):
        site = {
            "index": site_idx,
            "label": "E%d" % site_idx,
            "x_mm": 3.0 + (site_idx - 1) * 0.5,
            "y_mm": 10.0,
            "z_mm": -2.00 - (site_idx - 1) * 0.02,
        }
        contact_z, measure_z = _simulate_contact(site, args, events_csv)
        site_dir = out / ("site_%02d_%s" % (site_idx, site["label"]))
        result = _run_queue_for_site(site, site_dir, args)
        result.update(
            {
                "x_mm": site["x_mm"],
                "y_mm": site["y_mm"],
                "seed_z_mm": site["z_mm"],
                "contact_z_mm": contact_z,
                "measure_z_mm": measure_z,
            }
        )
        site_results.append(result)
        _write_json(
            out / "simulation_status.json",
            {
                "stage": "running" if site_idx < int(args.sites) else "complete",
                "completed_sites": site_results,
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
        if result["returncode"] != 0:
            break

    summary = {
        "stage": "complete" if all(r["returncode"] == 0 for r in site_results) else "failed",
        "output_dir": str(out),
        "site_results": site_results,
        "events_csv": str(events_csv),
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(out / "simulation_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["stage"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
