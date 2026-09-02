# -*- coding: utf-8 -*-
"""
Per-electrode ROI redetection and drift-correcting layout re-fit.

Ported from the sibling "Image Detection" (Microelectrode Tracker) project's
``circle_detector.py`` (``detect_circle_in_roi``, ``ElectrodeTracker``) and
``preprocessing.py``. The tight-ROI-search + "re-fit the affine transform
from only the confidently-redetected electrodes, then hard-snap every other
electrode to the freshly projected geometry" pattern
(``ElectrodeTracker._realign_and_extrapolate``) is the core new capability
this module provides -- Microprobe Python's existing
``vision/electrode_mapper.py::refine_circular_electrode_map_rgb`` only does
local per-circle re-search with no analogous global re-fit/extrapolation
step.

Unlike the source project's ``ElectrodeTracker`` class, the functions here
are stateless with respect to any live camera loop: callers own the
``tracked`` list and call ``detect_and_refit_frame`` once per drift-check
cycle. ``run_automation.py``'s headless ``AutoTrackXY`` calls it every few
CSV rows; ``gui.py``'s Image Monitor tab calls it every camera tick once a
tracking seed exists (the per-electrode ROI search is cheap enough that no
extra throttling is needed there, unlike the whole-frame Hough sweep used
before a seed exists).

NOTE on confidence: ``detect_circle_in_roi`` sets ``Circle.confidence`` to a
hardcoded ``1.0`` whenever Hough finds anything at all in the ROI -- it is a
detected/not-detected flag, not a real accumulator score. The only
meaningful headless trust signal after a refit is the number of confidently
redetected electrodes and the RANSAC inlier fraction from
``vision.layout_alignment.fit_layout_affine``, both surfaced on
``RefitResult`` below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

import cv2
import numpy as np

from vision.layout_alignment import Circle, LayoutModel, fit_layout_affine


def _preprocess(
    gray: np.ndarray,
    *,
    clahe_clip: float = 2.0,
    clahe_tile: Tuple[int, int] = (8, 8),
    bilateral_d: int = 9,
    bilateral_sigma: float = 75.0,
) -> np.ndarray:
    """
    Normalize uneven lighting and reduce noise while preserving edges.

    CLAHE (Contrast Limited Adaptive Histogram Equalization) corrects local
    contrast variation common in microscope images. Bilateral filtering
    smooths noise without blurring circle edges.
    """
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=clahe_tile)
    enhanced = clahe.apply(gray)
    return cv2.bilateralFilter(enhanced, bilateral_d, bilateral_sigma, bilateral_sigma)


def detect_circle_in_roi(
    processed: np.ndarray,
    center: Tuple[float, float],
    expected_radius: int,
    search_margin: int = 40,
    radius_tolerance: int = 6,
    param1: int = 50,
    param2: int = 20,
) -> Optional[Circle]:
    """
    Search for a single circle inside a small ROI around an expected
    position.

    ROI size is automatically expanded to always contain the full circle
    plus the search margin, so Hough always sees complete arcs. minDist is
    clamped to stay well within the ROI so OpenCV never silently discards
    all candidates.
    """
    ex, ey = int(center[0]), int(center[1])
    h, w = processed.shape[:2]

    # The ROI must be large enough to contain the entire circle (radius +
    # tolerance) plus the positional search margin on each side.
    half = expected_radius + radius_tolerance + search_margin
    x1 = max(0, ex - half)
    y1 = max(0, ey - half)
    x2 = min(w, ex + half)
    y2 = min(h, ey + half)

    roi = processed[y1:y2, x1:x2]
    roi_w = x2 - x1
    roi_h = y2 - y1
    if roi.size == 0 or roi_w < 3 or roi_h < 3:
        return None

    # minDist must be strictly less than the smallest ROI dimension,
    # otherwise OpenCV returns nothing even if a circle is found.
    max_min_dist = min(roi_w, roi_h) - 1
    min_dist = min(max(1, int(expected_radius * 1.5)), max_min_dist)

    raw = cv2.HoughCircles(
        roi,
        cv2.HOUGH_GRADIENT,
        dp=1,
        minDist=min_dist,
        param1=param1,
        param2=param2,
        minRadius=max(1, expected_radius - radius_tolerance),
        maxRadius=expected_radius + radius_tolerance,
    )

    if raw is None:
        return None

    # Take the best (first) result and convert ROI coords -> image coords.
    # Keep sub-pixel precision (do not round to int) so downstream x/y/r
    # values are not all whole numbers.
    cx, cy, r = raw[0][0]
    return Circle(x=float(cx) + x1, y=float(cy) + y1, radius=float(r), confidence=1.0, detected=True)


@dataclass
class RefitResult:
    """Outcome of one drift-correction cycle."""

    refit_performed: bool
    confident_count: int
    inlier_count: Optional[int] = None
    inlier_fraction: Optional[float] = None
    notes: List[str] = field(default_factory=list)


def refit_and_extrapolate(
    layout: LayoutModel,
    tracked: List[Circle],
    image_shape: Tuple[int, int],
    *,
    min_confident: int = 2,
) -> RefitResult:
    """
    Re-fit the layout transform using ONLY the circles already marked
    ``detected`` this cycle (matched to their known ``layout_index``), then
    move every undetected circle to its freshly extrapolated position.

    ``tracked`` is mutated in place. Off-image extrapolated positions are
    kept as-is (never clamped) and flagged ``on_image=False`` so callers can
    skip detection for them next cycle.

    ``min_confident`` is a library-level floor (the source project used the
    bare geometric minimum of 2); callers doing unattended/headless
    correction should pass a stricter, config-driven value (a handful of
    points gives RANSAC no redundancy to reject a single bad detection).
    """
    h, w = image_shape

    det_pts: List[List[float]] = []
    det_idx: List[int] = []
    for c in tracked:
        if c.detected and c.layout_index is not None:
            det_pts.append([c.smoothed_x, c.smoothed_y])
            det_idx.append(c.layout_index)

    confident_count = len(det_pts)
    if confident_count < max(2, min_confident):
        return RefitResult(
            refit_performed=False,
            confident_count=confident_count,
            notes=[
                f"only {confident_count} confidently redetected electrode(s) "
                f"(< {min_confident} required); keeping prior transform/positions"
            ],
        )

    fit_result = fit_layout_affine(
        layout, det_pts, det_idx, ransac_reproj_threshold=5.0
    )
    if fit_result is None:
        return RefitResult(
            refit_performed=False,
            confident_count=confident_count,
            notes=["affine re-fit failed; keeping prior transform/positions"],
        )
    _matrix, inlier_mask = fit_result
    inlier_count = int(np.sum(inlier_mask)) if inlier_mask is not None else None
    inlier_fraction = (inlier_count / confident_count) if inlier_count is not None and confident_count > 0 else None

    if layout.transform is None:
        return RefitResult(
            refit_performed=False,
            confident_count=confident_count,
            inlier_count=inlier_count,
            inlier_fraction=inlier_fraction,
            notes=["fit succeeded but layout.transform is unexpectedly unset"],
        )

    projected = layout.project_all()  # (N, 2), never clamped

    for c in tracked:
        if c.layout_index is None or c.layout_index >= len(projected):
            continue
        if c.detected:
            continue  # keep the measured position
        px, py = projected[c.layout_index]
        # Hard-set (not EMA) so extrapolation snaps to the true geometry.
        c.x = c.smoothed_x = float(px)
        c.y = c.smoothed_y = float(py)
        c.on_image = (0 <= px < w) and (0 <= py < h)

    return RefitResult(
        refit_performed=True,
        confident_count=confident_count,
        inlier_count=inlier_count,
        inlier_fraction=inlier_fraction,
        notes=[f"re-fit from {confident_count} confidently redetected electrode(s)"],
    )


def detect_and_refit_frame(
    frame: np.ndarray,
    layout: LayoutModel,
    tracked: List[Circle],
    *,
    min_confident: int = 2,
    search_margin: int = 40,
    param1: int = 50,
    param2_roi: int = 20,
    clahe_clip: float = 2.0,
    clahe_tile: int = 8,
    bilateral_d: int = 9,
    bilateral_sigma: float = 75.0,
    ema_alpha: float = 0.3,
    max_projected_deviation_radii: Optional[float] = None,
    excluded_layout_indices: Optional[Set[int]] = None,
) -> RefitResult:
    """
    Run one headless drift-correction cycle: ROI-search near every tracked
    electrode's last known position, then re-fit and extrapolate.

    Electrodes already flagged ``on_image=False``, or whose ``layout_index``
    is in ``excluded_layout_indices`` (e.g. currently occluded by the probe
    arm -- callers compute this set, this module has no notion of the
    probe), are skipped entirely this cycle, matching the source project's
    on-image behavior: no search is attempted, so they fall through to
    ``refit_and_extrapolate``'s existing snap-to-projected-position handling
    for undetected circles.

    ``max_projected_deviation_radii``, if given, rejects a fresh detection
    that lands farther than that many electrode-radii from the position
    ``layout.project_all()`` gives *before* this cycle's own re-fit runs
    (i.e. the last cycle's trusted transform -- ``layout.transform`` is only
    overwritten on a successful re-fit, so this is never circular). A
    rejected detection is treated exactly like a failed one: excluded from
    this cycle's affine re-fit input and left for extrapolation to correct,
    instead of being allowed to pull the tracked position (and possibly the
    re-fit itself) toward a wrong nearby feature.

    ``tracked`` is mutated in place; the returned ``RefitResult`` reports
    whether the re-fit was trusted enough to run.
    """
    h, w = frame.shape[:2]
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    processed = _preprocess(
        gray,
        clahe_clip=clahe_clip,
        clahe_tile=(clahe_tile, clahe_tile),
        bilateral_d=bilateral_d,
        bilateral_sigma=bilateral_sigma,
    )

    prior_projected = None
    if max_projected_deviation_radii is not None:
        try:
            prior_projected = layout.project_all()
        except Exception:
            prior_projected = None

    for prev in tracked:
        if not prev.on_image or (
            excluded_layout_indices and prev.layout_index in excluded_layout_indices
        ):
            prev.detected = False
            prev.confidence = 0.0
            continue

        r_prev = int(prev.radius)
        refine_tol = max(3, int(round(r_prev * 0.25)))
        refine_margin = min(search_margin, max(6, r_prev // 2))
        c = detect_circle_in_roi(
            processed,
            (prev.smoothed_x, prev.smoothed_y),
            r_prev,
            search_margin=refine_margin,
            radius_tolerance=refine_tol,
            param1=param1,
            param2=param2_roi,
        )

        accept = c is not None
        if (
            accept
            and prior_projected is not None
            and prev.layout_index is not None
            and prev.layout_index < len(prior_projected)
        ):
            px, py = prior_projected[prev.layout_index]
            deviation = ((c.x - px) ** 2 + (c.y - py) ** 2) ** 0.5
            if deviation > max_projected_deviation_radii * max(r_prev, 1):
                accept = False

        if accept:
            prev.update_smoothed(c.x, c.y, ema_alpha)
            prev.detected = True
            prev.confidence = c.confidence
        else:
            prev.detected = False
            prev.confidence = 0.0

    return refit_and_extrapolate(layout, tracked, (h, w), min_confident=min_confident)
