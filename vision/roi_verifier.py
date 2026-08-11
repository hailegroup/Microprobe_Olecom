# -*- coding: utf-8 -*-
"""
Minimal ROI re-identification helpers for future camera-guided stage moves.

The first practical use case is conservative:
    - capture a reference frame before / at a known electrode location
    - move the stage
    - capture a new frame
    - verify whether the expected ROI is still present near the predicted pixel

This does not try to solve full tip tracking today. It gives the project a
simple, testable primitive for "did we revisit the same neighborhood?"
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Tuple

import cv2
import numpy as np


@dataclass
class ROIMatchResult:
    score: float
    matched_center_x_px: float
    matched_center_y_px: float
    dx_px: float
    dy_px: float
    template_size_px: int
    search_radius_px: int

    def to_dict(self) -> dict:
        return asdict(self)


def _to_gray(image_rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)


def _prepare_match_image(image_rgb: np.ndarray) -> np.ndarray:
    gray = _to_gray(image_rgb)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_eq = clahe.apply(gray)
    blurred = cv2.GaussianBlur(gray_eq, (3, 3), 0)
    lap = cv2.Laplacian(blurred, cv2.CV_32F)
    lap_abs = np.abs(lap)
    return cv2.normalize(lap_abs, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def _bounded_square(
    width: int,
    height: int,
    center_x: float,
    center_y: float,
    half_size_px: int,
) -> Tuple[int, int, int, int]:
    cx = int(round(center_x))
    cy = int(round(center_y))
    x0 = max(0, cx - half_size_px)
    y0 = max(0, cy - half_size_px)
    x1 = min(width, cx + half_size_px)
    y1 = min(height, cy + half_size_px)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("ROI bounds collapsed outside the image")
    return x0, y0, x1, y1


def extract_square_roi(
    image_rgb: np.ndarray,
    *,
    center_x_px: float,
    center_y_px: float,
    half_size_px: int,
) -> np.ndarray:
    if half_size_px <= 0:
        raise ValueError("half_size_px must be positive")
    height, width = image_rgb.shape[:2]
    x0, y0, x1, y1 = _bounded_square(width, height, center_x_px, center_y_px, half_size_px)
    return image_rgb[y0:y1, x0:x1].copy()


def verify_roi_revisit(
    reference_rgb: np.ndarray,
    current_rgb: np.ndarray,
    *,
    expected_center_x_px: float,
    expected_center_y_px: float,
    half_size_px: int = 48,
    search_radius_px: int = 24,
) -> ROIMatchResult:
    """
    Find the best local match for the reference ROI around the expected center.

    A later stage-move verifier can use this to answer:
        "after moving, do I still see the same electrode neighborhood where I
        expected it?"
    """
    if search_radius_px < 0:
        raise ValueError("search_radius_px must be non-negative")

    ref_gray = _prepare_match_image(reference_rgb)
    cur_gray = _prepare_match_image(current_rgb)

    template = extract_square_roi(
        cv2.cvtColor(ref_gray, cv2.COLOR_GRAY2RGB),
        center_x_px=expected_center_x_px,
        center_y_px=expected_center_y_px,
        half_size_px=half_size_px,
    )[:, :, 0]

    cur_h, cur_w = cur_gray.shape[:2]
    search_half = half_size_px + search_radius_px
    sx0, sy0, sx1, sy1 = _bounded_square(
        cur_w,
        cur_h,
        expected_center_x_px,
        expected_center_y_px,
        search_half,
    )
    search = cur_gray[sy0:sy1, sx0:sx1]
    if (
        search.shape[0] < template.shape[0]
        or search.shape[1] < template.shape[1]
    ):
        raise ValueError("Search window is smaller than the ROI template")

    result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    top_left_x = sx0 + max_loc[0]
    top_left_y = sy0 + max_loc[1]
    matched_center_x = top_left_x + template.shape[1] / 2.0
    matched_center_y = top_left_y + template.shape[0] / 2.0

    return ROIMatchResult(
        score=float(max_val),
        matched_center_x_px=float(matched_center_x),
        matched_center_y_px=float(matched_center_y),
        dx_px=float(matched_center_x - expected_center_x_px),
        dy_px=float(matched_center_y - expected_center_y_px),
        template_size_px=int(template.shape[0]),
        search_radius_px=int(search_radius_px),
    )
