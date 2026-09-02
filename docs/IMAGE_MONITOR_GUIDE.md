# Image Monitor Tab — User Guide

The "Image Monitor" tab in the GUI is the camera/vision interface: live
electrode detection, probe-tip detection, calibration between camera pixels
and stage millimeters, locking a target electrode, and moving the stage to
it with a post-move visual sanity check.

There is a single workflow with two required parts, plus an optional third
part for higher-precision targeting:

- **Pixel-probe calibration.** Calibrates pixel↔stage mapping directly from
  the probe's own physical position — no reference image needed.
- **DXF-layout electrode alignment.** Seeds every electrode's position from
  a CAD file, locked to what the camera actually sees via a few
  manually-identified reference points. Accepting an alignment also seeds
  live electrode tracking, so the camera keeps following electrodes as the
  stage/view drifts.
- **Electrode Z-seed calibration** *(optional, recommended once you've
  validated the basics)*. Corrects a small but real source of targeting
  error: the camera isn't perfectly aligned with the stage's Z-travel axis,
  so a physical point's apparent pixel position shifts slightly depending
  on its height. This uses each electrode's own real, contact-measured
  height to correct for that — see section 5.

---

## 1. Start the camera

1. Click **Start Camera**. The live feed appears at the bottom of the tab
   (scroll down if you don't see it — the tab is scrollable). The camera
   backend and index are fixed (auto-detect backend, first camera) — there's
   nothing to configure. The electrode detector uses a built-in live-camera
   preset by default; see section 3 for retuning it via **Load Hough Params**.
2. Click **Stop Camera** when done. Always stop the camera before launching
   an unattended `run_automation.py` run that uses `AutoTrackXY=1` rows —
   the camera can't be open in the GUI and a headless run at the same time
   (see section 6 for the distinction between this and GUI-driven runs).

---

## 2. Pixel-probe calibration

This directly calibrates "camera pixel → stage mm" using the probe itself
as the reference object — no intermediate estimate involved.

1. Physically move the probe to a stage position that's clearly visible in
   the camera view, using the **Stage** row at the top of this tab (target
   X/Y/Z entries + **Move Tip**, plus a live current-position readout +
   **Refresh Current State**) — this is the same state as the Manual
   Control tab, so there's no need to switch tabs during calibration.
   **Move Tip** is Z-safe — it lifts, translates, then lowers, and is the
   single move path for both a plain jog and driving to a locked target
   (see §4 step 2).
   **Keep Z the same across every point in this calibration** (see why
   below) — pick one comfortable height and don't change it until you're
   done capturing points and have solved the affine (step 5 below).
2. Click **Select Probe ROI**, then click-and-drag a rectangle on the live
   view around just the probe tip and (if visible) one nearby electrode —
   an orange box shows the dragged region. **This matters**: probe-tip
   detection looks for the single largest bright/dark region in whatever
   area it searches, so with multiple electrodes in frame it can lock onto
   the wrong one; constraining the search to a small region keeps it
   reliable. The ROI persists across every point you capture below until
   you click **Clear Probe ROI** or stop the camera — you don't need to
   redraw it for each point unless the probe moves well outside it.
3. Back in Image Monitor, click **Capture Probe Cal Point**. This detects
   the probe tip in the current frame (inside the ROI, if one is set) and,
   if found, immediately pairs it with the stage's *current* X/Y/Z (read
   from Manual Control, so make sure that readback is fresh/refreshed
   before clicking) — one click does both. A crosshair appears on the live
   view at the detected tip location, and the status line shows the pixel
   coordinates and how many points have been captured so far. If it says
   "not detected," see the Troubleshooting section below. (**Detect Probe
   Tip** is still available separately if you just want to preview
   detection — e.g. to check lighting/contrast — before committing a
   point.)
4. Repeat steps 1–3 at **at least 3** well-separated stage positions
   (spread across the area you'll actually be working in, not clustered
   together — this matters for calibration accuracy). Only vary X/Y between
   points, not Z.
5. Click **Solve Probe Affine**. The status line reports how many points
   were used and confirms the solve succeeded — it also reports the
   reference Z used. **Why Z must be consistent**: the camera isn't
   perfectly aligned with the stage's Z-travel axis, so this calibration is
   only exactly valid at whatever Z it was captured at. If your points'
   Z values varied by more than `config.VISION_PROBE_CALIBRATION_Z_TOLERANCE_MM`
   (a few hundredths of a mm by default), the solve is refused and the
   status line reports the spread — recapture all the points at a
   consistent height and try again.
6. Click **Save Probe Cal** to persist it to a file — this is also the file
   `run_automation.py`'s `AutoTrackXY` headless drift correction will load
   later, so don't skip this step if you plan to use unattended tracking.
   The default filename matches `config.VISION_PIXEL_STAGE_CALIBRATION_PATH`.
7. **Load Probe Cal** reloads a previously saved calibration in a later
   session instead of repeating steps 1–5.

You can redo this at any point (e.g., if you suspect drift) — just repeat
from step 1; the reference points accumulate until you click **Clear Probe
Cal** to start over.

If your electrodes aren't all at the same height as this calibration (a
tilted or uneven sample), targeting accuracy will have a small systematic
bias until you set up electrode Z-seed calibration (section 5), which
corrects for that using each electrode's own measured height.

---

## 3. DXF-layout electrode alignment

This seeds every electrode's position from a CAD file, then locks it to
what the camera actually sees via a few manually-identified reference
points.

1. Next to **DXF layout**, click **Browse** and select your sample's DXF
   file, then **Load DXF Layout**. The status line reports how many
   electrodes were extracted. (If this fails because `ezdxf` isn't
   installed, see `requirements-win7-optional-vision.txt`.) This also
   auto-saves the extracted layout to `config.VISION_ELECTRODE_LAYOUT_PATH`
   — no separate save step needed. `run_automation.py`'s headless
   `AutoTrackXY` drift correction reads that file, so keep this step in the
   loop even if you're only using the GUI for calibration today.
2. Optionally, click **Load Hough Params** to load a `*_params.json` file
   tuned via the sibling "Image Detection" project's standalone tuning GUI
   (CLAHE/bilateral-filter preprocessing + Hough circle-search parameters).
   If skipped, generic defaults from `config.py`'s `VISION_CIRCLE_HOUGH_*`
   constants are used instead — reasonable for typical electrode sizes but
   not tuned to any specific sample. Loading a file here also retunes the
   live electrode detector used for tracking (section 2 and throughout) —
   one load retunes both, so if the built-in live-camera defaults are
   missing or over-detecting electrodes on your sample, tune a params file
   in that sibling project and load it here rather than trying to fix it
   in-app.
3. Click **Draw Electrode Circles** to enter draw mode (the button doesn't
   change appearance — check the status line to confirm draw mode is on).
4. Click directly on the live camera view at each electrode you can clearly
   identify — at least 3, well-spread across the visible area. Each click
   runs a small Hough circle search in a region around the click and snaps
   to the detected electrode edge (falling back to the raw click point if
   no circle is found there), then marks the result with a green marker.
   Use **Undo Last Circle** to remove a mis-click, or **Clear Circles** to
   start over.
5. Click **Draw Electrode Circles** again to exit draw mode.
6. For each marked circle, pair it with its corresponding electrode from the
   loaded layout: type the drawn circle's number (shown in the overlay
   label, `#0`, `#1`, ...) into the **Pair: drawn circle #** box, pick the
   matching electrode from the **layout electrode #** dropdown, and click
   **Confirm Pair**. Repeat for every circle you drew. **Undo Last Pair**
   removes the most recent pairing if you made a mistake.
7. Once you have at least 3 pairs, click **Fit Transform**. This computes
   an affine transform from the layout's physical geometry into camera
   pixels, and overlays every electrode from the layout (orange circles) —
   including ones outside the current view — at their projected positions.
8. Check the overlay against reality. If it's slightly off, use the
   **Nudge** sliders (scale / angle / tx / ty) to fine-tune it live — the
   orange overlay updates as you drag.
9. When it looks right, click **Accept Alignment**. This also captures every
   electrode's projected pixel position as the seed for live electrode
   tracking — from this point on, every camera tick re-searches a small
   region around each electrode's last known position for its circle
   (`vision/electrode_drift.py::detect_and_refit_frame`, the same mechanism
   `run_automation.py`'s headless `AutoTrackXY` uses), then re-fits the
   layout's affine transform from whichever electrodes were confidently
   found this tick and projects the rest from that fresh fit. This tracks
   each electrode independently by its own local appearance, so something
   temporarily covering part of the view (e.g. the probe arm swinging over
   the sample) only affects the electrodes it actually covers, not the
   whole frame's alignment.
10. Click **Save Alignment** to persist the fitted transform and every
    electrode's projected pixel position — this is the second file
    `run_automation.py`'s `AutoTrackXY` will load. **Load Alignment** reloads
    a previously saved one (note: loading an alignment does not by itself
    recreate the live-tracking seed — re-click **Accept Alignment** with the
    camera running if you want tracking seeded from the current session).

### 3.1 Generating a condition CSV from these seeded electrodes

Once alignment is accepted (above) and at least one electrode has a Z seed
(section 5), you can build a condition CSV directly from this tracked
layout instead of manually typing X1/Y1/XN/YN and interpolating:

1. In the **Semi-auto** or **Full-auto** tab, find the **Tip Position
   Source** box next to **Disable Unused Hardware** and select **Image
   Monitor seeded electrodes** instead of the default **Manual range**.
   This grays out the electrode-range and X/Y/Z fields (they're not used in
   this mode) and forces `AutoContactZ=1` on — Z-seeding is required, not
   optional, for this generator, since every electrode's position and
   height both come from having actually touched it (or from the Z-surface
   plane estimate, once 3+ others have been touched).
2. Click **Generate to CSV List** (or **Append**) as usual. One row is
   generated per electrode that currently has a Z seed — electrodes you
   haven't touched yet, and that the Z-surface plane can't estimate either,
   are silently skipped. If *no* electrode has a seed yet, generation fails
   with a clear error instead of producing an empty/nonsensical CSV.
3. The X/Y/Z written into the CSV are today's best estimate (tracked pixel
   position, parallax- and bias-corrected) — but they're only a *starting*
   value. A GUI-driven run re-corrects each row's X/Y live at move time from
   whatever the Image Monitor is tracking at that moment (section 6.1), so
   the CSV doesn't need to be regenerated just because the sample drifted
   between generating it and running it.

---

## 4. Locking a target and moving there

The target-lock controls live in the **Camera & Stage** box (top-left),
alongside the camera and stage-position controls, since locking a target
fills in the same "Move to:" fields those share with Manual Control.

Once electrodes are visible in the live overlay (from live auto-detection,
a frozen seed, or DXF-layout-seeded tracking above):

1. **Click an electrode directly in the live camera view** to lock it as
   the current target. The status line confirms the lock and, if pixel-probe
   calibration is solved, shows the projected stage X/Y. This also fills in
   the "Move to:" X/Y (if pixel-probe calibration is solved) and Z (if this
   is a DXF-layout electrode with a known Z seed or Z-surface estimate)
   fields as an aide, so you can see where the tip is about to go before
   moving — best-effort, whatever it can't determine is left unchanged.
2. Click **Move Tip**. This drives to whatever is currently in the "Move
   to:" fields, using a guarded move (`motor.move_xyz_safe`) — **this move
   is already Z-safe**: it lifts Z to a clearance height first (0.5 mm by
   default — `config.STAGE_SAFE_MOVE['lift_delta_mm']`), then translates
   X/Y, then returns Z to the target, so the tip never drags across the
   sample. **Move Tip** is the single move path for both a plain jog and
   driving to a locked target — there's no separate "safe move" button.
   If a target is locked, the move only counts as having reached it when
   the driven X/Y still matches that target's (freshly re-derived) position;
   if you've edited the "Move to:" fields away from the locked target since
   clicking it, the lock clears automatically instead of requiring a
   separate "clear target" step.

Other useful buttons in this area:
- **Freeze Current Frame** (under Start/Stop Camera) — manually lock the
  current live frame + overlay as the tracking reference, without waiting
  for a move.
- **Clear Frame** — drop the frozen seed and fall back to plain live
  detection (or DXF-layout-seeded tracking, if an alignment was accepted).

---

## 5. Electrode Z-seed calibration (optional for manual runs, required for the Image Monitor CSV generator)

This corrects the small targeting bias described in section 2: if an
electrode's real height differs from the pixel-probe calibration's
reference Z (an uneven sample, or a calibration done at a different height
than the electrodes), the projected stage XY for that electrode is
slightly off. Fixing it needs two things per electrode: its *real* height
(from a Z-contact search), and a *parallax slope* (how many pixels of
apparent shift correspond to one mm of height difference). Both come from
the rig's own existing contact-search tooling — there's no separate
calibration procedure to learn.

**How the parallax slope is measured**: a Z-contact search moves only Z —
XY never changes during the search. So the probe tip's own detected pixel
position at the start of the search versus at the moment contact is
confirmed differs *only* because of the Z change. That displacement is a
direct measurement of the parallax slope, captured automatically every
time a contact search runs (no extra steps). At least 3 such measurements
(from 3 separate contact searches, typically at different electrodes) are
needed before the slope is trusted; it keeps refitting and improving as
more accumulate over a run. This probe-tip sampling uses the same **Probe
ROI** set up in section 2 (if any) — set it once before seeding and it
applies to every contact search's probe detection here too.

Seed an electrode height with **Search Z**, in the **Z Contact Search** box
(which also holds the contact-search parameters it uses — start offset,
step, max beyond seed, OCV threshold, settle time, engage extra, target XY
tolerance — and whose action button sits to the right of those fields).
While a search is running, the button turns gold and the box shows
"Searching..." plus a live-updating OCV line.

Lock a target electrode (click it in the live view; this also fills the
"Move to:" fields in the Camera & Stage box, see section 4), click
**Move Tip** to drive there, then click **Search Z**. This runs its own
contact search starting from the stage's current Z (no need to run
**Find Contact Z** on the Manual Control tab first — that would just search
twice). Records that electrode's height and, if a parallax sample was
captured during the search, contributes it to the slope fit. **You must
actually click Move Tip first** — the search runs at whatever XY the stage
is currently at, so seeding without moving there first would measure the
wrong electrode. The Z-seed status line always shows whether the currently
locked target is ready to seed (locked only vs. locked *and* moved to), and
**Search Z** refuses to run otherwise.

The Z-seed status line reports each seed as it's recorded and whether the
parallax slope was (re)fit. Once fitted, targeting a *previously-seeded*
electrode automatically applies the correction — the "Move to:" fields
filled in when locking a target, and the target-status projection, both use
it transparently, no extra action needed.

**Electrodes you haven't individually seeded still get corrected, once 3+
others have been.** Every seed also records that electrode's physical
position in the loaded DXF layout, and once 3 or more electrodes (that
still have a layout loaded) have a seed, the store fits a tilted-plane
model of Z across the whole layout — the same idea as fitting a flat plane
through any uneven sample's surface. Targeting an electrode that hasn't
been individually contact-measured uses this plane's estimate at that
electrode's layout position instead of falling back to an uncorrected
projection. An electrode's *own* measurement always wins over the plane's
estimate if it has one. The Z-seed status line notes "Z-surface fit from N
electrode(s)" once the plane is active. Seeding more electrodes (especially
ones spread across the sample, not clustered) keeps refitting the plane and
improving its accuracy for everywhere else.

This is saved to `config.VISION_ELECTRODE_Z_SEED_PATH` and, like the other
calibration artifacts, persists across sessions — `run_automation.py`
loads it automatically and keeps updating it as `AutoContactZ` rows
complete their own contact searches during a run, so accuracy improves
automatically over the course of a run without any operator action.

**A second, independent correction is recorded at the same time: XY bias.**
The pixel<->stage calibration (section 2) is fit from *probe*-tip
pixel<->stage correspondences, but it also gets reused to project
*electrode*-tracked pixels — which implicitly assumes the probe and an
electrode look identical in pixel space when co-located. The moment
`AutoContactZ` confirms contact, the probe is physically at the electrode's
true stage XY, so a tight ROI probe search — sized to that electrode's own
tracked radius, not the general-purpose **Probe ROI** from section 2 — gives
a fresh, trustworthy comparison between where the probe is actually
detected and where the calibration predicts it should be for that achieved
position. Like the parallax slope, this needs 3+ samples before it's
trusted, keeps refitting as more accumulate, and is layered on top of the
pixel<->stage calibration without ever overwriting it — so a bad run can't
corrupt the base calibration itself. No separate setup is needed: it's
recorded automatically on every `AutoContactZ` touch, alongside the Z seed
and parallax sample, whenever an electrode identity and a live frame are
both available.

---

## 6. Automated runs: GUI-driven vs. headless `run_automation.py`

There are **two separate ways to run an automated sequence**, and they support
different vision corrections and have opposite requirements for the camera.
Both need the probe calibration and electrode alignment (sections 2–3) saved
first.

| | GUI-driven run | Headless `run_automation.py` |
|---|---|---|
| Where | **Full-auto**/**Semi-auto** tab (build rows) → **CSV List** tab → **Run / Monitor** tab (execute) | Separate script, run from a terminal |
| Vision correction | `AutoContactZ` (electrode Z-seed + parallax + XY bias), **and** live-tracked XY positioning for any row whose Label matches a tracked layout electrode (no separate column needed — see 6.1) | `AutoContactZ` **and** `AutoTrackXY` (a column-gated XY drift correction, mirroring the GUI's live-tracked positioning) |
| Camera state required | **Leave the Image Monitor camera running** — both `AutoContactZ`'s probe-tip sampling and live-tracked XY positioning reuse its live frames/tracking. Still works with the camera off, just without that refinement (falls back to each row's static CSV X/Y). | **Camera must be closed** (Stop Camera, or close the GUI) — the script opens its own capture and can't share the device with the GUI. |

### 6.1 GUI-driven runs

1. Build your conditions in the **Full-auto** or **Semi-auto** tab as usual
   (or generate them directly from seeded electrodes, section 3.1); set
   `AutoContactZ=1` on rows that should get a Z-contact search (this also
   seeds/refines the electrode Z-seed, parallax, and XY-bias stores from
   section 5).
2. Review/adjust in the **CSV List** tab if needed.
3. Go to the **Run / Monitor** tab and start the run. **Keep the Image
   Monitor tab's camera running throughout** — this is what powers two
   things during the run, not just `AutoContactZ`'s probe-tip sampling:
   - **Live-tracked XY positioning**: for any row whose `Label` contains an
     `E<N>` that matches a currently DXF-layout-tracked electrode, the run
     moves to that electrode's *live* tracked position (parallax- and
     bias-corrected) instead of the row's static CSV X/Y — this happens
     automatically, there's no `AutoTrackXY=1` column to set for the GUI
     path. A correction is only trusted if it's within
     `config.VISION_DRIFT_MAX_CORRECTION_MM` of the *last position this run
     itself trusted* for that electrode (or the row's own CSV position, on
     that electrode's first touch this run) — so a single bad detection
     falls back to the CSV value, but slow, genuine drift keeps being
     followed for the whole run instead of eventually exceeding a fixed
     original bound. The **Run / Monitor** log reports which source (CSV or
     live-tracked) was used for every row.
   - **`AutoContactZ`** as before, now also recording an XY-bias sample on
     every touch (section 5).
   
   Both degrade gracefully (fall back to the row's plain CSV values, not a
   failure) if the camera is off or tracking isn't active.
4. This path does not read the `AutoTrackXY` CSV column at all (that's
   headless-only, 6.2) — GUI-driven live-tracked positioning is
   Label-identity-driven, not column-gated.

### 6.2 Headless `run_automation.py` (`AutoTrackXY`)

`run_automation.py` can correct for XY drift *during* an unattended
overnight run, with no operator watching:

1. In `config.py`, set `VISION_AUTO_TRACK_XY_ENABLED = True` (it's `False`
   by default until you've validated the workflow).
2. In your conditions CSV, set `AutoTrackXY=1` on the rows you want
   corrected. The row's electrode is identified from its `Label` (must
   contain `E<N>`, e.g. `E3`) — the correction only applies if that number
   matches an electrode in your saved layout.
3. **Close the GUI's camera** (Stop Camera, or close the GUI entirely)
   before launching the run — the headless run needs the camera itself and
   can't share it with the GUI.
4. Run `run_automation.py` as usual. The log will show `AutoTrackXY: ...`
   lines reporting whether a drift-correction cycle ran, whether it was
   trusted, and whether each row's position was corrected or fell back to
   the CSV value. Like the GUI's live-tracked positioning (6.1), a
   correction is only trusted if it's within
   `config.VISION_DRIFT_MAX_CORRECTION_MM` of the *last position this run
   itself trusted* for that electrode (or the CSV value, on that
   electrode's first touch this run) — so slow, genuine drift keeps being
   followed for the whole run. `AutoContactZ` rows are also honored here,
   the same as the GUI-driven path.

Full details on cadence, thresholds, and the fallback rules are documented
in `run_automation.py`'s module docstring and in `config.py`'s `# ── Vision
/ camera ──` section.

---

## 7. Troubleshooting

- **"Probe: not detected"** — the probe-tip detector needs reasonably
  strong, clean contrast between the probe and whatever's behind it
  (electrode or bare substrate). Try adjusting lighting, or reposition the
  probe somewhere with clearer contrast before retrying.
- **A drawn/detected circle overlay looks correct but the projected stage
  XY is way off** — double check you added calibration points at
  *well-separated* stage positions (not clustered near each other); a
  cramped set of calibration points produces an unstable affine fit.
- **"Probe calibration failed: Z varied by ... mm across points"** — you
  moved Z between calibration points (section 2). Recapture all the points
  at one consistent height.
- **Electrode Z-seed correction doesn't seem to be applying** — check the
  Z-seed status line: it needs *both* a fitted parallax slope (3+ samples)
  *and* a seed specifically for the electrode you're targeting. A slope fit
  from other electrodes doesn't help an unseeded one until it gets its own
  contact measurement.
- **"Vision/OM tracking is unavailable..."** — a vision dependency failed
  to import. Check `requirements-win7-optional-vision.txt` and the
  Windows-7-specific notes in it (`opencv-python` DLL/version issues are
  the most common cause).
- **Buttons cut off on the right of a row** — the tab is vertically
  scrollable, but individual rows don't wrap; if a specific row is too wide
  for your screen, let the maintainer know which one.
