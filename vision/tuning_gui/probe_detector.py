# -*- coding: utf-8 -*-
"""
Probe tip detection.

Vendored from the sibling "Image Detection" project's probe_detector.py (see
vision/tuning_gui/__init__.py for why this subpackage exists at all) -- this
is the FULLER version probe_gui.py depends on, with strategy C (user-drawn
edge lines) that this project's own, separately-ported vision/probe_detector.py
dropped. Deliberately kept as its own separate copy rather than importing
from vision.probe_detector, so the interactive tuner and the production
detector can't accidentally diverge from under each other.

Deviation from the original: ProbeTip is inlined below instead of imported
from the sibling's models.py, which also defines Circle/LayoutModel --
this project already has different, incompatible versions of those two in
vision/layout_alignment.py, and only ProbeTip is actually needed here.

ProbeDetector locates a downward-pointing probe tip using one of two
strategies:

  A. Convexity defect  - the probe forms a notch in a coherent region
                         (an electrode disc it occludes, OR the probe body
                         itself where it contrasts with the background).
                         The tip is the deepest concavity in that region's
                         boundary.
  C. Line intersection - user draws the two probe edges; the tip is their
                         extrapolated intersection.  When lines are set they
                         override strategy A.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class ProbeTip:
    x: float
    y: float
    confidence: float = 0.0
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
    Detects a downward-pointing probe tip.

    Strategy A - Convexity defect
    -----------------------------
    Threshold the ROI, find the largest coherent region, and locate the
    deepest convexity defect in its boundary.  The far-point of that defect
    is the probe tip.

    The threshold polarity is not assumed: the method tries BOTH the bright
    region and the dark region and keeps whichever yields a stronger,
    more dominant defect.  This means it works whether the probe occludes a
    bright electrode (tip is a dark notch in a bright disc) OR the probe has
    better contrast with the background than the electrode does (tip is a
    bright wedge against a darker surround).

    Confidence = depth_of_deepest_defect / (depth_of_second_deepest + 1).
    A high ratio means one defect dominates, which is the probe notch.

    Strategy C - Line intersection
    ------------------------------
    The user draws the two probe edges; the extrapolated intersection is the
    tip.  Set via set_probe_lines(); when active it overrides strategy A.
    """

    def __init__(self,
                 clahe_clip: float = 4.0,
                 clahe_tile: int = 4,
                 bilateral_d: int = 9,
                 bilateral_sigma: float = 75.0,
                 ema_alpha: float = 0.3,
                 invert: bool = False):
        self._clahe_clip    = clahe_clip
        self._clahe_tile    = clahe_tile
        self._bil_d         = bilateral_d
        self._bil_sigma     = bilateral_sigma
        self.ema_alpha      = ema_alpha
        # If True, invert the grayscale before preprocessing.  Toggled
        # manually (there is no automatic polarity detection).  Use when the
        # probe is darker than the background.
        self.invert = invert
        # Strategy C: user-drawn lines whose intersection is the tip.
        # Each line is ((x1,y1),(x2,y2)) in image coordinates.
        self._probe_lines: Optional[list] = None

        self.tip: Optional[ProbeTip] = None
        self._last_strategy: str = ""

    # -- line annotation API ------------------------------------------------
    def set_probe_lines(self, line1: tuple, line2: tuple):
        """
        Store two user-drawn lines that define the probe edges.
        Each line is ((x1,y1),(x2,y2)) in image coordinates.
        Once set, strategy C overrides strategy A automatically.
        """
        self._probe_lines = [line1, line2]
        self._last_strategy = "lines"

    def clear_probe_lines(self):
        """Remove the line annotation and revert to strategy A."""
        self._probe_lines = None

    @staticmethod
    def _line_intersection(line1: tuple, line2: tuple) -> Optional[tuple]:
        """
        Return the intersection point of two lines, each defined by two
        points ((x1,y1),(x2,y2)).  Uses the standard parametric formula
        so the intersection can be outside the drawn segment (extrapolated).
        Returns None if the lines are parallel.
        """
        (x1, y1), (x2, y2) = line1
        (x3, y3), (x4, y4) = line2

        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-6:
            return None   # parallel

        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        ix = x1 + t * (x2 - x1)
        iy = y1 + t * (y2 - y1)
        return (float(ix), float(iy))

    # -- Strategy C: user-drawn line intersection ---------------------------
    def _detect_lines(self) -> Optional[tuple]:
        """
        Compute the probe tip as the intersection of the two stored edge
        lines.  Confidence is fixed at 10.0 (higher than strategy A) to
        indicate an explicit user annotation.  Returns None if lines are
        not set or are parallel.
        """
        if self._probe_lines is None:
            return None
        pt = self._line_intersection(*self._probe_lines)
        if pt is None:
            return None
        return (pt[0], pt[1]), 10.0

    # -- preprocessing ------------------------------------------------------
    def _preprocess(self, gray: np.ndarray) -> np.ndarray:
        if self.invert:
            gray = cv2.bitwise_not(gray)
        clahe = cv2.createCLAHE(clipLimit=self._clahe_clip,
                                 tileGridSize=(self._clahe_tile,
                                               self._clahe_tile))
        enhanced = clahe.apply(gray)
        return cv2.bilateralFilter(enhanced, self._bil_d,
                                   self._bil_sigma, self._bil_sigma)

    # -- convexity defect on a single binary region -------------------------
    def _best_defect(self, binary: np.ndarray) -> Optional[tuple]:
        """
        Given a binary image, find the largest coherent region and return
        (tip_xy, confidence) for its deepest convexity defect, or None.
        """
        # A region must cover a small minimum fraction of the ROI to be a
        # plausible electrode disc / probe body rather than speckle noise.
        MIN_REGION_AREA_FRACTION = 0.05
        total_px = binary.shape[0] * binary.shape[1]
        region_px = int(np.sum(binary > 0))
        if region_px < total_px * MIN_REGION_AREA_FRACTION:
            return None

        cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_NONE)
        if not cnts:
            return None
        cnt = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(cnt) < 50:
            return None

        # Require the region to be reasonably compact (an electrode disc or
        # a probe body), not a thin irregular sliver.
        area  = cv2.contourArea(cnt)
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
        # version; normalize so indexing below is version-independent (same
        # fix as vision/probe_detector.py's own copy of this method).
        defects = defects.reshape(-1, 4)

        depths = defects[:, 3] / 256.0
        order  = np.argsort(depths)[::-1]
        d1 = depths[order[0]]
        d2 = depths[order[1]] if len(order) > 1 else 1.0
        confidence = float(d1 / (d2 + 1.0))

        far_idx = defects[order[0], 2]
        tip_xy  = tuple(cnt[far_idx][0].tolist())
        return tip_xy, confidence

    # -- Strategy A: convexity defect, polarity-agnostic --------------------
    def _detect_convexity(self, proc: np.ndarray) -> Optional[tuple]:
        """
        Find the probe tip as the deepest convexity defect, trying BOTH
        threshold polarities and keeping whichever gives the more dominant
        defect.  Does not assume the tip sits on an electrode.
        Returns (tip_xy, confidence) or None.
        """
        _, bright = cv2.threshold(proc, 0, 255,
                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dark = cv2.bitwise_not(bright)

        best = None
        for binary in (bright, dark):
            result = self._best_defect(binary)
            if result is not None and (best is None or result[1] > best[1]):
                best = result
        return best

    # -- main detect --------------------------------------------------------
    def detect(self, frame: np.ndarray,
               roi: Optional[tuple] = None) -> Optional[ProbeTip]:
        """
        Detect the probe tip in frame (or within roi=(x1,y1,x2,y2)).

        Returns the current ProbeTip (position smoothed across frames),
        or None only on the very first call if nothing is found at all.
        On subsequent calls a lost detection returns the last smoothed
        position with detected=False.
        """
        gray = (frame if frame.ndim == 2
                else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))

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

        # Strategy C (user-drawn lines) overrides strategy A.
        result = self._detect_lines()
        if result is not None:
            self._last_strategy = "lines"
        else:
            result = self._detect_convexity(proc)
            self._last_strategy = "convexity" if result else "none"

        if result is None:
            if self.tip is not None:
                self.tip.detected   = False
                self.tip.confidence = 0.0
            return self.tip

        local_xy, confidence = result
        tip_x = float(local_xy[0] + offset[0])
        tip_y = float(local_xy[1] + offset[1])

        if self.tip is None:
            self.tip = ProbeTip(x=tip_x, y=tip_y,
                                confidence=confidence, detected=True)
        else:
            self.tip.update_smoothed(tip_x, tip_y, self.ema_alpha)
            self.tip.x          = tip_x
            self.tip.y          = tip_y
            self.tip.detected   = True
            self.tip.confidence = confidence

        return self.tip

    # -- drawing ------------------------------------------------------------
    def draw(self, frame: np.ndarray,
             colour_detected=(0, 255, 180),
             colour_lost=(0, 100, 255),
             colour_lines=(80, 180, 255),
             draw_tip: bool = True) -> np.ndarray:
        """
        Draw the two user-drawn edge lines (if strategy C is active) and,
        when draw_tip is True, the probe tip crosshair and strategy label.

        Callers that resize the output afterwards can pass draw_tip=False and
        draw their own constant-size crosshair so it stays visible.
        """
        out = frame.copy()
        h, w = out.shape[:2]

        if self._probe_lines is not None:
            for (x1, y1), (x2, y2) in self._probe_lines:
                dx, dy = x2 - x1, y2 - y1
                if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                    continue
                ts = []
                for t in ([(-x1/dx) if abs(dx) > 1e-6 else None,
                            ((w-1-x1)/dx) if abs(dx) > 1e-6 else None,
                            (-y1/dy) if abs(dy) > 1e-6 else None,
                            ((h-1-y1)/dy) if abs(dy) > 1e-6 else None]):
                    if t is not None:
                        px, py = x1 + t*dx, y1 + t*dy
                        if -1 <= px <= w and -1 <= py <= h:
                            ts.append(t)
                if len(ts) >= 2:
                    ts.sort()
                    p_start = (int(x1 + ts[0]*dx),  int(y1 + ts[0]*dy))
                    p_end   = (int(x1 + ts[-1]*dx), int(y1 + ts[-1]*dy))
                    cv2.line(out, p_start, p_end, colour_lines, 1, cv2.LINE_AA)

        if self.tip is None or not draw_tip:
            return out

        cx = int(round(self.tip.smoothed_x))
        cy = int(round(self.tip.smoothed_y))
        col = colour_detected if self.tip.detected else colour_lost
        size = 14
        cv2.line(out, (cx - size, cy), (cx + size, cy), col, 2)
        cv2.line(out, (cx, cy - size), (cx, cy + size), col, 2)
        cv2.circle(out, (cx, cy), 4, col, -1)
        label = f"probe [{self._last_strategy}] {self.tip.confidence:.2f}"
        cv2.putText(out, label, (cx + size + 3, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
        return out
