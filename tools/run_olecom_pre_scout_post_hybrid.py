from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import comtypes
import comtypes.client
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from galvani import BioLogic


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(PROJECT_DIR / "Analysis_Convert_CP_to_EIS") not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR / "Analysis_Convert_CP_to_EIS"))

from cp_first_policy import recommend_peis_lf_from_cp_txt  # noqa: E402
from Analysis_Convert_CP_to_EIS import EIS_Fitting as EF  # noqa: E402


@dataclass
class OleRunResult:
    mpr_path: Path
    output_base: Path
    runtime_s: float


MEASURE_STATUS_KEYS = [
    "Status",
    "Ox/Red",
    "OCV",
    "EIS",
    "Technique number",
    "Technique code",
    "Sequence number",
    "Current loop iteration number",
    "Current sequence within loop number",
    "Loop experiment iteration number",
    "Cycle number",
    "Counter 1",
    "Counter 2",
    "Counter 3",
    "Buffer size",
    "Time",
    "Ewe",
    "Ece",
    "Eoc",
    "I",
    "Q-Q0",
    "Aux1",
    "Aux2",
    "Irange",
    "R compensation",
    "Frequency",
    "|Z|",
    "Current point index",
    "Total point index",
    "T (deg. C)",
    "Safety limit",
    "Connection",
    "Result code",
]


def _status_tuple_to_dict(status) -> dict:
    values = list(status or [])
    out = {
        key: (values[idx] if idx < len(values) else None)
        for idx, key in enumerate(MEASURE_STATUS_KEYS)
    }
    out["_raw"] = values
    return out


def _ensure_com_initialized():
    """Make EC-Lab OLE-COM safe from GUI/worker threads."""
    try:
        comtypes.CoInitialize()
    except Exception:
        pass


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _cleanup_eclab_processes(*, include_visible: bool = False) -> list[int]:
    """Terminate stale EC-Lab processes through WMI so COM retries start cleanly."""
    ps = (
        "$procs = Get-Process EClab -ErrorAction SilentlyContinue | "
        + ("Where-Object { $true }" if include_visible else "Where-Object { $_.MainWindowHandle -eq 0 }")
        + "; "
        "$ids = @($procs | ForEach-Object { $_.Id }); "
        "foreach ($p in $procs) { "
        "  try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {} "
        "}; "
        "Start-Sleep -Milliseconds 500; "
        "$remaining = @(); "
        "foreach ($id in $ids) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $remaining += $id } }; "
        "($ids -join ',') + '|' + ($remaining -join ',')"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=20,
    )
    killed: list[int] = []
    stdout = result.stdout.strip()
    killed_text, _, remaining_text = stdout.partition("|")
    for part in killed_text.split(","):
        part = part.strip()
        if part.isdigit():
            killed.append(int(part))
    remaining = [int(p) for p in remaining_text.split(",") if p.strip().isdigit()]
    if remaining:
        # Last-resort wmic path worked more reliably on this EC-Lab install.
        for pid in remaining:
            subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "call", "terminate"],
                capture_output=True,
                text=True,
                timeout=10,
            )
    if killed:
        time.sleep(2.0)
    return killed


def _fmt_duration(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    total -= hours * 3600
    minutes = int(total // 60)
    sec = total - minutes * 60
    return f"{hours}:{minutes:02d}:{sec:.4f}"


def _mps_row(label: str, values: Iterable[object]) -> str:
    return f"{label:<20}" + "".join(f"{str(value):<20}" for value in values) + "\n"


def _require_mps_fields(path: Path, checks: Iterable[tuple[str, str]]) -> None:
    """Fail before OLE-COM if the generated settings file lost a critical field."""
    data = Path(path).read_bytes()
    missing: list[str] = []
    for label, expected in checks:
        token = expected.encode("latin1")
        if token not in data:
            missing.append(f"{label}={expected!r}")
    if missing:
        raise RuntimeError(
            f"Generated MPS sanity check failed for {path}: missing "
            + ", ".join(missing)
        )


def _write_ca_mps(
    path: Path,
    *,
    bias_v: float,
    dv_v: float,
    pre_s: float,
    scout_s: float,
    post_s: float,
    dt_s: float,
    bandwidth: int,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    v0 = f"{float(bias_v):.3f}"
    v1 = f"{float(bias_v + dv_v):.3f}"
    steps = [0, 1, 2]
    text = [
        "EC-LAB SETTING FILE\n\n",
        "Number of linked techniques : 1\n\n",
        "EC-LAB for windows v11.61 (software)\n",
        "Internet server v11.61 (firmware)\n",
        "Command interpretor v11.61 (firmware)\n\n",
        f"Filename : {path}\n\n",
        "Device : SP-200\n",
        "Electrode connection : standard\n",
        "Potential control : Ewe\n",
        "Ewe ctrl range : min = -2.50 V, max = 2.50 V\n",
        "Ewe,I filtering : 50 kHz\n",
        "Safety Limits :\n",
        "\tDo not start on E overload\n",
        "Channel : Grounded\n",
        "Electrode material : \n",
        "Initial state : \n",
        "Electrolyte : \n",
        "Comments : \n",
        "Cable : standard\n",
        "Electrode surface area : 0.001 cm²\n",
        "Characteristic mass : 0.001 g\n",
        "Equivalent Weight : 0.000 g/eq.\n",
        "Density : 0.000 g/cm3\n",
        "Volume (V) : 0.001 cm³\n",
        "Cycle Definition : Charge/Discharge alternance\n",
        "Do not turn to OCV between techniques\n\n",
        "Technique : 1\n",
        "Chronoamperometry / Chronocoulometry\n",
        _mps_row("Ns", steps),
        _mps_row("Ei (V)", [v0, v1, v0]),
        _mps_row("vs.", ["Ref", "Ref", "Ref"]),
        _mps_row("ti (h:m:s)", [_fmt_duration(pre_s), _fmt_duration(scout_s), _fmt_duration(post_s)]),
        _mps_row("Imax", ["pass", "pass", "pass"]),
        _mps_row("unit Imax", ["mA", "mA", "mA"]),
        _mps_row("Imin", ["pass", "pass", "pass"]),
        _mps_row("unit Imin", ["mA", "mA", "mA"]),
        _mps_row("dQM", ["0.000", "0.000", "0.000"]),
        _mps_row("unit dQM", ["mA.h", "mA.h", "mA.h"]),
        _mps_row("record", ["<I>", "<I>", "<I>"]),
        _mps_row("dI", ["5.000", "5.000", "5.000"]),
        _mps_row("unit dI", ["µA", "µA", "µA"]),
        _mps_row("dQ", ["0.000", "0.000", "0.000"]),
        _mps_row("unit dQ", ["mA.h", "mA.h", "mA.h"]),
        _mps_row("dt (s)", [f"{dt_s:.4f}", f"{dt_s:.4f}", f"{dt_s:.4f}"]),
        _mps_row("dta (s)", [f"{dt_s:.4f}", f"{dt_s:.4f}", f"{dt_s:.4f}"]),
        _mps_row("E range min (V)", ["-2.500", "-2.500", "-2.500"]),
        _mps_row("E range max (V)", ["2.500", "2.500", "2.500"]),
        _mps_row("I Range", ["Auto", "Auto", "Auto"]),
        _mps_row("I Range min", ["Unset", "Unset", "Unset"]),
        _mps_row("I Range max", ["Unset", "Unset", "Unset"]),
        _mps_row("I Range init", ["Unset", "Unset", "Unset"]),
        _mps_row("Bandwidth", [bandwidth, bandwidth, bandwidth]),
        _mps_row("goto Ns'", [0, 0, 0]),
        _mps_row("nc cycles", [0, 0, 0]),
    ]
    path.write_text("".join(text), encoding="latin1")
    return path


def _write_peis_mps(
    path: Path,
    *,
    bias_v: float,
    f_high_hz: float,
    f_low_hz: float,
    points_per_decade: int,
    amplitude_mv: float,
    bandwidth: int,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = [
        "EC-LAB SETTING FILE\n\n",
        "Number of linked techniques : 1\n\n",
        "EC-LAB for windows v11.61 (software)\n",
        "Internet server v11.61 (firmware)\n",
        "Command interpretor v11.61 (firmware)\n\n",
        f"Filename : {path}\n\n",
        "Device : SP-200\n",
        "Electrode connection : standard\n",
        "Potential control : Ewe\n",
        "Ewe ctrl range : min = -10.00 V, max = 10.00 V\n",
        "Ewe,I filtering : 50 kHz\n",
        "Safety Limits :\n",
        "\tDo not start on E overload\n",
        "Channel : Grounded\n",
        "Electrode material : \n",
        "Initial state : \n",
        "Electrolyte : \n",
        "Comments : \n",
        "Cable : standard\n",
        "Electrode surface area : 0.001 cm²\n",
        "Characteristic mass : 0.001 g\n",
        "Equivalent Weight : 0.000 g/eq.\n",
        "Density : 0.000 g/cm3\n",
        "Volume (V) : 0.001 cm³\n",
        "Record EIS quality indicators\n",
        "Cycle Definition : Charge/Discharge alternance\n",
        "Do not turn to OCV between techniques\n\n",
        "Technique : 1\n",
        "Potentio Electrochemical Impedance Spectroscopy\n",
        "Mode                Single sine\n",
        f"E (V)               {float(bias_v):.4f}\n",
        "vs.                 Ref\n",
        "tE (h:m:s)          0:00:0.0000\n",
        "record              0\n",
        "dI                  0.000\n",
        "unit dI             mA\n",
        "dt (s)              0.000\n",
        f"fi                  {float(f_high_hz):.3f}\n",
        "unit fi             Hz\n",
        f"ff                  {float(f_low_hz):.5f}\n",
        "unit ff             Hz\n",
        f"Nd                  {int(points_per_decade)}\n",
        "Points              per decade\n",
        "spacing             Logarithmic\n",
        f"Va (mV)             {float(amplitude_mv):.1f}\n",
        "pw                  0.10\n",
        "Na                  1\n",
        "corr                0\n",
        "E range min (V)     -10.000\n",
        "E range max (V)     10.000\n",
        "I Range             Auto\n",
        f"Bandwidth           {int(bandwidth)}\n",
        "nc cycles           0\n",
        "goto Ns'            0\n",
        "nr cycles           0\n",
        "inc. cycle          0\n",
    ]
    path.write_text("".join(text), encoding="latin1")
    return path


class OleComController:
    def __init__(
        self,
        *,
        ip: str,
        channel: int,
        device: int = 0,
        create_if_missing: bool = False,
        trust_test_connection: bool = False,
    ):
        self.ip = ip
        self.device = int(device)
        self.user_channel = int(channel)
        self.com_channel = int(channel) - 1
        self.create_if_missing = bool(create_if_missing)
        self.trust_test_connection = bool(trust_test_connection)
        self.obj = None

    def connect(self):
        _ensure_com_initialized()
        # Prefer attaching to an already-open EC-Lab session. If we create a
        # fresh COM-owned instance, EC-Lab can close when this Python process
        # exits even when we intentionally skip DisconnectDevice.
        attached_active = False
        try:
            self.obj = comtypes.client.GetActiveObject("EClabCOM.EClabExe")
            attached_active = True
        except Exception:
            if not self.create_if_missing:
                raise RuntimeError(
                    "No active EC-Lab OLE-COM session was found. Open EC-Lab first, "
                    "or allow create_if_missing for a standalone diagnostic."
                )
            self.obj = comtypes.client.CreateObject("EClabCOM.EClabExe")
        if self.obj is None:
            raise RuntimeError(
                "EC-Lab OLE-COM returned an empty COM object. Re-open EC-Lab, "
                "then reconnect BioLogic from the GUI."
            )
        ip_code = None
        try:
            ip_code = self.obj.ConnectDeviceByIP(self.ip)
        except Exception:
            pass
        # On this EC-Lab/OLE-COM install ConnectDeviceByIP can return 0 while
        # TestConnection already looks true.  A follow-up ConnectDevice call is
        # still needed before LoadSettings/RunChannel reliably accept commands.
        device_code = None
        if int(ip_code or 0) != 1 or not self._connected():
            device_code = self.obj.ConnectDevice(self.device)
        connected_by_command = int(ip_code or 0) == 1 or int(device_code or 0) == 1
        if not attached_active and not connected_by_command and not (self.trust_test_connection and self._connected()):
            raise RuntimeError(
                "OLE-COM could not take device ownership: "
                f"ConnectDeviceByIP={ip_code}, ConnectDevice={device_code}, "
                f"TestConnection={self._connected()}. This usually means EC-Lab "
                "or another COM server already owns the channel. Close/disconnect "
                "the existing EC-Lab session before starting automation, or use "
                "trust_test_connection only in an explicit recovery diagnostic."
            )
        if not self._connected():
            raise RuntimeError(f"OLE-COM could not connect to device {self.device} / {self.ip}")
        select_device_code = self.obj.SelectDevice(self.device)
        if select_device_code not in (0, 1):
            raise RuntimeError(f"SelectDevice returned {select_device_code}")
        select_code = self.obj.SelectChannel(self.device, self.com_channel)
        if select_code not in (0, 1):
            raise RuntimeError(f"SelectChannel returned {select_code}")
        return self

    def _connected(self) -> bool:
        _ensure_com_initialized()
        if self.obj is None:
            return False
        try:
            return int(self.obj.TestConnection(self.device)) == 1
        except Exception:
            return False

    def disconnect(self):
        _ensure_com_initialized()
        if self.obj is not None:
            try:
                self.obj.DisconnectDevice(self.device)
            except Exception:
                pass

    def status(self):
        _ensure_com_initialized()
        if self.obj is None:
            raise RuntimeError("OLE-COM object is not attached; reconnect BioLogic first")
        return self.obj.MeasureStatus(self.device, self.com_channel)

    def status_dict(self) -> dict:
        return _status_tuple_to_dict(self.status())

    def get_ocv(self, prefer: str = "Ewe") -> float:
        status = self.status_dict()
        keys = []
        for key in [str(prefer), "OCV", "Ewe", "Eoc", "Ece"]:
            if key and key not in keys:
                keys.append(key)
        for key in keys:
            try:
                value = status.get(key)
                if value is not None:
                    out = float(value)
                    if np.isfinite(out):
                        return out
            except Exception:
                pass
        raise RuntimeError(f"Could not read OLE-COM OCV/Ewe from MeasureStatus: {status}")

    def is_running(self) -> bool:
        try:
            status = self.status()
            return int(float(status[0])) == 1
        except Exception:
            return False

    def wait_until_stopped(self, *, timeout_s: float = 10.0) -> bool:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < float(timeout_s):
            if not self.is_running():
                return True
            time.sleep(0.25)
        return not self.is_running()

    def wait_until_started(
        self,
        mpr_path: Path,
        *,
        startup_grace_s: float = 15.0,
        label: str = "measurement",
    ) -> None:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < float(startup_grace_s):
            if self.is_running() or _safe_point_count(self, mpr_path) > 0:
                return
            time.sleep(0.25)
        raise RuntimeError(
            f"{label} did not enter RUN state or produce data within "
            f"{startup_grace_s:g} s"
        )

    def load_and_run(self, mps_path: Path, output_base: Path, *, stop_existing: bool = True) -> OleRunResult:
        _ensure_com_initialized()
        if self.obj is None or not self._connected():
            # EC-Lab/OLE-COM handles can go stale after manual EC-Lab device
            # removal/re-addition. Re-attach once instead of failing later with
            # a cryptic "'NoneType' object has no attribute 'LoadSettings'".
            self.connect()
        if self.obj is None:
            raise RuntimeError("OLE-COM object is not attached; cannot LoadSettings")
        mps_path = Path(mps_path)
        output_base = Path(output_base)
        if self.is_running():
            if not stop_existing:
                raise RuntimeError(
                    "Channel is already RUN. Refusing to load a new OLE-COM technique "
                    "because stop_existing=False."
                )
            self.stop()
            if not self.wait_until_stopped(timeout_s=15.0):
                raise RuntimeError("Channel stayed RUN after StopChannel; refusing to load a new OLE-COM technique")

        load_code = None
        last_status = None
        for attempt in range(1, 4):
            load_code = self.obj.LoadSettings(self.device, self.com_channel, str(mps_path))
            if int(load_code) == 1:
                break
            try:
                last_status = self.status()
            except Exception as exc:
                last_status = f"MeasureStatus unavailable: {exc}"
            try:
                if self.is_running():
                    self.stop()
                    self.wait_until_stopped(timeout_s=15.0)
            except Exception:
                pass
            try:
                self.obj.ConnectDevice(self.device)
                self.obj.SelectDevice(self.device)
                self.obj.SelectChannel(self.device, self.com_channel)
            except Exception:
                pass
            time.sleep(1.5 * attempt)
        if int(load_code or 0) != 1:
            raise RuntimeError(
                f"LoadSettings failed ({load_code}) for {mps_path}; "
                f"last MeasureStatus={last_status}. EC-Lab may still be flushing "
                "a previous buffer or holding a stale COM channel."
            )
        # EC-Lab can briefly keep the channel in an in-between state after loading.
        time.sleep(0.5)
        t0 = time.perf_counter()
        run_code = self.obj.RunChannel(self.device, self.com_channel, str(output_base))
        if int(run_code) != 1:
            try:
                status = self.status()
            except Exception as exc:
                status = f"MeasureStatus unavailable: {exc}"
            raise RuntimeError(
                f"RunChannel failed ({run_code}) for {mps_path}; "
                f"TestConnection={self._connected()}, MeasureStatus={status}. "
                "If LoadSettings succeeded but RunChannel is 0, the common causes are "
                "a duplicate/stale EC-Lab OLE server, a visible EC-Lab session owning "
                "the channel, or a settings file that EC-Lab parsed into an invalid "
                "technique state."
            )
        mpr = output_base.with_name(f"{output_base.name}_C{self.user_channel:02d}.mpr")
        return OleRunResult(mpr_path=mpr, output_base=output_base, runtime_s=time.perf_counter() - t0)

    def stop(self):
        _ensure_com_initialized()
        try:
            return self.obj.StopChannel(self.device, self.com_channel)
        except Exception:
            return None

    def point_count(self, mpr_path: Path) -> int:
        _ensure_com_initialized()
        return int(self.obj.MeasureNumberOfPoints(str(mpr_path)))

    def dc_value(self, mpr_path: Path, idx: int):
        _ensure_com_initialized()
        time_s, ewe_v, current_mA = self.obj.MeasureDcValue(str(mpr_path), int(idx))
        ns, code = self.obj.MeasureValueByID(str(mpr_path), "Ns", int(idx))
        irange, _ = self.obj.MeasureValueByID(str(mpr_path), "I Range", int(idx))
        return {
            "index": int(idx),
            "time_s": float(time_s),
            "ewe_v": float(ewe_v),
            "current_A": float(current_mA) * 1e-3,
            "current_raw_mA": float(current_mA),
            "Ns": int(round(float(ns))) if int(code) == 1 else None,
            "I_Range": int(round(float(irange))),
        }


def _safe_point_count(ctrl: OleComController, mpr_path: Path) -> int:
    try:
        if not Path(mpr_path).exists():
            return 0
        return int(ctrl.point_count(mpr_path))
    except Exception:
        return 0


def _wait_for_run_completion(
    ctrl: OleComController,
    mpr_path: Path,
    *,
    timeout_s: float,
    startup_grace_s: float = 15.0,
    label: str = "measurement",
) -> int:
    """
    Wait until a COM-launched experiment really finishes.

    EC-Lab/OLE-COM can report STOP for a short moment immediately after
    RunChannel before the channel flips to RUN.  Do not treat that startup
    transient as completion, otherwise PEIS can be disconnected before the
    first point is acquired.
    """
    t0 = time.perf_counter()
    seen_running = False
    seen_points = False
    last_points = 0

    while True:
        elapsed = time.perf_counter() - t0
        if elapsed > float(timeout_s):
            ctrl.stop()
            raise TimeoutError(f"{label} exceeded timeout ({timeout_s:g} s)")

        running = ctrl.is_running()
        points = _safe_point_count(ctrl, mpr_path)
        last_points = max(last_points, points)
        seen_running = seen_running or running
        seen_points = seen_points or points > 0

        if not running:
            startup_over = elapsed >= float(startup_grace_s)
            if (seen_running or seen_points) and startup_over:
                # Give EC-Lab one extra beat to flush final rows to disk.
                time.sleep(0.5)
                last_points = max(last_points, _safe_point_count(ctrl, mpr_path))
                return last_points
            if startup_over and not seen_running and not seen_points:
                raise RuntimeError(
                    f"{label} never produced a RUN state or data points within "
                    f"{startup_grace_s:g} s"
                )

        time.sleep(0.5)


def _wait_for_point_count_stable(
    ctrl: OleComController,
    mpr_path: Path,
    *,
    stable_s: float = 5.0,
    timeout_s: float = 120.0,
    poll_s: float = 0.5,
    label: str = "file",
) -> int:
    """
    Wait for EC-Lab to finish flushing buffered rows to the .mpr file.

    OLE-COM can report STOP before EC-Lab has emptied the channel buffer to
    disk.  Loading the next technique in that window can trigger EC-Lab's
    "buffer is still emptying" warning and LoadSettings=0.
    """
    t0 = time.perf_counter()
    last_count = -1
    stable_since = time.perf_counter()
    while True:
        elapsed = time.perf_counter() - t0
        if elapsed > float(timeout_s):
            raise TimeoutError(f"{label} point count did not stabilize within {timeout_s:g} s")
        count = _safe_point_count(ctrl, mpr_path)
        if count != last_count:
            last_count = count
            stable_since = time.perf_counter()
        elif count > 0 and time.perf_counter() - stable_since >= float(stable_s):
            return int(count)
        time.sleep(float(poll_s))


def _parse_mpr_dc(path: Path) -> np.ndarray:
    mpr = BioLogic.MPRfile(str(path))
    arr = mpr.data
    names = arr.dtype.names or ()
    t = np.asarray(arr["time/s"], dtype=float)
    v = np.asarray(arr["Ewe/V"], dtype=float)
    i = np.asarray(arr["<I>/mA"], dtype=float) * 1e-3
    ns = np.asarray(arr["Ns"], dtype=float) if "Ns" in names else np.zeros_like(t)
    return np.column_stack([t, v, i, ns])


def _parse_mpr_eis(path: Path) -> np.ndarray:
    mpr = BioLogic.MPRfile(str(path))
    arr = mpr.data
    names = arr.dtype.names or ()

    def pick(candidates):
        for cand in candidates:
            if cand in names:
                return cand
        lowered = {name.lower(): name for name in names}
        for cand in candidates:
            key = cand.lower()
            for low, orig in lowered.items():
                if key in low:
                    return orig
        raise KeyError(f"Could not find any of {candidates}; columns={names}")

    fcol = pick(["freq/Hz", "Frequency/Hz", "freq"])
    rcol = pick(["Re(Z)/Ohm", "Re(Z)", "Zre"])
    im_col = pick(["-Im(Z)/Ohm", "Im(Z)/Ohm", "-Im(Z)", "Im(Z)"])
    freq = np.asarray(arr[fcol], dtype=float)
    re_z = np.asarray(arr[rcol], dtype=float)
    im_raw = np.asarray(arr[im_col], dtype=float)
    neg_im = im_raw if im_col.lower().startswith("-im") else -im_raw
    valid = np.isfinite(freq) & np.isfinite(re_z) & np.isfinite(neg_im) & (freq > 0)
    out = np.column_stack([freq[valid], re_z[valid], neg_im[valid]])
    order = np.argsort(out[:, 0])
    return out[order]


def _save_ca_txt(path: Path, ca: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(path, ca[:, :3], header="time/s V/V I/A", comments="")
    return path


def _select_pre_tail_plus_scout(ca: np.ndarray, *, pre_tail_s: float) -> tuple[np.ndarray, str]:
    """Use EC-Lab sequence numbers to avoid mistaking pre-stabilization drift for dV.

    OLE-COM CA is one linked technique with Ns=0 pre, Ns=1 dV scout, Ns=2 post.
    FFT needs only a stable baseline immediately before dV plus the scout
    response. Feeding the full pre region can make tiny pre drift look like an
    extra excitation event.
    """
    arr = np.asarray(ca, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return arr.reshape(0, 3), "empty"
    if arr.shape[1] < 4:
        out = arr[:, :3].copy()
        if len(out):
            out[:, 0] -= out[0, 0]
        return out, "no_ns_available_raw_pre_plus_scout"

    ns = arr[:, 3]
    pre_mask = np.isclose(ns, 0)
    scout_mask = np.isclose(ns, 1)
    if not np.any(pre_mask) or not np.any(scout_mask):
        mask = pre_mask | scout_mask
        out = arr[mask, :3].copy() if np.any(mask) else arr[:, :3].copy()
        if len(out):
            out[:, 0] -= out[0, 0]
        return out, "ns_missing_pre_or_scout_raw_fallback"

    scout = arr[scout_mask]
    scout_start = float(np.nanmin(scout[:, 0]))
    pre_tail_start = scout_start - max(0.0, float(pre_tail_s))
    pre_tail_mask = pre_mask & (arr[:, 0] >= pre_tail_start) & (arr[:, 0] < scout_start)
    if not np.any(pre_tail_mask):
        pre_idx = np.where(pre_mask & (arr[:, 0] < scout_start))[0]
        if len(pre_idx):
            keep_n = min(len(pre_idx), max(5, int(round(max(1.0, float(pre_tail_s)) * 10))))
            pre_tail_mask[pre_idx[-keep_n:]] = True

    mask = pre_tail_mask | scout_mask
    out = arr[mask, :3].copy()
    order = np.argsort(out[:, 0])
    out = out[order]
    if len(out):
        out[:, 0] -= out[0, 0]
    return out, f"ns_pre_tail_{float(pre_tail_s):g}s_plus_scout"


def _ca_sequence_continuity_metrics(ca: np.ndarray) -> dict:
    """Audit whether linked CA Ns steps were sampled as a continuous sequence."""
    arr = np.asarray(ca, dtype=float)
    metrics: dict[str, object] = {}
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 4:
        metrics["available"] = False
        return metrics
    order = np.argsort(arr[:, 0])
    arr = arr[order]
    diffs = np.diff(arr[:, 0])
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    median_dt = float(np.nanmedian(diffs)) if len(diffs) else float("nan")
    metrics["available"] = True
    metrics["median_dt_s"] = median_dt

    def tail_head_stats(from_ns: int, to_ns: int, label: str) -> dict:
        left = arr[np.isclose(arr[:, 3], from_ns)]
        right = arr[np.isclose(arr[:, 3], to_ns)]
        out: dict[str, object] = {"available": bool(len(left) and len(right))}
        if not out["available"]:
            return out
        gap = float(right[0, 0] - left[-1, 0])
        threshold = max(0.2, 2.0 * median_dt) if np.isfinite(median_dt) else 0.2
        left_tail = left[left[:, 0] >= left[-1, 0] - 1.0]
        right_head = right[right[:, 0] <= right[0, 0] + 1.0]
        out.update(
            {
                "from_ns": int(from_ns),
                "to_ns": int(to_ns),
                "from_last_time_s": float(left[-1, 0]),
                "to_first_time_s": float(right[0, 0]),
                "sample_gap_s": gap,
                "nearly_continuous": bool(gap <= threshold),
                "continuity_threshold_s": float(threshold),
                "from_last_ewe_v": float(left[-1, 1]),
                "to_first_ewe_v": float(right[0, 1]),
                "sample_ewe_jump_v": float(right[0, 1] - left[-1, 1]),
                "from_tail1s_ewe_median_v": float(np.nanmedian(left_tail[:, 1])),
                "to_head1s_ewe_median_v": float(np.nanmedian(right_head[:, 1])),
                "tail_to_head1s_ewe_jump_v": float(np.nanmedian(right_head[:, 1]) - np.nanmedian(left_tail[:, 1])),
                "from_last_current_a": float(left[-1, 2]),
                "to_first_current_a": float(right[0, 2]),
                "sample_current_jump_a": float(right[0, 2] - left[-1, 2]),
                "from_tail1s_current_median_a": float(np.nanmedian(left_tail[:, 2])),
                "to_head1s_current_median_a": float(np.nanmedian(right_head[:, 2])),
                "tail_to_head1s_current_jump_a": float(np.nanmedian(right_head[:, 2]) - np.nanmedian(left_tail[:, 2])),
            }
        )
        return out

    metrics["pre_to_scout"] = tail_head_stats(0, 1, "pre_to_scout")
    metrics["scout_to_post"] = tail_head_stats(1, 2, "scout_to_post")
    return metrics


def _plot_ca_sequence_continuity(ca: np.ndarray, out_png: Path, metrics: dict) -> None:
    arr = np.asarray(ca, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return
    order = np.argsort(arr[:, 0])
    arr = arr[order]
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), constrained_layout=True)
    ns = arr[:, 3] if arr.shape[1] >= 4 else np.zeros(len(arr))
    colors = {0: "#4c78a8", 1: "#f58518", 2: "#54a24b"}
    labels = {0: "pre Ns=0", 1: "scout Ns=1", 2: "post Ns=2"}
    for value in [0, 1, 2]:
        mask = np.isclose(ns, value)
        if np.any(mask):
            axes[0, 0].plot(arr[mask, 0], arr[mask, 1], ".", ms=2, color=colors[value], label=labels[value])
            axes[1, 0].plot(arr[mask, 0], arr[mask, 2] * 1e9, ".", ms=2, color=colors[value], label=labels[value])
    axes[0, 0].set_title("Full linked CA Ewe")
    axes[0, 0].set_xlabel("time / s")
    axes[0, 0].set_ylabel("Ewe / V")
    axes[1, 0].set_title("Full linked CA current")
    axes[1, 0].set_xlabel("time / s")
    axes[1, 0].set_ylabel("current / nA")
    axes[0, 0].legend(loc="best", fontsize=8)

    for ax, key, title in [
        (axes[0, 1], "pre_to_scout", "pre -> scout zoom"),
        (axes[1, 1], "scout_to_post", "scout -> post zoom"),
    ]:
        item = metrics.get(key, {}) if isinstance(metrics, dict) else {}
        if not item or not item.get("available"):
            ax.set_title(title + " unavailable")
            continue
        t0 = float(item["to_first_time_s"])
        mask = (arr[:, 0] >= t0 - 3.0) & (arr[:, 0] <= t0 + 3.0)
        ax2 = ax.twinx()
        ax.plot(arr[mask, 0], arr[mask, 1], ".-", ms=3, color="#1f77b4", label="Ewe")
        ax2.plot(arr[mask, 0], arr[mask, 2] * 1e9, ".-", ms=3, color="#d62728", label="I")
        ax.axvline(t0, color="k", ls="--", lw=1)
        ax.set_title(f"{title} | gap={float(item['sample_gap_s']):.4g}s")
        ax.set_xlabel("time / s")
        ax.set_ylabel("Ewe / V", color="#1f77b4")
        ax2.set_ylabel("current / nA", color="#d62728")
    fig.suptitle("OLE-COM linked CA continuity audit")
    fig.savefig(out_png, dpi=180)
    plt.close(fig)


def _save_fft_ready_trace_from_recommendation(
    *,
    out_dir: Path,
    rec: dict,
    raw_ca_txt: Path,
    fallback_ca: np.ndarray,
) -> tuple[Path, np.ndarray, str]:
    """Persist the exact processed CA trace used for LF recommendation.

    The full-arc builder reads summary["ca_fft_txt"].  Keep that path pointed at
    the same auto-trimmed/despiked trace used by recommend_peis_lf_from_cp_txt,
    not the raw pre+scout audit file.
    """
    fft_ready = np.asarray(rec.get("fft_ready_trace", []), dtype=float)
    if fft_ready.ndim == 2 and fft_ready.shape[0] >= 2 and fft_ready.shape[1] >= 3:
        processed_txt = _save_ca_txt(out_dir / "ca_for_fft_pre_plus_scout_fft_ready.txt", fft_ready[:, :3])
        return processed_txt, fft_ready[:, :3], "auto_trimmed_fft_ready_trace"
    return raw_ca_txt, fallback_ca[:, :3], "raw_pre_plus_scout_fallback"


def _clamp_peis_lf(raw_lf: float, *, deep_limit: float, overlap_limit: float) -> float:
    if not np.isfinite(raw_lf) or raw_lf <= 0:
        return float(overlap_limit)
    return float(min(max(float(raw_lf), float(deep_limit)), float(overlap_limit)))


def _fit_and_plot(peis: np.ndarray, out_png: Path, title: str) -> dict:
    freq = peis[:, 0]
    re_z = peis[:, 1]
    neg_im = peis[:, 2]
    fit = EF.fit_equivalent_circuit_auto(freq, re_z, neg_im)
    model = fit.get("Equivalent circuit") or fit.get("Fit model") or "auto"
    params = EF.fit_result_to_model_params(fit)
    f_plot = np.logspace(np.log10(np.nanmin(freq)), np.log10(np.nanmax(freq)), 400)
    z_fit = EF.Z_model(model, params, f_plot) if params is not None else None

    fig, axs = plt.subplots(1, 3, figsize=(14, 4.5), constrained_layout=True)
    sc = axs[0].scatter(re_z, neg_im, c=np.log10(freq), cmap="plasma_r", s=34, label="PEIS")
    if z_fit is not None:
        axs[0].plot(z_fit.real, -z_fit.imag, color="black", lw=1.8, label=f"{model} fit")
    axs[0].set_title("Nyquist")
    axs[0].set_xlabel("Zre / Ohm")
    axs[0].set_ylabel("-Zim / Ohm")
    axs[0].grid(alpha=0.25)
    axs[0].legend()
    fig.colorbar(sc, ax=axs[0], label="log10(f / Hz)")

    z = re_z - 1j * neg_im
    axs[1].plot(np.log10(freq), np.log10(np.abs(z)), "o-", ms=3)
    if z_fit is not None:
        axs[1].plot(np.log10(f_plot), np.log10(np.abs(z_fit)), "k-", lw=1.8)
    axs[1].set_title("|Z|")
    axs[1].set_xlabel("log10(f / Hz)")
    axs[1].set_ylabel("log10(|Z| / Ohm)")
    axs[1].grid(alpha=0.25)

    axs[2].plot(np.log10(freq), np.degrees(np.angle(z)), "o-", ms=3)
    if z_fit is not None:
        axs[2].plot(np.log10(f_plot), np.degrees(np.angle(z_fit)), "k-", lw=1.8)
    axs[2].set_title("Phase")
    axs[2].set_xlabel("log10(f / Hz)")
    axs[2].set_ylabel("Phase / deg")
    axs[2].grid(alpha=0.25)

    score = fit.get("Fit quality score", np.nan)
    fig.suptitle(f"{title} | {model}, score={score:.4g}")
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return {k: (v.item() if isinstance(v, np.generic) else v) for k, v in fit.items() if k != "Auto fit candidate results"}


def _plot_onepage(
    *,
    out_png: Path,
    ca_all: np.ndarray,
    ca_fft: np.ndarray,
    peis: Optional[np.ndarray],
    summary: dict,
) -> None:
    fig, axs = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    ax = axs[0, 0]
    ns = ca_all[:, 3] if ca_all.shape[1] > 3 else np.zeros(len(ca_all))
    ax.plot(ca_all[:, 0], ca_all[:, 2] * 1e9, color="#b0b0b0", lw=0.8, label="all CA")
    for val, color, label in [(0, "#1f77b4", "pre 0 V"), (1, "#d62728", "scout dV"), (2, "#2ca02c", "post 0 V")]:
        mask = np.isclose(ns, val)
        if np.any(mask):
            ax.plot(ca_all[mask, 0], ca_all[mask, 2] * 1e9, color=color, lw=1.0, label=label)
    ax.set_title("OLE-COM CA: 0 -> dV -> 0")
    ax.set_xlabel("time / s")
    ax.set_ylabel("current / nA")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)

    ax = axs[1, 0]
    ax.plot(ca_fft[:, 0], ca_fft[:, 2] * 1e9, color="#d62728", lw=1.0)
    ax.set_title("FFT-used CA only (post removed)")
    ax.set_xlabel("time / s")
    ax.set_ylabel("current / nA")
    ax.grid(alpha=0.25)

    ax = axs[0, 1]
    if peis is not None and len(peis):
        sc = ax.scatter(peis[:, 1], peis[:, 2], c=np.log10(peis[:, 0]), cmap="plasma_r", s=32)
        fig.colorbar(sc, ax=ax, label="log10(f / Hz)")
    ax.set_title("Measured PEIS")
    ax.set_xlabel("Zre / Ohm")
    ax.set_ylabel("-Zim / Ohm")
    ax.grid(alpha=0.25)

    ax = axs[1, 1]
    lines = [
        f"bias = {summary.get('bias_v'):+.3f} V",
        f"dV scout = {summary.get('dv_v'):+.3f} V",
        f"raw FFT LF = {summary.get('raw_recommended_lf_hz')}",
        f"applied PEIS LF = {summary.get('applied_peis_lf_hz')}",
        f"PEIS range = {summary.get('peis_high_hz')} -> {summary.get('applied_peis_lf_hz')} Hz",
        f"CA stopped during post = {summary.get('ca_stopped_during_post')}",
        f"fit model = {summary.get('fit_model')}",
        f"fit score = {summary.get('fit_score')}",
    ]
    ax.axis("off")
    ax.text(0.02, 0.98, "\n".join(lines), va="top", ha="left", family="monospace", fontsize=11)
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _records_to_array(records: list[dict]) -> np.ndarray:
    if not records:
        return np.empty((0, 4), dtype=float)
    return np.array(
        [[r["time_s"], r["ewe_v"], r["current_A"], r["Ns"]] for r in records],
        dtype=float,
    )


def _plot_live_ca_snapshot(
    *,
    out_png: Path,
    records: list[dict],
    summary: dict,
    status_text: str,
) -> None:
    arr = _records_to_array(records)
    fig, axs = plt.subplots(2, 1, figsize=(10, 7), sharex=True, constrained_layout=True)
    fig.suptitle(status_text)

    if len(arr):
        ns = arr[:, 3]
        colors = {0: "#1f77b4", 1: "#d62728", 2: "#2ca02c"}
        labels = {0: "pre", 1: "dV scout", 2: "post"}
        axs[0].plot(arr[:, 0], arr[:, 2] * 1e9, color="#b0b0b0", lw=0.7, label="all")
        for val in (0, 1, 2):
            mask = np.isclose(ns, val)
            if np.any(mask):
                axs[0].plot(arr[mask, 0], arr[mask, 2] * 1e9, color=colors[val], lw=1.0, label=labels[val])
        axs[1].plot(arr[:, 0], arr[:, 1], color="#4c4c4c", lw=0.9)
        axs[0].legend(fontsize=8, loc="best")
    else:
        axs[0].text(0.5, 0.5, "Waiting for CA points...", transform=axs[0].transAxes, ha="center", va="center")
        axs[1].text(0.5, 0.5, "Waiting for Ewe points...", transform=axs[1].transAxes, ha="center", va="center")

    axs[0].set_ylabel("current / nA")
    axs[1].set_ylabel("Ewe / V")
    axs[1].set_xlabel("time / s")
    for ax in axs:
        ax.grid(alpha=0.25)
    info = (
        f"bias={summary.get('bias_v'):+.3f} V, dV={summary.get('dv_v'):+.3f} V, "
        f"raw LF={summary.get('raw_recommended_lf_hz')}, PEIS LF={summary.get('applied_peis_lf_hz')}"
    )
    axs[1].text(0.01, 0.02, info, transform=axs[1].transAxes, ha="left", va="bottom", fontsize=9)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _write_live_status(out_dir: Path, payload: dict) -> None:
    tmp = out_dir / "live_status.json.tmp"
    final = out_dir / "live_status.json"
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(final)


def run_once(args) -> dict:
    out_dir = Path(args.output_dir) if args.output_dir else PROJECT_DIR / "results" / f"olecom_hybrid_pre_scout_post_{_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    settings_path = out_dir / "settings.json"
    settings_path.write_text(json.dumps(vars(args), indent=2), encoding="utf-8")

    ca_mps = _write_ca_mps(
        out_dir / "olecom_pre_scout_post_ca.mps",
        bias_v=args.bias,
        dv_v=args.dv,
        pre_s=args.pre_s,
        scout_s=args.scout_s,
        post_s=args.post_s,
        dt_s=args.dt,
        bandwidth=args.bandwidth,
    )
    _require_mps_fields(
        ca_mps,
        [
            ("CA bias", f"{float(args.bias):.3f}"),
            ("CA scout", f"{float(args.bias + args.dv):.3f}"),
            ("CA I range", "I Range             Auto"),
            ("CA bandwidth", f"Bandwidth           {int(args.bandwidth)}"),
        ],
    )
    peis_mps = out_dir / "olecom_seeded_peis.mps"
    ca_output_base = out_dir / "olecom_pre_scout_post_ca"
    peis_output_base = out_dir / "olecom_seeded_peis"

    ctrl = OleComController(
        ip=args.ip,
        channel=args.channel,
        create_if_missing=args.allow_create_eclab,
        trust_test_connection=args.trust_test_connection,
    )
    summary = {
        "output_dir": str(out_dir),
        "bias_v": float(args.bias),
        "dv_v": float(args.dv),
        "peis_high_hz": float(args.peis_high),
    }
    records = []
    last_idx = 0
    fft_done = False
    ca_stopped_during_post = False
    raw_lf = None
    applied_lf = None
    ca_fft_for_plot = None
    live_ca_png = out_dir / "live_ca_status.png"
    last_live_plot_t = 0.0
    if bool(getattr(args, "auto_clean_eclab", False)):
        summary["auto_clean_eclab_killed_pids"] = _cleanup_eclab_processes(
            include_visible=bool(getattr(args, "auto_clean_visible_eclab", False))
        )
    try:
        ctrl.connect()
        ca_run = ctrl.load_and_run(ca_mps, ca_output_base)
        print(f"[OLE-COM] CA running: {ca_run.mpr_path}", flush=True)
        summary["live_ca_png"] = str(live_ca_png)
        _write_live_status(
            out_dir,
            {
                "stage": "ca_starting",
                "output_dir": str(out_dir),
                "ca_mpr": str(ca_run.mpr_path),
                "points": 0,
                "live_ca_png": str(live_ca_png),
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
        ctrl.wait_until_started(
            ca_run.mpr_path,
            startup_grace_s=args.ca_startup_grace_s,
            label="CA",
        )
        t_start = time.perf_counter()
        while True:
            if ca_run.mpr_path.exists():
                try:
                    n = ctrl.point_count(ca_run.mpr_path)
                except Exception:
                    n = last_idx
                for idx in range(last_idx, n):
                    try:
                        records.append(ctrl.dc_value(ca_run.mpr_path, idx))
                    except Exception:
                        break
                last_idx = max(last_idx, n)

                if records:
                    now = time.perf_counter()
                    rec_arr = _records_to_array(records)
                    if now - last_live_plot_t >= float(args.live_plot_interval_s):
                        status_text = f"OLE-COM CA live | points={len(records)} | t={rec_arr[-1, 0]:.1f}s"
                        _plot_live_ca_snapshot(
                            out_png=live_ca_png,
                            records=records,
                            summary=summary,
                            status_text=status_text,
                        )
                        _write_live_status(
                            out_dir,
                            {
                                "stage": "ca_running",
                                "output_dir": str(out_dir),
                                "ca_mpr": str(ca_run.mpr_path),
                                "points": len(records),
                                "last_time_s": float(rec_arr[-1, 0]),
                                "last_ewe_v": float(rec_arr[-1, 1]),
                                "last_current_A": float(rec_arr[-1, 2]),
                                "last_ns": int(round(float(rec_arr[-1, 3]))),
                                "raw_recommended_lf_hz": raw_lf,
                                "applied_peis_lf_hz": applied_lf,
                                "live_ca_png": str(live_ca_png),
                                "updated": datetime.now().isoformat(timespec="seconds"),
                            },
                        )
                        last_live_plot_t = now
                    post_mask = np.isclose(rec_arr[:, 3], 2)
                    scout_mask = np.isclose(rec_arr[:, 3], 1)
                    if (
                        not fft_done
                        and np.any(post_mask)
                        and np.any(scout_mask)
                        and float(rec_arr[post_mask, 0].max() - rec_arr[post_mask, 0].min()) >= float(args.min_post_before_stop_s)
                    ):
                        fft_ca_full = rec_arr[np.isclose(rec_arr[:, 3], 0) | np.isclose(rec_arr[:, 3], 1)]
                        fft_ca, fft_ca_trim_source = _select_pre_tail_plus_scout(
                            fft_ca_full,
                            pre_tail_s=args.fft_pre_tail_s,
                        )
                        raw_ca_fft_txt = _save_ca_txt(out_dir / "ca_for_fft_pre_plus_scout_only.txt", fft_ca)
                        rec = recommend_peis_lf_from_cp_txt(raw_ca_fft_txt, current_in_mA=False)
                        ca_fft_txt, ca_fft_for_plot, ca_fft_source = _save_fft_ready_trace_from_recommendation(
                            out_dir=out_dir,
                            rec=rec,
                            raw_ca_txt=raw_ca_fft_txt,
                            fallback_ca=fft_ca,
                        )
                        raw_lf = float(rec["recommendation"]["recommended_peis_lowest_freq_hz"])
                        applied_lf = _clamp_peis_lf(
                            raw_lf,
                            deep_limit=args.peis_deep_limit,
                            overlap_limit=args.peis_overlap_limit,
                        )
                        summary.update(
                            {
                                "raw_recommended_lf_hz": raw_lf,
                                "applied_peis_lf_hz": applied_lf,
                                "fft_ready_duration_s": float(rec.get("smoothed_duration_s", np.nan)),
                                "fft_ready_average_bin_s": rec.get("fft_ready_average_bin_s"),
                                "fft_ready_current_despike": rec.get("fft_ready_current_despike"),
                                "fft_ready_segmented_smoothing_window_points": rec.get("fft_ready_segmented_smoothing_window_points"),
                                "fft_ready_segmented_smoothing_window_s": rec.get("fft_ready_segmented_smoothing_window_s"),
                                "fft_ready_segmented_smoothing_step_guard_s": rec.get("fft_ready_segmented_smoothing_step_guard_s"),
                                "fft_ready_segmented_smoothing_blend_s": rec.get("fft_ready_segmented_smoothing_blend_s"),
                                "cp_saturation": rec.get("cp_saturation", {}),
                                "ca_fft_raw_txt": str(raw_ca_fft_txt),
                                "ca_fft_txt": str(ca_fft_txt),
                                "ca_fft_source": ca_fft_source,
                                "ca_fft_trim_source": fft_ca_trim_source,
                                "ca_fft_pre_tail_s": float(args.fft_pre_tail_s),
                            }
                        )
                        _plot_live_ca_snapshot(
                            out_png=live_ca_png,
                            records=records,
                            summary=summary,
                            status_text=f"OLE-COM CA FFT ready | raw LF={raw_lf:.5g} Hz | PEIS LF={applied_lf:.5g} Hz",
                        )
                        _write_live_status(
                            out_dir,
                            {
                                "stage": "ca_fft_ready",
                                "output_dir": str(out_dir),
                                "ca_mpr": str(ca_run.mpr_path),
                                "points": len(records),
                                "raw_recommended_lf_hz": raw_lf,
                                "applied_peis_lf_hz": applied_lf,
                                "live_ca_png": str(live_ca_png),
                                "updated": datetime.now().isoformat(timespec="seconds"),
                            },
                        )
                        fft_done = True
                        if args.stop_post_after_fft:
                            ctrl.stop()
                            ca_stopped_during_post = True
                            print(f"[OLE-COM] FFT done, stopping post. raw LF={raw_lf:.5g}, PEIS LF={applied_lf:.5g}", flush=True)
                            break

            if not ctrl.is_running():
                break
            if time.perf_counter() - t_start > float(args.pre_s + args.scout_s + args.post_s + 60):
                ctrl.stop()
                raise TimeoutError("CA run exceeded expected duration")
            time.sleep(float(args.poll_s))

        # Give EC-Lab a small flush window before parsing the final .mpr.
        time.sleep(1.0)
        stable_points = _wait_for_point_count_stable(
            ctrl,
            ca_run.mpr_path,
            stable_s=args.buffer_stable_s,
            timeout_s=args.buffer_timeout_s,
            poll_s=args.poll_s,
            label="CA MPR flush",
        )
        summary["ca_stable_points_after_flush"] = int(stable_points)
        ca_all = _parse_mpr_dc(ca_run.mpr_path)
        continuity = _ca_sequence_continuity_metrics(ca_all)
        continuity_png = out_dir / "ca_sequence_continuity_audit.png"
        _plot_ca_sequence_continuity(ca_all, continuity_png, continuity)
        summary["ca_sequence_continuity"] = continuity
        summary["ca_sequence_continuity_png"] = str(continuity_png)
        ca_fft_full = ca_all[np.isclose(ca_all[:, 3], 0) | np.isclose(ca_all[:, 3], 1)]
        ca_fft, fft_ca_trim_source = _select_pre_tail_plus_scout(ca_fft_full, pre_tail_s=args.fft_pre_tail_s)
        if not fft_done:
            raw_ca_fft_txt = _save_ca_txt(out_dir / "ca_for_fft_pre_plus_scout_only.txt", ca_fft)
            rec = recommend_peis_lf_from_cp_txt(raw_ca_fft_txt, current_in_mA=False)
            ca_fft_txt, ca_fft_for_plot, ca_fft_source = _save_fft_ready_trace_from_recommendation(
                out_dir=out_dir,
                rec=rec,
                raw_ca_txt=raw_ca_fft_txt,
                fallback_ca=ca_fft,
            )
            raw_lf = float(rec["recommendation"]["recommended_peis_lowest_freq_hz"])
            applied_lf = _clamp_peis_lf(raw_lf, deep_limit=args.peis_deep_limit, overlap_limit=args.peis_overlap_limit)
            summary.update(
                {
                    "raw_recommended_lf_hz": raw_lf,
                    "applied_peis_lf_hz": applied_lf,
                    "fft_ready_duration_s": float(rec.get("smoothed_duration_s", np.nan)),
                    "fft_ready_average_bin_s": rec.get("fft_ready_average_bin_s"),
                    "fft_ready_current_despike": rec.get("fft_ready_current_despike"),
                    "fft_ready_segmented_smoothing_window_points": rec.get("fft_ready_segmented_smoothing_window_points"),
                    "fft_ready_segmented_smoothing_window_s": rec.get("fft_ready_segmented_smoothing_window_s"),
                    "fft_ready_segmented_smoothing_step_guard_s": rec.get("fft_ready_segmented_smoothing_step_guard_s"),
                    "fft_ready_segmented_smoothing_blend_s": rec.get("fft_ready_segmented_smoothing_blend_s"),
                    "cp_saturation": rec.get("cp_saturation", {}),
                    "ca_fft_raw_txt": str(raw_ca_fft_txt),
                    "ca_fft_txt": str(ca_fft_txt),
                    "ca_fft_source": ca_fft_source,
                    "ca_fft_trim_source": fft_ca_trim_source,
                    "ca_fft_pre_tail_s": float(args.fft_pre_tail_s),
                }
            )
        if ca_fft_for_plot is None:
            ca_fft_for_plot = ca_fft[:, :3]
        summary["ca_mpr"] = str(ca_run.mpr_path)
        summary["ca_stopped_during_post"] = bool(ca_stopped_during_post)
        _plot_live_ca_snapshot(
            out_png=live_ca_png,
            records=records,
            summary=summary,
            status_text="OLE-COM CA complete",
        )

        _write_peis_mps(
            peis_mps,
            bias_v=args.bias,
            f_high_hz=args.peis_high,
            f_low_hz=applied_lf,
            points_per_decade=args.peis_points_per_decade,
            amplitude_mv=abs(args.dv) * 1000.0,
            bandwidth=args.bandwidth,
        )
        _require_mps_fields(
            peis_mps,
            [
                ("PEIS bias", f"E (V)               {float(args.bias):.4f}"),
                ("PEIS high", f"fi                  {float(args.peis_high):.3f}"),
                ("PEIS low", f"ff                  {float(applied_lf):.5f}"),
                ("PEIS amplitude", f"Va (mV)             {abs(float(args.dv)) * 1000.0:.1f}"),
                ("PEIS I range", "I Range             Auto"),
                ("PEIS bandwidth", f"Bandwidth           {int(args.bandwidth)}"),
            ],
        )
        peis_run = ctrl.load_and_run(peis_mps, peis_output_base)
        print(f"[OLE-COM] PEIS running: {peis_run.mpr_path}", flush=True)
        _write_live_status(
            out_dir,
            {
                "stage": "peis_running",
                "output_dir": str(out_dir),
                "ca_mpr": str(ca_run.mpr_path),
                "peis_mpr": str(peis_run.mpr_path),
                "raw_recommended_lf_hz": raw_lf,
                "applied_peis_lf_hz": applied_lf,
                "live_ca_png": str(live_ca_png),
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
        peis_points = _wait_for_run_completion(
            ctrl,
            peis_run.mpr_path,
            timeout_s=args.peis_timeout_s,
            startup_grace_s=args.peis_startup_grace_s,
            label="PEIS",
        )
        if peis_points <= 0:
            raise RuntimeError("PEIS completed but produced zero points")

        peis = _parse_mpr_eis(peis_run.mpr_path)
        if len(peis) == 0:
            raise RuntimeError(f"PEIS MPR has zero parseable impedance rows: {peis_run.mpr_path}")
        peis_txt = out_dir / "measured_peis.txt"
        np.savetxt(peis_txt, peis, delimiter="\t", header="freq_Hz\tReZ_ohm\tnegImZ_ohm", comments="")
        fit_png = out_dir / "peis_fit_onepage.png"
        fit = _fit_and_plot(peis, fit_png, f"OLE-COM PEIS after pre-scout-post CA, bias={args.bias:+.3f} V")
        summary.update(
            {
                "peis_mpr": str(peis_run.mpr_path),
                "peis_txt": str(peis_txt),
                "peis_points": int(len(peis)),
                "peis_min_hz": float(np.nanmin(peis[:, 0])),
                "peis_max_hz": float(np.nanmax(peis[:, 0])),
                "fit_png": str(fit_png),
                "fit_model": fit.get("Equivalent circuit") or fit.get("Fit model"),
                "fit_score": fit.get("Fit quality score"),
                "fit_cost": fit.get("Fit cost"),
                "fit": fit,
            }
        )
        onepage = out_dir / "olecom_hybrid_onepage.png"
        _plot_onepage(out_png=onepage, ca_all=ca_all, ca_fft=ca_fft_for_plot, peis=peis, summary=summary)
        summary["onepage_png"] = str(onepage)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        _write_live_status(
            out_dir,
            {
                "stage": "complete",
                "output_dir": str(out_dir),
                "summary_json": str(out_dir / "summary.json"),
                "onepage_png": str(onepage),
                "fit_png": str(fit_png),
                "peis_points": int(len(peis)),
                "fit_score": fit.get("Fit quality score"),
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
        print(json.dumps(summary, indent=2, default=str), flush=True)
        return summary
    except Exception as exc:
        _write_live_status(
            out_dir,
            {
                "stage": "error",
                "output_dir": str(out_dir),
                "error": f"{type(exc).__name__}: {exc}",
                "raw_recommended_lf_hz": raw_lf,
                "applied_peis_lf_hz": applied_lf,
                "live_ca_png": str(live_ca_png),
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
        try:
            if ctrl.obj is not None and ctrl.is_running():
                ctrl.stop()
                ctrl.wait_until_stopped(timeout_s=10.0)
        except Exception:
            pass
        raise
    finally:
        if bool(getattr(args, "disconnect_on_exit", False)):
            ctrl.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="OLE-COM hybrid EIS smoke test: pre -> dV scout -> post -> seeded PEIS.")
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1, help="Human EC-Lab channel number; internally OLE-COM uses channel-1.")
    parser.add_argument("--bias", type=float, default=0.0)
    parser.add_argument("--dv", type=float, default=0.03)
    parser.add_argument("--pre-s", type=float, default=60.0)
    parser.add_argument("--scout-s", type=float, default=120.0)
    parser.add_argument("--post-s", type=float, default=90.0)
    parser.add_argument("--min-post-before-stop-s", type=float, default=3.0)
    parser.add_argument("--stop-post-after-fft", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument("--bandwidth", type=int, default=4)
    parser.add_argument("--poll-s", type=float, default=0.5)
    parser.add_argument("--live-plot-interval-s", type=float, default=3.0)
    parser.add_argument("--buffer-stable-s", type=float, default=5.0)
    parser.add_argument("--buffer-timeout-s", type=float, default=180.0)
    parser.add_argument("--ca-startup-grace-s", type=float, default=15.0)
    parser.add_argument("--peis-high", type=float, default=100.0)
    parser.add_argument("--peis-deep-limit", type=float, default=0.05)
    parser.add_argument("--peis-overlap-limit", type=float, default=0.5)
    parser.add_argument("--peis-points-per-decade", type=int, default=10)
    parser.add_argument("--peis-timeout-s", type=float, default=1800.0)
    parser.add_argument("--peis-startup-grace-s", type=float, default=15.0)
    parser.add_argument(
        "--fft-pre-tail-s",
        type=float,
        default=10.0,
        help="Seconds of stable Ns=0 pre-tail to keep before Ns=1 scout for CA FFT recovery.",
    )
    parser.add_argument(
        "--disconnect-on-exit",
        action="store_true",
        help="Disconnect the EC-Lab device when the run finishes. Default keeps EC-Lab connected for the next LoadSettings call.",
    )
    parser.add_argument(
        "--allow-create-eclab",
        action="store_true",
        help="Allow this script to create a new hidden EC-Lab COM server if no active EC-Lab session exists. Default refuses to avoid duplicate EC-Lab processes.",
    )
    parser.add_argument(
        "--trust-test-connection",
        action="store_true",
        help="Recovery-only fallback for this EC-Lab OLE-COM build: continue when ConnectDevice returns 0 but TestConnection is true, then let LoadSettings/RunChannel prove ownership.",
    )
    parser.add_argument(
        "--auto-clean-eclab",
        action="store_true",
        help="Before connecting, terminate hidden/stale EC-Lab processes through WMI so OLE-COM starts from a clean session.",
    )
    parser.add_argument(
        "--auto-clean-visible-eclab",
        action="store_true",
        help="With --auto-clean-eclab, also terminate visible EC-Lab windows. Use only when no manual EC-Lab experiment must be preserved.",
    )
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    run_once(args)


if __name__ == "__main__":
    main()
