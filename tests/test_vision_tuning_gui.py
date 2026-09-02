# -*- coding: utf-8 -*-
"""
Tests for the native Tkinter tuning windows (vision/tuning_gui/). These
construct real (withdrawn) Tk widgets -- there's no headless/GUI-backend
concern here since Tkinter, unlike the old cv2-window tuner these replace,
doesn't need a GUI-capable OpenCV build at all.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import cv2

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import tkinter as tk

from vision.tuning_gui.tuning_gui import CircleTuningWindow
from vision.tuning_gui.probe_gui import ProbeTuningWindow
from vision.circle_detector import load_hough_params
from vision.probe_detector import load_probe_params


def _synthetic_frame(w=200, h=150):
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.circle(frame, (w // 2, h // 2), min(w, h) // 4, (220, 220, 220), -1)
    return frame


class TuningGuiWindowTestsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"no Tk display available: {exc}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()


class CircleTuningWindowTests(TuningGuiWindowTestsBase):
    def test_constructs_and_computes_initial_preview(self):
        win = CircleTuningWindow(self.root, _synthetic_frame())
        try:
            win.update()
            self.assertIsNotNone(win._current_values)
            self.assertEqual(len(win._current_values), 10)
            self.assertIsNotNone(win._preview_photo)
        finally:
            win.destroy()

    def test_slider_change_updates_preview_values(self):
        win = CircleTuningWindow(self.root, _synthetic_frame())
        try:
            win._vars['min_radius'].set(20)
            win._vars['max_radius'].set(40)
            win._refresh()
            _clahe_clip, _tile, _d, _sigma, _dp, _p1, _p2, _min_dist_x, r_min, r_max = win._current_values
            self.assertEqual(r_min, 20)
            self.assertEqual(r_max, 40)
        finally:
            win.destroy()

    def test_save_params_writes_json_loadable_by_production_loader(self):
        win = CircleTuningWindow(self.root, _synthetic_frame())
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                image_path = os.path.join(tmpdir, 'circle.png')
                cv2.imwrite(image_path, _synthetic_frame())
                win._image_path = image_path
                status_var = tk.StringVar(value='')
                win._status_var = status_var

                win._save_params()

                out_path = os.path.join(tmpdir, 'circle_params.json')
                self.assertTrue(os.path.exists(out_path))
                self.assertIn(out_path, status_var.get())

                kwargs = load_hough_params(out_path)
                self.assertIn('clahe_clip', kwargs)
                self.assertIn('param1', kwargs)
        finally:
            win.destroy()

    def test_save_params_without_image_path_reports_status_and_does_not_raise(self):
        win = CircleTuningWindow(self.root, _synthetic_frame())
        try:
            status_var = tk.StringVar(value='')
            win._status_var = status_var
            win._image_path = None

            win._save_params()

            self.assertIn('no source snapshot', status_var.get())
        finally:
            win.destroy()

    def test_params_path_preloads_sliders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            params_path = os.path.join(tmpdir, 'circle_params.json')
            with open(params_path, 'w') as f:
                json.dump({
                    'preprocessing': {'clahe_clip': 3.5, 'clahe_tile': 12,
                                       'bilateral_d': 15, 'bilateral_sigma': 90.0},
                    'hough': {'dp': 1.5, 'param1': 80, 'param2': 45,
                              'min_dist_radius_factor': 3.0,
                              'min_radius': 25, 'max_radius': 60},
                }, f)

            win = CircleTuningWindow(self.root, _synthetic_frame(), params_path=params_path)
            try:
                self.assertEqual(win._vars['clahe_clip_x10'].get(), 35)
                self.assertEqual(win._vars['clahe_tile'].get(), 12)
                self.assertEqual(win._vars['bf_d'].get(), 15)
                self.assertEqual(win._vars['bf_sigma'].get(), 90)
                self.assertEqual(win._vars['dp_x10'].get(), 15)
                self.assertEqual(win._vars['param1'].get(), 80)
                self.assertEqual(win._vars['param2'].get(), 45)
                self.assertEqual(win._vars['min_radius'].get(), 25)
                self.assertEqual(win._vars['max_radius'].get(), 60)
            finally:
                win.destroy()

    def test_forces_live_view_redraw_on_open_and_close(self):
        calls = []
        self.root._image_force_live_view_redraw = lambda: calls.append(1)
        try:
            win = CircleTuningWindow(self.root, _synthetic_frame())
            win.update()
            self.assertEqual(len(calls), 1)
            win.destroy()
            self.root.update()
            self.assertEqual(len(calls), 2)
        finally:
            del self.root._image_force_live_view_redraw

    def test_load_params_dialog_passes_parent_self(self):
        win = CircleTuningWindow(self.root, _synthetic_frame())
        try:
            with mock.patch(
                'vision.tuning_gui.tuning_gui.filedialog.askopenfilename',
                return_value='',
            ) as mocked_dialog:
                win._load_params_dialog()
            mocked_dialog.assert_called_once()
            self.assertIs(mocked_dialog.call_args.kwargs['parent'], win)
        finally:
            win.destroy()


class ProbeTuningWindowTests(TuningGuiWindowTestsBase):
    def test_constructs_with_no_roi_selected(self):
        win = ProbeTuningWindow(self.root, _synthetic_frame())
        try:
            win.update()
            self.assertIsNone(win._roi)
            self.assertIn('Drag a rectangle', win._status_label.cget('text'))
        finally:
            win.destroy()

    def test_roi_drag_sets_roi_and_refreshes_preview(self):
        win = ProbeTuningWindow(self.root, _synthetic_frame())
        try:
            win._roi_drag_start = (10, 10)
            end_x, end_y = win._image_to_canvas(150, 100)
            event = mock.Mock(x=end_x, y=end_y)

            win._on_release(event)

            self.assertEqual(win._roi, (10, 10, 150, 100))
            self.assertIsNotNone(win._preview_photo)
            self.assertNotIn('Drag a rectangle', win._status_label.cget('text'))
        finally:
            win.destroy()

    def test_tiny_drag_does_not_set_roi(self):
        win = ProbeTuningWindow(self.root, _synthetic_frame())
        try:
            win._roi_drag_start = (10, 10)
            end_x, end_y = win._image_to_canvas(11, 11)
            event = mock.Mock(x=end_x, y=end_y)

            win._on_release(event)

            self.assertIsNone(win._roi)
        finally:
            win.destroy()

    def test_status_text_never_shows_raw_confidence_or_strategy(self):
        # A reflection off the probe visible on an electrode can create a
        # second large convexity defect -- _best_defect always returns the
        # single deepest one regardless, so the confidence ratio was never
        # actionable and is no longer shown at all (see probe_gui.py's
        # _refresh). Covers both the detected and not-detected branches.
        win = ProbeTuningWindow(self.root, _synthetic_frame())
        try:
            win._roi_drag_start = (10, 10)
            end_x, end_y = win._image_to_canvas(150, 100)
            win._on_release(mock.Mock(x=end_x, y=end_y))
            text_with_roi = win._status_label.cget('text')
            self.assertNotIn('confidence', text_with_roi)
            self.assertNotIn('strategy', text_with_roi)

            win._clear_roi()
            text_without_roi = win._status_label.cget('text')
            self.assertNotIn('confidence', text_without_roi)
            self.assertNotIn('strategy', text_without_roi)
        finally:
            win.destroy()

    def test_forces_live_view_redraw_on_open_and_close(self):
        calls = []
        self.root._image_force_live_view_redraw = lambda: calls.append(1)
        try:
            win = ProbeTuningWindow(self.root, _synthetic_frame())
            win.update()
            self.assertEqual(len(calls), 1)
            win.destroy()
            self.root.update()
            self.assertEqual(len(calls), 2)
        finally:
            del self.root._image_force_live_view_redraw

    def test_load_params_dialog_passes_parent_self(self):
        win = ProbeTuningWindow(self.root, _synthetic_frame())
        try:
            with mock.patch(
                'vision.tuning_gui.probe_gui.filedialog.askopenfilename',
                return_value='',
            ) as mocked_dialog:
                win._load_params_dialog()
            mocked_dialog.assert_called_once()
            self.assertIs(mocked_dialog.call_args.kwargs['parent'], win)
        finally:
            win.destroy()

    def test_clear_roi_resets_state(self):
        win = ProbeTuningWindow(self.root, _synthetic_frame())
        try:
            win._roi = (5, 5, 50, 50)
            win._draw_roi_rect()

            win._clear_roi()

            self.assertIsNone(win._roi)
            self.assertIsNone(win._roi_rect_id)
        finally:
            win.destroy()

    def test_save_params_requires_roi(self):
        win = ProbeTuningWindow(self.root, _synthetic_frame())
        try:
            status_var = tk.StringVar(value='')
            win._status_var = status_var
            win._image_path = '/tmp/does_not_matter.png'

            win._save_params()

            self.assertIn('draw an ROI', status_var.get())
        finally:
            win.destroy()

    def test_save_params_writes_json_loadable_by_production_loader(self):
        win = ProbeTuningWindow(self.root, _synthetic_frame())
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                image_path = os.path.join(tmpdir, 'probe.png')
                cv2.imwrite(image_path, _synthetic_frame())
                win._image_path = image_path
                win._roi = (10, 10, 100, 100)
                win._invert_var.set(True)
                status_var = tk.StringVar(value='')
                win._status_var = status_var

                win._save_params()

                out_path = os.path.join(tmpdir, 'probe_probe_params.json')
                self.assertTrue(os.path.exists(out_path))
                self.assertIn(out_path, status_var.get())

                with open(out_path) as f:
                    data = json.load(f)
                self.assertEqual(data['probe']['roi'], [10, 10, 100, 100])
                self.assertTrue(data['probe']['invert'])

                kwargs = load_probe_params(out_path)
                self.assertTrue(kwargs['invert'])
        finally:
            win.destroy()

    def test_params_path_preloads_roi_and_invert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            params_path = os.path.join(tmpdir, 'probe_params.json')
            with open(params_path, 'w') as f:
                json.dump({
                    'preprocessing': {'clahe_clip': 5.0, 'clahe_tile': 6,
                                       'bilateral_d': 11, 'bilateral_sigma': 40.0},
                    'probe': {'invert': True, 'roi': [1, 2, 3, 4], 'lines': None},
                }, f)

            win = ProbeTuningWindow(self.root, _synthetic_frame(), params_path=params_path)
            try:
                self.assertTrue(win._invert_var.get())
                self.assertEqual(win._roi, (1, 2, 3, 4))
                self.assertEqual(win._vars['clahe_clip_x10'].get(), 50)
                self.assertEqual(win._vars['bf_d'].get(), 11)
            finally:
                win.destroy()

    def test_make_panel_default_size_unchanged(self):
        # _make_panel only ever shrinks to fit a cell (scale capped at
        # 1.0), so use a square image larger than both the default and the
        # overridden cell size to actually exercise the scaling; height
        # (320) is the binding constraint of the default 360x320 cell.
        img = np.zeros((900, 900, 3), dtype=np.uint8)
        panel = ProbeTuningWindow._make_panel([(img, 'a'), (img, 'b')])
        self.assertEqual(panel.shape[1], ProbeTuningWindow._PANEL_CELL_H * 2)

    def test_make_panel_accepts_smaller_cell_size_override(self):
        # gui.py's compact Z Contact Search debug panel needs a much
        # smaller cell size than ProbeTuningWindow's own large popup
        # default -- confirms the override actually takes effect and
        # existing no-args call sites are unaffected (previous test).
        img = np.zeros((900, 900, 3), dtype=np.uint8)
        panel = ProbeTuningWindow._make_panel([(img, 'a'), (img, 'b')], cell_w=110, cell_h=110)
        self.assertEqual(panel.shape[1], 110 * 2)
        self.assertLessEqual(panel.shape[0], 110 + 22)


if __name__ == "__main__":
    unittest.main()
