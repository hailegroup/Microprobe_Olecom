# -*- coding: utf-8 -*-
"""
Microprobe — Standalone Analysis GUI
====================================
A hardware-free companion to ``gui.py`` for *re-doing the analysis portion*.

It takes the raw export folder produced by the main measurement program
(``run_automation.py`` / ``gui.py``), auto-pairs each sample's
``{label}_CA_{ts}.txt`` (CP/DC trace) with ``{label}_PEIS_{ts}.txt`` (reference
EIS), and re-runs the CP->EIS recovery + RQRQRQ fit + Excel/Origin export via
``Analysis_Convert_CP_to_EIS/run_trusted_sample_analysis.recover_and_export``.

Nothing here touches instruments, so it can run on any machine that has the
analysis dependencies (numpy, pandas, scipy, matplotlib, openpyxl).

Run:  python analysis_gui.py
"""

from __future__ import annotations

import os
import re
import sys
import queue
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")  # analysis backend renders off-screen; we embed our own Figures
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk


BASE_DIR = Path(__file__).resolve().parent
ANALYSIS_DIR = BASE_DIR / "Analysis_Convert_CP_to_EIS"

# Style constants mirrored from gui.py so the two windows feel like one app.
CLR_BG = "#f5f5f5"
CLR_HEADER = "#2c3e50"
CLR_GREEN = "#27ae60"
CLR_RED = "#e74c3c"
CLR_ORANGE = "#e67e22"
CLR_BLUE = "#2980b9"
CLR_LGRAY = "#ecf0f1"

# Raw export naming from measurement_sequence.py:
#   {label}_PEIS_{ts}.txt / .mpr        -> reference EIS
#   {label}_CA_{ts}.txt / .mpr          -> CP/DC trace used as the analysis input
#   Pre_stabilization_{label}_CA_{ts}.. -> pre-hold, NOT an analysis input
_DATA_EXTS = ("txt", "mpr", "mpt", "csv", "dat")
_EXT_GROUP = "|".join(_DATA_EXTS)
_PEIS_RE = re.compile(rf"^(?P<label>.+)_PEIS_(?P<ts>\d{{8}}_\d{{6}})\.(?:{_EXT_GROUP})$", re.IGNORECASE)
_CA_RE = re.compile(rf"^(?P<label>.+)_CA_(?P<ts>\d{{8}}_\d{{6}})\.(?:{_EXT_GROUP})$", re.IGNORECASE)


# Sheets written by export_dict_to_excel.py that we can re-import from.
_EXCEL_RAW_SHEET = "Raw Data"
_EXCEL_ARC_SHEET = "Total Arc Data"


# ---------------------------------------------------------------------------
# Export-folder discovery / pairing
# ---------------------------------------------------------------------------
def _new_pair(label, ts, *, ca_path=None, peis_path=None, excel_path=None,
              source="files", status="ready", sample_name=None):
    return {
        "label": label,
        "ts": ts,
        "ca_path": ca_path,
        "peis_path": peis_path,
        "excel_path": excel_path,
        "source": source,
        "status": status,
        "sample_name": sample_name,
    }


def _is_analysis_excel(xlsx):
    """True if a workbook has the sheets we need to rebuild the analysis inputs."""
    try:
        names = pd.ExcelFile(xlsx).sheet_names
    except Exception:
        return False
    return _EXCEL_RAW_SHEET in names and _EXCEL_ARC_SHEET in names


def _discover_excel_samples(folder):
    """Find analysis .xlsx workbooks anywhere under folder (e.g. next to
    origin_export) and register each as a re-runnable, Excel-sourced sample."""
    folder = Path(folder)
    pairs = []
    for xlsx in sorted(folder.rglob("*.xlsx")):
        if xlsx.name.startswith("~$") or not xlsx.is_file():
            continue
        if not _is_analysis_excel(xlsx):
            continue
        try:
            location = str(xlsx.parent.relative_to(folder))
        except ValueError:
            location = xlsx.parent.name
        if location in ("", "."):
            location = xlsx.parent.name or "excel"
        pairs.append(_new_pair(
            xlsx.stem, location, excel_path=xlsx, source="excel", status="ready"))
    return pairs


def _origin_prefix(path):
    stem = path.stem
    for suffix in ("_ca_fft_input", "_measured_peis"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _discover_origin_export_samples(folder):
    """Find production ``origin_export`` folders (the deployed OLECOM format).

    Each holds ``*_ca_fft_input.csv`` (CP trace: time_s, voltage_V, current_A)
    and ``*_measured_peis.csv`` (reference PEIS: freq_Hz, ReZ_ohm, negImZ_ohm).
    Those are re-imported as the analysis inputs at run time.
    """
    folder = Path(folder)
    pairs = []
    for oe in sorted(p for p in folder.rglob("origin_export") if p.is_dir()):
        ca_csv = next(iter(oe.glob("*_ca_fft_input.csv")), None)
        peis_csv = next(iter(oe.glob("*_measured_peis.csv")), None)
        if ca_csv is None and peis_csv is None:
            continue
        sample_dir = oe.parent
        try:
            location = str(sample_dir.parent.relative_to(folder))
        except ValueError:
            location = sample_dir.parent.name
        if location in ("", "."):
            location = sample_dir.parent.name or "origin_export"
        if ca_csv is not None and peis_csv is not None:
            status = "ready"
        elif ca_csv is not None:
            status = "ca_only"
        else:
            status = "peis_only"
        pairs.append(_new_pair(
            sample_dir.name, location,
            ca_path=ca_csv, peis_path=peis_csv,
            source="origin_csv", status=status,
            sample_name=sample_dir.name,
        ))
    return pairs


def discover_sample_pairs(folder, recursive=True, include_excel=True):
    """Scan an export folder for re-runnable samples.

    Two sources are recognised:
      * raw text/mpr exports -> pairs ``{label}_CA_*`` with ``{label}_PEIS_*``
      * analysis Excel workbooks (Raw Data + Total Arc Data sheets), whose CP
        trace and reference PEIS are re-imported at run time.

    Returns a list of pair dicts (see ``_new_pair``). ``status`` is one of
    'ready', 'peis_only', 'ca_only'.
    """
    folder = Path(folder)
    files = folder.rglob("*") if recursive else folder.glob("*")

    peis = {}  # (label, ts) -> path
    ca = {}    # (label, ts) -> path
    for path in files:
        if not path.is_file():
            continue
        name = path.name
        m = _PEIS_RE.match(name)
        if m:
            peis[(m.group("label"), m.group("ts"))] = path
            continue
        if name.startswith("Pre_stabilization_"):
            continue  # pre-hold trace is never the analysis DC input
        m = _CA_RE.match(name)
        if m:
            ca[(m.group("label"), m.group("ts"))] = path

    pairs = []
    keys = set(peis) | set(ca)
    for key in keys:
        label, ts = key
        peis_path = peis.get(key)
        ca_path = ca.get(key)
        if peis_path and ca_path:
            status = "ready"
        elif peis_path:
            status = "peis_only"
        else:
            status = "ca_only"
        pairs.append(_new_pair(label, ts, ca_path=ca_path, peis_path=peis_path,
                               source="files", status=status))

    pairs.extend(_discover_origin_export_samples(folder))
    if include_excel:
        pairs.extend(_discover_excel_samples(folder))

    pairs.sort(key=lambda p: (p["source"], p["ts"], p["label"]))
    return pairs


# ---------------------------------------------------------------------------
# Excel -> analysis-input extraction
# ---------------------------------------------------------------------------
def extract_excel_dc(xlsx):
    """Return the imported CP/DC trace [time, V, I] from the 'Raw Data' sheet."""
    raw = pd.read_excel(xlsx, sheet_name=_EXCEL_RAW_SHEET)
    cols = list(raw.columns)
    start = None
    for i, col in enumerate(cols):
        if str(col).strip().lower().startswith("imported time"):
            start = i
            break
    if start is None or start + 3 > len(cols):
        raise ValueError(f"'{_EXCEL_RAW_SHEET}' has no 'Imported time(s), V(V), I(A)' block")
    block = raw.iloc[:, start:start + 3].apply(pd.to_numeric, errors="coerce").dropna()
    data = block.to_numpy(dtype=float)
    if data.size == 0:
        raise ValueError("imported CP/DC block is empty")
    return data


def extract_excel_reference_eis(xlsx):
    """Return the reference PEIS [freq, Re(Z), Im(Z)] from the 'Total Arc Data' sheet."""
    arc = pd.read_excel(xlsx, sheet_name=_EXCEL_ARC_SHEET)
    src = arc["Source"].astype(str).str.strip()
    ref = arc[src == "Reference PEIS"]
    data = (
        ref[["freq/Hz", "Re(Z)/Ohm", "Im(Z)/Ohm"]]
        .apply(pd.to_numeric, errors="coerce")
        .dropna()
        .to_numpy(dtype=float)
    )
    if data.size == 0:
        raise ValueError("no 'Reference PEIS' rows in the Total Arc Data sheet")
    return data[np.argsort(data[:, 0])[::-1]]  # descending frequency, like a normal PEIS


def _read_origin_csv_numeric(path, ncols=3):
    # encoding utf-8-sig strips the BOM the production CSVs carry.
    df = pd.read_csv(path, encoding="utf-8-sig")
    data = df.iloc[:, :ncols].apply(pd.to_numeric, errors="coerce").dropna().to_numpy(dtype=float)
    if data.size == 0:
        raise ValueError(f"{Path(path).name} has no numeric rows")
    return data


def origin_export_to_input_files(ca_csv, peis_csv, out_dir, sample_name):
    """Write temp CA/PEIS text files from a production origin_export folder.

    ``*_ca_fft_input.csv`` -> time_s, voltage_V, current_A  (current already in A)
    ``*_measured_peis.csv`` -> freq_Hz, ReZ_ohm, negImZ_ohm (3rd col is -Im(Z))

    Returns (ca_path, peis_path).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dc = _read_origin_csv_numeric(ca_csv, 3)          # [time, V, I]
    eis = _read_origin_csv_numeric(peis_csv, 3)       # [freq, ReZ, negImZ]

    ca_path = out_dir / f"{sample_name}_CA.txt"
    peis_path = out_dir / f"{sample_name}_PEIS.txt"
    np.savetxt(ca_path, dc, header="time/s  V/V  I/A", comments="")
    # 3rd column is already -Im(Z); '-Im(Z)/Ohm' header makes parse_eis_file flip it back to Im(Z).
    np.savetxt(peis_path, eis, header="freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm", comments="")
    return ca_path, peis_path


def excel_to_input_files(xlsx, out_dir, sample_name):
    """Write temp CA/PEIS text files (loader-native format) from an analysis Excel.

    Returns (ca_path, peis_path).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dc = extract_excel_dc(xlsx)
    eis = extract_excel_reference_eis(xlsx)

    ca_path = out_dir / f"{sample_name}_CA.txt"
    peis_path = out_dir / f"{sample_name}_PEIS.txt"
    np.savetxt(ca_path, dc, header="time/s  V/V  I/A", comments="")
    # Header 'Im(Z)/Ohm' (not '-Im(Z)') so parse_eis_file keeps the sign as-is.
    np.savetxt(peis_path, eis, header="freq/Hz  Re(Z)/Ohm  Im(Z)/Ohm", comments="")
    return ca_path, peis_path


# ---------------------------------------------------------------------------
# Lazy import of the analysis backend
# ---------------------------------------------------------------------------
def _load_runner():
    """Import run_trusted_sample_analysis with the analysis dir on sys.path."""
    os.environ.setdefault("MPLBACKEND", "Agg")
    if str(ANALYSIS_DIR) not in sys.path:
        sys.path.insert(0, str(ANALYSIS_DIR))
    import run_trusted_sample_analysis as runner  # type: ignore
    return runner


def _safe_sample_name(pair):
    """A unique, filesystem-safe output name for a sample.

    Excel-sourced samples carry a nested location in ``ts`` (e.g.
    ``[7-3]/[1]/row001``); sanitise separators so the output folder is flat.
    """
    raw = pair.get("sample_name") or f"{pair['label']}_{pair['ts']}"
    return re.sub(r"[^0-9A-Za-z._-]+", "_", raw).strip("_") or "sample"


def _build_eis_datasets(result):
    """Turn a recover_and_export result into named EIS datasets for Origin.

    Produces up to three series: reference PEIS, recovered CP->EIS, and a smooth
    RQRQRQ fit curve (when the fit is finite). Each dataset is
    ``{"name", "freq", "re", "im"}`` with ``im`` = conventional Im(Z).
    """
    datasets = []

    eis = np.asarray(result.get("eis_data"))
    if eis.ndim == 2 and eis.shape[1] >= 3 and len(eis):
        datasets.append({
            "name": "Reference PEIS",
            "freq": eis[:, 0], "re": eis[:, 1], "im": eis[:, 2],
        })

    f = np.asarray(result.get("filtered_f", []), dtype=float).reshape(-1)
    z = np.asarray(result.get("recovered_z", []))
    if f.size and z.size:
        datasets.append({
            "name": "Recovered (CP->EIS)",
            "freq": f, "re": np.real(z), "im": np.imag(z),
        })

    # Smooth fitted curve over the combined frequency span (best-effort).
    fit_result = result.get("fit_result") or {}
    if fit_result and datasets:
        try:
            import EIS_Fitting as EISFIT  # backend already put ANALYSIS_DIR on sys.path
            all_f = np.concatenate([np.asarray(d["freq"], dtype=float) for d in datasets])
            all_f = all_f[np.isfinite(all_f) & (all_f > 0)]
            if all_f.size:
                grid = np.logspace(np.log10(all_f.min()), np.log10(all_f.max()), 200)
                z_fit = np.asarray(EISFIT.Z_from_fit_result(fit_result, grid))
                if z_fit.size and np.all(np.isfinite(z_fit)):
                    datasets.append({
                        "name": "RQRQRQ fit",
                        "freq": grid, "re": np.real(z_fit), "im": np.imag(z_fit),
                    })
        except Exception:
            pass  # fit curve is optional; reference/recovered are enough

    return datasets


class _QueueWriter:
    """A line-buffered stdout shim that forwards backend prints to the GUI log."""

    def __init__(self, put_line):
        self._put = put_line
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._put(line.rstrip())

    def flush(self):
        if self._buf.strip():
            self._put(self._buf.rstrip())
            self._buf = ""


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class AnalysisGUI(tk.Tk):
    _CURRENT_UNIT_CHOICES = {
        "Auto (from file header)": None,
        "Already Amps (A)": False,
        "Milliamps (mA)": True,
    }

    def __init__(self):
        super().__init__()
        self.title("Microprobe — Standalone Analysis (re-run exports)")
        self.geometry("1220x760")
        self.configure(bg=CLR_BG)

        self._pairs = []
        self._tree_keys = {}       # tree item id -> (label, ts)
        self._pair_by_key = {}     # (label, ts) -> pair dict
        self._queue = queue.Queue()
        self._worker = None
        self._stop_flag = threading.Event()
        self._scan_thread = None
        self._scanning = False

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---- UI construction -------------------------------------------------
    def _build_ui(self):
        self._build_header()

        body = tk.Frame(self, bg=CLR_BG)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        paned = ttk.Panedwindow(body, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=CLR_BG)
        right = tk.Frame(paned, bg=CLR_BG)
        paned.add(left, weight=3)
        paned.add(right, weight=4)

        self._build_pair_list(left)
        self._build_right_panel(right)
        self._build_log(body)

    def _build_header(self):
        header = tk.Frame(self, bg=CLR_HEADER)
        header.pack(fill="x")
        tk.Label(
            header, text="Analysis Re-run  ·  CP→EIS recovery + RQRQRQ fit",
            bg=CLR_HEADER, fg="white", font=("Segoe UI", 13, "bold"),
            anchor="w", padx=12, pady=8,
        ).pack(side="left")

        bar = tk.Frame(self, bg=CLR_LGRAY)
        bar.pack(fill="x", padx=8, pady=8)

        tk.Label(bar, text="Export folder:", bg=CLR_LGRAY,
                 font=("Segoe UI", 10)).pack(side="left", padx=(8, 4), pady=8)
        self._folder_var = tk.StringVar()
        tk.Entry(bar, textvariable=self._folder_var, font=("Segoe UI", 10)).pack(
            side="left", fill="x", expand=True, padx=4, pady=8)
        ttk.Button(bar, text="Browse…", command=self._browse_folder).pack(
            side="left", padx=4, pady=8)
        self._scan_btn = ttk.Button(bar, text="Scan", command=self._scan_folder)
        self._scan_btn.pack(side="left", padx=(4, 4), pady=8)

        self._scan_status_var = tk.StringVar(value="Idle")
        tk.Label(bar, textvariable=self._scan_status_var, bg=CLR_LGRAY,
                 font=("Segoe UI", 9), fg="#555", width=14, anchor="w").pack(
                 side="left", padx=(2, 4), pady=8)
        self._scan_progress = ttk.Progressbar(bar, mode="indeterminate", length=90)
        self._scan_progress.pack(side="left", padx=(0, 8), pady=8)

    def _build_pair_list(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        tk.Label(parent, text="Discovered sample pairs",
                 bg=CLR_BG, font=("Segoe UI", 11, "bold")).grid(
                 row=0, column=0, sticky="w", pady=(0, 4))

        wrap = tk.Frame(parent, bg=CLR_BG)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        cols = ("label", "ts", "peis", "ca", "status")
        self._tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        headings = {
            "label": ("Label", 150),
            "ts": ("Timestamp", 130),
            "peis": ("PEIS file", 90),
            "ca": ("CA file", 90),
            "status": ("Status", 90),
        }
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text)
            self._tree.column(col, width=width, anchor="w")
        self._tree.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self._tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.tag_configure("ready", foreground="#1b5e20")
        self._tree.tag_configure("skip", foreground="#b71c1c")
        self._tree.tag_configure("done", background="#e8f5e9")
        self._tree.tag_configure("failed", background="#ffebee")
        self._tree.bind("<Double-1>", self._on_double_click)

        btns = tk.Frame(parent, bg=CLR_BG)
        btns.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(btns, text="Select all", command=self._select_all).pack(side="left")
        self._count_var = tk.StringVar(value="No folder scanned")
        tk.Label(btns, textvariable=self._count_var, bg=CLR_BG,
                 font=("Segoe UI", 9), fg="#555").pack(side="right")

    def _build_right_panel(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        self._build_settings(parent)

        plot_wrap = tk.Frame(parent, bg=CLR_BG)
        plot_wrap.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        plot_wrap.rowconfigure(0, weight=1)
        plot_wrap.columnconfigure(0, weight=1)

        self._fig = Figure(figsize=(5, 4), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._reset_plot()
        self._canvas = FigureCanvasTkAgg(self._fig, master=plot_wrap)
        self._canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        toolbar_frame = tk.Frame(plot_wrap, bg=CLR_BG)
        toolbar_frame.grid(row=1, column=0, sticky="ew")
        NavigationToolbar2Tk(self._canvas, toolbar_frame)

        self._fit_var = tk.StringVar(value="Fit summary will appear here after a run.")
        tk.Label(parent, textvariable=self._fit_var, bg=CLR_BG, anchor="w",
                 justify="left", font=("Consolas", 9), fg="#37474f").grid(
                 row=2, column=0, sticky="ew", pady=(6, 0))

    def _build_settings(self, parent):
        box = ttk.LabelFrame(parent, text="Analysis settings")
        box.grid(row=0, column=0, sticky="ew")
        for c in range(4):
            box.columnconfigure(c, weight=1)

        # Output folder
        tk.Label(box, text="Output folder:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self._out_var = tk.StringVar()
        tk.Entry(box, textvariable=self._out_var).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ttk.Button(box, text="…", width=3, command=self._browse_output).grid(
            row=0, column=3, sticky="w", padx=(0, 6), pady=4)

        # Current units
        tk.Label(box, text="Current units:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self._units_var = tk.StringVar(value="Auto (from file header)")
        ttk.Combobox(box, textvariable=self._units_var, state="readonly",
                     values=list(self._CURRENT_UNIT_CHOICES.keys())).grid(
                     row=1, column=1, sticky="ew", padx=4, pady=4)

        self._autotrim_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="Auto-trim early transient",
                        variable=self._autotrim_var).grid(
                        row=1, column=2, columnspan=2, sticky="w", padx=6, pady=4)

        # Numeric params
        tk.Label(box, text="Interp dt (s):").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self._dt_var = tk.StringVar(value="0.01")
        tk.Entry(box, textvariable=self._dt_var, width=10).grid(
            row=2, column=1, sticky="w", padx=4, pady=4)

        tk.Label(box, text="SavGol window:").grid(row=2, column=2, sticky="w", padx=6, pady=4)
        self._window_var = tk.StringVar(value="51")
        tk.Entry(box, textvariable=self._window_var, width=10).grid(
            row=2, column=3, sticky="w", padx=4, pady=4)

        tk.Label(box, text="Current scale ×:").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        self._scale_var = tk.StringVar(value="1.0")
        tk.Entry(box, textvariable=self._scale_var, width=10).grid(
            row=3, column=1, sticky="w", padx=4, pady=4)

        tk.Label(box, text="Trim start t (s):").grid(row=3, column=2, sticky="w", padx=6, pady=4)
        self._trim_var = tk.StringVar(value="")
        tk.Entry(box, textvariable=self._trim_var, width=10).grid(
            row=3, column=3, sticky="w", padx=4, pady=4)

        self._opju_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            box,
            text="Also build Origin .opju with editable Nyquist + Bode graphs (requires Origin installed)",
            variable=self._opju_var,
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 0))

        run_bar = tk.Frame(box, bg=CLR_BG)
        run_bar.grid(row=5, column=0, columnspan=4, sticky="ew", padx=6, pady=(2, 6))
        self._run_sel_btn = ttk.Button(run_bar, text="Run selected",
                                       command=lambda: self._start_run(selected_only=True))
        self._run_sel_btn.pack(side="left")
        self._run_all_btn = ttk.Button(run_bar, text="Run all ready",
                                       command=lambda: self._start_run(selected_only=False))
        self._run_all_btn.pack(side="left", padx=6)
        self._stop_btn = ttk.Button(run_bar, text="Stop", command=self._request_stop,
                                    state="disabled")
        self._stop_btn.pack(side="left")
        self._progress = ttk.Progressbar(run_bar, mode="determinate", length=180)
        self._progress.pack(side="right")

    def _build_log(self, parent):
        wrap = tk.Frame(parent, bg=CLR_BG)
        wrap.pack(fill="x", pady=(8, 0))
        tk.Label(wrap, text="Log", bg=CLR_BG, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self._log = tk.Text(wrap, height=8, font=("Consolas", 9), bg="#1e1e1e",
                            fg="#dcdcdc", wrap="word")
        self._log.pack(fill="x")
        self._log.configure(state="disabled")

    # ---- folder / scan ---------------------------------------------------
    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select the measurement export folder")
        if path:
            self._folder_var.set(path)
            if not self._out_var.get():
                self._out_var.set(str(Path(path) / "reanalysis"))
            self._scan_folder()

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select an output folder")
        if path:
            self._out_var.set(path)

    def _scan_folder(self):
        if self._scanning:
            return
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Busy", "Wait for the current analysis run to finish before scanning.")
            return
        folder = self._folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning("No folder", "Choose a valid export folder first.")
            return
        if not self._out_var.get():
            self._out_var.set(str(Path(folder) / "reanalysis"))

        # Show the scanning indicator, then discover in the background so big,
        # deeply-nested folders don't freeze the window.
        self._scanning = True
        self._scan_btn.configure(state="disabled")
        self._scan_status_var.set("Scanning…")
        self._count_var.set("Scanning…")
        try:
            self._scan_progress.start(12)
        except Exception:
            pass
        self._log_line(f"Scanning {folder} …")

        self._scan_thread = threading.Thread(
            target=self._scan_worker, args=(folder,), daemon=True)
        self._scan_thread.start()

    def _scan_worker(self, folder):
        try:
            pairs = discover_sample_pairs(folder)
            self._queue.put(("scan_done", (folder, pairs)))
        except Exception as exc:
            self._queue.put(("scan_error", (folder, str(exc))))

    def _end_scan_indicator(self, status_text):
        self._scanning = False
        try:
            self._scan_progress.stop()
        except Exception:
            pass
        self._scan_btn.configure(state="normal")
        self._scan_status_var.set(status_text)

    def _populate_pairs(self, folder, pairs):
        self._pairs = pairs
        self._pair_by_key = {(p["label"], p["ts"]): p for p in pairs}
        self._tree_keys.clear()
        self._tree.delete(*self._tree.get_children())

        for p in pairs:
            tag = "ready" if p["status"] == "ready" else "skip"
            if p.get("source") == "excel":
                status_text = "ready (Excel)"
                peis_col = p["excel_path"].name
                ca_col = "(from Excel)"
            elif p.get("source") == "origin_csv":
                status_text = {
                    "ready": "ready (origin_export)",
                    "peis_only": "no CA (skip)",
                    "ca_only": "no PEIS (skip)",
                }[p["status"]]
                peis_col = p["peis_path"].name if p["peis_path"] else "-"
                ca_col = p["ca_path"].name if p["ca_path"] else "-"
            else:
                status_text = {
                    "ready": "ready",
                    "peis_only": "no CA (skip)",
                    "ca_only": "no PEIS (skip)",
                }[p["status"]]
                peis_col = p["peis_path"].name if p["peis_path"] else "-"
                ca_col = p["ca_path"].name if p["ca_path"] else "-"
            item = self._tree.insert("", "end", values=(
                p["label"], p["ts"], peis_col, ca_col, status_text,
            ), tags=(tag,))
            self._tree_keys[item] = (p["label"], p["ts"])

        ready = sum(1 for p in pairs if p["status"] == "ready")
        self._count_var.set(f"{len(pairs)} pair(s) · {ready} ready")
        self._log_line(f"Scan complete: {len(pairs)} pair(s), {ready} ready to analyze.")
        if not pairs:
            self._log_line("  No raw *_CA_*/*_PEIS_* files and no analysis .xlsx "
                           "workbooks (Raw Data + Total Arc Data sheets) were found.")

    def _select_all(self):
        self._tree.selection_set(self._tree.get_children())

    def _on_double_click(self, _event):
        item = self._tree.focus()
        if item:
            self._tree.selection_set(item)
            self._start_run(selected_only=True)

    # ---- run orchestration ----------------------------------------------
    def _collect_settings(self):
        def _f(var, default):
            text = var.get().strip()
            if text == "":
                return default
            return float(text)

        return {
            "current_in_mA": self._CURRENT_UNIT_CHOICES[self._units_var.get()],
            "auto_trim": bool(self._autotrim_var.get()),
            "dt": _f(self._dt_var, 0.01),
            "window": int(_f(self._window_var, 51)),
            "current_scale_factor": _f(self._scale_var, 1.0),
            "trim_start_time": (None if self._trim_var.get().strip() == ""
                                else _f(self._trim_var, None)),
            "output_root": self._out_var.get().strip(),
            "build_opju": bool(self._opju_var.get()),
        }

    def _start_run(self, selected_only):
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Busy", "An analysis run is already in progress.")
            return

        if selected_only:
            keys = [self._tree_keys[i] for i in self._tree.selection() if i in self._tree_keys]
            targets = [self._pair_by_key[k] for k in keys]
        else:
            targets = list(self._pairs)

        targets = [p for p in targets if p["status"] == "ready"]
        if not targets:
            messagebox.showwarning(
                "Nothing to run",
                "No 'ready' pairs selected. A pair needs both a CA and a PEIS file.")
            return

        try:
            settings = self._collect_settings()
        except ValueError:
            messagebox.showerror("Bad setting", "One of the numeric fields is not a number.")
            return
        if not settings["output_root"]:
            messagebox.showwarning("No output", "Choose an output folder first.")
            return

        self._stop_flag.clear()
        self._set_running(True)
        self._progress.configure(maximum=len(targets), value=0)
        self._log_line(f"Starting analysis of {len(targets)} pair(s) → {settings['output_root']}")

        self._worker = threading.Thread(
            target=self._run_worker, args=(targets, settings), daemon=True)
        self._worker.start()

    def _request_stop(self):
        self._stop_flag.set()
        self._log_line("Stop requested — finishing the current pair, then halting.")

    def _run_worker(self, targets, settings):
        put = lambda msg: self._queue.put(("log", msg))
        writer = _QueueWriter(put)
        import contextlib

        try:
            runner = _load_runner()
        except Exception:
            self._queue.put(("log", "Failed to import analysis backend:"))
            self._queue.put(("log", traceback.format_exc()))
            self._queue.put(("finished", None))
            return

        # Push GUI settings into the backend's module-level SETTINGS dict.
        runner.SETTINGS["current_in_mA"] = settings["current_in_mA"]
        runner.SETTINGS["dt"] = settings["dt"]
        runner.SETTINGS["window"] = settings["window"]
        runner.SETTINGS["current_scale_factor"] = settings["current_scale_factor"]
        runner.SETTINGS["trim_start_time"] = settings["trim_start_time"]

        # Optional Origin (.opju) session — kept open across the whole batch.
        exporter = None
        if settings.get("build_opju"):
            exporter = self._open_origin_session()

        output_root = Path(settings["output_root"])
        done = failed = opju_done = 0
        try:
            for i, pair in enumerate(targets, start=1):
                key = (pair["label"], pair["ts"])
                if self._stop_flag.is_set():
                    self._queue.put(("log", "Halted before finishing all pairs."))
                    break
                sample_name = _safe_sample_name(pair)
                self._queue.put(("status", (key, "running…")))
                self._queue.put(("log", f"[{i}/{len(targets)}] {sample_name} …"))
                tmp_input_dir = None
                try:
                    source = pair.get("source")
                    if source == "excel":
                        import tempfile
                        tmp_input_dir = Path(tempfile.mkdtemp(prefix="excel_reanalyze_"))
                        with contextlib.redirect_stdout(writer):
                            dc_path, eis_path = excel_to_input_files(
                                pair["excel_path"], tmp_input_dir, sample_name)
                        self._queue.put(("log", f"  imported CP + reference PEIS from {pair['excel_path'].name}"))
                    elif source == "origin_csv":
                        import tempfile
                        tmp_input_dir = Path(tempfile.mkdtemp(prefix="origin_reanalyze_"))
                        with contextlib.redirect_stdout(writer):
                            dc_path, eis_path = origin_export_to_input_files(
                                pair["ca_path"], pair["peis_path"], tmp_input_dir, sample_name)
                        self._queue.put(("log", "  imported CP + reference PEIS from origin_export CSVs"))
                    else:
                        dc_path, eis_path = pair["ca_path"], pair["peis_path"]

                    with contextlib.redirect_stdout(writer):
                        result = runner.recover_and_export(
                            sample_name=sample_name,
                            dc_path=Path(dc_path),
                            eis_path=Path(eis_path),
                            output_root=output_root,
                            auto_trim=settings["auto_trim"],
                        )
                    done += 1
                    self._queue.put(("result", (key, sample_name, result)))
                    self._write_fit_json(output_root, sample_name, result)
                    if exporter is not None:
                        opju_done += self._export_opju(exporter, output_root, sample_name, result)
                except Exception:
                    failed += 1
                    self._queue.put(("status", (key, "FAILED")))
                    self._queue.put(("log", f"  ERROR on {sample_name}:"))
                    self._queue.put(("log", traceback.format_exc()))
                finally:
                    if tmp_input_dir is not None:
                        import shutil
                        shutil.rmtree(tmp_input_dir, ignore_errors=True)
                self._queue.put(("progress", i))
        finally:
            if exporter is not None:
                try:
                    exporter.close()
                except Exception:
                    pass

        tail = f", {opju_done} Origin .opju" if settings.get("build_opju") else ""
        self._queue.put(("log", f"Finished: {done} exported, {failed} failed{tail}."))
        self._queue.put(("finished", None))

    def _write_fit_json(self, output_root, sample_name, result):
        """Persist the fit parameters so build_opju_from_folder.py can redraw the fit."""
        fit = result.get("fit_result")
        if not fit:
            return
        try:
            import json
            path = Path(output_root) / sample_name / "fit_result.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({str(k): v for k, v in fit.items()}, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass  # non-fatal: the Excel still holds the fit params

    def _open_origin_session(self):
        """Create an Origin session for the batch, or log why it is unavailable."""
        try:
            from export_originpro import originpro_status, OriginExporter
        except Exception:
            self._queue.put(("log", "  Origin export module missing; skipping .opju."))
            return None
        ok, version, message = originpro_status()
        if not ok:
            self._queue.put(("log", f"  Origin .opju skipped — {message}"))
            return None
        try:
            exporter = OriginExporter(show=False)
        except Exception:
            self._queue.put(("log", "  Could not start Origin; skipping .opju:"))
            self._queue.put(("log", traceback.format_exc()))
            return None
        self._queue.put(("log", f"  Origin session started (originpro {version})."))
        return exporter

    def _export_opju(self, exporter, output_root, sample_name, result):
        """Build one sample's .opju next to its Excel. Returns 1 on success, else 0."""
        try:
            datasets = _build_eis_datasets(result)
            if not datasets:
                self._queue.put(("log", f"  {sample_name}: no EIS data for .opju; skipped."))
                return 0
            opju_path = Path(output_root) / sample_name / f"{sample_name}.opju"
            exporter.export_sample(
                opju_path, sample_name, datasets,
                cp_data=result.get("dc_data"),
            )
            self._queue.put(("log", f"  Origin project: {opju_path}"))
            return 1
        except Exception:
            self._queue.put(("log", f"  Origin .opju failed for {sample_name}:"))
            self._queue.put(("log", traceback.format_exc()))
            return 0

    # ---- queue polling / UI updates -------------------------------------
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log_line(payload)
                elif kind == "status":
                    key, text = payload
                    self._set_row_status(key, text)
                elif kind == "progress":
                    self._progress.configure(value=payload)
                elif kind == "result":
                    key, sample_name, result = payload
                    self._set_row_status(key, "done", tag="done")
                    self._show_result(sample_name, result)
                elif kind == "finished":
                    self._set_running(False)
                elif kind == "scan_done":
                    folder, pairs = payload
                    self._populate_pairs(folder, pairs)
                    self._end_scan_indicator(f"Scan complete · {len(pairs)}")
                elif kind == "scan_error":
                    folder, message = payload
                    self._end_scan_indicator("Scan failed")
                    self._count_var.set("Scan failed")
                    self._log_line(f"Scan failed for {folder}: {message}")
                    messagebox.showerror("Scan failed", message)
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _set_row_status(self, key, text, tag=None):
        for item, k in self._tree_keys.items():
            if k == key:
                vals = list(self._tree.item(item, "values"))
                vals[4] = text
                self._tree.item(item, values=vals)
                if tag:
                    self._tree.item(item, tags=(tag,))
                break

    def _show_result(self, sample_name, result):
        try:
            self._draw_nyquist(sample_name, result)
        except Exception:
            self._log_line("  (plot failed)\n" + traceback.format_exc())

        fit = result.get("fit_result") or {}
        excel = result.get("excel_path")
        lines = [f"{sample_name}"]
        if excel:
            lines.append(f"Excel: {excel}")
        # Prefer the resistances and any goodness-of-fit metric; the RQRQRQ fit
        # returns keys like 'R0 (Ohm)', 'Q0 ...', 'a0', 'R1 (Ohm)', ... 'Chi2'.
        preferred = [k for k in fit if re.match(r"^R\d", str(k))]
        preferred += [k for k in fit if "chi" in str(k).lower()]
        shown = [f"{k}={fit[k]}" for k in preferred]
        if not shown:
            shown = [f"{k}={v}" for k, v in list(fit.items())[:6]]
        if shown:
            lines.append("  ".join(shown))
        self._fit_var.set("\n".join(lines))

    def _reset_plot(self):
        self._ax.clear()
        self._ax.set_xlabel("Re(Z) / Ω")
        self._ax.set_ylabel("-Im(Z) / Ω")
        self._ax.set_title("Nyquist — run an analysis to populate")
        self._ax.grid(True, alpha=0.25)

    def _draw_nyquist(self, sample_name, result):
        self._ax.clear()
        eis = np.asarray(result.get("eis_data"))
        if eis.ndim == 2 and eis.shape[1] >= 3 and len(eis):
            self._ax.plot(eis[:, 1], -eis[:, 2], color="0.75", linewidth=2.4,
                          label="Reference PEIS")
        f = np.asarray(result.get("filtered_f", []), dtype=float).reshape(-1)
        z = np.asarray(result.get("recovered_z", []))
        if f.size and z.size:
            self._ax.scatter(np.real(z), -np.imag(z), s=24, color=CLR_ORANGE,
                             label="Recovered (CP→EIS)")
        self._ax.set_xlabel("Re(Z) / Ω")
        self._ax.set_ylabel("-Im(Z) / Ω")
        self._ax.set_title(f"Nyquist — {sample_name}")
        self._ax.grid(True, alpha=0.25)
        self._ax.legend(loc="best", fontsize=8)
        try:
            self._ax.set_aspect("equal", adjustable="datalim")
        except Exception:
            pass
        self._fig.tight_layout()
        self._canvas.draw_idle()

    # ---- misc helpers ----------------------------------------------------
    def _set_running(self, running):
        state = "disabled" if running else "normal"
        self._run_sel_btn.configure(state=state)
        self._run_all_btn.configure(state=state)
        self._stop_btn.configure(state="normal" if running else "disabled")

    def _log_line(self, text):
        self._log.configure(state="normal")
        self._log.insert("end", str(text).rstrip() + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")


def main():
    app = AnalysisGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
