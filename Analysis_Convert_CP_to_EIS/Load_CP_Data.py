# -*- coding: utf-8 -*-
"""
Utilities for loading BioLogic EC-Lab data from exported text files or raw
.mpr/.mpt files.
"""

import numpy as np
import pandas as pd
from tkinter import filedialog

try:
    import yadg
except ImportError:
    yadg = None


def _read_mpt_header_lines(file_path):
    """Read 'Nb header lines' from an EC-Lab .mpt file."""
    with open(file_path, "r", encoding="latin1") as f:
        for line in f:
            if line.startswith("Nb header lines"):
                return int(line.split(":")[1].strip())
    return 0


def _require_yadg():
    if yadg is None:
        raise ImportError(
            "yadg is required to read EC-Lab .mpr files. Install it with 'pip install yadg'."
        )


def _load_mpr_dataset(file_path):
    _require_yadg()
    data_tree = yadg.extractors.extract(filetype="eclab.mpr", path=str(file_path))
    return data_tree.to_dataset()


def _parse_text_dc_file(file_path):
    """
    Parse text DC data while tolerating either:
    - plain numeric exports without a header
    - EC-Lab exports with a header row such as: time/s, Ewe/V, <I>/mA

    Returns
    -------
    data : ndarray of shape (n, 3)
        Columns are [time, voltage, current] with the raw current unit unchanged.
    current_header : str | None
        Original current-column header if available.
    """
    try:
        data = np.loadtxt(file_path)
        return np.asarray(data, dtype=float), None
    except ValueError:
        pass

    try:
        data = np.loadtxt(file_path, delimiter=",")
        return np.asarray(data, dtype=float), None
    except ValueError:
        pass

    try:
        df = pd.read_csv(
            file_path,
            sep=None,
            engine="python",
            comment="#",
            skip_blank_lines=True,
        )
    except Exception:
        df = pd.read_csv(
            file_path,
            sep=r"\s+",
            engine="python",
            comment="#",
            skip_blank_lines=True,
        )
    if df.shape[1] <= 1 or any(str(col).startswith("Unnamed:") for col in df.columns):
        df = pd.read_csv(
            file_path,
            sep=r"\s+",
            engine="python",
            comment="#",
            skip_blank_lines=True,
        )
    df = df.dropna(axis=0, how="all")
    df.columns = [str(col).strip() for col in df.columns]

    def _find_column(candidates):
        lowered = {str(col).strip().lower(): col for col in df.columns}
        for candidate in candidates:
            for low_name, orig_name in lowered.items():
                if candidate in low_name:
                    return orig_name
        return None

    time_col = _find_column(["time/s", "time", "t/s"])
    voltage_col = _find_column(["ewe/v", "voltage", "e/v", "v/v", "potential/v"])
    current_col = _find_column(["<i>/ma", "<i>/a", "current", "i/a", "i/ma", "<i>"])

    if time_col is None or voltage_col is None or current_col is None:
        raise ValueError(f"Could not identify time/voltage/current columns in {file_path}")

    data = df[[time_col, voltage_col, current_col]].apply(pd.to_numeric, errors="coerce").dropna().to_numpy(dtype=float)
    return data, str(current_col)


def _convert_text_current_to_amp(data, current_in_mA=True, current_header=None):
    """
    Convert text current column to ampere.

    Parameters
    ----------
    current_in_mA : bool | None
        True  -> force mA to A conversion
        False -> force treat as already in A
        None  -> auto-detect from header when available, otherwise keep raw values
    """
    converted = np.asarray(data, dtype=float).copy()
    header = (current_header or "").lower()

    if current_in_mA is None:
        if "/ma" in header:
            converted[:, 2] /= 1000.0
            print("Current converted from text header: mA -> A")
        elif "/a" in header:
            print("Current kept as A based on text header")
        else:
            print("Current unit auto-detect: no header unit found, keeping raw values")
        return converted

    if current_in_mA:
        if "/a" in header and "/ma" not in header:
            print("Current kept as A based on text header override")
            return converted
        converted[:, 2] /= 1000.0
        print("Current converted: mA -> A")
    else:
        print("Current kept as A (no conversion)")

    return converted


def load_mpr_EIS_from_path(file_path):
    """
    Load EIS data from a BioLogic .mpr file using yadg.
    Returns: numpy array [freq, Re(Z), Im(Z)]
    """
    ds = _load_mpr_dataset(file_path)

    freq = ds["freq"].values.astype(float)
    z_re = ds["Re(Z)"].values.astype(float)
    z_im = -ds["-Im(Z)"].values.astype(float)

    valid = np.isfinite(freq) & np.isfinite(z_re) & np.isfinite(z_im)
    data = np.column_stack([freq[valid], z_re[valid], z_im[valid]])

    sort_idx = np.argsort(data[:, 0])
    data = data[sort_idx]

    print(f"Loaded {len(data)} EIS points from .mpr file")
    print(f"Frequency range: {data[:,0].min():.4f} ~ {data[:,0].max():.1f} Hz")
    return data


def _apply_time_trim(data, trim_start_time=None):
    """Trim rows earlier than trim_start_time, if provided."""
    if trim_start_time is None:
        return data

    trimmed = data[data[:, 0] >= float(trim_start_time)]
    print(
        f"Trimmed early-time region: kept {len(trimmed)} / {len(data)} points "
        f"from t >= {float(trim_start_time):.2f} s"
    )
    return trimmed


def _rolling_mean(values, window_points):
    """Simple centered rolling mean using edge padding."""
    if window_points <= 1 or len(values) == 0:
        return values.copy()

    pad_left = window_points // 2
    pad_right = window_points - 1 - pad_left
    padded = np.pad(values, (pad_left, pad_right), mode="edge")
    kernel = np.ones(window_points, dtype=float) / window_points
    return np.convolve(padded, kernel, mode="valid")


def detect_stable_current_start_time(
    data,
    voltage_step_sigma=8.0,
    smooth_window_points=11,
    stability_window_s=2.0,
    sustain_window_s=3.0,
    std_factor=2.5,
):
    """
    Detect a reasonable trim start time based on current stabilization before the
    main voltage step.

    Strategy:
    - Find the largest voltage-change event from dV/dt.
    - Examine the pre-step current region.
    - Compute rolling current standard deviation.
    - Use the late pre-step baseline as the target stable noise floor.
    - Return the earliest time where the current variation stays near that floor.

    Returns a float time in seconds, or None if no stable start is found.
    """
    if data is None or len(data) < 20:
        return None

    time = np.asarray(data[:, 0], dtype=float)
    voltage = np.asarray(data[:, 1], dtype=float)
    current = np.asarray(data[:, 2], dtype=float)

    positive_diffs = np.diff(time)
    positive_diffs = positive_diffs[np.isfinite(positive_diffs) & (positive_diffs > 0)]
    if len(positive_diffs) == 0:
        dt_med = 1.0
    else:
        dt_med = float(np.median(positive_diffs))

    time_for_grad = time.copy()
    for idx in range(1, len(time_for_grad)):
        if time_for_grad[idx] <= time_for_grad[idx - 1]:
            time_for_grad[idx] = time_for_grad[idx - 1] + dt_med

    smooth_points = max(3, int(smooth_window_points))
    if smooth_points % 2 == 0:
        smooth_points += 1

    voltage_smooth = _rolling_mean(voltage, smooth_points)
    abs_dvdt = np.abs(np.gradient(voltage_smooth, time_for_grad))

    dvdt_baseline = np.median(abs_dvdt)
    dvdt_spread = np.median(np.abs(abs_dvdt - dvdt_baseline))
    dvdt_threshold = dvdt_baseline + voltage_step_sigma * max(dvdt_spread, 1e-15)
    step_candidates = np.where(abs_dvdt >= dvdt_threshold)[0]
    if len(step_candidates) == 0:
        step_idx = int(np.argmax(abs_dvdt))
    else:
        step_idx = int(step_candidates[0])

    if step_idx < 10:
        return None

    pre_current = current[:step_idx]
    pre_time = time[:step_idx]
    if len(pre_current) < 10:
        return None

    stability_points = max(3, int(round(stability_window_s / dt_med)))
    sustain_points = max(3, int(round(sustain_window_s / dt_med)))

    rolling_std = np.array(
        [
            np.std(pre_current[max(0, i - stability_points + 1): i + 1])
            for i in range(len(pre_current))
        ],
        dtype=float,
    )

    tail_start = max(0, len(rolling_std) - max(10, 2 * sustain_points))
    stable_floor = np.median(rolling_std[tail_start:])
    if not np.isfinite(stable_floor):
        return None

    threshold = max(stable_floor * std_factor, stable_floor + 1e-18)
    stable_mask = rolling_std <= threshold

    run_length = 0
    for idx, is_stable in enumerate(stable_mask):
        run_length = run_length + 1 if is_stable else 0
        if run_length >= sustain_points:
            start_idx = idx - sustain_points + 1
            trim_start_time = float(pre_time[start_idx])
            print(
                "Auto-trim detected stable current region: "
                f"t >= {trim_start_time:.2f} s before the main voltage step at "
                f"t ~= {time[step_idx]:.2f} s"
            )
            return trim_start_time

    return None


def load_mpr_DC_from_path(
    file_path,
    CA_step_only=True,
    trim_start_time=None,
    current_scale_factor=1.0,
):
    """
    Load CA/CP data from a BioLogic .mpr file using yadg.
    Returns: numpy array [time(s), V(V), I(A)]
    """
    ds = _load_mpr_dataset(file_path)

    time = ds["time"].values.astype(float)
    voltage = ds["Ewe"].values.astype(float)
    current = ds["<I>"].values.astype(float) / 1000.0  # yadg returns mA; convert to A

    if CA_step_only and "Ns" in ds:
        ns = ds["Ns"].values
        mask = ns == 1
        if np.any(mask):
            time = time[mask]
            voltage = voltage[mask]
            current = current[mask]
            print("OCV region removed: using Ns=1 (CA step) only")

    current = current * float(current_scale_factor)

    valid = np.isfinite(time) & np.isfinite(voltage) & np.isfinite(current)
    data = np.column_stack([time[valid], voltage[valid], current[valid]])

    data = _apply_time_trim(data, trim_start_time=trim_start_time)

    print(f"Loaded {len(data)} data points from .mpr file")
    print(f"Time range: {data[:,0].min():.2f} ~ {data[:,0].max():.2f} s")
    return data


def load_mpt_EIS_from_path(file_path):
    """
    Load EIS data from a BioLogic .mpt file.
    Returns: freq (Hz), Z_real (Ohm), minus_ImZ (Ohm), file_path
    """
    n_header = _read_mpt_header_lines(file_path)
    df = pd.read_csv(
        file_path,
        sep="\t",
        skiprows=n_header - 1,
        encoding="latin1",
        on_bad_lines="skip",
    )

    freq = df["freq/Hz"].values.astype(float)
    z_real = df["Re(Z)/Ohm"].values.astype(float)
    minus_z_imag = df["-Im(Z)/Ohm"].values.astype(float)

    sort_idx = np.argsort(freq)
    freq = freq[sort_idx]
    z_real = z_real[sort_idx]
    minus_z_imag = minus_z_imag[sort_idx]

    print(f"Loaded {len(freq)} EIS points from .mpt file")
    print(f"Frequency range: {freq.min():.4f} ~ {freq.max():.1f} Hz")

    return freq, z_real, minus_z_imag, file_path


def load_mpt_DC_from_path(
    file_path,
    CA_step_only=True,
    trim_start_time=None,
    current_scale_factor=1.0,
):
    """
    Load CA/CP data from a BioLogic .mpt file.
    Returns: data array [time(s), V(V), I(A)], file_path
    """
    n_header = _read_mpt_header_lines(file_path)
    df = pd.read_csv(
        file_path,
        sep="\t",
        skiprows=n_header - 1,
        encoding="latin1",
        on_bad_lines="skip",
    )

    if CA_step_only and "Ns" in df.columns:
        df = df[df["Ns"] == 1].reset_index(drop=True)
        print("OCV region removed: using Ns=1 (CA step) only")

    time = df["time/s"].values.astype(float)
    voltage = df["Ewe/V"].values.astype(float)
    current = df["<I>/mA"].values.astype(float) / 1000.0
    current = current * float(current_scale_factor)

    data = np.column_stack([time, voltage, current])

    data = _apply_time_trim(data, trim_start_time=trim_start_time)

    print(f"Loaded {len(data)} data points from .mpt file")
    print(f"Time range: {data[:,0].min():.2f} ~ {data[:,0].max():.2f} s")
    print("Current unit converted: mA -> A")

    return data, file_path


def load_mpt_EIS(prompt):
    file_path = filedialog.askopenfilename(
        title=prompt,
        filetypes=[("BioLogic MPT files", "*.mpt"), ("All files", "*.*")],
    )
    if not file_path:
        return
    return load_mpt_EIS_from_path(file_path)


def load_mpt_DC(prompt, CA_step_only=True, trim_start_time=None, current_scale_factor=1.0):
    file_path = filedialog.askopenfilename(
        title=prompt,
        filetypes=[("BioLogic MPT files", "*.mpt"), ("All files", "*.*")],
    )
    if not file_path:
        return
    return load_mpt_DC_from_path(
        file_path,
        CA_step_only=CA_step_only,
        trim_start_time=trim_start_time,
        current_scale_factor=current_scale_factor,
    )


def parse_eis_file(file_path):
    """
    Parse a plain-text EIS file:
    - supports headered text exports such as freq/Hz, Re(Z)/Ohm, -Im(Z)/Ohm
    - auto-skips non-numeric lines
    - if multiple cycles exist, returns the last cycle only
    Returns: numpy array [freq, Re(Z), Im(Z)]
    """
    rows = []
    try:
        df = pd.read_csv(
            file_path,
            sep=None,
            engine="python",
            comment="#",
            skip_blank_lines=True,
        )
        if df.shape[1] <= 1 or any(str(col).startswith("Unnamed:") for col in df.columns):
            df = pd.read_csv(
                file_path,
                sep=r"\s+",
                engine="python",
                comment="#",
                skip_blank_lines=True,
            )
        df = df.dropna(axis=0, how="all")
        df.columns = [str(col).strip() for col in df.columns]

        lowered = {str(col).strip().lower(): col for col in df.columns}

        def _find_column(candidates):
            for candidate in candidates:
                for low_name, orig_name in lowered.items():
                    if candidate in low_name:
                        return orig_name
            return None

        freq_col = _find_column(["freq/hz", "frequency", "freq"])
        re_col = _find_column(["re(z)/ohm", "re(z)", "zre"])
        minus_im_col = _find_column(["-im(z)/ohm", "-im(z)", "-im"])
        im_col = _find_column(["im(z)/ohm", "im(z)"])

        if freq_col is not None and re_col is not None and (minus_im_col is not None or im_col is not None):
            third_col = minus_im_col if minus_im_col is not None else im_col
            data = (
                df[[freq_col, re_col, third_col]]
                .apply(pd.to_numeric, errors="coerce")
                .dropna()
                .to_numpy(dtype=float)
            )
            if minus_im_col is not None:
                data[:, 2] *= -1.0
            rows = data.tolist()
    except Exception:
        rows = []

    if not rows:
        with open(file_path, "r", encoding="latin1") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3:
                    try:
                        rows.append([float(p) for p in parts[:3]])
                    except ValueError:
                        pass

    data = np.array(rows)

    if len(data) == 0:
        print("No numeric data found in EIS file!")
        return None

    freqs = data[:, 0]
    max_f = freqs.max()
    cycle_starts = [0]
    for i in range(1, len(freqs)):
        if freqs[i] > max_f * 0.5 and freqs[i - 1] < max_f * 0.01:
            cycle_starts.append(i)

    n_cycles = len(cycle_starts)
    if n_cycles > 1:
        data = data[cycle_starts[-1]:]
        print(f"Detected {n_cycles} EIS cycles -> using last cycle only ({len(data)} points)")
    else:
        print(f"Single EIS cycle detected ({len(data)} points)")

    print(f"Frequency range: {data[:,0].min():.4f} ~ {data[:,0].max():.1f} Hz")
    return data


def load_eis_from_path(file_path):
    """
    Load EIS data from supported file types.
    Supports: .mpr, .mpt, .txt, .dat, .csv
    Returns: numpy array [freq, Re(Z), Im(Z)]
    """
    ext = str(file_path).lower().rsplit(".", 1)[-1]

    if ext == "mpr":
        return load_mpr_EIS_from_path(file_path)

    if ext == "mpt":
        freq, z_re, minus_z_imag, _ = load_mpt_EIS_from_path(file_path)
        return np.column_stack([freq, z_re, -minus_z_imag])

    return parse_eis_file(file_path)


def load_txt_EIS(prompt):
    file_path = filedialog.askopenfilename(
        title=prompt,
        filetypes=[("Text files", "*.txt *.dat *.csv"), ("All files", "*.*")],
    )
    if not file_path:
        return None, ""

    data = parse_eis_file(file_path)
    return data, file_path


def load_dc_from_path(
    file_path,
    current_in_mA=True,
    CA_step_only=True,
    trim_start_time=None,
    current_scale_factor=1.0,
    auto_trim=False,
    auto_trim_kwargs=None,
):
    """
    Load DC data from supported file types.
    Supports: .mpr, .mpt, .txt, .dat, .csv
    Returns: numpy array [time(s), V(V), I(A)]
    """
    ext = str(file_path).lower().rsplit(".", 1)[-1]

    if ext == "mpr":
        data = load_mpr_DC_from_path(
            file_path,
            CA_step_only=CA_step_only,
            trim_start_time=None,
            current_scale_factor=current_scale_factor,
        )
        if trim_start_time is not None:
            data = _apply_time_trim(data, trim_start_time=trim_start_time)
        elif auto_trim:
            detected_time = detect_stable_current_start_time(
                data, **(auto_trim_kwargs or {})
            )
            data = _apply_time_trim(data, trim_start_time=detected_time)
        return data

    if ext == "mpt":
        data, _ = load_mpt_DC_from_path(
            file_path,
            CA_step_only=CA_step_only,
            trim_start_time=None,
            current_scale_factor=current_scale_factor,
        )
        if trim_start_time is not None:
            data = _apply_time_trim(data, trim_start_time=trim_start_time)
        elif auto_trim:
            detected_time = detect_stable_current_start_time(
                data, **(auto_trim_kwargs or {})
            )
            data = _apply_time_trim(data, trim_start_time=detected_time)
        return data

    data, current_header = _parse_text_dc_file(file_path)
    data = _convert_text_current_to_amp(
        data,
        current_in_mA=current_in_mA,
        current_header=current_header,
    )

    data[:, 2] *= float(current_scale_factor)

    if trim_start_time is not None:
        data = _apply_time_trim(data, trim_start_time=trim_start_time)
    elif auto_trim:
        detected_time = detect_stable_current_start_time(
            data, **(auto_trim_kwargs or {})
        )
        data = _apply_time_trim(data, trim_start_time=detected_time)

    print(f"Loaded {len(data)} data points from text file")
    return data


def load_txt_data(prompt):
    file_path = filedialog.askopenfilename(
        title=prompt,
        filetypes=[("Text files", "*.txt *.dat *.csv"), ("All files", "*.*")],
    )

    if not file_path:
        return

    try:
        data = np.loadtxt(file_path)
    except ValueError:
        data = np.loadtxt(file_path, delimiter=",")

    print(f"There are {data.shape[0]} rows and {data.shape[1]} columns in this file.")
    return data, file_path
