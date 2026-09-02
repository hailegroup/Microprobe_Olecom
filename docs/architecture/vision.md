# Vision subsystem (`vision/`)

Camera-based electrode/probe detection, DXF-layout alignment, drift tracking,
and Z-height calibration. Everything here traces back to a sibling standalone
project ("Image Detection" / Microelectrode Tracker), from which this
codebase has repeatedly ported pure-math/detection logic while deliberately
dropping its `cv2.imshow`-based interactive UI in favor of native Tkinter
widgets inside `gui.py`.

`vision/__init__.py` re-exports only `CircleDetection`, `annotate_detections`,
and `project_points` from `electrode_mapper.py` — a thin, partial public
surface. Most modules are consumed via explicit
`from vision.<module> import ...` rather than through the package `__init__`.

## Two independent electrode-finding strategies

Worth understanding up front, since it explains why several modules seem to
duplicate each other's job:

1. **Cold-start, whole-frame detection** (`electrode_mapper.py`) — no prior
   layout needed; finds circular candidates anywhere in a raw camera frame
   and buckets them into a row/column grid.
2. **DXF-template-driven tracking** (`layout_alignment.py` +
   `electrode_drift.py`) — starts from a known CAD layout and continuously
   re-fits/drift-corrects an affine pixel↔template mapping as the stage
   moves.

They solve different problems (first-time layout discovery vs. continuous
correction of an already-known layout) and are not meant to be unified.

## Detectors

### `vision/probe_detector.py`

`ProbeDetector` — locates a downward-pointing probe tip via convexity
defects: CLAHE → bilateral filter → dual-polarity Otsu threshold (tries both
bright- and dark-region candidates and keeps whichever gives a more dominant
defect) → region/circularity filtering → convex hull → convexity defects →
the deepest defect's far point is the tip, EMA-smoothed across frames. See
inline code comments for the full step-by-step breakdown. Leaf module
(`cv2`/`numpy` only). Imported by `run_automation.py`, `gui.py`, and tests.

### `vision/circle_detector.py`

ROI-scoped Hough-circle refinement, for snapping a rough/expected electrode
center to the true circle edge.

- `preprocess_for_circle_search` — CLAHE + bilateral filter, tuned for uneven
  microscope illumination.
- `detect_circle_in_roi(processed, center, expected_radius, ...)` — runs
  `cv2.HoughCircles` in a small ROI; picks the candidate *closest to
  `center`*, not the top accumulator-score hit, to avoid snapping to a
  neighboring electrode.
- `load_hough_params(path)` — loads a `*_params.json` tuning file (shared
  schema with `vision/tuning_gui/`).

**Note:** `vision/electrode_drift.py` has its own, separately-implemented
`detect_circle_in_roi` (int-typed params, returns a `Circle` dataclass) — a
similar-but-not-shared duplicate, not a call into this one. If you fix a bug
in one, check whether the other needs the same fix.

### `vision/electrode_mapper.py`

The cold-start, whole-frame electrode detector.

- `CircleDetection` (dataclass) — one detected circle: pixel pos/radius,
  row/col/ordinal, size group, optional sample-mm coords, provenance.
- `detect_live_microscope_electrode_map_rgb(image_rgb, ...)` — the main
  "first look at this sample" detector; permissive preset tuned from a
  Swift-camera snapshot sweep; returns a `pandas.DataFrame` of
  `CircleDetection`s.
- `refine_circular_electrode_map_rgb(image_rgb, seed_detections, ...)` — light
  per-circle local re-search around already-known positions for simple
  frame-to-frame tracking. Explicitly does **not** do a global re-fit — that's
  `electrode_drift.py`'s job.
- `annotate_detections` — draws detection/occlusion-aware overlay circles +
  row/col labels.
- `filter_live_overlay_detections` — reduces raw noisy detections to a
  conservative subset for GUI overlay display.

Imports only `cv2`/`numpy`/`pandas` (no other `vision/*` module). Imported by
`gui.py` and `tools/live_electrode_overlay_view.py`.

## Layout, alignment, and drift

### `vision/layout_alignment.py`

Pure geometry — the shared data model everything else in this section builds
on.

- `Circle` (dataclass) — one tracked electrode: raw + EMA-smoothed pixel
  position, `confidence`/`detected` flags, `layout_index` (link back to the
  template), `on_image`. **`confidence` is not a calibrated score** — it's
  effectively boolean-in-disguise; the real trust signals are redetection
  count and RANSAC inlier fraction, both surfaced through
  `electrode_drift.py`'s `RefitResult`.
- `LayoutModel` — template positions (mm) + optional per-circle radii + the
  current template→pixel affine transform. `from_json` loads the schema
  `dxf_layout.py` produces.
- `fit_layout_affine(layout, image_points, layout_indices, ...)` — RANSAC
  6-DOF affine fit (independent x/y scale + shear + rotation + translation);
  falls back to LMEDS for tiny point sets. The one shared fitting routine
  used both by manual alignment and by `electrode_drift.py`'s continuous
  re-fit.
- `apply_manual_nudge` — applies a scale/rotation/shear/translation
  adjustment on top of a base transform, parameterized to match Tkinter
  `ttk.Scale` widget units directly.

Leaf module (`cv2`/`numpy`/`json`). Deliberately excludes the sibling
project's interactive `cv2.imshow` UI — that workflow is rebuilt as the
"Image Monitor" tab in `gui.py`, which calls these functions directly.

### `vision/electrode_drift.py`

Per-electrode ROI redetection plus global drift-correcting affine re-fit —
turns individual noisy per-circle detections into a trustworthy,
geometrically-consistent whole-layout position update.

- `RefitResult` (dataclass) — outcome of one drift-correction cycle: whether
  a re-fit ran, confident-electrode count, RANSAC inlier count/fraction,
  human-readable notes.
- `refit_and_extrapolate(layout, tracked, image_shape, min_confident=2)` —
  re-fits the affine transform using only circles marked `detected` this
  cycle, then hard-snaps every undetected circle to its freshly projected
  template position (mutates `tracked` in place; off-image extrapolated
  positions are flagged `on_image=False`, never clamped).
- `detect_and_refit_frame(frame, layout, tracked, ...)` — the main entry
  point: runs ROI redetection near every tracked electrode (skipping
  off-image or probe-occluded ones via `excluded_layout_indices`), optionally
  rejects detections that deviate too far from the prior-cycle projected
  position, then calls `refit_and_extrapolate`.

Deliberately stateless with respect to any live camera loop — callers own the
`tracked` list. Imported by `run_automation.py`'s headless `AutoTrackXY` and
`gui.py`'s Image Monitor tab, so both share identical drift-correction
behavior.

### `vision/dxf_layout.py`

Converts a CAD DXF file's circle entities into the layout JSON schema
`layout_alignment.LayoutModel.from_json` consumes — the origin of the
"template" electrode positions used throughout this pipeline.

- `extract_circles_from_dxf` — walks modelspace `CIRCLE` entities and
  `INSERT` block references, applying a `flip_y` correction for DXF's
  upward-Y vs. image's downward-Y convention.
- `warn_if_spacing_implausible` — nearest-neighbor spacing sanity check aimed
  at a known real-world failure mode (DXF reporting inches while the
  intended geometry was mm).

Only external dependency is `ezdxf` (optional —
`requirements-win7-optional-vision.txt`), the only module in `vision/` with
that dependency. Not imported by other `vision/*` modules — its output format
is a JSON contract, not a code import. Imported by `gui.py`'s Load DXF
Layout workflow.

### `vision/electrode_z_seed.py`

Persistent, disk-backed store of per-electrode Z-height "seeds" (contact-
measured heights) plus derived calibration fits — a pooled Z-parallax slope
and a tilted Z-surface plane fit — used to help the probe approach electrodes
at the right height. Distinct from `run_automation.py`'s in-memory,
per-run-only `contact_z_cache`. Largest file in `vision/` (~493 lines).

- `ZPlaneFit` — a `Z = a*u + b*v + c` plane fit over DXF template (u, v)
  coordinates from ≥3 electrodes' seeds. Deliberately fit in template space
  rather than stage-mm space: since both the layout→pixel and pixel→stage
  maps are affine, a template-space plane is equivalent to a stage-space
  plane without needing calibration solved first.
- `ElectrodeZCalibrationStore` — `get_seed`/`get_seed_or_estimate` (exact
  measurement, falling back to the plane estimate), `get_xy_bias(temperature_c)`
  (per-temperature-bucketed XY correction with nearest-bucket fallback),
  `record_contact(...)` (updates a seed, appends a parallax sample, triggers
  refits), `save`/`load` (tolerant JSON persistence — a corrupt/missing file
  yields an empty store rather than raising).

**This is the hand-off point from `vision/` to the root-level
`vision_stage_mapper.py`**: `electrode_z_seed.py` owns *accumulating and
persisting* raw contact/parallax/bias samples, while `vision_stage_mapper.py`
(see [`core_automation.md`](core_automation.md)) owns the actual
parallax-slope and XY-bias math, imported from here
(`ProbeXYBiasCalibration`, `ProbeZParallaxCalibration`,
`solve_parallax_slope_from_samples`, `solve_xy_bias_from_samples`). Layered
strictly on top of, and never overwrites, the manually-solved pixel↔stage
affine calibration those functions provide.

## Tuning GUIs (`vision/tuning_gui/`)

Interactive Tkinter parameter-tuning windows for the electrode
(`circle_detector`) and probe (`probe_detector`) detectors, launched
in-process from `gui.py`'s "Tune Circle Params" / "Tune Probe Params"
buttons.

**Why native Tkinter, not `cv2.imshow`/trackbars** (the sibling project's
original approach): this project intentionally runs
`opencv-python-headless` on the Windows 7 instrument PC, to avoid a
DLL-load-order conflict between Anaconda's MKL-linked numpy and a
GUI-capable OpenCV build. Headless wheels have no `cv2.imshow`/
`createTrackbar` backend at all. All the actual image-processing calls
(CLAHE, bilateral filter, Canny, HoughCircles, threshold, findContours,
convexHull, convexityDefects) are ordinary `cv2.imgproc` functions present in
headless builds — only *display* needed a Tkinter/PIL replacement, reusing
the same `ImageTk.PhotoImage` pipeline `gui.py`'s Image Monitor tab already
had. Because these run in-process as ordinary `Toplevel` windows (not
subprocess-isolated), there's no need to write a frame to disk just to hand
it to a child process — a snapshot is still saved to `vision_calibration/`
for record-keeping.

- **`vision/tuning_gui/stored_params.py`** — `load_params`/
  `save_tuning_params`, the `*_params.json` file I/O shared by both tuning
  windows and consumed by `vision/circle_detector.py::load_hough_params` and
  `vision/probe_detector.py::load_probe_params`.
- **`vision/tuning_gui/tuning_gui.py`** — `CircleTuningWindow`: 2×2 live
  preview (CLAHE, bilateral, Canny edges, Hough circles) with sliders for
  every preprocessing/Hough parameter.
- **`vision/tuning_gui/probe_detector.py`** — **a deliberate fork**, not an
  import, of production `vision/probe_detector.py`, specifically so the
  interactive tuner and the production detector can't accidentally diverge
  from under each other (per its own docstring — an intentional design
  choice, not an oversight). Also retains an unused "strategy C" (user-drawn
  probe-edge line intersection) that the production detector dropped
  entirely.
- **`vision/tuning_gui/probe_gui.py`** — `ProbeTuningWindow`: Tkinter-Canvas
  drag-based ROI selection driving this subpackage's own forked
  `ProbeDetector`. Only ROI selection + convexity-defect strategy are exposed
  in the UI.

Only file I/O (`stored_params.py`) and Tkinter/PIL preview helpers
(`_to_photo_image`/`_to_pil_image`, defined in `tuning_gui.py` and reused by
`probe_gui.py`) are shared between the two tuning windows and the production
detectors — everything image-processing-related is intentionally
independent.

## Dependency graph

```
vision/dxf_layout.py ──(writes layout JSON)──► vision/layout_alignment.py (LayoutModel.from_json)
                                                       │
                                                       ▼
                                         vision/electrode_drift.py (Circle, fit_layout_affine)

vision/circle_detector.py   (params loader, independent ROI-Hough impl)
vision/electrode_mapper.py  (independent cold-start whole-frame detector)
vision/probe_detector.py    (independent probe-tip detector)

vision/electrode_z_seed.py ──imports──► vision_stage_mapper.py (root level; see core_automation.md)
        (sample accumulation + persistence + Z-plane fit; math lives in vision_stage_mapper.py)

gui.py / run_automation.py ── import nearly every vision/* module directly

vision/tuning_gui/{tuning_gui,probe_gui}.py ── Tkinter windows launched by gui.py buttons;
        deliberately NOT wired into the production detectors above (tuning_gui/probe_detector.py
        is a forked copy, not an import of vision/probe_detector.py)
```
