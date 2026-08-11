# -*- coding: utf-8 -*-
"""
Minimal live preview for microscope/USB cameras using OpenCV.

This is meant as the next practical step after `probe_swift_camera.py`:
if a backend/index opens, this viewer lets us confirm whether we get a real
moving microscope image, a blank frame, or an unstable stream.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import cv2


PROJECT_DIR = Path(__file__).resolve().parent.parent

BACKENDS = {
    "any": cv2.CAP_ANY,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
}


def open_camera(index: int, backend_name: str):
    backend = BACKENDS[backend_name]
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index={index} backend={backend_name}")
    time.sleep(0.4)
    return cap


def main():
    parser = argparse.ArgumentParser(description="Live preview for Swift / USB microscope camera.")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="dshow")
    parser.add_argument("--save-frame", help="Optional path to save the latest shown frame when quitting.")
    args = parser.parse_args()

    cap = open_camera(args.index, args.backend)
    window_name = f"Swift Camera Preview ({args.backend}:{args.index})"
    last_frame = None

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("Frame read failed; retrying...")
                time.sleep(0.1)
                continue
            last_frame = frame
            cv2.imshow(window_name, frame)
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
