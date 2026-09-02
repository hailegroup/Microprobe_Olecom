# Core automation: orchestration, drivers, and policy

Root-level modules: the two entry points (`gui.py`, `run_automation.py`),
hardware drivers, and the GUI-independent policy/sequence layer they both
build on. See [`README.md`](README.md) for how this fits into the whole
system, and [`vision.md`](vision.md) / [`eis_analysis.md`](eis_analysis.md)
for the two subsystems this layer hands off to.

## Configuration

### `config.py`

Plain module-level constants — no classes or functions — edited directly to
match the physical rig. Sections: serial ports/baud rates; BioLogic IP/port
and backend selection (`BIOLOGIC_BACKEND = 'olecom'` by default,
`BIOLOGIC_ALLOW_EASY_FALLBACK = False`, plus OLE-COM bandwidth/I-range/PEIS
tuning constants); motor calibration and `STAGE_SAFE_MOVE` (backlash-overshoot
and travel-clearance safety policy); Watlow PID tables and temperature safety
thresholds; Aera MFC addressing/scale/write-safety settings; and a vision/
camera section (backend/index, calibration file paths, drift-recheck cadence
and thresholds).

Every driver, `gui.py`, and `run_automation.py` import from here — it's the
single source of truth for hardware addressing and safety policy. The
`BIOLOGIC_BACKEND` comment block is itself an architecture note: OLE-COM
(production) and easy-biologic (legacy) must not be mixed in one instrument
session, since the potentiostat may need a power-cycle to switch cleanly
between them.

## Entry points

### `gui.py` (~10,200 lines)

The production Tkinter GUI — the primary way the instrument is actually
operated. One large `MicroprobGUI(tk.Tk)` class (`_build_ui` builds a
`ttk.Notebook` with 8 tabs, each built by its own `_build_tab_*` method:
Hardware, CSV List, Semi-auto, Full-auto, Run/Monitor, Image Monitor, EIS
Monitor, Manual Control).

Hardware/driver imports are wrapped in try/except so the GUI still launches
if a driver or the optional vision stack (`cv2`, `PIL`, `vision.*`,
`vision_stage_mapper`) is unavailable — those features just report
unavailable instead of crashing the app. `_safe_import()` picks the BioLogic
backend at runtime from `config.BIOLOGIC_BACKEND`: `driver_biologic_olecom`
by default, falling back to `driver_biologic` only if OLE-COM import fails
and `BIOLOGIC_ALLOW_EASY_FALLBACK` is set.

The full-auto/adaptive pipeline (`analysis_adapter`, `full_processing_adapter`,
`adaptive_engine`, `adaptive_types`, `live_seeded_sequence`) is imported
*lazily*, inside the run methods, not at module top. The automation loop
itself: the Start button spawns `threading.Thread(target=self._run_worker)`;
`_run_worker` is an exception-safe wrapper (furnace ramp-down/cleanup in a
`finally`) around `_run_worker_impl`, the GUI's own per-row CSV loop —
structurally the GUI's analogue of `run_automation.py`'s `_run_conditions`.

Not a thin GUI: it embeds the full measurement/automation/adaptive/vision
orchestration logic itself, on top of what lives in the shared modules below,
which is why it's the largest file in the repo by a wide margin.

### `run_automation.py` (~1,750 lines)

Headless CLI: `python run_automation.py conditions.csv [--result-dir ./results]`.
Reads a condition CSV (columns documented in the module's own docstring —
temperature, gas, stage X/Y/Z, auto-contact-Z search params, vision
auto-track XY, PEIS/CA parameters, stabilization hold times, labels) and runs
an overnight sequence with no GUI: set temperature → set gas → move stage →
Z-contact search → adaptive-seeded rapid-EIS sequence → analysis/full
processing, looped per row in `_run_conditions`, driven by `main()`'s
argparse entry point.

Includes its own headless counterparts to GUI features: `find_contact_z`
(OCV-based Z-contact search), a vision drift-tracking block
(`_open_vision_camera`, `_init_vision_tracking_runtime`,
`_maybe_refresh_vision_drift_correction`, `_probe_occluded_layout_indices`,
`_record_electrode_z_contact`), and adaptive-runtime glue
(`_create_adaptive_runtime`, `_prepare_adaptive_row`,
`_apply_adaptive_recommendation`, `_finalize_adaptive_row`,
`_write_adaptive_summary`).

**Divergence worth knowing about:** this file imports `BioLogicController`
from `driver_biologic` (legacy easy-biologic), not `driver_biologic_olecom`
— unlike `gui.py`'s default. That's either an older/parallel automation path
not yet migrated to OLE-COM, or one meant only for non-Win7/debug use; either
way, don't assume it exercises the same BioLogic code path as the production
GUI.

## Hardware drivers

All four take their addressing/tuning constants from `config.py` and are
imported by both `gui.py` and `run_automation.py`.

- **`driver_biologic_olecom.py`** — production BioLogic driver, EC-Lab
  OLE-COM only. `BioLogicOleComController` (`connect`/`disconnect`,
  `discover_devices`, `list_channels`, `get_ocv`, `stop_measurement`,
  `run_peis`, `run_hybrid_live_stop`), aliased as `BioLogicController` at the
  bottom of the file so callers can import a uniformly-named class regardless
  of backend. Reaches into `tools/run_olecom_pre_scout_post_hybrid.py` and
  `tools/run_olecom_pre_scout_live_stop_hybrid.py` for its actual
  measurement-sequence implementations (see [`tools.md`](tools.md)) — and
  through those, transitively, into `cp_first_policy.py`.
- **`driver_biologic.py`** — legacy driver on `easy_biologic` (SP-200/SP-300
  LAN). Same-named `BioLogicController` class with a compatible surface
  (`connect`, `run_peis`, `get_ocv`, `stop_measurement`, `list_channels`,
  `discover_devices`) so calling code can treat either backend
  interchangeably via duck typing. Documented as legacy in both its own
  docstring and `config.py`, but still actively imported (not dead) — by
  `run_automation.py` unconditionally, and by `gui.py` as a guarded fallback.
- **`driver_motor.py`** — IMS MDrive 23 Plus stepper driver over RS-485
  (party-line X/Y/Z addressing on one COM port). `MDriveMotor`:
  `move_abs`/`move_rel`/`home`/`get_position`, and safety-aware
  `plan_safe_xyz_move`/`move_xyz_safe` implementing the backlash-overshoot
  and travel-clearance policy from `config.STAGE_SAFE_MOVE`.
  `UnsafeStageMoveError` is raised when a requested move can't be executed
  safely.
- **`driver_mfc.py`** — Aera FC-PA7800C mass-flow-controller driver over
  RS-485 (two channels, gas A/B). `AeraMFC`:
  `get_flow`/`set_flow`/`set_setting_fast` with readback-verification retry,
  `open_valve`/`close_valve`/`flow_mode`. Docstring records hardware reply-
  protocol quirks discovered empirically; a versioned driver-string constant
  (`MFC_DRIVER_VERSION`) suggests iterative field-tuning of write timing.
- **`driver_temp.py`** — Watlow EZ-ZONE PM controller, Standard Bus mode, via
  the third-party `pywatlow` library (monkey-patched at import time to fix an
  instance-handling bug — see `_apply_pywatlow_patch`, and the related
  root-level `patch_pywatlow.py`). `WatlowController`:
  `get_temperature`/`set_temperature`, PID-gain switching based on
  `WATLOW_PID_SWITCH_TEMP`, `safe_shutdown`, `wait_stable`.

## GUI-independent policy/sequence layer

Both entry points build on this layer, which is where most of the actual
measurement decision-making lives — deliberately kept independent of
Tkinter so `run_automation.py` can use the same logic headlessly.

- **`measurement_sequence.py`** — `rapid_eis_sequence` and
  `normal_eis_sequence`: CA hold → PEIS → CA sequence/perturbation → save raw
  data, against a `biologic` controller object passed in as a parameter.
  Imports neither driver module directly — it calls duck-typed methods on
  whatever controller it's given, which is exactly what lets it run
  unmodified against either `BioLogicController` implementation.
- **`live_seeded_sequence.py`** — `live_seeded_rapid_eis_sequence`, the
  production wrapper for the optimized live protocol: stable pre-bias CA →
  live-stopped dV-scout CA → FFT low-frequency recommendation → PEIS at that
  recommended LF (preferring measured-PEIS overlap where available). Reaches
  into `tools/run_continuous_prepost_seeded_reference.py`, which in turn uses
  `cp_first_policy.py`.
- **`cp_first_policy.py`** — CA("CP")-first live protocol logic: builds
  despiked, median-binned "FFT-ready" CA traces (`build_fft_ready_ca_trace`)
  and recommends the PEIS low-frequency bound from a completed run
  (`recommend_peis_lf_from_cp_txt`), plus chunked-hold helpers for the live
  scout/stabilization workflow. Not imported directly by `gui.py` or
  `run_automation.py` — reached only transitively through
  `driver_biologic_olecom.py` and `live_seeded_sequence.py`'s `tools/`
  dependencies. Its design mirrors (was likely engineered in)
  `Analysis_Convert_CP_to_EIS/compare_full_vs_optimized_fit.py` — see
  [`eis_analysis.md`](eis_analysis.md).
- **`stabilization_policy.py`** — bias-stabilization logic for the pre-PEIS
  hold: `analyze_pre_peis_stability`, `analyze_completed_bias_hold`,
  `recommend_initial_pre_peis_hold`, `recommend_post_peis_hold`,
  `choose_bias_sweep_order` (orders measurement voltages using history
  matched on electrode/gas/temperature regime). Reached only transitively,
  via `adaptive_engine.py` and `cp_first_policy.py` — not imported directly
  by either entry point.
- **`adaptive_types.py`** — pure data models, no logic: `PointMetadata`,
  `MeasurementExecution`, `MeasurementFiles`, `AnalysisResult`,
  `FullProcessingResultPayload`, `RecommendationPayload`,
  `CurrentRunPolicyDecision`, `RemeasurementRequest`, `PointStateRecord`.
- **`adaptive_engine.py`** — the GUI-independent adaptive orchestration
  layer: after each point, decides whether the next point should run
  PEIS-only ("normal") or the full rapid CA/CP+PEIS hybrid mode, manages
  background analysis/full-processing thread pools, and tracks per-point
  state across a run. `AdaptiveMeasurementEngine.recommend_next_point` is the
  core decision function; `plan_two_stage_current_run_policy` handles a
  separate two-stage-current policy. Delegates its actual policy math to
  `Analysis_Convert_CP_to_EIS/adaptive_measurement_policy.py` (imported
  lazily inside `__init__`) — see [`eis_analysis.md`](eis_analysis.md).
- **`analysis_adapter.py`** — fast, synchronous bridge to the
  `Analysis_Convert_CP_to_EIS/` analysis backend: PEIS-only sufficiency,
  CP-saturation-guard results, PEIS/CP crossover recommendations, without the
  heavier Excel/plot work. Dynamically adds `Analysis_Convert_CP_to_EIS/` to
  `sys.path` and imports its modules at call time.
- **`full_processing_adapter.py`** — the heavier counterpart, run after the
  live decision loop (so it's free to do Excel export/plotting without
  blocking the next-point decision); wraps
  `Analysis_Convert_CP_to_EIS/run_trusted_sample_analysis.py`'s
  `recover_and_export`.
- **`vision_stage_mapper.py`** — pixel↔stage-mm mapping math (no OpenCV
  dependency, deliberately kept light): affine calibration from clicked
  reference points, electrode grid generation from a design image, Z-parallax
  and XY-bias correction models. Used by both entry points' vision
  drift-tracking and by `vision/electrode_z_seed.py`, which owns the
  higher-level, disk-persisted calibration store built on top of this math —
  see [`vision.md`](vision.md).

## `live_sample_validation.py` — not runtime code

Worth flagging: despite the name, this is a documentation/planning generator,
not measurement-execution code. `build_default_validation_suite` hardcodes a
planned list of experiments to run once hardware is available;
`render_suite_markdown`/`write_validation_suite` produce a
`live_sample_validation_suite.json`/`.md`. Used only by
`tools/prepare_live_sample_validation_suite.py` — not part of the
`gui.py`/`run_automation.py` dependency graph at all.

## Dependency graph

```
gui.py ──(runtime-selects backend)──► driver_biologic_olecom.py (production)
                                    └► driver_biologic.py (fallback only)
gui.py ──► driver_motor.py, driver_mfc.py, driver_temp.py
gui.py ──(lazy)──► measurement_sequence.py, live_seeded_sequence.py,
                    adaptive_engine.py, adaptive_types.py,
                    analysis_adapter.py, full_processing_adapter.py,
                    vision_stage_mapper.py

run_automation.py ──► driver_biologic.py (NOT olecom — see divergence note)
run_automation.py ──► driver_motor.py, driver_mfc.py, driver_temp.py,
                       measurement_sequence.py, live_seeded_sequence.py,
                       adaptive_engine.py, adaptive_types.py,
                       vision_stage_mapper.py

adaptive_engine.py ──► adaptive_types.py, analysis_adapter.py,
                        full_processing_adapter.py, stabilization_policy.py,
                        Analysis_Convert_CP_to_EIS/ (lazy)

driver_biologic_olecom.py ──► tools/run_olecom_pre_scout_*.py ──► cp_first_policy.py
live_seeded_sequence.py ──► tools/run_continuous_prepost_seeded_reference.py ──► cp_first_policy.py
cp_first_policy.py ──► stabilization_policy.py

all drivers + gui.py + run_automation.py ──► config.py

live_sample_validation.py: isolated (only tools/prepare_live_sample_validation_suite.py + tests use it)
```
