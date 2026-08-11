# Plan Log

This file tracks open work, agreed decisions, and validation items for the `Microprobe Python` project.
Latest entries should be appended at the bottom.

## Rules

- Use only these statuses: `todo`, `doing`, `blocked`, `done`
- Keep entries short and execution-oriented so another AI or engineer can continue immediately
- Record date and AI name for each entry block
- Mark experimental or hardware-validation items as `blocked` and state the blocker
- Do not delete completed items; change their status to `done`
- Use `UPDATE_LOG.md` for implemented changes and `PLAN_LOG.md` for open work, agreed decisions, and next steps
- Before any substantial work, append a new bottom entry claiming the exact scope being touched (`files`, `folders`, or `experiment/result areas`) and mark it `doing`
- When stopping, update that claim to `done` or `blocked` here and append a matching `Session end` note in `UPDATE_LOG.md` so other AIs know the scope is released
- Keep scope claims narrow; if another AI already claimed an overlapping area, split the work first instead of silently editing the same files
---

## 2026-04-19 | Codex

### BioLogic OCV Validation
- [blocked] Verify whether `BioLogicDevice.get_values()` exists in the installed `easy-biologic` version on the instrument PC
  Decision: keep `get_ocv()` guarded and fail explicitly until the real API is confirmed.
  Blocker: requires the actual instrument PC environment with the installed `easy-biologic` package and a connected BioLogic device.

### Full-auto Z Contact Integration
- [todo] Connect automatic Z contact search into the Full-auto run loop
  Decision: use the existing OCV-based contact-search approach as the first implementation path for unattended runs.
  Next: call the contact-search logic from the automated measurement path instead of only exposing it in Manual Control.

- [todo] Start Full-auto Z search from slightly above the prior Z position, then step downward to contact
  Decision: reuse the previous Z as a seed and approach from a slightly higher point rather than scanning from a fixed absolute Z each time.
  Next: define how the prior Z seed is updated between temperature, gas, electrode, and voltage transitions.

### Electrode Sequencing
- [todo] Connect electrode 1 ~ N XY interpolation to real electrode-by-electrode automated runs
  Decision: keep XY interpolation linear between electrode 1 and electrode N in Full-auto generation.
  Next: ensure the generated electrode rows flow through the actual run loop with the intended per-electrode measurement sequence.

- [todo] Validate Full-auto condition generation against the real automated execution flow
  Decision: keep Full-auto as a row generator feeding Semi-auto until the automated execution path is fully verified.
  Next: test that generated rows preserve intended order across temperature, gas, electrode, and voltage combinations.

### Contact Search Tuning
- [todo] Tune OCV threshold, Z step, settle time, and engage depth experimentally
  Decision: keep these parameters editable in the GUI because practical values still depend on the real setup.
  Next: record working values after instrument-side validation and decide whether any defaults should be updated.

### Adaptive Measurement Optimization
- [todo] Integrate the external data-analysis project later to recommend faster PEIS LF cutoff and CP/CA duration settings
  Decision: do not implement sample-analysis logic inside this project yet; wait until the separate analysis workflow is mature enough to trust for automated parameter suggestions.
  Next: define a clean handoff format so the analysis side can return recommended LF frequency and CP/CA duration values.

- [todo] Use analysis-driven recommendations conservatively for the next measurement instead of pushing the absolute minimum time
  Decision: choose a slightly more conservative operating point than the raw optimized point so measurement time is reduced without becoming too aggressive or unstable.
  Next: decide how much safety margin to add once the external analysis outputs are validated on real datasets.

- [todo] Let the analysis workflow decide whether the collected data at a condition is still insufficient and trigger a re-measurement with updated parameters
  Decision: allow adaptive re-measurement at the same condition when the first pass does not capture enough low-frequency information or arc completion.
  Next: define what analysis outputs should mark a dataset as insufficient and how the measurement parameters should be updated before retrying.

- [todo] Reuse optimized parameters by electrode history instead of blindly copying the immediately previous point
  Decision: when entering a new electrode under a new gas or temperature condition, prefer the most recent optimized parameters from that same electrode under prior conditions over parameters from a different electrode measured just before it.
  Next: define the lookup priority for parameter reuse, such as same condition + same electrode first, then same electrode under earlier conditions, then fallback to the immediately previous point only if no same-electrode history exists.

- [todo] Treat the first measurement at a new operating regime conservatively before adaptive optimization takes over
  Decision: the very first measurement can start from a more conservative parameter set, with the understanding that the workflow may re-measure if the analysis still judges the data insufficient.
  Next: decide what default conservative starting settings should be used for the first pass at a new regime.

- [done] Add a mode-selection rule between normal EIS and Rapid EIS based on analysis results
  Result: the adaptive engine now applies runtime trust guards before switching to `normal_eis`; heuristic PEIS-only flags, weak LF agreement, and first-point-of-new-regime cases stay on Rapid EIS. `measurement_sequence.py` now exposes `normal_eis_sequence()` and returns `SequenceResult` objects for both modes.

- [todo] Expose the PEIS-only / normal EIS trust threshold as a user-configurable parameter
  Decision: the current trust guards are coded as internal constants; once the thresholds are validated on more real datasets the decision boundary (LF cutoff, CP saturation margin, regime-first-point rule) should be surfaced as editable config or GUI fields.
  Next: after more real-sample replay validation, decide which thresholds are worth exposing to the user and where (config.py vs GUI settings panel).

### Completed Decisions
- [done] Split the old Conditions workflow into `Semi-auto` and `Full-auto`
  Result: the GUI now separates direct row editing from generated-condition workflows.

- [done] Define post-PEIS CA as a single CA technique with multiple sequences
  Result: post-PEIS CA now runs as one CA technique containing the short `V_dc` hold followed by the long `V_dc + dV` hold.

- [done] Save pre-PEIS stabilization CA as a separate raw file
  Result: pre-PEIS stabilization CA is saved with the `Pre_stabilization_` filename prefix in the main result folder.

- [done] Move non-runtime reference and legacy files out of the project root
  Result: old manuals, logs, troubleshooting scripts, and the legacy `Microprobe Project` folder were moved into `Reference&Old stuff` to keep the active project root clean.

## 2026-04-19 | Codex

### Adaptive Engine Runtime
- [done] Add a GUI-independent adaptive orchestration layer inside `Microprobe Python`
  Result: the project now has `adaptive_engine.py` and `adaptive_types.py` for point-state tracking, hybrid wait handling, next-point planning, and late remeasurement requests without touching the GUI.

- [done] Standardize a callable machine-readable analysis backend around the copied local analysis package
  Result: `analysis_adapter.py` now returns a consistent payload with PEIS LF recommendation, conservative CP recommendation, sufficiency flags, confidence, reason, and provisional notes.

- [done] Add headless dry-run and automated tests for adaptive measurement logic
  Result: `run_adaptive_engine_demo.py` and `test_adaptive_engine.py` now exercise fast-analysis, delayed-analysis, fallback, trend-aware recommendation, and remeasurement behavior without hardware.

- [done] Wire the internal adaptive engine into the live GUI run loop for `ADAPT*` rows
  Result: the GUI now calls `AdaptiveMeasurementEngine.recommend_next_point(...)` before each adaptive row and applies the recommended `PEIS_fLow`, `CA_duration_s`, `HoldTime_s`, and `PostPEIS_HoldTime_s`. The first point of each same-regime block still uses the conservative CSV seed. `run_automation.py` CLI wiring is still pending separately.

- [done] Validate the fast analysis adapter on real BioLogic files produced by this project
  Result: real dummy-cell adaptive runs were completed (`results/adaptive_runtime_dummy_test_quick/`, `results/adaptive_runtime_dummy_test_2rows/`), adapter output was spot-checked against `recommend_optimized_configuration(...)` on `.mpr` and `.txt` files, and a major adapter mismatch was found and fixed (`auto_trim=False` -> `True`, reversed `peis_plateau_and_no_hybrid_gain` sign, current-unit auto-detection from `I/A` headers).

- [done] Revisit the provisional `data_sufficient` and `peis_only_sufficient` heuristics once the analysis project matures
  Result: the thresholds and trust rules have been substantially tightened through real-data replay across `16`+ references; the runtime now uses PEIS-plateau as the primary `normal_eis` signal with CP-saturation, LF agreement, and new-regime guards as secondary checks. Heuristic reasons (`peis_plateau_and_no_hybrid_gain`, `peis_plateau_exceeds_cp_saturation`) now carry explicit trust levels rather than all being treated equally.
- [done] Add internal bias-stabilization assessment for the pre-PEIS CP hold
  Result: `stabilization_policy.py` now evaluates drift/noise/range over recent CP windows and can recommend continue-hold, start-PEIS, or abort-and-retry behavior.

- [done] Add adaptive pre-PEIS hold planning based on delta-V and history
  Result: the internal engine now proposes planned pre-PEIS hold time using bias-step size and prior stabilization history instead of assuming one fixed hold for every bias.

- [done] Add internal bias sweep order scoring
  Result: the engine can now compare ascending vs descending bias orders using predicted stabilization burden and return a preferred internal order without changing the GUI yet.

- [todo] Validate pre-PEIS stabilization thresholds on real live current traces
  Decision: the current drift/noise/range thresholds are a strong first pass, but they still need instrument-side tuning against real microprobe bias-settling behavior.
  Next: record live CP traces at several temperatures, gases, electrodes, and bias steps and adjust the stabilization thresholds and window rules.
- [done] Add a 20 s minimum floor for both pre-PEIS and post-PEIS hold recommendations
  Result: the internal engine now keeps both stabilization-related holds at or above 20 s even when adaptive logic tries to shorten them.

- [done] Add internal optimization for the post-PEIS short hold at `V_dc`
  Result: the engine now recommends the post-PEIS hold from delta-V and history instead of treating it as a permanently fixed constant.
- [done] Split post-measurement work into a fast recommendation path and a background full-processing path
  Result: the internal engine can now hand optimized parameters to the next measurement quickly while heavier export/processing continues in a separate queue.

- [todo] Decide when and where the background full-processing results should surface in the runtime or GUI
  Decision: keep the queue internal for now, but preserve per-point status/result fields so the UI can later show whether full processing is still running or already finished.
  Next: choose whether the first integration should surface only a simple processing-status badge or a richer per-point processing summary.

## 2026-04-19 | Codex

### Temperature Ramp Control
- [blocked] Validate the Watlow ramp-rate write path on the real controller using parameter 7015 (scale select) and 7003 (ramp rate)
  Decision: the GUI and automation now carry `RampRate_C_per_min`, but the actual Watlow parameter IDs and units still need one instrument-side confirmation run.
  Blocker: requires the real EZ-ZONE controller on COM3 to confirm that the configured ramp rate changes the controller behavior as expected.

### MFC Protocol Safety
- [blocked] Validate Aera digital write commands before re-enabling gas control from this program
  Decision: disable MFC write commands by default because current `set_flow()` attempts can drive the real controllers into unsafe high-flow behavior.
  Blocker: requires protocol verification against the real Aera hardware and utility software before `MFC_WRITE_ENABLED` can be safely turned back on.

- [done] Identify which Aera read protocol is actually alive on the lab hardware
  Result: the real instrument PC responds to `STX + hex-address` `RFK/RFX/RFD` reads, while the legacy `@007Q`-style probes return nothing.

- [done] Decide whether current Aera readback values should be interpreted as percent or engineering units
  Result: the observed diagnostic output matches engineering-unit `sccm` values (`RFK=100/1000`, `RFD=0.0`, `RFX≈0`) much better than percent scaling, so the runtime driver now treats them as direct engineering units.

- [blocked] Re-enable Aera write commands only after a controlled low-flow validation run
  Decision: the next hardware test should use one tiny direct-sccm setpoint with the vendor utility closed and should verify that `SFD` changes only the intended channel without forcing valves fully open.
  Blocker: requires a supervised lab-PC test because earlier write attempts drove both MFCs into unsafe high-flow states.

- [done] Validate the current Rapid EIS text export directly against the analysis pipeline on dummy-cell / bench data
  Result: real dummy-cell Rapid EIS runs produced `.txt` files that were analyzed by `analysis_adapter.py`. Key fix found: the adapter was using `auto_trim=False` (offline default is `True`) and had incorrect current-unit detection for text files with `I/A` headers, causing over-conservative recommendations. Both issues were fixed and re-validated on saved real output.

- [todo] Revisit native BioLogic file preservation if a reliable `.mpr` path is found later
  Decision: the current runtime should assume parsed text export is the supported path unless an official / verified native `.mpr` save route is identified for the installed `easy-biologic` workflow.
  Next: if future instrument-side validation reveals a trustworthy native save method, consider storing both native raw output and parsed text export side by side.

## 2026-04-20 | Codex

### Adaptive Analysis Integration
- [done] Refresh the internal adaptive-analysis bridge against the latest external `Convert_CP_to_EIS 1` project
  Result: the copied backend files that drive recommendation logic were resynced, and `analysis_adapter.py` now uses the newer `recommend_optimized_configuration(...)` path rather than the older FFT-only shortcut.

- [done] Bring `PEIS-only sufficient` / hybrid-unnecessary judgment into the internal adaptive engine
  Result: internal analysis payloads now expose PEIS-only selection reasons, CP saturation guard outputs, optimized CP crossover metadata, and post-check reasons; the engine can now carry that richer information forward without GUI changes.

- [done] Prefer the post-PEIS CA file when the adaptive engine chooses which DC trace to analyze
  Result: when both `pre_ca_path` and `post_ca_path` exist, the internal engine now uses `post_ca_path` as the DC input for adaptive analysis because it is the more relevant CP/CA trace for the later hybrid/planning workflow.

- [done] Wire the updated internal adaptive decisions into the real runtime loop
  Result: the runtime guard was switched from CP-saturation-first to PEIS-plateau-first, with trusted plateau reasons (`peis_reaches_saturation`, `peis_plateau_and_no_hybrid_gain`) driving the `normal_eis` switch, and secondary checks for LF agreement and new-regime first-points keeping the switch conservative. Validated on the real dummy-cell bias sweep (`adaptive_bias_sweep_20260421_000350`).

- [todo] Run a final cross-project reconciliation / cleanup pass after the main adaptive and runtime work stabilizes
  Decision: before calling the full-auto system "done", do one explicit integration pass to find duplicated logic, partially diverged implementations, stale copied code, and mismatched assumptions between GUI, runtime, adaptive engine, and analysis backend.
  Next: when the feature set settles, review the whole project end-to-end, merge overlapping logic where sensible, verify that automation still runs cleanly, and validate on real output data that parameter extraction and adaptive recommendations behave as intended.

## 2026-04-20 | Codex

### Full-auto Motion Safety
- [done] Add a shared stage-travel planner that can express `Z up -> XY move -> Z down`
  Result: `driver_motor.py` now has `plan_safe_xyz_move(...)` / `move_xyz_safe(...)`, and both `gui.py` and `run_automation.py` use the shared planner logic instead of open-coded XYZ loops.

- [done] Prevent safe-mode XY travel when the planner cannot prove a safe Z reference
  Result: the planner now raises `UnsafeStageMoveError` if safe move is enabled but XY travel is requested without either an absolute `clearance_z_mm` or a readable current Z position. This closes the previous hole where safe mode could still fall through to direct XY travel when Z was unknown.

- [todo] Validate the real stage Z sign and travel-safe clearance height, then enable `STAGE_SAFE_MOVE`
  Decision: the safe-move structure is now in place, but it stays disabled until the real hardware confirms whether positive Z truly means "tip away from sample" and what the safe XY travel clearance should be.
  Next: once motor communication is restored, record one or two supervised moves, set `positive_z_is_up` correctly, choose `clearance_z_mm` or `lift_delta_mm`, and only then enable the policy.

- [todo] Decide the desired UX/runner behavior when `UnsafeStageMoveError` is raised
  Decision: the hard safety block is now correct, but the runtime policy still needs to be chosen explicitly: stop the whole run immediately, skip only the affected row, or request operator intervention.
  Next: once the operator preference is clear, handle the exception consistently in both `gui.py` and `run_automation.py` instead of relying on the generic outer error path.

### Adaptive Runtime / Simulation
- [done] Keep normal-EIS points in the adaptive feedback loop
  Result: PEIS-only / normal-EIS points are now analyzed through a dedicated follow-up path, so they can still refresh the next LF cutoff rather than disappearing from the optimization history.

- [done] Add periodic rapid-refresh protection on top of trusted normal-EIS switching
  Result: even if the runtime starts trusting PEIS-only, the engine now forces a periodic `rapid_eis` refresh after too many consecutive normal points so LF/CP optimization does not drift blind.

- [todo] Investigate why the live sweep summary for `adaptive_bias_sweep_20260421_000350` showed missing inline analysis on some middle points while the offline replay recovered those points successfully
  Decision: the analysis itself is not missing from the raw files; the gap is likely a runtime wait / reporting issue rather than a scientific one.
  Next: compare the live runner wait window, background queue timing, and summary-write timing so the JSON log always captures the same analysis state that the offline replay can recover.

- [todo] Keep replaying newly generated real-result folders and compare them against the live runtime decisions
  Decision: the recursive `simulate_adaptive_sequence.py` replay is now good enough to become part of the normal validation loop after every meaningful runtime tweak.
  Next: after each real dummy-cell or sample sweep, replay the saved folder through the simulator and note where live decisions and replayed decisions diverge.

### Image-guided Full Auto
- [done] Add the core pixel-to-stage mapping math for future OM/design-image workflows
  Result: `vision_stage_mapper.py` now supports affine calibration from clicked reference points plus regular-grid generation for electrode candidate mapping.

- [todo] Build a click-based OM calibration utility on top of `vision_stage_mapper.py`
  Decision: the math layer now exists; the next useful step is a lightweight utility that lets a user load an OM/design image, click a few reference landmarks, and save the stage calibration / electrode map.
  Next: implement an image loader + click capture tool and store the calibration as JSON for reuse in full-auto runs.

- [todo] Add microscope-camera verification during measurement / after stage moves
  Decision: future full-auto should not rely only on programmed XY coordinates; it should optionally verify that the tip is actually over the intended electrode after a move.
  Next: define the first camera loop as a simple overlay / ROI check rather than full contact inference, then expand toward drift correction and electrode confirmation.

- [todo] Use `OM/` image assets plus user clicks or simple geometric priors to propose electrode-center candidates automatically
  Decision: the first version does not need full CV; a hybrid workflow where Codex proposes centers from an OM/design image and the user confirms/corrects them is good enough to start.
  Next: inspect representative `OM/` images, decide the expected sample layout assumptions, and build a candidate-center proposal routine.

- [todo] Replay more real measured folders through the adaptive-sequence simulator before runtime hookup
  Decision: the new `simulate_adaptive_sequence.py` utility is a good low-risk way to sanity-check whether the internal auto-system would have made reasonable next-point choices on already measured data.
  Next: run the simulator on more dummy-cell and real-sample folders beyond `260419 microprobe semiauto-5`, then review where the suggested mode/cutoff/duration choices still look too aggressive or too conservative.

- [done] Keep `Rapid EIS` as the runtime default unless `PEIS-only sufficient` is strongly trusted enough to drop hybrid
  Result: runtime trust logic is now PEIS-plateau-first with secondary LF-agreement and new-regime guards. `peis_plateau_exceeds_cp_saturation` was demoted from a hard block. Validated across the real dummy-cell sweep and `Convert_CP_to_EIS 1/Input data` reference folders. The `peis_plateau_and_no_hybrid_gain` heuristic was also tightened so it no longer flips obvious hybrid cases (e.g. `260419 300/400 rapid`) into PEIS-only.

- [todo] Connect completed pre/post hold optimization to the real runtime loop once live trace handoff is ready
  Decision: the internal engine can now judge completed pre-PEIS and post-PEIS CP traces and recommend shorter/longer future hold durations, but the live measurement loop still needs to call those assessments at the right moments.
  Next: when runtime integration starts, feed the completed hold traces into `assess_completed_pre_peis_hold(...)` and `assess_completed_post_peis_hold(...)`, then use those optimized hold values in subsequent points.

- [todo] Run a final codebase reconciliation pass after the adaptive/runtime logic settles
  Decision: once the current guard logic and runtime hookup stabilize, explicitly compare `gui.py`, `run_automation.py`, `adaptive_engine.py`, and the copied `Analysis_Convert_CP_to_EIS/` backend for duplicated or diverged rules before calling the autosystem stable.
  Next: do one end-to-end sweep that checks duplicated helpers, stale copies, replay scripts, runtime hooks, saved-file compatibility, and whether the measured output still reproduces the analysis project recommendations where expected.

- [todo] Revisit the `peis_plateau_exceeds_cp_saturation` block in runtime trust logic
  Decision: after the PEIS-plateau-first runtime update, the main remaining mismatch against older semiauto datasets is that `V+0.000` / `V+0.100` in `260419 microprobe semiauto-3/4/5/6` still replay as `rapid_eis` only because of this reason.
  Next: compare those semiauto folders against the older `260419 microprobe semiauto-6 analysis` conclusion and decide whether this reason should be demoted from a hard runtime block to a softer warning when the PEIS LF tail is already plateau-like.

- [todo] Broaden the corrected replay validation beyond the current spot-check set
  Decision: after fixing the runtime adapter mismatch (`auto_trim` + current-unit detection) and the reversed no-hybrid shortcut direction, the current trusted split is back to the intended shape:
  rapid references (`260419-4 300/400`) -> rapid with `210 s`, `semiauto-6` -> normal, recent dummy sweep -> normal.
  Next: rerun a larger batch over `Convert_CP_to_EIS 1/Input data` and compare folder-level outcomes against older trusted result folders so future adaptive changes do not silently drift again.

- [done] Backfill stale `adaptive_bias_sweep_summary.json` entries after background adaptive analysis finishes
  Result: `tools/run_adaptive_bias_sweep.py` now performs an end-of-run summary refresh so rows that were initially written as `analysis_pending` can be rewritten from the latest engine state before shutdown.

- [todo] Add the same late-analysis backfill / refresh behavior to the GUI or any persisted live-runtime summary path
  Decision: the standalone adaptive bias-sweep helper now refreshes pending analysis at the end, but the GUI/live run loop still only logs the first finalize snapshot per row.
  Next: define where the GUI/runtime should persist adaptive row summaries, then add a small post-run poll/backfill step so saved runtime records match later replay results.

- [done] Add persisted adaptive summary + delayed-analysis backfill to the GUI/live runtime path
  Result: GUI runs with ADAPT rows now write `adaptive_runtime_summary.json` under the selected result directory and refresh pending analysis snapshots again before shutdown.

- [todo] Add a matching adaptive JSON summary/backfill path to `run_automation.py`
  Decision: GUI parity is now much better, but the headless CSV runner still only emits `measurement_log.csv`, so adaptive runtime history from non-GUI batch runs is not yet persisted in the same structured form.
  Next: add a small `adaptive_runtime_summary.json` writer/backfill path to `run_automation.py` using the same point-summary schema as the GUI/helper tools.

- [done] Add adaptive runtime summary/backfill and normal/rapid switching support to `run_automation.py`
  Result: the headless batch runner now supports ADAPT-labelled rows, persists `adaptive_runtime_summary.json`, and refreshes delayed analysis snapshots before shutdown.

- [todo] Run one saved-data or lab-style dry-run through `run_automation.py` with ADAPT-labelled conditions
  Decision: unit coverage now proves the headless adaptive path and delayed-summary backfill logic, but the next useful confidence step is a runner-level dry-run that mirrors a real CSV workflow more closely.
  Next: prepare a minimal ADAPT CSV fixture or safe dummy-style run and compare the saved `adaptive_runtime_summary.json` against the expected adaptive replay behavior.

- [todo] Decouple live BioLogic polling from hard-stop logic with a worker-thread / timeout-safe cache
  Decision: the new real dummy-cell benchmark shows `get_live_values()` is usually fast enough for monitoring (`~50 ms` cadence achieved, poll median ~`3 ms`) but still exhibits rare multi-second stalls and end-of-technique disconnect-like errors.
  Next: if online stop/optimization is implemented, do not call `get_live_values()` synchronously from the control loop; add a background poller with timeout/staleness metadata and let the main loop consume cached values only.

- [todo] On the lab computer with a real BioLogic/cell attached, test whether live polling is fast and stable enough for online stopping logic during CA/hold
  Decision: `driver_biologic.py` already exposes `get_live_values()` and the current architecture already streams CA/PEIS segments to the GUI monitor, so the next question is hardware/runtime feasibility rather than API availability.
  Test request:
  1. During a real CA hold or post-PEIS CA sequence, poll `get_live_values()` repeatedly and record achievable poll interval / jitter without disrupting data collection.
  2. Confirm whether mid-run stop is reliable on the installed BioLogic stack (`easy-biologic` + EC-Lab DLL), e.g. whether a running CA/hold can be stopped cleanly once a saturation rule is met.
  3. Note whether PEIS emits usable live status frequently enough for any online decision-making, or whether only CA/hold is realistic for first implementation.
  Success criteria: stable sub-second polling during CA plus clean early-stop behavior would justify implementing online saturation-based termination before full next-point adaptive planning.

---

## 2026-04-19 | Claude (Sonnet) — 코드 검토

### 버그 (즉시 수정 권장)

- [done] `run_automation.py:226-231` — `motor / tc / mfc`가 None일 때 `.disconnect()` 호출해서 AttributeError 발생
  Result: `finally` 블록에 `if motor:` / `if tc:` / `if mfc:` None 체크 추가 완료.

- [done] `measurement_sequence.py:74` — PEIS 결과가 빈 배열(0행)이면 `eis_data[:,0].min()` IndexError crash
  Result: `if len(eis_data) == 0:` 가드 추가 — 경고 출력 후 빈 배열 반환으로 조기 종료.

### 불확실 (검증 필요)

- [blocked] `driver_temp.py:139` — `self._w.write(target_f)` 가 파라미터 이름 없이 호출됨
  Decision: pywatlow의 `write()` 시그니처가 파라미터 없이 값만 받는지 확인 필요. 아니면 아래 분기의 `writeParam(7001, ...)` 경로만 남기는 게 안전.
  Blocker: 실제 Watlow 연결 후 어느 경로로 실행되는지 확인 필요.

- [blocked] `driver_biologic.py:_parse_eis` — 위상 단위 판단 로직이 `abs(phase) > 2π + 0.5` 기준으로 degrees/radians 자동 선택
  Decision: BioLogic은 항상 degrees 반환하는 것으로 보이므로 조건 분기 없이 `np.deg2rad()` 고정 적용 검토.
  Blocker: easy-biologic 실제 출력값 확인 후 결정.

- [blocked] `driver_mfc.py:set_flow` — UPDATE_LOG에는 "flow_mode + open_valve 먼저" 라고 했지만 실제 코드에는 SFD 직접 전송
  Decision: MFC_WRITE_ENABLED=False라 현재는 무관하지만, 재활성화 전에 flow_mode/open_valve 선행 전송 포함 여부 명확히 결정.
  Blocker: MFC 쓰기 재활성화 전 프로토콜 검증 시 함께 확인.

### 개선 사항 (우선순위 낮음)

- [todo] `_parse_optional_float` 함수가 `gui.py`와 `run_automation.py` 양쪽에 중복 정의됨
  Decision: 공통 유틸 모듈(예: `utils.py`)로 추출하거나, 한쪽에서 import하도록 정리.
  Next: 두 파일 중 어느 쪽이 더 기준이 되는지 결정 후 통합.

- [todo] MFC 쓰기 비활성 상태(`MFC_WRITE_ENABLED=False`)인데 GUI의 Set Gas / Apply All 버튼이 에러만 뱉음
  Decision: MFC 쓰기 비활성 상태일 때 해당 버튼을 회색 처리하거나 시작 시 경고 표시하면 UX 개선.
  Next: GUI 시작 시 MFC_WRITE_ENABLED 상태를 Hardware 탭에 표시하는 라벨 추가 검토.

- [todo] Surface both LF sources in persisted adaptive summaries when they diverge
  Decision: the runtime now distinguishes `recommended_normal_peis_lowest_freq_hz` from the general LF recommendation, but the trusted semiauto-6 reference still gives the same numeric value for both.
  Next: if a real divergent case appears, write both values into adaptive summaries / GUI notes so normal-mode reasoning stays auditable.

- [todo] Expose both `recommended_peis_lowest_freq_hz` and `recommended_normal_peis_lowest_freq_hz` in adaptive runtime summary artifacts
  Decision: saved-data scan now proves there are real normal-side cases where the two LF recommendations differ by `1 decade` (`0.0100 Hz` vs `0.1000 Hz`).
  Next: update GUI/headless adaptive summary writers so persisted JSON clearly records both values whenever they diverge.

- [done] Make `Manual Quick EIS` use the same live scalar polling path as automated runs
  Result: manual PEIS now starts `_start_biologic_live_poll(...)`, so the Live Monitor tab can receive live `frequency/current/elapsed` updates during the manual GUI path too.

- [todo] Surface a user-facing hint when live scalar polling is active but PEIS point streaming is absent
  Decision: after the manual-path fix, the GUI can show scalar live status during Quick EIS, but users may still expect Nyquist points to draw continuously. Those PEIS points still depend on intermediate `on_segment` emission from the BioLogic stack.
  Next: if the gap remains confusing in practice, add a small Live Monitor hint such as "live status active; Nyquist points may appear in chunks depending on BioLogic buffering".

- [done] Make live scalar polling draw proxy plots during PEIS instead of updating text only
  Result: when `device_live` values arrive before any streamed PEIS segments, the left plot now shows current-vs-time and the right plot shows frequency-vs-time until real Nyquist data takes over.

- [todo] Finish the user-requested manual measurement refresh: add stop support + `Quick Rapid EIS` + recommendation note after manual runs
  Decision: the immediate live-plot blocker is now fixed, but the earlier requested manual UX changes (stop button, fixed HF/LF quick-normal box, parallel quick-rapid box, small recommendation text after completion) are still the next concrete manual-control work item.
  Next: wire BioLogic stop through `stop_channel`, add manual rapid run controls, and persist a short recommendation string after manual normal/rapid runs.

- [todo] Add the remaining manual-control feature bundle: stop support, `Quick Rapid EIS`, and post-run recommendation note
  Decision: live-monitor semantics are now in the intended shape (left = scalar current proxy, right = real Nyquist only), so the next manual-control work should return to the original user request.
  Next: wire BioLogic stop into the manual path, replace free HF/LF manual inputs with fixed quick-normal settings, add a `Quick Rapid EIS` box, and display a small normal/rapid recommendation after the run finishes.

- [todo] Lab-validate the new manual BioLogic stop path during both Quick EIS and Quick Rapid EIS
  Decision: the stop plumbing is now present from GUI -> measurement_sequence -> driver_biologic -> easy-biologic `stop_channel`, but it has only been syntax- and regression-validated so far.
  Next: run a real Quick EIS and Quick Rapid EIS on the connected dummy cell, press `Stop Measurement` mid-run, and confirm how quickly the technique halts plus whether partial files and recommendation handling remain sane.

- [done] Make the left manual live-current proxy plot start at `0 s` from the first live sample rather than absolute BioLogic elapsed time
  Result: `gui.py` now keeps a per-row `live_elapsed_origin`, resets it on monitor reset / row start, and subtracts it from incoming `device_live.elapsed_s` values before plotting. Regression coverage added for a first sample at `32.5 s` becoming `0.0 s` on the plot and the next sample at `33.0 s` becoming `0.5 s`.
  Next: if the user still reports odd live-current plot shape, inspect whether the visible oscillation is just true current behavior on the dummy cell or whether we also need optional x-window trimming / downsampling for readability.

- [done] Make `Stop Measurement` visibly shared by both manual quick-measurement modes
  Result: the stop button now sits in a shared manual-measurement row below both `Quick EIS` and `Quick Rapid EIS`, and a GUI regression test now locks that layout in place.
  Next: on the lab setup, verify one real Quick EIS stop and one Quick Rapid EIS stop so the now-correct UI is backed by confirmed hardware behavior.

- [done] Make the right Nyquist canvas explain PEIS buffering when scalar live status is present but no parseable Nyquist chunk has arrived
  Result: instead of a misleading `Waiting for live data...`, the GUI now surfaces the BioLogic buffering state during PEIS and the driver emits a warning if empty PEIS callbacks arrive with nonzero buffer bytes.
  Next: capture one real rapid-run log and decide whether a bounded PEIS read-interval experiment (e.g. slower chunk retrieval for larger parseable PEIS segments) is justified.

- [done] Let PEIS live current replace the pre-hold CA trace on the left monitor during rapid runs
  Result: if rapid mode has already drawn `Pre-PEIS Hold Current`, the first PEIS live-current event now resets that left plot into `PEIS Live Current Monitor` instead of leaving the old pre-hold trace on screen.
  Next: if the user still sees a stale left plot after PEIS ends, inspect whether post-PEIS CA callbacks stream incrementally on this BioLogic stack or only appear via the final fallback at technique end.

- [done] Turn the left rapid-run monitor into a continuous current timeline across pre-hold, PEIS live current, and post-PEIS CA
  Result: the left plot now appends later technique current data instead of discarding the pre-hold trace or freezing on it. GUI regressions lock both the `pre-hold -> PEIS` and `PEIS -> post-PEIS CA` transitions.
  Next: if the user still reports awkward visuals, consider drawing subtle per-technique separators / legends on the left timeline so the appended phases are easier to distinguish without sacrificing continuity.

- [done] Make PEIS live polling survive transient `get_live_values()` failures and allow single-point Nyquist rendering
  Result: the live poll thread now retries through short BioLogic transition glitches instead of dying immediately, and the Nyquist canvas can show a single point instead of waiting for two points.
  Next: restart the GUI on the lab machine and rerun both Quick EIS and Quick Rapid EIS once to see whether the PEIS-start freeze disappears and whether the slower `run_peis(read_interval=1.0)` yields earlier parseable Nyquist chunks.

- [done] Restore editable PEIS HF/LF fields for both manual quick modes while keeping the old fixed values as defaults
  Result: `Quick EIS` and `Quick Rapid EIS` now both expose HF/LF entries defaulting to `100000 Hz` and `0.1 Hz`, and the execution path plus recommendation logic now use the chosen LF instead of silently assuming the old fixed range.
  Next: after the next GUI restart, confirm on the lab machine that the new HF/LF inputs are visible and that changing LF materially changes the recommendation wording after a manual run.

- [done] Mirror manual recommendation text into the Live Monitor tab and auto-fill manual quick controls from the latest measured/recommended values
  Result: `Live Monitor` now shows the same small recommendation string as the manual tab, and `Quick EIS` / `Quick Rapid EIS` automatically adopt the latest measured Vdc/HF/points plus the recommended LF / CA settings after each manual run.
  Next: once the user reruns the GUI, confirm the shared recommendation line updates in both tabs and that the quick controls visibly change after a completed manual run.

- [done] Make `post_peis_ca_sequence*_done` append to the left current timeline instead of replacing it
  Result: rapid runs now keep the left timeline continuous even when post-PEIS CA only arrives as the final `_done` payload after sparse/absent PEIS live-current callbacks.
  Next: if the user still sees a broken left timeline after restarting the GUI, capture the exact step label/log lines for the sequence so the remaining gap can be matched against the real callback names.

- [todo] Add a bounded raw PEIS chunk inspection path for the live Nyquist blocker
  Decision: verified from the installed `easy_biologic.lib.ec_lib.CurrentValues` definition that `get_live_values()` only provides scalar live fields (`State`, `MemFilled`, `Ewe`, `I`, `ElapsedTime`, `Freq`, ...) and not live `Re(Z)` / `-Im(Z)`.
  Next: instrument one mid-run PEIS callback path in `driver_biologic.py` to dump/inspect the first raw chunk when `MemFilled > 0` but `_parse_eis()` still returns zero points; this is the next bounded route to a true mid-run Nyquist plot.

- [done] Soften the manual recommendation wording when a PEIS-only run is already near the estimated LF target
  Result: a case like the saved `manual_eis_PEIS_20260421_133234.txt` (`current LF ~9 Hz`, `estimated LF ~9.74 Hz`) no longer gets the blunt `rapid EIS` wording; it now shows a `normal EIS is close` message instead.
  Next: if the user still finds the recommendation text too conservative, compare a few more saved manual EIS runs across different requested LF values and tune the boundary between `close normal` and `lower-LF normal / rapid` wording.

- [done] Make the entire Manual Control tab vertically scrollable
  Result: lower manual sections such as `Z Contact Search` are no longer forced to fit without overflow; the tab can now be scrolled vertically while keeping the existing layout intact.
  Next: after the next GUI restart, confirm the lab machine can scroll down to the full `Z Contact Search` section comfortably and decide whether the mouse-wheel binding should be narrowed to only act when the pointer is over the manual tab.

- [done] Overlay CP-FFT recovered Nyquist on the manual right-hand Nyquist canvas when the completed run is still judged hybrid / rapid
  Result: manual post-run analysis now exposes plotting-ready recovered FFT points, and the GUI overlays them in blue on top of the measured PEIS Nyquist arc in gold when `peis_only_sufficient=False`.
  Evidence: trusted rapid replay `260419-4 / 300 rapid` now yields `164` measured PEIS points + `80` recovered FFT points (`manual_overlay_validation_20260421.json`).
  Next: if the overlay is useful in practice, extend the same recovered-FFT series into persisted adaptive summaries / exported result figures.

- [done] Separate left time-series and right Nyquist downsampling limits in the Live Monitor
  Result: the left live-current timeline now keeps up to `5000` points before downsampling, while the right Nyquist plot still uses a smaller cap. This should reduce the visual effect where older left-side current oscillations seemed to disappear during long runs.
  Next: if the user still reports odd left-side behavior after a GUI restart, inspect whether the remaining visual jump is caused by true technique transitions rather than redraw decimation.

- [done] Revalidate whether real lab-PC live polling is fast enough to support online stopping decisions
  Result: the connected dummy-cell rerun still shows about `50 ms` interval / `3 ms` median poll cost, but rare `~5 s` stalls remain, so scalar live polling is still suitable for status display only, not yet for a hard real-time stop/switch decision.

- [done] Replay trusted rapid references and saved Manual Rapid pre/post holds against scalar-only stabilization logic
  Result: `260419-4 300/400 rapid` would still stop around `120 s` under scalar-only saturation even though the trusted offline rapid recommendation stays `210 s`, while saved dummy-cell Manual Rapid holds (`pre=5 s`, `post=3 s`) still replay as too short under the current pre/post stabilization policy.
  Next: keep any future online adaptive stop prototype centered on buffered partial-data analysis plus stabilization guards, not scalar-only CP stopping by itself.
- [todo] Prototype a two-stage current-run LF policy in Microprobe (exploratory CP seed -> initial PEIS -> mode-specific handoff)
  Decision: Convert replay now shows that exploratory CP alone gives a safe first LF seed for both rapid and normal references, but it is only accurate enough for rapid/hybrid cases. Normal cases still need the PEIS-based normal LF logic once PEIS itself says `normal`.
  Next: if prioritized, prototype this as a simulation-first Microprobe policy before wiring it into the runtime.
- [done] Strengthen the saved-data evidence behind the current-run exploratory CP LF-seed idea
  Result: the broadened Convert replay now shows the same pattern across `16` references instead of the original small subset: safe on both sides, useful mainly on rapid/hybrid, too deep on normal.
  Next: if implemented in Microprobe, do it as a two-stage adaptive policy only; do not let the exploratory CP seed act as the final normal-side LF.
- [done] Build and validate a direct Python/OpenCV probe path for the Swift Easy View microscope camera
  Result: OpenCV can open camera index `0` via `CAP_ANY` and `CAP_DSHOW`, but the returned frames are still completely black on the current lab-PC setup (`frame_looks_blank = true`).
  Evidence: `results/camera_probe_20260421_172203/camera_probe_summary.json`, `tools/probe_swift_camera.py`, `tools/live_swift_camera_view.py`.
  Next: test the live viewer with Swift Easy View closed so Python owns the camera directly; if the stream still stays black, treat the current camera/software combination as unsuitable for robust automated OM verification and shortlist a cleaner UVC/OpenCV camera path.
- [done] Recheck the Swift Easy View / 1.3MP USB2.0 camera with illumination restored
  Result: Python/OpenCV can now read valid non-blank frames from camera index `0` via `CAP_ANY` and `CAP_DSHOW`; the earlier black probe was not a hard compatibility blocker.
  Evidence: `results/camera_probe_20260421_172326/camera_probe_summary.json`.
  Next: use `CAP_DSHOW` index `0` as the default OM feed for the next prototype (live preview + ROI-based electrode/tip verification).
- [done] Add a GUI-level `Image Monitor` tab as the first live-camera bridge for OM-guided automation
  Result: the GUI now has a live Swift/OpenCV preview tab with backend/index/detector controls, and `Live Monitor` is renamed to `EIS Monitor` to keep meanings separate.
  Evidence: `gui.py`, GUI regression update in `tests/test_gui_adaptive_runtime.py`.
  Next: add point-pick / target-electrode selection on top of that tab so the user can lock a target electrode from the live image itself.

- [done] Add a minimal ROI revisit verifier for future move-after-image checks
  Result: `vision/roi_verifier.py` can now crop a reference ROI and recover the same neighborhood after a small synthetic stage-like shift using CLAHE + Laplacian-assisted template matching.
  Evidence: `tests/test_roi_verifier.py`, `results/vision_live_overlay_probe/roi_verifier_demo_summary.json`.
  Next: integrate `verify_roi_revisit(...)` into a conservative post-move image check after stage movement becomes available again.

- [done] Tune the live microscope detector against the saved Swift snapshot and make it the default live-view preset
  Result: the saved Swift snapshot now yields `20` candidates with the live preset versus only `1` with the generic detector, and the live overlay viewer defaults to the tuned `live` preset while still allowing `generic` as a comparison mode.
  Evidence: `results/vision_live_overlay_probe/swift_snapshot_live_detector_summary.json`, `tools/live_electrode_overlay_view.py`, `tests/test_vision_electrode_mapper.py`.
  Next: reduce over-detection by adding optional row/spacing priors once we start using the live image to choose specific electrodes instead of just proving visibility.
- [done] Remove the brittle `12 visible electrodes` assumption from the Image Monitor overlay
  Result: the live overlay filter is now dynamic again; it only removes unsupported/out-of-band candidates and no longer hard-caps the overlay to 12.
  Next: keep treating live overlay count as heuristic only, and rely on design/pre-shot assistance when the live view alone is ambiguous.

- [done] Add the first design-assisted setup controls to the Image Monitor tab
  Result: the tab now has a design path entry plus `Browse` / `Load Design` controls so future assisted setup can use a design image when the live camera view is too partial or ambiguous.
  Next: wire the loaded design map into explicit electrode target selection instead of only keeping it as stored context.
- [done] Add a parser for user-provided microscope markup images and expose markup loading in the Image Monitor
  Result: the user-supplied [Swift_snapshot_markup.png](C:/Users/mmq8658/Desktop/Microprobe/OM/Swift_snapshot_markup.png) now parses as a trusted visible layout with `18` electrodes in `3` rows / up to `6` columns, and the GUI can load that layout directly via a new `Markup image` field in `Image Monitor`.
  Evidence: [Swift_snapshot_markup_detected_overlay.png](C:/Users/mmq8658/Desktop/Microprobe/OM/Swift_snapshot_markup_detected_overlay.png), [Swift_snapshot_markup_detected.csv](C:/Users/mmq8658/Desktop/Microprobe/OM/Swift_snapshot_markup_detected.csv), [Swift_snapshot_markup_detected_summary.json](C:/Users/mmq8658/Desktop/Microprobe/OM/Swift_snapshot_markup_detected_summary.json).
  Next: use this trusted markup layout to drive visible-electrode target selection / design correspondence instead of the shaky live-only auto overlay.
- [done] Use a loaded markup layout as the initial live-tracking seed in `Image Monitor`
  Result: when a trusted markup image is loaded, the GUI now uses it as the first visible-electrode scaffold for live monitoring. The first frame runs ECC affine registration from the cleaned markup reference to the current frame, then refines locally with the circular tracker instead of trusting a brand-new noisy auto detect.
  Evidence: `results/vision_live_overlay_probe/markup_seed_tracking_shift_demo_summary.json` keeps all `18` electrodes through a synthetic `(+10 px, -7 px)` drift with ECC `~0.9999`.
  Next: expose a `Freeze current frame as tracking seed` action so this same workflow works even when the user has no offline markup image.
- [done] Add a GUI-level `Freeze Current as Seed` action for the Image Monitor
  Result: the user can now lock the current live frame + current overlay as a tracking seed directly from the GUI, without needing an offline markup file first. Later frames use ECC alignment + local circle refinement against that frozen seed.
  Next: build a click/select layer on top of the frozen seed so one visible electrode can be explicitly chosen as the target and then linked to stage/design coordinates.
- [done] Add a structure-driven `reference` detector preset for cleaner OM images
  Result: `Image Monitor` now offers `reference` in addition to `live` and `generic`. On `monitoring during measurement`, it cuts the raw candidate count from `393` (`live`) to `24` without assuming a fixed electrode count, by keeping only circles that belong to rows with near-regular spacing.
  Next: compare `reference + Freeze Current as Seed` against the noisier `live + Freeze Current as Seed` path during a few real microscope moves and keep the better practical acquisition path.
- [done] Exclude the upper zoomed region from the `reference` OM detector
  Result: the `reference` preset now ignores the upper non-array region of `monitoring during measurement` and only uses the lower repeated electrode field. This removes the obviously wrong top-region detections, though the detector is now too conservative (`7` candidates) and still needs lower-band retuning.
  Next: relax the lower-band structure filter so `reference` keeps more valid lower-field electrodes without letting the upper-region junk back in.
- [done] Recover more of the lower repeated field in the `reference` OM detector without bringing back the upper zoomed region
  Result: clustering near-duplicate x candidates inside each lower-band row before spacing checks improved the saved `monitoring during measurement` reference detector from `7` to `20` structured candidates while keeping `y_min > 0.2 * image_height` and preserving the upper-band exclusion.
  Evidence: `results/vision_live_overlay_probe/monitoring_measurement_reference_comparison_after_rowclustering.json`, `monitoring_measurement_reference_overlay_after_rowclustering.png`.
  Next: stop treating the reference overlay count as a truth value and build the next layer on top of it: click-to-select / target-locking from the current reference/frozen-seed layout.
- [done] Add click-to-lock target selection on top of the Image Monitor overlay
  Result: the live microscope preview is no longer just a candidate overlay. The user can now click the current preview to lock the nearest visible electrode as the target, clear that target, and keep it highlighted while later overlay refreshes track it by nearest-neighbor image position.
  Evidence: `gui.py`, GUI regressions in `tests/test_gui_adaptive_runtime.py` (`46` Microprobe tests OK).
  Next: wire the locked target into design/stage correspondence so a clicked visible electrode becomes a true motion target instead of only an image-space highlight.
- [done] Revalidate the current Image Monitor / target-lock baseline while the Convert two-stage runtime policy artifact was added
  Result: `tests/test_gui_adaptive_runtime.py` still passes (`36` tests OK), so the OM acquisition stack (`reference`, frozen seed, click-to-lock target) remains stable while the analysis-side adaptive simulation moves forward.
  Next: map the clicked/locked target to design and eventual stage coordinates instead of leaving it as an image-space-only selection.
- [done] Port the saved-data two-stage current-run LF handoff into a Microprobe-facing planner/simulation layer
  Result: Microprobe now has a native planner helper (`AdaptiveMeasurementEngine.plan_two_stage_current_run_policy`) plus its own replay artifact at `results/two_stage_current_run_policy/two_stage_current_run_policy_summary.json`. The helper reproduces the Convert artifact exactly across all `16` saved references (`all_actions_match=true`, `all_runtime_lf_match=true`).
  Evidence: `simulate_two_stage_current_run_policy.py`, `tests/test_two_stage_current_run_policy.py`.
  Next: wire this helper into the adaptive next-point planner/runtime instead of leaving it as a saved-data-only helper.
- [done] Expose the Microprobe two-stage current-run decision through runtime-facing finalize/export payloads
  Result: completed points now store `current_run_policy_decision`, and `finalize_completed_point(...)` returns it automatically. GUI/headless adaptive summaries therefore get the same two-stage policy decision without needing separate recomputation.
  Evidence: `adaptive_types.py`, `adaptive_engine.py`, `tests/test_adaptive_engine.py`.
  Next: consume `current_run_policy_decision` inside the adaptive next-point planner itself so planning behavior can branch on the stored handoff state instead of only carrying it along in the payload.

---

## 2026-04-21 | Claude (Sonnet) — 로그 정리

### 완료 처리 (이전 [todo]/[doing] 항목이 실제로 완료됨)
- 위 항목들 참조: fast analysis adapter 검증, Rapid EIS 텍스트 export 검증, runtime trust 로직 업데이트, PEIS-plateau-first 스위칭 구현 모두 완료로 표시.

### 현재 미결 Next Steps (2026-04-21 기준)

#### Adaptive Runtime
- [todo] Consume `current_run_policy_decision` inside the adaptive next-point planner
  Decision: the two-stage LF handoff decision is now stored in runtime payloads, but the planner still does not branch on it; next planning must explicitly differ when the current point is on the rapid/hybrid side vs normal-side handoff.
  Next: modify `recommend_next_point(...)` to check `current_run_policy_decision.keep_exploratory_seed_for_hybrid` and use the appropriate LF source accordingly.

- [todo] Run one saved-data or lab-style dry-run through `run_automation.py` with ADAPT-labelled conditions
  Decision: unit coverage proves the headless adaptive path, but a realistic end-to-end runner-level dry-run has not been done yet.
  Next: prepare a minimal ADAPT CSV fixture or safe dummy-style run and verify `adaptive_runtime_summary.json` against expected replay behavior.

- [todo] Address the GUI/runtime adaptive-summary gap where mid-run rows appear as `analysis_pending` while offline replay recovers them
  Decision: the standalone helper now backfills at end-of-run, but the GUI live run path still has a timing window where rows stay stale if background analysis is slow.
  Next: compare GUI backfill timing against offline replay timing to decide whether the end-of-run refresh window needs extending or a final explicit wait.

#### Image Monitor / OM Automation
- [todo] Wire the clicked/locked Image Monitor target to design and stage coordinates
  Decision: click-to-lock target selection is now working in image-space; the next step is mapping that locked visible electrode to the corresponding design-file electrode and eventually to XY stage coordinates.
  Next: implement design-image correspondence (clicked pixel → design electrode center → stage XY) using the existing affine calibration math in `vision_stage_mapper.py`.

- [todo] Lab-validate the new manual BioLogic stop path during Quick EIS and Quick Rapid EIS
  Decision: stop plumbing is GUI→measurement_sequence→driver_biologic→easy-biologic `stop_channel`, syntax- and regression-validated only.
  Next: run a real Quick EIS and Quick Rapid EIS on the connected dummy cell, press `Stop Measurement` mid-run, and confirm halt timing and partial-file behavior.

#### Hardware Validation (instrument PC)
- [blocked] Validate Watlow ramp-rate write path on real controller (params 7015, 7003)
  Blocker: requires real EZ-ZONE controller (COM4) with power.
- [blocked] Validate Aera MFC digital write commands before re-enabling (`MFC_WRITE_ENABLED`)
  Blocker: requires supervised lab-PC test with low-flow setpoint to confirm `SFD` does not force valves fully open.
- [done] Switch the Microprobe adaptive next-point planner to consume stored `current_run_policy_decision`
  Result: `recommend_next_point(...)` now actually branches on the stored two-stage current-run action. `handoff_to_normal_lf` yields `normal_eis` + stored normal LF when runtime guards still trust PEIS-only, while `keep_exploratory_seed_for_hybrid` yields `rapid_eis` + the stored exploratory seed.
  Evidence: `adaptive_engine.py`, planner-level regressions in `tests/test_adaptive_engine.py`, full Microprobe suite `70 tests OK`.
  Next: expose the consumed planner action more explicitly in adaptive runtime summaries/GUI notes so run history can show whether the recommendation came from normal handoff or exploratory-seed preservation.
- [done] Flatten the consumed/completed two-stage current-run policy into adaptive runtime summary point entries
  Result: `adaptive_runtime_summary.json` points now include top-level `consumed_current_run_policy` and `completed_point_current_run_policy` fields in both GUI and headless automation paths, and backfill refreshes the completed-point policy when late finalize results arrive.
  Evidence: `gui.py`, `run_automation.py`, `tests/test_gui_adaptive_runtime.py`, `tests/test_run_automation_adaptive.py`, Microprobe adaptive slice `73 tests OK`.
  Next: surface the same action/runtime-LF information in operator-facing GUI/export text so users do not need to open JSON to understand why a row used normal handoff vs exploratory-seed preservation.
- [done] Surface two-stage current-run policy action/runtime LF directly in the visible EIS Monitor recommendation line
  Result: `_prepare_adaptive_row(...)` and `_finalize_adaptive_row(...)` now write human-readable `handoff_to_normal_lf` / `keep_exploratory_seed_for_hybrid` text into the operator-facing monitor recommendation label instead of keeping that state only in JSON/log payloads.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive slice `75 tests OK`.
  Next: mirror the same policy wording into saved/exported adaptive artifacts so post-run review gets the same context without the live GUI.
- [done] Add saved/export-ready two-stage policy text fields to adaptive runtime summaries
  Result: GUI and headless `adaptive_runtime_summary.json` points now include `consumed_current_run_policy_text` and `completed_point_current_run_policy_text`, so post-run review sees the same human-readable handoff/exploratory-seed wording without opening nested payloads or relying on the live GUI.
  Evidence: `gui.py`, `run_automation.py`, `tests/test_gui_adaptive_runtime.py`, `tests/test_run_automation_adaptive.py`, Microprobe adaptive slice `75 tests OK`.
  Next: once text export is stable, return to the higher-value OM path of mapping the clicked/locked Image Monitor target to design/stage coordinates.
- [done] Extend Image Monitor target-lock from design/sample coordinates into anchor-aware stage XY candidates
  Result: `Image Monitor` now has `Use Current XY as Anchor` / `Clear Anchor`, stores one design-target/current-stage anchor, and shows projected `Stage (~x, y) mm` alongside `sample (~x, y) mm` for later locked targets.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `79 tests OK`.
  Next: replace the current aligned-axis delta assumption with explicit image/design-to-stage calibration (rotation/flip/affine) so stage projections remain valid for arbitrary microscope/sample orientation.
- [done] Add operator-facing swap/invert calibration controls for anchor-based stage projection
  Result: `Image Monitor` now supports `Swap XY`, `Invert X`, and `Invert Y`, and both the anchor status line and projected `Stage (~x, y) mm` honor that mapping.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `80 tests OK`.
  Next: add a fuller affine/image-to-stage calibration path on top of `vision_stage_mapper.py` so arbitrary rotation/shear does not rely on manual swap/invert toggles alone.
- [done] Add 3-point affine sample-to-stage calibration to Image Monitor
  Result: `Image Monitor` now supports `Add Cal Point`, `Solve Affine`, and `Clear Cal`, and solved affine calibration is used for projected target stage XY before the anchor/swap fallback.
  Evidence: `vision_stage_mapper.py`, `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `81 tests OK`.
  Next: connect the calibrated target XY candidate to an explicit move workflow and add save/load persistence for the solved calibration.
- [done] Let Image Monitor copy projected stage XY into Manual Control target fields
  Result: `Image Monitor` now has `Projected XY -> Target`, so a locked electrode with either anchor-projected or affine-projected stage XY can populate `Manual Control -> Target State` X/Y without manual retyping.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `83 tests OK`.
  Next: add save/load persistence for solved affine calibrations, then decide whether to expose a guarded `Move Target` action on top of the existing safe stage move path.
- [done] Add save/load persistence for Image Monitor stage calibration
  Result: `Image Monitor` now has `Save Cal` / `Load Cal`, and saved JSON roundtrips restore affine refs, anchor, swap/invert toggles, and the same projected target stage XY.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `84 tests OK`.
  Next: turn the now-persistent affine projection into a guarded `Move Target` workflow built on the existing safe stage move path.
- [done] Turn persistent Image Monitor target projection into a guarded `Move Target (Safe)` workflow and auto-refresh the stage anchor after a successful move
  Result: `Image Monitor` can now send the locked target through `motor.move_xyz_safe(...)`, preserve current Z, and immediately promote the reached target/stage XY to the new anchor so subsequent projected targets continue from the latest physical location.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `86 tests OK`.
  Next: lab-validate the guarded target move on the real motor controller and add a post-move image ROI verification hook so the moved-to electrode can be visually confirmed before the next step.
- [done] Add a post-move ROI verification hook to the Image Monitor safe-move workflow
  Result: `Move Target (Safe)` now arms a one-shot ROI revisit check from the pre-move live frame and reports `ROI verify: OK/weak` on the next live frame with score and pixel drift.
  Evidence: `gui.py`, `vision/roi_verifier.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `87 tests OK`.
  Next: real-hardware validate both guarded move timing and ROI verification usefulness on the instrument PC, then decide whether weak ROI checks should automatically trigger seed reacquisition or block the next projected move.
- [done] Auto-promote an ROI-verified safe-move result into the new frozen tracking seed
  Result: after `Move Target (Safe)` produces an `ROI verify: OK`, the current live frame plus current overlay/tracking map are promoted into the new frozen seed automatically, so subsequent tracking no longer depends on the pre-move seed.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `88 tests OK`.
  Next: real-hardware validate whether this should always happen on every `OK` ROI verify and whether `weak` should block the next move or fall back to manual reacquisition.
- [done] Block the next guarded move after `ROI verify: weak` until the seed is refreshed
  Result: `Move Target (Safe)` now refuses to send another move after a weak ROI verify until the operator refreshes the seed (manually or through a later ROI-verified promotion), so shaky post-move visual lock does not silently compound into the next projection.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `90 tests OK`.
  Next: real-hardware validate whether this conservative block should remain mandatory or whether some weak cases deserve an explicit operator override.
- [done] Add an explicit operator override for the weak-ROI move block
  Result: `Image Monitor` now exposes `Override Weak ROI Block`, so operators can intentionally release the conservative weak-ROI guard and continue with the next safe move without being forced to refresh the seed first.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `92 tests OK`.
  Next: real-hardware validate whether this one-click override is the right ergonomics or whether it should become a stronger confirmation flow.
- [done] Tighten the weak-ROI override into a one-shot latch
  Result: `Override Weak ROI Block` now permits only the next guarded move, then the default weak-ROI block reasserts itself unless a seed refresh or successful ROI verification clears it.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `93 tests OK`.
  Next: real-hardware validate whether this one-shot override is sufficient or whether a stronger confirmation flow is still needed.
- [done] Add a visible Image Monitor move-gate status line for weak-ROI move control
  Result: `Image Monitor` now surfaces `Move gate: clear`, `Move gate: weak ROI blocked`, and `Move gate: weak ROI blocked (one-shot override armed)` directly in the UI, and those states refresh on weak ROI, seed promotion, monitor stop, and one-shot override consumption.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `94 tests OK`.
  Next: lab-validate whether the new move-gate text is enough during real guarded moves or whether it should gain stronger visual emphasis (for example color coding) without changing the underlying one-shot weak-ROI semantics.
- [done] Add color-coded visual emphasis to the Image Monitor weak-ROI move gate
  Result: `Move gate` now shows the same weak-ROI/override/clear state as before, but the label is also color-coded (`blocked=red`, `override=orange`, `clear=green`) so operators can read guarded-move state faster during Image Monitor workflow.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `94 tests OK`.
  Next: lab-validate whether the color-coded move gate is sufficient or whether the guarded move buttons themselves should be disabled/enabled directly from the same state.
- [done] Bind Image Monitor guarded-move button enabled/disabled state directly to the weak-ROI move gate
  Result: `Move Target (Safe)` now disables itself while weak ROI is blocking, re-enables only when the one-shot override is armed or the gate returns to clear, and `Override Weak ROI Block` is enabled only when that manual release is actually applicable.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, Microprobe adaptive/runtime/GUI slice `94 tests OK`.
  Next: lab-validate whether the disabled/enabled button flow plus the color-coded gate is sufficient or whether one-shot override should still require an extra confirmation dialog.
- [done] Require explicit confirmation before arming the one-shot weak-ROI override
  Result: `Override Weak ROI Block` now prompts for confirmation, cancel keeps the block intact, and confirm still arms the same one-shot latch; the broader adaptive/runtime/GUI suite remains green (`95 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether the confirmation dialog is sufficient as-is or whether it should surface more context (for example the latest ROI verify score/dx/dy) before the operator confirms.
- [done] Surface the latest weak ROI verify score/dx/dy inside the one-shot override confirmation dialog
  Result: `Override Weak ROI Block` now shows the latest weak ROI `R?C?`, `score`, and `dx/dy` before arming the one-shot latch, while stop/failure paths clear stale ROI context and the broader adaptive/runtime/GUI slice remains green (`95 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether the ROI-context dialog is sufficient or whether the prompt should also include the currently locked target / projected stage XY before the operator confirms.
- [done] Add locked-target and projected-stage context to the weak-ROI one-shot override confirmation dialog
  Result: `Override Weak ROI Block` now shows the locked electrode, design sample mm, projected stage XY, and the latest weak ROI `score/dx/dy` before arming the one-shot override, while the broader adaptive/runtime/GUI slice remains green (`95 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether this target+stage context is sufficient or whether the dialog should also mention whether the projection came from anchor mapping or affine calibration.
- [done] Add projection provenance (anchor vs affine) to the weak-ROI one-shot override confirmation dialog
  Result: `Override Weak ROI Block` now shows `Projection source: stage anchor mapping` or `Projection source: affine calibration` alongside the projected stage XY, and both dialog paths are covered by GUI regressions while the broader adaptive/runtime/GUI slice remains green (`96 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether provenance text is sufficient or whether the dialog should additionally surface move-gate state / ROI-verify recency.
- [done] Add current move-gate context to the weak-ROI one-shot override confirmation dialog
  Result: `Override Weak ROI Block` now also shows `Current move gate: ...`, and the dialog refreshes the move-gate label first so it reflects the actual blocked state instead of stale `clear` text; broader adaptive/runtime/GUI slice remains green (`96 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether this makes the override prompt self-contained enough or whether ROI-verify recency/timestamping should also be surfaced.
- [done] Add ROI-verify recency/timestamping context to the weak-ROI one-shot override confirmation dialog
  Result: `Override Weak ROI Block` now shows how many seconds ago the latest weak ROI verification happened (for example `12.5 s ago`) alongside gate/target/stage/provenance/ROI-quality context, while the broader adaptive/runtime/GUI slice remains green (`96 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether relative recency is enough context or whether the prompt should also surface an absolute timestamp.
- [done] Add an absolute UTC verify timestamp to the weak-ROI one-shot override confirmation dialog
  Result: `Override Weak ROI Block` now shows both relative recency and a stable absolute UTC verify timestamp for the latest weak ROI context, while the broader adaptive/runtime/GUI slice remains green (`96 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether recency + UTC timestamp is enough context or whether the dialog should also surface the current live-frame capture time for direct comparison.
- [done] Add the current live-frame UTC timestamp to the weak-ROI one-shot override confirmation dialog
  Result: `Override Weak ROI Block` now also shows the UTC time of the current live frame, so operators can directly compare “the image I am looking at now” with the last weak ROI verify time; the broader adaptive/runtime/GUI slice remains green (`96 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether current-live-frame UTC plus weak-ROI verify UTC is enough, or whether the dialog should also surface the direct time gap between them.
- [done] Add the direct frame-vs-verify time gap to the weak-ROI one-shot override confirmation dialog
  Result: `Override Weak ROI Block` now explicitly shows `Frame vs weak ROI verify gap: ... s`, and a first-pass `frame_gap_text` regression on one-shot/no-ROI paths was bounded-fixed immediately; the broader adaptive/runtime/GUI slice remains green (`96 tests OK`).
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`.
  Next: lab-validate whether the raw gap is enough context or whether the dialog should also classify it into freshness buckets.
- [done] Classify the weak-ROI frame-vs-verify gap into operator-facing freshness buckets
  Result: `Override Weak ROI Block` now shows both the raw `Frame vs weak ROI verify gap: ... s` and `Frame freshness bucket: fresh|aging|stale (...)`, so the operator no longer has to translate raw seconds into an immediate trust judgment during a weak-ROI override.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `60 tests OK`, broader adaptive/runtime/GUI slice `96 tests OK`.
  Next: lab-validate whether the initial `10 s / 30 s` freshness thresholds feel right during real weak-ROI override decisions or need tuning.
- [done] Add explicit override-risk wording on top of the weak-ROI freshness buckets
  Result: `Override Weak ROI Block` now also shows `Override risk: low|moderate|high (...)`, so the operator no longer has to translate freshness buckets into an action judgment during a weak-ROI override.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `60 tests OK`, broader adaptive/runtime/GUI slice `96 tests OK`.
  Next: lab-validate whether the current risk mapping is intuitive enough or whether the dialog should add stronger action guidance when the risk is high.
- [done] Add explicit recommended-action guidance on top of the weak-ROI override risk
  Result: `Override Weak ROI Block` now also shows `Recommended action: ...`, and a new stale/high regression confirms the dialog tells the operator to refresh the seed first when the evidence is old enough to be high risk.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `61 tests OK`, broader adaptive/runtime/GUI slice `97 tests OK`.
  Next: lab-validate whether the current recommendation wording is clear enough or whether the `high`-risk path should become even more forceful.
- [done] Add a dedicated warning banner for the stale/high weak-ROI override path
  Result: the weak-ROI override dialog now shows a dedicated `WARNING: ... refreshing the seed is strongly preferred ...` banner only on the stale/high path, and the stale regression explicitly requires it.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `61 tests OK`, broader adaptive/runtime/GUI slice `97 tests OK`.
  Next: lab-validate whether the new high-risk warning is sufficient or whether the stale path should become even more conservative.
- [done] Require a second confirmation before arming stale/high weak-ROI overrides
  Result: stale/high weak-ROI overrides now need two deliberate confirmations before the one-shot override is armed, while low/moderate paths still use the original single confirmation flow.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `62 tests OK`, broader adaptive/runtime/GUI slice `98 tests OK`.
  Next: lab-validate whether stale/high should remain overrideable after two deliberate confirmations or whether the stale path should become blocked by default in real use.
- [done] Disable stale/high weak-ROI overrides by default unless the operator explicitly opts in
  Result: `Image Monitor` now keeps `Override Weak ROI Block` disabled for stale/high weak ROI until `Allow stale/high override` is explicitly enabled, and stale/high override attempts are guarded by both button state and a no-dialog function-level block.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `64 tests OK`, broader adaptive/runtime/GUI slice `100 tests OK`.
  Next: lab-validate whether the new stale/high opt-in gate is enough or whether stale/high should become a permanent hard block outside of a dedicated debug/service mode.
- [done] Auto-reset the stale/high override opt-in when the monitor or frozen seed is reacquired
  Result: `Allow stale/high override` now clears itself on `Stop Camera`, `Clear Seed`, and all new frozen-seed promotions, so stale/high opt-in does not silently linger after reacquisition events.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `65 tests OK`, broader adaptive/runtime/GUI slice `101 tests OK`.
  Next: lab-validate whether the current auto-reset points are sufficient or whether target changes / completed safe moves should also clear stale/high opt-in.
- [done] Auto-reset the stale/high override opt-in when the locked target is cleared
  Result: `Clear Target` now also clears `Allow stale/high override` and refreshes the weak-ROI move gate, so stale/high override availability drops back to the safe default as soon as the operator discards the target context.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `66 tests OK`, broader adaptive/runtime/GUI slice `102 tests OK`.
  Next: lab-validate whether completed `Move Target (Safe)` should also clear stale/high override opt-in or whether the current reset points are sufficient.
- [done] Auto-reset the stale/high override opt-in after a successful safe move
  Result: a successful `Move Target (Safe)` now also clears `Allow stale/high override` and refreshes the weak-ROI move gate, so stale/high override availability falls back to the safe default once the move is consumed and the post-move ROI verification window begins.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `67 tests OK`, broader adaptive/runtime/GUI slice `103 tests OK`.
  Next: lab-validate whether the current stale/high reset set is now sufficient in real operator workflows.
- [done] Auto-reset the stale/high override opt-in when a new target is selected
  Result: clicking a different target in Image Monitor now also clears `Allow stale/high override` and refreshes the weak-ROI move gate, so stale/high override availability falls back to the safe default as soon as the operator switches electrode context.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `68 tests OK`, broader adaptive/runtime/GUI slice `104 tests OK`.
  Next: lab-validate whether the current stale/high reset set is now sufficient in real operator workflows.
- [done] Auto-reset the stale/high override opt-in when stage projection mapping changes
  Result: changing the projected-stage basis itself now clears `Allow stale/high override`. Successful anchor creation, clear-anchor, clear-cal, affine solve/load, and `Swap XY` / `Invert X` / `Invert Y` toggles all drop stale/high override back to the safe default and re-lock the weak-ROI move gate.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `72 tests OK`, broader adaptive/runtime/GUI slice `108 tests OK`.
  Next: lab-validate whether design/markup reload should also auto-clear stale/high opt-in or whether projection-basis changes are the right stopping point.
- [done] Auto-reset the stale/high override opt-in when design or markup context is reloaded
  Result: design load/clear and markup load/clear now also clear `Allow stale/high override`, so an old stale/high exception cannot silently survive a new assisted-layout context.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `76 tests OK`, broader adaptive/runtime/GUI slice `112 tests OK`.
  Next: lab-validate whether the current stale/high reset set now feels complete in real operator workflows or whether any rare context change still deserves an explicit reset.
- [done] Also auto-reset the stale/high override opt-in on design/markup load failure
  Result: stale/high override now drops back to the safe default not only when design/markup loads succeed or are cleared, but also when a new design/markup load attempt fails.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `78 tests OK`, broader adaptive/runtime/GUI slice `112 tests OK`, Convert baseline `8 tests OK`.
  Next: lab-validate whether the current stale/high reset set now feels complete in real Image Monitor operator workflows.
- [done] Clear stale affine projection state when affine solve fails
  Result: `_image_solve_stage_affine_calibration()` now clears any previously solved affine object when there are too few refs or the solver raises, and the locked-target status is immediately refreshed so stale projected `Stage (~x, y) mm` text disappears on solve failure.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `80 tests OK`, broader adaptive/runtime/GUI slice `116 tests OK`.
  Next: lab-validate whether any remaining stage-projection failure path still leaves stale operator context that should be cleared just as aggressively.
- [done] Clear stale affine projection state when stage calibration load fails
  Result: `_image_load_stage_affine_calibration()` now clears any previously solved affine object when loading fails, and the locked-target status is immediately refreshed so stale projected `Stage (~x, y) mm` text disappears on load failure.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `81 tests OK`, broader adaptive/runtime/GUI slice `117 tests OK`.
  Next: lab-validate whether any remaining stage-calibration failure path still leaves stale operator context that should be cleared just as aggressively.
- [done] Make stage calibration loading transactional on failure
  Result: `_image_load_stage_affine_calibration()` now commits refs/anchor/swap-invert/affine state only after the whole payload parses and optional affine solving succeeds, so failed loads clear stale affine without partially overwriting the previous calibration context.
  Evidence: `gui.py`, `tests/test_gui_adaptive_runtime.py`, GUI slice `82 tests OK`, broader adaptive/runtime/GUI slice `118 tests OK`.
  Next: lab-validate whether preserving the previous anchor/swap-invert context on load failure feels right or whether failed `Load Cal` should clear even more state in real workflows.

## 2026-04-22 20:38
- Resolved: EIS GUI crash on high/invalid impedance by filtering non-finite / overflow-scale Nyquist points in parser + canvas plotting.
- Next: implement MFC command model that supports direct % entry and optional calibrated desired sccm -> % conversion.

## 2026-04-23 00:45
- [done] Trip the furnace to a safe state when a hot run suddenly reads room/0 C or drops implausibly fast
  Result: automated runs now start a background temperature safety monitor; if the hot-furnace PV falls below the configured minimum or drops by more than the configured delta, the GUI stops the run, stops BioLogic, commands Watlow safe shutdown, shows a popup, and writes 	emperature_safety_trip.json to the active result folder.
  Evidence: [config.py](C:/Users/mmq8658/Desktop/Microprobe/Microprobe%20Python/config.py), [driver_temp.py](C:/Users/mmq8658/Desktop/Microprobe/Microprobe%20Python/driver_temp.py), [gui.py](C:/Users/mmq8658/Desktop/Microprobe/Microprobe%20Python/gui.py), [tests/test_gui_adaptive_runtime.py](C:/Users/mmq8658/Desktop/Microprobe/Microprobe%20Python/tests/test_gui_adaptive_runtime.py), GUI slice 86 tests OK, broader slice 7 tests OK.
  Next: lab-tune the exact minimum/drop thresholds once a few real traces are collected so the safety trip stays conservative without nuisance trips.
- [pending] Implement MFC command model that supports direct % entry and optional calibrated desired sccm -> % conversion
  Status: partially advanced only in design discussion; no code landed this cycle because the new temperature-safety request was more urgent.
  Next: add an MFC calibration helper and wire GUI/manual/full-auto gas fields to either direct % or calibration-backed sccm -> % resolution.

## 2026-04-23 01:30
- [partial] Prefer current-sample-only optimization decisions and keep backtesting them across varied input shapes
  Result: Convert now has simulate_inflight_current_run_policy.py + regression coverage, and Microprobe compatibility rerun (33 tests OK) stayed green. The new artifact says rapid references stabilize to rapid very early in PEIS, but normal references do not stabilize to normal until essentially full PEIS.
  Evidence: C:\Users\mmq8658\Desktop\Microprobe\Convert_CP_to_EIS 1\result\inflight_current_run_policy\inflight_current_run_policy_summary.json
  Next: reflect this in Microprobe planning/UI language as �CP seed is safe for hybrid priors, but normal handoff still needs PEIS evidence�.
- [partial] Remove duplicate result/input files without throwing away needed raw context
  Result: generated exact-duplicate manifests for results and Input data, but did not delete yet because raw-input duplicate groups still need a deterministic keeper policy.
  Evidence: C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\results\duplicate_scan_results.json, C:\Users\mmq8658\Desktop\Microprobe\Convert_CP_to_EIS 1\result\duplicate_scan_input_data.json
  Next: choose a keeper rule (likely one canonical copy per SHA256 group) and only then delete the redundant exact duplicates.

## 2026-04-23 09:25
- [partial] Prefer current-sample-only optimization decisions and keep backtesting them across varied input shapes
  Result: Convert now also has nalyze_partial_peis_handoff_thresholds.py, and the stronger saved-data result is that partial-PEIS fit thresholds still do not provide a safe early-normal handoff. Microprobe compatibility rerun (33 tests OK) stayed green.
  Evidence: C:\Users\mmq8658\Desktop\Microprobe\Convert_CP_to_EIS 1\result\partial_peis_handoff_thresholds\partial_peis_handoff_thresholds_summary.json
  Next: keep early normal handoff blocked in planner reasoning until a richer current-run signal exists; switch implementation focus to the pending MFC model or duplicate cleanup policy.

## 2026-04-23 09:55
- [partial] Check whether �always CP+PEIS first� is cheap for normal-friendly samples because CP saturates very early
  Result: current saved-data says no. The semiauto normal references do not reduce the exploratory CP window under the present scalar saturation-stop rule, while rapid references still undershoot the trusted offline CP if stopped that way.
  Evidence: C:\Users\mmq8658\Desktop\Microprobe\Convert_CP_to_EIS 1\result\current_run_cp_saturation_stop\current_run_cp_saturation_stop_summary.json
  Next: only revisit this if a richer CP stop rule is designed; the current scalar saturation rule is not enough.
## 2026-04-23 11:44
- [done] Prepare a live normal-vs-hybrid sample validation suite before new hardware samples are connected
  Result: [live_sample_validation.py](C:/Users/mmq8658/Desktop/Microprobe/Microprobe%20Python/live_sample_validation.py) now defines the experiment stack, [tools/prepare_live_sample_validation_suite.py](C:/Users/mmq8658/Desktop/Microprobe/Microprobe%20Python/tools/prepare_live_sample_validation_suite.py) generates a timestamped manifest/checklist, and the latest suite was written to [C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\results\live_sample_validation_suite_20260423_114442\live_sample_validation_suite.md](C:/Users/mmq8658/Desktop/Microprobe/Microprobe%20Python/results/live_sample_validation_suite_20260423_114442/live_sample_validation_suite.md).
  Evidence: `tests/test_live_sample_validation.py`, `tests/test_driver_biologic.py`, `tests.test_adaptive_engine + tests.test_run_automation_adaptive + tests.test_live_sample_validation` (`35 OK`).
  Next: when you ask what to do next in the lab, the answer should now simply be to connect the normal candidate sample, connect the hybrid candidate sample, and tell me which is which so the suite can be run in order.

## 2026-04-23 12:59
- [done] Check whether the currently connected normal-favored sample survives a practical bias sweep across multiple sequence architectures
  Result: the smoke matrix completed successfully at -0.1 / 0.0 / +0.1 V for normal, rapid, cp_first, and peis_first with no sequence crash.
  Evidence: C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\results\sequence_voltage_smoke_matrix_20260423_125006\matrix_summary.json
  Next: expand from smoke settings to longer, more analysis-relevant live policies once the immediate robustness pass is complete.
- [partial] Determine whether CA supports stepwise/staged dt inside a single program
  Result: after fixing the earlier parameter-shape bug, single-program list-valued time_interval is rejected by EC-Lib with ERR_GEN_INVALIDPARAMETERS, while a two-program fallback (10 s at 0.01, then 10 s at 0.1) works and produces the expected observed dt in each window.
  Evidence: C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\results\variable_dt_ca_probe_20260423_125707\variable_dt_ca_probe_summary.json
  Next: treat staged dt as a split-program technique for now, then evaluate whether that workaround meaningfully improves FFT HF reconstruction once a hybrid-needed sample is connected.
## 2026-04-23 13:11
- [done] Pick a best current normal-sample runtime logic from live results and rerun it to verify
  Result: the first shallow candidate (normal-only PEIS to 6 Hz) failed at all three biases, but the next candidate (normal-only PEIS to 1 Hz, 20 pts, 10 mV) succeeded at -0.1 / 0.0 / +0.1 V with peis_only_sufficient = true across the board.
  Evidence: C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\results\normal_candidate_lf1p0_confirmation_matrix_20260423_130949\confirmation_summary.json
  Next: keep this as the current best verified logic for the connected normal-favored sample while the remaining open work stays focused on CP-driven live optimization and staged-dt/FFT value.
## 2026-04-23 13:14
- [done] Reframe the runtime logic as sample-blind rather than using post-hoc sample labels as inputs
  Result: a concrete unknown-sample 2-stage policy now exists and has been run on the currently connected sample. Stage 1 is a normal scout (PEIS to 1 Hz, 20 pts, 10 mV); if that scout is already sufficient and asks for no deeper LF, the run stops there, otherwise it escalates to a rapid/hybrid follow-up.
  Evidence: C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\results\unknown_sample_policy_probe_20260423_131405\unknown_sample_policy_probe_summary.json
  Next: validate that the same stage-1 scout fails safe and escalates correctly once the hybrid-needed sample is connected.
## 2026-04-23 - Codex � true CP-first runtime probe status
- Resolved this run:
  - build and validate a real `Vdc stabilization -> dV scout CA -> CP-only FFT LF seed -> PEIS` path instead of the earlier pseudo-CP-first rapid scaffold.
  - remove the blocking `Select full Impedance data file` dialog from CP-only seed estimation by avoiding the old interactive fallback path.
- Current finding:
  - true CP-first is now operational on the live setup.
  - On the current normal-leaning sample, CP-only seed `~0.075 Hz` is much deeper than the post-PEIS refreshed normal LF `~0.751 Hz`, so the CP-first front-end works but is conservative.
- Still open:
  - tighten the stage1/2 live stop thresholds so normal-like samples do not overshoot into overly deep PEIS due to conservative CP-only seeds.
  - validate whether current-sample CP-only seed is actually beneficial on a hybrid-needed sample.
  - add broader fitting/model-selection work after the runtime branch logic is steadier.
- Next concrete step: run side-by-side comparison of `PEIS-first scout`, old pseudo-`cp_first`, and this true CP-first on the same current sample using the newly saved artifact, then repeat on the next hybrid-needed sample.
## 2026-04-23 18:09
- [in_progress] Compare realtime CA-driven candidate logics with fixed 30 mV perturbation on the live hybrid-leaning cell
  Result so far: a dedicated live comparison script now exists and has been launched in the background. Instead of sweeping dV, it fixes 30 mV and compares realtime-vs-static CA stopping, raw-vs-detrended stabilization, and tiny-PEIS-scout on/off while walking the full +0.3 -> -0.3 V bias ladder.
  Evidence:
  - script: C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\tools\run_live_realtime_protocol_comparison.py
  - launch logs: C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\results\live_realtime_protocol_comparison_launch
  - active run root: C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python\results\live_realtime_protocol_comparison_20260423_180912
  Next:
  - let the ladder run
  - identify which candidate gives the best mix of runtime, correct hybrid-vs-normal branching, and fit quality
  - retry the best optimized follow-up and carry that forward to later temperature plateaus.
## 2026-04-23 19:08
- [pending] Long-horizon future ideas to revisit after the core live runtime policy is stable
  Scope:
  - thick-film / LF-dominant simplified measurement ideas, including possible source-meter-only surrogates for LF/capacitance-focused experiments
  - automatic interpretation beyond measurement automation, including anomaly detection, repeat-condition confirmation, and adaptive follow-up experiments for Arrhenius nonlinearity / degradation-vs-intrinsic checks
  - reaction-order / effective-pO2 global analysis for the future LSCF composition family, including automated extraction, plotting, and cross-composition mechanism ranking
  Rule:
  - keep these as future-plan items for now
  - do not let them displace the current live work on realtime runtime logic, optimized parameter extraction, and hybrid-vs-normal branching unless extra time opens up

## 2026-04-23 20:05 | Codex

### AI Coordination Protocol
- [done] Establish mandatory session-claim / start-stop logging across the linked Microprobe Python and Convert_CP_to_EIS 1 projects
  Decision: every AI must claim exact scope/files in the plan log before substantial work, then append a matching Session end note in the update log when stopping so the claim is explicitly released.
  Next: follow this protocol for every future AI session and keep claims narrow enough to avoid overlapping edits.

## 2026-04-23 20:12 | Codex

### Future Analysis Brainstorm
- [done] Refine the far-future `chemical capacitance` / `reaction order` ideas into a staged roadmap
  Decision: treat these as post-runtime-stability analysis layers, with `chemical capacitance` first as the lower-risk extension and `reaction order` second after gas/temperature/bias reproducibility is trusted.
  Next: define a minimal future dataset spec covering repeated temperature / gas plateaus, trustworthy low-frequency fits, and normalized metadata needed for `Cchem` / pO2-slope analysis.

## 2026-04-23 20:13 | Codex

### Reference Review Claim
- [done] Review references for `RQ fitting`, `chemical capacitance`, `oxygen nonstoichiometry`, `oxygen chemical potential`, and `reaction order`
  Decision: identify the exact reference(s) first, then summarize the extraction path from fit parameters and operating conditions before proposing any automation.
  Next: if the user confirms the exact paper set, map the formulas onto current fit outputs (`R`, `Q`, `n`, bias, `pO2`) and define a minimal export schema for future automation.
[2026-04-23 20:50:49] Short-term runtime policy update: keep 30 mV fixed for CA dV and PEIS perturbation, use 60 s static pre/post holds whenever the logic is not truly optimized/live-adaptive, and treat 10 min per run as the hard safety cap. After this focused 0.2..-0.2 campaign, compare raw vs detrended vs early-step-preserved CA->FFT behavior under bias before promoting any hybrid merge logic.
[2026-04-23 20:54:15] Add sparse reference-anchor runs to each temperature plateau as ground-truth answers for the realtime policies. For representative anodic / near-OCV / cathodic biases, run a deliberately conservative long protocol (long pre/post holds, long CA, deeper PEIS) so later fast logic can be graded against a near-truth full response.
[2026-04-24 13:10:00] Partial advance on the top-priority runtime logic: a dedicated continuous station-side prototype now exists for `continuous preCA -> continuous dV scout -> stable pre-tail + post-step FFT -> seeded PEIS`, with `CA dt=0.1 s` and `PEIS floor=0.5 Hz`. This closes the earlier gap where the station-side CA-first path was effectively `post-only FFT`. Next: decide whether to promote this combined-pre/post FFT input into the shared station runtime after more live cells are checked.
[2026-04-24 13:10:00] Still blocked item: the current live cell remained `tail_not_stabilized` even under the new continuous pre/post reference run, so the main remaining question is no longer whether `stable pre-tail + post-step` helps (it does directionally), but whether the present live cell is simply too noisy / too unsettled to validate the logic cleanly. Next concrete step: rerun the same continuous pre/post seeded reference on a cleaner or more stable cell and compare directly against the saved `[7] 400C` benchmark before promoting any runtime default.

## 2026-04-24 21:06 | Codex

### User-Specified Reference Review
- [done] Review the user-specified PPTX/DOCX references for the exact `RQ fit -> Cchem -> delta -> overpotential -> oxygen chemical potential -> reaction order` workflow
  Decision: prioritize the slide decks and manuscript files the user just named over the broader paper folder, and extract the exact formulas / assumptions used there.
  Next: carry both gas-side `n` and solid-state `lambda` in any future reaction-order automation, with `lambda` defined from the `j` dependence on `aO2` / `pO2,eff` / `mu_O` at fixed gas-side conditions.

## 2026-04-24 21:12 | Codex

### Visualization Idea
- [done] Capture the user's new reaction-order visualization idea
  Decision: compare the standard `n vs mu_O` plot against a richer diagram that uses `mu_O` on the x-axis, composition / stoichiometry on the y-axis, and color to encode the transition from `n = 1` to `n = 2` (or the broader `n, lambda` state).
  Next: when figure design starts, prototype both a simple `n(mu_O)` view and a composition-colored state map to see which better communicates mechanism shifts across defect chemistry.
