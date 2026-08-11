from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CONVERT_DIR = PROJECT_DIR.parent / "Convert_CP_to_EIS 1"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(CONVERT_DIR) not in sys.path:
    sys.path.insert(0, str(CONVERT_DIR))

from matplotlib_compat import install_pyplot_compat
install_pyplot_compat(plt)

from Analysis_Convert_CP_to_EIS import EIS_Fitting as EF
from cp_first_policy import recommend_peis_lf_from_cp_txt
from tools.run_continuous_prepost_seeded_reference import _load_eis, _merge_peis_and_fft, _resolve_overlap_cutoff


def _safe_float(value):
    try:
        out = float(value)
    except Exception:
        return np.nan
    return out if np.isfinite(out) else np.nan


def _load_summary(folder: Path) -> dict | None:
    path = folder / "continuous_prepost_seeded_reference_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _recover_merged(folder: Path, summary: dict):
    peis_path = folder / "seeded_peis.txt"
    fft_path = folder / "combined_prepost_fft_ready.txt"
    if not peis_path.exists() or not fft_path.exists():
        return None
    peis_loaded = _load_eis(peis_path)
    cutoff_target = _safe_float(summary.get("analysis_result", {}).get("recommended_peis_lowest_freq_hz"))
    if not np.isfinite(cutoff_target):
        cutoff_target = _safe_float(summary.get("merged_cutoff_hz"))
    cutoff = _resolve_overlap_cutoff(peis_loaded, cutoff_target)
    fft_bundle = recommend_peis_lf_from_cp_txt(str(fft_path), current_in_mA=False)
    merged_f, merged_z = _merge_peis_and_fft(
        peis_loaded,
        np.asarray(fft_bundle["analysis_f"], dtype=float),
        np.asarray(fft_bundle["analysis_z"], dtype=complex),
        cutoff_hz=cutoff,
    )
    return merged_f, merged_z


def validate_folders(root: Path, *, limit: int | None = None) -> list[dict]:
    folders = sorted(root.glob("continuous_prepost_seeded_reference_*"), key=lambda p: p.name)
    if limit is not None and limit > 0:
        folders = folders[-limit:]
    rows = []
    for idx, folder in enumerate(folders, start=1):
        summary = _load_summary(folder)
        if summary is None:
            continue
        try:
            recovered = _recover_merged(folder, summary)
            if recovered is None:
                continue
            merged_f, merged_z = recovered
            auto = EF.fit_equivalent_circuit_auto(merged_f, merged_z.real, merged_z.imag)
        except Exception as exc:
            rows.append({"folder": folder.name, "error": repr(exc)})
            continue

        candidates = {item["model"]: item for item in auto.get("Auto fit candidate results", [])}
        settings = summary.get("settings", {})
        ar = summary.get("analysis_result", {})
        row = {
            "idx": idx,
            "folder": folder.name,
            "started_at": summary.get("started_at"),
            "bias_v": settings.get("bias_v"),
            "dv_v": settings.get("dv_v"),
            "peis_high_hz": settings.get("peis_high_hz"),
            "selected_model": auto.get("Equivalent circuit"),
            "selection_reason": auto.get("Auto selection reason"),
            "selected_rel_rmse": auto.get("Common rel RMSE"),
            "selected_low_rmse": auto.get("Common low-frequency Nyquist RMSE"),
            "old_score": summary.get("merged_fit", {}).get("Fit quality score"),
            "old_model": summary.get("merged_fit", {}).get("Equivalent circuit") or summary.get("merged_fit", {}).get("Fit model") or "RQRQRQ",
            "data_sufficient": ar.get("data_sufficient"),
            "postcheck": ar.get("postcheck_reason"),
            "cp_duration_s": ar.get("actual_cp_duration_s"),
            "raw_lf_hz": summary.get("seed_recommendation", {}).get("raw_recommended_lf_hz"),
            "analysis_lf_hz": ar.get("recommended_peis_lowest_freq_hz"),
        }
        for model in ("RQRQ", "RRQRQ", "RQRQRQ"):
            cand = candidates.get(model, {})
            row[f"{model}_rel_rmse"] = cand.get("common_rel_rmse")
            row[f"{model}_low_rmse"] = cand.get("common_low_nyquist_rmse")
            row[f"{model}_bic"] = cand.get("common_bic")
        rows.append(row)
    return rows


def write_outputs(rows: list[dict], out_dir: Path, stem: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"
    png_path = out_dir / f"{stem}.png"
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if rows:
        fieldnames = sorted({key for row in rows for key in row.keys()})
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    valid = [row for row in rows if row.get("selected_model")]
    fig = plt.figure(figsize=(14, 8), layout="constrained")
    gs = fig.add_gridspec(2, 2)
    ax_counts = fig.add_subplot(gs[0, 0])
    ax_rmse = fig.add_subplot(gs[0, 1])
    ax_by_run = fig.add_subplot(gs[1, :])

    models = ["RQRQ", "RRQRQ", "RQRQRQ"]
    counts = [sum(1 for row in valid if row.get("selected_model") == model) for model in models]
    ax_counts.bar(models, counts, color=["#2a9d8f", "#e9c46a", "#e76f51"])
    ax_counts.set_title("Auto-selected circuit counts")
    ax_counts.set_ylabel("count")
    ax_counts.grid(True, axis="y", alpha=0.25)

    x = np.arange(len(valid))
    for model, color in zip(models, ["#2a9d8f", "#e9c46a", "#e76f51"]):
        vals = [_safe_float(row.get(f"{model}_rel_rmse")) for row in valid]
        ax_rmse.plot(x, vals, marker="o", lw=1.0, ms=3, label=model, color=color)
    ax_rmse.set_yscale("log")
    ax_rmse.set_title("Common rel RMSE by model")
    ax_rmse.set_xlabel("run index")
    ax_rmse.set_ylabel("rel RMSE")
    ax_rmse.grid(True, which="both", alpha=0.25)
    ax_rmse.legend(frameon=False)

    color_map = {"RQRQ": "#2a9d8f", "RRQRQ": "#e9c46a", "RQRQRQ": "#e76f51"}
    selected_vals = [_safe_float(row.get("selected_rel_rmse")) for row in valid]
    selected_colors = [color_map.get(row.get("selected_model"), "0.5") for row in valid]
    ax_by_run.scatter(x, selected_vals, c=selected_colors, s=40)
    for i, row in enumerate(valid):
        label = f"{i+1}:{row.get('selected_model')}"
        if row.get("bias_v") is not None:
            label += f" {float(row['bias_v']):+.1f}V"
        ax_by_run.text(i, selected_vals[i], label, fontsize=7, rotation=60, ha="left", va="bottom")
    ax_by_run.set_yscale("log")
    ax_by_run.set_title("Selected model and selected error")
    ax_by_run.set_xlabel("run index")
    ax_by_run.set_ylabel("selected rel RMSE")
    ax_by_run.grid(True, which="both", alpha=0.25)

    fig.suptitle(stem)
    fig.savefig(png_path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return {"json": str(json_path), "csv": str(csv_path), "png": str(png_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate automatic EIS circuit selection across saved live hybrid runs.")
    parser.add_argument("--root", type=Path, default=PROJECT_DIR / "results")
    parser.add_argument("--out-dir", type=Path, default=CONVERT_DIR / "result" / "260426" / "model_selection")
    parser.add_argument("--stem", default="auto_circuit_selection_validation")
    parser.add_argument("--limit", type=int, default=0, help="Use only the latest N folders if >0.")
    args = parser.parse_args()
    rows = validate_folders(args.root, limit=args.limit or None)
    paths = write_outputs(rows, args.out_dir, args.stem)
    print(json.dumps({"rows": len(rows), **paths}, indent=2))


if __name__ == "__main__":
    main()
