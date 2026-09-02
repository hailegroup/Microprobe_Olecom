# -*- coding: utf-8 -*-
"""
CAD DXF -> electrode layout JSON.

Ported from the sibling "Image Detection" (Microelectrode Tracker) project's
``dxf_to_layout.py``. Only the pure extraction/output functions are ported;
the matplotlib-only ``plot_layout``/CLI ``main`` are intentionally left out
-- the GUI shows a status summary instead of a plot popup.

The output schema matches ``vision.layout_alignment.LayoutModel.from_json``:
``{"positions": [[x,y], ...], "radii": [r, ...], "count": N, "notes": ...}``,
in whatever physical unit the DXF itself uses (expected: mm, to match the
pixel<->stage-mm calibration elsewhere in this project).

``ezdxf`` is an optional dependency (see requirements-win7-optional-vision.txt)
-- only needed for this module, not for the rest of the vision stack.
"""

from __future__ import annotations

import json
import math
from typing import List, Optional

import ezdxf


def extract_circles_from_dxf(
    dxf_path: str,
    layer_filter: Optional[str] = None,
    min_radius: float = 0.0,
    max_radius: float = float("inf"),
    flip_y: bool = True,
) -> List[dict]:
    """
    Return a list of dicts: {"x": float, "y": float, "radius": float}
    for every circle in the DXF that passes the filters.

    flip_y (default True): DXF uses a right-handed coordinate system where Y
    increases upward, while screen/image coordinates have Y increasing
    downward. Without flipping, the layout appears mirrored vertically when
    overlaid on an image. Pass flip_y=False only if your DXF already uses a
    top-left origin convention.

    Searches:
      1. The modelspace directly for CIRCLE entities.
      2. INSERT entities -- references to a reusable group of entities
         called a 'block.' The INSERT transform defines the position,
         rotation, and x/y scale.
    """
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    circles: List[dict] = []

    def layer_ok(entity) -> bool:
        if layer_filter is None:
            return True
        return entity.dxf.layer.lower() == layer_filter.lower()

    def add_circle(cx: float, cy: float, r: float):
        if not (min_radius <= r <= max_radius):
            return
        # DXF Y-axis points up; image Y-axis points down.
        # Negate Y here; caller must shift so min-Y becomes 0.
        if flip_y:
            cy = -cy
        circles.append({"x": cx, "y": cy, "radius": r})

    # -- 1. Search CIRCLE entities --------------------------------------
    for entity in msp.query("CIRCLE"):
        if not layer_ok(entity):
            continue
        c = entity.dxf.center
        add_circle(c.x, c.y, entity.dxf.radius)

    # -- 2. Search INSERT entities ---------------------------------------
    for insert in msp.query("INSERT"):
        if not layer_ok(insert):
            continue

        block_name = insert.dxf.name
        block = doc.blocks.get(block_name)

        insert_to_world = insert.matrix44()
        sx, sy = insert.dxf.xscale, insert.dxf.yscale

        for entity in block:
            if not layer_ok(entity):
                continue
            if entity.dxftype() != "CIRCLE":
                continue

            center = insert_to_world.transform(entity.dxf.center)
            # If x and y scale are unequal (elliptical), use the geometric
            # mean of the x/y scale.
            r = entity.dxf.radius * math.sqrt(abs(sx * sy))
            add_circle(center.x, center.y, r)

    return circles


def shift_to_origin(circles: List[dict]) -> List[dict]:
    """Translate so the bounding box minimum is at (0, 0)."""
    if not circles:
        return circles
    min_x = min(c["x"] for c in circles)
    min_y = min(c["y"] for c in circles)
    return [{"x": c["x"] - min_x, "y": c["y"] - min_y, "radius": c["radius"]} for c in circles]


def estimate_spacing_stats(circles: List[dict]) -> Optional[dict]:
    """
    Estimate nearest-neighbour center-to-center spacing across the layout.

    This project's DXF source has already caused one real unit mix-up
    (a DXF reporting inches while the intended geometry was mm) -- this is a
    cheap sanity check callers can use to catch that class of mistake before
    trusting the layout for calibration, not a hard validation. Returns None
    if there are fewer than 2 circles.
    """
    if len(circles) < 2:
        return None
    xs = [c["x"] for c in circles]
    ys = [c["y"] for c in circles]
    nn_dists = []
    for i in range(len(circles)):
        best = None
        for j in range(len(circles)):
            if i == j:
                continue
            d = math.hypot(xs[i] - xs[j], ys[i] - ys[j])
            if best is None or d < best:
                best = d
        if best is not None:
            nn_dists.append(best)
    nn_dists.sort()
    mid = len(nn_dists) // 2
    median_spacing = nn_dists[mid] if len(nn_dists) % 2 else (nn_dists[mid - 1] + nn_dists[mid]) / 2.0
    return {
        "median_nn_spacing": median_spacing,
        "min_nn_spacing": nn_dists[0],
        "max_nn_spacing": nn_dists[-1],
    }


def warn_if_spacing_implausible(
    circles: List[dict],
    *,
    min_expected_spacing_mm: float = 0.05,
    max_expected_spacing_mm: float = 50.0,
) -> Optional[str]:
    """
    Return a human-readable warning string if the layout's median
    nearest-neighbour spacing falls outside a plausible electrode-array
    range in mm, or None if it looks fine (or there isn't enough data to
    judge). Callers (e.g. the GUI's "Load DXF Layout" workflow) should
    surface this as a status warning rather than treating it as fatal --
    unusual samples are legitimate too.
    """
    stats = estimate_spacing_stats(circles)
    if stats is None:
        return None
    spacing = stats["median_nn_spacing"]
    if spacing < min_expected_spacing_mm:
        return (
            f"Median electrode spacing ({spacing:.4g}) is unusually small for mm units "
            f"(< {min_expected_spacing_mm} mm) -- check whether the DXF is actually in "
            f"a different unit (e.g. inches)."
        )
    if spacing > max_expected_spacing_mm:
        return (
            f"Median electrode spacing ({spacing:.4g}) is unusually large for mm units "
            f"(> {max_expected_spacing_mm} mm) -- check whether the DXF is actually in "
            f"a different unit (e.g. inches) or the wrong layer was extracted."
        )
    return None


def write_json(circles: List[dict], path: str):
    """
    Write layout JSON compatible with
    ``vision.layout_alignment.LayoutModel.from_json``.
    """
    content = {
        "positions": [[c["x"], c["y"]] for c in circles],
        "radii": [c["radius"] for c in circles],
        "count": len(circles),
        "notes": "Generated by vision/dxf_layout.py",
    }
    with open(path, "w") as f:
        json.dump(content, f, indent=2)
