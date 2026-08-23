# -*- coding: utf-8 -*-
"""
Probe tip detection tuning window.

Native Tkinter replacement for the old cv2-window probe tuning GUI (see
vision/tuning_gui/__init__.py for why). ProbeTuningWindow is a plain
tk.Toplevel -- it never calls cv2.namedWindow/imshow/setMouseCallback/
waitKey/etc, so it needs no GUI-capable OpenCV build. ROI selection is a
native Tkinter Canvas drag (a create_rectangle item, not a cv2 window);
preview panels are composed via cv2 imgproc calls only (CLAHE, bilateral
filter, threshold, findContours, convexHull -- all present in headless
OpenCV builds) and rendered through the same PIL/ImageTk PhotoImage
pipeline gui.py's own Image Monitor tab already uses.

Only ROI selection + strategy A (convexity defect) are exposed -- the old
cv2 tuner's line-annotator ("strategy C", draw two probe edges) is
dropped, matching this project's own production
vision/probe_detector.py::ProbeDetector, which never had it either.
ProbeDetector here (vision/tuning_gui/probe_detector.py) is still a
deliberately separate copy from that production module, so the two can't
drift into each other under shared code neither fully owns; it happens to
still carry strategy C's set_probe_lines/_detect_lines methods, just
unused by this window.
"""

import json
import os

import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog

from .probe_detector import ProbeDetector
from .tuning_gui import _to_photo_image, _to_pil_image


class ProbeTuningWindow(tk.Toplevel):
    """
    Interactive probe-tip tuning window.

    Top: the captured frame at a fixed fit-to-window scale -- drag a
    rectangle on it to select the probe ROI (native Canvas rectangle item,
    no image recompute during the drag itself).
    Bottom: two live preview panels, recomputed on ROI-drag-release and on
    any slider/checkbox change -- (1) preprocessed ROI with the detected
    tip crosshair, (2) threshold + contour + convex-hull overlay.

    If image_path is given, "Save Params" writes next to it; if
    params_path is given, sliders/ROI/invert start from those saved
    values.
    """

    _SLIDERS = [
        ("clahe_clip_x10", "CLAHE clip x10", 1, 80, 40,
         "Contrast boost strength (x10)."),
        ("clahe_tile", "CLAHE tile", 2, 16, 4,
         "CLAHE grid size."),
        ("bf_d", "BF d", 1, 25, 9,
         "Smoothing kernel diameter."),
        ("bf_sigma", "BF sigma", 5, 200, 75,
         "Smoothing tolerance."),
    ]

    _CANVAS_MAX_W = 640
    _CANVAS_MAX_H = 480
    _PANEL_CELL_W = 360
    _PANEL_CELL_H = 320

    def __init__(self, master, frame_bgr, *, image_path=None, params_path=None, status_var=None):
        super().__init__(master)
        self.title('Probe Tip Parameter Tuning')
        self._status_var = status_var
        self._image_path = image_path
        self._frame_bgr = frame_bgr
        self._gray_full = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        h, w = frame_bgr.shape[:2]
        self._scale = min(self._CANVAS_MAX_W / w, self._CANVAS_MAX_H / h, 1.0)
        self._disp_w, self._disp_h = max(int(w * self._scale), 1), max(int(h * self._scale), 1)

        self._roi = None
        self._roi_drag_start = None
        self._roi_rect_id = None
        self._vars = {}
        self._invert_var = tk.BooleanVar(value=False)
        self._detector = ProbeDetector()
        self._preview_photo = None
        self._canvas_photo = None

        defaults = {key: default for key, _l, _lo, _hi, default, _d in self._SLIDERS}
        probe_p = {}
        if params_path:
            defaults.update(self._read_saved_preprocessing(params_path))
            probe_p = self._read_saved_probe_section(params_path)
        if probe_p.get('invert'):
            self._invert_var.set(True)
        if probe_p.get('roi'):
            self._roi = tuple(int(v) for v in probe_p['roi'])

        body = ttk.Frame(self)
        body.pack(fill='both', expand=True, padx=8, pady=8)

        left = ttk.Frame(body)
        left.pack(side='left', fill='y')

        ttk.Label(left, text='Drag on the image to select the probe ROI').pack(anchor='w')
        self._canvas = tk.Canvas(
            left, width=self._disp_w, height=self._disp_h,
            highlightthickness=1, highlightbackground='#888',
        )
        self._canvas.pack()
        disp_bgr = cv2.resize(frame_bgr, (self._disp_w, self._disp_h))
        self._canvas_photo = _to_photo_image(_to_pil_image(disp_bgr))
        self._canvas.create_image(0, 0, anchor='nw', image=self._canvas_photo)
        self._canvas.bind('<ButtonPress-1>', self._on_press)
        self._canvas.bind('<B1-Motion>', self._on_drag)
        self._canvas.bind('<ButtonRelease-1>', self._on_release)

        controls = ttk.Frame(left)
        controls.pack(fill='x', pady=(8, 0))
        for key, label, lo, hi, default, _desc in self._SLIDERS:
            var = tk.IntVar(value=defaults.get(key, default))
            self._vars[key] = var
            row = ttk.Frame(controls)
            row.pack(fill='x', pady=(4, 0))
            ttk.Label(row, text=label, width=13, anchor='w').pack(side='left')
            tk.Scale(
                row, from_=lo, to=hi, orient='horizontal', variable=var,
                length=150, showvalue=True,
                command=lambda _v: self._refresh(),
            ).pack(side='left', fill='x', expand=True)

        ttk.Checkbutton(
            controls, text='Invert', variable=self._invert_var, command=self._refresh,
        ).pack(anchor='w', pady=(4, 0))

        btn_row = ttk.Frame(left)
        btn_row.pack(fill='x', pady=(8, 0))
        ttk.Button(btn_row, text='Save Params', command=self._save_params).pack(side='left')
        ttk.Button(btn_row, text='Load...', command=self._load_params_dialog).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Clear ROI', command=self._clear_roi).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Close', command=self.destroy).pack(side='left', padx=4)

        self._status_label = ttk.Label(left, text='', foreground='#666', wraplength=self._disp_w)
        self._status_label.pack(fill='x', pady=(6, 0))

        right = ttk.Frame(body)
        right.pack(side='left', fill='both', expand=True, padx=(8, 0))
        self._preview_label = ttk.Label(right)
        self._preview_label.pack(fill='both', expand=True)

        self._draw_roi_rect()
        self._refresh()

        # This window is a separate floating Toplevel that can overlap the
        # main app's live camera preview -- force an immediate redraw of
        # that preview now (in case opening triggered a stale-compositing
        # glitch) and again when this window closes, rather than leaving
        # the main app to self-heal on its next ~120ms render tick. See
        # gui.py::_image_force_live_view_redraw.
        if hasattr(master, '_image_force_live_view_redraw'):
            master._image_force_live_view_redraw()
            # <Destroy> bound on a Toplevel fires once per descendant widget
            # destroyed (Tk delivers it via each widget's bindtags, whose
            # 3rd tag is always its nearest Toplevel ancestor's pathname),
            # not just once for this window -- filter to only this
            # window's own destroy event.
            self.bind(
                '<Destroy>',
                lambda e: master._image_force_live_view_redraw() if e.widget is self else None,
            )

    # -- ROI drag on the canvas -----------------------------------------

    def _canvas_to_image(self, x, y):
        return int(round(x / self._scale)), int(round(y / self._scale))

    def _image_to_canvas(self, x, y):
        return x * self._scale, y * self._scale

    def _on_press(self, event):
        self._roi_drag_start = self._canvas_to_image(event.x, event.y)

    def _on_drag(self, event):
        if self._roi_drag_start is None:
            return
        x1, y1 = self._roi_drag_start
        x2, y2 = self._canvas_to_image(event.x, event.y)
        self._draw_roi_rect((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))

    def _on_release(self, event):
        if self._roi_drag_start is None:
            return
        x1, y1 = self._roi_drag_start
        x2, y2 = self._canvas_to_image(event.x, event.y)
        self._roi_drag_start = None
        roi = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if roi[2] - roi[0] > 4 and roi[3] - roi[1] > 4:
            self._roi = roi
        self._draw_roi_rect()
        self._refresh()

    def _draw_roi_rect(self, rect=None):
        rect = rect if rect is not None else self._roi
        if self._roi_rect_id is not None:
            self._canvas.delete(self._roi_rect_id)
            self._roi_rect_id = None
        if rect is None:
            return
        x1, y1 = self._image_to_canvas(rect[0], rect[1])
        x2, y2 = self._image_to_canvas(rect[2], rect[3])
        self._roi_rect_id = self._canvas.create_rectangle(x1, y1, x2, y2, outline='#50c8ff', width=2)

    def _clear_roi(self):
        self._roi = None
        self._draw_roi_rect()
        self._refresh()

    # -- parameter I/O ----------------------------------------------------

    @staticmethod
    def _read_saved_preprocessing(params_path):
        try:
            with open(params_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        pre = data.get('preprocessing', {})
        out = {}
        if 'clahe_clip' in pre:
            out['clahe_clip_x10'] = int(round(pre['clahe_clip'] * 10))
        if 'clahe_tile' in pre:
            out['clahe_tile'] = int(pre['clahe_tile'])
        if 'bilateral_d' in pre:
            out['bf_d'] = int(pre['bilateral_d'])
        if 'bilateral_sigma' in pre:
            out['bf_sigma'] = int(round(pre['bilateral_sigma']))
        return out

    @staticmethod
    def _read_saved_probe_section(params_path):
        try:
            with open(params_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data.get('probe', {})

    def _load_params_dialog(self):
        # parent=self: without it, this dialog attaches to the wrong
        # window and closing it can break window stacking/focus enough
        # that this Toplevel appears to vanish behind the main app on
        # macOS -- a well-known Tkinter gotcha for dialogs opened from a
        # Toplevel rather than the root window.
        path = filedialog.askopenfilename(
            parent=self,
            title='Load probe tuning params',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
        )
        if not path:
            return
        for key, value in self._read_saved_preprocessing(path).items():
            if key in self._vars:
                self._vars[key].set(value)
        probe_p = self._read_saved_probe_section(path)
        if 'invert' in probe_p:
            self._invert_var.set(bool(probe_p['invert']))
        if probe_p.get('roi'):
            self._roi = tuple(int(v) for v in probe_p['roi'])
            self._draw_roi_rect()
        self._refresh()

    def _save_params(self):
        if self._roi is None:
            if self._status_var is not None:
                self._status_var.set('Tuning GUI: draw an ROI before saving')
            return
        if not self._image_path:
            if self._status_var is not None:
                self._status_var.set('Tuning GUI: no source snapshot to save alongside')
            return
        clahe_clip, clahe_tile, bil_d, bil_sigma = self._current_preprocessing()
        out = {
            "source_image": self._image_path,
            "preprocessing": {
                "clahe_clip": clahe_clip, "clahe_tile": clahe_tile,
                "bilateral_d": bil_d, "bilateral_sigma": bil_sigma,
            },
            "probe": {
                "invert": bool(self._invert_var.get()),
                "roi": list(self._roi),
                "lines": None,
            },
        }
        base = os.path.splitext(os.path.basename(self._image_path))[0]
        out_dir = os.path.dirname(os.path.abspath(self._image_path))
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, base + '_probe_params.json')
        with open(out_path, 'w') as f:
            json.dump(out, f, indent=2)
        if self._status_var is not None:
            self._status_var.set(
                f'Tuning GUI: saved {out_path} -- use Load Probe Params on the main tab to apply it'
            )

    # -- preview ------------------------------------------------------------

    def _current_preprocessing(self):
        clahe_clip = max(0.1, self._vars['clahe_clip_x10'].get() / 10.0)
        clahe_tile = max(2, self._vars['clahe_tile'].get())
        bil_d = max(1, self._vars['bf_d'].get())
        bil_sigma = max(5.0, float(self._vars['bf_sigma'].get()))
        return clahe_clip, clahe_tile, bil_d, bil_sigma

    def _refresh(self):
        clahe_clip, clahe_tile, bil_d, bil_sigma = self._current_preprocessing()
        self._detector._clahe_clip = clahe_clip
        self._detector._clahe_tile = clahe_tile
        self._detector._bil_d = bil_d
        self._detector._bil_sigma = bil_sigma
        self._detector.invert = bool(self._invert_var.get())

        if self._roi is None:
            self._status_label.configure(text='Drag a rectangle on the image above to select the probe ROI.')
            self._preview_label.configure(image='')
            self._preview_photo = None
            return

        x1, y1, x2, y2 = self._roi
        roi_gray = self._gray_full[y1:y2, x1:x2]
        if roi_gray.size == 0:
            self._status_label.configure(text='ROI is empty -- draw a larger rectangle.')
            return

        tip = self._detector.detect(self._frame_bgr, roi=self._roi)
        proc_roi = self._detector._preprocess(roi_gray)

        panel1 = cv2.cvtColor(proc_roi, cv2.COLOR_GRAY2BGR)
        if tip is not None and tip.detected:
            self._draw_crosshair(panel1, tip.x - x1, tip.y - y1)

        _, thresh = cv2.threshold(proc_roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        panel2 = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
        cnts, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if cnts:
            cnt = max(cnts, key=cv2.contourArea)
            cv2.drawContours(panel2, [cnt], -1, (80, 180, 255), 1)
            hull = cv2.convexHull(cnt)
            cv2.drawContours(panel2, [hull], -1, (0, 255, 0), 1)
        if tip is not None and tip.detected:
            self._draw_crosshair(panel2, tip.x - x1, tip.y - y1, size=12)

        # No raw confidence number here: _best_defect always returns the
        # single deepest convexity defect regardless of how much it
        # dominates the second-deepest one, so a reflection off the probe
        # visible on an electrode (which can create its own large defect)
        # can make the detector lock onto the wrong point while any
        # confidence readout would have looked fine or badly -- it was
        # never actionable either way. Verify the crosshair visually and
        # redraw a tighter ROI (excluding the reflective electrode) if it
        # picked the wrong one.
        if tip is not None and tip.detected:
            status = 'Tip detected'
        else:
            status = 'No tip detected in this ROI -- try adjusting CLAHE/threshold, Invert, or redrawing the ROI.'
        self._status_label.configure(text=status)

        inv_txt = '  [inverted]' if self._invert_var.get() else ''
        panel = self._make_panel([
            (panel1, '1  ROI + tip' + inv_txt),
            (panel2, '2  threshold + contour + hull'),
        ])
        image = _to_pil_image(panel)
        self._preview_photo = _to_photo_image(image)
        self._preview_label.configure(image=self._preview_photo)

    @staticmethod
    def _draw_crosshair(img, x, y, colour=(0, 255, 180), size=14, thick=2):
        h, w = img.shape[:2]
        xi, yi = int(round(x)), int(round(y))
        if not (0 <= xi < w and 0 <= yi < h):
            return
        cv2.line(img, (xi - size, yi), (xi + size, yi), colour, thick)
        cv2.line(img, (xi, yi - size), (xi, yi + size), colour, thick)
        dot = max(1, size // 4)
        cv2.circle(img, (xi, yi), dot, colour, -1)

    @classmethod
    def _make_panel(cls, imgs_labels, cell_w=None, cell_h=None):
        cell_w = cls._PANEL_CELL_W if cell_w is None else cell_w
        cell_h = cls._PANEL_CELL_H if cell_h is None else cell_h
        cells = []
        for img, label in imgs_labels:
            h, w = img.shape[:2]
            scale = min(cell_w / w, cell_h / h, 1.0)
            cw, ch = max(int(w * scale), 1), max(int(h * scale), 1)
            cell = cv2.resize(img, (cw, ch))
            banner = np.zeros((22, cw, 3), dtype=np.uint8)
            cv2.putText(banner, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, (200, 220, 255), 1, cv2.LINE_AA)
            cells.append(np.vstack([banner, cell]))
        max_h = max(c.shape[0] for c in cells)
        padded = []
        for c in cells:
            if c.shape[0] < max_h:
                pad = np.zeros((max_h - c.shape[0], c.shape[1], 3), dtype=np.uint8)
                c = np.vstack([c, pad])
            padded.append(c)
        return np.hstack(padded)
