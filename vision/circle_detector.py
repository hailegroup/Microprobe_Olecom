# -*- coding: utf-8 -*-
"""
Hough-circle-in-ROI refinement for DXF-layout electrode alignment.

Ported from the sibling "Image Detection" project's circle_detector.py
(detect_circle_in_roi): given a rough clicked/expected center, searches a
small region around it for the true electrode-circle edge via
cv2.HoughCircles, instead of relying on raw click precision alone.
"""

from __future__ import annotations

import json
from typing import Dict, Optional, Tuple

import cv2
import numpy as np


def preprocess_for_circle_search(
    gray: np.ndarray,
    *,
    clahe_clip: float = 2.0,
    clahe_tile: int = 8,
    bilateral_d: int = 9,
    bilateral_sigma: float = 75.0,
) -> np.ndarray:
    """
    CLAHE (local contrast correction for uneven microscope illumination)
    followed by a bilateral filter (denoise while preserving the
    electrode-edge detail Hough search depends on).
    """
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(clahe_tile, clahe_tile))
    enhanced = clahe.apply(gray)
    return cv2.bilateralFilter(enhanced, bilateral_d, bilateral_sigma, bilateral_sigma)


def detect_circle_in_roi(
    processed: np.ndarray,
    center: Tuple[float, float],
    expected_radius: float,
    *,
    search_margin: float = 40.0,
    radius_tolerance: float = 6.0,
    param1: float = 50.0,
    param2: float = 20.0,
) -> Optional[Tuple[float, float, float]]:
    """
    Search for a single circle inside a small ROI around center.

    Returns (x, y, radius) in the full processed-image coordinate space, or
    None if no circle was found. The ROI is expanded to always fully contain
    the expected circle plus the search margin, so Hough always sees
    complete arcs; minDist is clamped to stay within the ROI so OpenCV never
    silently discards every candidate.
    """
    ex, ey = int(round(center[0])), int(round(center[1]))
    h, w = processed.shape[:2]

    half = int(round(expected_radius + radius_tolerance + search_margin))
    x1 = max(0, ex - half)
    y1 = max(0, ey - half)
    x2 = min(w, ex + half)
    y2 = min(h, ey + half)

    roi = processed[y1:y2, x1:x2]
    roi_w, roi_h = x2 - x1, y2 - y1
    if roi.size == 0 or roi_w < 3 or roi_h < 3:
        return None

    max_min_dist = min(roi_w, roi_h) - 1
    if max_min_dist < 1:
        return None
    min_dist = min(max(1, int(round(expected_radius * 1.5))), max_min_dist)

    raw = cv2.HoughCircles(
        roi, cv2.HOUGH_GRADIENT, dp=1, minDist=min_dist,
        param1=param1, param2=param2,
        minRadius=max(1, int(round(expected_radius - radius_tolerance))),
        maxRadius=int(round(expected_radius + radius_tolerance)),
    )
    if raw is None:
        return None

    # HoughCircles can return several candidates within the ROI (a tightly
    # spaced neighbouring electrode easily falls inside expected_radius +
    # radius_tolerance + search_margin) and orders them by internal
    # accumulator score, not by distance to the clicked/expected center --
    # taking candidate 0 unconditionally could "snap" to a neighbour that
    # scored better instead of the one actually under the click. Pick
    # whichever candidate is geometrically closest to center instead.
    local_ex, local_ey = ex - x1, ey - y1
    cx, cy, r = min(
        raw[0], key=lambda c: (c[0] - local_ex) ** 2 + (c[1] - local_ey) ** 2,
    )
    return float(cx) + x1, float(cy) + y1, float(r)


def load_hough_params(path: str) -> Dict[str, float]:
    """
    Load a *_params.json file -- the same format saved by the sibling
    "Image Detection" project's standalone tuning GUI -- into a flat dict of
    keyword arguments for preprocess_for_circle_search/detect_circle_in_roi.
    Missing keys fall back to this module's own defaults. Raises on a
    missing/corrupt file or non-numeric contents (mirrors this project's
    tolerant-load convention of leaving that decision to the caller, since a
    silently-wrong params file is worse than a visible failure here).
    """
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    pre = data.get('preprocessing', {})
    hough = data.get('hough', {})

    min_radius = float(hough.get('min_radius', 14.0))
    max_radius = float(hough.get('max_radius', 26.0))

    return {
        'clahe_clip': float(pre.get('clahe_clip', 2.0)),
        'clahe_tile': int(pre.get('clahe_tile', 8)),
        'bilateral_d': int(pre.get('bilateral_d', 9)),
        'bilateral_sigma': float(pre.get('bilateral_sigma', 75.0)),
        'param1': float(hough.get('param1', 50.0)),
        'param2': float(hough.get('param2_roi', hough.get('param2', 20.0))),
        'expected_radius': (min_radius + max_radius) / 2.0,
        'radius_tolerance': max(2.0, (max_radius - min_radius) / 2.0),
    }
