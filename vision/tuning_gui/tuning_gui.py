# -*- coding: utf-8 -*-
"""
Circle/Hough electrode parameter tuning window.

Native Tkinter replacement for the old cv2-window tuning GUI (see
vision/tuning_gui/__init__.py for why). CircleTuningWindow is a plain
tk.Toplevel: it never calls cv2.namedWindow/imshow/createTrackbar/
waitKey/etc, so it needs no GUI-capable OpenCV build at all -- every
image-processing call it makes (CLAHE, bilateral filter, Canny,
HoughCircles) is a plain cv2 imgproc function, present in headless OpenCV
builds too. Display goes through the same PIL/ImageTk PhotoImage pipeline
gui.py's own Image Monitor tab already uses.

Shows a 2x2 live preview (CLAHE, bilateral, Canny edges, Hough circles)
with a slider for every preprocessing and Hough parameter, and saves the
result via vision/tuning_gui/stored_params.py's save_tuning_params (same
<image>_params.json format the old cv2 tuner wrote, still consumable by
vision/circle_detector.py::load_hough_params / the main tab's "Load Hough
Params" button, unchanged).
"""

import json
import os

import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog

from .stored_params import save_tuning_params


class CircleTuningWindow(tk.Toplevel):
    """
    Interactive parameter tuning window using Tkinter sliders.

    Layout: a control column (10 sliders + short description captions,
    Save/Load/Close buttons) beside a live 2x2 preview panel:

      +-----------------+-----------------+
      | 1. CLAHE        | 2. Bilateral    |
      |    contrast     |    smoothing    |
      +-----------------+-----------------+
      | 3. Canny edges  | 4. Detected     |
      |    (param1)     |    circles      |
      +-----------------+-----------------+

    If image_path is given, "Save Params" writes next to it (via
    save_tuning_params); if params_path is given, sliders start from those
    saved values.
    """

    # (state key, slider label, lo, hi, default, description)
    _SLIDERS = [
        ("clahe_clip_x10", "CLAHE clip x10", 1, 80, 20,
         "Contrast boost strength (x10). Higher = more local contrast + noise."),
        ("clahe_tile", "CLAHE tile size", 2, 32, 8,
         "CLAHE grid size. ~electrode diameter; smaller = more local."),
        ("bf_d", "BF d", 1, 25, 9,
         "Smoothing kernel diameter. Higher = stronger, slower smoothing."),
        ("bf_sigma", "BF sigma", 5, 200, 75,
         "Smoothing tolerance. Higher blurs more but softens edges."),
        ("dp_x10", "Hough dp x10", 5, 30, 10,
         "Accumulator resolution (x10). 10 = full res; higher = coarser."),
        ("param1", "Hough param1", 5, 300, 50,
         "Canny high threshold. Watch panel 3: raise to cut noisy edges."),
        ("param2", "Hough param2", 5, 300, 30,
         "Accumulator vote threshold. Higher = fewer, surer circles."),
        ("min_dist_x10", "Min dist xr", 5, 50, 25,
         "Min centre spacing = value/10 x radius. Prevents duplicates."),
        ("min_radius", "Min radius", 1, 200, 10,
         "Smallest circle radius (px) accepted by Hough."),
        ("max_radius", "Max radius", 1, 500, 50,
         "Largest circle radius (px) accepted by Hough."),
    ]

    _PANEL_CELL_W = 460
    _PANEL_CELL_H = 340

    def __init__(self, master, frame_bgr, *, image_path=None, params_path=None, status_var=None):
        super().__init__(master)
        self.title('Circle / Hough Parameter Tuning')
        self._status_var = status_var
        self._image_path = image_path
        self._gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        self._current_values = None
        self._vars = {}
        self._preview_photo = None

        defaults = {key: default for key, _label, _lo, _hi, default, _desc in self._SLIDERS}
        if params_path:
            defaults.update(self._read_saved_defaults(params_path))

        body = ttk.Frame(self)
        body.pack(fill='both', expand=True, padx=8, pady=8)

        controls = ttk.Frame(body)
        controls.pack(side='left', fill='y', padx=(0, 8))

        for key, label, lo, hi, default, desc in self._SLIDERS:
            var = tk.IntVar(value=defaults.get(key, default))
            self._vars[key] = var
            row = ttk.Frame(controls)
            row.pack(fill='x', pady=(4, 0))
            ttk.Label(row, text=label, width=15, anchor='w').pack(side='left')
            tk.Scale(
                row, from_=lo, to=hi, orient='horizontal', variable=var,
                length=170, showvalue=True,
                command=lambda _v: self._refresh(),
            ).pack(side='left', fill='x', expand=True)
            ttk.Label(
                controls, text=desc, wraplength=260, foreground='#666',
                font=('TkDefaultFont', 8),
            ).pack(fill='x', pady=(0, 4))

        btn_row = ttk.Frame(controls)
        btn_row.pack(fill='x', pady=(8, 0))
        ttk.Button(btn_row, text='Save Params', command=self._save_params).pack(side='left')
        ttk.Button(btn_row, text='Load...', command=self._load_params_dialog).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Close', command=self.destroy).pack(side='left', padx=4)

        self._preview_label = ttk.Label(body)
        self._preview_label.pack(side='left', fill='both', expand=True)

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

    # -- parameter I/O --------------------------------------------------

    @staticmethod
    def _read_saved_defaults(params_path):
        """Read preprocessing.*/hough.* directly from a saved params JSON
        into this window's slider-state keys. A plain dict read (not
        stored_params.load_params, which remaps keys for constructing an
        ElectrodeTracker and doesn't carry dp/param2/min-dist) so every
        slider round-trips."""
        try:
            with open(params_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        pre = data.get('preprocessing', {})
        hough = data.get('hough', {})
        out = {}
        if 'clahe_clip' in pre:
            out['clahe_clip_x10'] = int(round(pre['clahe_clip'] * 10))
        if 'clahe_tile' in pre:
            out['clahe_tile'] = int(pre['clahe_tile'])
        if 'bilateral_d' in pre:
            out['bf_d'] = int(pre['bilateral_d'])
        if 'bilateral_sigma' in pre:
            out['bf_sigma'] = int(round(pre['bilateral_sigma']))
        if 'dp' in hough:
            out['dp_x10'] = int(round(hough['dp'] * 10))
        if 'param1' in hough:
            out['param1'] = int(hough['param1'])
        if 'param2' in hough:
            out['param2'] = int(hough['param2'])
        if 'min_dist_radius_factor' in hough:
            out['min_dist_x10'] = int(round(hough['min_dist_radius_factor'] * 10))
        if 'min_radius' in hough:
            out['min_radius'] = int(hough['min_radius'])
        if 'max_radius' in hough:
            out['max_radius'] = int(hough['max_radius'])
        return out

    def _load_params_dialog(self):
        # parent=self: without it, this dialog attaches to the wrong
        # window and closing it can break window stacking/focus enough
        # that this Toplevel appears to vanish behind the main app on
        # macOS -- a well-known Tkinter gotcha for dialogs opened from a
        # Toplevel rather than the root window.
        path = filedialog.askopenfilename(
            parent=self,
            title='Load circle tuning params',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
        )
        if not path:
            return
        for key, value in self._read_saved_defaults(path).items():
            if key in self._vars:
                self._vars[key].set(value)
        self._refresh()

    def _save_params(self):
        if self._current_values is None:
            return
        if not self._image_path:
            if self._status_var is not None:
                self._status_var.set('Tuning GUI: no source snapshot to save alongside')
            return
        out_path = save_tuning_params(self._current_values, self._image_path)
        if self._status_var is not None:
            self._status_var.set(
                f'Tuning GUI: saved {out_path} -- use Load Hough Params on the main tab to apply it'
            )

    # -- preview ----------------------------------------------------------

    def _refresh(self):
        v = {key: var.get() for key, var in self._vars.items()}
        clahe_clip = max(0.1, v['clahe_clip_x10'] / 10.0)
        clahe_tile = max(2, v['clahe_tile'])
        bil_d = max(1, v['bf_d'])
        bil_sigma = max(5.0, float(v['bf_sigma']))
        dp = max(0.5, v['dp_x10'] / 10.0)
        p1 = max(5, v['param1'])
        p2 = max(5, v['param2'])
        min_dist_x = max(0.5, v['min_dist_x10'] / 10.0)
        r_min = max(1, v['min_radius'])
        r_max = max(r_min + 1, v['max_radius'])

        self._current_values = (clahe_clip, clahe_tile, bil_d, bil_sigma,
                                 dp, p1, p2, min_dist_x, r_min, r_max)

        clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(clahe_tile, clahe_tile))
        enhanced = clahe.apply(self._gray)
        smoothed = cv2.bilateralFilter(enhanced, bil_d, bil_sigma, bil_sigma)
        edges = cv2.Canny(smoothed, p1 // 2, p1)

        r_mid = (r_min + r_max) // 2
        min_dist = max(1, int(r_mid * min_dist_x))
        raw = cv2.HoughCircles(
            smoothed, cv2.HOUGH_GRADIENT, dp=dp, minDist=min_dist,
            param1=p1, param2=p2, minRadius=r_min, maxRadius=r_max)

        result_img = cv2.cvtColor(smoothed, cv2.COLOR_GRAY2BGR)
        n_found = 0
        r_min_found = r_max_found = None
        if raw is not None:
            circles = np.round(raw[0]).astype(int)
            n_found = len(circles)
            radii = [int(r) for _, _, r in circles]
            r_min_found, r_max_found = min(radii), max(radii)
            for i, (cx, cy, r) in enumerate(circles):
                ratio = i / max(n_found - 1, 1)
                colour = (0, int(255 * (1 - ratio)), int(255 * ratio))
                cv2.circle(result_img, (cx, cy), r, colour, 2)
                cv2.circle(result_img, (cx, cy), 3, colour, -1)

        status = f"{n_found} circle(s) found"
        cv2.putText(result_img, status, (8, result_img.shape[0] - 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 220, 255), 2, cv2.LINE_AA)
        radius_text = (f"radius: min={r_min_found}px  max={r_max_found}px"
                        if r_min_found is not None else "radius: n/a")
        cv2.putText(result_img, radius_text, (8, result_img.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (160, 255, 200), 2, cv2.LINE_AA)

        panel = self._make_panel([
            (enhanced, f"1 CLAHE clip={clahe_clip:.1f} tile={clahe_tile}x{clahe_tile}"),
            (smoothed, f"2 Bilateral d={bil_d} sigma={bil_sigma:.0f}"),
            (edges, f"3 Canny param1={p1} low={p1 // 2}"),
            (result_img, f"4 Hough p1={p1} p2={p2} dp={dp:.1f}"),
        ])

        image = _to_pil_image(panel)
        self._preview_photo = _to_photo_image(image)
        self._preview_label.configure(image=self._preview_photo)

    @classmethod
    def _make_panel(cls, imgs_labels):
        """Arrange 4 (image, label) pairs into a 2x2 BGR grid."""
        cells = []
        for img, label in imgs_labels:
            h, w = img.shape[:2]
            scale = min(cls._PANEL_CELL_W / w, cls._PANEL_CELL_H / h, 1.0)
            cw, ch = max(int(w * scale), 1), max(int(h * scale), 1)
            cell = (cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
                    if img.ndim == 2 else img.copy())
            cell = cv2.resize(cell, (cw, ch))
            banner = np.zeros((22, cw, 3), dtype=np.uint8)
            cv2.putText(banner, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (200, 220, 255), 1, cv2.LINE_AA)
            cells.append(np.vstack([banner, cell]))
        row1 = np.hstack(cells[:2])
        row2 = np.hstack(cells[2:])
        return np.vstack([row1, row2])


def _to_pil_image(bgr):
    from PIL import Image
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def _to_photo_image(pil_image):
    from PIL import ImageTk
    return ImageTk.PhotoImage(pil_image)
