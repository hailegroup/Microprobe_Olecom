# EIS analysis package (`Analysis_Convert_CP_to_EIS/`)

FFT-based recovery of impedance from chronoamperometry (CA/CP) transients,
equivalent-circuit fitting, and the offline validation/comparison scripts
this logic was engineered and proven in. Used both for offline batch analysis
of BioLogic files and as the source the live runtime's optimized-measurement
logic (`cp_first_policy.py`, see [`core_automation.md`](core_automation.md))
was ported from.

The root-level `analysis_adapter.py` and `full_processing_adapter.py` add
this directory to `sys.path` at runtime and import from it dynamically,
rather than treating it as a normal installed package — it's embedded in this
repo as a semi-independent analysis project.

## The two core modules

### `Convert_CP_to_EIS.py` — FFT recovery of impedance from a CA transient

A CA step contains a broad continuum of frequency content (an ideal step's
spectrum spans all frequencies), so a single transient's response encodes
system behavior across a whole frequency band — no need to measure one
discrete sine frequency at a time the way PEIS does.

Pipeline (`FFT_EIS`): differentiate both `V(t)` and `I(t)` (removes the raw
step discontinuity that would otherwise dominate the spectrum, and the same
factor cancels out of the later `V(ω)/I(ω)` ratio) → zero-pad both →
`np.fft.fft` on each → `Z(f) = FT_V(f) / FT_I(f)` at every positive-frequency
bin up to `max_f` (which defaults, via `resolve_fft_max_f`, to the lowest
measured PEIS frequency — i.e. FFT only covers the range PEIS doesn't reach)
→ `extract_log_spaced_impedance` down-samples the dense FFT bin grid to
~100 log-spaced representative points by snapping each target frequency to
its nearest actual FFT bin.

Also: `recommend_peis_lowest_frequency` — recommends the lowest conventional
PEIS frequency needed to cover the noisy high-frequency side of the
FFT-recovered spectrum (frequencies below the cutoff are smooth enough to
trust from FFT; above it, PEIS covers them better).

### `EIS_Fitting.py` — equivalent-circuit fitting

Multi-model equivalent-circuit fitting (RQ/RRQ/RQRQ/RRQRQ/RQRQRQ) via
`scipy.optimize.least_squares` (TRF — the same solver
`scipy.optimize.curve_fit` uses internally when given bounds), with multi-seed
strategies, BIC-based model/seed selection, and Jacobian-SVD-based parameter
uncertainty (`param_std_errors` — direct SVD of the Jacobian, not
`pinv(JᵀJ)`, which squares the condition number and understates errors on
ill-conditioned fits). A `relative_error` toggle controls whether the fit
objective weights residuals by `|Z_data|` (proportional/percentage error,
the default, matching how noise typically scales with impedance magnitude)
or minimizes unscaled absolute error. `Z_model`/`fit_result_to_model_params`
evaluate a fitted circuit's impedance at arbitrary frequencies from a stored
fit-result dict.

## Support layer

- **`Load_CP_Data.py`** (`LD`) — all raw-file ingestion: BioLogic `.mpr` (via
  `yadg`), `.mpt`, and generic delimited text, for both DC (CP/CA) and EIS
  (PEIS) data. `detect_stable_current_start_time` is the auto-trim heuristic
  (finds the largest voltage-step event, then where the pre-step current's
  rolling std settles). `load_dc_from_path`/`load_eis_from_path` are the
  unified, extension-dispatching entry points everything else calls; a
  `filedialog`-driven interactive variant of each also exists for manual use.
  The sole raw-instrument-file loader for the whole directory.
- **`Plotting_Functions.py`** (`PF`) — plotting only, no data processing:
  Nyquist/|Z|/phase mosaics for reference PEIS, recovered EIS, and data-vs-fit
  comparisons; `plot_DC`/`plot_DC_comp` for V(t)/I(t) traces.
- **`Smooth_and_Interpolate.py`** (`SI`) — one function,
  `smooth_and_interpolate`: Savitzky–Golay smoothing + interpolation onto a
  uniform time grid, run immediately before `FFT_EIS` in most pipeline
  variants so the FFT differentiation/ratio step is well-behaved.
- **`matplotlib_compat.py`** — back-compat shim (`install_pyplot_compat`) so
  newer `layout="constrained"`/`subplot_mosaic` matplotlib calls still work
  on the older matplotlib versions these lab PCs run; imported at the top of
  every plotting-capable module in the directory.
- **`export_dict_to_excel.py`** — `export_to_excel`: multi-sheet `.xlsx`
  workbook bundling figures, summary/raw-data dicts, and an optional
  reference-vs-recovered "Total Arc Data" sheet.
- **`export_origin_friendly.py`** — `export_origin_bundle`: flat,
  Origin-software-friendly CSVs (one per data stage — raw DC, treated DC,
  differentiated dV/dI, reference PEIS, recovered EIS, fit input, FFT
  analysis) for manual plotting outside Python. Always called alongside the
  Excel export, not a duplicate of it.

## Adaptive policy prototype

- **`adaptive_measurement_policy.py`** — pure-logic (no I/O, no numpy) module
  deciding next-point measurement parameters (PEIS lowest frequency, CP scout
  duration, or a remeasurement plan) from prior-point history and analysis
  recommendations. `PolicySettings` holds all tunables; `_fit_linear_trend`/
  `_blend_conservative` extrapolate a two-point trend, weighting slowdowns
  more heavily than speedups; `propose_measurement_action` is the top-level
  orchestrator choosing between "measure next point" and "remeasure same
  point". Fully decoupled from the CP/EIS recovery and fitting code (no
  import of `Convert_CP_to_EIS`/`EIS_Fitting`/`Load_CP_Data`) — operates only
  on plain dicts. **This one is genuinely live**: `adaptive_engine.py`
  imports it directly (see [`core_automation.md`](core_automation.md)).
- **`run_adaptive_policy_demo.py`** — non-GUI driver exercising the policy
  module end-to-end with a hardcoded demo history, writing a JSON summary.
  Demonstration/test-harness only; the runtime consumer is `adaptive_engine.py`,
  not this script.

## Validation/comparison drivers

These are where the recovery + fitting + optimized-configuration logic was
engineered and proven against real data, not reusable libraries — file paths
and settings are baked in at module scope.

- **`run_trusted_sample_analysis.py`** — end-to-end driver for one sample:
  loads a CP+PEIS `.mpr` pair twice (auto-trim on and off), runs the full
  smooth → FFT-recover → recommend → fit → export pipeline for each via
  `recover_and_export`, and produces a side-by-side auto-trim comparison
  plot. The reference invocation showing how `LD`, `PF`, `SI`, `DC_EIS`
  (`Convert_CP_to_EIS`), `EISFIT` (`EIS_Fitting`), and both export modules
  compose into one full run.
- **`compare_full_vs_optimized.py`** — **legacy** predecessor comparing a
  "full measurement" against an "optimized" (truncated PEIS + short CP
  recovery) one, using its own inline 2-arc `lmfit`-based circuit model
  rather than `EIS_Fitting.py`. Text-file-only, no `.mpr` support. Reads as
  superseded by the next file rather than actively maintained.
- **`compare_full_vs_optimized_fit.py`** (~2,090 lines — the largest file in
  the directory) — the **current, actively-developed** version of the same
  comparison, `.mpr`-only, using `EIS_Fitting.py`'s `fit_RQRQRQ_stable`
  instead of an inline model. This is where the "optimized live measurement"
  decision algorithm was engineered: `build_fft_ready_ca_trace` (binning +
  per-segment Hampel despiking of current, preserving the voltage-step
  boundary — the function `cp_first_policy.py`'s live version of the same
  name mirrors), `_recover_fft_points_fast` (fast FFT recovery restricted to
  a truncated time window, used inside sweeps), `assess_cp_saturation`,
  `choose_overlap_peis_cutoff` (PEIS "owns" any frequency at/above its
  measured low end; FFT recovery only fills below that),
  `assess_peis_only_sufficiency` (decides whether PEIS alone already reached
  the saturated low-frequency resistance, skipping the CP scout entirely),
  and `recommend_optimized_configuration` — a two-stage fast-search-then-
  verify design (`extract_optimized_parameters_fast` for the search, then a
  full/legacy-smoothing re-run of the winning configuration for trustworthy
  final numbers) that `cp_first_policy.py`'s live pipeline mirrors in shape.

## Standalone / superseded

- **`Exp_CP_data_fitting.py`** — an older, standalone time-domain curve-fit
  experiment (`scipy.optimize.curve_fit` directly on the CA transient: linear
  baseline + bi-exponential decay + diffusion term), representing an
  alternative approach abandoned in favor of the FFT method above. Not
  imported by anything else in the directory or the wider repo.

## Dependency graph

```
Convert_CP_to_EIS.py ──► Load_CP_Data.py, Plotting_Functions.py,
                          Smooth_and_Interpolate.py, matplotlib_compat.py

run_trusted_sample_analysis.py, compare_full_vs_optimized_fit.py
        ──► Convert_CP_to_EIS.py (DC_EIS), EIS_Fitting.py (EISFIT),
            Load_CP_Data.py (LD), Plotting_Functions.py (PF),
            Smooth_and_Interpolate.py (SI),
            export_dict_to_excel.py (Export), export_origin_friendly.py (OriginExport)

compare_full_vs_optimized.py ──► Convert_CP_to_EIS.py only (own inline lmfit model,
                                  no EIS_Fitting.py, no export modules — legacy)

adaptive_measurement_policy.py ──► (isolated: no imports of the above)
run_adaptive_policy_demo.py ──► adaptive_measurement_policy.py

Exp_CP_data_fitting.py ──► Load_CP_Data.py, Plotting_Functions.py (standalone, unused elsewhere)

External consumers (repo root):
  adaptive_engine.py ──► adaptive_measurement_policy.py (lazy import)
  analysis_adapter.py, full_processing_adapter.py ──► dynamically load this whole directory
  cp_first_policy.py: not imported from here, but its live logic mirrors
        compare_full_vs_optimized_fit.py's optimized-configuration search
  tools/make_olecom_full_arc_from_run.py, tools/*: import EIS_Fitting.py directly
        (see tools.md) — the production post-processing path uses EIS_Fitting.py
        but not the rest of this directory
```
