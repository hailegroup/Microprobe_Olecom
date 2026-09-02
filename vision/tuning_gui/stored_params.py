# -*- coding: utf-8 -*-
"""
Parameter I/O helpers.

Vendored from the sibling "Image Detection" project's stored_params.py (see
vision/tuning_gui/__init__.py for why this subpackage exists at all).

load_params reads a *_params.json file (saved by the tuning GUIs) into a
flat dict suitable for constructing an ElectrodeTracker.  save_tuning_params
writes the electrode tuning GUI parameters back out.

Deviation from the original: save_tuning_params's output path used to be a
literal "params/" + image_path (with its extension stripped) -- correct only
when run with a cwd the caller controls and an image_path relative to it, as
the sibling project's own CLI always was. Here the output path is instead
anchored directly to the image's own directory (vision_calibration/, since
gui.py's tuning windows save their source snapshot there) with no nested
subdirectory -- gui.py and this file's caller keep every tuning-GUI
artifact (snapshot image + saved params JSON) flat in one place.
"""

import os
import json


def load_params(params_path: str) -> dict:
    """
    Load a _params.json file saved by the tuning GUI.
    Returns a flat dict with all keys needed to construct ElectrodeTracker.
    Missing keys fall back to safe defaults.
    """
    with open(params_path) as f:
        data = json.load(f)

    pre   = data.get("preprocessing", {})
    hough = data.get("hough", {})

    return {
        "clahe_clip":    pre.get("clahe_clip",   2.0),
        "clahe_tile":    int(pre.get("clahe_tile", 8)),
        "bilateral_d":   int(pre.get("bilateral_d", 9)),
        "bilateral_sigma": float(pre.get("bilateral_sigma", 75.0)),
        "param1":        int(hough.get("param1",  50)),
        "param2_roi":    int(hough.get("param2_roi",
                             max(5, int(hough.get("param2", 30)) - 10))),
        "min_radius":    int(hough.get("min_radius", 10)),
        "max_radius":    int(hough.get("max_radius", 50)),
        "radius_tolerance": max(2, int(
            (hough.get("max_radius", 50) - hough.get("min_radius", 10)) // 2)),
        "expected_radius": int(
            (hough.get("min_radius", 10) + hough.get("max_radius", 50)) // 2),
    }


def save_tuning_params(vals: tuple, image_path: str) -> str:
    """Write the current tuning parameters to a JSON sidecar file directly
    next to image_path (same directory, created if missing). Returns the
    written path."""
    (clahe_clip, clahe_tile, bil_d, bil_sigma,
     dp, p1, p2, min_dist_x, r_min, r_max) = vals

    out = {
        "source_image": image_path,
        "preprocessing": {
            "clahe_clip":  clahe_clip,
            "clahe_tile":  clahe_tile,
            "bilateral_d": bil_d,
            "bilateral_sigma": bil_sigma,
        },
        "hough": {
            "dp":          dp,
            "param1":      p1,
            "param2":      p2,
            "min_dist_radius_factor": min_dist_x,
            "min_radius":  r_min,
            "max_radius":  r_max,
        }
    }

    base = os.path.splitext(os.path.basename(image_path))[0]
    out_dir = os.path.dirname(os.path.abspath(image_path))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, base + "_params.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"[SAVED] Parameters written to {out_path}")
    return out_path
