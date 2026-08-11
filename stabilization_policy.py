# -*- coding: utf-8 -*-
"""
Bias-stabilization logic for the pre-PEIS hold.

This module is intentionally GUI-independent so the runtime can later decide
whether to:
1. keep waiting at the current bias,
2. start PEIS immediately,
3. invalidate a PEIS that started too early and retry after a longer hold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class StabilizationSettings:
    min_hold_s: float = 20.0
    min_post_peis_hold_s: float = 20.0
    max_hold_s: float = 600.0
    extend_step_s: float = 20.0
    live_window_s: float = 15.0
    min_points_in_window: int = 20

    stable_slope_a_per_s: float = 5e-7
    stable_rel_slope_per_s: float = 0.002
    stable_std_a: float = 2e-7
    stable_rel_std: float = 0.01
    stable_range_a: float = 8e-7
    stable_rel_range: float = 0.03
    required_stable_windows: int = 2
    hold_completion_margin_factor: float = 1.10
    hold_completion_margin_s: float = 5.0
    hold_extension_multiplier: float = 1.25
    min_reduction_to_shorten_s: float = 5.0

    first_bias_hold_s: float = 120.0
    first_post_peis_hold_s: float = 20.0
    large_step_threshold_v: float = 0.15
    medium_step_threshold_v: float = 0.05
    large_step_multiplier: float = 1.40
    medium_step_multiplier: float = 1.15
    small_step_multiplier: float = 0.90

    slower_trend_weight: float = 0.75
    faster_trend_weight: float = 0.25
    min_history_scale: float = 0.80
    max_history_scale: float = 1.60
    changed_regime_history_factor: float = 1.30

    peis_retry_after_unstable: bool = True
    abort_peis_if_prehold_insufficient: bool = True
    sweep_default_order: str = "descending_abs_from_positive"


@dataclass
class StabilizationAssessment:
    stable: bool
    should_continue_hold: bool
    should_start_peis: bool
    should_abort_recent_peis: bool
    recommended_total_hold_s: float
    estimated_additional_hold_s: float
    stable_window_count: int
    metrics: Dict[str, float]
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class CompletedHoldAssessment:
    hold_kind: str
    saturated_enough: bool
    should_extend_now: bool
    can_reduce_next_time: bool
    current_hold_s: float
    estimated_stable_time_s: Optional[float]
    recommended_next_hold_s: float
    recommended_retry_hold_s: float
    stable_window_count: int
    metrics: Dict[str, float]
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


def _to_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _window_slice(time_s: np.ndarray, window_s: float) -> np.ndarray:
    if len(time_s) == 0:
        return np.zeros(0, dtype=bool)
    cutoff = float(time_s[-1]) - float(window_s)
    return time_s >= cutoff


def _fit_slope(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    x0 = x - x[0]
    denom = np.dot(x0, x0)
    if denom <= 0:
        return 0.0
    return float(np.dot(x0, y - np.mean(y)) / denom)


def analyze_pre_peis_stability(
    time_s: Sequence[float],
    current_a: Sequence[float],
    *,
    settings: Optional[StabilizationSettings] = None,
    peis_already_started: bool = False,
) -> StabilizationAssessment:
    settings = settings or StabilizationSettings()
    time_arr = np.asarray(time_s, dtype=float)
    current_arr = np.asarray(current_a, dtype=float)

    finite = np.isfinite(time_arr) & np.isfinite(current_arr)
    time_arr = time_arr[finite]
    current_arr = current_arr[finite]

    if len(time_arr) < max(settings.min_points_in_window, 3):
        hold_now = float(time_arr[-1]) if len(time_arr) else 0.0
        return StabilizationAssessment(
            stable=False,
            should_continue_hold=True,
            should_start_peis=False,
            should_abort_recent_peis=bool(peis_already_started and settings.abort_peis_if_prehold_insufficient),
            recommended_total_hold_s=max(settings.min_hold_s, hold_now + settings.extend_step_s),
            estimated_additional_hold_s=max(settings.min_hold_s, settings.extend_step_s),
            stable_window_count=0,
            metrics={"hold_now_s": hold_now},
            reasons=["too few pre-PEIS CP points to assess stabilization"],
            notes=["collect more CP data before trusting the bias as stabilized"],
        )

    windows = []
    hold_now = float(time_arr[-1] - time_arr[0])
    recent_mask = _window_slice(time_arr, settings.live_window_s)
    if np.sum(recent_mask) < settings.min_points_in_window:
        recent_mask = np.arange(len(time_arr)) >= max(0, len(time_arr) - settings.min_points_in_window)
    windows.append(recent_mask)

    older_cutoff = float(time_arr[-1]) - 2.0 * float(settings.live_window_s)
    older_mask = (time_arr >= older_cutoff) & (time_arr < (float(time_arr[-1]) - float(settings.live_window_s) / 2.0))
    if np.sum(older_mask) >= settings.min_points_in_window:
        windows.append(older_mask)

    stable_window_count = 0
    metrics = {"hold_now_s": hold_now}
    reasons: List[str] = []

    for idx, mask in enumerate(windows, start=1):
        t = time_arr[mask]
        i = current_arr[mask]
        if len(t) < 3:
            continue
        mean_abs_i = float(max(np.mean(np.abs(i)), 1e-12))
        slope = abs(_fit_slope(t, i))
        std = float(np.std(i))
        span = float(np.max(i) - np.min(i))
        total_delta = float(abs(i[0] - i[-1]))
        scale = float(max(mean_abs_i, total_delta, span, 1e-12))

        metrics[f"window_{idx}_abs_slope_a_per_s"] = slope
        metrics[f"window_{idx}_scale_a"] = scale
        metrics[f"window_{idx}_rel_slope_per_s"] = slope / scale
        metrics[f"window_{idx}_std_a"] = std
        metrics[f"window_{idx}_rel_std"] = std / scale
        metrics[f"window_{idx}_range_a"] = span
        metrics[f"window_{idx}_rel_range"] = span / scale

        slope_ok = metrics[f"window_{idx}_rel_slope_per_s"] <= settings.stable_rel_slope_per_s
        std_ok = metrics[f"window_{idx}_rel_std"] <= settings.stable_rel_std
        range_ok = metrics[f"window_{idx}_rel_range"] <= settings.stable_rel_range

        if slope_ok and std_ok and range_ok:
            stable_window_count += 1
        else:
            if not slope_ok:
                reasons.append(f"window {idx} current drift is still too large")
            if not std_ok:
                reasons.append(f"window {idx} current noise is still too large")
            if not range_ok:
                reasons.append(f"window {idx} current span is still too wide")

    stable = hold_now >= settings.min_hold_s and stable_window_count >= settings.required_stable_windows

    if stable:
        return StabilizationAssessment(
            stable=True,
            should_continue_hold=False,
            should_start_peis=True,
            should_abort_recent_peis=False,
            recommended_total_hold_s=hold_now,
            estimated_additional_hold_s=0.0,
            stable_window_count=stable_window_count,
            metrics=metrics,
            reasons=["pre-PEIS CP looks sufficiently stabilized"],
            notes=["bias drift and noise passed the current stabilization thresholds"],
        )

    recommended_total = min(
        settings.max_hold_s,
        max(settings.min_hold_s, hold_now + settings.extend_step_s),
    )
    extra = max(0.0, recommended_total - hold_now)
    if hold_now >= settings.max_hold_s:
        reasons.append("reached max pre-PEIS hold without satisfying stabilization thresholds")
    else:
        reasons.append("extend pre-PEIS CP and reassess stabilization")

    return StabilizationAssessment(
        stable=False,
        should_continue_hold=hold_now < settings.max_hold_s,
        should_start_peis=False,
        should_abort_recent_peis=bool(peis_already_started and settings.abort_peis_if_prehold_insufficient),
        recommended_total_hold_s=recommended_total,
        estimated_additional_hold_s=extra,
        stable_window_count=stable_window_count,
        metrics=metrics,
        reasons=reasons,
        notes=[
            "pre-PEIS stabilization is not yet convincing enough",
            "keep data completeness as the hard constraint before PEIS",
        ],
    )


def _evaluate_window(
    t: np.ndarray,
    i: np.ndarray,
    settings: StabilizationSettings,
) -> Tuple[bool, Dict[str, float], List[str]]:
    mean_abs_i = float(max(np.mean(np.abs(i)), 1e-12))
    slope = abs(_fit_slope(t, i))
    std = float(np.std(i))
    span = float(np.max(i) - np.min(i))
    total_delta = float(abs(i[0] - i[-1]))
    scale = float(max(mean_abs_i, total_delta, span, 1e-12))

    metrics = {
        "abs_slope_a_per_s": slope,
        "scale_a": scale,
        "rel_slope_per_s": slope / scale,
        "std_a": std,
        "rel_std": std / scale,
        "range_a": span,
        "rel_range": span / scale,
    }

    slope_ok = metrics["rel_slope_per_s"] <= settings.stable_rel_slope_per_s
    std_ok = metrics["rel_std"] <= settings.stable_rel_std
    range_ok = metrics["rel_range"] <= settings.stable_rel_range

    reasons = []
    if not slope_ok:
        reasons.append("current drift is still too large")
    if not std_ok:
        reasons.append("current noise is still too large")
    if not range_ok:
        reasons.append("current span is still too wide")

    return bool(slope_ok and std_ok and range_ok), metrics, reasons


def _estimate_stable_hold_time(
    time_arr: np.ndarray,
    current_arr: np.ndarray,
    settings: StabilizationSettings,
) -> Tuple[Optional[float], int, Dict[str, float], List[str]]:
    positive_diffs = np.diff(time_arr)
    positive_diffs = positive_diffs[np.isfinite(positive_diffs) & (positive_diffs > 0)]
    dt_med = float(np.median(positive_diffs)) if len(positive_diffs) else 1.0
    window_step_s = max(float(settings.live_window_s) * 0.5, dt_med * float(settings.min_points_in_window))

    start_eval_time = float(time_arr[0]) + float(settings.live_window_s)
    if start_eval_time >= float(time_arr[-1]):
        return None, 0, {}, ["hold trace is too short to evaluate completion robustly"]

    end_times = np.arange(start_eval_time, float(time_arr[-1]) + 1e-12, window_step_s)
    if len(end_times) == 0 or end_times[-1] < float(time_arr[-1]):
        end_times = np.append(end_times, float(time_arr[-1]))

    consecutive = 0
    stable_window_count = 0
    stable_time_s: Optional[float] = None
    last_metrics: Dict[str, float] = {}
    last_reasons: List[str] = []

    for idx, end_time in enumerate(end_times, start=1):
        mask = (time_arr >= end_time - float(settings.live_window_s)) & (time_arr <= end_time)
        if np.sum(mask) < settings.min_points_in_window:
            continue
        t = time_arr[mask]
        i = current_arr[mask]
        stable, metrics, reasons = _evaluate_window(t, i, settings)
        last_metrics = {
            **metrics,
            "window_end_time_s": float(end_time - time_arr[0]),
        }
        last_reasons = reasons
        if stable:
            consecutive += 1
            stable_window_count = max(stable_window_count, consecutive)
            if consecutive >= settings.required_stable_windows and stable_time_s is None:
                stable_time_s = float(end_time - time_arr[0])
        else:
            consecutive = 0

    return stable_time_s, stable_window_count, last_metrics, last_reasons


def analyze_completed_bias_hold(
    time_s: Sequence[float],
    current_a: Sequence[float],
    *,
    hold_kind: str,
    settings: Optional[StabilizationSettings] = None,
) -> CompletedHoldAssessment:
    settings = settings or StabilizationSettings()
    time_arr = np.asarray(time_s, dtype=float)
    current_arr = np.asarray(current_a, dtype=float)

    finite = np.isfinite(time_arr) & np.isfinite(current_arr)
    time_arr = time_arr[finite]
    current_arr = current_arr[finite]

    min_hold = settings.min_hold_s if hold_kind == "pre_peis" else settings.min_post_peis_hold_s

    if len(time_arr) < max(settings.min_points_in_window, 3):
        current_hold = float(time_arr[-1] - time_arr[0]) if len(time_arr) >= 2 else 0.0
        retry = min(
            settings.max_hold_s,
            max(min_hold, current_hold + settings.extend_step_s, current_hold * settings.hold_extension_multiplier),
        )
        return CompletedHoldAssessment(
            hold_kind=hold_kind,
            saturated_enough=False,
            should_extend_now=True,
            can_reduce_next_time=False,
            current_hold_s=current_hold,
            estimated_stable_time_s=None,
            recommended_next_hold_s=retry,
            recommended_retry_hold_s=retry,
            stable_window_count=0,
            metrics={"current_hold_s": current_hold},
            reasons=["too few hold points to evaluate saturation reliably"],
            notes=["collect more hold data before shortening this step in later runs"],
        )

    current_hold = float(time_arr[-1] - time_arr[0])
    stable_time_s, stable_window_count, last_metrics, last_reasons = _estimate_stable_hold_time(
        time_arr,
        current_arr,
        settings,
    )
    metrics = {"current_hold_s": current_hold, **last_metrics}

    if stable_time_s is not None:
        recommended_next = stable_time_s * settings.hold_completion_margin_factor + settings.hold_completion_margin_s
        recommended_next = min(settings.max_hold_s, max(min_hold, recommended_next))
        can_reduce = current_hold > recommended_next + settings.min_reduction_to_shorten_s
        reasons = (
            ["hold reached a stable plateau before the trace ended"]
            if can_reduce
            else ["hold duration was close to the observed stabilization time"]
        )
        notes = []
        if can_reduce:
            notes.append("this hold looks longer than necessary, so the next planned hold can be shortened conservatively")
        else:
            notes.append("the current hold was close to the observed stabilization time, so keep it similar next time")
        return CompletedHoldAssessment(
            hold_kind=hold_kind,
            saturated_enough=True,
            should_extend_now=False,
            can_reduce_next_time=can_reduce,
            current_hold_s=current_hold,
            estimated_stable_time_s=stable_time_s,
            recommended_next_hold_s=float(recommended_next),
            recommended_retry_hold_s=float(recommended_next),
            stable_window_count=stable_window_count,
            metrics=metrics,
            reasons=reasons,
            notes=notes,
        )

    retry = min(
        settings.max_hold_s,
        max(min_hold, current_hold + settings.extend_step_s, current_hold * settings.hold_extension_multiplier),
    )
    reasons = ["hold did not satisfy stabilization thresholds by the end of the trace"]
    if last_reasons:
        reasons.extend(last_reasons)
    notes = [
        "if this were a live run, the hold should be extended before proceeding",
        "the next planned hold should start longer than the current one",
    ]
    return CompletedHoldAssessment(
        hold_kind=hold_kind,
        saturated_enough=False,
        should_extend_now=current_hold < settings.max_hold_s,
        can_reduce_next_time=False,
        current_hold_s=current_hold,
        estimated_stable_time_s=None,
        recommended_next_hold_s=float(retry),
        recommended_retry_hold_s=float(retry),
        stable_window_count=stable_window_count,
        metrics=metrics,
        reasons=reasons,
        notes=notes,
    )


def _valid_history_for_field(records: Iterable[Dict], field: str) -> List[Dict]:
    out = []
    for rec in records:
        hold = _to_float(rec.get(field))
        if hold is None:
            continue
        out.append(rec)
    return out


def _same_regime(a: Dict, b: Dict) -> bool:
    return (
        _to_float(a.get("temperature_c")) == _to_float(b.get("temperature_c"))
        and _to_float(a.get("gas_a_sccm")) == _to_float(b.get("gas_a_sccm"))
        and _to_float(a.get("gas_b_sccm")) == _to_float(b.get("gas_b_sccm"))
        and _to_float(a.get("electrode_id")) == _to_float(b.get("electrode_id"))
    )


def _same_electrode(a: Dict, b: Dict) -> bool:
    return _to_float(a.get("electrode_id")) == _to_float(b.get("electrode_id"))


def _same_gas(a: Dict, b: Dict) -> bool:
    return (
        _to_float(a.get("gas_a_sccm")) == _to_float(b.get("gas_a_sccm"))
        and _to_float(a.get("gas_b_sccm")) == _to_float(b.get("gas_b_sccm"))
    )


def _same_temperature(a: Dict, b: Dict) -> bool:
    return _to_float(a.get("temperature_c")) == _to_float(b.get("temperature_c"))


def _blend(base: float, predicted: Optional[float], settings: StabilizationSettings) -> Tuple[float, Optional[str]]:
    if predicted is None or predicted <= 0:
        return base, None
    if predicted > base:
        delta = predicted - base
        value = base + settings.slower_trend_weight * delta
        return min(value, base * settings.max_history_scale), "slower stabilization trend applied strongly"
    if predicted < base:
        delta = base - predicted
        value = base - settings.faster_trend_weight * delta
        return max(value, base * settings.min_history_scale), "faster stabilization trend applied gently"
    return base, None


def recommend_initial_pre_peis_hold(
    current_point: Dict,
    history: Iterable[Dict],
    *,
    previous_bias_v: Optional[float],
    target_bias_v: float,
    settings: Optional[StabilizationSettings] = None,
) -> Dict:
    settings = settings or StabilizationSettings()
    notes: List[str] = []

    if previous_bias_v is None:
        base_hold = settings.first_bias_hold_s
        notes.append("used conservative first-bias stabilization hold because no previous bias was available")
    else:
        dv = abs(float(target_bias_v) - float(previous_bias_v))
        if dv >= settings.large_step_threshold_v:
            base_hold = settings.first_bias_hold_s * settings.large_step_multiplier
            notes.append("large delta-V step detected; increased planned pre-PEIS hold")
        elif dv >= settings.medium_step_threshold_v:
            base_hold = settings.first_bias_hold_s * settings.medium_step_multiplier
            notes.append("medium delta-V step detected; moderately increased planned pre-PEIS hold")
        else:
            base_hold = settings.first_bias_hold_s * settings.small_step_multiplier
            notes.append("small delta-V step detected; planned pre-PEIS hold can start shorter")

    history_records = _valid_history_for_field(history, "optimized_pre_peis_hold_s")
    same_regime = [rec for rec in history_records if _same_regime(rec, current_point)]
    same_electrode = [
        rec for rec in history_records
        if _same_electrode(rec, current_point)
    ]

    seed_source = "no_history"
    if same_regime:
        predicted = _to_float(same_regime[-1].get("optimized_pre_peis_hold_s"))
        base_hold, note = _blend(base_hold, predicted, settings)
        seed_source = "same_regime"
        if note:
            notes.append(note)
    elif same_electrode:
        same_electrode_same_gas = [
            rec for rec in same_electrode
            if _same_gas(rec, current_point)
        ]
        same_electrode_same_temp = [
            rec for rec in same_electrode
            if _same_temperature(rec, current_point)
        ]
        if same_electrode_same_gas:
            predicted = _to_float(same_electrode_same_gas[-1].get("optimized_pre_peis_hold_s"))
            seed_source = "same_electrode_same_gas_changed_temperature"
        elif same_electrode_same_temp:
            predicted = _to_float(same_electrode_same_temp[-1].get("optimized_pre_peis_hold_s"))
            seed_source = "same_electrode_same_temperature_changed_gas"
        else:
            predicted = _to_float(same_electrode[-1].get("optimized_pre_peis_hold_s"))
            seed_source = "same_electrode_changed_temperature_or_gas"
        if predicted is not None and predicted > 0:
            base_hold = float(predicted) * float(settings.changed_regime_history_factor)
            notes.append(
                "changed temperature/gas regime used previous comparable pre hold multiplied by "
                f"{settings.changed_regime_history_factor:.2f}"
            )

    return {
        "planned_pre_peis_hold_s": float(min(max(base_hold, settings.min_hold_s), settings.max_hold_s)),
        "seed_source": seed_source,
        "notes": notes,
    }


def recommend_post_peis_hold(
    current_point: Dict,
    history: Iterable[Dict],
    *,
    previous_bias_v: Optional[float],
    target_bias_v: float,
    settings: Optional[StabilizationSettings] = None,
) -> Dict:
    settings = settings or StabilizationSettings()
    notes: List[str] = []
    base_hold = max(settings.first_post_peis_hold_s, settings.min_post_peis_hold_s)

    if previous_bias_v is None:
        notes.append("used default post-PEIS hold because no previous bias was available")
    else:
        dv = abs(float(target_bias_v) - float(previous_bias_v))
        if dv >= settings.large_step_threshold_v:
            base_hold *= settings.large_step_multiplier
            notes.append("large delta-V step detected; increased planned post-PEIS hold")
        elif dv >= settings.medium_step_threshold_v:
            base_hold *= settings.medium_step_multiplier
            notes.append("medium delta-V step detected; moderately increased planned post-PEIS hold")
        else:
            notes.append("small delta-V step detected; keeping the post-PEIS hold near its minimum")

    history_records = _valid_history_for_field(history, "optimized_post_peis_hold_s")
    same_regime = [rec for rec in history_records if _same_regime(rec, current_point)]
    same_electrode = [
        rec for rec in history_records
        if _to_float(rec.get("electrode_id")) == _to_float(current_point.get("electrode_id"))
    ]

    seed_source = "no_history"
    if same_regime:
        predicted = _to_float(same_regime[-1].get("optimized_post_peis_hold_s"))
        base_hold, note = _blend(base_hold, predicted, settings)
        seed_source = "same_regime"
        if note:
            notes.append(note)
    elif same_electrode:
        predicted = _to_float(same_electrode[-1].get("optimized_post_peis_hold_s"))
        base_hold, note = _blend(base_hold, predicted, settings)
        seed_source = "same_electrode_previous_condition"
        if note:
            notes.append(note)

    return {
        "planned_post_peis_hold_s": float(min(max(base_hold, settings.min_post_peis_hold_s), settings.max_hold_s)),
        "seed_source": seed_source,
        "notes": notes,
    }


def choose_bias_sweep_order(
    bias_values: Sequence[float],
    history: Iterable[Dict],
    *,
    condition_context: Optional[Dict] = None,
    settings: Optional[StabilizationSettings] = None,
) -> Dict:
    settings = settings or StabilizationSettings()
    values = [float(v) for v in bias_values]
    if not values:
        return {"ordered_biases_v": [], "strategy": "empty", "notes": ["no bias values supplied"]}

    ascending = sorted(values)
    descending = list(reversed(ascending))

    def _score(order: Sequence[float]) -> Tuple[float, List[str]]:
        total = 0.0
        local_notes: List[str] = []
        prev = None
        records = list(_valid_history_for_field(history, "optimized_pre_peis_hold_s"))
        for value in order:
            point = dict(condition_context or {})
            point["voltage_v"] = value
            hold_info = recommend_initial_pre_peis_hold(
                point,
                records,
                previous_bias_v=prev,
                target_bias_v=value,
                settings=settings,
            )
            total += float(hold_info["planned_pre_peis_hold_s"])
            prev = value
        local_notes.append(f"predicted cumulative stabilization burden = {total:.3f} s")
        return total, local_notes

    asc_score, asc_notes = _score(ascending)
    desc_score, desc_notes = _score(descending)

    if asc_score < desc_score:
        return {
            "ordered_biases_v": ascending,
            "strategy": "ascending",
            "notes": ["ascending order predicted a lower cumulative stabilization burden"] + asc_notes,
        }
    if desc_score < asc_score:
        return {
            "ordered_biases_v": descending,
            "strategy": "descending",
            "notes": ["descending order predicted a lower cumulative stabilization burden"] + desc_notes,
        }

    if settings.sweep_default_order == "descending_abs_from_positive":
        ordered = sorted(values, reverse=True)
        strategy = "descending"
    else:
        ordered = ascending
        strategy = "ascending"
    return {
        "ordered_biases_v": ordered,
        "strategy": strategy,
        "notes": ["ascending and descending looked similar, so the configured default order was used"],
    }
