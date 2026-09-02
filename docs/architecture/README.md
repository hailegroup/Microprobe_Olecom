# Codebase architecture

This is a code-level map of the repository: what each module does and how the
pieces fit together. For setup/launch instructions see the top-level
[`README.md`](../../README.md), [`WIN7_RUNNER_SETUP.md`](../WIN7_RUNNER_SETUP.md),
[`WIN7_OLECOM_README.md`](../WIN7_OLECOM_README.md), and
[`IMAGE_MONITOR_GUIDE.md`](../IMAGE_MONITOR_GUIDE.md) — this doc set doesn't
repeat that material.

## What this system is

Control and automation software for a scanning microprobe electrochemical
measurement rig: a motorized XYZ stage, a Watlow furnace controller, Aera
mass-flow controllers (gas composition), a BioLogic potentiostat (EIS/CA
measurements), and an optional camera for locating electrodes and guiding the
probe tip. It runs unattended overnight sequences across many
electrode/temperature/gas/bias combinations, using CA (chronoamperometry)
transients and FFT recovery to keep individual measurements fast while a
conventional PEIS sweep still measures the frequencies FFT can't reliably
reach.

## Subsystem docs

| Doc | Covers |
|---|---|
| [`core_automation.md`](core_automation.md) | Root-level orchestration, hardware drivers, and measurement/adaptive policy modules — `gui.py`, `run_automation.py`, `config.py`, `driver_*.py`, `adaptive_engine.py`, `stabilization_policy.py`, `measurement_sequence.py`, `cp_first_policy.py`, `live_seeded_sequence.py`. |
| [`vision.md`](vision.md) | The `vision/` package — electrode/probe detection, DXF-layout alignment, drift tracking, Z-height calibration, and the native-Tkinter tuning GUIs. |
| [`eis_analysis.md`](eis_analysis.md) | `Analysis_Convert_CP_to_EIS/` — FFT-based CP→EIS recovery, equivalent-circuit fitting, and the offline validation/comparison scripts this logic was engineered in. |
| [`tools.md`](tools.md) | The 50+ scripts in `tools/` — live OLE-COM orchestrators, post-processing, diagnostics, tuning GUIs, and one-off experiments. |

## Two independent entry points

There are **two separate top-level programs that both drive the same
hardware**, and they do not import each other:

- **`gui.py`** — the production Tkinter GUI (what actually runs on the Win7
  instrument PC day to day). ~10,200 lines; one large `MicroprobGUI(tk.Tk)`
  class with 8 tabs (Hardware, CSV List, Semi-auto, Full-auto, Run/Monitor,
  Image Monitor, EIS Monitor, Manual Control).
- **`run_automation.py`** — a headless CLI that reads a conditions CSV and
  runs the same kind of overnight sequence with no GUI.

Both re-implement large parts of the same logic (Z-contact search, adaptive
runtime wiring, vision drift tracking) rather than sharing it through a common
module — `gui.py` has many comments explicitly cross-referencing
`run_automation.py` functions by name to keep the two in behavioral parity
by hand. See [`core_automation.md`](core_automation.md) for the details,
including one real divergence worth knowing about: `run_automation.py` still
imports the legacy `driver_biologic.py` (easy-biologic) rather than the
production `driver_biologic_olecom.py` backend `gui.py` uses by default.

## High-level data flow

```
config.py (hardware addressing + safety policy)
        │
        ▼
driver_motor.py / driver_mfc.py / driver_temp.py / driver_biologic_olecom.py (or driver_biologic.py)
        │
        ▼
measurement_sequence.py  ──►  live_seeded_sequence.py  ──►  tools/run_continuous_prepost_seeded_reference.py
   (rapid/normal EIS)          (CA-FFT-seeded PEIS)               │
        │                                                          ▼
        │                                                   cp_first_policy.py
        │                                            (FFT-ready CA trace, PEIS-LF recommendation)
        ▼
adaptive_engine.py  ──►  analysis_adapter.py / full_processing_adapter.py  ──►  Analysis_Convert_CP_to_EIS/
 (next-point policy)         (bridge to the analysis package)                  (FFT recovery + circuit fitting)
        │
        ▼
gui.py  /  run_automation.py   (the two orchestrators above pull all of this together)
        │
        ▼
vision/ (parallel subsystem: electrode/probe detection, drift correction, Z-seed calibration)
        │
        ▼
tools/ (live OLE-COM run scripts, post-run full-arc fitting, batch CSV export, diagnostics)
```

The hybrid CA-FFT + PEIS measurement idea (documented in depth in
[`eis_analysis.md`](eis_analysis.md) and [`tools.md`](tools.md)) recurs at
every layer: a short CA transient is Fourier-decomposed to recover
low-frequency impedance quickly, merged with a real PEIS sweep that covers
the higher, more reliably-measured frequencies, and the merged spectrum is
fit to an equivalent-circuit model.

## Known architectural debt

Worth knowing before changing code in these areas:

- **`gui.py` and `run_automation.py` duplicate rather than share** their
  per-row run loop, Z-contact search, and vision drift-tracking wiring. A fix
  in one does not apply to the other unless done by hand in both.
- **`run_automation.py` imports the legacy `driver_biologic.py`**, not the
  production `driver_biologic_olecom.py` that `gui.py` defaults to —
  `config.py` explicitly warns the two BioLogic backends should not be mixed
  in one instrument session. Confirm which path an automation change needs
  before assuming `run_automation.py` matches current GUI behavior.
- **`vision/circle_detector.py` and `vision/electrode_drift.py` each carry
  their own, slightly different `detect_circle_in_roi` implementation** —
  not shared, a maintenance seam if one is fixed without the other.
- **`tools/` has three-plus near-duplicate "which measurement policy is
  best for an unknown sample" experiment scripts** and several other
  evolutionary-lineage script families (see [`tools.md`](tools.md)'s
  duplication notes) — check for an existing near-match before adding a new
  one-off `run_*.py` script.
- **A couple of `tools/` scripts are one-off debug artifacts, not reusable
  tools** (`tmp_dt_compare.py`, `make_parameter_logic_onepage_tmp.py`) —
  hardcoded personal paths, no CLI. Candidates for archival, not templates
  to copy from.

Some things that look like debt but are deliberate, documented choices — not
worth "fixing":

- **`vision/tuning_gui/probe_detector.py` is an intentional fork**, not an
  accidental copy, of production `vision/probe_detector.py` — kept separate
  specifically so the interactive tuner and the production detector can't
  silently diverge from under each other.
- **Two independent electrode-finding strategies coexist** in `vision/`
  (whole-frame cold-start detection vs. DXF-template-driven tracking) because
  they solve different problems (first-time layout discovery vs. continuous
  drift correction of an already-known layout).
