# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision_stage_mapper import (
    PixelStageReference,
    generate_bilinear_grid_pixels,
    map_pixels_to_stage_xy,
    pixel_to_stage_xy,
    solve_stage_affine_calibration,
)


class VisionStageMapperTests(unittest.TestCase):
    def test_affine_calibration_maps_pixels_to_stage(self):
        refs = [
            PixelStageReference(0, 0, 10, 20),
            PixelStageReference(100, 0, 20, 20),
            PixelStageReference(0, 200, 10, 40),
        ]
        calibration = solve_stage_affine_calibration(refs)
        stage_x, stage_y = pixel_to_stage_xy(calibration, 50, 100)
        self.assertAlmostEqual(stage_x, 15.0, places=6)
        self.assertAlmostEqual(stage_y, 30.0, places=6)

    def test_bilinear_grid_generation(self):
        pixels = generate_bilinear_grid_pixels(
            top_left=(0, 0),
            top_right=(10, 0),
            bottom_left=(0, 20),
            bottom_right=(10, 20),
            rows=3,
            cols=2,
        )
        self.assertEqual(
            pixels,
            [
                (0.0, 0.0),
                (10.0, 0.0),
                (0.0, 10.0),
                (10.0, 10.0),
                (0.0, 20.0),
                (10.0, 20.0),
            ],
        )

    def test_map_pixels_to_stage_batch(self):
        refs = [
            PixelStageReference(0, 0, 0, 0),
            PixelStageReference(10, 0, 1, 0),
            PixelStageReference(0, 10, 0, 2),
        ]
        calibration = solve_stage_affine_calibration(refs)
        mapped = map_pixels_to_stage_xy(calibration, [(0, 0), (10, 10)])
        self.assertEqual(len(mapped), 2)
        self.assertAlmostEqual(mapped[1][0], 1.0, places=6)
        self.assertAlmostEqual(mapped[1][1], 2.0, places=6)


if __name__ == "__main__":
    unittest.main()
