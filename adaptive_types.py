# -*- coding: utf-8 -*-
"""
Internal data models for the GUI-independent adaptive measurement engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class PointMetadata:
    point_id: str
    label: str
    electrode_id: Optional[int] = None
    temperature_c: Optional[float] = None
    gas_a_sccm: Optional[float] = None
    gas_b_sccm: Optional[float] = None
    voltage_v: Optional[float] = None
    sequence_index: Optional[int] = None

    def to_policy_dict(self) -> Dict:
        return {
            "point_id": self.point_id,
            "label": self.label,
            "electrode_id": self.electrode_id,
            "temperature_c": self.temperature_c,
            "gas_a_sccm": self.gas_a_sccm,
            "gas_b_sccm": self.gas_b_sccm,
            "voltage_v": self.voltage_v,
            "sequence_index": self.sequence_index,
        }


@dataclass
class MeasurementExecution:
    measurement_mode: str
    peis_lowest_freq_hz: Optional[float]
    cp_duration_s: Optional[float]
    stabilization_hold_s: Optional[float] = None
    optimized_pre_peis_hold_s: Optional[float] = None
    planned_pre_peis_hold_s: Optional[float] = None
    post_peis_hold_s: Optional[float] = None
    optimized_post_peis_hold_s: Optional[float] = None
    planned_post_peis_hold_s: Optional[float] = None
    previous_bias_v: Optional[float] = None
    actual_peis_high_freq_hz: Optional[float] = None
    actual_peis_npts: Optional[int] = None
    dv_v: Optional[float] = None
    pre_hold_time_s: Optional[float] = None
    post_peis_hold_time_s: Optional[float] = None
    data_sufficient: Optional[bool] = None
    optimized_peis_lowest_freq_hz: Optional[float] = None
    optimized_cp_duration_s: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    def to_policy_dict(self) -> Dict:
        return {
            "measurement_mode": self.measurement_mode,
            "peis_lowest_freq_hz": self.peis_lowest_freq_hz,
            "cp_duration_s": self.cp_duration_s,
            "stabilization_hold_s": (
                self.stabilization_hold_s
                if self.stabilization_hold_s is not None
                else self.planned_pre_peis_hold_s
            ),
            "optimized_pre_peis_hold_s": (
                self.optimized_pre_peis_hold_s
                if self.optimized_pre_peis_hold_s is not None
                else (
                    self.stabilization_hold_s
                    if self.stabilization_hold_s is not None
                    else self.planned_pre_peis_hold_s
                )
            ),
            "post_peis_hold_s": (
                self.post_peis_hold_s
                if self.post_peis_hold_s is not None
                else self.planned_post_peis_hold_s
            ),
            "optimized_post_peis_hold_s": (
                self.optimized_post_peis_hold_s
                if self.optimized_post_peis_hold_s is not None
                else (
                    self.post_peis_hold_s
                    if self.post_peis_hold_s is not None
                    else self.planned_post_peis_hold_s
                )
            ),
            "data_sufficient": self.data_sufficient,
            "optimized_peis_lowest_freq_hz": (
                self.optimized_peis_lowest_freq_hz
                if self.optimized_peis_lowest_freq_hz is not None
                else self.peis_lowest_freq_hz
            ),
            "optimized_cp_duration_s": (
                self.optimized_cp_duration_s
                if self.optimized_cp_duration_s is not None
                else self.cp_duration_s
            ),
        }


@dataclass
class MeasurementFiles:
    pre_ca_path: Optional[str] = None
    peis_path: Optional[str] = None
    post_ca_path: Optional[str] = None


@dataclass
class AnalysisResult:
    recommended_peis_lowest_freq_hz: Optional[float]
    recommended_peis_conservative_cp_time_s: Optional[float]
    data_sufficient: bool
    peis_only_sufficient: bool
    recommended_normal_peis_lowest_freq_hz: Optional[float] = None
    confidence: Optional[float] = None
    reason: str = ""
    analysis_latency_s: Optional[float] = None
    source: str = "analysis_backend"
    provisional: bool = True
    notes: List[str] = field(default_factory=list)
    recommended_cp_highest_freq_hz: Optional[float] = None
    minimum_valid_cp_duration_s: Optional[float] = None
    saturation_recommended_cp_duration_s: Optional[float] = None
    optimization_selection_reason: Optional[str] = None
    peis_only_selection_reason: Optional[str] = None
    cp_saturation_reached: Optional[bool] = None
    cp_saturation_recommended_duration_s: Optional[float] = None
    cp_saturation_selection_reason: Optional[str] = None
    postcheck_reason: Optional[str] = None
    agreement_rel_err: Optional[float] = None
    actual_cp_duration_s: Optional[float] = None

    def to_policy_dict(self) -> Dict:
        return {
            "recommended_peis_lowest_freq_hz": self.recommended_peis_lowest_freq_hz,
            "recommended_normal_peis_lowest_freq_hz": self.recommended_normal_peis_lowest_freq_hz,
            "recommended_peis_conservative_cp_time_s": self.recommended_peis_conservative_cp_time_s,
            "recommended_cp_highest_freq_hz": self.recommended_cp_highest_freq_hz,
            "minimum_valid_cp_duration_s": self.minimum_valid_cp_duration_s,
            "saturation_recommended_cp_duration_s": self.saturation_recommended_cp_duration_s,
            "data_sufficient": self.data_sufficient,
            "peis_only_sufficient": self.peis_only_sufficient,
            "confidence": self.confidence,
            "reason": self.reason,
            "optimization_selection_reason": self.optimization_selection_reason,
            "peis_only_selection_reason": self.peis_only_selection_reason,
            "cp_saturation_reached": self.cp_saturation_reached,
            "cp_saturation_recommended_duration_s": self.cp_saturation_recommended_duration_s,
            "cp_saturation_selection_reason": self.cp_saturation_selection_reason,
            "postcheck_reason": self.postcheck_reason,
            "agreement_rel_err": self.agreement_rel_err,
            "actual_cp_duration_s": self.actual_cp_duration_s,
            "notes": list(self.notes),
        }

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class FullProcessingResultPayload:
    status: str
    sample_name: str
    excel_path: Optional[str] = None
    output_dir: Optional[str] = None
    processing_latency_s: Optional[float] = None
    fit_summary: Optional[Dict] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RecommendationPayload:
    action: str
    measurement_mode: str
    peis_lowest_freq_hz: float
    cp_duration_s: float
    analysis_source: str
    planned_pre_peis_hold_s: Optional[float] = None
    planned_post_peis_hold_s: Optional[float] = None
    recommended_cp_highest_freq_hz: Optional[float] = None
    analysis_point_id: Optional[str] = None
    seed_source: Optional[str] = None
    hybrid_unnecessary: bool = False
    analysis_selection_reason: Optional[str] = None
    peis_only_selection_reason: Optional[str] = None
    postcheck_reason: Optional[str] = None
    used_fallback: bool = False
    current_run_policy_action: Optional[str] = None
    current_run_policy_consumed: bool = False
    current_run_policy_analysis_point_id: Optional[str] = None
    current_run_policy_runtime_lf_hz: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class CurrentRunPolicyDecision:
    action: str
    measurement_mode: str
    exploratory_seed_lf_hz: float
    general_target_lf_hz: float
    normal_target_lf_hz: float
    runtime_lf_hz: float
    runtime_target_lf_hz: float
    runtime_vs_target_relation: str
    runtime_vs_target_log10_gap: float
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RemeasurementRequest:
    point_id: str
    measurement_mode: str
    peis_lowest_freq_hz: float
    cp_duration_s: float
    planned_pre_peis_hold_s: Optional[float] = None
    planned_post_peis_hold_s: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PointStateRecord:
    metadata: PointMetadata
    measurement: MeasurementExecution
    files: MeasurementFiles = field(default_factory=MeasurementFiles)
    state: str = "measurement_complete"
    analysis_result: Optional[AnalysisResult] = None
    analysis_started_at: Optional[float] = None
    analysis_ready_at: Optional[float] = None
    full_processing_state: str = "not_started"
    full_processing_started_at: Optional[float] = None
    full_processing_ready_at: Optional[float] = None
    full_processing_result: Optional[FullProcessingResultPayload] = None
    current_run_policy_decision: Optional[CurrentRunPolicyDecision] = None
    applied_to_future_point_ids: List[str] = field(default_factory=list)
    remeasurement_request: Optional[RemeasurementRequest] = None
    notes: List[str] = field(default_factory=list)

    def to_history_record(self) -> Dict:
        record = self.metadata.to_policy_dict()
        record.update(self.measurement.to_policy_dict())
        if self.analysis_result is not None:
            record["data_sufficient"] = self.analysis_result.data_sufficient
            record["peis_only_sufficient"] = self.analysis_result.peis_only_sufficient
            record["peis_only_selection_reason"] = self.analysis_result.peis_only_selection_reason
            record["agreement_rel_err"] = self.analysis_result.agreement_rel_err
            record["cp_saturation_reached"] = self.analysis_result.cp_saturation_reached
            record["actual_cp_duration_s"] = self.analysis_result.actual_cp_duration_s
            if self.analysis_result.recommended_peis_lowest_freq_hz is not None:
                record["optimized_peis_lowest_freq_hz"] = self.analysis_result.recommended_peis_lowest_freq_hz
            if self.analysis_result.recommended_peis_conservative_cp_time_s is not None:
                record["optimized_cp_duration_s"] = self.analysis_result.recommended_peis_conservative_cp_time_s
        return record

    def to_dict(self) -> Dict:
        return {
            "metadata": asdict(self.metadata),
            "measurement": asdict(self.measurement),
            "files": asdict(self.files),
            "state": self.state,
            "analysis_result": None if self.analysis_result is None else self.analysis_result.to_dict(),
            "analysis_started_at": self.analysis_started_at,
            "analysis_ready_at": self.analysis_ready_at,
            "full_processing_state": self.full_processing_state,
            "full_processing_started_at": self.full_processing_started_at,
            "full_processing_ready_at": self.full_processing_ready_at,
            "full_processing_result": None if self.full_processing_result is None else self.full_processing_result.to_dict(),
            "current_run_policy_decision": None if self.current_run_policy_decision is None else self.current_run_policy_decision.to_dict(),
            "applied_to_future_point_ids": list(self.applied_to_future_point_ids),
            "remeasurement_request": None if self.remeasurement_request is None else self.remeasurement_request.to_dict(),
            "notes": list(self.notes),
        }
