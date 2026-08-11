# Update Log

This file tracks cross-machine and cross-AI edits for the `Microprobe Python` project.
최신 항목은 **맨 아래**에 추가.

---

## 2026-04-18 | Codex

- Re-created this update log after it was overwritten so the recent GUI and BioLogic work remains documented.
- Added a `Live Monitor` tab in the GUI to watch measurement progress during a run.
- Added live CA/CP current plotting and live impedance Nyquist plotting in the GUI using incoming BioLogic data segments.
- Connected the BioLogic driver callback path so CA hold, PEIS, and CA perturbation can push live segments to the GUI while measuring.
- Updated the measurement sequence to emit run-step and data events for the GUI monitor during Rapid EIS execution.
- Added a `Manual Control` tab with current-state readback and target-state controls for temperature, motor XYZ, and gas flows.
- Added COM port refresh and serial autodetect in the hardware tab so motor, temp controller, and MFC ports can be re-selected when Windows changes COM numbers.
- Rolled back the COM port refresh/autodetect UI and probing logic after it proved unstable in practice. Returned serial device connection flow to the original fixed-port behavior.
- Re-enabled COM port refresh/autodetect after confirming the earlier connection failure was caused by another program holding the serial ports open, not by the autodetect feature itself.
- Hardened GUI shutdown so closing the window now disconnects hardware, quits Tk cleanly, and then force-exits the Python process to avoid Anaconda Prompt staying open.
- Added `Copy Current -> Target` in Manual Control so the current hardware state can be copied into the editable target fields with one click.
- Added a `Quick EIS` panel in Manual Control for one-off manual PEIS runs using the connected BioLogic, with results sent to the live monitor.
- Expanded the Conditions table to show PEIS high/low frequency, PEIS point count, CA dt, and a new `GasStableTime_s` column.
- Updated automation logic so temperature, gas, and stage actions are skipped when the next row keeps the same conditions.
- Changed gas stabilization to use `GasStableTime_s` with a 600 s default instead of a hard-coded 30 s wait on every row.
- Added a first-pass manual `Find Contact Z` workflow that uses BioLogic OCV while stepping Z downward from a start offset and then applies a small extra engage distance after contact is detected.
- Contact-search thresholds, step size, settle time, and engage depth still need experimental tuning and are intentionally exposed as editable parameters in the GUI.

---

## 2026-04-18 | Claude (Sonnet)

- 프로젝트 전체 코드 구조 파악 (코드 수정 없음)
- `Microprobe Project/` 폴더는 기존 LabVIEW 프로그램임을 확인 (수정 대상 아님)
- `requirements.txt`에 `pywatlow` 누락 확인
- UPDATE_LOG.md 실수로 초기화 후 Codex 기록 복원

---

## 2026-04-19 | Codex

- Confirmed the new manual-control additions are present in the active top-level Python project, including `Copy Current -> Target`, live OCV readout, `Quick EIS`, and `Find Contact Z`.
- Added BioLogic-side OCV read support (`get_ocv`) so the GUI can check contact state without starting a measurement program.
- Implemented a first-pass manual Z contact-search routine that starts slightly above the target Z, steps downward, checks OCV after each step, and then moves slightly further down after contact for more stable measurement contact.
- Left the Z contact-search parameters user-editable in the GUI because the practical values still need to be tuned experimentally on the real setup.
- Explicit follow-up needed: validate OCV threshold, Z step size, per-step settle time, and final engage distance under real temperature and probe-contact conditions before relying on this for unattended runs.
- Renamed the existing `Conditions` tab to `Semi-auto` and added a new `Full-auto` tab so the two workflows are visually separated in the GUI.
- Added a first-pass `Full-auto` condition generator that expands temperature lists, gas-pair lists, voltage lists, and electrode ranges into rows and pushes them into the `Semi-auto` table for review before running.
- Implemented linear XY interpolation between electrode 1 and electrode N inside the `Full-auto` generator so intermediate electrode positions can be generated automatically.
- Left Z in the `Full-auto` generator as a seed value for now; automatic OCV-based contact finding still needs to be connected into the actual run loop for true unattended electrode-by-electrode operation.
- Updated the Rapid EIS sequence definition to support a separate `PostPEIS_HoldTime_s` at `V_dc` after PEIS and before the long CA at `V_dc + dV`.
- Implemented the post-PEIS CA as one CA technique with two internal sequences (`V_dc` short hold, then `V_dc + dV` long hold) instead of saving two separately run CA techniques and stitching files afterward.
- Added `PostPEIS_HoldTime_s` to the `Semi-auto` table, `Full-auto` generator inputs, and `conditions_template.csv`.
- Changed file output so each measurement no longer creates a per-label subfolder; raw files are now written directly into the selected result folder.
- Added saving for the pre-PEIS stabilization CA with filenames prefixed by `Pre_stabilization_`, while keeping PEIS and post-PEIS CA sequence files saved separately in the same result folder.
- Unified `dV` usage so the same value is now applied to both the PEIS amplitude and the post-PEIS CA voltage step.
- Cleaned up several mojibake-corrupted log messages in `gui.py` so run-status messages are readable again.
- Hardened `get_ocv()` to fail with an explicit message if the installed `easy-biologic` device object does not provide `get_values()`, since that API still needs confirmation against the actual package version used on the instrument PC.
- Created a new `Reference&Old stuff` folder in the project root and moved non-runtime reference materials into it, including the legacy `Microprobe Project` folder, troubleshooting/test scripts, hardware manuals, logs, and old support files.
- Kept only the active runtime files in the project root and re-ran Python syntax checks on the core application modules after the cleanup.
- Added one-click GUI launchers in the project root: `Launch_Microprobe_GUI.bat` for a simple batch launch and `Launch_Microprobe_GUI.vbs` for launching the GUI without keeping a visible console window open.
- Copied the analysis-side CP/CA-to-EIS codebase into `Analysis_Convert_CP_to_EIS/` inside the active project so future measurement-analysis integration can happen locally without modifying the original external analysis folder.
- Included the current adaptive-measurement prototype files in that local analysis copy and confirmed the copied modules still import from the new location.

---

## 2026-04-19 | Claude (Sonnet)

- 코드 수정 없음 — 전체 파일 현황 파악 및 Codex 로그 대조
- gui.py 1710줄, driver_biologic.py 222줄, measurement_sequence.py 106줄 확인
- 탭 6개(Hardware / Semi-auto / Full-auto / Run·Monitor / Live Monitor / Manual Control) 전부 구현 확인
- 한글 깨짐 및 get_ocv() 문제는 Codex가 수정 완료 확인
- 미결: get_ocv()의 get_values() API는 장비 연결해서 검증 필요

---

## 2026-04-19 | Claude (Sonnet) — 2차 세션

- 실제 프로젝트 위치가 `G:\My Drive\...\Microprobe Python\`임을 파악 (이전에 `Desktop\Microprobe Project\Python\`에 중복 생성한 것은 무효 — 삭제 권장)
- HML DMFC utility tool 스크린샷으로 MFC 스펙 확인: ID 007 = 100 sccm, ID 008 = 1000 sccm (config.py의 전체 스케일 값 수정 필요)
- Aera MFC 프로토콜: Digital 모드, COM5 / 9600 bps 확인
- driver_mfc.py에 `probe_protocol(log_fn=)` 파라미터 추가 — GUI 로그에 직접 출력 가능하도록
- gui.py Hardware 탭에 `MFC Protocol Test` 버튼 추가 제안 (현재 Codex 버전에는 미반영 — 필요 시 병합 필요)
- 미결: Codex 버전 gui.py에 MFC Protocol Test 버튼 병합, config.py MFC 전체 스케일 값 확인 및 수정

---

## 2026-04-19 | Codex

- Added a GUI-independent adaptive measurement layer to the active project with `adaptive_types.py`, `analysis_adapter.py`, and `adaptive_engine.py`.
- Added internal dataclasses for point metadata, measurement execution records, analysis payloads, recommendation payloads, remeasurement requests, and per-point state tracking.
- Added a fast machine-readable analysis adapter that uses the copied `Analysis_Convert_CP_to_EIS/` backend without figure or Excel export so recommendations can be produced quickly after a measurement finishes.
- Standardized local analysis outputs to include `recommended_peis_lowest_freq_hz`, `recommended_peis_conservative_cp_time_s`, `data_sufficient`, `peis_only_sufficient`, confidence, reason, and conservative/provisional notes.
- Implemented a headless adaptive engine with hybrid-wait behavior: it can briefly wait for analysis, continue with conservative fallback when analysis is late, and apply late analysis results to later points.
- Implemented internal point-state transitions covering measurement complete, analysis pending, recommendation ready, applied-to-future-point, and remeasure-required.
- Implemented automatic remeasurement request generation when the analysis says the current point is insufficient or when the actual run parameters are too optimistic relative to the recommendation.
- Kept the adaptive policy conservative by reusing the copied `adaptive_measurement_policy.py` logic for same-electrode preference, first-pass fallback, asymmetric trend handling, and mode selection between normal EIS and Rapid EIS.
- Added `run_adaptive_engine_demo.py` to dry-run the internal engine without GUI or hardware and to save a machine-readable summary under `result/adaptive_engine_demo/`.
- Added `test_adaptive_engine.py` with unit and integration coverage for history selection, asymmetric trend behavior, first-pass fallback, remeasurement generation, mode selection, hybrid wait behavior, and fast analysis adapter output generation from saved files.
- Verified the new modules with Python syntax checks, ran the dry-run adaptive demo successfully, and passed all `python -m unittest test_adaptive_engine.py` checks.
- Added `stabilization_policy.py` for pre-PEIS bias-stabilization assessment, adaptive pre-hold planning from delta-V/history, and bias sweep order scoring.
- Extended the internal adaptive engine so it can assess whether the pre-PEIS CP looks stable enough, recommend more hold time, and request a retry if PEIS started before bias stabilization was convincing.
- Added internal support for planned pre-PEIS hold time in recommendation payloads and remeasurement requests so later runtime integration can adapt bias-stabilization time per point.
- Added dry-run and unit-test coverage for pre-PEIS stabilization success, PEIS abort/retry on unstable bias, and bias sweep order selection.
- Re-ran `python -m unittest test_adaptive_engine.py` after the stabilization additions and kept the full adaptive-engine test suite passing.
- Added a 20 s floor for planned pre-PEIS stabilization hold and planned post-PEIS hold inside the internal adaptive engine.
- Added internal post-PEIS hold recommendation logic so the short CA at `V_dc` before stepping to `V_dc + dV` can be adapted from delta-V and history instead of staying fixed forever.
- Extended recommendation and remeasurement payloads to carry both `planned_pre_peis_hold_s` and `planned_post_peis_hold_s` for later runtime/GUI hookup.
- Added test coverage to keep the post-PEIS hold recommendation at or above 20 s.
- Added `full_processing_adapter.py` so the copied analysis project can run a heavier export/processing path in the background while fast recommendation logic continues to drive the next point.
- Extended the adaptive engine with a separate full-processing executor, per-point full-processing state, queue-status reporting, and `start_post_measurement_pipeline()` to launch fast analysis and background processing together.
- Added unit coverage proving that fast analysis can be used for the next-point decision while slower full processing keeps running in the background and finishes later.
- Re-ran the adaptive-engine test suite after the queue split and kept all tests passing.
- Debugged manual hardware-command failures in the active GUI path after field testing showed that BioLogic, MFC, and Watlow would connect but ignore action commands while the motor still worked.
- Hardened `gui.py` manual actions so exceptions raised by worker threads are surfaced in the Manual Control status bar and the GUI log instead of failing silently.
- Updated `driver_biologic.py` to use the documented `easy-biologic` 0.4.x PEIS/CA parameter names and `run('data')`, and removed the explicit `sweep` key because the library mishandles `dict` params via `params.sweep`, which matched the observed `Quick EIS failed: 'dict' object has no attribute 'sweep'` error.
- Reworked `driver_temp.py` to use pywatlow write APIs compatible with parameter 7001 setpoint writes, added explicit response/error validation, and added setpoint readback logging after each temperature command.
- Reworked `driver_mfc.py` so MFC setpoint commands now force flow mode + valve open before writing, raise on non-OK serial responses, and report readback setpoints instead of silently accepting failed commands.
- Updated `requirements.txt` to pin `easy-biologic` to the supported 0.4.x line and clarified the Watlow COM3 comment in `config.py` to reflect Standard Bus / pywatlow usage.
- Re-ran Python syntax checks on `gui.py`, `driver_biologic.py`, `driver_temp.py`, `driver_mfc.py`, and `measurement_sequence.py` after the manual-command fixes.
- Added temperature ramp-rate handling across Manual Control, Semi-auto rows, Full-auto generation, and the automated run loop using a new `RampRate_C_per_min` field.
- Added Watlow ramp-rate helper methods to `driver_temp.py` and hooked temperature commands so the configured ramp rate is written before each new setpoint is sent.
- Updated `conditions_template.csv` to include the new ramp-rate column with a default seed value.
- Disabled Aera MFC digital write commands by default via `MFC_WRITE_ENABLED = False` in `config.py` after real hardware testing showed that the current protocol guess can drive both MFCs into unsafe high-flow behavior.
- Added `mfc_protocol_diagnostic.py` as a read-only probe script for the Aera controllers so the real instrument PC can be queried without sending any setpoint or valve-changing commands.
- Confirmed on the lab PC that the active Aera read protocol is `STX + hex-address` rather than the legacy `@007Q`-style ASCII path, because `RFK/RFX/RFD` read probes returned valid values while the legacy probes returned nothing.
- Updated `driver_mfc.py` to treat `RFK`, `RFX`, and `RFD` responses as engineering-unit values (sccm) instead of percent-of-full-scale, based on the real diagnostic output (`RFK=100/1000`, `RFX≈0`, `RFD=0`).
- Simplified MFC setpoint writing to send clamped direct-sccm `SFD...` values without the earlier percent conversion logic, while still keeping digital writes disabled by default until a small controlled hardware validation run is completed.
- Fixed the Live Monitor fallback path for Quick EIS and Rapid EIS so the GUI now queues the final parsed PEIS/CA datasets even when the installed `easy-biologic` build does not emit streaming `on_data` callbacks during measurement.
- Updated Manual `Quick EIS` saving so PEIS data is now written automatically under the selected result folder inside a dedicated `Manual EIS` subfolder instead of being left unsaved.
- Added row-level skip tokens (`None`, `non`, blank, `-`, `skip`, `na`, `n/a`) for temperature, gas, and stage fields so dummy-cell or BioLogic-only tests can bypass unused hardware steps without changing the rest of the automation flow.
- Updated both the GUI run loop and `run_automation.py` to interpret those skip tokens consistently, and made the CLI runner avoid connecting temperature / MFC / motor hardware when the loaded condition file does not actually request those devices.
- Added `Full-auto` hardware-use toggles (`Use temperature`, `Use gas`, `Use tip position`) in the GUI so users can disable unused hardware directly from the generator; disabled sections now gray out their input boxes and emit `None` values into generated rows.
- Updated visible GUI wording from `stage` to `tip` where appropriate in the manual controls and run-monitor messaging so the interface better matches the actual motion hardware concept.
- Fixed BioLogic CA parsing for the installed `easy-biologic` variant by allowing CA data rows to use `voltage/current` fields instead of assuming `Ewe/I`; this unblocks Rapid EIS automation from failing in the pre-PEIS CA hold before any files are saved.
- Synced the copied internal analysis backend with the latest external `Convert_CP_to_EIS 1` project for the files that matter to adaptive measurement planning: `compare_full_vs_optimized_fit.py`, `Load_CP_Data.py`, `Convert_CP_to_EIS.py`, `EIS_Fitting.py`, `run_trusted_sample_analysis.py`, and the new `export_origin_friendly.py`.
- Reworked `analysis_adapter.py` so the internal adaptive engine now uses the latest optimized-analysis entrypoint (`recommend_optimized_configuration`) instead of the older simplified FFT-only bridge.
- Extended the internal machine-readable `AnalysisResult` payload with newer analysis outputs including `recommended_cp_highest_freq_hz`, `minimum_valid_cp_duration_s`, `saturation_recommended_cp_duration_s`, `optimization_selection_reason`, `peis_only_selection_reason`, `cp_saturation_*`, `postcheck_reason`, and `agreement_rel_err`.
- Added logic in the adapter to classify `PEIS-only sufficient` cases as hybrid-unnecessary and to mark hybrid cases as insufficient when the optimized analysis says the measured CP trace is still too short or the CP tail is not saturated enough.
- Updated the adaptive engine so post-measurement analysis now prefers `post_ca_path` over `pre_ca_path` when both exist, which better matches the intended CP/PEIS pairing for the later adaptive workflow.
- Extended `RecommendationPayload` so future runtime integration can see whether hybrid was unnecessary and what analysis reason drove the suggestion, without needing GUI changes yet.
- Added unit coverage proving that the adaptive engine prefers the post-PEIS CA file when choosing the DC trace to analyze.
- Re-ran `python -m unittest test_adaptive_engine.py` after the analysis-backend update and kept all 13 tests passing.
- Re-ran `run_adaptive_engine_demo.py` after the update and regenerated `result/adaptive_engine_demo/adaptive_engine_demo_summary.json`.
- Verified the new adapter directly on `Convert_CP_to_EIS 1/Input data/260419 microprobe semiauto-3/V+0.000` and confirmed that the internal result now captures the external analysis judgment `PEIS-only sufficient = True` with `reason = PEIS already appears to reach the LF saturation region, so hybrid is unnecessary`.
- Updated the internal adaptive policy so `PEIS-only sufficient` points are treated as valid history even when no hybrid CP duration is returned, which keeps same-electrode experience available for later points.
- Updated the internal adaptive policy so `normal_eis` / `PEIS-only` recommendations no longer carry a fake hybrid CP duration by default when the analysis did not actually request one.
- Added `simulate_adaptive_sequence.py`, a headless replay utility that reads an existing measured folder, analyzes each point with the current internal engine, and records what the auto-system would have recommended for the next point.
- Replayed `Convert_CP_to_EIS 1/Input data/260419 microprobe semiauto-5` through the new sequence simulator and saved `result/adaptive_sequence_simulations/260419 microprobe semiauto-5_adaptive_sequence.json`.
- The current simulated sequence for `260419 microprobe semiauto-5` says `V+0.000 -> V+0.100` and `V+0.100 -> V+0.200` would already have switched to `normal_eis` with `hybrid_unnecessary = True`, while the final `V+0.200` analysis itself also came back `PEIS-only sufficient` and suggested a much higher PEIS cutoff than the actually measured `0.01 Hz`.
- Generalized `simulate_adaptive_sequence.py` so it can replay both Microprobe-style timestamped text folders and generic `.mpr/.mpt/.txt/.csv/.dat` PEIS/CA pairs using the same base-name / file-type logic as the analysis project.
- Added a simple label parser to the sequence simulator so dataset names like `300 rapid measurement` and `400 rapid measurement` are treated as different temperature regimes during replay.
- Replayed `Convert_CP_to_EIS 1/Input data/260419-1` through the same simulator and saved `result/adaptive_sequence_simulations/260419-1_adaptive_sequence.json`.
- The current simulated sequence for `260419-1` found no replay/runtime errors and judged both `300 rapid measurement` and `400 rapid measurement` as `PEIS-only sufficient`, with the next-point recommendation after `300 rapid measurement` already switching to `normal_eis`, `hybrid_unnecessary = True`, `recommended PEIS lowest freq ~ 0.0425 Hz`, and `recommended hybrid CP duration = 0 s`.
- Added completed-hold optimization logic for both pre-PEIS CP and post-PEIS CP in `stabilization_policy.py` so the internal system can judge whether a finished hold trace already saturated early, was just right, or was still not stabilized enough.
- The new completed-hold assessment now returns `estimated_stable_time_s`, `recommended_next_hold_s`, `recommended_retry_hold_s`, and explicit flags for `should_extend_now` and `can_reduce_next_time`.
- Extended `MeasurementExecution` with `optimized_pre_peis_hold_s` and `optimized_post_peis_hold_s` so the system can remember a shorter/longer recommended hold for future points instead of blindly reusing the raw hold that happened last time.
- Updated `recommend_initial_pre_peis_hold(...)` and `recommend_post_peis_hold(...)` to seed from those optimized hold values rather than only from the raw observed hold length.
- Added adaptive-engine methods `assess_completed_pre_peis_hold(...)` and `assess_completed_post_peis_hold(...)` so later runtime integration can feed completed CP traces back into the internal hold optimizer.
- Added unit coverage for: shortening future pre-PEIS holds after an early plateau, extending future pre-PEIS holds when saturation is not reached, and carrying a shorter optimized post-PEIS hold into the next-point recommendation.
- Re-ran `python -m unittest test_adaptive_engine.py` after the hold-optimization update and kept all 16 tests passing.
- Tightened the runtime trust rules around `PEIS-only sufficient` so the internal auto-system no longer drops from Rapid/Hybrid to `normal_eis` just because the analysis backend returned a raw PEIS-only flag.
- Added runtime guards in `adaptive_engine.py` for three specific cases: heuristic PEIS-only reasons (`peis_plateau_and_no_hybrid_gain`, `peis_plateau_exceeds_cp_saturation`), weak PEIS/CP LF agreement, and first-point-of-new-regime transitions.
- Added a regime-aware conservative rule so the first point of a new `electrode + temperature + gas` regime stays on Rapid EIS even if the previous point's analysis says PEIS-only may be sufficient.
- Updated the runtime recommendation path so `hybrid_unnecessary` now reflects the guarded runtime decision rather than the raw backend flag.
- Updated the internal adaptive policy seed logic so, when an optimized hybrid CP duration is missing, the next-point planner can fall back to the actually measured CP duration instead of collapsing to an unrealistically short default.
- Added runtime fallback seeding from CP tail saturation duration when a raw PEIS-only judgment is rejected because the CP tail still appears unsaturated.
- Replayed `Convert_CP_to_EIS 1/Input data/260419-1` again and confirmed the internal auto-system now keeps `300 rapid measurement -> 400 rapid measurement` on `rapid_eis` with a suggested CP around 322 s, which is much closer to the actual measured rapid run.
- Replayed `Convert_CP_to_EIS 1/Input data/260419 microprobe semiauto-5` again and confirmed the internal auto-system now keeps the sequence on `rapid_eis` with a guarded CP suggestion around 287.5 s instead of prematurely switching to `normal_eis`.
- Added new adaptive-engine test coverage for: trusted PEIS-only transitions that are allowed to switch to normal EIS, new-regime first points that stay conservative, and heuristic PEIS-only cases that keep Rapid EIS while preserving a realistic CP seed.
- Re-ran `python -m unittest test_adaptive_engine.py` after the runtime trust-guard update and kept all 18 tests passing.
- Re-ran `simulate_adaptive_sequence.py` for both `260419-1` and `260419 microprobe semiauto-5` and regenerated the JSON summaries under `result/adaptive_sequence_simulations/`.
- Spot-checked the latest `analysis_adapter.py` output directly against the external `compare_full_vs_optimized_fit.recommend_optimized_configuration(...)` call on representative `.mpr` and Microprobe `.txt` cases and confirmed the machine-readable fields still match the analysis project outputs for the compared keys.
- Re-checked the external analysis project again after a newer timestamp appeared on `Convert_CP_to_EIS 1/compare_full_vs_optimized_fit.py`, verified that the local copied backend now matches the external hash, and re-ran unit tests plus the `260419-1` replay to confirm there was no regression.

---

## 2026-04-19 | Claude (Sonnet) — 버그 수정

- Fixed `run_automation.py` `finally` block so `motor`, `tc`, and `mfc` are guarded with `if` checks before calling `.disconnect()`; previously would raise `AttributeError` when those devices were not connected.
- Fixed `measurement_sequence.py` crash when PEIS returns an empty result array: added a `len(eis_data) == 0` guard that prints a warning and returns empty arrays immediately instead of crashing on `eis_data[:,0].min()`.

---

## 2026-04-20 | Codex

- Renamed the visible GUI workflow tabs so the editable condition table now shows as `CSV List`, the existing generator workflow shows as `Semi-auto`, and a new future-facing `Full-auto` tab appears beside it.
- Kept the current live measurement flow intact while adding a separate `Full-auto Adaptive Planner` UI for future analysis-driven next-point optimization work.
- Added a new `Normal EIS below (Hz)` input plus matching hardware-disable checkboxes in the new `Full-auto` tab.
- Added shared row-generation helper logic so the future `Full-auto` planner can already generate conservative base rows into the `CSV List` table without changing the current runtime behavior.
- Verified `gui.py` syntax with `py -m py_compile gui.py` and confirmed the module still imports successfully on the current machine.
