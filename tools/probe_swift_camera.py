# -*- coding: utf-8 -*-
"""
Probe microscope/USB cameras from Python/OpenCV on the lab PC.

This script is intentionally practical:
    - try common Windows OpenCV backends (`CAP_DSHOW`, `CAP_MSMF`)
    - scan a bounded set of camera indices
    - capture a few frames if a device opens
    - save a JSON summary and optional snapshot image

Use this to answer:
    "Can the Swift Easy View / 1.3MP USB2.0 camera be accessed directly from
     Python without the vendor GUI?"
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import List, Optional

import cv2


PROJECT_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_DIR / "results"


@dataclass
class CameraProbeAttempt:
    backend_name: str
    backend_code: int
    index: int
    opened: bool
    read_ok: bool
    frame_width: Optional[int] = None
    frame_height: Optional[int] = None
    mean_bgr: Optional[List[float]] = None
    std_bgr: Optional[List[float]] = None
    nonzero_fraction: Optional[float] = None
    frame_looks_blank: Optional[bool] = None
    snapshot_path: Optional[str] = None
    error: Optional[str] = None


def _probe_once(index: int, backend_name: str, backend_code: int, out_dir: Path) -> CameraProbeAttempt:
    cap = None
    try:
        cap = cv2.VideoCapture(index, backend_code)
        opened = bool(cap and cap.isOpened())
        if not opened:
            return CameraProbeAttempt(
                backend_name=backend_name,
                backend_code=int(backend_code),
                index=index,
                opened=False,
                read_ok=False,
            )

        # Give Windows camera graph a moment to start streaming.
        time.sleep(0.4)

        frame = None
        read_ok = False
        for _ in range(8):
            ok, candidate = cap.read()
            if ok and candidate is not None and candidate.size:
                frame = candidate
                read_ok = True
                break
            time.sleep(0.15)

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or None
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or None

        if not read_ok or frame is None:
            return CameraProbeAttempt(
                backend_name=backend_name,
                backend_code=int(backend_code),
                index=index,
                opened=True,
                read_ok=False,
                frame_width=width,
                frame_height=height,
                error="device opened but no valid frame was returned",
            )

        snapshot_path = out_dir / f"{backend_name.lower()}_index{index}.png"
        cv2.imwrite(str(snapshot_path), frame)
        mean_bgr = [float(v) for v in frame.mean(axis=(0, 1))]
        std_bgr = [float(v) for v in frame.std(axis=(0, 1))]
        nonzero_fraction = float((frame > 0).any(axis=2).mean())
        frame_looks_blank = bool(nonzero_fraction < 0.001 and max(mean_bgr) < 1.0)

        return CameraProbeAttempt(
            backend_name=backend_name,
            backend_code=int(backend_code),
            index=index,
            opened=True,
            read_ok=True,
            frame_width=int(frame.shape[1]),
            frame_height=int(frame.shape[0]),
            mean_bgr=mean_bgr,
            std_bgr=std_bgr,
            nonzero_fraction=nonzero_fraction,
            frame_looks_blank=frame_looks_blank,
            snapshot_path=str(snapshot_path),
        )
    except Exception as exc:
        return CameraProbeAttempt(
            backend_name=backend_name,
            backend_code=int(backend_code),
            index=index,
            opened=False,
            read_ok=False,
            error=str(exc),
        )
    finally:
        if cap is not None:
            with contextlib.suppress(Exception):
                cap.release()


def probe_cameras(max_index: int = 6):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS_DIR / f"camera_probe_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    backends = [
        ("ANY", cv2.CAP_ANY),
        ("DSHOW", cv2.CAP_DSHOW),
        ("MSMF", cv2.CAP_MSMF),
    ]

    attempts: List[CameraProbeAttempt] = []
    for backend_name, backend_code in backends:
        for index in range(max_index + 1):
            attempts.append(_probe_once(index, backend_name, backend_code, out_dir))

    summary = {
        "timestamp": timestamp,
        "python_executable": sys.executable,
        "opencv_version": cv2.__version__,
        "attempts": [asdict(item) for item in attempts],
        "successful_attempts": [asdict(item) for item in attempts if item.read_ok],
        "notes": {
            "interpretation": (
                "A successful attempt means Python/OpenCV can pull frames directly "
                "from that camera/backend combination without Swift Easy View."
            )
        },
    }

    summary_path = out_dir / "camera_probe_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(summary_path)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Probe microscope cameras via OpenCV.")
    parser.add_argument("--max-index", type=int, default=6, help="Highest camera index to try.")
    args = parser.parse_args()
    probe_cameras(max_index=args.max_index)
