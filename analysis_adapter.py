# -*- coding: utf-8 -*-
"""
Fast machine-readable bridge to the copied CP/CA-to-EIS analysis backend.

This adapter now mirrors the latest optimized-analysis pipeline more closely so
the internal adaptive engine can reason about:
    - PEIS-only sufficiency
    - CP saturation guard results
    - optimized PEIS/CP crossover recommendations
    - post-check reasons when hybrid data is still insufficient
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
import warnings
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

from adaptive_types import AnalysisResult


BASE_DIR = Path(__file__).resolve().parent
ANALYSIS_DIR = BASE_DIR / "Analysis_Convert_CP_to_EIS"


@dataclass
class AnalysisBackendSettings:
    current_in_mA: Optional[bool] = None
    current_scale_factor: float = 1.0
    ca_step_only: bool = False
    trim_start_time: Optional[float] = None
    auto_trim: bool = True
    auto_trim_kwargs: Optional[Dict] = None
    rapid_switch_lf_threshold_hz: float = 0.01
    duration_guard_tolerance_s: float = 1.0

    def __post_init__(self):
        if self.auto_trim_kwargs is None:
            self.auto_trim_kwargs = {
                "voltage_step_sigma": 8.0,
                "smooth_window_points": 11,
                "stability_window_s": 2.0,
                "sustain_window_s": 3.0,
                "std_factor": 2.5,
            }


def _load_analysis_modules():
    os.environ.setdefault("MPLBACKEND", "Agg")
    if str(ANALYSIS_DIR) not in sys.path:
        sys.path.insert(0, str(ANALYSIS_DIR))
    import compare_full_vs_optimized_fit as OPT  # type: ignore
    import Load_CP_Data as LD  # type: ignore
    return OPT, LD


def _to_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _infer_current_in_mA_from_path(dc_path, explicit_value: Optional[bool]) -> bool:
    if explicit_value is not None:
        return bool(explicit_value)

    suffix = Path(dc_path).suffix.lower()
    if suffix in {".mpr", ".mpt"}:
        # Trusted BioLogic batch workflow uses current_in_mA=True for native files.
        return True

    if suffix in {".txt", ".csv", ".dat"}:
        try:
            with open(dc_path, "r", encoding="utf-8", errors="ignore") as handle:
                for raw_line in handle:
                    line = raw_line.strip().lower()
                    if not line:
                        continue
                    if "i/a" in line or "current/a" in line:
                        return False
                    if "i/ma" in line or "current/ma" in line:
                        return True
                    break
        except Exception:
            pass

    # Fall back to the long-trusted text/MPR batch setting when the file
    # does not advertise its units explicitly.
    return True


def _resolve_backend_settings(
    settings: AnalysisBackendSettings,
    *,
    dc_path: Optional[str] = None,
) -> AnalysisBackendSettings:
    if dc_path:
        return replace(
            settings,
            current_in_mA=_infer_current_in_mA_from_path(dc_path, settings.current_in_mA),
        )
    if settings.current_in_mA is None:
        return replace(settings, current_in_mA=True)
    return settings


def _estimate_confidence(
    *,
    peis_only_sufficient: bool,
    data_sufficient: bool,
    quality_margin: Optional[float],
    cp_saturation_reached: Optional[bool],
    agreement_rel_err: Optional[float],
) -> float:
    score = 0.45
    if data_sufficient:
        score += 0.20
    if peis_only_sufficient:
        score += 0.15
    if cp_saturation_reached is True:
        score += 0.10
    if quality_margin is not None:
        score += max(0.0, min(float(quality_margin) / 0.08, 1.0)) * 0.08
    if agreement_rel_err is not None:
        score += max(0.0, 1.0 - min(float(agreement_rel_err) / 0.2, 1.0)) * 0.07
    if not data_sufficient:
        score -= 0.15
    return round(max(0.0, min(score, 1.0)), 3)


def _load_actual_dc_duration_s(dc_path, settings: AnalysisBackendSettings) -> Optional[float]:
    settings = _resolve_backend_settings(settings, dc_path=dc_path)
    _, LD = _load_analysis_modules()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        dc_data = LD.load_dc_from_path(
            dc_path,
            current_in_mA=settings.current_in_mA,
            CA_step_only=settings.ca_step_only,
            trim_start_time=settings.trim_start_time,
            current_scale_factor=settings.current_scale_factor,
            auto_trim=settings.auto_trim,
            auto_trim_kwargs=settings.auto_trim_kwargs,
        )
    if dc_data is None or len(dc_data) < 2:
        return None
    return float(dc_data[-1, 0] - dc_data[0, 0])


def _analyze_peis_only_measurement(
    eis_path,
    *,
    settings: AnalysisBackendSettings,
    sample_name: Optional[str] = None,
) -> AnalysisResult:
    settings = _resolve_backend_settings(settings)
    OPT, LD = _load_analysis_modules()

    started_at = time.time()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="FigureCanvasAgg is non-interactive, and thus cannot be shown",
            category=UserWarning,
        )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            eis_data = LD.load_eis_from_path(eis_path)
            peis_only = OPT.assess_peis_only_sufficiency(
                eis_data,
                np.array([], dtype=float),
                np.array([], dtype=complex),
            )
    latency = time.time() - started_at

    recommended_lf = _to_float(peis_only.get("recommended_peis_lowest_freq_hz"))
    recommended_normal_lf = recommended_lf
    peis_only_sufficient = bool(peis_only.get("peis_only_sufficient", False))
    peis_only_selection_reason = str(peis_only.get("selection_reason", "") or "")
    agreement_rel_err = _to_float(peis_only.get("agreement_rel_err"))
    quality_margin = None
    data_sufficient = peis_only_sufficient

    reasons = []
    if peis_only_sufficient:
        reasons.append("PEIS-only follow-up still supports normal-EIS and refreshed the LF cutoff")
    else:
        reasons.append("PEIS-only follow-up did not prove LF saturation, so a hybrid refresh is still needed")

    confidence = _estimate_confidence(
        peis_only_sufficient=peis_only_sufficient,
        data_sufficient=data_sufficient,
        quality_margin=quality_margin,
        cp_saturation_reached=None,
        agreement_rel_err=agreement_rel_err if agreement_rel_err is not None and np.isfinite(agreement_rel_err) else None,
    )

    note_list = [
        "PEIS-only follow-up analysis path was used because no CA/CP file was present",
        f"sample_name={sample_name or Path(eis_path).stem}",
    ]
    if recommended_lf is not None:
        note_list.append(f"recommended_peis_lowest_freq_hz={recommended_lf:.6g}")
    if peis_only_selection_reason:
        note_list.append(f"peis_only_selection_reason={peis_only_selection_reason}")
    if agreement_rel_err is not None and np.isfinite(agreement_rel_err):
        note_list.append(f"peis_only_agreement_rel_err={agreement_rel_err:.6g}")

    return AnalysisResult(
        recommended_peis_lowest_freq_hz=recommended_lf,
        recommended_normal_peis_lowest_freq_hz=recommended_normal_lf,
        recommended_peis_conservative_cp_time_s=None,
        data_sufficient=data_sufficient,
        peis_only_sufficient=peis_only_sufficient,
        confidence=confidence,
        reason="; ".join(reasons),
        analysis_latency_s=latency,
        source="peis_only_followup",
        provisional=True,
        notes=note_list,
        recommended_cp_highest_freq_hz=None,
        minimum_valid_cp_duration_s=None,
        saturation_recommended_cp_duration_s=None,
        optimization_selection_reason="peis_only_followup",
        peis_only_selection_reason=peis_only_selection_reason,
        cp_saturation_reached=None,
        cp_saturation_recommended_duration_s=None,
        cp_saturation_selection_reason=None,
        postcheck_reason=None if peis_only_sufficient else "peis_only_followup_needs_hybrid_refresh",
        agreement_rel_err=agreement_rel_err if agreement_rel_err is not None and np.isfinite(agreement_rel_err) else None,
        actual_cp_duration_s=None,
    )


def _build_visual_overlay_payload(analysis: Dict) -> Dict:
    optimized_recovery = analysis.get("optimized_recovery") or {}
    full_recovery = analysis.get("full_recovery") or {}

    filtered_f = np.asarray(optimized_recovery.get("filtered_f", np.array([], dtype=float)), dtype=float).reshape(-1)
    recovered_z = np.asarray(optimized_recovery.get("recovered_z", np.array([], dtype=complex))).reshape(-1)
    peis_data = np.asarray(full_recovery.get("eis_data", np.array([], dtype=float)), dtype=float)

    peis_points = []
    if peis_data.ndim == 2 and peis_data.shape[1] >= 3 and len(peis_data):
        peis_order = np.argsort(peis_data[:, 0])[::-1]
        peis_points = [
            # The downstream overlay expects Nyquist payloads in (Zre, -Zim).
            # `load_eis_from_path()` returns the conventional Im(Z) column, so
            # flip the sign here to match the recovered FFT payload.
            (float(peis_data[idx, 1]), float(-peis_data[idx, 2]))
            for idx in peis_order
        ]

    recovered_points = []
    if filtered_f.size and recovered_z.size:
        rec_order = np.argsort(filtered_f)[::-1]
        recovered_points = [
            (float(np.real(recovered_z[idx])), float(-np.imag(recovered_z[idx])))
            for idx in rec_order
        ]

    return {
        "peis_nyquist_points": peis_points,
        "recovered_fft_nyquist_points": recovered_points,
        "has_recovered_fft_overlay": bool(recovered_points),
    }


def _build_analysis_result_from_optimized_output(
    analysis: Dict,
    *,
    settings: AnalysisBackendSettings,
    dc_path,
    eis_path,
    sample_name: Optional[str] = None,
    latency_override: Optional[float] = None,
) -> AnalysisResult:
    fast = analysis.get("fast_parameter_summary", {})
    peis_only = analysis.get("peis_only_assessment", {})
    cp_saturation = analysis.get("cp_saturation", {})
    timing = analysis.get("timing", {})

    recommended_lf = _to_float(analysis.get("recommended_peis_lowest_freq_hz"))
    recommended_normal_lf = _to_float(
        analysis.get("recommended_normal_peis_lowest_freq_hz"),
        recommended_lf,
    )
    recommended_cp = _to_float(analysis.get("recommended_min_cp_duration_s"))
    recommended_cp_high = _to_float(analysis.get("recommended_cp_highest_freq_hz"))
    minimum_valid_cp = _to_float(fast.get("minimum_valid_cp_duration_s"))
    saturation_recommended_cp = _to_float(fast.get("saturation_recommended_cp_duration_s"))
    quality_margin = _to_float(fast.get("quality_margin"))
    optimization_selection_reason = str(fast.get("selection_reason", "") or "")
    parameter_extraction_time_s = _to_float(
        timing.get("parameter_extraction_time_s"),
        latency_override,
    )

    peis_only_sufficient = bool(peis_only.get("peis_only_sufficient", False))
    peis_only_selection_reason = str(peis_only.get("selection_reason", "") or "")
    strong_agreement_relaxed_plateau = bool(
        peis_only.get("strong_agreement_relaxed_plateau", False)
    )
    agreement_rel_err = _to_float(peis_only.get("agreement_rel_err"))
    cp_saturation_reached = cp_saturation.get("saturation_reached")
    if cp_saturation_reached is not None:
        cp_saturation_reached = bool(cp_saturation_reached)
    cp_saturation_recommended_duration_s = _to_float(cp_saturation.get("recommended_duration_s"))
    cp_saturation_selection_reason = str(cp_saturation.get("selection_reason", "") or "")
    postcheck_reason = analysis.get("postcheck_reason")
    if postcheck_reason is not None:
        postcheck_reason = str(postcheck_reason)

    actual_cp_duration_s = _load_actual_dc_duration_s(dc_path, settings)

    reasons = []
    if recommended_lf is None:
        reasons.append("optimized analysis did not return a PEIS lowest-frequency recommendation")

    if peis_only_sufficient:
        data_sufficient = True
        if not peis_only_selection_reason:
            peis_only_selection_reason = "peis_only_sufficient"
        if recommended_normal_lf is not None:
            recommended_lf = recommended_normal_lf
    else:
        data_sufficient = True
        if recommended_cp is None:
            data_sufficient = False
            reasons.append("hybrid path was selected but no final CP duration recommendation was returned")
        if (
            recommended_cp is not None
            and actual_cp_duration_s is not None
            and recommended_cp > actual_cp_duration_s + settings.duration_guard_tolerance_s
        ):
            data_sufficient = False
            reasons.append(
                "optimized analysis wants a longer CP duration than the currently measured DC trace"
            )
        if (
            minimum_valid_cp is not None
            and actual_cp_duration_s is not None
            and minimum_valid_cp > actual_cp_duration_s + settings.duration_guard_tolerance_s
        ):
            data_sufficient = False
            reasons.append("measured CP duration is shorter than the minimum valid duration from the fast stage")
        if postcheck_reason in {
            "duration_guard_extended",
            "duration_guard_best_available",
            "cp_tail_not_saturated",
            "cp_tail_saturation_guard",
        }:
            data_sufficient = False
            reasons.append(f"post-check guard reported '{postcheck_reason}'")
        if cp_saturation_reached is False and cp_saturation_recommended_duration_s:
            if (
                actual_cp_duration_s is not None
                and cp_saturation_recommended_duration_s > actual_cp_duration_s + settings.duration_guard_tolerance_s
            ):
                data_sufficient = False
                reasons.append("CP tail saturation guard indicates the DC tail has not settled enough yet")

    if not reasons:
        if peis_only_sufficient:
            reasons.append("PEIS already appears to reach the LF saturation region, so hybrid is unnecessary")
        else:
            reasons.append("optimized hybrid recommendation passed the current CP-duration and post-check guards")

    confidence = _estimate_confidence(
        peis_only_sufficient=peis_only_sufficient,
        data_sufficient=data_sufficient,
        quality_margin=quality_margin,
        cp_saturation_reached=cp_saturation_reached,
        agreement_rel_err=agreement_rel_err,
    )

    note_list = [
        "latest optimized-analysis path from Analysis_Convert_CP_to_EIS was used",
        "analysis backend recommendations are still provisional and should keep conservative runtime margins",
        f"sample_name={sample_name or Path(eis_path).stem}",
        f"current_in_mA={bool(settings.current_in_mA)}",
    ]
    if recommended_lf is not None:
        note_list.append(f"recommended_peis_lowest_freq_hz={recommended_lf:.6g}")
    if (
        recommended_normal_lf is not None
        and recommended_lf is not None
        and abs(float(recommended_normal_lf) - float(recommended_lf)) > 1e-12
    ):
        note_list.append(f"recommended_normal_peis_lowest_freq_hz={recommended_normal_lf:.6g}")
    if recommended_cp is not None:
        note_list.append(f"recommended_min_cp_duration_s={recommended_cp:.6g}")
    if recommended_cp_high is not None:
        note_list.append(f"recommended_cp_highest_freq_hz={recommended_cp_high:.6g}")
    if actual_cp_duration_s is not None:
        note_list.append(f"actual_cp_duration_s={actual_cp_duration_s:.6g}")
    if optimization_selection_reason:
        note_list.append(f"optimization_selection_reason={optimization_selection_reason}")
    if peis_only_selection_reason:
        note_list.append(f"peis_only_selection_reason={peis_only_selection_reason}")
    if strong_agreement_relaxed_plateau:
        note_list.append("peis_only_plateau_relaxed_by_strong_endpoint_agreement")
    if cp_saturation_selection_reason:
        note_list.append(f"cp_saturation_selection_reason={cp_saturation_selection_reason}")
    if postcheck_reason:
        note_list.append(f"postcheck_reason={postcheck_reason}")

    return AnalysisResult(
        recommended_peis_lowest_freq_hz=recommended_lf,
        recommended_normal_peis_lowest_freq_hz=recommended_normal_lf,
        recommended_peis_conservative_cp_time_s=recommended_cp,
        data_sufficient=data_sufficient,
        peis_only_sufficient=peis_only_sufficient,
        confidence=confidence,
        reason="; ".join(reasons),
        analysis_latency_s=parameter_extraction_time_s,
        source="Analysis_Convert_CP_to_EIS.optimized_configuration",
        provisional=True,
        notes=note_list,
        recommended_cp_highest_freq_hz=recommended_cp_high,
        minimum_valid_cp_duration_s=minimum_valid_cp,
        saturation_recommended_cp_duration_s=saturation_recommended_cp,
        optimization_selection_reason=optimization_selection_reason or None,
        peis_only_selection_reason=peis_only_selection_reason or None,
        cp_saturation_reached=cp_saturation_reached,
        cp_saturation_recommended_duration_s=cp_saturation_recommended_duration_s,
        cp_saturation_selection_reason=cp_saturation_selection_reason or None,
        postcheck_reason=postcheck_reason,
        agreement_rel_err=agreement_rel_err,
        actual_cp_duration_s=actual_cp_duration_s,
    )


def analyze_measurement_files_with_visuals(
    dc_path,
    eis_path,
    *,
    settings: Optional[AnalysisBackendSettings] = None,
    sample_name: Optional[str] = None,
) -> Tuple[AnalysisResult, Dict]:
    settings = _resolve_backend_settings(settings or AnalysisBackendSettings(), dc_path=dc_path)
    if not dc_path:
        result = _analyze_peis_only_measurement(
            eis_path,
            settings=settings,
            sample_name=sample_name,
        )
        return result, {
            "peis_nyquist_points": [],
            "recovered_fft_nyquist_points": [],
            "has_recovered_fft_overlay": False,
        }

    OPT, _ = _load_analysis_modules()
    started_at = time.time()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="FigureCanvasAgg is non-interactive, and thus cannot be shown",
            category=UserWarning,
        )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            analysis = OPT.recommend_optimized_configuration(
                dc_path=dc_path,
                eis_path=eis_path,
                current_in_mA=settings.current_in_mA,
                current_scale_factor=settings.current_scale_factor,
                trim_start_time=settings.trim_start_time,
                auto_trim=settings.auto_trim,
                auto_trim_kwargs=settings.auto_trim_kwargs,
            )
    latency = time.time() - started_at
    result = _build_analysis_result_from_optimized_output(
        analysis,
        settings=settings,
        dc_path=dc_path,
        eis_path=eis_path,
        sample_name=sample_name,
        latency_override=latency,
    )
    visuals = _build_visual_overlay_payload(analysis)
    return result, visuals


def analyze_measurement_files(
    dc_path,
    eis_path,
    *,
    settings: Optional[AnalysisBackendSettings] = None,
    sample_name: Optional[str] = None,
) -> AnalysisResult:
    settings = _resolve_backend_settings(settings or AnalysisBackendSettings(), dc_path=dc_path)
    if not dc_path:
        return _analyze_peis_only_measurement(
            eis_path,
            settings=settings,
            sample_name=sample_name,
        )

    OPT, _ = _load_analysis_modules()

    started_at = time.time()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="FigureCanvasAgg is non-interactive, and thus cannot be shown",
            category=UserWarning,
        )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            analysis = OPT.recommend_optimized_configuration(
                dc_path=dc_path,
                eis_path=eis_path,
                current_in_mA=settings.current_in_mA,
                current_scale_factor=settings.current_scale_factor,
                trim_start_time=settings.trim_start_time,
                auto_trim=settings.auto_trim,
                auto_trim_kwargs=settings.auto_trim_kwargs,
            )
    latency = time.time() - started_at

    return _build_analysis_result_from_optimized_output(
        analysis,
        settings=settings,
        dc_path=dc_path,
        eis_path=eis_path,
        sample_name=sample_name,
        latency_override=latency,
    )


def write_analysis_result_json(result: AnalysisResult, output_path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def export_backend_settings(settings: Optional[AnalysisBackendSettings] = None) -> Dict:
    return asdict(settings or AnalysisBackendSettings())
