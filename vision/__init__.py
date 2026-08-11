# -*- coding: utf-8 -*-
"""Image-analysis helpers for future microscope-guided full-auto workflows."""

from .electrode_mapper import (
    CircleDetection,
    RegistrationResult,
    annotate_detections,
    compute_homography,
    detect_electrode_map,
    load_image_rgb,
    manual_pick_points,
    project_points,
    save_detection_outputs,
)

