# -*- coding: utf-8 -*-
"""
Export recovered / reference EIS data to a native OriginLab project (.opju)
with **editable** Nyquist and Bode graphs, using the ``originpro`` package.

Requirements
------------
``originpro`` is not a pure-Python library: it automates a locally installed
copy of OriginLab **Origin / OriginPro (2021b or newer)** over COM. That means:

  * Windows only.
  * Origin/OriginPro must be installed and licensed on the machine that runs
    this code (the free Origin Viewer cannot be automated).
  * ``pip install originpro`` (it pulls in ``pywin32``).

If Origin is not available the GUI falls back silently to the Excel + CSV
exports; nothing here is required for the core analysis to work.

The graphs are created from worksheet columns, so everything (axis scales,
titles, symbols, colours, legend) stays fully editable inside Origin.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd


def _safe_folder(name):
    """Sanitise a name for an Origin Project Explorer folder."""
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", str(name)).strip()
    return cleaned or "folder"


def _short_sheet(name):
    """Short, unique-ish worksheet name for an EIS dataset."""
    mapping = {
        "Reference PEIS": "Reference",
        "Recovered (CP->EIS)": "Recovered",
        "RQRQRQ fit": "Fit",
    }
    return mapping.get(name, str(name)[:28] or "Sheet")


def _row_label(name):
    """Short row label (e.g. 'row001') from a full row folder name."""
    m = re.match(r"(row\d+)", str(name), re.IGNORECASE)
    return m.group(1) if m else (str(name).split("_")[0] or str(name))


ORIGIN_REQUIREMENT_NOTE = (
    "Needs OriginLab Origin/OriginPro 2021b+ installed locally "
    "(originpro drives it via COM; Windows only)."
)

# Origin internal plot-type IDs (stable across versions).
_PLOT_LINE = 200
_PLOT_SCATTER = 201
_PLOT_LINE_SYMBOL = 202

# Symbol shapes for the combined graphs (Plot.symbol_kind / LabTalk set -k):
# measured vs recovered get different shapes but share the plasma colour scheme.
_SYM_MEASURED = 1   # square
_SYM_RECOVERED = 2  # circle

# LabTalk axis scale types: 0 = linear, 2 = log10.
_AXIS_LINEAR = 0
_AXIS_LOG10 = 2

# Column specs for the grouped-master layout (see build_opju_from_folder.py).
# EIS sheets:  A=Re_Z(X) B=-Im_Z(Y) C=frequency(X) D=|Z|(Y) E=Phase(Y)
_EIS_LNAMES = ["Re_Z", "-Im_Z", "frequency", "|Z|", "Phase"]
_EIS_UNITS = ["ohms", "ohms", "Hz", "ohms", "deg"]
_EIS_AXIS = "xyxyy"
# CA sheet:    A=time(X) B=voltage(Y) C=current(Y)
_CA_LNAMES = ["time", "voltage", "current"]
_CA_UNITS = ["s", "V", "A"]
_CA_AXIS = "xyy"


def _plasma_colors(n):
    """n RGB tuples sampled evenly from the Plasma palette (increment by one)."""
    if n <= 0:
        return []
    try:
        from matplotlib import cm
        cmap = cm.get_cmap("plasma")
        return [
            tuple(int(round(255 * c)) for c in cmap((i / (n - 1)) if n > 1 else 0.0)[:3])
            for i in range(n)
        ]
    except Exception:
        return [(0, 0, 0)] * n


def originpro_status():
    """Return (available: bool, version: str, message: str)."""
    try:
        import originpro as op  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on host install
        return False, "", f"originpro not importable: {exc}. {ORIGIN_REQUIREMENT_NOTE}"
    version = getattr(op, "__version__", "unknown")
    return True, version, ""


def _eis_dataframe(freq, re, im) -> pd.DataFrame:
    """Build the per-dataset worksheet columns (Long Names drive axis titles)."""
    f = np.asarray(freq, dtype=float).reshape(-1)
    re = np.asarray(re, dtype=float).reshape(-1)
    im = np.asarray(im, dtype=float).reshape(-1)
    z = re + 1j * im
    return pd.DataFrame({
        "Frequency (Hz)": f,          # col 0
        "Re(Z) (Ohm)": re,            # col 1
        "Im(Z) (Ohm)": im,            # col 2
        "-Im(Z) (Ohm)": -im,          # col 3  (Nyquist Y)
        "|Z| (Ohm)": np.abs(z),       # col 4  (Bode magnitude)
        "Phase (deg)": np.degrees(np.angle(z)),  # col 5  (Bode phase)
    })


class OriginExporter:
    """Keep one Origin session open across a batch of samples.

    Usage::

        exporter = OriginExporter(show=False)
        try:
            exporter.export_sample(opju_path, name, eis_datasets, cp_data)
        finally:
            exporter.close()

    ``eis_datasets`` is a list of dicts: ``{"name": str, "freq", "re", "im"}``.
    """

    def __init__(self, show: bool = False):
        # COM must be initialised on the thread that talks to Origin.
        self._pythoncom = None
        try:
            import pythoncom
            pythoncom.CoInitialize()
            self._pythoncom = pythoncom
        except Exception:
            self._pythoncom = None

        import originpro as op  # raises if Origin/originpro unavailable
        self.op = op
        try:
            op.set_show(bool(show))
        except Exception:
            pass

    # -- public ----------------------------------------------------------
    def begin_project(self):
        """Start a fresh, empty Origin project."""
        try:
            self.op.new()
        except Exception:
            pass

    def save_project(self, opju_path) -> str:
        out = Path(opju_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.op.save(str(out))
        return str(out)

    def add_project_note(self, text, name="Notes"):
        """Add a project-level Notes window (used to record the electrode's
        coordinates and any user-supplied note in each .opju)."""
        if not text:
            return
        # Preferred: originpro Notes window.
        try:
            nt = self.op.new_notes(name)
            nt.text = str(text)
            return
        except Exception:
            pass
        # Fallback: stash the note in the project's Comments via LabTalk.
        safe = str(text).replace('"', "'")
        self._lt(f'project.comments$="{safe}";')

    def add_sample(self, sample_name, eis_datasets, cp_data=None, folder_path=None):
        """Add one sample (worksheet + Nyquist + Bode) to the CURRENT project.

        ``folder_path`` (a list of folder names) places the sample under that
        path in Origin's Project Explorer, mirroring the source hierarchy.
        """
        self._cd_folder(folder_path)

        datasets = [dict(ds) for ds in eis_datasets]  # copy; we attach _wks
        wb = self._new_book(sample_name)
        first = True
        for ds in datasets:
            wks = wb[0] if first else self._add_sheet(wb, _short_sheet(ds["name"]))
            first = False
            try:
                wks.name = _short_sheet(ds["name"])
            except Exception:
                pass
            wks.from_df(_eis_dataframe(ds["freq"], ds["re"], ds["im"]))
            ds["_wks"] = wks

        if cp_data is not None:
            self._add_cp_sheet(wb, cp_data)

        self._build_nyquist(sample_name, datasets)
        self._build_bode(sample_name, datasets)

    def export_sample(
        self,
        opju_path,
        sample_name: str,
        eis_datasets: Sequence[dict],
        cp_data=None,
    ) -> str:
        """One-sample-per-project convenience (unchanged public behaviour)."""
        self.begin_project()
        self.add_sample(sample_name, eis_datasets, cp_data)
        return self.save_project(opju_path)

    def close(self):
        try:
            self.op.exit()
        except Exception:
            pass
        if self._pythoncom is not None:
            try:
                self._pythoncom.CoUninitialize()
            except Exception:
                pass

    # -- project-explorer folders ---------------------------------------
    def _cd_folder(self, folder_path):
        """Navigate Origin's Project Explorer to folder_path (creating folders)."""
        pe = getattr(self.op, "pe", None)
        if pe is None:
            return
        try:
            pe.cd("/")
        except Exception:
            pass
        if not folder_path:
            return
        for part in folder_path:
            name = _safe_folder(part)
            try:
                pe.mkdir(name, chk=True)   # chk=True: reuse if it already exists
            except TypeError:
                try:
                    pe.mkdir(name)
                except Exception:
                    pass
            except Exception:
                pass
            try:
                pe.cd(name)
            except Exception:
                pass

    # -- worksheets ------------------------------------------------------
    def _new_book(self, long_name: str):
        op = self.op
        try:
            wb = op.new_book("w", lname=long_name)
        except TypeError:
            wb = op.new_book("w")
            try:
                wb.lname = long_name
            except Exception:
                pass
        return wb

    def _add_sheet(self, wb, name):
        try:
            return wb.add_sheet(name)
        except Exception:
            # Fallback: a fresh single-sheet book if add_sheet is unavailable.
            return self._new_book(name)[0]

    def _add_cp_sheet(self, wb, cp_data):
        dc = np.asarray(cp_data)
        if dc.ndim != 2 or dc.shape[1] < 3 or not len(dc):
            return
        df = pd.DataFrame({
            "time (s)": dc[:, 0],
            "Voltage (V)": dc[:, 1],
            "Current (A)": dc[:, 2],
        })
        self._add_sheet(wb, "CP").from_df(df)

    # -- graphs ----------------------------------------------------------
    def _build_nyquist(self, sample_name: str, datasets: List[dict]):
        op = self.op
        gp = op.new_graph(template="scatter")
        try:
            gp.lname = f"{sample_name} · Nyquist"
        except Exception:
            pass
        gl = gp[0]
        for ds in datasets:
            wks = ds.get("_wks")
            if wks is None:
                continue
            plot = gl.add_plot(wks, coly=3, colx=1, type=_PLOT_LINE_SYMBOL)  # -Im(Z) vs Re(Z)
            self._name_plot(plot, ds["name"])
        self._finish_layer(gl, group=True)

    def _build_bode(self, sample_name: str, datasets: List[dict]):
        op = self.op
        gp = None
        try:
            gp = op.new_graph(template="doubleY")
        except Exception:
            gp = None

        if gp is not None and self._layer_count(gp) >= 2:
            try:
                gp.lname = f"{sample_name} · Bode"
            except Exception:
                pass
            gl_mag, gl_phase = gp[0], gp[1]
            for ds in datasets:
                wks = ds.get("_wks")
                if wks is None:
                    continue
                self._name_plot(gl_mag.add_plot(wks, coly=4, colx=0, type=_PLOT_LINE_SYMBOL), ds["name"])
                self._name_plot(gl_phase.add_plot(wks, coly=5, colx=0, type=_PLOT_LINE_SYMBOL), ds["name"])
            self._set_log_x(gl_mag)
            self._set_linear_y(gl_mag)
            self._set_log_x(gl_phase)
            self._finish_layer(gl_mag, group=True)
            self._finish_layer(gl_phase, group=True)
        else:
            # Fallback: two independent single-panel graphs.
            self._build_bode_single(f"{sample_name} · Bode |Z|", datasets, coly=4, log_y=True)
            self._build_bode_single(f"{sample_name} · Bode phase", datasets, coly=5, log_y=False)

    def _build_bode_single(self, title: str, datasets: List[dict], coly: int, log_y: bool):
        op = self.op
        gp = op.new_graph(template="line")
        try:
            gp.lname = title
        except Exception:
            pass
        gl = gp[0]
        for ds in datasets:
            wks = ds.get("_wks")
            if wks is None:
                continue
            self._name_plot(gl.add_plot(wks, coly=coly, colx=0, type=_PLOT_LINE_SYMBOL), ds["name"])
        self._set_log_x(gl)
        if log_y:
            self._set_log_y(gl)
        self._finish_layer(gl, group=True)

    # -- low-level styling helpers (all best-effort) ---------------------
    @staticmethod
    def _name_plot(plot, name):
        try:
            plot.name = name
        except Exception:
            pass

    @staticmethod
    def _layer_count(gp) -> int:
        try:
            return len(gp)
        except Exception:
            return 1

    def _set_axis_scale(self, gl, axis: str, scale_type: int):
        """Set an axis to log/linear via LabTalk, tolerant of API differences."""
        cmd = f"layer.{axis}.type={scale_type};"
        # Preferred: run LabTalk against this layer directly.
        try:
            gl.lt_exec(cmd)
            return
        except Exception:
            pass
        # Fallback: make the layer active, then run module-level LabTalk.
        try:
            try:
                gl.activate()
            except Exception:
                pass
            self.op.lt_exec(cmd)
        except Exception:
            pass

    def _set_log_x(self, gl):
        self._set_axis_scale(gl, "x", _AXIS_LOG10)

    def _set_log_y(self, gl):
        self._set_axis_scale(gl, "y", _AXIS_LOG10)

    @staticmethod
    def _finish_layer(gl, group: bool):
        if group:
            try:
                gl.group()
            except Exception:
                pass
        try:
            gl.rescale()
        except Exception:
            pass

    # === Grouped master layout ==========================================
    # One condition folder -> a workbook per row (3 sheets) + 7 graphs, all
    # filed under folder_parts in the Project Explorer.
    def add_group(self, folder_parts, rows):
        self._cd_folder(folder_parts)
        handles = []
        for row in rows:
            self._cd_folder(folder_parts)
            try:
                handles.append(self._add_row_workbook(row))
            except Exception:
                pass
        self._cd_folder(folder_parts)
        self._build_group_graphs(handles)

    def _add_row_workbook(self, row):
        # Workbook long name = the row's display label. In the per-electrode
        # layout that is the voltage token (e.g. 'V+0p21'); otherwise it falls
        # back to the short 'row001' label. The full folder name stays in
        # handle["name"] for plot names and is written to the workbook's Comments.
        label = self._handle_label(row)
        wb = self._new_book(label)
        self._set_comments(wb, row.get("name"))
        handle = {"name": row["name"], "label": row.get("label"),
                  "measured": None, "ca": None, "recovered": None,
                  "fit": None, "ca_fft_ready": None}
        specs = [
            ("measured", "Measured PEIS", _EIS_LNAMES, _EIS_UNITS, _EIS_AXIS),
            ("ca", "Measured CA", _CA_LNAMES, _CA_UNITS, _CA_AXIS),
            ("recovered", "Recovered EIS", _EIS_LNAMES, _EIS_UNITS, _EIS_AXIS),
            ("fit", "RRQRQ fit", _EIS_LNAMES, _EIS_UNITS, _EIS_AXIS),
            ("ca_fft_ready", "CA fft-ready", _CA_LNAMES, _CA_UNITS, _CA_AXIS),
        ]
        first = True
        for key, sheet_name, lnames, units, axis in specs:
            df = row.get(key)
            if df is None:
                continue
            wks = wb[0] if first else self._add_sheet(wb, sheet_name)
            first = False
            self._fill_sheet(wks, sheet_name, df, lnames, units, axis)
            self._set_comments(wks, row.get("name"))
            handle[key] = wks
        return handle

    @staticmethod
    def _handle_label(h):
        """Display label for a row/handle: the explicit label (voltage token in
        the per-electrode layout) if present, else the short 'row001' label."""
        return h.get("label") or _row_label(h.get("name", ""))

    def _set_comments(self, obj, text):
        """Best-effort: write the full source-folder name into a workbook's or
        worksheet's Comments/Notes field."""
        if not text:
            return
        try:
            obj.comments = str(text)
            return
        except Exception:
            pass
        try:
            obj.set_str("comments", str(text))
        except Exception:
            pass

    def _fill_sheet(self, wks, sheet_name, df, lnames, units, axis):
        try:
            wks.name = sheet_name
        except Exception:
            pass
        try:
            wks.from_df(df)
        except Exception:
            pass
        try:
            wks.set_labels(lnames, "L")   # long names
        except Exception:
            pass
        try:
            wks.set_labels(units, "U")    # units
        except Exception:
            pass
        try:
            wks.cols_axis(axis)           # X/Y designations, e.g. 'xyxyy'
        except Exception:
            pass

    def _build_group_graphs(self, handles):
        self._nyquist_graph("measured_EIS_Nyquist", handles, ["measured"])
        self._ca_graph("measured_CA", handles)
        self._nyquist_graph("recovered_EIS_Nyquist", handles, ["recovered"])
        self._bode_graph("measured_EIS_Bode", handles, ["measured"])
        self._bode_graph("recovered_EIS_Bode", handles, ["recovered"])
        self._nyquist_graph("measured_recovered_Nyquist", handles, ["measured", "recovered"])
        self._bode_graph("measured_recovered_Bode", handles, ["measured", "recovered"])

    def _safe_add_plot(self, gl, wks, coly, colx, ptype):
        try:
            return gl.add_plot(wks, coly=coly, colx=colx, type=ptype)
        except Exception:
            return None

    def _nyquist_graph(self, name, handles, keys):
        gp = self.op.new_graph(template="scatter")
        try:
            gp.lname = name
        except Exception:
            pass
        gl = gp[0]
        # X = Re_Z (col A=0), Y = -Im_Z (col B=1)
        if len(keys) > 1:
            labels = self._plot_by_source(gl, handles, coly=1, colx=0,
                                          ptype=_PLOT_SCATTER, keys=keys)
            # RRQRQ fit: dashed line over the full Nyquist, grouped (coloured by row).
            self._overlay_dashed(gl, handles, "fit", coly=1, colx=0, suffix="fit", group=True)
        else:
            plots, labels = [], []
            key = keys[0]
            for h in handles:
                wks = h.get(key)
                if wks is None:
                    continue
                p = self._safe_add_plot(gl, wks, coly=1, colx=0, ptype=_PLOT_SCATTER)
                if p is not None:
                    lab = self._plot_label(h, key, keys)
                    self._name_plot(p, lab)
                    plots.append(p); labels.append(lab)
            self._group_plasma(gl, plots)
        self._equal_aspect(gl)   # 1:1 X:Y scale
        self._show_frame(gl)     # top + right lines
        try:
            gl.rescale()
        except Exception:
            pass
        self._set_legend([(gl, labels)])

    def _plot_by_source(self, gl, handles, coly, colx, ptype, keys):
        """Combined graph: two groups (measured, recovered) with different SYMBOLS
        but the same per-row plasma colour scheme (edge-colour increment by one)."""
        colors = _plasma_colors(len(handles))
        labels = []
        idx = 0
        for src, symbol in (("measured", _SYM_MEASURED), ("recovered", _SYM_RECOVERED)):
            start = idx
            src_plots = []
            for i, h in enumerate(handles):
                wks = h.get(src)
                if wks is None:
                    continue
                p = self._safe_add_plot(gl, wks, coly=coly, colx=colx, ptype=ptype)
                if p is None:
                    continue
                lab = self._plot_label(h, src, keys)
                self._name_plot(p, lab)
                src_plots.append((p, i))
                labels.append(lab)
                idx += 1
            if not src_plots:
                continue
            self._group_range(gl, start, idx - 1)     # its own group
            try:
                src_plots[0][0].colorinc = 1           # edge colour "By One"
            except Exception:
                pass
            try:
                src_plots[0][0].symbol_kindinc = 0     # ONE shape per group (no cycling)
            except Exception:
                pass
            for p, i in src_plots:
                r, g, b = colors[i]                    # colour by ROW (shared scheme)
                self._color_plot(p, r, g, b)
                self._set_symbol(p, symbol)            # shape by SOURCE
        return labels

    def _group_range(self, gl, start, end):
        if end < start:
            return
        try:
            gl.group(True, start, end)   # group plots start..end into one group
        except Exception:
            try:
                gl.group()
            except Exception:
                pass

    def _set_symbol(self, plot, kind):
        try:
            plot.symbol_kind = kind
        except Exception:
            try:
                plot.set_int("symbol.kind", kind)
            except Exception:
                pass

    def _set_legend(self, layer_labels):
        """One legend entry per plot with the row label as LITERAL text.

        Using literal text ('\\l(1) row001') avoids the @-token issue you saw
        (icons render but %(n,@WL) resolved to blank).
        """
        for gl, labels in layer_labels:
            if not labels:
                continue
            text = "\n".join(f"\\l({i}) {lab}" for i, lab in enumerate(labels, 1))
            self._apply_legend_text(gl, text)

    @staticmethod
    def _plot_label(h, key, keys):
        lab = OriginExporter._handle_label(h)
        return f"{lab} {key}" if len(keys) > 1 else lab

    def _apply_legend_text(self, gl, text):
        # Documented approach: set the layer's 'Legend' label text object.
        try:
            gl.label("Legend").text = text
            return
        except Exception:
            pass
        self._activate_layer(gl)
        self._lt(f'legend.text$="{text}";')

    def _ca_graph(self, name, handles):
        # layer 1 (l1) = bottom panel (current, visible X); layer 2 (l2) = top (voltage)
        gp, l1, l2 = self._stacked_pair(name)
        vplots, cplots, labels = [], [], []
        for h in handles:
            wks = h.get("ca")
            if wks is None:
                continue
            lab = self._handle_label(h)
            pv = self._safe_add_plot(l2, wks, coly=1, colx=0, ptype=_PLOT_LINE)  # voltage (top)
            pc = self._safe_add_plot(l1, wks, coly=2, colx=0, ptype=_PLOT_LINE)  # current (bottom)
            if pv is not None:
                self._name_plot(pv, lab); vplots.append(pv)
            if pc is not None:
                self._name_plot(pc, lab); cplots.append(pc)
            labels.append(lab)
        self._group_plasma(l2, vplots)
        self._group_plasma(l1, cplots)
        # Scout FFT-ready trace: dashed BLACK, over the scout_only CA. NOT grouped:
        # grouping it (with the layers linked) coupled every group to one colour.
        self._overlay_dashed(l2, handles, "ca_fft_ready", coly=1, colx=0,
                             suffix="fft-ready", color=(0, 0, 0))
        self._overlay_dashed(l1, handles, "ca_fft_ready", coly=2, colx=0,
                             suffix="fft-ready", color=(0, 0, 0))
        for gl in (l1, l2):
            try:
                gl.rescale()
            except Exception:
                pass
        self._finalize_stack(stacked_layer=l2)  # layer 1 stays; move layer 2 only
        self._show_frame(l1)
        self._show_frame(l2)
        self._set_legend([(l1, labels), (l2, labels)])

    def _bode_graph(self, name, handles, keys):
        # layer 1 (l1) = bottom panel (Phase, visible X); layer 2 (l2) = top (|Z|)
        gp, l1, l2 = self._stacked_pair(name)
        # |Z| (col D=3) vs frequency (col C=2) on top; Phase (col E=4) on bottom.
        if len(keys) > 1:
            # Combined: group by source (square=measured, circle=recovered) on each layer.
            labels = self._plot_by_source(l2, handles, coly=3, colx=2,
                                          ptype=_PLOT_SCATTER, keys=keys)
            self._plot_by_source(l1, handles, coly=4, colx=2,
                                 ptype=_PLOT_SCATTER, keys=keys)
            # RRQRQ fit as a dashed line over the full Bode (|Z| on top, Phase on bottom).
            self._overlay_dashed(l2, handles, "fit", coly=3, colx=2, suffix="fit")
            self._overlay_dashed(l1, handles, "fit", coly=4, colx=2, suffix="fit")
        else:
            mplots, pplots, labels = [], [], []
            key = keys[0]
            for h in handles:
                wks = h.get(key)
                if wks is None:
                    continue
                lab = self._plot_label(h, key, keys)
                pm = self._safe_add_plot(l2, wks, coly=3, colx=2, ptype=_PLOT_SCATTER)
                pp = self._safe_add_plot(l1, wks, coly=4, colx=2, ptype=_PLOT_SCATTER)
                if pm is not None:
                    self._name_plot(pm, lab); mplots.append(pm)
                if pp is not None:
                    self._name_plot(pp, lab); pplots.append(pp)
                labels.append(lab)
            self._group_plasma(l2, mplots)
            self._group_plasma(l1, pplots)
        self._set_log_x(l1)
        self._set_log_x(l2)
        for gl in (l1, l2):
            try:
                gl.rescale()
            except Exception:
                pass
        self._finalize_stack(stacked_layer=l2)  # layer 1 stays; move layer 2 only
        self._show_frame(l1)
        self._show_frame(l2)
        self._set_legend([(l1, labels), (l2, labels)])

    def _lt(self, cmd):
        """Run a LabTalk command, tolerating originpro API differences."""
        try:
            self.op.lt_exec(cmd)
        except Exception:
            pass

    def _stacked_pair(self, name):
        """Return (page, top_layer, bottom_layer). Layers exist but are not yet
        positioned/linked — call _finalize_stack after the plots are added."""
        op = self.op
        gp = None
        for tmpl in ("PAN2VERT", "panel2vert", "2Ys_TB", "stack"):
            try:
                cand = op.new_graph(template=tmpl)
            except Exception:
                cand = None
            if cand is not None and self._layer_count(cand) >= 2:
                gp = cand
                break
        if gp is None:
            gp = op.new_graph()
            try:
                gp.activate()
            except Exception:
                pass
            self._lt("layadd;")   # add a second layer
        try:
            gp.lname = name
        except Exception:
            pass
        top = gp[0]
        bottom = gp[1] if self._layer_count(gp) >= 2 else gp[0]
        return gp, top, bottom

    # -- axis / frame / stack formatting (best-effort LabTalk) ----------
    def _activate_layer(self, gl):
        try:
            gl.activate()
        except Exception:
            pass

    def _set_dashed(self, plot):
        """Dashed line (LabTalk line-style code 1 = Dash)."""
        try:
            plot.line_style = 1
            return
        except Exception:
            pass
        for key in ("lineType", "line.type", "line.linestyle"):
            try:
                plot.set_int(key, 1)
                return
            except Exception:
                continue

    def _layer_plot_count(self, gl):
        try:
            return len(gl.plot_list())
        except Exception:
            return 0

    def _overlay_dashed(self, gl, handles, key, coly, colx, suffix, color=None, group=False):
        """Overlay each row's `key` sheet as a dashed LINE.

        color=None -> colour by row (plasma); color=(r,g,b) -> that fixed colour.
        group=True -> collect the overlay lines into their own group.
        """
        colors = _plasma_colors(len(handles))
        start = self._layer_plot_count(gl)
        added = []  # (plot, row_index)
        for i, h in enumerate(handles):
            wks = h.get(key)
            if wks is None:
                continue
            p = self._safe_add_plot(gl, wks, coly=coly, colx=colx, ptype=_PLOT_LINE)
            if p is None:
                continue
            self._name_plot(p, f"{self._handle_label(h)} {suffix}")
            added.append((p, i))
        if not added:
            return
        if group and start > 0:
            self._group_range(gl, start, start + len(added) - 1)
            lead = added[0][0]
            try:
                lead.colorinc = 0 if color is not None else 1
            except Exception:
                pass
            try:
                lead.symbol_kindinc = 0
            except Exception:
                pass
        for p, i in added:                      # colour + dash after grouping so it sticks
            rgb = color if color is not None else colors[i]
            self._color_plot(p, *rgb)
            self._set_dashed(p)

    def _equal_aspect(self, gl):
        """Nyquist: lock X:Y scale 1:1 ('Link Axis Length to Scale', isometric).

        layer.isisometric=1 (docs.originlab.com Layer object). Note: fixedFactor
        is element scaling, NOT the axis ratio, which is why it didn't lock.
        """
        try:
            gl.set_int("isisometric", 1)
        except Exception:
            pass
        self._activate_layer(gl)
        self._lt("layer.isisometric=1;")

    def _show_frame(self, gl):
        """Add top + right axis lines to a layer.

        Documented originpro way (per docs.originlab.com graphing examples):
        GLayer.set_int('x.opposite', 1) / set_int('y.opposite', 1).
        """
        for prop in ("x.opposite", "y.opposite"):
            try:
                gl.set_int(prop, 1)
            except Exception:
                pass
        # LabTalk fallback in case set_int is unavailable.
        self._activate_layer(gl)
        self._lt("layer.x.opposite=1; layer.y.opposite=1;")

    def _hide_x_axis(self, gl):
        """Hide the bottom-X tick labels, ticks, and title on a layer."""
        self._activate_layer(gl)
        for cmd in ("layer.x.showLabels=0;",
                    "layer.x.majorTicks=0;",
                    "layer.x.minorTicks=0;",
                    "xb.show=0;"):
            self._lt(cmd)

    def _finalize_stack(self, stacked_layer, base_idx=1, stacked_idx=2, link_x=True):
        """Stack two panels (the layout that worked): layer 1 = bottom panel in
        % of page; layer 2 = top panel linked to layer 1 at Top = -100% of the
        linked layer, directly above, with its X axis hidden."""
        # Layer 1 = bottom panel, % of page (default unit).
        self._lt(f"layer -s {base_idx}; "
                 f"layer.left=17.86; layer.top=45; layer.width=70; layer.height=40;")
        # Layer 2 = top panel, linked to layer 1 at -100% of the linked layer.
        if link_x:
            self._lt(f"layer -s {stacked_idx}; layer.link.to={base_idx}; layer.link.xaxis=1;")
        self._lt(f"layer -s {stacked_idx}; layer.unit=7; "
                 f"layer.left=0; layer.top=-100; layer.width=100; layer.height=100;")
        # Top panel (layer 2): hide X tick labels, ticks, and title (shared X is the bottom).
        self._lt(f"layer -s {stacked_idx}; layer.x.showLabels=0; "
                 f"layer.x.majorTicks=0; layer.x.minorTicks=0;")
        self._lt(f"layer -s {stacked_idx}; xb.show=0;")
        # Bottom panel (layer 1): show X tick labels; leave tick spacing at the default.
        self._lt(f"layer -s {base_idx}; layer.x.showLabels=1;")

    def _group_plasma(self, gl, plots):
        """Group the plots, set the group colour increment to "By One", then
        colour each member with the Plasma palette.

        colorinc=1 gives every member its own colour slot. The Nyquist group had
        this "By One" increment automatically; the 2-layer Bode/CA groups
        defaulted to "None", which collapsed all members to one colour. See
        originpro Plot.colorinc (docs.originlab.com Plot class reference).
        """
        try:
            gl.group()
        except Exception:
            pass
        if not plots:
            return
        try:
            plots[0].colorinc = 1        # "By One" colour increment on the group
        except Exception:
            pass
        try:
            plots[0].symbol_kindinc = 0  # keep ONE symbol shape (no shape cycling)
        except Exception:
            pass
        for plot, (r, g, b) in zip(plots, _plasma_colors(len(plots))):
            self._color_plot(plot, r, g, b)

    @staticmethod
    def _color_plot(plot, r, g, b):
        # Origin custom-RGB color (COLORREF 0x00BBGGRR with the custom-color flag).
        ocolor = (1 << 24) | (b << 16) | (g << 8) | r
        # Main/line colour: high-level property first, then encodings.
        for value in ((r, g, b), f"#{r:02X}{g:02X}{b:02X}", ocolor):
            try:
                plot.color = value
                break
            except Exception:
                continue
        # Symbol edge + fill so SCATTER markers are coloured too.
        for prop in ("symbol.color", "symbol.edge.color", "symbol.fill.color",
                     "edgecolor", "fillcolor"):
            try:
                plot.set_int(prop, ocolor)
            except Exception:
                pass


def export_opju(opju_path, sample_name, eis_datasets, cp_data=None, show=False) -> str:
    """One-shot convenience wrapper (opens and closes its own Origin session)."""
    exporter = OriginExporter(show=show)
    try:
        return exporter.export_sample(opju_path, sample_name, eis_datasets, cp_data)
    finally:
        exporter.close()
