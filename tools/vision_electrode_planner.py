# -*- coding: utf-8 -*-
"""
CLI helper for future image-guided full-auto planning.

Examples
--------
1. Detect electrode candidates directly from an OM image:
   python tools/vision_electrode_planner.py --image "C:\\...\\OM\\photo.png"

2. Detect only the larger-circle group:
   python tools/vision_electrode_planner.py --image "C:\\...\\OM\\photo.png" --size-group large

3. Register a design image to a target OM image using 4 manual clicks on each:
   python tools/vision_electrode_planner.py --image "C:\\...\\OM\\photo.png" --design "C:\\...\\OM\\Design.png" --manual-register
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision.electrode_mapper import (
    annotate_detections,
    build_projected_detection_table,
    detect_electrode_map,
    load_image_rgb,
    manual_pick_points,
    register_design_to_target,
    save_detection_outputs,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Detect and prepare microscope electrode maps.")
    parser.add_argument("--image", required=True, help="Target OM / sample image.")
    parser.add_argument("--design", help="Optional design image for manual registration.")
    parser.add_argument(
        "--outdir",
        help="Output directory. Defaults to result/vision_planning/<image_stem>/",
    )
    parser.add_argument(
        "--size-group",
        choices=["all", "large", "small"],
        default="all",
        help="Filter detections by detected radius group.",
    )
    parser.add_argument(
        "--sample-side-mm",
        type=float,
        default=10.0,
        help="Physical side length of the sample square in mm (default: 10 mm).",
    )
    parser.add_argument(
        "--manual-register",
        action="store_true",
        help="When --design is given, collect 4 manual correspondences on design and image and project design centers.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    image_path = Path(args.image)
    outdir = (
        Path(args.outdir)
        if args.outdir
        else PROJECT_DIR / "result" / "vision_planning" / image_path.stem
    )

    detections = detect_electrode_map(
        image_path,
        sample_side_mm=args.sample_side_mm,
        size_group=args.size_group,
    )
    annotated = annotate_detections(load_image_rgb(image_path), detections)
    csv_path, overlay_path = save_detection_outputs(outdir, image_path, detections, annotated)

    print(f"Detected {len(detections)} electrode candidates")
    print(csv_path)
    print(overlay_path)

    if args.design and args.manual_register:
        design_path = Path(args.design)
        design_map = detect_electrode_map(
            design_path,
            sample_side_mm=args.sample_side_mm,
            size_group=args.size_group,
        )
        design_points = manual_pick_points(
            design_path,
            n_points=4,
            title="Click 4 reference points on the design image, then press Enter",
        )
        target_points = manual_pick_points(
            image_path,
            n_points=4,
            title="Click the matching 4 reference points on the OM image, then press Enter",
        )
        registration = register_design_to_target(design_map, design_points, target_points)
        projected = build_projected_detection_table(design_map, registration)
        projected_annotated = annotate_detections(load_image_rgb(image_path), projected)
        projected_csv, projected_overlay = save_detection_outputs(
            outdir, f"{image_path.stem}_projected", projected, projected_annotated
        )
        print(projected_csv)
        print(projected_overlay)


if __name__ == "__main__":
    main()

