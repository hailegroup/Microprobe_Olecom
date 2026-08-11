# -*- coding: utf-8 -*-
"""
Prototype policy helpers for adaptive measurement planning.

This module stays inside the analysis project for now.  It is meant to test
whether the analysis outputs can drive next-point measurement choices before
any GUI/runtime integration happens.

Core design rules
-----------------
1. Data completeness is a hard constraint.
2. Speed reduction is allowed only after a conservative margin is preserved.
3. Same-electrode history is preferred over blindly copying the immediately
   previous point from a different electrode.
4. Trend information is used asymmetrically:
   - slower / more demanding trends are reflected more strongly
   - faster / less demanding trends are reflected only partially
"""

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class PolicySettings:
    peis_conservative_factor: float = 0.85
    cp_conservative_factor: float = 1.20
    rapid_switch_lf_threshold_hz: float = 0.01

    same_electrode_cp_margin: float = 1.05
    same_electrode_peis_margin: float = 0.95

    trend_slowdown_weight: float = 0.80
    trend_speedup_weight: float = 0.30
    max_cp_scale_from_trend: float = 1.60
    min_cp_scale_from_trend: float = 0.85
    min_peis_scale_from_trend: float = 0.55
    max_peis_scale_from_trend: float = 1.15

    first_pass_peis_lowest_freq_hz: float = 0.05
    first_pass_cp_duration_s: float = 120.0
    remeasure_cp_multiplier: float = 1.35
    remeasure_peis_multiplier: float = 0.75


def _to_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _is_sufficient(record: Dict) -> bool:
    return bool(record.get("data_sufficient", True))


def _gas_b_fraction(record: Dict) -> float:
    gas_a = _to_float(record.get("gas_a_sccm"), 0.0) or 0.0
    gas_b = _to_float(record.get("gas_b_sccm"), 0.0) or 0.0
    total = gas_a + gas_b
    if total <= 0:
        return 0.0
    return gas_b / total


def _same_condition(a: Dict, b: Dict) -> bool:
    return (
        _to_float(a.get("temperature_c")) == _to_float(b.get("temperature_c"))
        and _to_float(a.get("gas_a_sccm")) == _to_float(b.get("gas_a_sccm"))
        and _to_float(a.get("gas_b_sccm")) == _to_float(b.get("gas_b_sccm"))
    )


def _fallback_cp_value(record: Dict) -> Optional[float]:
    return _to_float(
        record.get("optimized_cp_duration_s"),
        _to_float(record.get("cp_duration_s")),
    )


def _valid_history(records: Iterable[Dict]) -> List[Dict]:
    out = []
    for rec in records:
        if not _is_sufficient(rec):
            continue
        peis_only = bool(rec.get("peis_only_sufficient", False))
        if _to_float(rec.get("optimized_peis_lowest_freq_hz")) is None:
            continue
        if _to_float(rec.get("optimized_cp_duration_s")) is None and not peis_only:
            continue
        out.append(rec)
    return out


def _select_seed_record(current_point: Dict, history: Iterable[Dict]) -> Tuple[Optional[Dict], str]:
    valid = _valid_history(history)
    if not valid:
        return None, "no_history"

    current_electrode = current_point.get("electrode_id")

    same_condition_same_electrode = [
        rec for rec in valid
        if rec.get("electrode_id") == current_electrode and _same_condition(rec, current_point)
    ]
    if same_condition_same_electrode:
        return same_condition_same_electrode[-1], "same_condition_same_electrode"

    same_electrode = [
        rec for rec in valid
        if rec.get("electrode_id") == current_electrode
    ]
    if same_electrode:
        return same_electrode[-1], "same_electrode_previous_condition"

    same_condition = [
        rec for rec in valid
        if _same_condition(rec, current_point)
    ]
    if same_condition:
        return same_condition[-1], "same_condition_other_electrode"

    return valid[-1], "latest_available"


def _fit_linear_trend(points: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    if len(points) < 2:
        return None
    x1, y1 = points[-2]
    x2, y2 = points[-1]
    if x2 == x1:
        return None
    slope = (y2 - y1) / (x2 - x1)
    intercept = y2 - slope * x2
    return slope, intercept


def _predict_from_trend(points: List[Tuple[float, float]], x_target: float) -> Optional[float]:
    coeffs = _fit_linear_trend(points)
    if coeffs is None:
        return None
    slope, intercept = coeffs
    return slope * x_target + intercept


def _collect_same_condition_electrode_points(current_point: Dict, history: Iterable[Dict], field: str) -> List[Tuple[float, float]]:
    points = []
    for rec in _valid_history(history):
        if not _same_condition(rec, current_point):
            continue
        electrode = _to_float(rec.get("electrode_id"))
        value = _to_float(rec.get(field))
        if electrode is None or value is None:
            continue
        points.append((electrode, value))
    return sorted(points)


def _collect_same_electrode_gas_points(current_point: Dict, history: Iterable[Dict], field: str) -> List[Tuple[float, float]]:
    points = []
    current_electrode = current_point.get("electrode_id")
    current_temp = _to_float(current_point.get("temperature_c"))
    for rec in _valid_history(history):
        if rec.get("electrode_id") != current_electrode:
            continue
        if _to_float(rec.get("temperature_c")) != current_temp:
            continue
        value = _to_float(rec.get(field))
        if value is None:
            continue
        points.append((_gas_b_fraction(rec), value))
    return sorted(points)


def _collect_same_electrode_temp_points(current_point: Dict, history: Iterable[Dict], field: str) -> List[Tuple[float, float]]:
    points = []
    current_electrode = current_point.get("electrode_id")
    current_gas_a = _to_float(current_point.get("gas_a_sccm"))
    current_gas_b = _to_float(current_point.get("gas_b_sccm"))
    for rec in _valid_history(history):
        if rec.get("electrode_id") != current_electrode:
            continue
        if _to_float(rec.get("gas_a_sccm")) != current_gas_a:
            continue
        if _to_float(rec.get("gas_b_sccm")) != current_gas_b:
            continue
        temp = _to_float(rec.get("temperature_c"))
        value = _to_float(rec.get(field))
        if temp is None or value is None:
            continue
        points.append((temp, value))
    return sorted(points)


def _blend_conservative(base_value: float, predicted_value: Optional[float], *,
                        slower_is_larger: bool, settings: PolicySettings,
                        min_scale: float, max_scale: float) -> Tuple[float, Optional[str]]:
    if predicted_value is None or predicted_value <= 0:
        return base_value, None

    if slower_is_larger:
        if predicted_value > base_value:
            delta = predicted_value - base_value
            blended = base_value + settings.trend_slowdown_weight * delta
            return min(blended, base_value * max_scale), "slower trend applied strongly"
        if predicted_value < base_value:
            delta = base_value - predicted_value
            blended = base_value - settings.trend_speedup_weight * delta
            return max(blended, base_value * min_scale), "faster trend applied gently"
    else:
        if predicted_value < base_value:
            delta = base_value - predicted_value
            blended = base_value - settings.trend_slowdown_weight * delta
            return max(blended, base_value * min_scale), "slower trend applied strongly"
        if predicted_value > base_value:
            delta = predicted_value - base_value
            blended = base_value + settings.trend_speedup_weight * delta
            return min(blended, base_value * max_scale), "faster trend applied gently"

    return base_value, None


def choose_measurement_mode(
    analysis_recommendation: Optional[Dict],
    settings: PolicySettings,
) -> str:
    if analysis_recommendation and analysis_recommendation.get("peis_only_sufficient") is True:
        return "normal_eis"

    rec_lf = None if analysis_recommendation is None else _to_float(
        analysis_recommendation.get("recommended_peis_lowest_freq_hz")
    )
    if rec_lf is not None and rec_lf < settings.rapid_switch_lf_threshold_hz:
        return "rapid_eis"

    return "rapid_eis"


def assess_measurement_sufficiency(
    analysis_recommendation: Optional[Dict],
    actual_parameters: Optional[Dict] = None,
) -> Dict:
    """
    Decide whether the current measurement should be considered sufficient.

    The analysis project is still maturing, so this function is intentionally
    permissive unless it sees explicit evidence that the current run was too
    short or that the caller already marked the result insufficient.
    """
    reasons = []

    if actual_parameters and actual_parameters.get("data_sufficient") is False:
        reasons.append("explicit insufficiency flag from caller")

    if analysis_recommendation:
        explicit = analysis_recommendation.get("data_sufficient")
        if explicit is False:
            reasons.append("analysis flagged the dataset as insufficient")

        required_cp = _to_float(analysis_recommendation.get("recommended_peis_conservative_cp_time_s"))
        actual_cp = None if actual_parameters is None else _to_float(actual_parameters.get("cp_duration_s"))
        if required_cp is not None and actual_cp is not None and actual_cp < required_cp:
            reasons.append("actual CP duration is shorter than the conservative recommended duration")

        required_lf = _to_float(analysis_recommendation.get("recommended_peis_lowest_freq_hz"))
        actual_lf = None if actual_parameters is None else _to_float(actual_parameters.get("peis_lowest_freq_hz"))
        if required_lf is not None and actual_lf is not None and actual_lf > required_lf:
            reasons.append("actual PEIS lowest frequency is higher than the recommended LF cutoff")

    return {
        "data_sufficient": len(reasons) == 0,
        "reasons": reasons,
    }


def suggest_next_parameters(
    current_point: Dict,
    history: Iterable[Dict],
    analysis_recommendation: Optional[Dict] = None,
    settings: Optional[PolicySettings] = None,
) -> Dict:
    """
    Return a conservative, trend-aware prototype recommendation for the next point.
    """
    settings = settings or PolicySettings()
    notes = []
    mode = choose_measurement_mode(analysis_recommendation, settings)

    if analysis_recommendation is None:
        rec_lf = None
    else:
        normal_lf = _to_float(analysis_recommendation.get("recommended_normal_peis_lowest_freq_hz"))
        general_lf = _to_float(analysis_recommendation.get("recommended_peis_lowest_freq_hz"))
        rec_lf = normal_lf if mode == "normal_eis" and normal_lf is not None else general_lf
    rec_cp = None if analysis_recommendation is None else _to_float(
        analysis_recommendation.get("recommended_peis_conservative_cp_time_s")
    )

    base_peis_lf = None if rec_lf is None else rec_lf * settings.peis_conservative_factor
    base_cp = None if rec_cp is None else rec_cp * settings.cp_conservative_factor
    if base_peis_lf is not None:
        notes.append("analysis recommendation used with conservative lower-frequency margin")
    if base_cp is not None:
        notes.append("analysis recommendation used with conservative longer-duration margin")

    seed_record, seed_source = _select_seed_record(current_point, history)
    if seed_record is not None:
        seed_peis = _to_float(seed_record.get("optimized_peis_lowest_freq_hz"))
        seed_cp = _fallback_cp_value(seed_record)
        if seed_peis is not None:
            seed_peis *= settings.same_electrode_peis_margin
            base_peis_lf = seed_peis if base_peis_lf is None else min(base_peis_lf, seed_peis)
        if seed_cp is not None:
            seed_cp *= settings.same_electrode_cp_margin
            base_cp = seed_cp if base_cp is None else max(base_cp, seed_cp)
        notes.append(f"seeded from {seed_source}")

    if base_peis_lf is None:
        base_peis_lf = settings.first_pass_peis_lowest_freq_hz
        notes.append("used conservative first-pass PEIS setting because no validated recommendation was available")
    if base_cp is None:
        base_cp = settings.first_pass_cp_duration_s
        notes.append("used conservative first-pass CP setting because no validated recommendation was available")

    current_electrode = _to_float(current_point.get("electrode_id"))
    electrode_cp_points = _collect_same_condition_electrode_points(current_point, history, "optimized_cp_duration_s")
    electrode_peis_points = _collect_same_condition_electrode_points(current_point, history, "optimized_peis_lowest_freq_hz")
    if current_electrode is not None:
        pred_cp = _predict_from_trend(electrode_cp_points, current_electrode)
        pred_peis = _predict_from_trend(electrode_peis_points, current_electrode)
        base_cp, note = _blend_conservative(
            base_cp, pred_cp,
            slower_is_larger=True,
            settings=settings,
            min_scale=settings.min_cp_scale_from_trend,
            max_scale=settings.max_cp_scale_from_trend,
        )
        if note:
            notes.append(f"same-condition electrode CP trend: {note}")
        base_peis_lf, note = _blend_conservative(
            base_peis_lf, pred_peis,
            slower_is_larger=False,
            settings=settings,
            min_scale=settings.min_peis_scale_from_trend,
            max_scale=settings.max_peis_scale_from_trend,
        )
        if note:
            notes.append(f"same-condition electrode PEIS trend: {note}")

    current_gas_fraction = _gas_b_fraction(current_point)
    gas_cp_points = _collect_same_electrode_gas_points(current_point, history, "optimized_cp_duration_s")
    gas_peis_points = _collect_same_electrode_gas_points(current_point, history, "optimized_peis_lowest_freq_hz")
    pred_cp = _predict_from_trend(gas_cp_points, current_gas_fraction)
    pred_peis = _predict_from_trend(gas_peis_points, current_gas_fraction)
    base_cp, note = _blend_conservative(
        base_cp, pred_cp,
        slower_is_larger=True,
        settings=settings,
        min_scale=settings.min_cp_scale_from_trend,
        max_scale=settings.max_cp_scale_from_trend,
    )
    if note:
        notes.append(f"same-electrode gas CP trend: {note}")
    base_peis_lf, note = _blend_conservative(
        base_peis_lf, pred_peis,
        slower_is_larger=False,
        settings=settings,
        min_scale=settings.min_peis_scale_from_trend,
        max_scale=settings.max_peis_scale_from_trend,
    )
    if note:
        notes.append(f"same-electrode gas PEIS trend: {note}")

    current_temp = _to_float(current_point.get("temperature_c"))
    temp_cp_points = _collect_same_electrode_temp_points(current_point, history, "optimized_cp_duration_s")
    temp_peis_points = _collect_same_electrode_temp_points(current_point, history, "optimized_peis_lowest_freq_hz")
    if current_temp is not None:
        pred_cp = _predict_from_trend(temp_cp_points, current_temp)
        pred_peis = _predict_from_trend(temp_peis_points, current_temp)
        base_cp, note = _blend_conservative(
            base_cp, pred_cp,
            slower_is_larger=True,
            settings=settings,
            min_scale=settings.min_cp_scale_from_trend,
            max_scale=settings.max_cp_scale_from_trend,
        )
        if note:
            notes.append(f"same-electrode temperature CP trend: {note}")
        base_peis_lf, note = _blend_conservative(
            base_peis_lf, pred_peis,
            slower_is_larger=False,
            settings=settings,
            min_scale=settings.min_peis_scale_from_trend,
            max_scale=settings.max_peis_scale_from_trend,
        )
        if note:
            notes.append(f"same-electrode temperature PEIS trend: {note}")

    if mode == "normal_eis" and rec_cp is None:
        base_cp = 0.0
        notes.append("PEIS-only / normal-EIS mode selected, so hybrid CP duration is not needed for the next point")

    return {
        "measurement_mode": mode,
        "peis_lowest_freq_hz": float(base_peis_lf),
        "cp_duration_s": float(base_cp),
        "seed_source": seed_source,
        "notes": notes,
    }


def plan_remeasurement(
    current_parameters: Dict,
    sufficiency_result: Dict,
    settings: Optional[PolicySettings] = None,
) -> Dict:
    settings = settings or PolicySettings()
    next_cp = max(
        _to_float(current_parameters.get("cp_duration_s"), settings.first_pass_cp_duration_s)
        * settings.remeasure_cp_multiplier,
        settings.first_pass_cp_duration_s,
    )
    next_lf = min(
        _to_float(current_parameters.get("peis_lowest_freq_hz"), settings.first_pass_peis_lowest_freq_hz)
        * settings.remeasure_peis_multiplier,
        _to_float(current_parameters.get("peis_lowest_freq_hz"), settings.first_pass_peis_lowest_freq_hz),
    )
    return {
        "should_remeasure": True,
        "measurement_mode": current_parameters.get("measurement_mode", "rapid_eis"),
        "peis_lowest_freq_hz": float(next_lf),
        "cp_duration_s": float(next_cp),
        "reasons": list(sufficiency_result.get("reasons", [])),
        "notes": [
            "data judged insufficient; generated a more conservative retry plan",
            "remeasurement increases CP duration and pushes PEIS to a lower frequency",
        ],
    }


def propose_measurement_action(
    current_point: Dict,
    history: Iterable[Dict],
    analysis_recommendation: Optional[Dict] = None,
    current_measurement: Optional[Dict] = None,
    settings: Optional[PolicySettings] = None,
) -> Dict:
    """
    High-level helper that either suggests the next point parameters or,
    if the current measurement was insufficient, returns a remeasurement plan.
    """
    settings = settings or PolicySettings()

    if current_measurement is not None:
        sufficiency = assess_measurement_sufficiency(
            analysis_recommendation=analysis_recommendation,
            actual_parameters=current_measurement,
        )
        if not sufficiency["data_sufficient"]:
            retry = plan_remeasurement(current_measurement, sufficiency, settings=settings)
            retry["action"] = "remeasure_same_point"
            return retry

    next_plan = suggest_next_parameters(
        current_point=current_point,
        history=history,
        analysis_recommendation=analysis_recommendation,
        settings=settings,
    )
    next_plan["action"] = "measure_next_point"
    return next_plan


def export_policy_settings(settings: Optional[PolicySettings] = None) -> Dict:
    return asdict(settings or PolicySettings())


if __name__ == "__main__":
    demo_history = [
        {
            "electrode_id": 1,
            "temperature_c": 300,
            "gas_a_sccm": 10,
            "gas_b_sccm": 10,
            "optimized_peis_lowest_freq_hz": 0.12,
            "optimized_cp_duration_s": 40,
            "data_sufficient": True,
        },
        {
            "electrode_id": 2,
            "temperature_c": 280,
            "gas_a_sccm": 10,
            "gas_b_sccm": 10,
            "optimized_peis_lowest_freq_hz": 0.08,
            "optimized_cp_duration_s": 55,
            "data_sufficient": True,
        },
        {
            "electrode_id": 2,
            "temperature_c": 300,
            "gas_a_sccm": 10,
            "gas_b_sccm": 30,
            "optimized_peis_lowest_freq_hz": 0.07,
            "optimized_cp_duration_s": 60,
            "data_sufficient": True,
        },
        {
            "electrode_id": 1,
            "temperature_c": 300,
            "gas_a_sccm": 10,
            "gas_b_sccm": 20,
            "optimized_peis_lowest_freq_hz": 0.10,
            "optimized_cp_duration_s": 45,
            "data_sufficient": True,
        },
    ]

    demo_current = {
        "electrode_id": 2,
        "temperature_c": 300,
        "gas_a_sccm": 10,
        "gas_b_sccm": 20,
    }

    demo_analysis = {
        "recommended_peis_lowest_freq_hz": 0.10,
        "recommended_peis_conservative_cp_time_s": 42,
        "peis_only_sufficient": False,
    }

    print("Next-point proposal")
    print(
        propose_measurement_action(
            current_point=demo_current,
            history=demo_history,
            analysis_recommendation=demo_analysis,
        )
    )

    print("\nRe-measurement proposal")
    print(
        propose_measurement_action(
            current_point=demo_current,
            history=demo_history,
            analysis_recommendation=demo_analysis,
            current_measurement={
                "measurement_mode": "rapid_eis",
                "peis_lowest_freq_hz": 0.09,
                "cp_duration_s": 35,
                "data_sufficient": False,
            },
        )
    )
