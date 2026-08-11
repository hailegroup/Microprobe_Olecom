# -*- coding: utf-8 -*-
"""
Electrode-map planning helpers for future microscope-guided full automation.

This module is intentionally image-first and conservative:
- it can detect circular electrode candidates from design / OM images,
- convert those pixel positions into approximate sample-mm coordinates,
- optionally collect manual correspondences through matplotlib clicks,
- and project design-map locations into a target OM image via homography.

The goal is not to fully automate microscope guidance today, but to give the
project a concrete internal scaffold for:
1. design-assisted planning before measurement,
2. OM-image assisted electrode localization,
3. later live-camera tip verification / contact confirmation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from skimage.feature import canny
from skimage.transform import hough_circle, hough_circle_peaks


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


@dataclass
class RegistrationResult:
    homography: np.ndarray
    projected_points: np.ndarray


def load_image_rgb(path: Path | str) -> np.ndarray:
    path = Path(path)
    image_bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


def _to_gray(image_rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)


def enhance_live_microscope_contrast(image_rgb: np.ndarray) -> np.ndarray:
    gray = _to_gray(image_rgb)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_eq = clahe.apply(gray)
    return cv2.cvtColor(gray_eq, cv2.COLOR_GRAY2RGB)


def strip_red_markup_from_rgb(image_rgb: np.ndarray) -> np.ndarray:
    """
    Remove red annotation strokes/circles from a markup image.

    This keeps the user-provided markup useful for extracting trusted electrode
    centers, while also giving us a cleaner microscope-looking reference frame
    for later ECC registration / drift tracking.
    """
    hsv = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2HSV)
    mask1 = cv2.inRange(
        hsv,
        np.array([0, 80, 80], dtype=np.uint8),
        np.array([12, 255, 255], dtype=np.uint8),
    )
    mask2 = cv2.inRange(
        hsv,
        np.array([168, 80, 80], dtype=np.uint8),
        np.array([180, 255, 255], dtype=np.uint8),
    )
    mask = cv2.bitwise_or(mask1, mask2)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel, iterations=1)
    cleaned_bgr = cv2.inpaint(
        cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR),
        mask,
        inpaintRadius=5,
        flags=cv2.INPAINT_TELEA,
    )
    return cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)


def scale_detection_table(
    detections: pd.DataFrame,
    *,
    source_shape: Tuple[int, int],
    target_shape: Tuple[int, int],
) -> pd.DataFrame:
    """
    Scale a detection table from one image size to another.

    This is mainly for microscope markup workflows where the seed layout was
    drawn on a saved snapshot and the live camera feed may be resized but still
    represents the same field of view.
    """
    if detections is None or detections.empty:
        return detections

    src_h, src_w = source_shape[:2]
    dst_h, dst_w = target_shape[:2]
    if src_h <= 0 or src_w <= 0:
        raise ValueError("source_shape must be positive")
    sx = float(dst_w) / float(src_w)
    sy = float(dst_h) / float(src_h)
    scale_r = (sx + sy) / 2.0

    scaled = detections.copy()
    scaled["x_px"] = scaled["x_px"].astype(float) * sx
    scaled["y_px"] = scaled["y_px"].astype(float) * sy
    scaled["radius_px"] = scaled["radius_px"].astype(float) * scale_r
    return scaled


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
    allow_skimage_fallback: bool = True,
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

    # Fallback for weak OM contrast / low-diameter circles.
    if allow_skimage_fallback and len(detections) < 8:
        edges = canny(gray, sigma=2.0, low_threshold=10, high_threshold=50)
        radii = np.arange(min_radius_px, max_radius_px + 1, max(1, (max_radius_px - min_radius_px) // 12 or 1))
        if len(radii) > 0:
            hough_res = hough_circle(edges, radii)
            accums, cx, cy, rr = hough_circle_peaks(
                hough_res, radii, total_num_peaks=64
            )
            for x, y, r in zip(cx, cy, rr):
                detections.append((float(x), float(y), float(r), "skimage_hough"))

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


def _cluster_row_circles_by_x(
    circles: Sequence[Tuple[float, float, float, str]],
    *,
    proximity_px: float,
) -> List[Tuple[float, float, float, str]]:
    if not circles:
        return []

    clustered: List[Tuple[float, float, float, str]] = []
    current_cluster: List[Tuple[float, float, float, str]] = [circles[0]]
    for circle in circles[1:]:
        if abs(circle[0] - current_cluster[-1][0]) <= proximity_px:
            current_cluster.append(circle)
            continue
        clustered.append(max(current_cluster, key=lambda item: item[2]))
        current_cluster = [circle]
    if current_cluster:
        clustered.append(max(current_cluster, key=lambda item: item[2]))
    return clustered


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


def _filter_structured_circle_rows(
    circles: Sequence[Tuple[float, float, float, str]],
) -> List[Tuple[float, float, float, str]]:
    """
    Keep only circles that participate in row/spacing structure.

    This is meant for cleaner microscope images where electrodes appear as
    repeated circular patterns. It deliberately does *not* assume a fixed
    number of electrodes; instead it keeps circles that belong to rows with
    consistent intra-row spacing.
    """
    if not circles:
        return []

    radii = np.array([circle[2] for circle in circles], dtype=float)
    row_tol = max(8.0, float(np.median(radii)) * 2.0)
    row_ids = _assign_rows([circle[1] for circle in circles], tolerance_px=row_tol)

    row_groups: List[Tuple[float, List[Tuple[float, float, float, str]]]] = []
    median_r = float(np.median(radii)) if len(radii) else 10.0
    min_gap = max(12.0, 1.2 * median_r)
    max_gap = max(160.0, 10.0 * median_r)
    precluster_tol = max(14.0, 1.8 * median_r)
    for row_id in sorted(set(row_ids)):
        row_circles = [circles[idx] for idx, rid in enumerate(row_ids) if rid == row_id]
        row_circles = sorted(row_circles, key=lambda item: item[0])
        row_circles = _cluster_row_circles_by_x(row_circles, proximity_px=precluster_tol)
        if len(row_circles) < 3:
            continue
        xs = np.array([circle[0] for circle in row_circles], dtype=float)
        diffs = np.diff(xs)
        valid_diffs = diffs[(diffs >= min_gap) & (diffs <= max_gap)]
        if len(valid_diffs) == 0:
            continue
        dominant_spacing = float(np.median(valid_diffs))
        row_circles = _cluster_row_circles_by_x(
            row_circles,
            proximity_px=max(precluster_tol, 0.35 * dominant_spacing),
        )
        if len(row_circles) < 3:
            continue
        xs = np.array([circle[0] for circle in row_circles], dtype=float)
        filtered_row = []
        for idx, circle in enumerate(row_circles):
            left_ok = (
                idx > 0
                and 0.45 * dominant_spacing <= xs[idx] - xs[idx - 1] <= 1.9 * dominant_spacing
            )
            right_ok = (
                idx < len(row_circles) - 1
                and 0.45 * dominant_spacing <= xs[idx + 1] - xs[idx] <= 1.9 * dominant_spacing
            )
            if left_ok or right_ok:
                filtered_row.append(circle)
        if len(filtered_row) >= 3:
            row_span = float(np.max([circle[0] for circle in filtered_row]) - np.min([circle[0] for circle in filtered_row]))
            if row_span < max(2.0 * dominant_spacing, 120.0):
                continue
            row_groups.append((dominant_spacing, filtered_row))

    if not row_groups:
        return list(circles)

    spacings = np.array([spacing for spacing, _ in row_groups], dtype=float)
    dominant_row_spacing = float(np.median(spacings))
    kept: List[Tuple[float, float, float, str]] = []
    for spacing, row_circles in row_groups:
        if 0.6 * dominant_row_spacing <= spacing <= 1.5 * dominant_row_spacing:
            kept.extend(row_circles)

    return _deduplicate_circles(kept, center_tol_px=6.0, radius_tol_px=5.0)


def detect_electrode_map(
    image_path: Path | str,
    *,
    sample_side_mm: Optional[float] = 10.0,
    size_group: str = "all",
    min_radius_px: int = 3,
    max_radius_px: Optional[int] = None,
) -> pd.DataFrame:
    image_rgb = load_image_rgb(image_path)
    return detect_electrode_map_rgb(
        image_rgb,
        sample_side_mm=sample_side_mm,
        size_group=size_group,
        min_radius_px=min_radius_px,
        max_radius_px=max_radius_px,
    )


def detect_electrode_map_rgb(
    image_rgb: np.ndarray,
    *,
    sample_side_mm: Optional[float] = 10.0,
    size_group: str = "all",
    min_radius_px: int = 3,
    max_radius_px: Optional[int] = None,
) -> pd.DataFrame:
    circles = detect_circular_features(
        image_rgb,
        min_radius_px=min_radius_px,
        max_radius_px=max_radius_px,
    )
    if not circles:
        raise RuntimeError("No circle-like electrodes were detected in the provided RGB image")

    rows = _assign_rows(
        [circle[1] for circle in circles],
        tolerance_px=max(6.0, float(np.median([circle[2] for circle in circles])) * 1.8),
    )
    size_groups = _assign_size_groups([circle[2] for circle in circles])
    sample_bounds = estimate_sample_bounds(_to_gray(image_rgb))

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
                    source=source,
                )
            )
            ordinal += 1

    if size_group != "all":
        detections = [det for det in detections if det.size_group == size_group]

    return pd.DataFrame([det.to_dict() for det in detections])


def detect_live_microscope_electrode_map_rgb(
    image_rgb: np.ndarray,
    *,
    sample_side_mm: Optional[float] = 10.0,
    size_group: str = "all",
) -> pd.DataFrame:
    """
    More permissive preset for noisy/soft microscope-camera live views.

    This uses CLAHE contrast enhancement and a wider Hough-circle preset tuned
    from the Swift camera snapshot sweep on the lab PC.
    """
    enhanced = enhance_live_microscope_contrast(image_rgb)
    circles = detect_circular_features(
        enhanced,
        min_radius_px=8,
        max_radius_px=40,
        min_dist_px=50.0,
        hough_param2=15.0,
        allow_skimage_fallback=False,
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


def detect_reference_microscope_electrode_map_rgb(
    image_rgb: np.ndarray,
    *,
    sample_side_mm: Optional[float] = 10.0,
    size_group: str = "all",
) -> pd.DataFrame:
    """
    Detector preset for higher-quality OM images.

    Unlike the noisier live preset, this path prefers structural regularity
    (row support + near-uniform intra-row spacing) and is intended for clearer
    microscope images such as the `monitoring during measurement` reference.
    """
    enhanced = enhance_live_microscope_contrast(image_rgb)
    frame_h = enhanced.shape[0]
    # In reference-style OM images such as "monitoring during measurement",
    # the upper band can contain a zoomed/partial structure that is not part
    # of the repeated electrode field we want to localize. Keep the lower
    # field-of-interest only.
    roi_y0 = int(round(frame_h * 0.22))
    roi_rgb = enhanced[roi_y0:, :, :]
    circles = detect_circular_features(
        roi_rgb,
        min_radius_px=10,
        max_radius_px=28,
        min_dist_px=65.0,
        hough_param2=24.0,
        allow_skimage_fallback=False,
    )
    circles = [(x, y + roi_y0, r, source) for x, y, r, source in circles]
    circles = _filter_structured_circle_rows(circles)
    if not circles:
        raise RuntimeError("No structured circular electrode pattern was detected in the reference microscope RGB image")

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
                    source=f"{source}_reference_preset",
                )
            )
            ordinal += 1

    if size_group != "all":
        detections = [det for det in detections if det.size_group == size_group]

    return pd.DataFrame([det.to_dict() for det in detections])


def detect_markup_electrode_map_rgb(
    image_rgb: np.ndarray,
    *,
    sample_side_mm: Optional[float] = 10.0,
    size_group: str = "all",
    min_radius_px: float = 10.0,
) -> pd.DataFrame:
    """
    Parse user-drawn red circle markup exported from a microscope snapshot.

    This is meant to convert a manually annotated screenshot into a reliable
    electrode layout when automatic live detection is still too noisy.
    """
    hsv = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2HSV)
    mask1 = cv2.inRange(hsv, np.array([0, 80, 80], dtype=np.uint8), np.array([12, 255, 255], dtype=np.uint8))
    mask2 = cv2.inRange(hsv, np.array([168, 80, 80], dtype=np.uint8), np.array([180, 255, 255], dtype=np.uint8))
    mask = cv2.bitwise_or(mask1, mask2)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    circles: List[Tuple[float, float, float, str]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 100:
            continue
        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue
        circularity = 4.0 * np.pi * area / (perimeter * perimeter)
        (x, y), r = cv2.minEnclosingCircle(contour)
        if r < min_radius_px:
            continue
        if circularity < 0.45:
            continue
        circles.append((float(x), float(y), float(r), "manual_markup"))

    circles = _deduplicate_circles(circles, center_tol_px=10.0, radius_tol_px=8.0)
    if not circles:
        raise RuntimeError("No red markup circles were detected in the provided RGB image")

    rows = _assign_rows(
        [circle[1] for circle in circles],
        tolerance_px=max(24.0, float(np.median([circle[2] for circle in circles])) * 0.8),
    )
    size_groups = _assign_size_groups([circle[2] for circle in circles])
    sample_bounds = estimate_sample_bounds(_to_gray(image_rgb))

    detections: List[CircleDetection] = []
    row_buckets = {}
    for circle, row_index, size_name in zip(circles, rows, size_groups):
        x, y, r, source = circle
        row_buckets.setdefault(row_index, []).append((x, y, r, size_name, source))

    ordinal = 1
    for row_index in sorted(row_buckets):
        for col_index, (x, y, r, size_name, source) in enumerate(
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
                    source=source,
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
    for _, row in detections.iterrows():
        center = (int(round(row["x_px"])), int(round(row["y_px"])))
        radius = int(round(row["radius_px"]))
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


def save_detection_outputs(
    out_dir: Path | str,
    image_path: Path | str,
    detections: pd.DataFrame,
    annotated_rgb: np.ndarray,
) -> Tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(image_path).stem
    csv_path = out_dir / f"{stem}_electrode_map.csv"
    png_path = out_dir / f"{stem}_electrode_overlay.png"

    detections.to_csv(csv_path, index=False)
    cv2.imwrite(str(png_path), cv2.cvtColor(annotated_rgb, cv2.COLOR_RGB2BGR))
    return csv_path, png_path


def manual_pick_points(
    image_path: Path | str,
    *,
    n_points: int,
    title: str,
) -> np.ndarray:
    image_rgb = load_image_rgb(image_path)
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(image_rgb)
    ax.set_title(title)
    ax.axis("off")
    points = plt.ginput(n=n_points, timeout=0, show_clicks=True)
    plt.close(fig)
    if len(points) != n_points:
        raise RuntimeError(
            f"Expected {n_points} clicks for '{title}', got {len(points)}"
        )
    return np.array(points, dtype=np.float32)


def compute_homography(
    src_points: np.ndarray,
    dst_points: np.ndarray,
) -> np.ndarray:
    homography, mask = cv2.findHomography(src_points, dst_points, cv2.RANSAC, 5.0)
    if homography is None:
        raise RuntimeError("Could not compute homography from the selected points")
    return homography


def project_points(
    points_xy: np.ndarray,
    homography: np.ndarray,
) -> np.ndarray:
    if points_xy.ndim != 2 or points_xy.shape[1] != 2:
        raise ValueError("points_xy must have shape (N, 2)")
    src = points_xy.reshape(-1, 1, 2).astype(np.float32)
    dst = cv2.perspectiveTransform(src, homography)
    return dst.reshape(-1, 2)


def register_design_to_target(
    design_map: pd.DataFrame,
    design_points: np.ndarray,
    target_points: np.ndarray,
) -> RegistrationResult:
    homography = compute_homography(design_points, target_points)
    projected = project_points(
        design_map[["x_px", "y_px"]].to_numpy(dtype=np.float32),
        homography,
    )
    return RegistrationResult(homography=homography, projected_points=projected)


def build_projected_detection_table(
    design_map: pd.DataFrame,
    registration: RegistrationResult,
) -> pd.DataFrame:
    projected = design_map.copy()
    projected["x_px"] = registration.projected_points[:, 0]
    projected["y_px"] = registration.projected_points[:, 1]
    projected["projected"] = True
    projected["source"] = "design_projection"
    return projected


def compute_ecc_affine_registration(
    reference_rgb: np.ndarray,
    moving_rgb: np.ndarray,
    *,
    iterations: int = 200,
    epsilon: float = 1e-6,
) -> Tuple[np.ndarray, float]:
    """
    Register a moving microscope frame to a reference frame using ECC affine alignment.
    Returns a 3x3 homography-like affine matrix plus the ECC score.
    """
    ref_gray = _to_gray(enhance_live_microscope_contrast(reference_rgb)).astype(np.float32) / 255.0
    mov_gray = _to_gray(enhance_live_microscope_contrast(moving_rgb)).astype(np.float32) / 255.0
    if ref_gray.shape != mov_gray.shape:
        mov_gray = cv2.resize(mov_gray, (ref_gray.shape[1], ref_gray.shape[0]), interpolation=cv2.INTER_AREA)

    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        int(iterations),
        float(epsilon),
    )
    cc, warp = cv2.findTransformECC(
        ref_gray,
        mov_gray,
        warp,
        cv2.MOTION_AFFINE,
        criteria,
    )
    homography = np.eye(3, dtype=np.float32)
    homography[:2, :] = warp
    return homography, float(cc)


def transform_detection_table(
    detections: pd.DataFrame,
    transform: np.ndarray,
    *,
    source_tag: str = "registered_projection",
) -> pd.DataFrame:
    if detections is None or detections.empty:
        return detections
    projected = detections.copy()
    projected_pts = project_points(
        projected[["x_px", "y_px"]].to_numpy(dtype=np.float32),
        transform,
    )
    projected["x_px"] = projected_pts[:, 0]
    projected["y_px"] = projected_pts[:, 1]
    projected["projected"] = True
    projected["source"] = source_tag
    return projected
