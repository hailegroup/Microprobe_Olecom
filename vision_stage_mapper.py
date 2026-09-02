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
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class PixelStageReference:
    pixel_x: float
    pixel_y: float
    stage_x_mm: float
    stage_y_mm: float
    z_mm: Optional[float] = None


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


@dataclass
class ProbeZParallaxCalibration:
    """Pixel-shift-per-mm-of-Z rate for the probe tip, measured from the
    displacement of its detected pixel position between the start and the
    contact-confirmed end of a Z-contact search (XY held fixed throughout,
    so any pixel displacement is attributable to Z alone -- see
    solve_parallax_slope_from_samples). Deliberately has no z_ref_mm: it is
    a pure rate, not an absolute offset: the reference Z belongs to
    whichever pixel-stage affine calibration this correction is applied
    alongside.
    """
    du_per_mm: float
    dv_per_mm: float

    def to_dict(self):
        return asdict(self)


@dataclass
class ProbeXYBiasCalibration:
    """
    Pixel offset between where a probe-tip detector actually finds the
    probe and the electrode's own tracked pixel (its detected center, in
    the same frame, at the same actual Z), measured at AutoContactZ
    touches, since the probe is then physically co-located with the
    electrode -- see solve_xy_bias_from_samples. Exists because thermal
    expansion of the probe arm shifts the *true* pixel<->stage
    relationship as furnace temperature changes, while the pixel<->stage
    affine calibration itself (fit once, from probe-tip clicks at a
    single reference temperature) stays fixed -- so aiming at an
    electrode's own tracked pixel through that now-stale calibration
    lands the probe off by however much the arm has since
    expanded/contracted. The electrode tracker isn't subject to this: the
    electrodes themselves don't move, so their tracked positions stay
    correct regardless of probe-arm temperature -- only the probe's own
    true position relative to the stale calibration drifts. That's what
    makes this bias measurable at all: at a confirmed touch, comparing
    the probe's true (detected) position against the electrode's (still
    accurate) tracked position isolates exactly the probe-arm drift.

    A plain constant, fit from whichever set of samples the caller passes
    in -- e.g. probe-tip thermal expansion shifts this at different
    furnace setpoints, but since the instrument is run at a handful of
    discrete temperatures rather than a continuum, that's handled by the
    caller fitting a separate ProbeXYBiasCalibration per temperature
    bucket (see ElectrodeZCalibrationStore.get_xy_bias), not by this
    dataclass representing temperature dependence itself.
    """
    bias_x_px: float
    bias_y_px: float

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


def stage_to_pixel_xy(
    calibration: StageAffineCalibration,
    stage_x_mm: float,
    stage_y_mm: float,
) -> Tuple[float, float]:
    """
    Inverse of pixel_to_stage_xy: map a stage mm position back to the pixel
    position it corresponds to under the calibrated affine transform.

    Used to compute the probe's current expected pixel position from its
    known motor XY (no image detection needed) so a drift-correction cycle
    can exclude that region from the electrode search rather than risking
    the probe being mistaken for an electrode.

    Raises numpy.linalg.LinAlgError if the calibration's 2x3 matrix is
    singular (e.g. a degenerate calibration with collinear reference points).
    """
    matrix = np.asarray(calibration.matrix_2x3, dtype=float)
    matrix_3x3 = np.vstack([matrix, [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(matrix_3x3)
    vec = np.asarray([float(stage_x_mm), float(stage_y_mm), 1.0], dtype=float)
    pixel_xy = inverse @ vec
    return float(pixel_xy[0]), float(pixel_xy[1])


def solve_parallax_slope_from_samples(
    samples: Sequence[Tuple[float, float, float]],
) -> ProbeZParallaxCalibration:
    """
    Fit the probe's Z-parallax slope from raw (delta_z_mm, delta_pixel_x,
    delta_pixel_y) samples -- each sample is one Z-contact search's
    probe-tip pixel displacement between search-start and contact-confirmed,
    over that search's own known Z change. Pooling samples from multiple
    contact searches (typically at different electrodes/times) gives a more
    robust fit. Requires at least 3 samples, matching this project's
    general preference for redundancy over the mathematical minimum (1, for
    a 1-parameter linear fit -- see below).

    The fit is forced through the origin (zero pixel shift at zero Z
    change): delta_z_mm and delta_pixel are both differences within the
    same contact search with XY held fixed, so delta_z_mm == 0 implies
    zero pixel shift by construction, not just as a modeling convenience.
    An unconstrained intercept (the previous approach, via
    np.polyfit(..., 1) with the intercept term discarded) wastes a degree
    of freedom fitting noise into a term known to be physically zero, and
    its slope estimate is highly sensitive to how tightly the pooled
    samples' delta_z_mm values cluster around their own mean -- confirmed
    on real rig data where 4 samples spanning only a 0.02mm delta_z range
    produced an implausible ~127px/mm slope. The origin-constrained
    estimator (ordinary least squares through the origin,
    sum(z*p)/sum(z*z)) doesn't have this failure mode: its precision
    depends on how far delta_z_mm sits from zero, not on the spread across
    samples -- confirmed the same real data now yields a physically
    plausible ~14px/mm (X) / ~5px/mm (Y) slope instead.
    """
    if len(samples) < 3:
        raise ValueError(
            f"At least 3 parallax samples are required to fit a Z-parallax slope "
            f"({len(samples)} present)."
        )

    delta_zs = np.asarray([float(s[0]) for s in samples], dtype=float)
    delta_pxs = np.asarray([float(s[1]) for s in samples], dtype=float)
    delta_pys = np.asarray([float(s[2]) for s in samples], dtype=float)

    sum_zz = float(np.dot(delta_zs, delta_zs))
    if sum_zz <= 0.0:
        raise ValueError(
            "All parallax samples have zero delta_z_mm; cannot fit a slope."
        )

    du_per_mm = float(np.dot(delta_zs, delta_pxs) / sum_zz)
    dv_per_mm = float(np.dot(delta_zs, delta_pys) / sum_zz)
    return ProbeZParallaxCalibration(du_per_mm=du_per_mm, dv_per_mm=dv_per_mm)


def correct_pixel_for_z_parallax(
    parallax: Optional[ProbeZParallaxCalibration],
    pixel_x: float,
    pixel_y: float,
    delta_z_mm: Optional[float],
) -> Tuple[float, float]:
    """
    Shift an as-observed electrode pixel back to the frame the pixel-stage
    affine calibration was fit in, given how far (in mm) the electrode's
    own known Z is from that calibration's reference Z. Identity (no
    correction) if either input is None -- callers should treat that as
    "not enough information to correct, fall back to today's behavior"
    rather than an error.
    """
    if parallax is None or delta_z_mm is None:
        return float(pixel_x), float(pixel_y)
    delta_z = float(delta_z_mm)
    return (
        float(pixel_x) - parallax.du_per_mm * delta_z,
        float(pixel_y) - parallax.dv_per_mm * delta_z,
    )


def solve_xy_bias_from_samples(
    samples: Sequence[Tuple[float, float]],
) -> ProbeXYBiasCalibration:
    """
    Fit a constant XY pixel bias from raw (observed_px - expected_px,
    observed_py - expected_py) samples -- each sample is one AutoContactZ
    touch's probe-tip ROI detection compared against the electrode's own
    tracked pixel at that touch (both observed in the same frame, at the
    same actual Z, so no calibration-reference-Z assumption is involved --
    see _image_recalibrate_xy_bias_from_contact). Uses the median, not the
    mean, of each axis's deltas: robust against an occasional bad ROI
    detection over a run's worth of accumulated touches. Requires at least
    3 samples, matching this project's general preference for redundancy
    over the mathematical minimum.

    Callers wanting temperature-awareness (e.g. probe-tip thermal
    expansion shifting this at different furnace setpoints) should call
    this once per temperature bucket rather than expecting it to model
    temperature itself -- see ElectrodeZCalibrationStore.get_xy_bias,
    which fits a separate ProbeXYBiasCalibration per discrete setpoint
    (the instrument is run at a handful of fixed temperatures, not a
    continuum, so a single fit across mixed temperatures would blur
    together what should be independent corrections).
    """
    if len(samples) < 3:
        raise ValueError(
            f"At least 3 XY-bias samples are required to fit a bias correction "
            f"({len(samples)} present)."
        )

    delta_xs = np.asarray([float(s[0]) for s in samples], dtype=float)
    delta_ys = np.asarray([float(s[1]) for s in samples], dtype=float)

    return ProbeXYBiasCalibration(
        bias_x_px=float(np.median(delta_xs)),
        bias_y_px=float(np.median(delta_ys)),
    )


def correct_pixel_for_xy_bias(
    bias: Optional[ProbeXYBiasCalibration],
    pixel_x: float,
    pixel_y: float,
) -> Tuple[float, float]:
    """
    Shift a tracked electrode pixel by the probe's known XY bias before
    projecting it through the (probe-fit, now-stale-at-this-temperature)
    pixel-stage affine calibration. bias is defined as
    (observed_probe_pixel - expected_electrode_pixel) at a confirmed
    touch -- i.e. how far the probe's true current position has drifted,
    due to thermal expansion of the probe arm, from where an uncorrected
    aim through the (fixed, single-temperature) calibration would land
    it. An uncorrected future aim at the electrode's own tracked pixel
    would drift by that same amount, so the pixel fed into the
    calibration must be pre-shifted the opposite way to cancel it out --
    this SUBTRACTS. Same sign direction as correct_pixel_for_z_parallax,
    which also subtracts to undo a known physical drift rather than
    compound it. Identity (no correction) if bias is None -- callers
    should treat that as "not enough information yet," not an error.
    Callers needing a temperature-specific bias should resolve it first
    (see ElectrodeZCalibrationStore.get_xy_bias) and pass the result in --
    this function itself has no notion of temperature.
    """
    if bias is None:
        return float(pixel_x), float(pixel_y)
    return (
        float(pixel_x) - bias.bias_x_px,
        float(pixel_y) - bias.bias_y_px,
    )


def parallax_within_trusted_range(
    parallax: Optional[ProbeZParallaxCalibration],
    delta_z_mm: Optional[float],
    max_extrapolation_mm: Optional[float],
) -> bool:
    """
    True if there is nothing to gate (any input is None) or the requested
    delta_z is within max_extrapolation_mm of zero -- i.e. within the range
    the parallax slope can be trusted to extrapolate over. False means the
    caller should skip the correction and fall back to the uncorrected
    projection rather than extrapolating the linear model too far past
    where it was actually measured.
    """
    if parallax is None or delta_z_mm is None or max_extrapolation_mm is None:
        return True
    return abs(float(delta_z_mm)) <= float(max_extrapolation_mm)


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
