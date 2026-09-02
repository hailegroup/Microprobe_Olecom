# -*- coding: utf-8 -*-
"""
Layout geometry and affine-alignment math for CAD-DXF-derived electrode
layouts.

Ported from the sibling "Image Detection" (Microelectrode Tracker) project's
``models.py`` (``Circle``, ``LayoutModel``) and the pure math inside
``alignment.py``'s ``LayoutAlignmentTool`` (``_fit_from_pairs``,
``_apply_nudge``, ``_project_with_transform``, ``_estimate_proj_radius``,
``_project_all_circles``).

Deliberately excludes the source project's interactive ``cv2.imshow``/mouse-
callback/trackbar UI (circle drawing, side-by-side pairing view, nudge
trackbars) -- that workflow is rebuilt natively in the Tkinter "Image
Monitor" tab in ``gui.py``, which calls the free functions here.

NOTE on ``Circle.confidence``: despite its docstring below (kept from the
source for continuity), nothing in this codebase currently computes a real
Hough accumulator score. ``vision/electrode_drift.py::detect_circle_in_roi``
sets it to a hardcoded ``1.0`` whenever a circle is found at all -- it is a
detected/not-detected flag today, not a calibrated confidence value. Do not
treat it as a probability in gating logic; use a redetection *count* and/or
RANSAC inlier fraction instead (see ``vision/electrode_drift.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np


@dataclass
class Circle:
    x: float
    y: float
    radius: float
    confidence: float = 0.0       # NOT a calibrated score -- see module docstring
    detected: bool = False        # Was this found by Hough in this frame?
    smoothed_x: float = 0.0
    smoothed_y: float = 0.0
    layout_index: Optional[int] = None  # which template electrode this is
    on_image: bool = True               # is the position within image bounds?

    def __post_init__(self):
        self.smoothed_x = self.x
        self.smoothed_y = self.y

    @property
    def center(self):
        return (self.smoothed_x, self.smoothed_y)

    def update_smoothed(self, new_x, new_y, alpha=0.3):
        """Exponential moving average for temporal smoothing."""
        self.smoothed_x = alpha * new_x + (1 - alpha) * self.smoothed_x
        self.smoothed_y = alpha * new_y + (1 - alpha) * self.smoothed_y


class LayoutModel:
    """
    Stores the canonical (template) positions of all electrodes, in physical
    CAD (mm) units, and fits/holds an affine transform from that template
    space into image-pixel space.

    The layout is normally loaded from a JSON file produced by
    ``vision/dxf_layout.py`` via ``from_json()``.
    """

    def __init__(self, template_positions: np.ndarray, template_radii: Optional[np.ndarray] = None):
        # shape (N, 2), the "ideal" relative positions
        self.template = template_positions.astype(np.float32)
        self.n = len(self.template)
        self.transform: Optional[np.ndarray] = None  # 2x3 affine matrix, template -> pixel
        # Per-electrode radii in DXF units (may be None if not available)
        self.template_radii = (
            template_radii.astype(np.float32) if template_radii is not None else None
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def from_json(cls, path: str) -> "LayoutModel":
        """
        Load from JSON produced by vision/dxf_layout.py.
        Schema: {"positions": [[x,y], ...], "radii": [r, ...]}
        The "radii" key is optional.
        """
        with open(path) as f:
            data = json.load(f)
        pts = np.array(data["positions"], dtype=np.float32)
        radii = np.array(data["radii"], dtype=np.float32) if "radii" in data else None
        return cls(pts, radii)

    # ------------------------------------------------------------------
    # Core: fit transform from detections to template
    # ------------------------------------------------------------------

    def fit_indexed(self, image_points: np.ndarray, layout_indices: List[int]) -> bool:
        """
        Fit the affine transform from explicit correspondences:
        image_points[k] corresponds to template[layout_indices[k]].

        This is used when we already KNOW which detected circle maps to which
        template electrode (e.g. after alignment, or when re-aligning from
        circles that carry their layout_index). Because correspondences are
        explicit rather than guessed by nearest-neighbour, off-image template
        points cannot corrupt the result.

        Returns True if at least 2 correspondences yielded a transform.
        """
        result = fit_layout_affine(self, image_points, layout_indices, ransac_reproj_threshold=5.0)
        return result is not None

    def project_all(self) -> np.ndarray:
        """
        Project all template positions into image space using the current
        transform. Returns (N, 2) array of (x, y) positions. Positions are
        NEVER clamped to the image; off-image electrodes keep their true
        extrapolated coordinates.
        """
        if self.transform is None:
            raise RuntimeError("Call fit_indexed()/fit_layout_affine() before project_all()")
        pts = self.template.reshape(-1, 1, 2)
        projected = cv2.transform(pts, self.transform)
        return projected.reshape(-1, 2)


def fit_layout_affine(
    layout: LayoutModel,
    image_points: Sequence[Tuple[float, float]],
    layout_indices: Sequence[int],
    *,
    ransac_reproj_threshold: float = 8.0,
) -> Optional[Tuple[np.ndarray, Optional[np.ndarray]]]:
    """
    RANSAC-fit a full 6-DOF affine transform (independent x/y scale + shear
    + rotation + translation -- not just a rotation+uniform-scale
    similarity transform) mapping ``layout.template`` (physical mm space)
    to ``image_points`` (pixel space), from explicit correspondences
    ``image_points[k] <-> layout.template[layout_indices[k]]``. This means
    a shear/non-uniform-scale correction dialed in via the manual nudge
    (``apply_manual_nudge``) persists through live tracking instead of
    being overwritten by the next automatic drift re-fit (both the manual
    pre-accept fit and ``vision/electrode_drift.py``'s continuous
    post-accept re-fit call this same function).

    Needs at least 3 non-collinear correspondences (6 unknowns); callers
    with fewer must gate before calling (e.g. gui.py's manual alignment UI
    already requires 3 pairs; the continuous drift re-fit requires at
    least ``VISION_DRIFT_MIN_CONFIDENT_ELECTRODES``, default 4).

    Falls back to an unconstrained LMEDS fit for tiny point sets where RANSAC
    cannot find a consensus (LMEDS has no inlier concept, so the returned
    mask is None in that case). On success, sets ``layout.transform`` and
    returns ``(matrix, inlier_mask)``; returns None (leaving
    ``layout.transform`` unchanged) on failure. ``inlier_mask`` is an (N, 1)
    array of 0/1 -- the only real headless trust signal available for a fit,
    since detected circles carry no calibrated confidence score (see
    ``vision/electrode_drift.py``).

    Unlike the source project's ``LayoutAlignmentTool._fit_from_pairs``, this
    function does not itself refine ``image_points`` via a Hough ROI search
    -- callers that want that refinement (e.g. the semi-manual alignment
    workflow, where the user's click is only approximate) should call
    ``vision.electrode_drift.detect_circle_in_roi`` on each point first and
    pass the refined centers in.
    """
    if len(image_points) < 3 or len(image_points) != len(layout_indices):
        return None
    tmpl = layout.template[np.asarray(layout_indices, dtype=int)].astype(np.float32)
    img = np.asarray(image_points, dtype=np.float32)

    M, inliers = cv2.estimateAffine2D(
        tmpl, img, method=cv2.RANSAC, ransacReprojThreshold=ransac_reproj_threshold
    )
    if M is None:
        # Fall back to a full (unconstrained) estimate for tiny sets.
        M, inliers = cv2.estimateAffine2D(tmpl, img, method=cv2.LMEDS)
        inliers = None
    if M is None:
        return None
    layout.transform = M
    return M, inliers


def apply_manual_nudge(
    base_transform: np.ndarray,
    *,
    scale_x_permille: float = 0,
    scale_y_permille: float = 0,
    angle_deg_x10: float = 0,
    translate_x_px: float = 0,
    translate_y_px: float = 0,
    shear_x_permille: float = 0,
    shear_y_permille: float = 0,
    center: Tuple[float, float],
) -> np.ndarray:
    """
    Apply a scale/rotation/shear/translation nudge on top of
    ``base_transform``. Scale, rotation, and shear pivot around ``center``
    (typically the image center). Returns a new 2x3 affine matrix; does not
    mutate ``base_transform``.

    Parameter units match the source project's trackbar convention so a
    Tkinter ``ttk.Scale`` widget can drive these directly: ``scale_x_permille``/
    ``scale_y_permille`` of +-50 is +-5%, ``angle_deg_x10`` of +-100 is +-10
    degrees. Scale is applied independently per axis (scale-then-rotate:
    ``R = Rotation(angle) @ diag(scale_x, scale_y)``) so a layout that's
    stretched differently in X than Y (e.g. from a slightly non-square
    camera pixel aspect) can be corrected without distorting the other axis.

    ``shear_x_permille``/``shear_y_permille`` (+-100 is +-10%) approximate
    the keystone/foreshortening a tilted camera view would produce, as a
    shear on the existing affine transform -- a true camera tilt is a
    projective (homography) effect, not a linear one, but a shear is a good
    first-order approximation over the modest field of view and tilt angles
    this manual nudge is meant to correct, and it keeps the whole
    layout-alignment pipeline (project_all, radius estimation, the RANSAC
    fit) working with a plain 2x3 affine matrix throughout.
    """
    cx, cy = center
    d_scale_x = 1.0 + scale_x_permille / 1000.0
    d_scale_y = 1.0 + scale_y_permille / 1000.0
    d_angle = angle_deg_x10 / 10.0
    d_tx = float(translate_x_px)
    d_ty = float(translate_y_px)
    shear_x = shear_x_permille / 1000.0
    shear_y = shear_y_permille / 1000.0

    rad = np.radians(d_angle)
    cos_a, sin_a = np.cos(rad), np.sin(rad)

    # Rotation+scale, then shear (tilt approximation), combined into one
    # 2x2 linear map L, pivoted about (cx, cy) and composed with a
    # translation -- general form for any 2x2 L:
    #   tx = cx - (L00*cx + L01*cy) + d_tx
    #   ty = cy - (L10*cx + L11*cy) + d_ty
    # which reduces to the original rotation-only formula when L is a pure
    # rotation+scale (shear_x = shear_y = 0), and to the original uniform
    # -scale formula when scale_x = scale_y.
    R = np.array(
        [[d_scale_x * cos_a, -d_scale_y * sin_a],
         [d_scale_x * sin_a, d_scale_y * cos_a]],
        dtype=np.float64,
    )
    Sh = np.array([[1.0, shear_x], [shear_y, 1.0]], dtype=np.float64)
    L = Sh @ R
    N = np.array(
        [
            [L[0, 0], L[0, 1], cx - (L[0, 0] * cx + L[0, 1] * cy) + d_tx],
            [L[1, 0], L[1, 1], cy - (L[1, 0] * cx + L[1, 1] * cy) + d_ty],
        ],
        dtype=np.float64,
    )

    M3 = np.vstack([np.asarray(base_transform, dtype=np.float64), [0.0, 0.0, 1.0]])
    N3 = np.vstack([N, [0.0, 0.0, 1.0]])
    C = (N3 @ M3)[:2]
    return C.astype(np.float32)


def estimate_proj_radius(layout: LayoutModel, layout_idx: int, transform: np.ndarray) -> int:
    """
    Estimate the pixel radius of layout circle ``layout_idx`` after applying
    ``transform``. Uses the scale factor extracted from the matrix, applied
    to the template radius if available, otherwise falls back to a fraction
    of the nearest-neighbour template spacing.
    """
    scale = float(np.sqrt(abs(transform[0, 0] * transform[1, 1] - transform[0, 1] * transform[1, 0])))
    if layout.template_radii is not None:
        return max(4, int(layout.template_radii[layout_idx] * scale))
    pts = layout.template
    if len(pts) > 1:
        dists = np.linalg.norm(pts - pts[layout_idx], axis=1)
        dists = dists[dists > 0]
        nn_dist = float(dists.min()) if len(dists) else 20.0
        return max(4, int(nn_dist * scale * 0.35))
    return 10


def project_all_circles(layout: LayoutModel) -> List[dict]:
    """
    Return {"center": (x, y), "radius": r} for every layout electrode,
    projected into image-pixel space via ``layout.transform``. Requires
    ``layout.transform`` to already be set (via ``fit_layout_affine``/
    ``fit_indexed``).
    """
    if layout.transform is None:
        raise RuntimeError("Call fit_layout_affine()/fit_indexed() before project_all_circles()")
    proj = layout.project_all()
    result = []
    for i, (px, py) in enumerate(proj):
        r = estimate_proj_radius(layout, i, layout.transform)
        result.append({"center": (float(px), float(py)), "radius": r})
    return result
