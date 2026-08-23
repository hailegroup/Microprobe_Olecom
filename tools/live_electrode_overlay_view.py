# -*- coding: utf-8 -*-
"""
Live microscope preview with electrode-circle overlay.

This is the first practical bridge from the Swift/OpenCV camera feed into
image-guided automation groundwork:
    - open the microscope camera directly from Python/OpenCV
    - periodically run the existing electrode detector on the current frame
    - draw the detected electrode circles / labels over the live video

The detector can be throttled so live preview remains responsive even if
circle detection is moderately expensive.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import cv2

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision.circle_detector import load_hough_params
from vision.electrode_mapper import (
    annotate_detections,
    detect_live_microscope_electrode_map_rgb,
    filter_live_overlay_detections,
)


BACKENDS = {
    "any": cv2.CAP_ANY,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
}


def open_camera(index: int, backend_name: str):
    cap = cv2.VideoCapture(index, BACKENDS[backend_name])
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index={index} backend={backend_name}")
    time.sleep(0.4)
    return cap


def _detector_kwargs_from_params_file(path):
    """
    Map a *_params.json file (same format the sibling "Image Detection"
    project's tuning GUI saves) into detect_live_microscope_electrode_map_rgb
    kwargs. Returns {} (use the detector's own built-in defaults) if path is
    None.
    """
    if path is None:
        return {}
    params = load_hough_params(path)
    expected_radius = float(params["expected_radius"])
    radius_tolerance = float(params["radius_tolerance"])
    return {
        "clahe_clip": params["clahe_clip"],
        "clahe_tile": params["clahe_tile"],
        "param1": params["param1"],
        "param2": params["param2"],
        "min_radius_px": max(1, expected_radius - radius_tolerance),
        "max_radius_px": expected_radius + radius_tolerance,
    }


def main():
    parser = argparse.ArgumentParser(description="Live microscope view with electrode overlay.")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="dshow")
    parser.add_argument(
        "--params",
        help=(
            "Optional *_params.json file (from the sibling 'Image Detection' "
            "project's tuning GUI) to retune the live detector's CLAHE/Hough "
            "parameters. Omit to use the built-in live-camera-tuned defaults."
        ),
    )
    parser.add_argument("--sample-side-mm", type=float, default=10.0)
    parser.add_argument("--detect-every", type=int, default=12, help="Run electrode detection every N frames.")
    parser.add_argument("--size-group", choices=["all", "large", "small"], default="all")
    parser.add_argument("--save-frame", help="Optional path to save the most recent frame on quit.")
    args = parser.parse_args()

    cap = open_camera(args.index, args.backend)
    frame_idx = 0
    last_frame = None
    last_overlay = None
    last_error = None
    last_detection_count = 0
    window_name = f"Electrode Overlay ({args.backend}:{args.index})"

    detector_kwargs = _detector_kwargs_from_params_file(args.params)

    try:
        while True:
            ok, frame_bgr = cap.read()
            if not ok or frame_bgr is None:
                time.sleep(0.05)
                continue

            last_frame = frame_bgr.copy()
            frame_idx += 1
            display_bgr = frame_bgr.copy()

            if last_overlay is None or frame_idx % max(args.detect_every, 1) == 1:
                try:
                    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                    detections = detect_live_microscope_electrode_map_rgb(
                        frame_rgb,
                        sample_side_mm=args.sample_side_mm,
                        size_group=args.size_group,
                        **detector_kwargs,
                    )
                    overlay_detections = filter_live_overlay_detections(detections)
                    annotated_rgb = annotate_detections(
                        frame_rgb,
                        overlay_detections,
                        annotate_labels=False,
                    )
                    last_overlay = cv2.cvtColor(annotated_rgb, cv2.COLOR_RGB2BGR)
                    last_detection_count = len(overlay_detections)
                    last_error = None
                except Exception as exc:
                    last_overlay = None
                    last_detection_count = 0
                    last_error = str(exc)

            if last_overlay is not None:
                display_bgr = last_overlay.copy()

            status = f"detections={last_detection_count}"
            if last_error:
                status = f"detections=0 | {last_error}"
            cv2.putText(
                display_bgr,
                status,
                (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow(window_name, display_bgr)

            key = cv2.waitKey(20) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord("s") and args.save_frame and last_frame is not None:
                cv2.imwrite(args.save_frame, last_frame)
                print(f"Saved frame to {args.save_frame}")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        if args.save_frame and last_frame is not None and not Path(args.save_frame).exists():
            cv2.imwrite(args.save_frame, last_frame)
            print(f"Saved last frame to {args.save_frame}")


if __name__ == "__main__":
    main()
