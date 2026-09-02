# Tools directory (`tools/`)

50+ scripts: live OLE-COM run orchestrators, post-run analysis/fitting,
interactive tuning GUIs, hardware diagnostics, and a long tail of one-off
experiment scripts accumulated across many validation sessions. Grouped by
role below rather than alphabetically.

## Post-processing and analysis (the offline "full arc" pipeline)

- **`make_olecom_full_arc_from_run.py`** — post-processes a completed OLE-COM
  run: merges CA-FFT-recovered low-frequency impedance with measured PEIS
  high-frequency impedance into one "full arc," fits an equivalent-circuit
  model (RRQ/RQRQ/RRQRQ/RQRQRQ, via `Analysis_Convert_CP_to_EIS/EIS_Fitting.py`),
  generates plots (a 3-panel |Z|/Phase/CA-input overview PNG, plus a dedicated
  equal-aspect Nyquist PNG with a Monte Carlo parameter-error estimator),
  supports a hot/relaxed10x30/zp30 3-variant pipeline with automatic
  "recommended" variant selection, and has a CLI (`--absolute-error`,
  `--fit-model`, etc.).
- **`ca_fft_tuning_gui.py`** — native Tkinter GUI wrapping
  `make_olecom_full_arc_from_run.py`'s plotting/fit core, for interactively
  re-tuning FFT/fit parameters (including the relative-vs-absolute error
  weighting toggle and Monte Carlo frequency-binning) on a saved run without
  re-measuring. Recompute / Save fits / Monte Carlo confidence / Save fits +
  Monte Carlo buttons.
- **`extract_full_arc_results_to_csv.py`** — flattens `full_arc_summary.json`
  across many run folders into one CSV for downstream spreadsheet/plotting
  use.
- **`batch_reprocess_ca_mprs.py`**, **`diag_inspect_ca_mpr.py`**,
  **`diag_replay_pre_tail_selection.py`** — newer batch-reprocessing and
  diagnostic scripts for re-running the full-arc pipeline or inspecting raw
  `.mpr` CA files and pre-tail selection behavior.
- **`validate_auto_circuit_selection.py`** — validates automatic
  equivalent-circuit-model selection across saved live-hybrid runs by
  re-running `EIS_Fitting` against `continuous_prepost_seeded_reference_summary.json`
  folders.
- **`prepare_live_sample_validation_suite.py`** — thin CLI wrapper around
  `live_sample_validation.write_validation_suite` (see
  [`core_automation.md`](core_automation.md) — that module is a
  documentation generator, not measurement code).

## OLE-COM live orchestrators (current production family)

All build on a shared `OleComController` base defined in
`run_olecom_pre_scout_post_hybrid.py`. This is the most actively maintained
cluster and is what feeds `make_olecom_full_arc_from_run.py`.

| File | Purpose |
|---|---|
| `run_olecom_pre_scout_post_hybrid.py` | **Base module.** `OleComController` (COM connect/LoadSettings/RunChannel wrapper), MPS row helpers, MPR parsing, plotting, and the core "pre-hold → dV scout → post-hold → seeded PEIS" hybrid sequence. |
| `run_olecom_pre_scout_live_stop_hybrid.py` | Adds **live-stop** logic: continuous pre+scout CA with early termination based on live stability, separate post, PEIS. Imported by `driver_biologic_olecom.py`. |
| `run_olecom_three_bias_live_stop_queue.py` | Queue/campaign runner: drives a multi-bias ladder (`--biases`) through the live-stop hybrid, with adaptive pre-hold-duration learning and EC-Lab process-conflict recovery. |
| `run_olecom_multicycle_contact_ladder.py` | Repeats the three-bias ladder across multiple cycles with between-cycle OCV contact checks and automatic EC-Lab process recovery — one level above the queue runner. |
| `run_continuous_prepost_seeded_reference.py` | Standalone "continuous pre/post seeded reference" run: stable pre-tail + post-step CA-FFT merged with PEIS. The reference/ground-truth run type; imported by `live_seeded_sequence.py` and invoked as a subprocess by `run_400c_priority_queue.py`. |

## OLE-COM diagnostic/probe scripts

Narrow, single-hypothesis scripts investigating specific EC-Lab/OLE-COM
instability issues, each documenting its hypothesis in a docstring.

| File | Purpose |
|---|---|
| `probe_olecom_duplicate_process_conflict.py` | Tests whether dropping/recreating the OLE-COM COM reference spawns a second competing `EClab.exe`. |
| `probe_olecom_reconnect_dialog.py` | Tests whether repeated `connect()` cycles alone trigger EC-Lab's blocking "reconfirm safety limits" dialog. |
| `probe_olecom_ocv_readback.py` | Focused OCV-readback timing/behavior probe. |
| `olecom_admin_load_test.py` | Standalone (raw `comtypes`, not the shared controller) admin-context `LoadSettings` diagnostic. |

## BioLogic-direct live experiment scripts (`run_*`, non-OLE-COM)

A large cluster of near-identical scaffolding
(`driver_biologic.BioLogicController`, `analysis_adapter`,
`measurement_sequence`) built incrementally across many one-off validation
sessions — an evolutionary series of increasingly sophisticated
bias-sweep/protocol-comparison experiments. Several are close variants of
each other rather than clearly distinct permanent tools.

`run_adaptive_bias_sweep.py`, `run_bias_0p25_bundle_with_fit.py`,
`run_case7_exact_live.py`, `run_live_learning_sweep.py`,
`run_live_protocol_voltage_matrix.py`,
`run_live_realtime_protocol_comparison.py`,
`run_live_saturation_guided_sequence.py`,
`run_normal_candidate_baseline_bundle.py`,
`run_normal_optimized_confirmation_matrix.py`,
`run_sequence_architecture_matrix.py`, `run_sequence_voltage_smoke_matrix.py`,
`run_true_cp_first_unknown_sample_probe.py`,
`run_unknown_sample_cp_first_policy_probe.py`,
`run_unknown_sample_policy_probe.py`, `run_400c_priority_queue.py`,
`run_350c_longterm_voltage_supervisor.py` (the largest/most elaborate of
this family — a long-running 0V-repeat + voltage-ladder campaign supervisor
at 350°C with matplotlib summary plots).

**Consolidation candidates**, flagged for anyone touching this area:
`run_true_cp_first_unknown_sample_probe.py`,
`run_unknown_sample_cp_first_policy_probe.py`, and
`run_unknown_sample_policy_probe.py` share nearly all scaffolding and
overlapping intent (deciding measurement policy for an unknown sample) —
likely successive iterations rather than three distinct permanent tools.
Similarly, `run_normal_candidate_baseline_bundle.py` /
`run_normal_optimized_confirmation_matrix.py` and
`run_live_protocol_voltage_matrix.py` /
`run_live_realtime_protocol_comparison.py` /
`run_sequence_architecture_matrix.py` /
`run_sequence_voltage_smoke_matrix.py` look like the same evolutionary
lineage of "which sequence/bias combo is best" experiments. Check for an
existing near-match before adding a new one.

## BioLogic driver benchmarking/probing

| File | Purpose |
|---|---|
| `benchmark_biologic_live_polling.py` | Benchmarks live-value polling responsiveness/latency. |
| `benchmark_biologic_stop_behavior.py` | Benchmarks technique-stop timing. |
| `probe_biologic_buffered_peis.py` | Probes buffered/streamed PEIS data retrieval while a technique runs. |
| `probe_variable_dt_ca.py` | Probes variable-dt CA acquisition via `easy_biologic.base_programs`. |
| `biologic_import_check.py` | Environment diagnostic — verifies BioLogic/easy-biologic imports resolve on the runner PC. |
| `tmp_dt_compare.py` | **One-off debug script**, not a reusable tool — `tmp_` name, hardcoded personal Windows path, no CLI. Compares dt=0.01 vs dt=0.001 CA recording. Candidate for archival/removal. |

## MFC (mass flow controller) diagnostics

| File | Purpose |
|---|---|
| `mfc_protocol_diagnostic.py` | Read-only serial protocol probe distinguishing legacy ASCII vs. STX+hex-address Aera MFC command styles. |
| `mfc_step_response_test.py` | Interactive step-response logger (setpoint registration vs. measured-output ramp). |
| `mfc_version_check.py` | Prints MFC driver/config version info on the runner PC. |
| `mfc_write_smoke_test.py` | Interactive write smoke test — temporarily force-enables `driver_mfc` writes after a typed confirmation. |
| `mfc_write_variant_test.py` | Tries several candidate raw setpoint-write command formats. |

## Camera / vision probes and live viewers

| File | Purpose |
|---|---|
| `probe_swift_camera.py` | Scans OpenCV backends/indices for the Swift/USB microscope camera. |
| `live_swift_camera_view.py` | Minimal live OpenCV preview to confirm a working video stream. |
| `live_electrode_overlay_view.py` | Live camera preview with the electrode-circle detector overlaid in near-real-time (bridges the camera feed to `vision.electrode_mapper`/`vision.circle_detector`). |

## Simulation / dry-run validators (no hardware contact)

| File | Purpose |
|---|---|
| `simulate_adaptive_sequence.py` | Headless replay of a measured rapid-EIS folder through the adaptive engine. |
| `run_adaptive_engine_demo.py` | Headless dry-run demo of `AdaptiveMeasurementEngine` with fake analyzers. |
| `simulate_olecom_gui_auto_cycle.py` | Dry-runs the intended GUI automation cycle (move → OCV contact search → OLE-COM queue in `--simulate` mode → next site). |
| `simulate_olecom_matrix_learning_protocol.py` | Simulates the overnight gas×site×voltage matrix scheduler policy without touching hardware. |

## Packaging / infra

| File | Purpose |
|---|---|
| `package_win7_runner.ps1` | PowerShell packaging script — zips the project (excluding venvs/results/caches) for deployment to the Windows 7 instrument PC. |
| `presets/dummy_cell_bias_only_short_test.csv` | Sample preset CSV (bias-only sweep template), not code. |

## Flagged as dead code / one-off debug scripts

- **`tmp_dt_compare.py`** — see above.
- **`make_parameter_logic_onepage_tmp.py`** — `_tmp` filename, hardcoded
  personal Windows paths for both input run and output directory, builds a
  one-off explanatory figure for a specific run/presentation. Not reusable
  as-is.

Neither is wired into any other script's import graph — safe to archive or
delete without checking callers, should anyone want to clean these up.
