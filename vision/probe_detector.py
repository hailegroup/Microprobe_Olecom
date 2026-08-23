# -*- coding: utf-8 -*-
"""
Probe-tip pixel detection.

Ported from the sibling "Image Detection" (Microelectrode Tracker) project's
``probe_detector.py``/``models.py``. Pure OpenCV/NumPy, no GUI coupling.

ProbeDetector locates a downward-pointing probe tip via convexity defect: the
probe forms a notch in a coherent region (an electrode disc it occludes, OR
the probe body itself where it contrasts with the background); the tip is
the deepest concavity in that region's boundary.

``ProbeTip`` has no ``confidence`` field: the source project's version was an
unbounded, uncalibrated ratio of convexity-defect depths that was never a
real probability, so it was not useful as a gating signal and is dropped
here. An internal dominance score (deepest defect depth vs. the
second-deepest) picks between the bright-region and dark-region threshold
candidates -- that score never leaves ``_detect_convexity``/``_best_defect``.

Detection also assumes a roughly bimodal, high-contrast ROI (it thresholds
with Otsu, which needs a clearly bimodal histogram to produce a clean split).
Low/uneven contrast between the probe and its background will make the
detected blob small/irregular and get rejected outright, or let a spurious
region dominate. There is no automatic dark/light polarity detection --
``invert`` is a manual toggle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class ProbeTip:
    x: float
    y: float
    detected: bool = False
    smoothed_x: float = 0.0
    smoothed_y: float = 0.0

    def __post_init__(self):
        self.smoothed_x = self.x
        self.smoothed_y = self.y

    def update_smoothed(self, new_x: float, new_y: float, alpha: float = 0.3):
        self.smoothed_x = alpha * new_x + (1 - alpha) * self.smoothed_x
        self.smoothed_y = alpha * new_y + (1 - alpha) * self.smoothed_y


class ProbeDetector:
    """
    Detects a downward-pointing probe tip via convexity defect.

    Threshold the ROI, find the largest coherent region, and locate the
    deepest convexity defect in its boundary. The far-point of that defect
    is the probe tip.

    The threshold polarity is not assumed: the method tries BOTH the bright
    region and the dark region and keeps whichever yields a stronger, more
    dominant defect. This means it works whether the probe occludes a bright
    electrode (tip is a dark notch in a bright disc) OR the probe has better
    contrast with the background than the electrode does (tip is a bright
    wedge against a darker surround).

    Internally, a dominance score = depth_of_deepest_defect /
    (depth_of_second_deepest + 1) picks between the bright- and dark-region
    candidates (a high ratio means one defect clearly dominates, which is
    the probe notch); this score is not exposed on the returned ProbeTip.
    """

    def __init__(
        self,
        clahe_clip: float = 4.0,
        clahe_tile: int = 4,
        bilateral_d: int = 9,
        bilateral_sigma: float = 75.0,
        ema_alpha: float = 0.3,
        invert: bool = False,
    ):
        self._clahe_clip = clahe_clip
        self._clahe_tile = clahe_tile
        self._bil_d = bilateral_d
        self._bil_sigma = bilateral_sigma
        self.ema_alpha = ema_alpha
        # If True, invert the grayscale before preprocessing. Toggled
        # manually (there is no automatic polarity detection). Use when the
        # probe is darker than the background.
        self.invert = invert

        self.tip: Optional[ProbeTip] = None
        self._last_strategy: str = ""

    # -- preprocessing --------------------------------------------------------
    def _preprocess(self, gray: np.ndarray) -> np.ndarray:
        if self.invert:
            gray = cv2.bitwise_not(gray)
        clahe = cv2.createCLAHE(
            clipLimit=self._clahe_clip,
            tileGridSize=(self._clahe_tile, self._clahe_tile),
        )
        enhanced = clahe.apply(gray)
        return cv2.bilateralFilter(enhanced, self._bil_d, self._bil_sigma, self._bil_sigma)

    # -- convexity defect on a single binary region ---------------------------
    def _best_defect(self, binary: np.ndarray) -> Optional[tuple]:
        """
        Given a binary image, find the largest coherent region and return
        (tip_xy, dominance_score) for its deepest convexity defect, or None.
        ``dominance_score`` is used only to pick between the bright- and
        dark-region candidates in ``_detect_convexity`` -- it is not part of
        the public API and is discarded by ``detect()``.
        """
        # A region must cover a small minimum fraction of the ROI to be a
        # plausible electrode disc / probe body rather than speckle noise.
        MIN_REGION_AREA_FRACTION = 0.05
        total_px = binary.shape[0] * binary.shape[1]
        region_px = int(np.sum(binary > 0))
        if region_px < total_px * MIN_REGION_AREA_FRACTION:
            return None

        cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cnts:
            return None
        cnt = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(cnt) < 50:
            return None

        # Require the region to be reasonably compact (an electrode disc or
        # a probe body), not a thin irregular sliver.
        area = cv2.contourArea(cnt)
        perim = cv2.arcLength(cnt, True)
        circularity = 4 * np.pi * area / (perim ** 2) if perim > 0 else 0
        if circularity < 0.3:
            return None

        hull_idx = cv2.convexHull(cnt, returnPoints=False)
        if hull_idx is None or len(hull_idx) < 3:
            return None

        defects = cv2.convexityDefects(cnt, hull_idx)
        if defects is None or len(defects) < 1:
            return None
        # OpenCV has returned this as either (n, 1, 4) or (n, 4) depending on
        # version; normalize so indexing below is version-independent.
        defects = defects.reshape(-1, 4)

        depths = defects[:, 3] / 256.0
        order = np.argsort(depths)[::-1]
        d1 = depths[order[0]]
        d2 = depths[order[1]] if len(order) > 1 else 1.0
        dominance_score = float(d1 / (d2 + 1.0))

        far_idx = defects[order[0], 2]
        tip_xy = tuple(cnt[far_idx][0].tolist())
        return tip_xy, dominance_score

    # -- Strategy A: convexity defect, polarity-agnostic ----------------------
    def _detect_convexity(self, proc: np.ndarray) -> Optional[tuple]:
        """
        Find the probe tip as the deepest convexity defect, trying BOTH
        threshold polarities and keeping whichever gives the more dominant
        defect. Does not assume the tip sits on an electrode.
        Returns (tip_xy, dominance_score) or None; the caller (``detect()``)
        discards ``dominance_score``.
        """
        _, bright = cv2.threshold(proc, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dark = cv2.bitwise_not(bright)

        best = None
        for binary in (bright, dark):
            result = self._best_defect(binary)
            if result is not None and (best is None or result[1] > best[1]):
                best = result
        return best

    # -- main detect ------------------------------------------------------------
    def detect(self, frame: np.ndarray, roi: Optional[tuple] = None) -> Optional[ProbeTip]:
        """
        Detect the probe tip in frame (or within roi=(x1,y1,x2,y2)).

        Returns the current ProbeTip (position smoothed across frames), or
        None only on the very first call if nothing is found at all. On
        subsequent calls a lost detection returns the last smoothed position
        with detected=False.
        """
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if roi is not None:
            x1, y1 = max(0, roi[0]), max(0, roi[1])
            x2 = min(gray.shape[1], roi[2])
            y2 = min(gray.shape[0], roi[3])
            search = gray[y1:y2, x1:x2]
            offset = (x1, y1)
        else:
            search = gray
            offset = (0, 0)

        proc = self._preprocess(search)

        convexity_result = self._detect_convexity(proc)
        self._last_strategy = "convexity" if convexity_result else "none"
        local_xy = convexity_result[0] if convexity_result is not None else None

        if local_xy is None:
            if self.tip is not None:
                self.tip.detected = False
            return self.tip

        tip_x = float(local_xy[0] + offset[0])
        tip_y = float(local_xy[1] + offset[1])

        if self.tip is None:
            self.tip = ProbeTip(x=tip_x, y=tip_y, detected=True)
        else:
            self.tip.update_smoothed(tip_x, tip_y, self.ema_alpha)
            self.tip.x = tip_x
            self.tip.y = tip_y
            self.tip.detected = True

        return self.tip

    # -- drawing ------------------------------------------------------------------
    def draw(
        self,
        frame: np.ndarray,
        colour_detected=(0, 255, 180),
        colour_lost=(0, 100, 255),
        draw_tip: bool = True,
    ) -> np.ndarray:
        """
        Draw the probe tip crosshair and strategy label, when draw_tip is
        True.

        Callers that resize the output afterwards can pass draw_tip=False
        and draw their own constant-size crosshair so it stays visible.
        """
        out = frame.copy()

        if self.tip is None or not draw_tip:
            return out

        cx = int(round(self.tip.smoothed_x))
        cy = int(round(self.tip.smoothed_y))
        col = colour_detected if self.tip.detected else colour_lost
        size = 14
        cv2.line(out, (cx - size, cy), (cx + size, cy), col, 2)
        cv2.line(out, (cx, cy - size), (cx, cy + size), col, 2)
        cv2.circle(out, (cx, cy), 4, col, -1)
        label = f"probe [{self._last_strategy}]"
        cv2.putText(out, label, (cx + size + 3, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
        return out


def load_probe_params(path: str) -> dict:
    """
    Load a ``<image>_probe_params.json`` file saved by
    ``vision/tuning_gui/probe_gui.py``'s interactive tuner into a flat kwargs
    dict for ``ProbeDetector.__init__``. Mirrors
    ``vision/circle_detector.py::load_hough_params``'s shape.

    Only ``preprocessing.*`` and ``probe.invert`` are read -- the saved
    file's ``probe.roi``/``probe.lines`` fields describe the tuner's own ROI
    selection and edge-line "strategy C", which this project's
    ``ProbeDetector`` above doesn't have (see this module's docstring); Probe
    ROI in this project is the separate live-view drag rectangle
    (``gui.py``'s ``_image_probe_roi``), not something restored from a
    params file. Missing keys fall back to ``ProbeDetector.__init__``'s own
    defaults. Raises on a missing/corrupt file (same tolerant-load convention
    as ``load_hough_params`` -- a silently-wrong params file is worse than a
    visible failure here).
    """
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    pre = data.get('preprocessing', {})
    probe = data.get('probe', {})

    return {
        'clahe_clip': float(pre.get('clahe_clip', 4.0)),
        'clahe_tile': int(pre.get('clahe_tile', 4)),
        'bilateral_d': int(pre.get('bilateral_d', 9)),
        'bilateral_sigma': float(pre.get('bilateral_sigma', 75.0)),
        'invert': bool(probe.get('invert', False)),
    }
