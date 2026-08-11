from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CONVERT_DIR = PROJECT_DIR.parent / "Convert_CP_to_EIS 1"
VENV_PYTHON = PROJECT_DIR.parent / ".venv" / "Scripts" / "python.exe"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(CONVERT_DIR) not in sys.path:
    sys.path.insert(0, str(CONVERT_DIR))

from matplotlib_compat import install_pyplot_compat
install_pyplot_compat(plt)

from analysis_adapter import analyze_measurement_files_with_visuals
from driver_biologic import BioLogicController
import compare_full_vs_optimized_fit as OPT


def _timestamp_dir(prefix: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"{prefix}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _save_txt(path: Path, data, header: str) -> None:
    arr = np.asarray(data, dtype=float)
    np.savetxt(path, arr, header=header, comments="")


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


def _ensure_connected(bl: BioLogicController) -> None:
    """
    Some live runs leave the easy-biologic device object disconnected even
    though the queue is still active. Reconnect lazily before each case.
    """
    try:
        _ = bl.get_ocv()
        return
    except Exception:
        try:
            bl.disconnect()
        except Exception:
            pass
        bl.connect()


def _plot_ca_curve(dc_path: Path, out_path: Path, title: str) -> None:
    data = np.loadtxt(dc_path, skiprows=1)
    fig, ax = plt.subplots(figsize=(7.5, 4.8), constrained_layout=True)
    ax.plot(data[:, 0], data[:, 2] * 1e6, color="#1f77b4", linewidth=1.2)
    ax.set_xlabel("time / s")
    ax.set_ylabel("Current / uA")
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _plot_measured_peis(eis_path: Path, out_path: Path, title: str) -> None:
    data = np.loadtxt(eis_path, skiprows=1)
    order = np.argsort(data[:, 0])[::-1]
    fig, ax = plt.subplots(figsize=(6.5, 5.4), constrained_layout=True)
    # Queue-exported raw PEIS text stores the third column as -Im(Z), so plot it directly.
    ax.scatter(data[order, 1], data[order, 2], s=18, color="#1f77b4")
    ax.plot(data[order, 1], data[order, 2], color="#1f77b4", linewidth=1.0, alpha=0.5)
    ax.set_xlabel("Zre / Ohm")
    ax.set_ylabel("-Zim / Ohm")
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _plot_full_arc_from_analysis(
    dc_path: Path,
    eis_path: Path,
    out_path: Path,
    title: str,
    *,
    protocol_trim_start_s: float | None = None,
) -> dict:
    ca_data = np.loadtxt(dc_path, skiprows=1)
    if protocol_trim_start_s is not None:
        ca_data = ca_data[ca_data[:, 0] >= float(protocol_trim_start_s)].copy()
        ca_data[:, 0] -= float(ca_data[0, 0])
        trim_path = out_path.with_name(out_path.stem + "_fft_input.txt")
        np.savetxt(trim_path, ca_data, header="time/s  V/V  I/A", comments="")
        analysis_dc_path = trim_path
    else:
        analysis_dc_path = dc_path

    analysis = OPT.recommend_optimized_configuration(
        dc_path=str(analysis_dc_path),
        eis_path=str(eis_path),
        current_in_mA=False,
        current_scale_factor=1.0,
        trim_start_time=None,
        auto_trim=False,
    )

    opt_recovery = analysis["optimized_recovery"]
    opt_fit_bundle = analysis["optimized_fit_bundle"]
    full_recovery = analysis["full_recovery"]

    eis_data = np.asarray(full_recovery["eis_data"], dtype=float)
    rec_f = np.asarray(opt_recovery["filtered_f"], dtype=float)
    rec_z = np.asarray(opt_recovery["recovered_z"])
    # For the displayed "full arc", keep all actually measured PEIS points and
    # only let CA FFT contribute below the measured PEIS low-frequency limit.
    # The recommended LF remains useful for protocol planning, but it should not
    # replace real PEIS data that already exists.
    recommended_cutoff_hz = analysis.get("recommended_peis_lowest_freq_hz")
    actual_merge_cutoff_hz = float(np.min(eis_data[:, 0])) if len(eis_data) else None
    if actual_merge_cutoff_hz is not None and np.isfinite(actual_merge_cutoff_hz):
        hybrid = OPT.build_hybrid_dataset(
            eis_data,
            rec_f,
            rec_z,
            peis_lowest_freq_hz=actual_merge_cutoff_hz,
        )
        used_peis_mask = np.asarray(hybrid["used_peis_mask"], dtype=bool)
        used_rec_mask = np.asarray(hybrid["used_recovered_mask"], dtype=bool)
        display_fit_bundle = OPT.fit_hybrid_dataset(hybrid, make_figure=False)
        display_fit_quality = float(display_fit_bundle["fit"].get("Fit quality score", np.nan))
        display_fit = display_fit_bundle["fit"]
    else:
        actual_merge_cutoff_hz = None
        used_peis_mask = np.ones(len(eis_data), dtype=bool)
        used_rec_mask = np.ones(len(rec_f), dtype=bool)
        display_fit_quality = float(opt_fit_bundle["fit"].get("Fit quality score", np.nan))
        display_fit = opt_fit_bundle["fit"]

    min_freq = min(
        float(np.min(eis_data[:, 0])),
        float(np.min(rec_f[used_rec_mask])) if np.any(used_rec_mask) else float(np.min(eis_data[:, 0])),
    )
    max_freq = max(
        float(np.max(eis_data[:, 0])),
        float(np.max(rec_f[used_rec_mask])) if np.any(used_rec_mask) else float(np.max(eis_data[:, 0])),
    )
    min_plot_freq = max(min(min_freq / 100.0, 1e-5), 1e-6)
    freq_plot = np.logspace(np.log10(min_plot_freq), np.log10(max_freq), 800)
    z_fit = OPT.model_curve_from_fit(display_fit, freq_plot)

    fig, ax = plt.subplots(figsize=(7.5, 5.8), constrained_layout=True)
    if np.any(~used_peis_mask):
        ax.scatter(
            eis_data[~used_peis_mask, 1],
            -eis_data[~used_peis_mask, 2],
            s=14,
            color="#9ecae1",
            alpha=0.45,
            label="Measured PEIS (overlap, not used)",
        )
    ax.scatter(
        eis_data[used_peis_mask, 1],
        -eis_data[used_peis_mask, 2],
        s=18,
        color="#1f77b4",
        alpha=0.9,
        label="Measured PEIS (used)",
    )
    if len(rec_f) and np.any(~used_rec_mask):
        ax.scatter(
            rec_z.real[~used_rec_mask],
            -rec_z.imag[~used_rec_mask],
            s=14,
            color="#fdd0a2",
            alpha=0.45,
            label="CA FFT (overlap, not used)",
        )
    if len(rec_f) and np.any(used_rec_mask):
        ax.scatter(
            rec_z.real[used_rec_mask],
            -rec_z.imag[used_rec_mask],
            s=22,
            color="#ff7f0e",
            alpha=0.9,
            label="Recovered from CA FFT (LF used)",
        )
    ax.plot(z_fit.real, -z_fit.imag, color="#d62728", linewidth=2.2, label="RQRQRQ fit")
    ax.set_xlabel("Zre / Ohm")
    ax.set_ylabel("-Zim / Ohm")
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    if actual_merge_cutoff_hz is not None:
        ax.text(
            0.02,
            0.02,
            f"PEIS merge cutoff = {actual_merge_cutoff_hz:.3g} Hz",
            transform=ax.transAxes,
            fontsize=9,
            color="#444",
            ha="left",
            va="bottom",
        )
    ax.legend(frameon=False)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)

    return {
        "fit_quality_score": display_fit_quality,
        "recommended_peis_lowest_freq_hz": float(analysis["recommended_peis_lowest_freq_hz"]),
        "actual_peis_merge_cutoff_hz": actual_merge_cutoff_hz,
        "recommended_min_cp_duration_s": (
            None
            if analysis["recommended_min_cp_duration_s"] is None
            else float(analysis["recommended_min_cp_duration_s"])
        ),
        "used_peis_points": int(np.sum(used_peis_mask)),
        "used_recovered_points": int(np.sum(used_rec_mask)),
        "postcheck_reason": analysis.get("postcheck_reason"),
        "protocol_trim_start_s": protocol_trim_start_s,
    }


def _generate_case_artifacts(
    case_dir: Path,
    dc_path: Path,
    eis_path: Path,
    title_prefix: str,
    *,
    protocol_trim_start_s: float | None = None,
) -> dict:
    ca_curve_path = case_dir / f"{title_prefix}_CA_curve.png"
    measured_peis_path = case_dir / f"{title_prefix}_measured_peis.png"
    full_arc_path = case_dir / f"{title_prefix}_full_arc.png"

    _plot_ca_curve(dc_path, ca_curve_path, f"{title_prefix} CA")
    _plot_measured_peis(eis_path, measured_peis_path, f"{title_prefix} measured PEIS")
    full_arc_summary = _plot_full_arc_from_analysis(
        dc_path,
        eis_path,
        full_arc_path,
        f"{title_prefix} full arc",
        protocol_trim_start_s=protocol_trim_start_s,
    )

    artifact_summary = {
        "ca_curve_path": str(ca_curve_path),
        "measured_peis_path": str(measured_peis_path),
        "full_arc_path": str(full_arc_path),
        **full_arc_summary,
    }
    (case_dir / f"{title_prefix}_artifact_summary.json").write_text(
        json.dumps(artifact_summary, indent=2),
        encoding="utf-8",
    )
    return artifact_summary


def _run_case7_exact_live(bl: BioLogicController, out_dir: Path) -> dict:
    _ensure_connected(bl)
    case_dir = out_dir / "01_case7_exact_repeat1"
    case_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "label": "case7_exact_repeat1",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": {
            "ca_voltages_v": [0.100, 0.120],
            "ca_durations_s": [120.0, 300.0],
            "ca_dt_s": 0.1,
            "peis_f_high_hz": 7.0e6,
            "peis_f_low_hz": 0.05,
            "peis_npts": 160,
            "peis_amplitude_mv": 20.0,
        },
    }
    summary["ocv_before_v"] = float(bl.get_ocv())

    ca = bl.run_ca_sequence(
        [0.100, 0.120],
        [120.0, 300.0],
        dt_record=0.1,
        channel=1,
        read_interval=0.1,
    )
    ca_path = case_dir / "case7_exact_live_CA.txt"
    _save_txt(ca_path, ca, "time/s  V/V  I/A")

    peis = bl.run_peis(
        0.100,
        f_high=7.0e6,
        f_low=0.05,
        n_pts=160,
        amplitude_mv=20.0,
        channel=1,
        read_interval=1.0,
    )
    peis_path = case_dir / "case7_exact_live_PEIS.txt"
    _save_txt(peis_path, peis, "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")

    try:
        analysis_result, visuals = analyze_measurement_files_with_visuals(
            str(ca_path),
            str(peis_path),
            sample_name="case7_exact_repeat1",
        )
        summary["analysis_result"] = _json_safe(analysis_result.__dict__)
        summary["visuals"] = _json_safe(visuals)
    except Exception as exc:
        summary["analysis_error"] = f"{type(exc).__name__}: {exc}"

    try:
        summary["artifacts"] = _json_safe(
            _generate_case_artifacts(
                case_dir,
                ca_path,
                peis_path,
                "case7_exact_repeat1",
            )
        )
    except Exception as exc:
        summary["artifact_error"] = f"{type(exc).__name__}: {exc}"

    summary["rows"] = {
        "ca_rows": int(len(np.asarray(ca))),
        "peis_rows": int(len(np.asarray(peis))),
    }
    try:
        summary["ocv_after_v"] = float(bl.get_ocv())
    except Exception:
        summary["ocv_after_v"] = None
    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (case_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _run_short_trusted_style(
    bl: BioLogicController,
    out_dir: Path,
    *,
    case_name: str,
    label: str,
    peis_v_dc_v: float,
    post_v_dc_v: float,
    pre_duration_s: float,
    post_duration_s: float,
    protocol_trim_start_s: float | None = None,
) -> dict:
    _ensure_connected(bl)
    case_dir = out_dir / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "label": label,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": {
            "peis_v_dc_v": peis_v_dc_v,
            "peis_f_high_hz": 7.0e6,
            "peis_f_low_hz": 0.05,
            "peis_npts": 160,
            "peis_amplitude_mv": 20.0,
            "ca_voltages_v": [peis_v_dc_v, post_v_dc_v],
            "ca_durations_s": [pre_duration_s, post_duration_s],
            "ca_dt_s": 0.1,
            "protocol_trim_start_s": protocol_trim_start_s,
        },
    }
    summary["ocv_before_v"] = float(bl.get_ocv())

    peis = bl.run_peis(
        peis_v_dc_v,
        f_high=7.0e6,
        f_low=0.05,
        n_pts=160,
        amplitude_mv=20.0,
        channel=1,
        read_interval=1.0,
    )
    peis_path = case_dir / f"{label}_PEIS.txt"
    _save_txt(peis_path, peis, "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")

    ca = bl.run_ca_sequence(
        [peis_v_dc_v, post_v_dc_v],
        [pre_duration_s, post_duration_s],
        dt_record=0.1,
        channel=1,
        read_interval=0.1,
    )
    ca_path = case_dir / f"{label}_CA.txt"
    _save_txt(ca_path, ca, "time/s  V/V  I/A")

    try:
        analysis_result, visuals = analyze_measurement_files_with_visuals(
            str(ca_path),
            str(peis_path),
            sample_name=label,
        )
        summary["analysis_result"] = _json_safe(analysis_result.__dict__)
        summary["visuals"] = _json_safe(visuals)
    except Exception as exc:
        summary["analysis_error"] = f"{type(exc).__name__}: {exc}"

    try:
        summary["artifacts"] = _json_safe(
            _generate_case_artifacts(
                case_dir,
                ca_path,
                peis_path,
                label,
                protocol_trim_start_s=protocol_trim_start_s,
            )
        )
    except Exception as exc:
        summary["artifact_error"] = f"{type(exc).__name__}: {exc}"

    summary["rows"] = {
        "ca_rows": int(len(np.asarray(ca))),
        "peis_rows": int(len(np.asarray(peis))),
    }
    try:
        summary["ocv_after_v"] = float(bl.get_ocv())
    except Exception:
        summary["ocv_after_v"] = None
    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (case_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _run_main_candidate(bl: BioLogicController, out_dir: Path, *, case_name: str, bias_v: float) -> dict:
    case_dir = out_dir / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    import subprocess

    started = time.time()
    python_exe = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    proc = subprocess.run(
        [
            python_exe,
            str(SCRIPT_DIR / "run_continuous_prepost_seeded_reference.py"),
            "--bias",
            str(bias_v),
            "--dv",
            "0.03",
            "--pre-min",
            "20",
            "--pre-max",
            "600",
            "--scout-min",
            "60",
            "--scout-max",
            "600",
            "--dt",
            "0.1",
            "--peis-floor",
            "0.5",
            "--peis-npts",
            "20",
            "--post-stable-buffer",
            "20",
        ],
        capture_output=True,
        text=True,
        shell=False,
    )
    summary = {
        "label": case_name,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "returncode": int(proc.returncode),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "runtime_s": float(time.time() - started),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (case_dir / "subprocess_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    out_dir = _timestamp_dir("priority400_queue")
    queue_summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "cases": [],
    }

    bl = BioLogicController()
    bl.connect()
    try:
        queue_summary["ocv_before_v"] = float(bl.get_ocv())
        queue_summary["cases"].append(_run_case7_exact_live(bl, out_dir))
        queue_summary["cases"].append(
            _run_short_trusted_style(
                bl,
                out_dir,
                case_name="02_plus0p1_short_trusted_style",
                label="plus0p1_short_trusted_style",
                peis_v_dc_v=0.100,
                post_v_dc_v=0.120,
                pre_duration_s=10.0,
                post_duration_s=300.0,
                protocol_trim_start_s=7.0,
            )
        )
        queue_summary["cases"].append(
            _run_short_trusted_style(
                bl,
                out_dir,
                case_name="03_zeroV_short_trusted_style",
                label="zeroV_short_trusted_style",
                peis_v_dc_v=0.000,
                post_v_dc_v=0.020,
                pre_duration_s=10.0,
                post_duration_s=300.0,
                protocol_trim_start_s=7.0,
            )
        )
        queue_summary["cases"].append(
            _run_short_trusted_style(
                bl,
                out_dir,
                case_name="04_minus0p1_short_trusted_style",
                label="minus0p1_short_trusted_style",
                peis_v_dc_v=-0.100,
                post_v_dc_v=-0.080,
                pre_duration_s=10.0,
                post_duration_s=300.0,
                protocol_trim_start_s=7.0,
            )
        )
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    queue_summary["cases"].append(
        _run_main_candidate(
            bl,
            out_dir,
            case_name="05_main_candidate_plus0p1",
            bias_v=0.1,
        )
    )
    queue_summary["cases"].append(
        _run_main_candidate(
            bl,
            out_dir,
            case_name="06_main_candidate_zeroV",
            bias_v=0.0,
        )
    )
    queue_summary["cases"].append(
        _run_main_candidate(
            bl,
            out_dir,
            case_name="07_main_candidate_minus0p1",
            bias_v=-0.1,
        )
    )
    queue_summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out_dir / "queue_summary.json").write_text(json.dumps(queue_summary, indent=2), encoding="utf-8")
    print(str(out_dir / "queue_summary.json"))


if __name__ == "__main__":
    main()
