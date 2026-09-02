from __future__ import annotations

import argparse
import json
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cp_first_policy import (  # noqa: E402
    DEFAULT_FFT_READY_DESPIKE_SIGMA,
    DEFAULT_FFT_READY_DESPIKE_STEP_GUARD_S,
    DEFAULT_FFT_READY_DESPIKE_WINDOW_S,
    _detect_voltage_step_idx,
)
from make_olecom_full_arc_from_run import (  # noqa: E402
    DEFAULT_MC_ITERATIONS,
    _average_duplicate_freq,
    _build_arc_and_plots,
    _load_and_smooth_raw_ca,
    _load_ca_txt,
    _load_peis_txt,
    _run_file,
    monte_carlo_param_errors,
)
from extract_full_arc_results_to_csv import RQ_R_KEY_RE, extract_row  # noqa: E402

DEFAULT_PARAMS = {
    "zero_pad_factor": 30,
    "n_log_points": 100,
    "duration_guard_periods": 0.0,
    "peis_anchor_gap_decades": 0.0,
    "despike_sigma": DEFAULT_FFT_READY_DESPIKE_SIGMA,
    "despike_window_s": DEFAULT_FFT_READY_DESPIKE_WINDOW_S,
    "despike_step_guard_s": DEFAULT_FFT_READY_DESPIKE_STEP_GUARD_S,
    "average_bin_s": None,
    "segmented_smoothing_window_s": None,
    "voltage_step_sigma": 8.0,
    "smooth_window_points": 11,
    "stability_window_s": 2.0,
    "sustain_window_s": 3.0,
    "std_factor": 2.5,
    "fit_model": "RRQRQ",
    "relative_error": True,
}

# (key, label, kind) -- kind is 'int' | 'float' | 'optional_int' | 'optional_float'
FIELD_GROUPS = [
    (
        "Padding",
        [
            ("zero_pad_factor", "Zero pad factor", "int"),
            ("n_log_points", "N log points", "int"),
            ("duration_guard_periods", "Duration guard (periods)", "float"),
            ("peis_anchor_gap_decades", "PEIS anchor gap (decades)", "float"),
        ],
    ),
    (
        "Smoothing",
        [
            ("despike_sigma", "Despike sigma", "float"),
            ("despike_window_s", "Despike window (s)", "float"),
            ("despike_step_guard_s", "Despike step guard (s)", "float"),
            ("average_bin_s", "Median bin width (s, blank=auto)", "optional_float"),
            ("segmented_smoothing_window_s", "Boxcar smoothing width (s, blank=off)", "optional_float"),
        ],
    ),
    (
        "Baseline auto-trim",
        [
            ("voltage_step_sigma", "Voltage step sigma", "float"),
            ("smooth_window_points", "Trim-detect smooth pts", "int"),
            ("stability_window_s", "Stability window (s)", "float"),
            ("sustain_window_s", "Sustain window (s)", "float"),
            ("std_factor", "Std factor", "float"),
        ],
    ),
]

AUTO_TRIM_KEYS = ("voltage_step_sigma", "smooth_window_points", "stability_window_s", "sustain_window_s", "std_factor")

TUNING_OUTPUT_SUFFIX = "tuning_preview"
CA_TRACE_PREVIEW_FILENAME = f"ca_trace_comparison_{TUNING_OUTPUT_SUFFIX}.png"

# The scrollable left control panel's total on-screen width, and the safe
# text-wrap width for labels inside it (narrower than the panel itself to
# leave room for the vertical scrollbar plus the canvas/frame padding --
# a wraplength equal to or wider than the panel makes text overflow it).
LEFT_PANEL_WIDTH = 360
LEFT_PANEL_TEXT_WRAPLENGTH = 310


def _detect_step_time(t: np.ndarray, v: np.ndarray) -> float:
    """Absolute time of the largest voltage jump in a (t, v) trace."""
    t = np.asarray(t, dtype=float)
    v = np.asarray(v, dtype=float)
    if len(t) < 3:
        return float(t[0]) if len(t) else 0.0
    idx = _detect_voltage_step_idx(np.column_stack([t, v]))
    idx = min(max(int(idx), 0), len(t) - 1)
    return float(t[idx])


def _render_ca_trace_comparison_png(
    out_png: Path,
    t_raw: np.ndarray,
    v_raw: np.ndarray,
    i_raw: np.ndarray,
    t_proc: np.ndarray,
    v_proc: np.ndarray,
    i_proc: np.ndarray,
) -> Path:
    """Raw vs despiked/binned CA current, overlaid -- lets a param change be
    seen directly on the trace, not only in the downstream Nyquist arc.

    Both traces share the raw trace's own detected voltage-step time as a
    single t=0 reference (not each trace's own independently-detected
    step): the processed trace can legitimately have no pre-step data
    left after gap-aware resampling drops a sparse pre-hold entirely, in
    which case there is no step transition left in the processed trace's
    OWN data to detect -- only the raw trace reliably has both sides of
    the step to find it from. A zoomed inset frames the pre-step/step-onset
    region directly -- the pre-step tail is intentionally short (default
    10s) next to a scout segment that can run to several minutes, so on a
    single full-duration linear axis it can look like the pre-step data
    is missing when it is actually just a few percent of the plot width.
    """
    step_t = _detect_step_time(t_raw, v_raw)
    t_raw_rel = np.asarray(t_raw, dtype=float) - step_t
    t_proc_rel = np.asarray(t_proc, dtype=float) - step_t

    i_raw = np.asarray(i_raw, dtype=float) * 1e9
    i_proc = np.asarray(i_proc, dtype=float) * 1e9

    pre_raw = t_raw_rel[t_raw_rel <= 0.0]
    pre_available_s = float(-pre_raw.min()) if len(pre_raw) else 0.0
    zoom_half_width = float(np.clip(pre_available_s * 1.2, 5.0, 60.0))

    fig, (ax_full, ax_zoom) = plt.subplots(1, 2, figsize=(11, 3.4), constrained_layout=True)
    for ax in (ax_full, ax_zoom):
        ax.axvline(0.0, color="#444", lw=0.8, ls="--")
        # Markers, not just connecting lines -- a sparsely-sampled region
        # (e.g. a flat pre-hold EC-Lab barely recorded) should visibly show
        # as gaps between dots, not a smooth line implying continuous data.
        ax.plot(t_raw_rel, i_raw, color="#b0b0b0", lw=0.6, marker=".", ms=2,
                 label=f"raw ({len(t_raw)} pts)")
        ax.plot(t_proc_rel, i_proc, color="#d62728", lw=1.0, marker=".", ms=2,
                 label=f"processed ({len(t_proc)} pts)")
        ax.set_xlabel("time relative to voltage step / s")
        ax.set_ylabel("current / nA")
        ax.grid(alpha=0.25)
    ax_full.set_title("Full trace (t=0 at voltage step)")
    ax_full.legend(fontsize=8)
    ax_zoom.set_xlim(-zoom_half_width, zoom_half_width)
    ax_zoom.set_title(f"Zoomed near step (+/-{zoom_half_width:.3g}s)")
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_png


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _prefill_defaults(run_dir: Path) -> dict:
    """Seed fields from this run's own full_arc_summary.json where possible.

    Falls back to DEFAULT_PARAMS for anything not recorded there (despike/
    auto-trim knobs aren't persisted anywhere today, only whether despiking
    was on).
    """
    params = dict(DEFAULT_PARAMS)
    full_arc = _load_json(run_dir / "full_arc_summary.json")
    if full_arc.get("zero_pad_factor"):
        params["zero_pad_factor"] = int(full_arc["zero_pad_factor"])
    if full_arc.get("n_log_points"):
        params["n_log_points"] = int(full_arc["n_log_points"])
    if full_arc.get("fft_duration_guard_periods") is not None:
        params["duration_guard_periods"] = float(full_arc["fft_duration_guard_periods"])
    if full_arc.get("fft_peis_anchor_gap_decades") is not None:
        params["peis_anchor_gap_decades"] = float(full_arc["fft_peis_anchor_gap_decades"])
    if full_arc.get("fit_model"):
        params["fit_model"] = str(full_arc["fit_model"])
    if full_arc.get("fit_relative_error") is not None:
        params["relative_error"] = bool(full_arc["fit_relative_error"])
    return params


def _parse_field(raw: str, kind: str):
    text = (raw or "").strip()
    if kind == "int":
        return int(float(text))
    if kind == "float":
        return float(text)
    if kind == "optional_int":
        return int(float(text)) if text else None
    if kind == "optional_float":
        return float(text) if text else None
    raise ValueError(f"Unknown field kind: {kind}")


def _list_sibling_row_dirs(run_dir: Path) -> list[Path]:
    """Every directory alongside run_dir (same parent) that looks like a row
    result folder -- i.e. has a measured_peis.txt -- sorted alphabetically.

    This is what the Prev/Next buttons and "Save fits" batch action walk;
    run_dir's own parent directory is treated as "the results folder."
    """
    parent = run_dir.resolve().parent
    if not parent.is_dir():
        return []
    candidates = [
        child for child in parent.iterdir()
        if child.is_dir() and (child / "measured_peis.txt").exists()
    ]
    return sorted(candidates, key=lambda p: p.name)


def _summary_json_path(run_dir: Path, output_suffix: str) -> Path:
    suffix = f"_{output_suffix}" if output_suffix else ""
    return run_dir / f"full_arc_summary{suffix}.json"


def _format_number(value) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return str(value)


def _format_value_with_error(value, error, mc_error=None, mc_n_success=None, mc_n_iterations=None) -> str:
    """"X", "X +/- err", or "X +/- err  [MC +/- mc_err, N=success/iterations]".

    err/mc_error are each omitted independently whenever missing/non-finite
    (e.g. a fit whose Jacobian was singular, an older JSON predating the
    error-bar feature, or Monte Carlo never having been run for this row)
    rather than showing a misleading "+/- --"."""
    text = _format_number(value)
    if value is None:
        return text
    try:
        err = float(error)
    except (TypeError, ValueError):
        err = None
    if err is not None and np.isfinite(err):
        text = f"{text} +/- {_format_number(err)}"
    try:
        mc_err = float(mc_error)
    except (TypeError, ValueError):
        mc_err = None
    if mc_err is not None and np.isfinite(mc_err):
        n_part = f", N={mc_n_success}/{mc_n_iterations}" if mc_n_success is not None else ""
        text = f"{text}  [MC +/- {_format_number(mc_err)}{n_part}]"
    return text


def _format_condition_line(extracted: dict) -> str:
    """Surfaces the fit Jacobian's conditioning next to the error bars it
    governs -- an ill-conditioned fit's "+/-" values below are numerically
    unreliable even after the SVD-based fix (see EIS_Fitting.py's
    param_std_errors), so this is the one place that tells you not to trust
    them at a glance. Also shows which residual weighting produced the
    displayed fit -- relative_error=False changes the actual fitted
    parameters, not just their uncertainty, so it needs to be visible at a
    glance too, not just recorded in the saved JSON."""
    cond = extracted.get("fit_condition_number")
    line = f"Fit condition number: {_format_number(cond)}"
    if extracted.get("fit_errors_ill_conditioned"):
        line += "  [ILL-CONDITIONED -- error bars below are unreliable]"
    relative_error = extracted.get("fit_relative_error")
    if relative_error is False:
        line += "\nWeighting: ABSOLUTE (unweighted) residuals"
    elif relative_error is True:
        line += "\nWeighting: relative residuals"
    return line


def _format_extracted_row(result: dict, extracted: dict, warnings: list[str]) -> str:
    """Render _build_arc_and_plots's FFT diagnostics plus extract_row's
    circuit-parameter breakdown -- the same derivation the CSV extractor
    uses, so the GUI and the CSV never disagree."""
    lines = [
        f"Nyquist peak reached: {result.get('nyquist_peak_reached')}  |  "
        f"FFT points used: {result.get('fft_points_used')}  |  "
        f"FFT min used: {_format_number(result.get('fft_min_used_hz'))} Hz",
        f"Fit model: {extracted.get('fit_model')}   "
        f"Fit quality score: {_format_number(extracted.get('fit_score'))}   "
        f"Fit cost: {_format_number(extracted.get('fit_cost'))}",
        _format_condition_line(extracted),
    ]
    rq_indices = sorted({int(m.group(1)) for k in extracted if (m := RQ_R_KEY_RE.match(k))})
    if not rq_indices:
        lines.append("(no RQ-element breakdown available -- the fit may have failed)")
    else:
        # Rs is a genuine model feature, not a fallback signal: RRQRQ has one,
        # but RQRQ/RQRQRQ don't (see EIS_Fitting.py's MODEL_PARAM_KEYS) -- so
        # its absence here is normal for those models, not an error.
        mc_n_success = extracted.get("mc_n_success")
        mc_n_iterations = extracted.get("mc_n_iterations")
        if extracted.get("Rs_ohm") is not None:
            rs_cm2 = extracted.get("Rs_ohm_cm2")
            lines.append(
                f"Rs: {_format_value_with_error(extracted['Rs_ohm'], extracted.get('Rs_err_ohm'), extracted.get('Rs_mc_err_ohm'), mc_n_success, mc_n_iterations)} ohm"
                + (f"  ({_format_number(rs_cm2)} ohm*cm2)" if rs_cm2 is not None else "")
            )
        for idx in rq_indices:
            r = extracted.get(f"RQ{idx}_R_ohm")
            r_err = extracted.get(f"RQ{idx}_R_err_ohm")
            r_mc_err = extracted.get(f"RQ{idx}_R_mc_err_ohm")
            r_cm2 = extracted.get(f"RQ{idx}_R_ohm_cm2")
            a = extracted.get(f"RQ{idx}_alpha")
            a_err = extracted.get(f"RQ{idx}_alpha_err")
            a_mc_err = extracted.get(f"RQ{idx}_alpha_mc_err")
            f = extracted.get(f"RQ{idx}_fchar_Hz")
            frac = extracted.get(f"RQ{idx}_resistance_fraction")
            q = extracted.get(f"RQ{idx}_Q_F_s_alpha_minus_1")
            q_err = extracted.get(f"RQ{idx}_Q_err_F_s_alpha_minus_1")
            q_mc_err = extracted.get(f"RQ{idx}_Q_mc_err_F_s_alpha_minus_1")
            c = extracted.get(f"RQ{idx}_C_F")
            c_cm2 = extracted.get(f"RQ{idx}_C_F_cm2")
            # Capacitance has no Jacobian-based error at all (see
            # extract_full_arc_results_to_csv.py's comment on RQ{i}_C_mc_err_F):
            # it's a nonlinear function of R, Q, and alpha together, so
            # propagating the Jacobian covariance through it would need the
            # R/Q/alpha cross-covariance terms, which aren't tracked. Monte
            # Carlo doesn't have that problem -- it re-derives capacitance
            # fresh per bootstrap refit -- so this is MC-only, unlike R/Q/alpha.
            c_mc_err = extracted.get(f"RQ{idx}_C_mc_err_F")
            lines.append(
                f"RQ{idx}: R={_format_value_with_error(r, r_err, r_mc_err, mc_n_success, mc_n_iterations)} ohm"
                + (f" ({_format_number(r_cm2)} ohm*cm2)" if r_cm2 is not None else "")
                + f"  alpha={_format_value_with_error(a, a_err, a_mc_err, mc_n_success, mc_n_iterations)}"
                + f"  fchar={_format_number(f)} Hz  frac={_format_number(frac)}"
            )
            lines.append(
                f"      Q={_format_value_with_error(q, q_err, q_mc_err, mc_n_success, mc_n_iterations)} F*s^(a-1)"
            )
            lines.append(
                f"      C={_format_value_with_error(c, None, c_mc_err, mc_n_success, mc_n_iterations)} F"
                + (f" ({_format_number(c_cm2)} F/cm2)" if c_cm2 is not None else "")
            )
        r1_plus_r2_cm2 = extracted.get("R1+R2_ohm_cm2")
        lines.append(
            f"R1+R2: {_format_value_with_error(extracted.get('R1+R2_ohm'), extracted.get('R1+R2_err_ohm'), extracted.get('R1+R2_mc_err_ohm'), mc_n_success, mc_n_iterations)} ohm"
            + (f"  ({_format_number(r1_plus_r2_cm2)} ohm*cm2)" if r1_plus_r2_cm2 is not None else "")
        )
    if warnings:
        lines.append("Warnings: " + "; ".join(warnings))
    return "\n".join(lines)


def _merge_monte_carlo_into_result(result: dict, mc: dict) -> dict:
    """Split monte_carlo_param_errors' flat return dict into its three kinds
    of field (raw per-parameter "<key> Monte Carlo error", RRQRQ component
    rollup "RQ1_R_mc_err_ohm"-style, and "mc_n_iterations"/"mc_n_success"
    metadata) and merge each into the right spot of a full_arc_summary.json-
    shaped `result` dict -- shared by the single-row Monte Carlo action and
    the "Save fits + Monte Carlo" batch action so both write the exact same
    shape, in place (mutates and returns `result`)."""
    mc_fit_fields = {k: v for k, v in mc.items() if k.endswith("Monte Carlo error")}
    mc_meta_fields = {"mc_n_iterations": mc.get("mc_n_iterations"), "mc_n_success": mc.get("mc_n_success")}
    mc_component_fields = {
        k: v for k, v in mc.items()
        if k not in mc_fit_fields and k not in ("mc_n_iterations", "mc_n_success")
    }

    result.update(mc_meta_fields)
    merged_fit = dict(result.get("fit") or {})
    merged_fit.update(mc_fit_fields)
    result["fit"] = merged_fit
    if mc_component_fields and isinstance(result.get("rrqrq_components"), dict):
        merged_components = dict(result["rrqrq_components"])
        merged_components.update(mc_component_fields)
        result["rrqrq_components"] = merged_components
    return result


class CAFFTTuningWindow(tk.Tk):
    def __init__(self, run_dir: Path):
        super().__init__()
        self._apply_portable_ttk_theme()
        self._size_to_screen()

        self._vars: dict[str, tk.StringVar] = {}
        self._ca_trace_image = None
        self._nyquist_image = None
        self._last_result: dict | None = None
        self.run_dir = Path(run_dir).resolve()
        self._raw_ca_txt = self._resolve_raw_ca_txt(self.run_dir)
        self._peis_txt = self._resolve_peis_txt(self.run_dir)
        self._siblings: list[Path] = []
        self._sibling_index: int | None = None

        self._build_layout()
        self._load_run_dir(self.run_dir)
        self._load_initial_values()

    def _apply_portable_ttk_theme(self):
        """macOS's native "aqua" ttk theme has a longstanding Tk bug where
        ttk.Button/Checkbutton/Combobox text can render illegibly (e.g.
        white-on-white) under system Dark Mode, since ttk widgets are
        theme-drawn and don't take plain fg=/bg= overrides. "clam" draws its
        own colors instead of deferring to native (dark-mode-aware) chrome,
        so it stays legible in both light and dark mode."""
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

    def _size_to_screen(self):
        """Never request a window bigger than the actual screen -- the fixed
        1180x980 default used to overflow smaller/laptop displays entirely."""
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = max(900, min(1180, screen_w - 100))
        height = max(600, min(900, screen_h - 140))
        self.geometry(f"{width}x{height}")
        # Cached (not read back via winfo_width()/height()) so preview image
        # caps have a value immediately, even before the window is mapped --
        # matters for headless tests that call .withdraw() right after init.
        self._window_w = width
        self._window_h = height

    def _resolve_raw_ca_txt(self, run_dir: Path) -> Path:
        summary = _load_json(run_dir / "summary.json")
        return _run_file(run_dir, summary.get("ca_fft_raw_txt"), "ca_for_fft_pre_plus_scout_only.txt")

    def _resolve_peis_txt(self, run_dir: Path) -> Path:
        summary = _load_json(run_dir / "summary.json")
        return _run_file(run_dir, summary.get("peis_txt"), "measured_peis.txt")

    def _load_run_dir(self, new_dir: Path):
        """Point the window at a (possibly different) row folder without
        touching the current parameter form values -- Prev/Next navigation
        is meant to compare one fixed parameter set across many folders."""
        self.run_dir = Path(new_dir).resolve()
        self.title(f"CA-FFT Nyquist Tuning -- {self.run_dir.name}")
        self._raw_ca_txt = self._resolve_raw_ca_txt(self.run_dir)
        self._peis_txt = self._resolve_peis_txt(self.run_dir)
        self._siblings = _list_sibling_row_dirs(self.run_dir)
        self._sibling_index = (
            self._siblings.index(self.run_dir) if self.run_dir in self._siblings else None
        )
        self._status_var.set(f"raw CA: {self._raw_ca_txt.name} | PEIS: {self._peis_txt.name}")
        self._update_position_label()
        self._update_nav_buttons()

    def _update_position_label(self):
        self._position_var.set(self.run_dir.name)
        if self._sibling_index is None or not self._siblings:
            self._goto_var.set("")
            self._goto_of_var.set("")
        else:
            self._goto_var.set(str(self._sibling_index + 1))
            self._goto_of_var.set(f"of {len(self._siblings)}")

    def _update_nav_buttons(self):
        has_prev = self._sibling_index is not None and self._sibling_index > 0
        has_next = self._sibling_index is not None and self._sibling_index < len(self._siblings) - 1
        self._prev_btn.config(state="normal" if has_prev else "disabled")
        self._next_btn.config(state="normal" if has_next else "disabled")

    def _on_prev(self):
        self._navigate(-1)

    def _on_next(self):
        self._navigate(1)

    def _navigate(self, step: int):
        if self._sibling_index is None:
            return
        new_index = self._sibling_index + step
        if not (0 <= new_index < len(self._siblings)):
            return
        self._load_run_dir(self._siblings[new_index])
        self._on_recompute()

    def _on_goto(self):
        if not self._siblings:
            return
        text = self._goto_var.get().strip()
        current = (self._sibling_index or 0) + 1
        try:
            n = int(text)
        except ValueError:
            messagebox.showerror("Go to row", f"Not a number: {text!r}")
            self._goto_var.set(str(current))
            return
        if not (1 <= n <= len(self._siblings)):
            messagebox.showerror(
                "Go to row", f"Enter a number from 1 to {len(self._siblings)} (got {n})."
            )
            self._goto_var.set(str(current))
            return
        new_index = n - 1
        if new_index == self._sibling_index:
            return
        self._load_run_dir(self._siblings[new_index])
        self._on_recompute()

    def _build_scrollable_left(self, outer: tk.Frame) -> tk.Frame:
        """The left control panel now carries a growing amount of content
        (multi-model RQ-element results, Prev/Next, Save fits) -- wrap it in
        a scrolling canvas so it never forces the window taller than the
        screen; only the window's own size is capped (_size_to_screen), not
        this panel's content.
        """
        container = tk.Frame(outer, width=LEFT_PANEL_WIDTH)
        container.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        container.grid_propagate(False)

        canvas = tk.Canvas(container, highlightthickness=0, width=LEFT_PANEL_WIDTH)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        inner = tk.Frame(canvas)
        inner_window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(inner_window, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            step = -1 if event.delta > 0 else 1
            canvas.yview_scroll(step, "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        return inner

    def _build_layout(self):
        outer = tk.Frame(self)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = self._build_scrollable_left(outer)

        nav_row = tk.Frame(left)
        nav_row.pack(fill="x", pady=(0, 2))
        self._prev_btn = ttk.Button(nav_row, text="< Prev", command=self._on_prev)
        self._prev_btn.pack(side="left")
        self._goto_var = tk.StringVar(value="")
        goto_entry = tk.Entry(nav_row, textvariable=self._goto_var, width=4, justify="center")
        goto_entry.pack(side="left", padx=(6, 2))
        goto_entry.bind("<Return>", lambda _e: self._on_goto())
        self._goto_of_var = tk.StringVar(value="")
        tk.Label(nav_row, textvariable=self._goto_of_var).pack(side="left")
        ttk.Button(nav_row, text="Go", command=self._on_goto, width=3).pack(side="left", padx=(4, 4))
        self._next_btn = ttk.Button(nav_row, text="Next >", command=self._on_next)
        self._next_btn.pack(side="left")

        name_row = tk.Frame(left)
        name_row.pack(fill="x", pady=(0, 6))
        self._position_var = tk.StringVar(value="")
        tk.Label(
            name_row, textvariable=self._position_var, anchor="w",
            font=("Segoe UI", 8), fg="#666",
            wraplength=LEFT_PANEL_TEXT_WRAPLENGTH, justify="left",
        ).pack(fill="x")

        for group_name, fields in FIELD_GROUPS:
            box = tk.LabelFrame(left, text=group_name, padx=8, pady=6)
            box.pack(fill="x", pady=(0, 8))
            box.columnconfigure(0, weight=1)
            for row, (key, label, kind) in enumerate(fields):
                tk.Label(
                    box, text=label, anchor="w", justify="left",
                    wraplength=LEFT_PANEL_WIDTH - 130,
                ).grid(row=row, column=0, sticky="w", pady=2, padx=(0, 8))
                var = tk.StringVar(value="")
                tk.Entry(box, textvariable=var, width=9).grid(row=row, column=1, sticky="e", pady=2)
                self._vars[key] = var

        fit_box = tk.LabelFrame(left, text="Fit", padx=8, pady=6)
        fit_box.pack(fill="x", pady=(0, 8))
        tk.Label(fit_box, text="Model", anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 8))
        fit_var = tk.StringVar(value="RRQRQ")
        ttk.Combobox(
            fit_box, textvariable=fit_var, width=12, state="readonly",
            values=["RRQ", "RQRQ", "RRQRQ", "RQRQRQ"],
        ).grid(row=0, column=1, sticky="w")
        self._vars["fit_model"] = fit_var

        self._relative_error_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            fit_box, text="Relative error (uncheck to fit unweighted/absolute residuals)",
            variable=self._relative_error_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btn_row = tk.Frame(left)
        btn_row.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row, text="Recompute", command=self._on_recompute).pack(side="left")
        ttk.Button(btn_row, text="Save parameters", command=self._on_save_params).pack(side="left", padx=(6, 0))
        btn_row2 = tk.Frame(left)
        btn_row2.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row2, text="Save fits", command=self._on_save_fits).pack(side="left")

        mc_row = tk.Frame(left)
        mc_row.pack(fill="x", pady=(4, 0))
        ttk.Button(mc_row, text="Monte Carlo confidence", command=self._on_monte_carlo).pack(side="left")
        tk.Label(mc_row, text="N=").pack(side="left", padx=(6, 2))
        self._mc_n_var = tk.StringVar(value=str(DEFAULT_MC_ITERATIONS))
        tk.Entry(mc_row, textvariable=self._mc_n_var, width=4, justify="center").pack(side="left")
        tk.Label(mc_row, text="bins=").pack(side="left", padx=(6, 2))
        self._mc_n_freq_bins_var = tk.StringVar(value="1")
        tk.Entry(mc_row, textvariable=self._mc_n_freq_bins_var, width=3, justify="center").pack(side="left")

        mc_batch_row = tk.Frame(left)
        mc_batch_row.pack(fill="x", pady=(4, 0))
        ttk.Button(
            mc_batch_row, text="Save fits + Monte Carlo", command=self._on_save_fits_with_monte_carlo
        ).pack(side="left")
        tk.Label(
            mc_batch_row, text="(uses N above -- very slow across many folders)",
            font=("Segoe UI", 8), fg="#666", wraplength=LEFT_PANEL_TEXT_WRAPLENGTH, justify="left",
        ).pack(side="left", padx=(6, 0))

        self._status_var = tk.StringVar(value="")
        tk.Label(
            left, textvariable=self._status_var,
            wraplength=LEFT_PANEL_TEXT_WRAPLENGTH, justify="left", fg="#444",
        ).pack(fill="x", pady=(8, 0))
        self._metrics_var = tk.StringVar(value="")
        tk.Label(
            left, textvariable=self._metrics_var,
            wraplength=LEFT_PANEL_TEXT_WRAPLENGTH, justify="left",
        ).pack(fill="x", pady=(6, 0))

        right = tk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=0)
        right.rowconfigure(1, weight=1)

        self._ca_trace_label = tk.Label(
            right, text="CA trace preview appears here after Recompute", bg="#f4f4f4"
        )
        self._ca_trace_label.grid(row=0, column=0, sticky="new", pady=(0, 6))

        self._nyquist_label = tk.Label(
            right, text="Nyquist preview appears here after Recompute", bg="#f4f4f4"
        )
        self._nyquist_label.grid(row=1, column=0, sticky="nsew")

    def _load_initial_values(self):
        params = _prefill_defaults(self.run_dir)
        for key, var in self._vars.items():
            value = params.get(key)
            var.set("" if value is None else str(value))
        # Not in self._vars (a BooleanVar, not a text field the generic
        # FIELD_GROUPS loop parses) -- set explicitly.
        self._relative_error_var.set(bool(params.get("relative_error", True)))

    def _read_params(self) -> dict:
        out = {}
        for group_name, fields in FIELD_GROUPS:
            for key, _label, kind in fields:
                out[key] = _parse_field(self._vars[key].get(), kind)
        out["fit_model"] = self._vars["fit_model"].get().strip() or "RRQRQ"
        out["relative_error"] = bool(self._relative_error_var.get())
        return out

    def _on_recompute(self):
        try:
            params = self._read_params()
        except ValueError as exc:
            messagebox.showerror("Invalid parameter", str(exc))
            return
        try:
            auto_trim_kwargs = {k: params[k] for k in AUTO_TRIM_KEYS}
            t, v, i = _load_and_smooth_raw_ca(
                self._raw_ca_txt,
                average_bin_s=params["average_bin_s"],
                despike_sigma=params["despike_sigma"],
                despike_window_s=params["despike_window_s"],
                despike_step_guard_s=params["despike_step_guard_s"],
                segmented_smoothing_window_s=params["segmented_smoothing_window_s"],
                auto_trim_kwargs=auto_trim_kwargs,
            )
            t_raw, v_raw, i_raw = _load_ca_txt(self._raw_ca_txt)
            ca_trace_png = _render_ca_trace_comparison_png(
                self.run_dir / CA_TRACE_PREVIEW_FILENAME, t_raw, v_raw, i_raw, t, v, i,
            )
            peis = _average_duplicate_freq(_load_peis_txt(self._peis_txt))
            result = _build_arc_and_plots(
                self.run_dir,
                t,
                v,
                i,
                peis,
                zero_pad_factor=params["zero_pad_factor"],
                n_log_points=params["n_log_points"],
                duration_guard_periods=params["duration_guard_periods"],
                peis_anchor_gap_decades=params["peis_anchor_gap_decades"],
                output_suffix=TUNING_OUTPUT_SUFFIX,
                fit_model=params["fit_model"],
                relative_error=params["relative_error"],
                ca_fft_input_label="re-smoothed by tuning GUI",
                extra_summary_fields={"ca_fft_processing": "raw_ca_reprocessed_by_tuning_gui"},
            )
        except Exception as exc:
            messagebox.showerror("Recompute failed", str(exc))
            return
        # Sized off the window's own (screen-capped) dimensions, not fixed
        # pixel constants -- otherwise these previews alone could demand a
        # window bigger than the screen on smaller displays.
        avail_w = max(400, self._window_w - LEFT_PANEL_WIDTH - 40)
        self._ca_trace_image = self._show_preview(
            self._ca_trace_label, ca_trace_png, max_w=avail_w, max_h=max(160, int(self._window_h * 0.26))
        )
        self._nyquist_image = self._show_preview(
            self._nyquist_label, Path(result["nyquist_equal_aspect_png"]),
            max_w=avail_w, max_h=max(300, int(self._window_h * 0.58)),
        )
        self._status_var.set(f"Saved: {ca_trace_png.name}, {Path(result['nyquist_equal_aspect_png']).name}")
        # Recompute always invalidates any earlier Monte Carlo run (new
        # parameters/data mean the old bootstrap samples no longer apply) --
        # _on_monte_carlo requires this to be freshly set before it will run.
        self._last_result = result
        warnings: list[str] = []
        extracted = extract_row(_summary_json_path(self.run_dir, TUNING_OUTPUT_SUFFIX), warnings)
        self._metrics_var.set(_format_extracted_row(result, extracted, warnings))

    def _on_save_params(self):
        try:
            params = self._read_params()
        except ValueError as exc:
            messagebox.showerror("Invalid parameter", str(exc))
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = self.run_dir / f"tuning_params_{stamp}.json"
        out_path.write_text(json.dumps(params, indent=2), encoding="utf-8")
        self._status_var.set(f"Parameters saved: {out_path.name}")

    def _on_save_fits(self):
        try:
            params = self._read_params()
        except ValueError as exc:
            messagebox.showerror("Invalid parameter", str(exc))
            return
        siblings = self._siblings or [self.run_dir]
        if not messagebox.askyesno(
            "Save fits",
            f"This will overwrite full_arc_summary.json in all {len(siblings)} "
            f"folder(s) under {self.run_dir.parent} using the current "
            "parameters. Continue?",
        ):
            return
        auto_trim_kwargs = {k: params[k] for k in AUTO_TRIM_KEYS}
        failures: list[tuple[str, str]] = []
        saved = 0
        for idx, folder in enumerate(siblings, start=1):
            self._status_var.set(f"Refitting {idx}/{len(siblings)}: {folder.name} ...")
            self.update_idletasks()
            try:
                raw_ca_txt = self._resolve_raw_ca_txt(folder)
                peis_txt = self._resolve_peis_txt(folder)
                t, v, i = _load_and_smooth_raw_ca(
                    raw_ca_txt,
                    average_bin_s=params["average_bin_s"],
                    despike_sigma=params["despike_sigma"],
                    despike_window_s=params["despike_window_s"],
                    despike_step_guard_s=params["despike_step_guard_s"],
                    segmented_smoothing_window_s=params["segmented_smoothing_window_s"],
                    auto_trim_kwargs=auto_trim_kwargs,
                )
                peis = _average_duplicate_freq(_load_peis_txt(peis_txt))
                _build_arc_and_plots(
                    folder,
                    t,
                    v,
                    i,
                    peis,
                    zero_pad_factor=params["zero_pad_factor"],
                    n_log_points=params["n_log_points"],
                    duration_guard_periods=params["duration_guard_periods"],
                    peis_anchor_gap_decades=params["peis_anchor_gap_decades"],
                    output_suffix="",
                    fit_model=params["fit_model"],
                    relative_error=params["relative_error"],
                    ca_fft_input_label="re-smoothed by tuning GUI (Save fits)",
                    extra_summary_fields={
                        "ca_fft_processing": "raw_ca_reprocessed_by_tuning_gui",
                        "manual_override": True,
                        "manual_override_source": "ca_fft_tuning_gui_batch_save",
                    },
                )
                saved += 1
            except Exception as exc:
                failures.append((folder.name, str(exc)))
        summary_lines = [f"Saved {saved}/{len(siblings)}."]
        if failures:
            summary_lines.append("Failed:")
            summary_lines.extend(f"  {name}: {err}" for name, err in failures)
        self._status_var.set(summary_lines[0])
        messagebox.showinfo("Save fits", "\n".join(summary_lines))

    def _on_save_fits_with_monte_carlo(self):
        """Same batch refit as _on_save_fits, but also runs Monte Carlo
        confidence (monte_carlo_param_errors) on every sibling folder and
        merges the result in before writing -- i.e. "generate MC error data
        for all of the Nyquist plots" in one run, rather than one row at a
        time via _on_monte_carlo. Deliberately a separate button from plain
        "Save fits": Monte Carlo multiplies the time per folder by roughly
        N (a full refit per bootstrap iteration), so this can take a very
        long time across many folders -- users who just want a fast batch
        refit should not pay that cost by accident."""
        try:
            params = self._read_params()
        except ValueError as exc:
            messagebox.showerror("Invalid parameter", str(exc))
            return
        try:
            n_iterations = _parse_field(self._mc_n_var.get(), "int")
            n_freq_bins = _parse_field(self._mc_n_freq_bins_var.get(), "int")
        except ValueError as exc:
            messagebox.showerror("Invalid parameter", str(exc))
            return
        if n_iterations < 1:
            messagebox.showerror("Invalid parameter", "N must be at least 1.")
            return
        if n_freq_bins < 1:
            messagebox.showerror("Invalid parameter", "Freq bins must be at least 1.")
            return

        siblings = self._siblings or [self.run_dir]
        mode_label = "relative" if params["relative_error"] else "absolute (unweighted)"
        if not messagebox.askyesno(
            "Save fits + Monte Carlo",
            f"This will overwrite full_arc_summary.json in all {len(siblings)} "
            f"folder(s) under {self.run_dir.parent} using the current "
            f"parameters ({mode_label} residuals), and run Monte Carlo "
            f"(N={n_iterations}) on each one. Monte Carlo alone can take a "
            "long time per folder (every iteration is a full refit) -- "
            "across many folders this could run for a long time with the "
            "window unresponsive. Continue?",
        ):
            return

        auto_trim_kwargs = {k: params[k] for k in AUTO_TRIM_KEYS}
        failures: list[tuple[str, str]] = []
        mc_failures: list[tuple[str, str]] = []
        saved = 0
        for idx, folder in enumerate(siblings, start=1):
            self._status_var.set(f"Refitting {idx}/{len(siblings)}: {folder.name} ...")
            self.update_idletasks()
            try:
                raw_ca_txt = self._resolve_raw_ca_txt(folder)
                peis_txt = self._resolve_peis_txt(folder)
                t, v, i = _load_and_smooth_raw_ca(
                    raw_ca_txt,
                    average_bin_s=params["average_bin_s"],
                    despike_sigma=params["despike_sigma"],
                    despike_window_s=params["despike_window_s"],
                    despike_step_guard_s=params["despike_step_guard_s"],
                    segmented_smoothing_window_s=params["segmented_smoothing_window_s"],
                    auto_trim_kwargs=auto_trim_kwargs,
                )
                peis = _average_duplicate_freq(_load_peis_txt(peis_txt))
                result = _build_arc_and_plots(
                    folder,
                    t,
                    v,
                    i,
                    peis,
                    zero_pad_factor=params["zero_pad_factor"],
                    n_log_points=params["n_log_points"],
                    duration_guard_periods=params["duration_guard_periods"],
                    peis_anchor_gap_decades=params["peis_anchor_gap_decades"],
                    output_suffix="",
                    fit_model=params["fit_model"],
                    relative_error=params["relative_error"],
                    ca_fft_input_label="re-smoothed by tuning GUI (Save fits + Monte Carlo)",
                    extra_summary_fields={
                        "ca_fft_processing": "raw_ca_reprocessed_by_tuning_gui",
                        "manual_override": True,
                        "manual_override_source": "ca_fft_tuning_gui_batch_save_monte_carlo",
                    },
                )
                saved += 1
            except Exception as exc:
                failures.append((folder.name, str(exc)))
                continue

            self._status_var.set(
                f"Monte Carlo {idx}/{len(siblings)}: {folder.name} (N={n_iterations}, this can take a while) ..."
            )
            self.update_idletasks()
            try:
                full_arc_txt = Path(result["full_arc_txt"])
                mc_data = np.loadtxt(full_arc_txt, skiprows=1)
                mc_freq, mc_re, mc_im = mc_data[:, 0], mc_data[:, 1], mc_data[:, 2]
                fit = result.get("fit") or {}
                model = result.get("fit_model") or "RRQ"
                mc = monte_carlo_param_errors(
                    mc_freq, mc_re, mc_im, fit, model, n_iterations=n_iterations,
                    relative_error=params["relative_error"], n_freq_bins=n_freq_bins,
                )
                if mc is None:
                    mc_failures.append((folder.name, "fewer than the minimum successful refits converged"))
                    continue
                _merge_monte_carlo_into_result(result, mc)
                summary_path = _summary_json_path(folder, "")
                summary_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            except Exception as exc:
                # The refit itself already saved successfully above -- a
                # Monte Carlo failure on one folder shouldn't be treated as
                # if that folder's whole refit was lost.
                mc_failures.append((folder.name, str(exc)))

        mc_added = saved - len(mc_failures)
        summary_lines = [f"Saved {saved}/{len(siblings)} refits; Monte Carlo added to {mc_added}/{saved}."]
        if failures:
            summary_lines.append("Refit failures:")
            summary_lines.extend(f"  {name}: {err}" for name, err in failures)
        if mc_failures:
            summary_lines.append("Monte Carlo failures (refit itself was still saved):")
            summary_lines.extend(f"  {name}: {err}" for name, err in mc_failures)
        self._status_var.set(summary_lines[0])
        messagebox.showinfo("Save fits + Monte Carlo", "\n".join(summary_lines))

    def _on_monte_carlo(self):
        """Residual-resampling ("adapted Monte-Carlo", the method BioLogic's
        ZFit uses -- see monte_carlo_param_errors' docstring) confidence for
        the row currently shown, as a slower, explicitly-triggered
        alternative/complement to the always-on Jacobian-based error bars.
        Requires a prior Recompute in this session (self._last_result) --
        it resamples that fit's own residuals, so there is nothing to
        resample from before one exists."""
        if self._last_result is None:
            messagebox.showerror(
                "Monte Carlo confidence",
                "Run Recompute first -- Monte Carlo resamples the residuals of the most recent fit.",
            )
            return
        try:
            n_iterations = _parse_field(self._mc_n_var.get(), "int")
            n_freq_bins = _parse_field(self._mc_n_freq_bins_var.get(), "int")
        except ValueError as exc:
            messagebox.showerror("Invalid parameter", str(exc))
            return
        if n_iterations < 1:
            messagebox.showerror("Invalid parameter", "N must be at least 1.")
            return
        if n_freq_bins < 1:
            messagebox.showerror("Invalid parameter", "Freq bins must be at least 1.")
            return

        full_arc_txt = Path(self._last_result["full_arc_txt"])
        try:
            data = np.loadtxt(full_arc_txt, skiprows=1)
        except Exception as exc:
            messagebox.showerror("Monte Carlo confidence", f"Could not reload {full_arc_txt.name}: {exc}")
            return
        freq, re_z, im_z = data[:, 0], data[:, 1], data[:, 2]

        # The weighting the *displayed* fit actually used -- not whatever the
        # checkbox currently shows, in case it was toggled after Recompute
        # but before clicking this. Monte Carlo resamples that fit's own
        # residuals, so it must match what produced them, not the UI's
        # current (possibly since-changed) state.
        relative_error = bool(self._last_result.get("fit_relative_error", True))
        mode_label = "relative" if relative_error else "absolute (unweighted)"
        self._status_var.set(
            f"Running Monte Carlo ({n_iterations} refits, {mode_label} residuals) -- this can take a while..."
        )
        self.update_idletasks()
        fit = self._last_result.get("fit") or {}
        model = self._last_result.get("fit_model") or "RRQ"
        try:
            mc = monte_carlo_param_errors(
                freq, re_z, im_z, fit, model, n_iterations=n_iterations,
                relative_error=relative_error, n_freq_bins=n_freq_bins,
            )
        except Exception as exc:
            # A button callback that raises is otherwise swallowed by
            # Tkinter's default exception handler (traceback to stderr,
            # nothing shown in the window) -- looks exactly like "the button
            # did nothing" from the GUI, so this must never be unguarded.
            self._status_var.set("Monte Carlo failed -- see error dialog.")
            messagebox.showerror("Monte Carlo confidence", f"{type(exc).__name__}: {exc}")
            return
        if mc is None:
            msg = f"Fewer than the minimum successful refits converged out of {n_iterations} attempted."
            self._status_var.set(f"Monte Carlo failed: {msg}")
            messagebox.showwarning("Monte Carlo confidence", msg)
            return

        try:
            _merge_monte_carlo_into_result(self._last_result, mc)
            summary_path = _summary_json_path(self.run_dir, TUNING_OUTPUT_SUFFIX)
            summary_path.write_text(json.dumps(self._last_result, indent=2, default=str), encoding="utf-8")

            warnings: list[str] = []
            extracted = extract_row(summary_path, warnings)
            panel_text = _format_extracted_row(self._last_result, extracted, warnings)
        except Exception as exc:
            self._status_var.set("Monte Carlo succeeded but updating the panel failed -- see error dialog.")
            messagebox.showerror("Monte Carlo confidence", f"{type(exc).__name__}: {exc}")
            return

        self._status_var.set(
            f"Monte Carlo done: {mc.get('mc_n_success')}/{n_iterations} refits converged "
            f"({mode_label} residuals, {n_freq_bins} freq bin{'s' if n_freq_bins != 1 else ''})."
        )
        self._metrics_var.set(panel_text)

    def _show_preview(self, label: tk.Label, png_path: Path, *, max_w: int, max_h: int):
        from PIL import Image, ImageTk

        if not png_path.exists():
            return None
        image = Image.open(png_path)
        image.thumbnail((max_w, max_h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        label.configure(image=photo, text="")
        return photo


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactively tune CA-FFT padding/smoothing against an already-saved OLE-COM run folder."
    )
    parser.add_argument("run_dir", help="Path to a per-row result folder (must contain summary.json).")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    if not (run_dir / "summary.json").exists():
        print(f"Warning: {run_dir} has no summary.json -- expected file locations will be guessed.", file=sys.stderr)
    app = CAFFTTuningWindow(run_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
