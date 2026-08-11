# -*- coding: utf-8 -*-
"""
GUI-independent adaptive measurement orchestration layer.
"""

from __future__ import annotations

import math
import time
import traceback
from copy import deepcopy
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from adaptive_types import (
    AnalysisResult,
    CurrentRunPolicyDecision,
    FullProcessingResultPayload,
    MeasurementExecution,
    MeasurementFiles,
    PointMetadata,
    PointStateRecord,
    RecommendationPayload,
    RemeasurementRequest,
)
from analysis_adapter import AnalysisBackendSettings, analyze_measurement_files
from full_processing_adapter import FullProcessingSettings, run_full_processing
from stabilization_policy import (
    CompletedHoldAssessment,
    StabilizationAssessment,
    StabilizationSettings,
    analyze_completed_bias_hold,
    analyze_pre_peis_stability,
    choose_bias_sweep_order,
    recommend_initial_pre_peis_hold,
    recommend_post_peis_hold,
)


@dataclass
class AdaptiveEngineSettings:
    analysis_wait_timeout_s: float = 10.0
    analysis_poll_interval_s: float = 0.05
    max_analysis_workers: int = 2
    max_full_processing_workers: int = 1
    normal_eis_floor_hz: float = 0.01
    trusted_peis_only_agreement_rel_err_max: float = 0.30
    require_peis_plateau_signal_for_normal_mode: bool = True
    trusted_peis_only_selection_reasons: List[str] = field(
        default_factory=lambda: [
            "peis_reaches_saturation",
            "peis_plateau_and_no_hybrid_gain",
            "peis_plateau_exceeds_cp_saturation",
        ]
    )
    allow_strong_agreement_fallback_without_plateau: bool = True
    strong_agreement_fallback_rel_err_max: float = 0.03
    keep_rapid_on_first_point_of_new_regime: bool = True
    blocked_peis_only_selection_reasons: List[str] = field(
        default_factory=list
    )
    max_consecutive_normal_eis_points: int = 2

    def to_dict(self) -> Dict:
        return asdict(self)


class AdaptiveMeasurementEngine:
    def __init__(
        self,
        *,
        policy_settings=None,
        engine_settings: Optional[AdaptiveEngineSettings] = None,
        analysis_settings: Optional[AnalysisBackendSettings] = None,
        full_processing_settings: Optional[FullProcessingSettings] = None,
        stabilization_settings: Optional[StabilizationSettings] = None,
        analyzer: Optional[Callable[..., AnalysisResult]] = None,
        full_processor: Optional[Callable[..., object]] = None,
    ):
        from Analysis_Convert_CP_to_EIS.adaptive_measurement_policy import (  # type: ignore
            PolicySettings,
            assess_measurement_sufficiency,
            plan_remeasurement,
            suggest_next_parameters,
        )

        self.policy_settings = policy_settings or PolicySettings()
        self.engine_settings = engine_settings or AdaptiveEngineSettings()
        self.analysis_settings = analysis_settings or AnalysisBackendSettings(
            rapid_switch_lf_threshold_hz=self.engine_settings.normal_eis_floor_hz
        )
        self.full_processing_settings = full_processing_settings or FullProcessingSettings()
        self.stabilization_settings = stabilization_settings or StabilizationSettings()
        self._assess_measurement_sufficiency = assess_measurement_sufficiency
        self._plan_remeasurement = plan_remeasurement
        self._suggest_next_parameters = suggest_next_parameters

        self._analyzer = analyzer or self._default_analyzer
        self._full_processor = full_processor or self._default_full_processor
        self._executor = ThreadPoolExecutor(max_workers=self.engine_settings.max_analysis_workers)
        self._full_processing_executor = ThreadPoolExecutor(max_workers=self.engine_settings.max_full_processing_workers)
        self._points: Dict[str, PointStateRecord] = {}
        self._analysis_futures: Dict[str, Future] = {}
        self._full_processing_futures: Dict[str, Future] = {}
        self._pending_remeasurements: List[RemeasurementRequest] = []

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=False)
        self._full_processing_executor.shutdown(wait=False, cancel_futures=False)

    def _default_analyzer(self, *, dc_path: Optional[str], eis_path: str, sample_name: str) -> AnalysisResult:
        return analyze_measurement_files(
            dc_path,
            eis_path,
            settings=self.analysis_settings,
            sample_name=sample_name,
        )

    def _default_full_processor(self, *, dc_path: str, eis_path: str, sample_name: str):
        return run_full_processing(
            dc_path,
            eis_path,
            sample_name=sample_name,
            settings=self.full_processing_settings,
        )

    def register_point(
        self,
        metadata: PointMetadata,
        measurement: MeasurementExecution,
        files: Optional[MeasurementFiles] = None,
    ) -> str:
        record = PointStateRecord(
            metadata=deepcopy(metadata),
            measurement=deepcopy(measurement),
            files=deepcopy(files or MeasurementFiles()),
        )
        self._points[metadata.point_id] = record
        return metadata.point_id

    def get_point_record(self, point_id: str) -> PointStateRecord:
        return self._points[point_id]

    def assess_pre_peis_stabilization(
        self,
        point_id: str,
        *,
        time_s,
        current_a,
        peis_already_started: bool = False,
    ) -> StabilizationAssessment:
        assessment = analyze_pre_peis_stability(
            time_s,
            current_a,
            settings=self.stabilization_settings,
            peis_already_started=peis_already_started,
        )
        record = self._points[point_id]
        record.measurement.stabilization_hold_s = assessment.metrics.get("hold_now_s")
        record.notes.extend(assessment.notes)
        if not assessment.stable and assessment.should_abort_recent_peis:
            record.state = "remeasure_required"
            request = RemeasurementRequest(
                point_id=point_id,
                measurement_mode=record.measurement.measurement_mode,
                peis_lowest_freq_hz=float(record.measurement.peis_lowest_freq_hz or self.policy_settings.first_pass_peis_lowest_freq_hz),
                cp_duration_s=float(record.measurement.cp_duration_s or self.policy_settings.first_pass_cp_duration_s),
                planned_pre_peis_hold_s=float(assessment.recommended_total_hold_s),
                planned_post_peis_hold_s=float(
                    record.measurement.post_peis_hold_s
                    or record.measurement.planned_post_peis_hold_s
                    or self.stabilization_settings.min_post_peis_hold_s
                ),
                reasons=list(assessment.reasons),
                notes=["pre-PEIS bias stabilization failed after PEIS had already started"],
            )
            record.remeasurement_request = request
            self._pending_remeasurements.append(request)
        return assessment

    def assess_completed_pre_peis_hold(
        self,
        point_id: str,
        *,
        time_s,
        current_a,
    ) -> CompletedHoldAssessment:
        assessment = analyze_completed_bias_hold(
            time_s,
            current_a,
            hold_kind="pre_peis",
            settings=self.stabilization_settings,
        )
        record = self._points[point_id]
        record.measurement.stabilization_hold_s = assessment.current_hold_s
        record.measurement.optimized_pre_peis_hold_s = assessment.recommended_next_hold_s
        record.notes.extend(assessment.notes)
        return assessment

    def assess_completed_post_peis_hold(
        self,
        point_id: str,
        *,
        time_s,
        current_a,
    ) -> CompletedHoldAssessment:
        assessment = analyze_completed_bias_hold(
            time_s,
            current_a,
            hold_kind="post_peis",
            settings=self.stabilization_settings,
        )
        record = self._points[point_id]
        record.measurement.post_peis_hold_s = assessment.current_hold_s
        record.measurement.optimized_post_peis_hold_s = assessment.recommended_next_hold_s
        record.notes.extend(assessment.notes)
        return assessment

    def list_point_records(self) -> List[PointStateRecord]:
        return [self._points[key] for key in sorted(self._points)]

    def build_history_records(self) -> List[Dict]:
        records = []
        for state in self._points.values():
            if state.state == "analysis_pending":
                continue
            records.append(state.to_history_record())
        return records

    def start_analysis(self, point_id: str, *, delay_s: float = 0.0):
        record = self._points[point_id]
        dc_path = self._resolve_dc_path_for_analysis(record)
        if not record.files.peis_path:
            raise ValueError(f"Point {point_id} does not have the required measurement files.")
        if point_id in self._analysis_futures:
            return

        record.state = "analysis_pending"
        record.analysis_started_at = time.time()

        def _job():
            if delay_s > 0:
                time.sleep(delay_s)
            return self._analyzer(
                dc_path=dc_path,
                eis_path=record.files.peis_path,
                sample_name=record.metadata.label,
            )

        self._analysis_futures[point_id] = self._executor.submit(_job)

    def start_full_processing(self, point_id: str, *, delay_s: float = 0.0):
        record = self._points[point_id]
        dc_path = self._resolve_dc_path_for_analysis(record)
        if not dc_path or not record.files.peis_path:
            raise ValueError(f"Point {point_id} does not have the required measurement files.")
        if point_id in self._full_processing_futures:
            return

        record.full_processing_state = "processing"
        record.full_processing_started_at = time.time()

        def _job():
            if delay_s > 0:
                time.sleep(delay_s)
            return self._full_processor(
                dc_path=dc_path,
                eis_path=record.files.peis_path,
                sample_name=record.metadata.label,
            )

        self._full_processing_futures[point_id] = self._full_processing_executor.submit(_job)

    def start_post_measurement_pipeline(
        self,
        point_id: str,
        *,
        analysis_delay_s: float = 0.0,
        start_full_processing: bool = True,
        full_processing_delay_s: float = 0.0,
    ):
        self.start_analysis(point_id, delay_s=analysis_delay_s)
        if start_full_processing:
            self.start_full_processing(point_id, delay_s=full_processing_delay_s)

    def wait_briefly_for_analysis(self, point_id: str, timeout_s: Optional[float] = None) -> bool:
        future = self._analysis_futures.get(point_id)
        if future is None:
            return self._points[point_id].analysis_result is not None

        timeout_s = self.engine_settings.analysis_wait_timeout_s if timeout_s is None else timeout_s
        try:
            result = future.result(timeout=timeout_s)
        except TimeoutError:
            return False
        except Exception as exc:
            self._finalize_analysis_failure(point_id, exc)
            return False

        self._finalize_analysis(point_id, result)
        return True

    def poll_ready_analyses(self) -> List[str]:
        ready = []
        for point_id, future in list(self._analysis_futures.items()):
            if not future.done():
                continue
            try:
                result = future.result()
            except Exception as exc:
                self._finalize_analysis_failure(point_id, exc)
            else:
                self._finalize_analysis(point_id, result)
            ready.append(point_id)
        return ready

    def poll_ready_full_processing(self) -> List[str]:
        ready = []
        for point_id, future in list(self._full_processing_futures.items()):
            if not future.done():
                continue
            try:
                result = future.result()
            except Exception as exc:
                self._finalize_full_processing_failure(point_id, exc)
            else:
                self._finalize_full_processing(point_id, result)
            ready.append(point_id)
        return ready

    def _finalize_analysis_failure(self, point_id: str, exc: Exception):
        record = self._points[point_id]
        record.analysis_ready_at = time.time()
        record.state = "analysis_failed"
        self._analysis_futures.pop(point_id, None)
        message = f"analysis failed: {type(exc).__name__}: {exc}"
        record.notes.append(message)
        record.notes.append(traceback.format_exc(limit=8))

    def _finalize_full_processing_failure(self, point_id: str, exc: Exception):
        record = self._points[point_id]
        record.full_processing_ready_at = time.time()
        record.full_processing_state = "failed"
        self._full_processing_futures.pop(point_id, None)
        message = f"full processing failed: {type(exc).__name__}: {exc}"
        record.notes.append(message)
        record.full_processing_result = FullProcessingResultPayload(
            status="failed",
            sample_name=record.metadata.label,
            notes=[message, traceback.format_exc(limit=8)],
        )

    def _finalize_full_processing(self, point_id: str, result):
        record = self._points[point_id]
        record.full_processing_ready_at = time.time()
        record.full_processing_state = "completed"
        self._full_processing_futures.pop(point_id, None)

        if hasattr(result, "to_dict"):
            payload_dict = result.to_dict()
        elif isinstance(result, dict):
            payload_dict = result
        else:
            payload_dict = {"status": "completed", "sample_name": record.metadata.label, "notes": [str(result)]}

        record.full_processing_result = FullProcessingResultPayload(
            status=str(payload_dict.get("status", "completed")),
            sample_name=str(payload_dict.get("sample_name", record.metadata.label)),
            excel_path=payload_dict.get("excel_path"),
            output_dir=payload_dict.get("output_dir"),
            processing_latency_s=payload_dict.get("processing_latency_s"),
            fit_summary=payload_dict.get("fit_summary"),
            notes=list(payload_dict.get("notes", [])),
        )

    def get_background_queue_status(self) -> Dict:
        self.poll_ready_analyses()
        self.poll_ready_full_processing()
        return {
            "analysis_pending": sorted(self._analysis_futures.keys()),
            "full_processing_pending": sorted(self._full_processing_futures.keys()),
        }

    def _finalize_analysis(self, point_id: str, result: AnalysisResult):
        record = self._points[point_id]
        record.analysis_result = result
        record.analysis_ready_at = time.time()
        self._analysis_futures.pop(point_id, None)
        record.notes.extend(result.notes)
        record.current_run_policy_decision = self.plan_two_stage_current_run_policy(
            exploratory_seed_lf_hz=record.measurement.peis_lowest_freq_hz,
            analysis_result=result,
        )
        record.notes.extend(record.current_run_policy_decision.notes)

        sufficiency = self._assess_measurement_sufficiency(
            analysis_recommendation=result.to_policy_dict(),
            actual_parameters=record.measurement.to_policy_dict(),
        )
        record.measurement.data_sufficient = sufficiency["data_sufficient"]
        record.measurement.optimized_peis_lowest_freq_hz = result.recommended_peis_lowest_freq_hz
        record.measurement.optimized_cp_duration_s = result.recommended_peis_conservative_cp_time_s

        if sufficiency["data_sufficient"]:
            record.state = "recommendation_ready"
            return

        retry = self._plan_remeasurement(
            record.measurement.to_policy_dict(),
            sufficiency,
            settings=self.policy_settings,
        )
        retry_mode = str(retry["measurement_mode"])
        retry_notes = list(retry.get("notes", []))
        if (
            record.measurement.measurement_mode == "normal_eis"
            and not result.peis_only_sufficient
        ):
            retry_mode = "rapid_eis"
            retry_notes.append(
                "normal-EIS follow-up was not sufficient, so the retry is upgraded to rapid-EIS refresh"
            )
        request = RemeasurementRequest(
            point_id=point_id,
            measurement_mode=retry_mode,
            peis_lowest_freq_hz=float(retry["peis_lowest_freq_hz"]),
            cp_duration_s=float(retry["cp_duration_s"]),
            planned_pre_peis_hold_s=float(
                record.measurement.stabilization_hold_s
                or record.measurement.planned_pre_peis_hold_s
                or self.stabilization_settings.first_bias_hold_s
            ),
            planned_post_peis_hold_s=float(
                record.measurement.post_peis_hold_s
                or record.measurement.planned_post_peis_hold_s
                or self.stabilization_settings.min_post_peis_hold_s
            ),
            reasons=list(retry.get("reasons", [])),
            notes=retry_notes,
        )
        record.remeasurement_request = request
        record.state = "remeasure_required"
        self._pending_remeasurements.append(request)

    def collect_pending_remeasurements(self) -> List[RemeasurementRequest]:
        self.poll_ready_analyses()
        self.poll_ready_full_processing()
        pending = list(self._pending_remeasurements)
        self._pending_remeasurements.clear()
        return pending

    def _select_analysis_seed(self, current_point: PointMetadata) -> Tuple[Optional[AnalysisResult], str, Optional[str]]:
        ready_records = [rec for rec in self._points.values() if rec.analysis_result is not None]
        if not ready_records:
            return None, "no_ready_analysis", None

        same_condition_same_electrode = [
            rec for rec in ready_records
            if rec.metadata.electrode_id == current_point.electrode_id
            and rec.metadata.temperature_c == current_point.temperature_c
            and rec.metadata.gas_a_sccm == current_point.gas_a_sccm
            and rec.metadata.gas_b_sccm == current_point.gas_b_sccm
        ]
        if same_condition_same_electrode:
            rec = same_condition_same_electrode[-1]
            return rec.analysis_result, "same_condition_same_electrode_analysis", rec.metadata.point_id

        same_electrode = [rec for rec in ready_records if rec.metadata.electrode_id == current_point.electrode_id]
        if same_electrode:
            rec = same_electrode[-1]
            return rec.analysis_result, "same_electrode_analysis", rec.metadata.point_id

        same_condition = [
            rec for rec in ready_records
            if rec.metadata.temperature_c == current_point.temperature_c
            and rec.metadata.gas_a_sccm == current_point.gas_a_sccm
            and rec.metadata.gas_b_sccm == current_point.gas_b_sccm
        ]
        if same_condition:
            rec = same_condition[-1]
            return rec.analysis_result, "same_condition_other_electrode_analysis", rec.metadata.point_id

        rec = ready_records[-1]
        return rec.analysis_result, "latest_available_analysis", rec.metadata.point_id

    def recommend_next_point(self, current_point: PointMetadata) -> RecommendationPayload:
        self.poll_ready_analyses()
        history = self.build_history_records()
        analysis_result, analysis_source, analysis_point_id = self._select_analysis_seed(current_point)
        guarded_analysis, trusted_peis_only, guard_notes = self._guarded_analysis_recommendation(
            current_point,
            analysis_result,
            analysis_point_id,
        )
        plan = self._suggest_next_parameters(
            current_point=current_point.to_policy_dict(),
            history=history,
            analysis_recommendation=guarded_analysis,
            settings=self.policy_settings,
        )
        previous_bias_v = self._find_previous_bias_for_regime(current_point)
        hold_plan = recommend_initial_pre_peis_hold(
            current_point.to_policy_dict(),
            history,
            previous_bias_v=previous_bias_v,
            target_bias_v=float(current_point.voltage_v or 0.0),
            settings=self.stabilization_settings,
        )
        post_hold_plan = recommend_post_peis_hold(
            current_point.to_policy_dict(),
            history,
            previous_bias_v=previous_bias_v,
            target_bias_v=float(current_point.voltage_v or 0.0),
            settings=self.stabilization_settings,
        )
        payload = RecommendationPayload(
            action="measure_next_point",
            measurement_mode=str(plan["measurement_mode"]),
            peis_lowest_freq_hz=float(plan["peis_lowest_freq_hz"]),
            cp_duration_s=float(plan["cp_duration_s"]),
            planned_pre_peis_hold_s=float(hold_plan["planned_pre_peis_hold_s"]),
            planned_post_peis_hold_s=float(post_hold_plan["planned_post_peis_hold_s"]),
            recommended_cp_highest_freq_hz=(
                None if analysis_result is None else analysis_result.recommended_cp_highest_freq_hz
            ),
            analysis_source=analysis_source,
            analysis_point_id=analysis_point_id,
            seed_source=plan.get("seed_source"),
            hybrid_unnecessary=bool(trusted_peis_only),
            analysis_selection_reason=(
                None if analysis_result is None else analysis_result.optimization_selection_reason
            ),
            peis_only_selection_reason=(
                None if analysis_result is None else analysis_result.peis_only_selection_reason
            ),
            postcheck_reason=(
                None if analysis_result is None else analysis_result.postcheck_reason
            ),
            used_fallback=analysis_result is None,
            notes=(
                list(plan.get("notes", []))
                + list(guard_notes)
                + list(hold_plan.get("notes", []))
                + list(post_hold_plan.get("notes", []))
                + ([] if analysis_result is None else list(analysis_result.notes))
            ),
        )
        self._apply_current_run_policy_decision(
            payload,
            current_point=current_point,
            analysis_result=analysis_result,
            analysis_point_id=analysis_point_id,
            trusted_peis_only=trusted_peis_only,
        )
        self._apply_periodic_rapid_refresh(current_point, payload, analysis_result)
        if analysis_point_id and analysis_point_id in self._points:
            self._points[analysis_point_id].applied_to_future_point_ids.append(current_point.point_id)
            if self._points[analysis_point_id].state == "recommendation_ready":
                self._points[analysis_point_id].state = "applied_to_future_point"
        return payload

    def recommend_bias_sweep_order(
        self,
        *,
        bias_values_v: Iterable[float],
        condition_context: Optional[Dict] = None,
    ) -> Dict:
        history = self.build_history_records()
        return choose_bias_sweep_order(
            list(bias_values_v),
            history,
            condition_context=condition_context,
            settings=self.stabilization_settings,
        )

    def _find_previous_bias_for_regime(self, current_point: PointMetadata) -> Optional[float]:
        candidates = [
            rec for rec in self._points.values()
            if rec.metadata.electrode_id == current_point.electrode_id
            and rec.metadata.temperature_c == current_point.temperature_c
            and rec.metadata.gas_a_sccm == current_point.gas_a_sccm
            and rec.metadata.gas_b_sccm == current_point.gas_b_sccm
            and rec.metadata.point_id != current_point.point_id
            and rec.metadata.voltage_v is not None
        ]
        if not candidates:
            return None
        return float(candidates[-1].metadata.voltage_v)

    @staticmethod
    def _same_regime(a: PointMetadata, b: PointMetadata) -> bool:
        return (
            a.electrode_id == b.electrode_id
            and a.temperature_c == b.temperature_c
            and a.gas_a_sccm == b.gas_a_sccm
            and a.gas_b_sccm == b.gas_b_sccm
        )

    def _has_same_regime_history(self, current_point: PointMetadata) -> bool:
        for record in self._points.values():
            if record.metadata.point_id == current_point.point_id:
                continue
            if self._same_regime(record.metadata, current_point):
                return True
        return False

    def _same_regime_records(self, current_point: PointMetadata) -> List[PointStateRecord]:
        return [
            record
            for record in self._points.values()
            if record.metadata.point_id != current_point.point_id
            and self._same_regime(record.metadata, current_point)
        ]

    def _latest_same_regime_rapid_record(self, current_point: PointMetadata) -> Optional[PointStateRecord]:
        for record in reversed(self._same_regime_records(current_point)):
            if record.measurement.measurement_mode == "rapid_eis":
                return record
        return None

    def _count_consecutive_same_regime_normal_points(self, current_point: PointMetadata) -> int:
        count = 0
        for record in reversed(self._same_regime_records(current_point)):
            if record.measurement.measurement_mode != "normal_eis":
                break
            count += 1
        return count

    def _choose_rapid_refresh_cp_duration(
        self,
        current_point: PointMetadata,
        analysis_result: Optional[AnalysisResult],
    ) -> float:
        latest_rapid = self._latest_same_regime_rapid_record(current_point)
        candidates: List[float] = []
        if latest_rapid is not None:
            for value in (
                latest_rapid.measurement.optimized_cp_duration_s,
                latest_rapid.measurement.cp_duration_s,
            ):
                if value is not None and value > 0:
                    candidates.append(float(value))
                    break
        if analysis_result is not None and analysis_result.recommended_peis_conservative_cp_time_s:
            candidates.append(float(analysis_result.recommended_peis_conservative_cp_time_s))
        if not candidates:
            candidates.append(float(self.policy_settings.first_pass_cp_duration_s))
        return max(candidates)

    def _apply_periodic_rapid_refresh(
        self,
        current_point: PointMetadata,
        payload: RecommendationPayload,
        analysis_result: Optional[AnalysisResult],
    ) -> None:
        threshold = int(self.engine_settings.max_consecutive_normal_eis_points or 0)
        if payload.measurement_mode != "normal_eis" or threshold <= 0:
            return

        consecutive_normal = self._count_consecutive_same_regime_normal_points(current_point)
        if consecutive_normal < threshold:
            return

        payload.measurement_mode = "rapid_eis"
        payload.cp_duration_s = self._choose_rapid_refresh_cp_duration(current_point, analysis_result)
        payload.hybrid_unnecessary = False
        payload.notes.append(
            f"forced periodic rapid refresh after {consecutive_normal} consecutive normal-EIS points so LF/CP optimization stays calibrated"
        )

    def _apply_current_run_policy_decision(
        self,
        payload: RecommendationPayload,
        *,
        current_point: PointMetadata,
        analysis_result: Optional[AnalysisResult],
        analysis_point_id: Optional[str],
        trusted_peis_only: bool,
    ) -> None:
        if analysis_result is None or not analysis_point_id:
            return
        record = self._points.get(analysis_point_id)
        if record is None or record.current_run_policy_decision is None:
            return

        decision = record.current_run_policy_decision
        payload.current_run_policy_action = decision.action
        payload.current_run_policy_analysis_point_id = analysis_point_id
        payload.current_run_policy_runtime_lf_hz = float(decision.runtime_lf_hz)
        if decision.action == "handoff_to_normal_lf":
            if not trusted_peis_only:
                payload.current_run_policy_consumed = False
                payload.notes.append(
                    "stored current-run handoff_to_normal_lf exists, but runtime guard kept this regime in rapid mode for now"
                )
                return
            payload.measurement_mode = "normal_eis"
            payload.peis_lowest_freq_hz = float(decision.runtime_lf_hz)
            payload.hybrid_unnecessary = True
            payload.current_run_policy_consumed = True
            payload.notes.append(
                "next-point planner consumed current_run_policy_decision=handoff_to_normal_lf and switched to the PEIS-based normal LF"
            )
            return

        if decision.action == "keep_exploratory_seed_for_hybrid":
            payload.measurement_mode = "rapid_eis"
            payload.peis_lowest_freq_hz = float(decision.runtime_lf_hz)
            payload.hybrid_unnecessary = False
            payload.current_run_policy_consumed = True
            payload.notes.append(
                "next-point planner consumed current_run_policy_decision=keep_exploratory_seed_for_hybrid and kept the exploratory LF seed for rapid mode"
            )

    def _estimate_peis_gain_ratio(
        self,
        analysis_result: AnalysisResult,
        analysis_point_id: Optional[str],
    ) -> Optional[float]:
        if analysis_result.recommended_peis_lowest_freq_hz is None:
            return None
        if not analysis_point_id or analysis_point_id not in self._points:
            return None
        measured_lf = self._points[analysis_point_id].measurement.peis_lowest_freq_hz
        if measured_lf is None or measured_lf <= 0:
            return None
        return float(analysis_result.recommended_peis_lowest_freq_hz) / float(measured_lf)

    def _runtime_trusts_peis_only(
        self,
        analysis_result: AnalysisResult,
        current_point: PointMetadata,
        analysis_point_id: Optional[str],
    ) -> Tuple[bool, List[str]]:
        if not analysis_result.peis_only_sufficient:
            return False, []

        reasons: List[str] = []
        blocked_reasons = set(self.engine_settings.blocked_peis_only_selection_reasons)
        raw_reason = analysis_result.peis_only_selection_reason or ""
        if raw_reason in blocked_reasons:
            reasons.append(f"raw PEIS-only reason '{raw_reason}' is heuristic, so runtime keeps rapid mode")

        trusted_plateau_reasons = set(self.engine_settings.trusted_peis_only_selection_reasons)
        plateau_based_signal = raw_reason in trusted_plateau_reasons
        strong_agreement_fallback = bool(
            self.engine_settings.allow_strong_agreement_fallback_without_plateau
            and raw_reason == "peis_reaches_saturation_via_strong_agreement"
            and analysis_result.agreement_rel_err is not None
            and analysis_result.agreement_rel_err
            <= self.engine_settings.strong_agreement_fallback_rel_err_max
        )

        if (
            self.engine_settings.require_peis_plateau_signal_for_normal_mode
            and not plateau_based_signal
            and not strong_agreement_fallback
        ):
            reasons.append(
                "PEIS LF plateau signal is not strong enough yet, so runtime keeps rapid mode"
            )

        if (
            analysis_result.agreement_rel_err is not None
            and analysis_result.agreement_rel_err > self.engine_settings.trusted_peis_only_agreement_rel_err_max
        ):
            reasons.append(
                "PEIS/CP LF agreement is too weak to trust a runtime switch to normal EIS"
            )

        if (
            self.engine_settings.keep_rapid_on_first_point_of_new_regime
            and not self._has_same_regime_history(current_point)
        ):
            reasons.append("first point in a new temperature/gas/electrode regime stays conservative and keeps rapid mode")

        return len(reasons) == 0, reasons

    def _guarded_analysis_recommendation(
        self,
        current_point: PointMetadata,
        analysis_result: Optional[AnalysisResult],
        analysis_point_id: Optional[str],
    ) -> Tuple[Optional[Dict], bool, List[str]]:
        if analysis_result is None:
            return None, False, []

        guarded = analysis_result.to_policy_dict()
        trusted_peis_only, guard_reasons = self._runtime_trusts_peis_only(
            analysis_result,
            current_point,
            analysis_point_id,
        )
        if analysis_result.peis_only_sufficient and not trusted_peis_only:
            guarded["peis_only_sufficient"] = False
            if (
                guarded.get("recommended_peis_conservative_cp_time_s") is None
                and analysis_result.cp_saturation_reached is False
                and analysis_result.cp_saturation_recommended_duration_s is not None
            ):
                guarded["recommended_peis_conservative_cp_time_s"] = (
                    float(analysis_result.cp_saturation_recommended_duration_s)
                    / max(float(self.policy_settings.cp_conservative_factor), 1e-9)
                )
                guard_reasons.append(
                    "used CP tail saturation duration as the rapid-mode seed because PEIS-only was not trusted"
                )
        return guarded, trusted_peis_only, guard_reasons

    def _resolve_dc_path_for_analysis(self, record: PointStateRecord) -> Optional[str]:
        if record.files.post_ca_path:
            return record.files.post_ca_path
        return record.files.pre_ca_path

    def plan_two_stage_current_run_policy(
        self,
        *,
        exploratory_seed_lf_hz: Optional[float],
        analysis_result: AnalysisResult,
    ) -> CurrentRunPolicyDecision:
        general_target_lf_hz = float(
            analysis_result.recommended_peis_lowest_freq_hz
            or self.engine_settings.normal_eis_floor_hz
        )
        normal_target_lf_hz = float(
            analysis_result.recommended_normal_peis_lowest_freq_hz
            or analysis_result.recommended_peis_lowest_freq_hz
            or self.engine_settings.normal_eis_floor_hz
        )
        exploratory_seed = float(exploratory_seed_lf_hz or general_target_lf_hz)

        if analysis_result.peis_only_sufficient:
            action = "handoff_to_normal_lf"
            measurement_mode = "normal_eis"
            runtime_lf_hz = normal_target_lf_hz
            runtime_target_lf_hz = normal_target_lf_hz
            notes = [
                "PEIS-only signal is strong enough, so runtime should stop trusting the exploratory CP seed.",
                "Switching to the PEIS-based normal LF target keeps normal-mode follow-up aligned with the trusted analysis.",
            ]
        else:
            action = "keep_exploratory_seed_for_hybrid"
            measurement_mode = "rapid_eis"
            runtime_lf_hz = exploratory_seed
            runtime_target_lf_hz = general_target_lf_hz
            notes = [
                "Hybrid/rapid path remains active, so the exploratory CP seed stays in control.",
                "Runtime keeps the current-run CP seed until PEIS proves a later normal-mode handoff is safe.",
            ]

        relation = (
            "conservative_or_equal"
            if runtime_lf_hz <= runtime_target_lf_hz + 1e-12
            else "too_shallow"
        )
        if runtime_lf_hz > 0 and runtime_target_lf_hz > 0:
            log10_gap = abs(math.log10(runtime_lf_hz / runtime_target_lf_hz))
        else:
            log10_gap = float("inf")

        return CurrentRunPolicyDecision(
            action=action,
            measurement_mode=measurement_mode,
            exploratory_seed_lf_hz=exploratory_seed,
            general_target_lf_hz=general_target_lf_hz,
            normal_target_lf_hz=normal_target_lf_hz,
            runtime_lf_hz=runtime_lf_hz,
            runtime_target_lf_hz=runtime_target_lf_hz,
            runtime_vs_target_relation=relation,
            runtime_vs_target_log10_gap=float(log10_gap),
            notes=notes,
        )

    def finalize_completed_point(self, point_id: str, *, timeout_s: Optional[float] = None) -> Dict:
        analysis_ready = self.wait_briefly_for_analysis(point_id, timeout_s=timeout_s)
        self.poll_ready_full_processing()
        record = self._points[point_id]
        return {
            "point_id": point_id,
            "analysis_ready_within_wait": analysis_ready,
            "state": record.state,
            "analysis_result": None if record.analysis_result is None else record.analysis_result.to_dict(),
            "current_run_policy_decision": None if record.current_run_policy_decision is None else record.current_run_policy_decision.to_dict(),
            "full_processing_state": record.full_processing_state,
            "remeasurement_request": None if record.remeasurement_request is None else record.remeasurement_request.to_dict(),
        }

    def export_state(self) -> Dict:
        self.poll_ready_analyses()
        self.poll_ready_full_processing()
        return {
            "engine_settings": self.engine_settings.to_dict(),
            "analysis_settings": asdict(self.analysis_settings),
            "full_processing_settings": asdict(self.full_processing_settings),
            "stabilization_settings": asdict(self.stabilization_settings),
            "points": {point_id: record.to_dict() for point_id, record in self._points.items()},
        }
