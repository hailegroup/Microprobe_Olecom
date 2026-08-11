from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import cm


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = PROJECT_DIR / "results"
RUN_LOG_DIR = PROJECT_DIR / "run_logs"
REQUESTED_DEST = Path(r"C:\Users\mmq8658\Desktop\Microprobe\Convert_CP_to_EIS 1\result\260426")
FALLBACK_DEST = REQUESTED_DEST
RUNTIME_DESTINATIONS = [REQUESTED_DEST]

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
analysis_dir = PROJECT_DIR / "Analysis_Convert_CP_to_EIS"
if str(analysis_dir) not in sys.path:
    sys.path.insert(0, str(analysis_dir))

from matplotlib_compat import install_pyplot_compat  # noqa: E402
install_pyplot_compat(plt)

from cp_first_policy import recommend_peis_lf_from_cp_txt  # noqa: E402
from Analysis_Convert_CP_to_EIS import EIS_Fitting as EF  # noqa: E402
import Load_CP_Data as LD  # noqa: E402


DEFAULT_RUN_ARGS = [
    "--ip",
    "192.109.209.128",
    "--channel",
    "1",
    "--dv",
    "0.03",
    "--peis-amplitude-mv",
    "10",
    "--bandwidth",
    "BW4",
    "--dt",
    "0.1",
    "--pre-min",
    "60",
    "--pre-max",
    "900",
    "--scout-min",
    "60",
    "--scout-max",
    "1200",
    "--peis-floor",
    "0.5",
    "--peis-deep-limit",
    "0.05",
    "--peis-npts",
    "60",
    "--peis-high",
    "1000000",
    "--post-stable-buffer",
    "20",
    "--fit-model",
    "RQRQ",
]


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    print(line, flush=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def active_destinations() -> list[Path]:
    return RUNTIME_DESTINATIONS


def ensure_destinations() -> list[Path]:
    destinations = active_destinations()
    for dest in destinations:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "raw_results").mkdir(exist_ok=True)
        (dest / "reports").mkdir(exist_ok=True)
        (dest / "longterm").mkdir(exist_ok=True)
        (dest / "voltage_ladder").mkdir(exist_ok=True)
    return destinations


def sync_file_to_destinations(src: Path, relative: Path) -> None:
    for dest in ensure_destinations():
        out = dest / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)


def sync_tree_to_destinations(src_dir: Path, relative_dir: Path) -> None:
    for dest in ensure_destinations():
        out = dest / relative_dir
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(src_dir, out)


def load_summary(folder: Path) -> dict:
    return json.loads((folder / "continuous_prepost_seeded_reference_summary.json").read_text())


def wait_for_summary(folder: Path, timeout_s: float | None, log_path: Path) -> dict:
    start = time.time()
    summary_path = folder / "continuous_prepost_seeded_reference_summary.json"
    while True:
        if summary_path.exists():
            try:
                return json.loads(summary_path.read_text())
            except json.JSONDecodeError:
                time.sleep(2)
        if timeout_s is not None and time.time() - start > timeout_s:
            raise TimeoutError(f"Timed out waiting for summary in {folder}")
        stage_path = folder / "stage_marker.json"
        if stage_path.exists():
            try:
                stage = json.loads(stage_path.read_text()).get("stage")
                log(log_path, f"waiting for {folder.name}: stage={stage}")
            except Exception:
                pass
        time.sleep(20)


def newest_result_after(start_time: datetime) -> Path | None:
    candidates: list[Path] = []
    for folder in RESULTS_DIR.glob("continuous_prepost_seeded_reference_*"):
        if folder.is_dir() and folder.stat().st_ctime >= start_time.timestamp() - 5:
            candidates.append(folder)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run_measurement(
    *,
    python_exe: Path,
    bias: float,
    label: str,
    run_log_path: Path,
) -> Path:
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    start = datetime.now()
    stamp = now_stamp()
    out_log = RUN_LOG_DIR / f"{label}_{stamp}.out.log"
    err_log = RUN_LOG_DIR / f"{label}_{stamp}.err.log"
    cmd = [
        str(python_exe),
        "tools\\run_continuous_prepost_seeded_reference.py",
        "--bias",
        f"{bias:.6g}",
        *DEFAULT_RUN_ARGS,
    ]
    log(run_log_path, f"START measurement label={label} bias={bias:+.3f} cmd={' '.join(cmd)}")
    with out_log.open("w", encoding="utf-8") as out, err_log.open("w", encoding="utf-8") as err:
        proc = subprocess.run(cmd, cwd=PROJECT_DIR, stdout=out, stderr=err)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with return code {proc.returncode}; see {err_log}")
    folder = newest_result_after(start)
    if folder is None:
        raise RuntimeError(f"{label} completed but no result folder was found")
    summary = wait_for_summary(folder, timeout_s=30, log_path=run_log_path)
    log(
        run_log_path,
        f"END measurement label={label} folder={folder.name} "
        f"score={summary.get('merged_fit', {}).get('Fit quality score')}",
    )
    return folder


def _recover_merged_dataset(folder: Path, summary: dict) -> dict:
    peis_path = folder / "seeded_peis.txt"
    fft_path = folder / "combined_prepost_fft_ready.txt"
    # The live BioLogic driver writes text PEIS as [freq, Re(Z), -Im(Z)].
    # Load through the common parser so downstream code always sees
    # [freq, Re(Z), Im(Z)] and Nyquist plots consistently use -Im(Z).
    peis = np.asarray(LD.load_eis_from_path(str(peis_path)), dtype=float)
    if peis.ndim == 1:
        peis = peis.reshape(1, -1)
    cutoff = float(summary.get("merged_cutoff_hz") or np.nanmin(peis[:, 0]))
    with contextlib.redirect_stdout(io.StringIO()):
        fft_bundle = recommend_peis_lf_from_cp_txt(str(fft_path), current_in_mA=False)
    fft_f = np.asarray(fft_bundle["analysis_f"], dtype=float)
    fft_z = np.asarray(fft_bundle["analysis_z"], dtype=complex)
    keep_peis = np.isfinite(peis[:, 0]) & (peis[:, 0] > 0)
    peis_f = peis[keep_peis, 0].astype(float)
    peis_z = peis[keep_peis, 1].astype(float) + 1j * peis[keep_peis, 2].astype(float)
    keep_fft = (
        np.isfinite(fft_f)
        & np.isfinite(fft_z.real)
        & np.isfinite(fft_z.imag)
        & (fft_f > 0)
        & (fft_f < cutoff * (1.0 - 1e-9))
    )
    merged_f = np.concatenate([peis_f, fft_f[keep_fft]])
    merged_z = np.concatenate([peis_z, fft_z[keep_fft]])
    order = np.argsort(merged_f)[::-1]
    merged_f = merged_f[order]
    merged_z = merged_z[order]
    fit = summary.get("merged_fit", {})
    fit_f = np.logspace(np.log10(max(np.nanmin(merged_f), 1e-5)), np.log10(np.nanmax(merged_f)), 500)
    fit_z = EF.Z_from_fit_result(fit, fit_f)
    return {
        "peis_f": peis_f,
        "peis_z": peis_z,
        "fft_f": fft_f[keep_fft],
        "fft_z": fft_z[keep_fft],
        "merged_f": merged_f,
        "merged_z": merged_z,
        "fit_f": fit_f,
        "fit_z": fit_z,
    }


def summarize_folder(folder: Path, idx: int, phase: str, label: str) -> dict:
    summary = load_summary(folder)
    started = datetime.strptime(summary["started_at"], "%Y-%m-%d %H:%M:%S")
    ar = summary.get("analysis_result", {})
    fit = summary.get("merged_fit", {})
    rec = {
        "idx": idx,
        "phase": phase,
        "label": label,
        "folder": folder,
        "folder_name": folder.name,
        "started": started,
        "finished": summary.get("finished_at"),
        "bias": float(summary.get("settings", {}).get("bias_v", np.nan)),
        "score": float(fit.get("Fit quality score", np.nan)),
        "cost": float(fit.get("Fit cost", np.nan)),
        "data_sufficient": bool(ar.get("data_sufficient")),
        "postcheck": ar.get("postcheck_reason"),
        "reason": ar.get("reason"),
        "cp_duration": ar.get("actual_cp_duration_s"),
        "raw_lf": summary.get("seed_recommendation", {}).get("raw_recommended_lf_hz"),
        "analysis_lf": ar.get("recommended_peis_lowest_freq_hz"),
        "R0": float(fit.get("R0 (Ohm)", np.nan)),
        "R1": float(fit.get("R1 (Ohm)", np.nan)),
        "R2": float(fit.get("R2 (Ohm)", np.nan)),
        "fit_model": fit.get("Equivalent circuit") or fit.get("Fit model") or "RQRQRQ",
        "summary": summary,
    }
    rec.update(_recover_merged_dataset(folder, summary))
    return rec


def write_ledger(records: Iterable[dict], relative_path: Path) -> None:
    rows = []
    for r in records:
        rows.append(
            {
                "idx": r["idx"],
                "phase": r["phase"],
                "label": r["label"],
                "folder": r["folder_name"],
                "started": r["started"].strftime("%Y-%m-%d %H:%M:%S"),
                "finished": r["finished"],
                "bias": r["bias"],
                "score": r["score"],
                "cost": r["cost"],
                "data_sufficient": r["data_sufficient"],
                "postcheck": r["postcheck"],
                "cp_duration_s": r["cp_duration"],
                "raw_lf_hz": r["raw_lf"],
                "analysis_lf_hz": r["analysis_lf"],
                "fit_model": r.get("fit_model"),
                "R0_ohm": r["R0"],
                "R1_ohm": r["R1"],
                "R2_ohm": r["R2"],
            }
        )
    for dest in ensure_destinations():
        path = dest / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        csv_path = path.with_suffix(".csv")
        if rows:
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)


def save_folder_outputs(record: dict, relative_raw_dir: Path) -> None:
    sync_tree_to_destinations(record["folder"], relative_raw_dir / record["folder_name"])


def make_folder_onepage(record: dict, relative_path: Path, title: str) -> None:
    folder = record["folder"]
    pre_path = folder / "pre_ca_continuous.txt"
    scout_path = folder / "dv_scout_continuous.txt"
    raw_path = folder / "combined_prepost_raw.txt"
    trimmed_path = folder / "combined_prepost_trimmed.txt"
    peis = np.asarray(LD.load_eis_from_path(str(folder / "seeded_peis.txt")), dtype=float)
    if peis.ndim == 1:
        peis = peis.reshape(1, -1)
    pre = np.loadtxt(pre_path, skiprows=1) if pre_path.exists() else np.empty((0, 3))
    scout = np.loadtxt(scout_path, skiprows=1) if scout_path.exists() else np.empty((0, 3))
    raw = np.loadtxt(raw_path, skiprows=1) if raw_path.exists() else np.empty((0, 3))
    trimmed = np.loadtxt(trimmed_path, skiprows=1) if trimmed_path.exists() else np.empty((0, 3))

    fig = plt.figure(figsize=(14, 9), layout="constrained")
    gs = fig.add_gridspec(2, 3)
    ax_pre = fig.add_subplot(gs[0, 0])
    ax_scout = fig.add_subplot(gs[0, 1])
    ax_fft = fig.add_subplot(gs[0, 2])
    ax_fit = fig.add_subplot(gs[1, :2])
    ax_text = fig.add_subplot(gs[1, 2])

    if len(pre):
        ax_pre.plot(pre[:, 0], pre[:, 2] * 1e6, color="#1d3557", lw=1.1)
    ax_pre.set_title("pre-CA")
    ax_pre.set_xlabel("time / s")
    ax_pre.set_ylabel("current / uA")
    ax_pre.grid(True, alpha=0.25)

    if len(scout):
        ax_scout.plot(scout[:, 0], scout[:, 2] * 1e6, color="#2a9d8f", lw=1.1)
    ax_scout.set_title("dV scout CA")
    ax_scout.set_xlabel("time / s")
    ax_scout.set_ylabel("current / uA")
    ax_scout.grid(True, alpha=0.25)

    if len(raw):
        ax_fft.plot(raw[:, 0], raw[:, 2] * 1e6, color="0.75", lw=0.9, label="raw")
    if len(trimmed):
        ax_fft.plot(trimmed[:, 0], trimmed[:, 2] * 1e6, color="#e76f51", lw=1.1, label="FFT-used")
    ax_fft.set_title("FFT input segment")
    ax_fft.set_xlabel("time / s")
    ax_fft.set_ylabel("current / uA")
    ax_fft.grid(True, alpha=0.25)
    ax_fft.legend(frameon=False, fontsize=8)

    z = record["merged_z"]
    ax_fit.scatter(z.real, -z.imag, s=18, color="#1d3557", alpha=0.8, label="merged data")
    ax_fit.plot(record["fit_z"].real, -record["fit_z"].imag, color="#d62828", lw=2.0, label=f"{record.get('fit_model', 'fit')} fit")
    ax_fit.scatter(peis[:, 1], -peis[:, 2], s=12, color="#457b9d", alpha=0.35, label="PEIS measured")
    ax_fit.set_title("full arc")
    ax_fit.set_xlabel("Zre / Ohm")
    ax_fit.set_ylabel("-Zim / Ohm")
    ax_fit.grid(True, alpha=0.25)
    ax_fit.legend(frameon=False, fontsize=8)
    x_all = np.concatenate([z.real, record["fit_z"].real])
    y_all = np.concatenate([-z.imag, -record["fit_z"].imag])
    finite = np.isfinite(x_all) & np.isfinite(y_all)
    if finite.any():
        xmin, xmax = np.nanpercentile(x_all[finite], [0.5, 99.5])
        ymin, ymax = np.nanpercentile(y_all[finite], [0.5, 99.5])
        pad = max(xmax - xmin, ymax - ymin, 1.0) * 0.08
        ax_fit.set_xlim(xmin - pad, xmax + pad)
        ax_fit.set_ylim(max(0.0, ymin - pad), ymax + pad)
        ax_fit.set_aspect("equal", adjustable="box")

    ax_text.axis("off")
    text = [
        title,
        f"folder: {record['folder_name']}",
        f"started: {record['started']:%Y-%m-%d %H:%M:%S}",
        f"bias: {record['bias']:+.3f} V",
        "dV: 30 mV, BW4, dt=0.1 s",
        "PEIS: 1 MHz -> 0.5 Hz, npts=60",
        f"fit model: {record.get('fit_model', 'n/a')}",
        f"fit score: {record['score']:.3g}",
        f"fit cost: {record['cost']:.3g}",
        f"data sufficient: {record['data_sufficient']}",
        f"postcheck: {record['postcheck']}",
        f"CP duration: {record['cp_duration']:.1f} s" if record["cp_duration"] is not None else "CP duration: n/a",
        f"raw LF rec: {record['raw_lf']:.3g} Hz" if record["raw_lf"] is not None else "raw LF rec: n/a",
        f"analysis LF rec: {record['analysis_lf']:.3g} Hz" if record["analysis_lf"] is not None else "analysis LF rec: n/a",
        f"R0/R1/R2: {record['R0']:.3g}, {record['R1']:.3g}, {record['R2']:.3g} Ohm",
    ]
    ax_text.text(0.02, 0.98, "\n".join(text), va="top", ha="left", fontsize=10)
    fig.suptitle(title, fontsize=14)

    tmp = PROJECT_DIR / "_tmp_onepage.png"
    fig.savefig(tmp, dpi=170, bbox_inches="tight")
    plt.close(fig)
    sync_file_to_destinations(tmp, relative_path)
    try:
        tmp.unlink()
    except OSError:
        pass


def make_full_arc_overlay(records: list[dict], relative_path: Path, title: str) -> None:
    if not records:
        return
    colors = cm.viridis(np.linspace(0.05, 0.95, len(records)))
    fig, ax = plt.subplots(figsize=(9.5, 8.0), layout="constrained")
    all_x = []
    all_y = []
    for c, rec in zip(colors, records):
        z = rec["merged_z"]
        label = f"{rec['label']} {rec['started']:%H:%M} s={rec['score']:.2f}"
        ax.plot(z.real, -z.imag, marker="o", ms=2.6, lw=1.0, color=c, alpha=0.78, label=label)
        all_x.append(z.real)
        all_y.append(-z.imag)
    ax.set_title(title)
    ax.set_xlabel("Zre / Ohm")
    ax.set_ylabel("-Zim / Ohm")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7, frameon=False, ncol=2)
    x_all = np.concatenate(all_x)
    y_all = np.concatenate(all_y)
    finite = np.isfinite(x_all) & np.isfinite(y_all)
    if finite.any():
        xmin, xmax = np.nanpercentile(x_all[finite], [0.5, 99.5])
        ymin, ymax = np.nanpercentile(y_all[finite], [0.5, 99.5])
        pad = max(xmax - xmin, ymax - ymin, 1.0) * 0.08
        ax.set_xlim(xmin - pad, xmax + pad)
        ax.set_ylim(max(0.0, ymin - pad), ymax + pad)
        ax.set_aspect("equal", adjustable="box")
    tmp = PROJECT_DIR / "_tmp_overlay.png"
    fig.savefig(tmp, dpi=180, bbox_inches="tight")
    plt.close(fig)
    sync_file_to_destinations(tmp, relative_path)
    try:
        tmp.unlink()
    except OSError:
        pass


def make_longterm_onepage(records: list[dict], relative_path: Path, title: str, start_time: datetime) -> None:
    if not records:
        return
    colors = cm.viridis(np.linspace(0.05, 0.95, len(records)))
    fig = plt.figure(figsize=(15, 10), layout="constrained")
    gs = fig.add_gridspec(3, 3, height_ratios=[1.2, 1.0, 0.85])
    ax_nyq = fig.add_subplot(gs[:2, :2])
    ax_zoom = fig.add_subplot(gs[0, 2])
    ax_score = fig.add_subplot(gs[1, 2])
    ax_r = fig.add_subplot(gs[2, 0])
    ax_cp = fig.add_subplot(gs[2, 1])
    ax_lf = fig.add_subplot(gs[2, 2])
    all_x = []
    all_y = []
    for c, rec in zip(colors, records):
        label = f"#{rec['idx']} {rec['started']:%H:%M} s={rec['score']:.2f}"
        z = rec["merged_z"]
        ax_nyq.plot(z.real, -z.imag, marker="o", ms=2.7, lw=0.9, color=c, alpha=0.75, label=label)
        ax_nyq.plot(rec["fit_z"].real, -rec["fit_z"].imag, lw=1.0, color=c, alpha=0.50)
        pz = rec["peis_z"]
        ax_zoom.plot(pz.real, -pz.imag, marker=".", ms=2.4, lw=0.8, color=c, alpha=0.75)
        all_x.append(z.real)
        all_y.append(-z.imag)
    ax_nyq.set_title("merged full arc over time\nmarkers=data (PEIS + FFT LF), lines=selected fit")
    ax_nyq.set_xlabel("Zre / Ohm")
    ax_nyq.set_ylabel("-Zim / Ohm")
    ax_nyq.grid(True, alpha=0.25)
    ax_nyq.legend(fontsize=7, ncol=2, frameon=False, loc="best")
    x_all = np.concatenate(all_x)
    y_all = np.concatenate(all_y)
    finite = np.isfinite(x_all) & np.isfinite(y_all)
    if finite.any():
        xmin, xmax = np.nanpercentile(x_all[finite], [0.5, 99.5])
        ymin, ymax = np.nanpercentile(y_all[finite], [0.5, 99.5])
        pad = max(xmax - xmin, ymax - ymin, 1.0) * 0.08
        ax_nyq.set_xlim(xmin - pad, xmax + pad)
        ax_nyq.set_ylim(max(0.0, ymin - pad), ymax + pad)
        ax_nyq.set_aspect("equal", adjustable="box")

    ax_zoom.set_title("PEIS-only zoom")
    ax_zoom.set_xlabel("Zre / Ohm")
    ax_zoom.set_ylabel("-Zim / Ohm")
    ax_zoom.grid(True, alpha=0.25)
    px = np.concatenate([r["peis_z"].real for r in records])
    py = np.concatenate([-r["peis_z"].imag for r in records])
    finite = np.isfinite(px) & np.isfinite(py)
    if finite.any():
        xmin, xmax = np.nanpercentile(px[finite], [1, 99])
        ymin, ymax = np.nanpercentile(py[finite], [1, 99])
        pad = max(xmax - xmin, ymax - ymin, 1.0) * 0.12
        ax_zoom.set_xlim(xmin - pad, xmax + pad)
        ax_zoom.set_ylim(max(0.0, ymin - pad), ymax + pad)
        ax_zoom.set_aspect("equal", adjustable="box")

    elapsed = [(r["started"] - start_time).total_seconds() / 3600.0 for r in records]
    ax_score.plot(elapsed, [r["score"] for r in records], marker="o", color="#1d3557")
    for r, x in zip(records, elapsed):
        if (not r["data_sufficient"]) or (np.isfinite(r["score"]) and r["score"] > 1.0):
            ax_score.scatter([x], [r["score"]], color="#d62828", s=55, zorder=5)
            ax_score.text(x, r["score"], f"#{r['idx']}", fontsize=8, va="bottom")
    ax_score.axhline(1.0, color="#d62828", lw=1, ls="--", alpha=0.5)
    ax_score.set_title("Fit score vs time")
    ax_score.set_xlabel("elapsed / h")
    ax_score.set_ylabel("score")
    ax_score.grid(True, alpha=0.25)

    for key, col in [("R0", "#264653"), ("R1", "#e76f51"), ("R2", "#2a9d8f")]:
        ax_r.plot(elapsed, [r[key] for r in records], marker="o", lw=1.2, label=key, color=col)
    ax_r.set_yscale("log")
    ax_r.set_title("selected-fit branch R trend")
    ax_r.set_xlabel("elapsed / h")
    ax_r.set_ylabel("R / Ohm")
    ax_r.grid(True, which="both", alpha=0.25)
    ax_r.legend(frameon=False, fontsize=8)

    ax_cp.plot(elapsed, [r["cp_duration"] if r["cp_duration"] is not None else np.nan for r in records], marker="o", color="#457b9d")
    ax_cp.set_title("Live-stop CP duration")
    ax_cp.set_xlabel("elapsed / h")
    ax_cp.set_ylabel("duration / s")
    ax_cp.grid(True, alpha=0.25)

    ax_lf.plot(elapsed, [r["raw_lf"] if r["raw_lf"] is not None else np.nan for r in records], marker="o", label="raw LF rec", color="#6a4c93")
    ax_lf.plot(elapsed, [r["analysis_lf"] if r["analysis_lf"] is not None else np.nan for r in records], marker="s", label="analysis LF rec", color="#ff9f1c")
    ax_lf.axhline(0.5, color="0.35", ls="--", lw=1, label="PEIS actual LF")
    ax_lf.set_yscale("log")
    ax_lf.set_title("Recommended LF")
    ax_lf.set_xlabel("elapsed / h")
    ax_lf.set_ylabel("Hz")
    ax_lf.grid(True, which="both", alpha=0.25)
    ax_lf.legend(frameon=False, fontsize=7)

    fig.suptitle(title, fontsize=14)
    tmp = PROJECT_DIR / "_tmp_longterm_onepage.png"
    fig.savefig(tmp, dpi=170, bbox_inches="tight")
    plt.close(fig)
    sync_file_to_destinations(tmp, relative_path)
    try:
        tmp.unlink()
    except OSError:
        pass


def choose_representative(records: list[dict]) -> dict | None:
    clean = [r for r in records if r["data_sufficient"] and np.isfinite(r["score"])]
    pool = clean or [r for r in records if np.isfinite(r["score"])]
    if not pool:
        return records[0] if records else None
    return min(pool, key=lambda r: r["score"])


def maybe_adopt_current(adopt_folder: Path | None, run_log_path: Path) -> Path | None:
    if adopt_folder is None:
        return None
    log(run_log_path, f"Adopting currently running/existing folder: {adopt_folder}")
    wait_for_summary(adopt_folder, timeout_s=None, log_path=run_log_path)
    return adopt_folder


def find_completed_longterm_history(since: datetime, before: datetime | None = None) -> list[Path]:
    folders: list[tuple[datetime, Path]] = []
    for folder in RESULTS_DIR.glob("continuous_prepost_seeded_reference_*"):
        summary_path = folder / "continuous_prepost_seeded_reference_summary.json"
        if not summary_path.exists():
            continue
        try:
            summary = json.loads(summary_path.read_text())
            started = datetime.strptime(summary["started_at"], "%Y-%m-%d %H:%M:%S")
            settings = summary.get("settings", {})
            if abs(float(settings.get("bias_v", np.nan)) - 0.0) > 1e-12:
                continue
            if abs(float(settings.get("dv_v", np.nan)) - 0.03) > 1e-12:
                continue
            if float(settings.get("peis_high_hz", 0.0)) < 9.9e5:
                continue
            if started < since:
                continue
            if before is not None and started >= before:
                continue
            folders.append((started, folder))
        except Exception:
            continue
    return [folder for _, folder in sorted(folders)]


def run_longterm(args: argparse.Namespace, run_log_path: Path) -> list[dict]:
    start_folder = maybe_adopt_current(args.adopt_current, run_log_path)
    start_time = datetime.now()
    if start_folder is not None:
        try:
            start_time = datetime.strptime(load_summary(start_folder)["started_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            start_time = datetime.fromtimestamp(start_folder.stat().st_ctime)
    if args.history_since is not None:
        start_time = args.history_since
    end_time = start_time + timedelta(hours=args.longterm_hours)
    log(run_log_path, f"LONGTERM start={start_time:%Y-%m-%d %H:%M:%S} end={end_time:%Y-%m-%d %H:%M:%S}")

    records: list[dict] = []
    repeat_idx = 1
    if args.history_since is not None:
        adopt_started = None
        if start_folder is not None:
            try:
                adopt_started = datetime.strptime(load_summary(start_folder)["started_at"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                adopt_started = None
        for hist_folder in find_completed_longterm_history(args.history_since, before=adopt_started):
            rec = summarize_folder(hist_folder, repeat_idx, "longterm", f"repeat{repeat_idx:03d}")
            records.append(rec)
            save_folder_outputs(rec, Path("raw_results") / "longterm")
            repeat_idx += 1

    if start_folder is not None:
        rec = summarize_folder(start_folder, repeat_idx, "longterm", f"repeat{repeat_idx:03d}")
        records.append(rec)
        save_folder_outputs(rec, Path("raw_results") / "longterm")
        write_ledger(records, Path("longterm") / "longterm_0V_repeat_ledger.json")
        make_full_arc_overlay(records, Path("longterm") / "longterm_0V_full_arc_overlay.png", f"{args.temperature_label} 0V longterm full arc overlay")
        make_longterm_onepage(records, Path("longterm") / "longterm_0V_trend_onepage.png", f"{args.temperature_label} 0V longterm trend", start_time)
        repeat_idx += 1

    while datetime.now() < end_time:
        scheduled = start_time + timedelta(seconds=args.interval_s * (repeat_idx - 1))
        wait_s = (scheduled - datetime.now()).total_seconds()
        if wait_s > 0:
            log(run_log_path, f"Waiting {wait_s:.1f}s for repeat #{repeat_idx} scheduled at {scheduled:%H:%M:%S}")
            time.sleep(wait_s)
        folder = run_measurement(
            python_exe=args.python,
            bias=0.0,
            label=f"{args.temperature_label}_restart_longterm_repeat{repeat_idx:03d}_0V",
            run_log_path=run_log_path,
        )
        rec = summarize_folder(folder, repeat_idx, "longterm", f"repeat{repeat_idx:03d}")
        records.append(rec)
        save_folder_outputs(rec, Path("raw_results") / "longterm")
        write_ledger(records, Path("longterm") / "longterm_0V_repeat_ledger.json")
        make_full_arc_overlay(records, Path("longterm") / "longterm_0V_full_arc_overlay.png", f"{args.temperature_label} 0V longterm full arc overlay")
        make_longterm_onepage(records, Path("longterm") / "longterm_0V_trend_onepage.png", f"{args.temperature_label} 0V longterm trend", start_time)
        rep = choose_representative(records)
        if rep is not None:
            make_folder_onepage(
                rep,
                Path("longterm") / f"representative_{rep['label']}_onepage.png",
                f"{args.temperature_label} 0V longterm representative {rep['label']}",
            )
        repeat_idx += 1
    log(run_log_path, f"LONGTERM complete with {len(records)} records")
    return records


def run_voltage_ladder(args: argparse.Namespace, run_log_path: Path) -> list[dict]:
    voltages = [0.3, 0.2, 0.1, 0.0, -0.1, -0.2, -0.3]
    records: list[dict] = []
    for idx, bias in enumerate(voltages, start=1):
        label_token = f"{bias:+.1f}V".replace("+", "p").replace("-", "m").replace(".", "p")
        folder = run_measurement(
            python_exe=args.python,
            bias=bias,
            label=f"{args.temperature_label}_voltage_ladder_{idx:02d}_{label_token}",
            run_log_path=run_log_path,
        )
        rec = summarize_folder(folder, idx, "voltage_ladder", label_token)
        records.append(rec)
        save_folder_outputs(rec, Path("raw_results") / "voltage_ladder")
        write_ledger(records, Path("voltage_ladder") / "voltage_ladder_ledger.json")
        make_folder_onepage(
            rec,
            Path("voltage_ladder") / f"voltage_{idx:02d}_{label_token}_onepage.png",
            f"{args.temperature_label} voltage ladder {bias:+.1f} V",
        )
        make_full_arc_overlay(records, Path("voltage_ladder") / "voltage_ladder_full_arc_overlay.png", f"{args.temperature_label} voltage ladder full arc overlay")
    log(run_log_path, f"VOLTAGE ladder complete with {len(records)} records")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Robust 350C longterm 0V repeat + voltage ladder supervisor.")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--longterm-hours", type=float, default=10.0)
    parser.add_argument("--interval-s", type=float, default=600.0)
    parser.add_argument("--adopt-current", type=Path, default=None)
    parser.add_argument(
        "--history-since",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d %H:%M:%S"),
        default=None,
        help="Include completed 0V longterm folders since this local timestamp before adopting the active folder.",
    )
    parser.add_argument("--skip-longterm", action="store_true")
    parser.add_argument("--skip-voltage-ladder", action="store_true")
    parser.add_argument("--temperature-label", default="350C")
    parser.add_argument("--destination", type=Path, default=REQUESTED_DEST)
    args = parser.parse_args()

    global RUNTIME_DESTINATIONS
    RUNTIME_DESTINATIONS = [args.destination]
    ensure_destinations()
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_log_path = RUN_LOG_DIR / f"{args.temperature_label}_longterm_voltage_supervisor_{now_stamp()}.log"
    log(run_log_path, f"Supervisor started. destinations={[str(p) for p in active_destinations()]}")
    if REQUESTED_DEST not in active_destinations():
        log(run_log_path, f"Requested G: destination is unavailable; writing fallback to {FALLBACK_DEST}")

    longterm_records: list[dict] = []
    if not args.skip_longterm:
        longterm_records = run_longterm(args, run_log_path)
    if not args.skip_voltage_ladder:
        run_voltage_ladder(args, run_log_path)
    log(run_log_path, f"Supervisor finished. longterm_records={len(longterm_records)}")


if __name__ == "__main__":
    main()
