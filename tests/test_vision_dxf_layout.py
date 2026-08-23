# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    import ezdxf
    _HAS_EZDXF = True
except Exception:
    _HAS_EZDXF = False

from vision.layout_alignment import LayoutModel

if _HAS_EZDXF:
    from vision.dxf_layout import (
        estimate_spacing_stats,
        extract_circles_from_dxf,
        shift_to_origin,
        warn_if_spacing_implausible,
        write_json,
    )


def _write_test_dxf(path, circles):
    """circles: list of (x, y, radius, layer)."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    layers = {layer for *_rest, layer in circles}
    for layer in layers:
        if layer not in doc.layers:
            doc.layers.add(layer)
    for x, y, r, layer in circles:
        msp.add_circle((x, y), radius=r, dxfattribs={"layer": layer})
    doc.saveas(path)


@unittest.skipUnless(_HAS_EZDXF, "ezdxf not installed")
class ExtractCirclesFromDxfTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".dxf")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_extracts_all_circles_default(self):
        _write_test_dxf(self.path, [(0, 0, 5, "E"), (10, 0, 5, "E"), (0, 10, 5, "E")])
        circles = extract_circles_from_dxf(self.path)
        self.assertEqual(len(circles), 3)

    def test_flip_y_negates_y_by_default(self):
        _write_test_dxf(self.path, [(0, 10, 5, "E")])
        circles = extract_circles_from_dxf(self.path, flip_y=True)
        self.assertEqual(circles[0]["y"], -10.0)

        circles_noflip = extract_circles_from_dxf(self.path, flip_y=False)
        self.assertEqual(circles_noflip[0]["y"], 10.0)

    def test_layer_filter(self):
        _write_test_dxf(self.path, [(0, 0, 5, "ELECTRODES"), (10, 0, 5, "OTHER")])
        circles = extract_circles_from_dxf(self.path, layer_filter="electrodes")
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0]["x"], 0.0)

    def test_radius_filter(self):
        _write_test_dxf(self.path, [(0, 0, 5, "E"), (10, 0, 0.1, "E"), (20, 0, 50, "E")])
        circles = extract_circles_from_dxf(self.path, min_radius=1.0, max_radius=10.0)
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0]["radius"], 5.0)

    def test_no_circles_returns_empty_list(self):
        doc = ezdxf.new()
        doc.saveas(self.path)
        circles = extract_circles_from_dxf(self.path)
        self.assertEqual(circles, [])


class ShiftToOriginTests(unittest.TestCase):
    def test_shifts_bounding_box_to_zero(self):
        circles = [{"x": 5, "y": 10, "radius": 1}, {"x": -5, "y": 20, "radius": 1}]
        shifted = shift_to_origin(circles) if _HAS_EZDXF else None
        if not _HAS_EZDXF:
            self.skipTest("ezdxf not installed")
        xs = [c["x"] for c in shifted]
        ys = [c["y"] for c in shifted]
        self.assertEqual(min(xs), 0.0)
        self.assertEqual(min(ys), 0.0)
        self.assertEqual(max(xs), 10.0)
        self.assertEqual(max(ys), 10.0)

    def test_empty_list_returns_empty(self):
        if not _HAS_EZDXF:
            self.skipTest("ezdxf not installed")
        self.assertEqual(shift_to_origin([]), [])


@unittest.skipUnless(_HAS_EZDXF, "ezdxf not installed")
class SpacingSanityCheckTests(unittest.TestCase):
    def test_estimate_spacing_stats_uniform_grid(self):
        circles = [{"x": x * 60.0, "y": 0.0, "radius": 8} for x in range(4)]
        stats = estimate_spacing_stats(circles)
        self.assertAlmostEqual(stats["median_nn_spacing"], 60.0, places=3)

    def test_warns_when_spacing_too_small_for_mm(self):
        circles = [{"x": x * 0.001, "y": 0.0, "radius": 0.0001} for x in range(4)]
        warning = warn_if_spacing_implausible(circles)
        self.assertIsNotNone(warning)
        self.assertIn("small", warning)

    def test_warns_when_spacing_too_large_for_mm(self):
        circles = [{"x": x * 500.0, "y": 0.0, "radius": 20} for x in range(4)]
        warning = warn_if_spacing_implausible(circles)
        self.assertIsNotNone(warning)
        self.assertIn("large", warning)

    def test_no_warning_for_plausible_spacing(self):
        circles = [{"x": x * 10.0, "y": 0.0, "radius": 2} for x in range(4)]
        warning = warn_if_spacing_implausible(circles)
        self.assertIsNone(warning)


@unittest.skipUnless(_HAS_EZDXF, "ezdxf not installed")
class WriteJsonRoundTripTests(unittest.TestCase):
    def test_write_json_round_trips_through_layout_model(self):
        circles = [{"x": 0.0, "y": 0.0, "radius": 8.0}, {"x": 60.0, "y": 0.0, "radius": 8.0}]
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            write_json(circles, path)
            with open(path) as f:
                payload = json.load(f)
            self.assertEqual(payload["count"], 2)
            layout = LayoutModel.from_json(path)
            self.assertEqual(layout.n, 2)
            self.assertEqual(list(layout.template[1]), [60.0, 0.0])
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
