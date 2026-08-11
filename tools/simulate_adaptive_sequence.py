# -*- coding: utf-8 -*-
"""
Replay a measured rapid-EIS folder through the internal adaptive engine.

This is a headless analysis utility for answering questions such as:
    "If the current auto-system had analyzed point n, what would it have used
     for point n+1?"

The script intentionally does not drive hardware. It only:
    - discovers measured CA/PEIS pairs from an existing folder
    - analyzes each point with the current internal analysis adapter
    - asks the adaptive engine what it would recommend for the next point
    - saves a machine-readable JSON summary
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from adaptive_engine import AdaptiveMeasurementEngine, AdaptiveEngineSettings
from adaptive_types import MeasurementExecution, MeasurementFiles, PointMetadata


DEFAULT_INPUT_DIR = Path(
    r"G:\My Drive\research\2. Northwestern\1-2. LSF-LSC (breakthrough project)\Convert_CP_to_EIS 1\Input data\260419 microprobe semiauto-5"
)
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "result" / "adaptive_sequence_simulations"


def _load_backend_loader():
    import os

    analysis_dir = PROJECT_DIR / "Analysis_Convert_CP_to_EIS"
    os.environ.setdefault("MPLBACKEND", "Agg")
    if str(analysis_dir) not in sys.path:
        sys.path.insert(0, str(analysis_dir))
    import Load_CP_Data as LD  # type: ignore

    return LD


PAIR_RE = re.compile(
    r"^(?P<label>.+?)_(?P<kind>CA|PEIS)_(?P<stamp>\d{8}_\d{6})\.(?P<ext>txt|csv|dat|mpt|mpr)$",
    flags=re.IGNORECASE,
)
PRE_RE = re.compile(
    r"^Pre_stabilization_(?P<label>.+?)_CA_(?P<stamp>\d{8}_\d{6})\.txt$",
    flags=re.IGNORECASE,
)
VOLT_RE = re.compile(r"V(?P<sign>[+-])(?P<value>\d+(?:\.\d+)?)", flags=re.IGNORECASE)
NUMBERED_BASE_RE = re.compile(r"^(?P<label>.+?)_\d+_(?:PEIS|CA|CP)_.+$", flags=re.IGNORECASE)
TEMP_PREFIX_RE = re.compile(r"^(?P<temp>\d+(?:\.\d+)?)\s*[Cc]?(?:\b|_)", flags=re.IGNORECASE)


def get_base_name(filepath: Path) -> str:
    fname = filepath.stem
    microprobe_match = re.match(
        r"^(?:Pre_stabilization_)?(?P<label>.+?)_(?:PEIS|CA)_\d{8}_\d{6}$",
        fname,
        flags=re.IGNORECASE,
    )
    if microprobe_match:
        return microprobe_match.group("label").strip()
    numbered = NUMBERED_BASE_RE.match(fname)
    if numbered:
        return numbered.group("label").strip()
    parts = re.split(r"_\d+_", fname)
    return parts[0].strip() if len(parts) > 1 else fname.strip()


def get_file_type(filepath: Path) -> Optional[str]:
    fname = filepath.name.upper()
    if fname.startswith("PRE_STABILIZATION_") and "_CA_" in fname:
        return "PRE_DC"
    if "PEIS" in fname or "EIS" in fname:
        return "EIS"
    if "CA" in fname or "CP" in fname:
        return "DC"
    return None


def file_priority(filepath: Path) -> int:
    ext = filepath.suffix.lower()
    priority_map = {
        ".mpr": 5,
        ".mpt": 4,
        ".txt": 3,
        ".csv": 2,
        ".dat": 1,
    }
    return priority_map.get(ext, 0)


def parse_voltage_from_label(label: str) -> Optional[float]:
    match = VOLT_RE.search(label)
    if not match:
        return None
    value = float(match.group("value"))
    if match.group("sign") == "-":
        value = -value
    return value


def parse_temperature_from_label(label: str) -> Optional[float]:
    match = TEMP_PREFIX_RE.match(label.strip())
    if not match:
        return None
    try:
        return float(match.group("temp"))
    except Exception:
        return None


def collect_latest_pairs(folder: Path) -> List[Dict]:
    pairs: Dict[str, Dict[str, Path]] = {}
    microprobe_pairs: Dict[str, Dict[str, Dict[str, Path]]] = {}

    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".txt", ".csv", ".dat", ".mpt", ".mpr"}:
            continue

        pre_match = PRE_RE.match(path.name)
        if pre_match:
            label = pre_match.group("label")
            stamp = pre_match.group("stamp")
            microprobe_pairs.setdefault(label, {}).setdefault(stamp, {})["pre_ca"] = path
            continue

        match = PAIR_RE.match(path.name)
        if not match:
            label = get_base_name(path)
            ftype = get_file_type(path)
            if not ftype:
                continue
            existing = pairs.setdefault(label, {}).get(ftype)
            if existing is None or file_priority(path) > file_priority(existing):
                pairs[label][ftype] = path
            continue

        label = match.group("label")
        stamp = match.group("stamp")
        kind = match.group("kind").lower()
        slot = "post_ca" if kind == "ca" else "peis"
        microprobe_pairs.setdefault(label, {}).setdefault(stamp, {})[slot] = path

    selected: List[Dict] = []
    for label, by_stamp in microprobe_pairs.items():
        valid = [
            (stamp, files)
            for stamp, files in by_stamp.items()
            if "post_ca" in files and "peis" in files
        ]
        if not valid:
            continue
        stamp, files = sorted(valid, key=lambda item: item[0])[-1]
        selected.append(
            {
                "label": label,
                "timestamp": stamp,
                "pre_ca_path": files.get("pre_ca"),
                "post_ca_path": files["post_ca"],
                "peis_path": files["peis"],
            }
        )

    for label, files in pairs.items():
        if "DC" not in files or "EIS" not in files:
            continue
        if any(item["label"] == label for item in selected):
            continue
        timestamp = max(
            files["DC"].stat().st_mtime,
            files["EIS"].stat().st_mtime,
        )
        selected.append(
            {
                "label": label,
                "timestamp": f"{int(timestamp)}",
                "pre_ca_path": None,
                "post_ca_path": files["DC"],
                "peis_path": files["EIS"],
            }
        )

    return sorted(selected, key=lambda item: item["timestamp"])


def load_actual_measurement_summary(pair: Dict) -> Dict:
    LD = _load_backend_loader()
    dc_data = LD.load_dc_from_path(
        pair["post_ca_path"],
        current_in_mA=True,
        CA_step_only=False,
        trim_start_time=None,
        current_scale_factor=1.0,
        auto_trim=True,
    )
    eis_data = LD.load_eis_from_path(pair["peis_path"])
    return {
        "actual_cp_duration_s": float(dc_data[-1, 0] - dc_data[0, 0]) if len(dc_data) >= 2 else None,
        "actual_peis_lowest_freq_hz": float(eis_data[:, 0].min()) if len(eis_data) else None,
        "actual_peis_highest_freq_hz": float(eis_data[:, 0].max()) if len(eis_data) else None,
        "actual_peis_npts": int(len(eis_data)),
    }


def simulate_folder(folder: Path) -> Dict:
    pairs = collect_latest_pairs(folder)
    engine = AdaptiveMeasurementEngine(
        engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=120.0)
    )

    sequence_summary = []

    try:
        for idx, pair in enumerate(pairs, start=1):
            measured = load_actual_measurement_summary(pair)
            point_id = f"pt{idx:03d}"
            metadata = PointMetadata(
                point_id=point_id,
                label=pair["label"],
                electrode_id=1,
                temperature_c=parse_temperature_from_label(pair["label"]),
                gas_a_sccm=None,
                gas_b_sccm=None,
                voltage_v=parse_voltage_from_label(pair["label"]),
                sequence_index=idx,
            )
            measurement = MeasurementExecution(
                measurement_mode="rapid_eis",
                peis_lowest_freq_hz=measured["actual_peis_lowest_freq_hz"],
                cp_duration_s=measured["actual_cp_duration_s"],
                actual_peis_high_freq_hz=measured["actual_peis_highest_freq_hz"],
                actual_peis_npts=measured["actual_peis_npts"],
            )
            files = MeasurementFiles(
                pre_ca_path=None if pair["pre_ca_path"] is None else str(pair["pre_ca_path"]),
                peis_path=str(pair["peis_path"]),
                post_ca_path=str(pair["post_ca_path"]),
            )
            engine.register_point(metadata, measurement, files)
            engine.start_analysis(point_id)
            finalized = engine.finalize_completed_point(point_id, timeout_s=120.0)

            step_summary = {
                "point_id": point_id,
                "label": pair["label"],
                "timestamp": pair["timestamp"],
                "actual_measurement": measured,
                "analysis_result": finalized["analysis_result"],
            }

            if idx < len(pairs):
                next_pair = pairs[idx]
                next_metadata = PointMetadata(
                    point_id=f"pt{idx+1:03d}",
                    label=next_pair["label"],
                    electrode_id=1,
                    temperature_c=parse_temperature_from_label(next_pair["label"]),
                    gas_a_sccm=None,
                    gas_b_sccm=None,
                    voltage_v=parse_voltage_from_label(next_pair["label"]),
                    sequence_index=idx + 1,
                )
                recommendation = engine.recommend_next_point(next_metadata)
                next_actual = load_actual_measurement_summary(next_pair)
                step_summary["next_point_recommendation"] = recommendation.to_dict()
                step_summary["next_actual_measurement"] = next_actual
                step_summary["recommendation_vs_actual_next"] = {
                    "recommended_mode": recommendation.measurement_mode,
                    "actual_mode": "rapid_eis",
                    "recommended_peis_lowest_freq_hz": recommendation.peis_lowest_freq_hz,
                    "actual_next_peis_lowest_freq_hz": next_actual["actual_peis_lowest_freq_hz"],
                    "recommended_cp_duration_s": recommendation.cp_duration_s,
                    "actual_next_cp_duration_s": next_actual["actual_cp_duration_s"],
                    "hybrid_unnecessary": recommendation.hybrid_unnecessary,
                    "analysis_selection_reason": recommendation.analysis_selection_reason,
                    "peis_only_selection_reason": recommendation.peis_only_selection_reason,
                }
            sequence_summary.append(step_summary)

        return {
            "input_folder": str(folder),
            "pair_selection": "latest complete CA/PEIS pair per label, sorted by timestamp",
            "points": sequence_summary,
            "engine_state": engine.export_state(),
        }
    finally:
        engine.shutdown()


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT_DIR
    result = simulate_folder(folder)
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DEFAULT_OUTPUT_DIR / f"{folder.name}_adaptive_sequence.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
