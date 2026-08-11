# -*- coding: utf-8 -*-
"""
BioLogic SP-200/SP-300 driver (easy-biologic, LAN).

Implements:
  1. PEIS (Potentiostatic EIS) at a DC bias voltage
  2. CA (Chronoamperometry) hold at DC bias
  3. CP (Chronopotentiometry) perturbation pulse
  4. Data retrieval

Rapid EIS sequence per measurement point:
  Step 1  CA hold at V_dc              → establish steady state
  Step 2  PEIS at V_dc                 → reference EIS (high freq range)
  Step 3  CA perturbation at V_dc+dV   → single-pulse for FFT-EIS
  Step 4  FFT-EIS recovery             → done externally in Convert_CP_to_EIS

easy-biologic docs: https://github.com/bicarlsen/easy-biologic
"""

import time
import numpy as np

try:
    import easy_biologic as ebl
    import easy_biologic.techniques as blt
    _HAS_BIOLOGIC = True
except Exception as _ebl_err:
    _HAS_BIOLOGIC = False
    print(f"[BioLogic] import failed: {_ebl_err}")
    print(f"[BioLogic] easy_biologic location: ", end='')
    try:
        import importlib.util, sys
        spec = importlib.util.find_spec('easy_biologic')
        print(spec.origin if spec else 'not found')
        if spec:
            import easy_biologic as _ebl_tmp
            print(f"[BioLogic] version: {getattr(_ebl_tmp, '__version__', 'unknown')}")
            print(f"[BioLogic] dir: {[x for x in dir(_ebl_tmp) if not x.startswith('_')]}")
    except Exception as _e2:
        print(f'probe failed: {_e2}')

from config import BIOLOGIC_IP, BIOLOGIC_PORT


class BioLogicController:
    """
    High-level interface to BioLogic SP-200/SP-300 via easy-biologic.

    Usage
    -----
    bl = BioLogicController()
    bl.connect()

    # Reference PEIS
    eis_data = bl.run_peis(v_dc=0.3, f_high=1e5, f_low=0.1, n_pts=60)

    # CA hold + perturbation for Rapid EIS
    ca_data = bl.run_ca_hold(v_dc=0.3, duration=30)
    cp_data = bl.run_cp_perturbation(v_dc=0.3, dv=0.03, duration=200)

    bl.disconnect()
    """

    def __init__(self, ip=None, port=None):
        self.ip   = ip   or BIOLOGIC_IP
        self.port = port or BIOLOGIC_PORT
        self.dev  = None

    # ── Connection ─────────────────────────────────────────────────────────
    def connect(self):
        if not _HAS_BIOLOGIC:
            raise ImportError("easy-biologic not installed")
        self.dev = ebl.BiologicDevice(self.ip)
        self.dev.connect()
        print(f"[BioLogic] Connected to {self.ip}")

    def disconnect(self):
        if self.dev:
            self.dev.disconnect()
            print("[BioLogic] Disconnected")

    # ── PEIS (reference EIS) ───────────────────────────────────────────────
    def run_peis(self, v_dc: float, f_high=1e5, f_low=0.1,
                 n_pts=60, amplitude_mv=10.0, channel=1):
        """
        Run Potentiostatic EIS and return numpy array [freq, ReZ, -ImZ].

        Parameters
        ----------
        v_dc        : DC bias voltage (V)
        f_high      : upper frequency limit (Hz)
        f_low       : lower frequency limit (Hz)
        n_pts       : number of frequency points (log-spaced)
        amplitude_mv: AC amplitude (mV RMS)
        channel     : BioLogic channel number (1-based)
        """
        params = blt.PEISParams(
            vs_initial        = False,            # vs equilibrium OCP? No, absolute
            initial_voltage_step = v_dc,
            duration_step      = 0,               # no hold before sweep
            record_dt          = 0,
            final_frequency    = f_low,
            initial_frequency  = f_high,
            sweep              = True,            # log sweep
            amplitude_voltage  = amplitude_mv / 1000.0,
            frequency_number   = n_pts,
            average_n_times    = 1,
            correction         = False,
            wait_for_steady    = 0,
        )
        print(f"[BioLogic] PEIS: V={v_dc}V, {f_high:.1e}~{f_low:.1e} Hz, {n_pts} pts")
        data = self._run_technique(blt.PEIS, params, channel)
        return self._parse_eis(data)

    # ── CA hold ────────────────────────────────────────────────────────────
    def run_ca_hold(self, v_dc: float, duration: float,
                    dt_record=0.1, channel=1):
        """
        Run CA at v_dc for `duration` seconds.
        Returns numpy array [time(s), V(V), I(A)].
        """
        params = blt.CAParams(
            vs_initial         = False,
            voltage_step       = [v_dc],
            duration_step      = [duration],
            record_dt          = dt_record,
            n_cycles           = 0,
        )
        print(f"[BioLogic] CA hold: V={v_dc}V for {duration}s")
        data = self._run_technique(blt.CA, params, channel)
        return self._parse_dc(data)

    # ── CA perturbation (for FFT-EIS) ──────────────────────────────────────
    def run_ca_perturbation(self, v_dc: float, dv: float, duration: float,
                             dt_record=0.01, channel=1):
        """
        Run CA at (v_dc + dv) for `duration` seconds (single perturbation pulse).
        Returns numpy array [time(s), V(V), I(A)].

        dv should be small (~10-30 mV) to stay in linear regime.
        """
        v_perturb = v_dc + dv
        params = blt.CAParams(
            vs_initial         = False,
            voltage_step       = [v_perturb],
            duration_step      = [duration],
            record_dt          = dt_record,
            n_cycles           = 0,
        )
        print(f"[BioLogic] CA perturbation: V={v_perturb:.4f}V ({dv*1000:+.1f}mV) for {duration}s")
        data = self._run_technique(blt.CA, params, channel)
        return self._parse_dc(data)

    # ── Internal helpers ───────────────────────────────────────────────────
    def _run_technique(self, technique_cls, params, channel: int):
        """Run a technique on the given channel and return raw data."""
        ch = self.dev.channel(channel)
        ch.load_technique(technique_cls, params, first=True, last=True)
        ch.start()
        while ch.is_running():
            time.sleep(1)
        data = ch.data()
        return data

    def _parse_dc(self, data) -> np.ndarray:
        """Parse CA/CP data into [time, V, I] numpy array."""
        t = np.array([d.time   for d in data])
        v = np.array([d.Ewe    for d in data])
        i = np.array([d.I      for d in data])
        return np.column_stack([t, v, i])

    def _parse_eis(self, data) -> np.ndarray:
        """Parse PEIS data into [freq, ReZ, -ImZ] numpy array."""
        f  = np.array([d.frequency for d in data])
        re = np.array([d.Zreal     for d in data])
        im = np.array([d.Zimag     for d in data])   # BioLogic stores -Im(Z)
        # Sort ascending frequency
        idx = np.argsort(f)
        return np.column_stack([f[idx], re[idx], im[idx]])
