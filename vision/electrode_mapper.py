# -*- coding: utf-8 -*-
"""
Electrode-map detection helpers for the live Image Monitor camera pipeline.

This module detects circular electrode candidates in a camera frame (a
single permissive preset tuned for live-camera views, tunable via
vision/circle_detector.py::load_hough_params), converts pixel positions into
approximate sample-mm coordinates, and refines previously-known circle
positions against a new frame for lightweight per-electrode tracking. Global
frame-to-frame drift correction (re-fitting from confidently-redetected
electrodes) lives in vision/electrode_drift.py, used by both
run_automation.py's headless AutoTrackXY and the GUI's Image Monitor tab.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
import pandas as pd


@dataclass
class CircleDetection:
    x_px: float
    y_px: float
    radius_px: float
    row_index: int
    col_index: int
    ordinal: int
    size_group: str
    sample_x_mm: Optional[float] = None
    sample_y_mm: Optional[float] = None
    projected: bool = False
    source: str = "opencv_hough"

    def to_dict(self) -> dict:
        return asdict(self)


def _to_gray(image_rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)


def enhance_live_microscope_contrast(
    image_rgb: np.ndarray,
    *,
    clahe_clip: float = 2.0,
    clahe_tile: int = 8,
) -> np.ndarray:
    gray = _to_gray(image_rgb)
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(clahe_tile, clahe_tile))
    gray_eq = clahe.apply(gray)
    return cv2.cvtColor(gray_eq, cv2.COLOR_GRAY2RGB)


def estimate_sample_bounds(gray: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """
    Estimate the sample square/rectangle bounds from a mostly bright image.

    This is intentionally simple and works best when the sample is clearly
    brighter or darker than the surrounding background.
    """
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(
        blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    # Try both polarities and keep the largest plausible rectangular contour.
    candidates = []
    for binary in (thresh, cv2.bitwise_not(thresh)):
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < gray.shape[0] * gray.shape[1] * 0.05:
                continue
            aspect = w / max(h, 1)
            if 0.6 <= aspect <= 1.8:
                candidates.append((area, x, y, w, h))

    if not candidates:
        return None
    _, x, y, w, h = max(candidates, key=lambda item: item[0])
    return x, y, w, h


def detect_circular_features(
    image_rgb: np.ndarray,
    *,
    min_radius_px: int = 3,
    max_radius_px: Optional[int] = None,
    min_dist_px: Optional[float] = None,
    hough_param1: float = 100.0,
    hough_param2: float = 15.0,
) -> List[Tuple[float, float, float, str]]:
    gray = _to_gray(image_rgb)
    if max_radius_px is None:
        max_radius_px = max(8, min(gray.shape[:2]) // 8)
    if min_dist_px is None:
        min_dist_px = max(min_radius_px * 2.0, 10.0)

    blurred = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=min_dist_px,
        param1=hough_param1,
        param2=hough_param2,
        minRadius=min_radius_px,
        maxRadius=max_radius_px,
    )

    detections: List[Tuple[float, float, float, str]] = []
    if circles is not None:
        for x, y, r in circles[0]:
            detections.append((float(x), float(y), float(r), "opencv_hough"))

    return _deduplicate_circles(detections)


def _deduplicate_circles(
    circles: Sequence[Tuple[float, float, float, str]],
    *,
    center_tol_px: float = 6.0,
    radius_tol_px: float = 4.0,
) -> List[Tuple[float, float, float, str]]:
    if not circles:
        return []

    deduped: List[Tuple[float, float, float, str]] = []
    for candidate in sorted(circles, key=lambda item: (-item[2], item[1], item[0])):
        x, y, r, source = candidate
        keep = True
        for ex, ey, er, _ in deduped:
            if abs(x - ex) <= center_tol_px and abs(y - ey) <= center_tol_px and abs(r - er) <= radius_tol_px:
                keep = False
                break
        if keep:
            deduped.append(candidate)
    return sorted(deduped, key=lambda item: (item[1], item[0]))


def _assign_rows(values: Sequence[float], tolerance_px: float) -> List[int]:
    if not values:
        return []
    row_labels: List[int] = []
    row_centers: List[float] = []
    for value in values:
        assigned = False
        for idx, center in enumerate(row_centers):
            if abs(value - center) <= tolerance_px:
                row_labels.append(idx)
                row_centers[idx] = (center + value) / 2.0
                assigned = True
                break
        if not assigned:
            row_centers.append(float(value))
            row_labels.append(len(row_centers) - 1)
    return row_labels


def _assign_size_groups(radii: Sequence[float]) -> List[str]:
    if not radii:
        return []
    unique = sorted(set(round(r, 1) for r in radii))
    if len(unique) <= 1:
        return ["default"] * len(radii)

    median_r = float(np.median(radii))
    return ["large" if r >= median_r else "small" for r in radii]


def detect_live_microscope_electrode_map_rgb(
    image_rgb: np.ndarray,
    *,
    sample_side_mm: Optional[float] = 10.0,
    size_group: str = "all",
    clahe_clip: float = 2.0,
    clahe_tile: int = 8,
    min_radius_px: int = 8,
    max_radius_px: int = 40,
    min_dist_px: float = 50.0,
    param1: float = 100.0,
    param2: float = 15.0,
) -> pd.DataFrame:
    """
    Permissive detector for noisy/soft microscope-camera live views.

    Defaults are the original preset tuned from the Swift camera snapshot
    sweep on the lab PC. All Hough/preprocessing parameters are overridable
    -- see vision/circle_detector.py::load_hough_params, which reads the
    same *_params.json format as the sibling "Image Detection" project's
    tuning GUI, so a params file tuned there can retune this detector too
    without needing separate hardcoded presets for different sample/lighting
    conditions.
    """
    enhanced = enhance_live_microscope_contrast(image_rgb, clahe_clip=clahe_clip, clahe_tile=clahe_tile)
    circles = detect_circular_features(
        enhanced,
        min_radius_px=min_radius_px,
        max_radius_px=max_radius_px,
        min_dist_px=min_dist_px,
        hough_param1=param1,
        hough_param2=param2,
    )
    if not circles:
        raise RuntimeError("No circle-like electrodes were detected in the live microscope RGB image")

    rows = _assign_rows(
        [circle[1] for circle in circles],
        tolerance_px=max(8.0, float(np.median([circle[2] for circle in circles])) * 2.0),
    )
    size_groups = _assign_size_groups([circle[2] for circle in circles])
    sample_bounds = estimate_sample_bounds(_to_gray(enhanced))

    detections: List[CircleDetection] = []
    row_buckets = {}
    for idx, (circle, row_index, size_name) in enumerate(zip(circles, rows, size_groups), start=1):
        x, y, r, source = circle
        row_buckets.setdefault(row_index, []).append((x, y, r, size_name, source, idx))

    ordinal = 1
    for row_index in sorted(row_buckets):
        for col_index, (x, y, r, size_name, source, _) in enumerate(
            sorted(row_buckets[row_index], key=lambda item: item[0]), start=1
        ):
            sample_x_mm = None
            sample_y_mm = None
            if sample_bounds and sample_side_mm:
                bx, by, bw, bh = sample_bounds
                sample_x_mm = ((x - bx) / max(bw, 1)) * sample_side_mm
                sample_y_mm = ((y - by) / max(bh, 1)) * sample_side_mm

            detections.append(
                CircleDetection(
                    x_px=x,
                    y_px=y,
                    radius_px=r,
                    row_index=row_index + 1,
                    col_index=col_index,
                    ordinal=ordinal,
                    size_group=size_name,
                    sample_x_mm=sample_x_mm,
                    sample_y_mm=sample_y_mm,
                    source=f"{source}_live_preset",
                )
            )
            ordinal += 1

    if size_group != "all":
        detections = [det for det in detections if det.size_group == size_group]

    return pd.DataFrame([det.to_dict() for det in detections])


def refine_circular_electrode_map_rgb(
    image_rgb: np.ndarray,
    seed_detections: pd.DataFrame,
    *,
    search_radius_px: float = 40.0,
    radius_tolerance: float = 0.35,
) -> pd.DataFrame:
    """
    Refine previously known circle locations on a new frame.

    This is intended for live microscope monitoring when the approximate
    electrode layout is already known (from a prior auto pass, markup, or
    design-assisted setup) and we just need to track small stage/camera drift.
    """
    if seed_detections is None or seed_detections.empty:
        return seed_detections

    enhanced = enhance_live_microscope_contrast(image_rgb)
    gray = _to_gray(enhanced)
    frame_h, frame_w = gray.shape[:2]
    refined_rows = []

    for _, row in seed_detections.iterrows():
        x = float(row["x_px"])
        y = float(row["y_px"])
        r = float(row["radius_px"])
        roi_pad = max(search_radius_px, r * 1.2)
        x0 = int(max(np.floor(x - roi_pad - r), 0))
        y0 = int(max(np.floor(y - roi_pad - r), 0))
        x1 = int(min(np.ceil(x + roi_pad + r), frame_w))
        y1 = int(min(np.ceil(y + roi_pad + r), frame_h))
        roi = gray[y0:y1, x0:x1]

        best = None
        if roi.size > 0:
            min_r = max(5, int(round(r * (1.0 - radius_tolerance))))
            max_r = max(min_r + 1, int(round(r * (1.0 + radius_tolerance))))
            circles = cv2.HoughCircles(
                cv2.medianBlur(roi, 5),
                cv2.HOUGH_GRADIENT,
                dp=1.2,
                minDist=max(10.0, r * 1.5),
                param1=100,
                param2=12,
                minRadius=min_r,
                maxRadius=max_r,
            )
            if circles is not None:
                candidates = []
                for cx, cy, cr in circles[0]:
                    abs_x = float(x0 + cx)
                    abs_y = float(y0 + cy)
                    center_dist = float(np.hypot(abs_x - x, abs_y - y))
                    radius_dist = abs(float(cr) - r)
                    score = center_dist + 0.5 * radius_dist
                    candidates.append((score, abs_x, abs_y, float(cr)))
                if candidates:
                    _, bx, by, br = min(candidates, key=lambda item: item[0])
                    best = (bx, by, br)

        updated = row.to_dict()
        if best is not None:
            updated["x_px"] = best[0]
            updated["y_px"] = best[1]
            updated["radius_px"] = best[2]
            updated["source"] = f"{updated.get('source', 'seed')}_tracked"
        refined_rows.append(updated)

    return pd.DataFrame(refined_rows)


def annotate_detections(
    image_rgb: np.ndarray,
    detections: pd.DataFrame,
    *,
    annotate_labels: bool = True,
) -> np.ndarray:
    canvas = image_rgb.copy()
    has_detected_col = "detected" in detections.columns
    has_occluded_col = "occluded" in detections.columns
    for _, row in detections.iterrows():
        center = (int(round(row["x_px"])), int(round(row["y_px"])))
        radius = int(round(row["radius_px"]))
        # "detected" (only present for tracked/seeded circles -- see
        # gui.py::_image_tracked_circles_to_table) is True if Hough
        # actually re-found this electrode this cycle, False if its
        # position was only carried forward/projected from tracking.
        # Callers with no such column (live/refined detections, which ARE
        # this frame's own fresh output) are treated as detected=True.
        detected = bool(row["detected"]) if has_detected_col else True
        # "occluded" (also gui.py::_image_tracked_circles_to_table) marks
        # an electrode skipped entirely this cycle as probe-occluded --
        # distinct from an ordinary detection failure so the occlusion
        # region can be visually verified. Temporary diagnostic color, not
        # meant to be permanent.
        occluded = bool(row["occluded"]) if has_occluded_col else False
        if occluded:
            color = (160, 160, 160)
        elif not detected:
            color = (160, 160, 160)
        else:
            color = (40, 220, 40) if row["size_group"] == "large" else (255, 160, 40)
        cv2.circle(canvas, center, radius, color, 2)
        cv2.circle(canvas, center, 2, (255, 0, 0), -1)
        if annotate_labels:
            text = f"R{int(row['row_index'])}C{int(row['col_index'])}"
            cv2.putText(
                canvas,
                text,
                (center[0] + 4, center[1] - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    return canvas


def filter_live_overlay_detections(
    detections: pd.DataFrame,
    *,
    max_candidates: Optional[int] = None,
    neighbor_dist_px: float = 90.0,
) -> pd.DataFrame:
    """
    Reduce raw live detector output to a conservative overlay subset.

    The live microscope detector intentionally casts a wide net. For GUI
    overlay, showing every raw circle can look erratic, especially before
    calibration. This helper keeps only candidates that have local support and
    then returns the strongest few by a simple radius + neighbor score.
    """
    if detections is None or detections.empty:
        return detections

    work = detections.copy()
    coords = work[["x_px", "y_px"]].to_numpy(dtype=float)
    radii = work["radius_px"].to_numpy(dtype=float)
    median_r = float(np.median(radii)) if len(radii) else 0.0
    effective_neighbor_dist = max(float(neighbor_dist_px), 4.0 * median_r if median_r > 0 else 0.0)

    neighbor_counts = []
    for i, (x, y) in enumerate(coords):
        deltas = coords - np.array([x, y], dtype=float)
        dists = np.sqrt(np.sum(deltas * deltas, axis=1))
        neighbor_counts.append(int(np.sum((dists <= effective_neighbor_dist) & (dists > 0))))

    work["overlay_neighbor_count"] = neighbor_counts
    supported = work[work["overlay_neighbor_count"] >= 1].copy()
    if supported.empty:
        supported = work.copy()

    y_sorted = np.sort(supported["y_px"].to_numpy(dtype=float))
    if len(y_sorted) >= 4:
        y_gaps = np.diff(y_sorted)
        largest_gap_idx = int(np.argmax(y_gaps))
        largest_gap = float(y_gaps[largest_gap_idx])
        median_supported_r = float(np.median(supported["radius_px"])) if len(supported) else 1.0
        gap_threshold = max(60.0, 2.5 * median_supported_r)
        if largest_gap > gap_threshold:
            cutoff_y = float(y_sorted[largest_gap_idx])
            upper = supported[supported["y_px"] <= cutoff_y].copy()
            lower = supported[supported["y_px"] > cutoff_y].copy()
            supported = upper if len(upper) >= len(lower) else lower

    median_supported_r = float(np.median(supported["radius_px"])) if len(supported) else 1.0
    supported["overlay_score"] = (
        supported["overlay_neighbor_count"] * 2.0
        + supported["radius_px"] / max(median_supported_r, 1.0)
    )
    supported = supported.sort_values(
        by=["overlay_score", "radius_px"],
        ascending=[False, False],
    )
    if max_candidates is not None:
        supported = supported.head(max_candidates)
    return supported.drop(columns=["overlay_neighbor_count", "overlay_score"], errors="ignore")


def project_points(
    points_xy: np.ndarray,
    homography: np.ndarray,
) -> np.ndarray:
    """Project pixel points through a 3x3 homography/affine matrix."""
    if points_xy.ndim != 2 or points_xy.shape[1] != 2:
        raise ValueError("points_xy must have shape (N, 2)")
    src = points_xy.reshape(-1, 1, 2).astype(np.float32)
    dst = cv2.perspectiveTransform(src, homography)
    return dst.reshape(-1, 2)
