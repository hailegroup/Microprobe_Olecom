# -*- coding: utf-8 -*-
"""
BioLogic SP-200/SP-300 driver (easy-biologic 0.4.x, LAN).

Implements:
  1. PEIS (Potentiostatic EIS) at a DC bias voltage
  2. CA (Chronoamperometry) hold at DC bias
  3. CA perturbation pulse for FFT-EIS

Rapid EIS sequence per measurement point:
  Step 1  CA hold at V_dc              → establish steady state
  Step 2  PEIS at V_dc                 → reference EIS (high freq range)
  Step 3  CA perturbation at V_dc+dV   → single-pulse for FFT-EIS

easy-biologic docs: https://github.com/bicarlsen/easy-biologic
"""

from __future__ import annotations

import numpy as np

try:
    import easy_biologic as ebl
    import easy_biologic.base_programs as ebp
    import easy_biologic.lib.ec_find as ec_find
    import easy_biologic.lib.ec_lib as ecl
    import easy_biologic.lib.data_parser as eparser
    _HAS_BIOLOGIC = True
except Exception as _ebl_err:
    _HAS_BIOLOGIC = False
    ec_find = None
    ecl = None
    eparser = None
    print(f"[BioLogic] import failed: {_ebl_err}")

from config import BIOLOGIC_IP, BIOLOGIC_PORT

MAX_REASONABLE_EIS_ABS_OHM = 1.0e15
DEFAULT_PEIS_BIAS_SETTLE_S = 1.0


class BioLogicController:
    """
    High-level interface to BioLogic SP-200/SP-300 via easy-biologic 0.4.x.

    Usage
    -----
    bl = BioLogicController()
    bl.connect()
    eis_data = bl.run_peis(v_dc=0.3, f_high=1e5, f_low=0.1, n_pts=60)
    ca_data  = bl.run_ca_hold(v_dc=0.3, duration=30)
    bl.disconnect()
    """

    def __init__(self, ip=None, port=None):
        self.ip   = ip   or BIOLOGIC_IP
        self.port = port or BIOLOGIC_PORT
        self.dev  = None
        self._active_stop_event = None

    def _default_hardware_params(self, *, current_range=None, bandwidth=None) -> dict:
        """
        Match the EC-Lab chrono/EIS hardware defaults as closely as easy-biologic
        exposes them.

        We prefer Auto current range and BW8, which matches the user's EC-Lab
        setup on SP-300 hardware. If BW8 is not available in the installed
        package/device family, fall back to the fastest widely supported BW7.
        """
        params = {}
        if ecl is None:
            return params

        if current_range is None:
            current_range = getattr(getattr(ecl, 'IRange', None), 'AUTO', None)
        if current_range is not None:
            params['current_range'] = current_range

        if bandwidth is None:
            bandwidth_enum = getattr(ecl, 'Bandwidth', None)
            if bandwidth_enum is not None:
                bandwidth = getattr(bandwidth_enum, 'BW8', None)
                if bandwidth is None:
                    bandwidth = getattr(bandwidth_enum, 'BW7', None)
        if bandwidth is not None:
            params['bandwidth'] = bandwidth

        return params

    # ── Connection ─────────────────────────────────────────────────────────
    def connect(self):
        if not _HAS_BIOLOGIC:
            raise ImportError("easy-biologic import failed — check terminal for details")
        self.dev = ebl.BiologicDevice(self.ip)
        self.dev.connect()
        print(f"[BioLogic] Connected to {self.ip}")

    def disconnect(self):
        if self.dev:
            try:
                self.dev.disconnect()
            except Exception:
                pass
            self.dev = None
            self._active_stop_event = None
            print("[BioLogic] Disconnected")

    @staticmethod
    def _channel_info_to_dict(info) -> dict:
        if info is None:
            return {}
        out = {}
        fields = getattr(info, "_fields_", None)
        if fields:
            names = [name for name, *_ in fields]
        else:
            names = (
                "Channel", "BoardVersion", "BoardSerialNumber",
                "FirmwareVersion", "AmpCode", "NbAmps", "Lcboard",
                "Zboard", "MemSize", "State", "MaxIRange", "MinIRange",
                "MaxBandwidth", "NbOfTechniques",
            )
        for name in names:
            if not hasattr(info, name):
                continue
            value = getattr(info, name)
            if hasattr(value, "value"):
                value = value.value
            try:
                if isinstance(value, np.generic):
                    value = value.item()
            except Exception:
                pass
            try:
                if isinstance(value, bytes):
                    value = value.decode(errors="replace")
            except Exception:
                pass
            out[name] = value
        return out

    @staticmethod
    def discover_devices(connection="eth") -> list[dict]:
        """
        Discover BioLogic instruments using BioLogic's BL_FindEChem* DLL.

        This is more reliable than probing TCP port 5000 because some SP-300
        Ethernet setups are discoverable by EC-Lab/EC-Lib while that port does
        not respond to a generic socket probe.
        """
        if ec_find is None:
            return []
        try:
            devices = ec_find.find_devices(connection)
        except Exception:
            return []
        found = []
        for dev in devices:
            address = getattr(dev, "address", None)
            if not address:
                continue
            found.append(
                {
                    "connection": getattr(dev, "connection", None),
                    "address": str(address).strip(),
                    "kind": str(getattr(dev, "kind", "")).strip(),
                    "serial": str(getattr(dev, "sn", "")).strip(),
                    "gateway": str(getattr(dev, "gateway", "")).strip(),
                    "netmask": str(getattr(dev, "netmask", "")).strip(),
                }
            )
        return found

    def list_channels(self, max_channels=16) -> list[dict]:
        """
        Return 1-based BioLogic channels detected on the connected instrument.

        easy-biologic exposes plugged channels as 0-based indices. The GUI and
        public driver methods use 1-based channel numbers so the selected value
        matches the labels printed on the instrument front panel.
        """
        if self.dev is None:
            raise RuntimeError("BioLogic device is not connected")

        plugged = None
        try:
            plugged = getattr(self.dev, "plugged", None)
        except Exception:
            plugged = None
        if plugged is None and ecl is not None and hasattr(self.dev, "idn"):
            try:
                plugged = ecl.get_channels(self.dev.idn, size=max_channels)
            except Exception:
                plugged = None
        if plugged is None:
            plugged = [True]

        channels = []
        for ch_idx, is_plugged in enumerate(list(plugged)):
            if not bool(is_plugged):
                continue
            info_dict = {}
            try:
                if hasattr(self.dev, "channel_info"):
                    info_dict = self._channel_info_to_dict(self.dev.channel_info(ch_idx))
            except Exception as exc:
                info_dict = {"info_error": str(exc)}
            channels.append(
                {
                    "channel": ch_idx + 1,
                    "index": ch_idx,
                    "plugged": True,
                    "label": f"CH {ch_idx + 1}",
                    "info": info_dict,
                }
            )

        if not channels:
            channels.append(
                {
                    "channel": 1,
                    "index": 0,
                    "plugged": True,
                    "label": "CH 1",
                    "info": {"fallback": "No plugged channel reported; using channel 1"},
                }
            )
        return channels

    def stop_measurement(self, channel=1):
        """
        Request that the currently running technique stop as soon as possible.
        """
        if self._active_stop_event is not None:
            self._active_stop_event.set()
        if self.dev is None:
            return
        ch_idx = channel - 1
        try:
            if hasattr(self.dev, 'stop_channel'):
                self.dev.stop_channel(ch_idx)
            elif hasattr(self.dev, 'stop_channels'):
                self.dev.stop_channels([ch_idx])
        except Exception as exc:
            print(f"[BioLogic] stop_measurement warning: {exc}")

    def get_ocv(self, channel=1) -> float:
        """
        Read the latest working electrode voltage (Ewe) without starting a program.
        """
        if self.dev is None:
            raise RuntimeError("BioLogic device is not connected")
        ch_idx = channel - 1
        if not hasattr(self.dev, 'get_values'):
            raise RuntimeError(
                "This easy-biologic device object does not expose get_values(); "
                "the OCV read path must be verified against the installed easy-biologic version."
            )
        values = self.dev.get_values(ch_idx)
        if isinstance(values, dict):
            for key in ('Ewe', 'ewe', 'voltage'):
                if key in values:
                    return float(values[key])
        if hasattr(values, 'Ewe'):
            return float(values.Ewe)
        if hasattr(values, 'ewe'):
            return float(values.ewe)
        raise RuntimeError(f"Could not read OCV from BioLogic values: {values!r}")

    def get_live_values(self, channel=1) -> dict:
        """
        Read the current live scalar values exposed by EC-Lib.

        This mirrors the `CurrentValues` structure used by lower-level EC-Lib /
        OLE-COM integrations and is useful for status polling even when full
        PEIS data points are only retrieved in larger buffered chunks.
        """
        if self.dev is None:
            raise RuntimeError("BioLogic device is not connected")
        ch_idx = channel - 1
        if not hasattr(self.dev, 'get_values'):
            raise RuntimeError(
                "This easy-biologic device object does not expose get_values(); "
                "live status polling is unavailable in the installed version."
            )

        values = self.dev.get_values(ch_idx)
        keys = (
            'State', 'MemFilled', 'ElapsedTime', 'Ewe', 'Ece', 'I',
            'Freq', 'Rcomp', 'Saturation', 'Eoverflow', 'Ioverflow',
        )
        snapshot = {}
        for key in keys:
            if hasattr(values, key):
                snapshot[key.lower()] = getattr(values, key)

        # Keep a few friendlier aliases for the GUI layer.
        if 'elapsedtime' in snapshot:
            snapshot['elapsed_s'] = float(snapshot['elapsedtime'])
        if 'freq' in snapshot:
            snapshot['frequency_hz'] = float(snapshot['freq'])
        if 'ewe' in snapshot:
            snapshot['ewe_v'] = float(snapshot['ewe'])
        if 'i' in snapshot:
            snapshot['current_a'] = float(snapshot['i'])
        if 'memfilled' in snapshot:
            snapshot['buffer_bytes'] = int(snapshot['memfilled'])
        return snapshot

    def get_buffered_data(self, channel=1, parse_kind=None) -> dict:
        """
        Pull and clear the currently buffered EC-Lib data for a channel.

        This mirrors the lower-level `BL_GetData` / NUPyLab-style split between
        lightweight scalar `CurrentValues` polling and explicit buffered data
        retrieval. It is meant for experiments with mid-run PEIS chunk access.
        """
        if self.dev is None:
            raise RuntimeError("BioLogic device is not connected")
        if ecl is None or eparser is None:
            raise RuntimeError(
                "easy-biologic low-level EC-Lib bindings are unavailable; "
                "buffered data retrieval cannot be used in this environment."
            )

        ch_idx = channel - 1
        raw, info, values = ecl.get_data(self.dev.idn, ch_idx)
        return self._format_buffered_data_payload(raw, info, values, parse_kind=parse_kind)

    # ── PEIS ───────────────────────────────────────────────────────────────
    def run_peis(self, v_dc: float, f_high=1e5, f_low=0.1,
                 n_pts=60, amplitude_mv=10.0, channel=1,
                 current_range=None, bandwidth=None,
                 on_segment=None, read_interval=1.0, stop_event=None,
                 bias_settle_s=DEFAULT_PEIS_BIAS_SETTLE_S):
        """
        Run Potentiostatic EIS.
        Returns numpy array columns: [freq/Hz, Re(Z)/Ohm, -Im(Z)/Ohm]
        """
        v_dc = float(v_dc)
        amplitude_v = float(amplitude_mv) / 1000.0
        bias_settle_s = max(0.0, float(bias_settle_s))
        params = {
            'voltage':            v_dc,
            'amplitude_voltage':  amplitude_v,
            'initial_frequency':  f_high,
            'final_frequency':    f_low,
            'frequency_number':   n_pts,
            # A nonzero PEIS pre-step duration makes the instrument explicitly
            # apply the requested DC offset before the sinusoidal EIS process.
            # With duration=0 some SP-300/easy-biologic combinations can appear
            # to start the AC perturbation around 0 V after a CA handoff.
            'duration':           bias_settle_s,
            'vs_initial':         False,
            'time_interval':      1,
            'current_interval':   0.001,
            # easy-biologic 0.4.x mishandles dict params['sweep'] via params.sweep,
            # so we omit it here and rely on the default logarithmic spacing.
            'repeat':             1,
            'correction':         False,
            'wait':               0,
        }
        params.update(self._default_hardware_params(current_range=current_range, bandwidth=bandwidth))
        if ecl is not None and hasattr(ebp, 'get_voltage_range'):
            try:
                params['voltage_range'] = ebp.get_voltage_range(abs(v_dc) + abs(amplitude_v))
            except Exception as exc:
                print(f"[BioLogic] PEIS voltage range warning: {exc}")
        print(
            f"[BioLogic] PEIS: DC bias={v_dc:+.4f} V, "
            f"AC amp={amplitude_v:.4f} V, settle={bias_settle_s:.1f}s, "
            f"{f_high:.1e}~{f_low:.1e} Hz  {n_pts} pts"
        )
        raw = self._run_program(ebp.PEIS, params, channel,
                                parse_kind='eis', on_segment=on_segment,
                                read_interval=read_interval,
                                stop_event=stop_event)
        return self._parse_eis(raw)

    # ── CA hold ────────────────────────────────────────────────────────────
    def run_ca_hold(self, v_dc: float, duration: float,
                    dt_record=0.1, channel=1, current_range=None, bandwidth=None, on_segment=None,
                    read_interval=0.05, stop_event=None):
        """
        Run CA at v_dc for `duration` seconds.
        Returns numpy array columns: [time/s, V/V, I/A]
        """
        params = {
            'voltages':         [v_dc],
            'durations':        [duration],
            'vs_initial':       False,
            'time_interval':    dt_record,
            'current_interval': 0.001,
        }
        params.update(self._default_hardware_params(current_range=current_range, bandwidth=bandwidth))
        print(f"[BioLogic] CA hold: V={v_dc}V for {duration}s")
        raw = self._run_program(ebp.CA, params, channel,
                                parse_kind='dc', on_segment=on_segment,
                                read_interval=read_interval,
                                stop_event=stop_event)
        return self._parse_dc(raw)

    def run_ca_sequence(self, voltage_steps, duration_steps,
                        dt_record=0.1, channel=1, current_range=None, bandwidth=None, on_segment=None,
                        read_interval=0.05, stop_event=None):
        """
        Run a multi-step CA sequence within a single CA technique.
        Returns numpy array columns: [time/s, V/V, I/A]
        """
        if len(voltage_steps) != len(duration_steps):
            raise ValueError("voltage_steps and duration_steps must have the same length")
        params = {
            'voltages':         list(voltage_steps),
            'durations':        list(duration_steps),
            'vs_initial':       False,
            'time_interval':    dt_record,
            'current_interval': 0.001,
        }
        params.update(self._default_hardware_params(current_range=current_range, bandwidth=bandwidth))
        desc = ', '.join(
            f"{float(v):.4f}V for {float(t):g}s"
            for v, t in zip(voltage_steps, duration_steps)
        )
        print(f"[BioLogic] CA sequence: {desc}")
        raw = self._run_program(ebp.CA, params, channel,
                                parse_kind='dc', on_segment=on_segment,
                                read_interval=read_interval,
                                stop_event=stop_event)
        return self._parse_dc(raw)

    # ── CA perturbation (FFT-EIS) ──────────────────────────────────────────
    def run_ca_perturbation(self, v_dc: float, dv: float, duration: float,
                             dt_record=0.1, channel=1, current_range=None, bandwidth=None, on_segment=None,
                             read_interval=0.05, stop_event=None):
        """
        Run CA at (v_dc + dv) for `duration` seconds.
        Returns numpy array columns: [time/s, V/V, I/A]
        """
        v_perturb = v_dc + dv
        params = {
            'voltages':         [v_perturb],
            'durations':        [duration],
            'vs_initial':       False,
            'time_interval':    dt_record,
            'current_interval': 0.001,
        }
        params.update(self._default_hardware_params(current_range=current_range, bandwidth=bandwidth))
        print(f"[BioLogic] CA perturbation: V={v_perturb:.4f}V ({dv*1000:+.1f}mV) for {duration}s")
        raw = self._run_program(ebp.CA, params, channel,
                                parse_kind='dc', on_segment=on_segment,
                                read_interval=read_interval,
                                stop_event=stop_event)
        return self._parse_dc(raw)

    # ── Internal helpers ───────────────────────────────────────────────────
    def _run_program(self, prog_cls, params, channel: int,
                     parse_kind=None, on_segment=None, read_interval=0.25,
                     stop_event=None):
        """Instantiate program, run, return list of data points for the channel."""
        ch_idx = channel - 1   # easy-biologic 0.4.x uses 0-based channel index
        # The controller already owns the device connection. Letting each
        # easy-biologic Program auto-connect again can leave channel state in a
        # broken/empty state after aggressive stop/reconnect recovery paths.
        prog = prog_cls(
            self.dev,
            params,
            channels=[ch_idx],
            stop_event=stop_event,
            autoconnect=False,
        )
        if not hasattr(prog, 'channel'):
            prog.channel = ch_idx
        self._active_stop_event = stop_event
        logged_empty_eis_chunk_debug = False
        if on_segment and hasattr(prog, 'on_data'):
            def _forward_segment(segment, _program):
                nonlocal logged_empty_eis_chunk_debug
                try:
                    parsed = self._parse_segment(segment.data, parse_kind)
                except Exception:
                    parsed = np.empty((0, 0))
                if parse_kind == 'eis' and parsed.size == 0:
                    memfilled = getattr(segment.values, 'MemFilled', 0)
                    freq = getattr(segment.values, 'Freq', None)
                    if memfilled:
                        freq_text = f" near {float(freq):.4g} Hz" if freq not in (None, 0) else ""
                        print(
                            f"[BioLogic] PEIS live chunk buffered ({int(memfilled)} B){freq_text} "
                            "but no Nyquist points were parseable yet."
                        )
                        if not logged_empty_eis_chunk_debug:
                            logged_empty_eis_chunk_debug = True
                            print(
                                "[BioLogic] First empty PEIS chunk debug: "
                                f"values={self._describe_segment_values(segment.values)}; "
                                f"data={self._describe_first_eis_datum(segment.data)}"
                            )
                on_segment(parsed, segment)

            prog.on_data(_forward_segment)
        ran_with_custom_interval = False
        if hasattr(prog, '_run') and hasattr(prog, 'params'):
            try:
                technique, low_level_params = self._build_low_level_params(
                    prog_cls, prog.params
                )
                prog._run(
                    technique,
                    low_level_params,
                    read_interval=read_interval,
                    retrieve_data=True,
                )
                ran_with_custom_interval = True
            except Exception:
                ran_with_custom_interval = False

        try:
            if not ran_with_custom_interval:
                prog.run(True)
            data = prog.data
            # data is a dict keyed by channel index
            if isinstance(data, dict):
                return data.get(ch_idx, data.get(channel, []))
            return data   # fallback: might already be a list
        finally:
            self._active_stop_event = None

    def _build_low_level_params(self, prog_cls, params):
        if prog_cls is ebp.CA:
            formatted = {}
            for ch, ch_params in params.items():
                steps = len(ch_params['voltages'])
                formatted[ch] = {
                    'Voltage_step':      ch_params['voltages'],
                    'vs_initial':        [ch_params['vs_initial']] * steps,
                    'Duration_step':     ch_params['durations'],
                    'Step_number':       steps - 1,
                    'Record_every_dT':   ch_params['time_interval'],
                    'Record_every_dI':   ch_params['current_interval'],
                    'N_Cycles':          0,
                }
                formatted[ch].update(
                    ebp.map_hardware_params(ch_params, by_channel=False)
                )
            return 'ca', formatted

        if prog_cls is ebp.PEIS:
            formatted = {}
            for ch, ch_params in params.items():
                formatted[ch] = {
                    'vs_initial':           ch_params['vs_initial'],
                    'vs_final':             ch_params['vs_initial'],
                    'Initial_Voltage_step': ch_params['voltage'],
                    'Final_Voltage_step':   ch_params['voltage'],
                    'Duration_step':        ch_params['duration'],
                    'Step_number':          0,
                    'Record_every_dT':      ch_params['time_interval'],
                    'Record_every_dI':      ch_params['current_interval'],
                    'Final_frequency':      ch_params['final_frequency'],
                    'Initial_frequency':    ch_params['initial_frequency'],
                    'sweep':                ch_params.get('sweep', False),
                    'Amplitude_Voltage':    ch_params['amplitude_voltage'],
                    'Frequency_number':     ch_params['frequency_number'],
                    'Average_N_times':      ch_params['repeat'],
                    'Correction':           ch_params['correction'],
                    'Wait_for_steady':      ch_params['wait'],
                }
                formatted[ch].update(
                    ebp.map_hardware_params(ch_params, by_channel=False)
                )
            return 'peis', formatted

        raise ValueError(f"Unsupported program class for live read interval: {prog_cls}")

    def _parse_segment(self, raw, parse_kind):
        if parse_kind == 'dc':
            return self._parse_dc(raw)
        if parse_kind == 'eis':
            return self._parse_eis(raw)
        return np.empty((0, 0))

    def _format_buffered_data_payload(self, raw, info, values, parse_kind=None) -> dict:
        technique_name = None
        process_index = int(getattr(info, 'ProcessIndex', -1))
        try:
            technique = ecl.TechniqueId(getattr(info, 'TechniqueID'))
            technique_name = technique.name
        except Exception:
            technique = None

        parsed_rows = []
        if int(getattr(info, 'NbRows', 0) or 0) and int(getattr(info, 'NbCols', 0) or 0):
            try:
                parsed_rows = eparser.parse(raw, info, device=self.dev)
            except Exception as exc:
                parsed_rows = []
                technique_name = technique_name or f"parse_error:{type(exc).__name__}"

        if parse_kind is None:
            if technique_name in ('PEIS', 'GEIS') and process_index == 1:
                parse_kind = 'eis'
            elif technique_name in ('CA', 'CP', 'CALIMIT', 'CPLIMIT'):
                parse_kind = 'dc'

        parsed_array = np.empty((0, 0))
        if parse_kind == 'eis':
            parsed_array = self._parse_eis(parsed_rows)
        elif parse_kind == 'dc':
            parsed_array = self._parse_buffered_dc(parsed_rows, info, values)

        payload = {
            'technique': technique_name,
            'process_index': process_index,
            'nb_rows': int(getattr(info, 'NbRows', 0) or 0),
            'nb_cols': int(getattr(info, 'NbCols', 0) or 0),
            'start_time_s': self._safe_float(getattr(info, 'StartTime', None)),
            'raw_word_count': int(getattr(info, 'NbRows', 0) or 0) * int(getattr(info, 'NbCols', 0) or 0),
            'values': self._snapshot_current_values(values),
            'parsed_rows': parsed_rows,
            'parsed_array': parsed_array,
        }
        return payload

    def _parse_buffered_dc(self, raw, info, values) -> np.ndarray:
        rows = []
        for datum in raw:
            t_high = getattr(datum, 't_high', None)
            t_low = getattr(datum, 't_low', None)
            voltage = getattr(datum, 'voltage', None)
            current = getattr(datum, 'current', None)
            if None in (t_high, t_low, voltage, current):
                continue
            try:
                time_value = eparser.calculate_time(int(t_high), int(t_low), info, values)
                rows.append((float(time_value), float(voltage), float(current)))
            except Exception:
                continue
        if not rows:
            return np.empty((0, 3))
        return np.array(rows, dtype=float)

    def _snapshot_current_values(self, values) -> dict:
        snapshot = {}
        keys = (
            'State', 'MemFilled', 'ElapsedTime', 'Ewe', 'Ece', 'I',
            'Freq', 'Rcomp', 'Saturation', 'Eoverflow', 'Ioverflow',
        )
        for key in keys:
            if hasattr(values, key):
                snapshot[key.lower()] = getattr(values, key)
        if 'elapsedtime' in snapshot:
            snapshot['elapsed_s'] = self._safe_float(snapshot['elapsedtime'])
        if 'freq' in snapshot:
            snapshot['frequency_hz'] = self._safe_float(snapshot['freq'])
        if 'ewe' in snapshot:
            snapshot['ewe_v'] = self._safe_float(snapshot['ewe'])
        if 'i' in snapshot:
            snapshot['current_a'] = self._safe_float(snapshot['i'])
        if 'memfilled' in snapshot:
            snapshot['buffer_bytes'] = int(snapshot['memfilled'])
        return snapshot

    def _safe_float(self, value):
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _parse_dc(self, raw) -> np.ndarray:
        """Parse CA data into [time, V, I] numpy array."""
        rows = []
        for datum in raw:
            time_value = getattr(datum, 'time', None)
            voltage_value = None
            current_value = None

            for key in ('Ewe', 'ewe', 'voltage'):
                if hasattr(datum, key):
                    voltage_value = getattr(datum, key)
                    break

            for key in ('I', 'current'):
                if hasattr(datum, key):
                    current_value = getattr(datum, key)
                    break

            if time_value is None or voltage_value is None or current_value is None:
                continue

            rows.append((float(time_value), float(voltage_value), float(current_value)))

        if not rows:
            return np.empty((0, 3))

        return np.array(rows, dtype=float)

    def _parse_eis(self, raw) -> np.ndarray:
        """Parse PEIS data into [freq, Re(Z), -Im(Z)] numpy array."""
        rows = []
        for datum in raw:
            freq = getattr(datum, 'frequency', None)
            modulus = getattr(datum, 'impedance_modulus', None)
            phase = getattr(datum, 'impedance_phase', None)
            if modulus is None:
                abs_voltage = getattr(datum, 'abs_voltage', None)
                abs_current = getattr(datum, 'abs_current', None)
                try:
                    if abs_voltage is not None and abs_current not in (None, 0):
                        modulus = float(abs_voltage) / float(abs_current)
                except Exception:
                    modulus = None
            if freq in (None, 0) or modulus is None or phase is None:
                continue

            try:
                freq = float(freq)
                modulus = float(modulus)
                phase = float(phase)
            except Exception:
                continue
            if not (
                np.isfinite(freq) and
                np.isfinite(modulus) and
                np.isfinite(phase)
            ):
                continue
            if freq <= 0:
                continue
            # easy-biologic exposes phase but the exact unit can vary with technique data;
            # treat large-magnitude values as degrees, otherwise radians.
            phase_rad = np.deg2rad(phase) if abs(phase) > (2 * np.pi + 0.5) else phase
            re_z = modulus * np.cos(phase_rad)
            neg_im_z = -modulus * np.sin(phase_rad)
            if not (
                np.isfinite(re_z) and
                np.isfinite(neg_im_z)
            ):
                continue
            if max(abs(re_z), abs(neg_im_z), abs(modulus)) > MAX_REASONABLE_EIS_ABS_OHM:
                continue
            rows.append((freq, re_z, neg_im_z))

        if not rows:
            return np.empty((0, 3))

        arr = np.array(rows, dtype=float)
        idx = np.argsort(arr[:, 0])
        return arr[idx]

    def _describe_segment_values(self, values) -> str:
        keys = ['State', 'MemFilled', 'ElapsedTime', 'Freq', 'Ewe', 'I', 'Saturation']
        parts = []
        for key in keys:
            if hasattr(values, key):
                try:
                    parts.append(f"{key}={getattr(values, key)!r}")
                except Exception:
                    parts.append(f"{key}=<unreadable>")
        return ', '.join(parts) if parts else '<no-known-values>'

    def _describe_first_eis_datum(self, raw) -> str:
        try:
            first = raw[0]
        except Exception:
            return '<no-raw-data>'
        attrs = [
            'frequency',
            'impedance_modulus',
            'impedance_phase',
            'time',
            'voltage',
            'current',
        ]
        parts = []
        for attr in attrs:
            if hasattr(first, attr):
                try:
                    parts.append(f"{attr}={getattr(first, attr)!r}")
                except Exception:
                    parts.append(f"{attr}=<unreadable>")
        if parts:
            return ', '.join(parts)
        public_attrs = [name for name in dir(first) if not name.startswith('_')]
        return f"type={type(first).__name__}, attrs={public_attrs[:12]!r}"
