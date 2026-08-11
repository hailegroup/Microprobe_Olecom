# -*- coding: utf-8 -*-
"""
Minimal image-to-stage mapping helpers for future OM/design-guided full-auto runs.

This module intentionally avoids heavy GUI/OpenCV dependencies for now and
focuses on the core math we will need later:
  - solve an affine pixel -> stage mapping from clicked reference points
  - generate regular electrode candidates on an OM/design image
  - map those candidate pixels into stage XY positions
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, List, Sequence, Tuple

import numpy as np


@dataclass
class PixelStageReference:
    pixel_x: float
    pixel_y: float
    stage_x_mm: float
    stage_y_mm: float


@dataclass
class SampleStageReference:
    sample_x_mm: float
    sample_y_mm: float
    stage_x_mm: float
    stage_y_mm: float


@dataclass
class StageAffineCalibration:
    matrix_2x3: List[List[float]]

    def to_dict(self):
        return asdict(self)


def solve_stage_affine_calibration(
    references: Sequence[PixelStageReference],
) -> StageAffineCalibration:
    if len(references) < 3:
        raise ValueError("At least 3 reference points are required for affine calibration.")

    a_rows = []
    b_rows = []
    for ref in references:
        a_rows.append([float(ref.pixel_x), float(ref.pixel_y), 1.0])
        b_rows.append([float(ref.stage_x_mm), float(ref.stage_y_mm)])

    a = np.asarray(a_rows, dtype=float)
    b = np.asarray(b_rows, dtype=float)
    coeffs, *_ = np.linalg.lstsq(a, b, rcond=None)
    matrix = coeffs.T
    return StageAffineCalibration(matrix_2x3=matrix.tolist())


def solve_sample_to_stage_affine_calibration(
    references: Sequence[SampleStageReference],
) -> StageAffineCalibration:
    if len(references) < 3:
        raise ValueError("At least 3 reference points are required for affine calibration.")

    a_rows = []
    b_rows = []
    for ref in references:
        a_rows.append([float(ref.sample_x_mm), float(ref.sample_y_mm), 1.0])
        b_rows.append([float(ref.stage_x_mm), float(ref.stage_y_mm)])

    a = np.asarray(a_rows, dtype=float)
    b = np.asarray(b_rows, dtype=float)
    coeffs, *_ = np.linalg.lstsq(a, b, rcond=None)
    matrix = coeffs.T
    return StageAffineCalibration(matrix_2x3=matrix.tolist())


def pixel_to_stage_xy(
    calibration: StageAffineCalibration,
    pixel_x: float,
    pixel_y: float,
) -> Tuple[float, float]:
    matrix = np.asarray(calibration.matrix_2x3, dtype=float)
    vec = np.asarray([float(pixel_x), float(pixel_y), 1.0], dtype=float)
    stage_xy = matrix @ vec
    return float(stage_xy[0]), float(stage_xy[1])


def sample_to_stage_xy(
    calibration: StageAffineCalibration,
    sample_x_mm: float,
    sample_y_mm: float,
) -> Tuple[float, float]:
    matrix = np.asarray(calibration.matrix_2x3, dtype=float)
    vec = np.asarray([float(sample_x_mm), float(sample_y_mm), 1.0], dtype=float)
    stage_xy = matrix @ vec
    return float(stage_xy[0]), float(stage_xy[1])


def generate_bilinear_grid_pixels(
    *,
    top_left: Tuple[float, float],
    top_right: Tuple[float, float],
    bottom_left: Tuple[float, float],
    bottom_right: Tuple[float, float],
    rows: int,
    cols: int,
) -> List[Tuple[float, float]]:
    if rows <= 0 or cols <= 0:
        raise ValueError("rows and cols must be positive.")

    tl = np.asarray(top_left, dtype=float)
    tr = np.asarray(top_right, dtype=float)
    bl = np.asarray(bottom_left, dtype=float)
    br = np.asarray(bottom_right, dtype=float)

    pixels = []
    for row_idx in range(rows):
        v = 0.0 if rows == 1 else row_idx / (rows - 1)
        left = tl * (1.0 - v) + bl * v
        right = tr * (1.0 - v) + br * v
        for col_idx in range(cols):
            u = 0.0 if cols == 1 else col_idx / (cols - 1)
            pt = left * (1.0 - u) + right * u
            pixels.append((float(pt[0]), float(pt[1])))
    return pixels


def map_pixels_to_stage_xy(
    calibration: StageAffineCalibration,
    pixels: Iterable[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    return [pixel_to_stage_xy(calibration, px, py) for px, py in pixels]
