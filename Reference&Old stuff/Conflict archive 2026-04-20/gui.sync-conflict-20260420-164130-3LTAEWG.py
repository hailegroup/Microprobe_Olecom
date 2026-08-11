# -*- coding: utf-8 -*-
"""
Microprobe Automated Measurement — GUI
Run: python gui.py
"""

import os
import sys
import time
import threading
import traceback
import queue
import socket

import numpy as np
import pandas as pd
import serial.tools.list_ports
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from config import COM_PORTS, BIOLOGIC_IP

# ── 측정 관련 import (연결 실패해도 GUI는 뜨도록) ───────────────────────────
def _safe_import():
    mods = {}
    try:
        from driver_biologic import BioLogicController
        mods['biologic'] = BioLogicController
    except Exception as e:
        mods['biologic'] = None
        print(f"[WARN] BioLogic import failed: {e}")
    try:
        from driver_motor import MDriveMotor
        mods['motor'] = MDriveMotor
    except Exception as e:
        mods['motor'] = None
    try:
        from driver_temp import WatlowController
        mods['temp'] = WatlowController
    except Exception as e:
        mods['temp'] = None
    try:
        from driver_mfc import AeraMFC
        mods['mfc'] = AeraMFC
    except Exception as e:
        mods['mfc'] = None
    try:
        from measurement_sequence import rapid_eis_sequence
        mods['sequence'] = rapid_eis_sequence
    except Exception as e:
        mods['sequence'] = None
    return mods

MODS = _safe_import()

# ── 색상 상수 ───────────────────────────────────────────────────────────────
CLR_BG      = '#f5f5f5'
CLR_HEADER  = '#2c3e50'
CLR_GREEN   = '#27ae60'
CLR_RED     = '#e74c3c'
CLR_ORANGE  = '#e67e22'
CLR_BLUE    = '#2980b9'
CLR_LGRAY   = '#ecf0f1'
CLR_GOLD    = '#f1c40f'
SKIP_TOKENS = {'', 'nan', 'none', 'non', 'skip', '-', 'na', 'n/a'}


def _parse_optional_float(value, default=None):
    if value is None:
        return default
    if pd.isna(value):
        return default
    text = str(value).strip().lower()
    if text in SKIP_TOKENS:
        return default
    return float(value)


class MicroprobGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Microprobe Automation")
        self.geometry("1100x750")
        self.configure(bg=CLR_BG)
        self.resizable(True, True)

        # Hardware objects
        self.bl    = None
        self.motor = None
        self.tc    = None
        self.mfc   = None

        # State
        self.running      = False
        self.stop_flag    = threading.Event()
        self.log_queue    = queue.Queue()
        self.monitor_queue = queue.Queue()
        self.condition_df = pd.DataFrame()
        self._monitor_label = ''
        self._monitor_step = 'Idle'
        self._monitor_dc_points = []
        self._monitor_eis_points = []
        self._monitor_dc_title = 'CA / CP Current Monitor'
        self._monitor_eis_title = 'Impedance Monitor'
        self._monitor_last_current = None
        self._monitor_last_voltage = None
        self._monitor_last_impedance = None
        self._available_ports = []
        self._serial_port_var = {
            'motor': tk.StringVar(value=COM_PORTS['motor']),
            'temp': tk.StringVar(value=COM_PORTS['temp']),
            'mfc': tk.StringVar(value=COM_PORTS['mfc']),
        }
        self._manual_target = {
            'temp': tk.StringVar(value='600'),
            'temp_ramp': tk.StringVar(value='5.0'),
            'x': tk.StringVar(value='0.000'),
            'y': tk.StringVar(value='0.000'),
            'z': tk.StringVar(value='0.000'),
            'gas_a': tk.StringVar(value='0'),
            'gas_b': tk.StringVar(value='0'),
        }
        self._quick_eis = {
            'label': tk.StringVar(value='manual_eis'),
            'v_dc': tk.StringVar(value='0.300'),
            'amp_mv': tk.StringVar(value='10'),
            'f_high': tk.StringVar(value='100000'),
            'f_low': tk.StringVar(value='0.1'),
            'n_pts': tk.StringVar(value='60'),
        }
        self._contact_search = {
            'start_offset': tk.StringVar(value='0.050'),
            'step_mm': tk.StringVar(value='0.005'),
            'max_drop_mm': tk.StringVar(value='0.200'),
            'ocv_threshold': tk.StringVar(value='0.300'),
            'settle_s': tk.StringVar(value='0.30'),
            'engage_mm': tk.StringVar(value='0.010'),
        }
        self._full_auto = {
            'temperatures': tk.StringVar(value='600, 550, 500'),
            'gas_pairs': tk.StringVar(value='10:30; 30:10'),
            'voltages': tk.StringVar(value='0.0, 0.1, 0.2'),
            'electrode_start': tk.StringVar(value='1'),
            'electrode_end': tk.StringVar(value='8'),
            'x1': tk.StringVar(value='0.000'),
            'y1': tk.StringVar(value='0.000'),
            'xn': tk.StringVar(value='7.000'),
            'yn': tk.StringVar(value='0.000'),
            'z_seed': tk.StringVar(value='0.000'),
            'dv': tk.StringVar(value='0.03'),
            'hold_time': tk.StringVar(value='60'),
            'post_peis_hold_time': tk.StringVar(value='30'),
            'peis_f_high': tk.StringVar(value='100000'),
            'peis_f_low': tk.StringVar(value='0.1'),
            'peis_n_pts': tk.StringVar(value='60'),
            'ca_duration': tk.StringVar(value='200'),
            'ca_dt': tk.StringVar(value='0.01'),
            'temp_ramp_rate': tk.StringVar(value='5.0'),
            'stable_time': tk.StringVar(value='120'),
            'gas_stable_time': tk.StringVar(value='600'),
        }
        self._full_auto_use = {
            'temperature': tk.BooleanVar(value=True),
            'gas': tk.BooleanVar(value=True),
            'tip': tk.BooleanVar(value=True),
        }
        self._full_auto_entries = {}
        self._full_auto_summary = tk.StringVar(
            value='Semi-auto generator ready'
        )
        self._adaptive_full_auto = {
            'temperatures': tk.StringVar(value='600, 550, 500'),
            'gas_pairs': tk.StringVar(value='10:30; 30:10'),
            'voltages': tk.StringVar(value='0.0, 0.1, 0.2'),
            'electrode_start': tk.StringVar(value='1'),
            'electrode_end': tk.StringVar(value='8'),
            'x1': tk.StringVar(value='0.000'),
            'y1': tk.StringVar(value='0.000'),
            'xn': tk.StringVar(value='7.000'),
            'yn': tk.StringVar(value='0.000'),
            'z_seed': tk.StringVar(value='0.000'),
            'dv': tk.StringVar(value='0.03'),
            'hold_time': tk.StringVar(value='60'),
            'post_peis_hold_time': tk.StringVar(value='30'),
            'peis_f_high': tk.StringVar(value='100000'),
            'peis_f_low': tk.StringVar(value='0.1'),
            'peis_n_pts': tk.StringVar(value='60'),
            'ca_duration': tk.StringVar(value='200'),
            'ca_dt': tk.StringVar(value='0.01'),
            'temp_ramp_rate': tk.StringVar(value='5.0'),
            'stable_time': tk.StringVar(value='120'),
            'gas_stable_time': tk.StringVar(value='600'),
            'normal_eis_floor_hz': tk.StringVar(value='0.01'),
        }
        self._adaptive_full_auto_use = {
            'temperature': tk.BooleanVar(value=True),
            'gas': tk.BooleanVar(value=True),
            'tip': tk.BooleanVar(value=True),
        }
        self._adaptive_full_auto_entries = {}
        self._adaptive_full_auto_summary = tk.StringVar(
            value='Full-auto adaptive planner ready'
        )
        self._manual_current = {
            'temp': tk.StringVar(value='-'),
            'x': tk.StringVar(value='-'),
            'y': tk.StringVar(value='-'),
            'z': tk.StringVar(value='-'),
            'gas_a': tk.StringVar(value='-'),
            'gas_b': tk.StringVar(value='-'),
        }
        self._manual_status_var = tk.StringVar(value='Manual control ready')
        self._manual_ocv_var = tk.StringVar(value='OCV: -')

        self._build_ui()
        self._poll_log()
        self._poll_monitor()
        self._refresh_port_choices()

    # ══════════════════════════════════════════════════════════════════════
    # UI 구성
    # ══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        # ── 상단 헤더 ────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=CLR_HEADER, height=45)
        hdr.pack(fill='x')
        tk.Label(hdr, text="Microprobe Automated Measurement System",
                 bg=CLR_HEADER, fg='white',
                 font=('Segoe UI', 13, 'bold')).pack(side='left', padx=15, pady=10)

        # ── 탭 ───────────────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=True, padx=8, pady=8)

        self.tab_hw   = ttk.Frame(nb)
        self.tab_cond = ttk.Frame(nb)
        self.tab_full = ttk.Frame(nb)
        self.tab_auto = ttk.Frame(nb)
        self.tab_run  = ttk.Frame(nb)
        self.tab_live = ttk.Frame(nb)
        self.tab_manual = ttk.Frame(nb)

        nb.add(self.tab_hw,   text='  Hardware  ')
        nb.add(self.tab_cond, text='  CSV List  ')
        nb.add(self.tab_full, text='  Semi-auto  ')
        nb.add(self.tab_auto, text='  Full-auto  ')
        nb.add(self.tab_run,  text='  Run / Monitor  ')
        nb.add(self.tab_live, text='  Live Monitor  ')
        nb.add(self.tab_manual, text='  Manual Control  ')

        self._build_tab_hardware()
        self._build_tab_conditions()
        self._build_tab_full_auto()
        self._build_tab_adaptive_full_auto()
        self._build_tab_run()
        self._build_tab_live()
        self._build_tab_manual()

    # ── Tab 1: Hardware ────────────────────────────────────────────────
    def _build_tab_hardware(self):
        f = self.tab_hw
        pad = {'padx': 10, 'pady': 6}

        self._hw_status = {}
        self._hw_btn    = {}

        tk.Label(f, text="Hardware Connections",
                 font=('Segoe UI', 11, 'bold'), bg=CLR_BG).grid(
                 row=0, column=0, columnspan=5, sticky='w', **pad)

        headers = ['Device', 'Interface', '', 'Status', '']
        widths  = [18, 22, 10, 18, 12]
        for c, (h, w) in enumerate(zip(headers, widths)):
            tk.Label(f, text=h, font=('Segoe UI', 9, 'bold'),
                     bg=CLR_LGRAY, width=w,
                     relief='flat', anchor='w').grid(row=1, column=c, sticky='ew', padx=4, pady=2)

        # ── BioLogic row (with editable IP + Auto-detect) ──────────────
        tk.Label(f, text='BioLogic SP-200', anchor='w', bg=CLR_BG,
                 font=('Segoe UI', 10)).grid(row=2, column=0, sticky='w', **pad)

        ip_frame = tk.Frame(f, bg=CLR_BG)
        ip_frame.grid(row=2, column=1, sticky='w', padx=10, pady=6)
        tk.Label(ip_frame, text='IP:', bg=CLR_BG,
                 font=('Courier', 9), fg='#555').pack(side='left')
        self._biologic_ip = tk.StringVar(value=BIOLOGIC_IP)
        ip_entry = tk.Entry(ip_frame, textvariable=self._biologic_ip,
                            width=16, font=('Courier', 9))
        ip_entry.pack(side='left', padx=2)

        ttk.Button(f, text='Auto-detect',
                   command=self._autodetect_biologic).grid(row=2, column=2, padx=4, pady=6)

        bl_status = tk.Label(f, text='● Not connected', fg=CLR_RED,
                             bg=CLR_BG, font=('Segoe UI', 10))
        bl_status.grid(row=2, column=3, sticky='w', **pad)
        self._hw_status['biologic'] = bl_status

        bl_btn = ttk.Button(f, text='Connect',
                            command=lambda: self._toggle_connect('biologic'))
        bl_btn.grid(row=2, column=4, **pad)
        self._hw_btn['biologic'] = bl_btn

        # ── Other devices ──────────────────────────────────────────────
        other_devices = [
            (3, 'IMS MDrive Motor',  'motor', 'COM9  9600 bps'),
            (4, 'Watlow EZ-ZONE',    'temp',  'COM3  9600 bps  Modbus'),
            (5, 'Aera MFC (×2)',     'mfc',   'COM5  9600 bps'),
        ]
        self._hw_port_box = {}
        for r, name, key, iface in other_devices:
            tk.Label(f, text=name, anchor='w', bg=CLR_BG,
                     font=('Segoe UI', 10)).grid(row=r, column=0, sticky='w', **pad)
            port_frame = tk.Frame(f, bg=CLR_BG)
            port_frame.grid(row=r, column=1, sticky='w', padx=10, pady=6)
            box = ttk.Combobox(
                port_frame,
                textvariable=self._serial_port_var[key],
                width=12,
                state='readonly'
            )
            box.pack(side='left')
            tk.Label(port_frame, text=iface, anchor='w', bg=CLR_BG,
                     font=('Courier', 9), fg='#555').pack(side='left', padx=(6, 0))
            self._hw_port_box[key] = box

            lbl = tk.Label(f, text='● Not connected', fg=CLR_RED,
                           bg=CLR_BG, font=('Segoe UI', 10))
            lbl.grid(row=r, column=3, sticky='w', **pad)
            self._hw_status[key] = lbl

            btn = ttk.Button(f, text='Connect',
                             command=lambda k=key: self._toggle_connect(k))
            btn.grid(row=r, column=4, **pad)
            self._hw_btn[key] = btn

        # Connect All / Disconnect All
        btn_frame = tk.Frame(f, bg=CLR_BG)
        btn_frame.grid(row=10, column=0, columnspan=5, pady=20, sticky='w', padx=10)
        ttk.Button(btn_frame, text='Connect All',
                   command=self._connect_all).pack(side='left', padx=5)
        ttk.Button(btn_frame, text='Disconnect All',
                   command=self._disconnect_all).pack(side='left', padx=5)
        ttk.Button(btn_frame, text='Refresh COM Ports',
                   command=self._refresh_port_choices).pack(side='left', padx=5)
        ttk.Button(btn_frame, text='Auto Detect Serial',
                   command=self._autodetect_serial_devices).pack(side='left', padx=5)

        # Live readback
        tk.Label(f, text="Live Readback",
                 font=('Segoe UI', 11, 'bold'), bg=CLR_BG).grid(
                 row=11, column=0, columnspan=5, sticky='w', padx=10, pady=(20,4))

        rb_frame = tk.Frame(f, bg=CLR_LGRAY, relief='groove', bd=1)
        rb_frame.grid(row=12, column=0, columnspan=5, sticky='ew', padx=10, pady=4)

        self._rb_temp = self._readback_row(rb_frame, 0, 'Temperature (°C)')
        self._rb_fa   = self._readback_row(rb_frame, 1, 'Gas A flow (sccm)')
        self._rb_fb   = self._readback_row(rb_frame, 2, 'Gas B flow (sccm)')
        self._rb_x    = self._readback_row(rb_frame, 3, 'Motor X (mm)')
        self._rb_y    = self._readback_row(rb_frame, 4, 'Motor Y (mm)')
        self._rb_z    = self._readback_row(rb_frame, 5, 'Motor Z (mm)')

        ttk.Button(f, text='Read now',
                   command=lambda: threading.Thread(
                       target=self._do_readback, daemon=True).start()
                   ).grid(row=13, column=0, padx=10, pady=6, sticky='w')
        self._port_status_var = tk.StringVar(value='Serial ports: not scanned yet')
        tk.Label(f, textvariable=self._port_status_var, bg=CLR_BG,
                 fg='#555', anchor='w', font=('Segoe UI', 9)).grid(
                 row=14, column=0, columnspan=5, sticky='w', padx=10, pady=(0, 8))

    def _readback_row(self, parent, row, label):
        tk.Label(parent, text=label, bg=CLR_LGRAY, width=25,
                 anchor='w', font=('Segoe UI', 10)).grid(
                 row=row, column=0, padx=10, pady=4)
        var = tk.StringVar(value='—')
        tk.Label(parent, textvariable=var, bg=CLR_LGRAY, width=15,
                 anchor='w', font=('Courier', 10)).grid(
                 row=row, column=1, padx=10)
        return var

    # ── Tab 2: Conditions ──────────────────────────────────────────────
    def _build_tab_conditions(self):
        f = self.tab_cond

        btn_row = tk.Frame(f, bg=CLR_BG)
        btn_row.pack(fill='x', padx=8, pady=6)
        ttk.Button(btn_row, text='Load CSV',
                   command=self._load_csv).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Save CSV',
                   command=self._save_csv).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Add row',
                   command=self._add_row).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Delete row',
                   command=self._del_row).pack(side='left', padx=4)
        ttk.Button(btn_row, text='Load template',
                   command=self._load_template).pack(side='left', padx=4)

        # Treeview table
        cols = ['Label', 'Temperature_C', 'RampRate_C_per_min', 'GasA_sccm', 'GasB_sccm',
                'X_mm', 'Y_mm', 'Z_mm',
                'V_dc', 'dV', 'HoldTime_s', 'PostPEIS_HoldTime_s',
                'PEIS_fHigh', 'PEIS_fLow', 'PEIS_nPts',
                'CA_duration_s', 'CA_dt',
                'StableTime_s', 'GasStableTime_s', 'Skip']
        self._tree_cols = cols

        tree_frame = tk.Frame(f)
        tree_frame.pack(fill='both', expand=True, padx=8, pady=4)

        vsb = ttk.Scrollbar(tree_frame, orient='vertical')
        hsb = ttk.Scrollbar(tree_frame, orient='horizontal')
        self.tree = ttk.Treeview(tree_frame, columns=cols, show='headings',
                                 yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)

        for c in cols:
            w = 120 if c == 'Label' else 80
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, minwidth=50, anchor='center')

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.bind('<Double-1>', self._edit_cell)

    def _build_tab_full_auto(self):
        f = self.tab_full

        intro = tk.LabelFrame(f, text='Semi-auto Condition Generator',
                              bg=CLR_BG, padx=10, pady=10)
        intro.pack(fill='x', padx=8, pady=(8, 6))
        header = tk.Frame(intro, bg=CLR_BG)
        header.pack(fill='x')
        tk.Label(
            header,
            text=('Generate a condition table from temperature, gas, voltage, '
                  'and electrode ranges. Generated rows are sent to the '
                  'CSV List table for review before running.'),
            bg=CLR_BG,
            justify='left',
            anchor='w',
            font=('Segoe UI', 10),
        ).pack(side='left', fill='x', expand=True)
        option_box = tk.LabelFrame(header, text='Disable Unused Hardware', bg=CLR_BG, padx=8, pady=6)
        option_box.pack(side='right', padx=(12, 0))
        ttk.Checkbutton(
            option_box, text='Use temperature',
            variable=self._full_auto_use['temperature'],
            command=self._update_full_auto_field_states
        ).grid(row=0, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use gas',
            variable=self._full_auto_use['gas'],
            command=self._update_full_auto_field_states
        ).grid(row=1, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use tip position',
            variable=self._full_auto_use['tip'],
            command=self._update_full_auto_field_states
        ).grid(row=2, column=0, sticky='w', padx=4)

        grid = tk.Frame(f, bg=CLR_BG)
        grid.pack(fill='x', padx=8, pady=4)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        fields = [
            ('Temperatures (C)', 'temperatures', 0, 0),
            ('Gas pairs A:B (sccm)', 'gas_pairs', 0, 2),
            ('Voltages (V)', 'voltages', 1, 0),
            ('Seed Z (mm)', 'z_seed', 1, 2),
            ('Electrode start', 'electrode_start', 2, 0),
            ('Electrode end', 'electrode_end', 2, 2),
            ('Electrode 1 X (mm)', 'x1', 3, 0),
            ('Electrode 1 Y (mm)', 'y1', 3, 2),
            ('Electrode N X (mm)', 'xn', 4, 0),
            ('Electrode N Y (mm)', 'yn', 4, 2),
            ('dV (V)', 'dv', 5, 0),
            ('Pre-PEIS hold (s)', 'hold_time', 5, 2),
            ('Post-PEIS hold (s)', 'post_peis_hold_time', 6, 0),
            ('PEIS f high (Hz)', 'peis_f_high', 6, 2),
            ('PEIS f low (Hz)', 'peis_f_low', 7, 0),
            ('PEIS n pts', 'peis_n_pts', 7, 2),
            ('CA duration (s)', 'ca_duration', 8, 0),
            ('CA dt (s)', 'ca_dt', 8, 2),
            ('Temp ramp rate (C/min)', 'temp_ramp_rate', 9, 0),
            ('Temp stable time (s)', 'stable_time', 9, 2),
            ('Gas stable time (s)', 'gas_stable_time', 10, 0),
        ]
        for label, key, row, col in fields:
            tk.Label(
                grid, text=label, bg=CLR_BG, anchor='w',
                font=('Segoe UI', 10)
            ).grid(row=row, column=col, sticky='w', padx=(0, 8), pady=4)
            entry = tk.Entry(
                grid, textvariable=self._full_auto[key],
                font=('Courier New', 10), width=28
            )
            entry.grid(row=row, column=col + 1, sticky='ew', padx=(0, 18), pady=4)
            self._full_auto_entries[key] = entry

        help_box = tk.LabelFrame(f, text='Input Format', bg=CLR_BG, padx=10, pady=10)
        help_box.pack(fill='x', padx=8, pady=(4, 6))
        help_lines = [
            'Temperatures / Voltages: comma-separated, for example 600, 550, 500',
            'Gas pairs: semicolon-separated A:B pairs, for example 10:30; 30:10',
            'Tip positions: XY are linearly interpolated from electrode 1 to electrode N',
            'Z is still a seed value for now. Automatic OCV-based contact search remains a later step.',
            'Uncheck temperature / gas / tip position above to disable those inputs and generate None values for that hardware step.',
            'Post-PEIS CA uses one CA technique with two sequences: Vdc short hold, then Vdc+dV long CA.',
        ]
        for line in help_lines:
            tk.Label(help_box, text=line, bg=CLR_BG, anchor='w',
                     justify='left', font=('Segoe UI', 10)).pack(fill='x', pady=1)

        btn_row = tk.Frame(f, bg=CLR_BG)
        btn_row.pack(fill='x', padx=8, pady=(2, 6))
        ttk.Button(
            btn_row, text='Generate to CSV List',
            command=self._generate_full_auto_conditions
        ).pack(side='left', padx=4)
        ttk.Button(
            btn_row, text='Append to CSV List',
            command=lambda: self._generate_full_auto_conditions(append=True)
        ).pack(side='left', padx=4)

        tk.Label(
            f, textvariable=self._full_auto_summary, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='sunken'
        ).pack(fill='x', padx=8, pady=(0, 8))
        self._update_full_auto_field_states()

    def _build_tab_adaptive_full_auto(self):
        f = self.tab_auto

        intro = tk.LabelFrame(f, text='Full-auto Adaptive Planner',
                              bg=CLR_BG, padx=10, pady=10)
        intro.pack(fill='x', padx=8, pady=(8, 6))
        header = tk.Frame(intro, bg=CLR_BG)
        header.pack(fill='x')
        tk.Label(
            header,
            text=('Prepare the future fully automatic workflow that will use '
                  'optimized analysis results to choose the next measurement '
                  'parameters. For now, this planner generates the same base '
                  'CSV rows while keeping adaptive settings visible in the UI.'),
            bg=CLR_BG,
            justify='left',
            anchor='w',
            font=('Segoe UI', 10),
        ).pack(side='left', fill='x', expand=True)
        option_box = tk.LabelFrame(header, text='Disable Unused Hardware', bg=CLR_BG, padx=8, pady=6)
        option_box.pack(side='right', padx=(12, 0))
        ttk.Checkbutton(
            option_box, text='Use temperature',
            variable=self._adaptive_full_auto_use['temperature'],
            command=self._update_adaptive_full_auto_field_states
        ).grid(row=0, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use gas',
            variable=self._adaptive_full_auto_use['gas'],
            command=self._update_adaptive_full_auto_field_states
        ).grid(row=1, column=0, sticky='w', padx=4)
        ttk.Checkbutton(
            option_box, text='Use tip position',
            variable=self._adaptive_full_auto_use['tip'],
            command=self._update_adaptive_full_auto_field_states
        ).grid(row=2, column=0, sticky='w', padx=4)

        grid = tk.Frame(f, bg=CLR_BG)
        grid.pack(fill='x', padx=8, pady=4)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        fields = [
            ('Temperatures (C)', 'temperatures', 0, 0),
            ('Gas pairs A:B (sccm)', 'gas_pairs', 0, 2),
            ('Voltages (V)', 'voltages', 1, 0),
            ('Seed Z (mm)', 'z_seed', 1, 2),
            ('Electrode start', 'electrode_start', 2, 0),
            ('Electrode end', 'electrode_end', 2, 2),
            ('Electrode 1 X (mm)', 'x1', 3, 0),
            ('Electrode 1 Y (mm)', 'y1', 3, 2),
            ('Electrode N X (mm)', 'xn', 4, 0),
            ('Electrode N Y (mm)', 'yn', 4, 2),
            ('dV (V)', 'dv', 5, 0),
            ('Pre-PEIS hold (s)', 'hold_time', 5, 2),
            ('Post-PEIS hold (s)', 'post_peis_hold_time', 6, 0),
            ('PEIS f high (Hz)', 'peis_f_high', 6, 2),
            ('PEIS f low (Hz)', 'peis_f_low', 7, 0),
            ('PEIS n pts', 'peis_n_pts', 7, 2),
            ('CA duration (s)', 'ca_duration', 8, 0),
            ('CA dt (s)', 'ca_dt', 8, 2),
            ('Temp ramp rate (C/min)', 'temp_ramp_rate', 9, 0),
            ('Temp stable time (s)', 'stable_time', 9, 2),
            ('Gas stable time (s)', 'gas_stable_time', 10, 0),
            ('Normal EIS below (Hz)', 'normal_eis_floor_hz', 10, 2),
        ]
        for label, key, row, col in fields:
            tk.Label(
                grid, text=label, bg=CLR_BG, anchor='w',
                font=('Segoe UI', 10)
            ).grid(row=row, column=col, sticky='w', padx=(0, 8), pady=4)
            entry = tk.Entry(
                grid, textvariable=self._adaptive_full_auto[key],
                font=('Courier New', 10), width=28
            )
            entry.grid(row=row, column=col + 1, sticky='ew', padx=(0, 18), pady=4)
            self._adaptive_full_auto_entries[key] = entry

        help_box = tk.LabelFrame(f, text='Adaptive Planning Notes', bg=CLR_BG, padx=10, pady=10)
        help_box.pack(fill='x', padx=8, pady=(4, 6))
        help_lines = [
            'This tab is for the future optimized-parameter workflow that will analyze each completed point before deciding the next one.',
            'Normal EIS below (Hz) is not yet exposed anywhere else in the GUI. Internally the adaptive engine currently defaults to 0.01 Hz.',
            'For now, Generate/Append still creates the same conservative base rows in CSV List. The adaptive runtime hookup remains a later implementation step.',
            'Uncheck temperature / gas / tip position above to disable those inputs and generate None values for that hardware step.',
            'Automatic OCV-based contact finding and analysis-driven next-point updates still need to be wired into the live run loop.',
        ]
        for line in help_lines:
            tk.Label(help_box, text=line, bg=CLR_BG, anchor='w',
                     justify='left', font=('Segoe UI', 10)).pack(fill='x', pady=1)

        btn_row = tk.Frame(f, bg=CLR_BG)
        btn_row.pack(fill='x', padx=8, pady=(2, 6))
        ttk.Button(
            btn_row, text='Generate to CSV List',
            command=self._generate_adaptive_full_auto_conditions
        ).pack(side='left', padx=4)
        ttk.Button(
            btn_row, text='Append to CSV List',
            command=lambda: self._generate_adaptive_full_auto_conditions(append=True)
        ).pack(side='left', padx=4)

        tk.Label(
            f, textvariable=self._adaptive_full_auto_summary, bg=CLR_LGRAY,
            anchor='w', font=('Segoe UI', 10), relief='sunken'
        ).pack(fill='x', padx=8, pady=(0, 8))
        self._update_adaptive_full_auto_field_states()

    # ── Tab 3: Run / Monitor ───────────────────────────────────────────
    def _build_tab_run(self):
        f = self.tab_run

        # Controls row
        ctrl = tk.Frame(f, bg=CLR_BG)
        ctrl.pack(fill='x', padx=8, pady=6)

        tk.Label(ctrl, text='Result folder:', bg=CLR_BG).pack(side='left')
        self._result_dir = tk.StringVar(value=os.path.join(os.getcwd(), 'results'))
        tk.Entry(ctrl, textvariable=self._result_dir, width=40).pack(side='left', padx=4)
        ttk.Button(ctrl, text='Browse',
                   command=self._browse_result).pack(side='left', padx=2)

        self._btn_start = ttk.Button(ctrl, text='▶  Start',
                                     command=self._start_run)
        self._btn_start.pack(side='left', padx=(20, 4))
        self._btn_stop = ttk.Button(ctrl, text='■  Stop',
                                    command=self._stop_run, state='disabled')
        self._btn_stop.pack(side='left', padx=4)

        # Progress
        prog_frame = tk.Frame(f, bg=CLR_BG)
        prog_frame.pack(fill='x', padx=8, pady=4)

        tk.Label(prog_frame, text='Progress:', bg=CLR_BG).pack(side='left')
        self._progress_var = tk.DoubleVar()
        self._progress_bar = ttk.Progressbar(prog_frame, variable=self._progress_var,
                                              maximum=100, length=400)
        self._progress_bar.pack(side='left', padx=8)
        self._progress_lbl = tk.Label(prog_frame, text='0 / 0', bg=CLR_BG)
        self._progress_lbl.pack(side='left')

        # Status line
        self._status_var = tk.StringVar(value='Ready')
        tk.Label(f, textvariable=self._status_var, bg=CLR_LGRAY,
                 anchor='w', font=('Segoe UI', 10), relief='sunken').pack(
                 fill='x', padx=8, pady=2)

        # Log text
        log_frame = tk.Frame(f)
        log_frame.pack(fill='both', expand=True, padx=8, pady=4)

        self.log_text = tk.Text(log_frame, bg='#1e1e1e', fg='#d4d4d4',
                                font=('Courier New', 9), wrap='word',
                                state='disabled')
        log_sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        log_sb.pack(side='right', fill='y')

        ttk.Button(f, text='Clear log', command=self._clear_log).pack(
            anchor='e', padx=8, pady=2)

    def _build_tab_live(self):
        f = self.tab_live

        header = tk.Frame(f, bg=CLR_BG)
        header.pack(fill='x', padx=8, pady=8)

        self._monitor_run_var = tk.StringVar(value='Run: idle')
        self._monitor_step_var = tk.StringVar(value='Step: idle')
        self._monitor_dc_var = tk.StringVar(value='Current: -')
        self._monitor_eis_var = tk.StringVar(value='Impedance: -')

        for text_var in (self._monitor_run_var, self._monitor_step_var,
                         self._monitor_dc_var, self._monitor_eis_var):
            tk.Label(
                header, textvariable=text_var, bg=CLR_LGRAY, anchor='w',
                font=('Segoe UI', 10), relief='groove', padx=8, pady=6
            ).pack(fill='x', pady=3)

        charts = tk.Frame(f, bg=CLR_BG)
        charts.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        charts.columnconfigure(0, weight=1)
        charts.columnconfigure(1, weight=1)
        charts.rowconfigure(0, weight=1)

        left = tk.Frame(charts, bg=CLR_BG)
        right = tk.Frame(charts, bg=CLR_BG)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
        right.grid(row=0, column=1, sticky='nsew', padx=(4, 0))
        left.rowconfigure(1, weight=1)
        right.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        tk.Label(left, text='CA / CP Current Monitor',
                 bg=CLR_BG, font=('Segoe UI', 11, 'bold')).grid(
                 row=0, column=0, sticky='w', pady=(0, 4))
        self._dc_canvas = tk.Canvas(left, bg='white', highlightthickness=1,
                                    highlightbackground='#cfd8dc')
        self._dc_canvas.grid(row=1, column=0, sticky='nsew')

        tk.Label(right, text='Impedance Monitor (Nyquist)',
                 bg=CLR_BG, font=('Segoe UI', 11, 'bold')).grid(
                 row=0, column=0, sticky='w', pady=(0, 4))
        self._eis_canvas = tk.Canvas(right, bg='white', highlightthickness=1,
                                     highlightbackground='#cfd8dc')
        self._eis_canvas.grid(row=1, column=0, sticky='nsew')

        for canvas in (self._dc_canvas, self._eis_canvas):
            canvas.bind('<Configure>', lambda _e: self._redraw_monitor())

        ttk.Button(f, text='Reset monitor',
                   command=self._monitor_reset).pack(anchor='e', padx=8, pady=(0, 6))

    def _build_tab_manual(self):
        f = self.tab_manual

        top = tk.Frame(f, bg=CLR_BG)
        top.pack(fill='both', expand=True, padx=8, pady=8)
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        current = tk.LabelFrame(top, text='Current State', bg=CLR_BG, padx=10, pady=10)
        target = tk.LabelFrame(top, text='Target State', bg=CLR_BG, padx=10, pady=10)
        current.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        target.grid(row=0, column=1, sticky='nsew', padx=(6, 0))

        current_fields = [
            ('Temperature (C)', self._manual_current['temp']),
            ('Motor X (mm)', self._manual_current['x']),
            ('Motor Y (mm)', self._manual_current['y']),
            ('Motor Z (mm)', self._manual_current['z']),
            ('Gas A (sccm)', self._manual_current['gas_a']),
            ('Gas B (sccm)', self._manual_current['gas_b']),
        ]
        for row, (label, var) in enumerate(current_fields):
            tk.Label(current, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=row, column=0, sticky='w', pady=4)
            tk.Label(current, textvariable=var, bg=CLR_LGRAY, anchor='w', width=14,
                     font=('Courier New', 10), relief='groove').grid(
                     row=row, column=1, sticky='ew', padx=(10, 0), pady=4)

        target_fields = [
            ('Temperature (C)', 'temp'),
            ('Ramp Rate (C/min)', 'temp_ramp'),
            ('Motor X (mm)', 'x'),
            ('Motor Y (mm)', 'y'),
            ('Motor Z (mm)', 'z'),
            ('Gas A (sccm)', 'gas_a'),
            ('Gas B (sccm)', 'gas_b'),
        ]
        for row, (label, key) in enumerate(target_fields):
            tk.Label(target, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=row, column=0, sticky='w', pady=4)
            tk.Entry(target, textvariable=self._manual_target[key], width=16,
                     font=('Courier New', 10)).grid(
                     row=row, column=1, sticky='ew', padx=(10, 0), pady=4)

        btns = tk.Frame(f, bg=CLR_BG)
        btns.pack(fill='x', padx=8, pady=(0, 8))
        ttk.Button(btns, text='Refresh Current State',
                   command=lambda: self._run_manual_action(self._manual_refresh_state)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Copy Current -> Target',
                   command=self._copy_current_to_target).pack(side='left', padx=4)
        ttk.Button(btns, text='Apply Temperature',
                   command=lambda: self._run_manual_action(self._manual_apply_temperature)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Move Tip',
                   command=lambda: self._run_manual_action(self._manual_move_stage)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Set Gas',
                   command=lambda: self._run_manual_action(self._manual_set_gas)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Apply All',
                   command=lambda: self._run_manual_action(self._manual_apply_all)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Stop Tip',
                   command=lambda: self._run_manual_action(self._manual_stop_stage)
                   ).pack(side='left', padx=4)
        ttk.Button(btns, text='Find Contact Z',
                   command=lambda: self._run_manual_action(self._manual_find_contact_z)
                   ).pack(side='left', padx=4)

        tk.Label(f, textvariable=self._manual_status_var, bg=CLR_LGRAY,
                 anchor='w', font=('Segoe UI', 10), relief='sunken').pack(
                 fill='x', padx=8, pady=(0, 8))
        tk.Label(f, textvariable=self._manual_ocv_var, bg=CLR_BG,
                 anchor='w', font=('Courier New', 10)).pack(
                 fill='x', padx=8, pady=(0, 6))

        quick = tk.LabelFrame(f, text='Quick EIS', bg=CLR_BG, padx=10, pady=10)
        quick.pack(fill='x', padx=8, pady=(0, 8))

        quick_fields = [
            ('Label', 'label'),
            ('Vdc (V)', 'v_dc'),
            ('Amplitude (mV)', 'amp_mv'),
            ('f high (Hz)', 'f_high'),
            ('f low (Hz)', 'f_low'),
            ('Points', 'n_pts'),
        ]
        for row, (label, key) in enumerate(quick_fields):
            col = (row % 3) * 2
            line = row // 3
            tk.Label(quick, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=line, column=col, sticky='w', pady=4, padx=(0, 6))
            tk.Entry(quick, textvariable=self._quick_eis[key], width=16,
                     font=('Courier New', 10)).grid(
                     row=line, column=col + 1, sticky='w', pady=4, padx=(0, 14))

        ttk.Button(quick, text='Run Quick EIS',
                   command=lambda: self._run_manual_action(self._manual_run_quick_eis)
                   ).grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))

        contact = tk.LabelFrame(f, text='Z Contact Search', bg=CLR_BG, padx=10, pady=10)
        contact.pack(fill='x', padx=8, pady=(0, 8))

        contact_fields = [
            ('Start offset (mm)', 'start_offset'),
            ('Step (mm)', 'step_mm'),
            ('Max drop (mm)', 'max_drop_mm'),
            ('OCV threshold (V)', 'ocv_threshold'),
            ('Settle per step (s)', 'settle_s'),
            ('Engage extra (mm)', 'engage_mm'),
        ]
        for row, (label, key) in enumerate(contact_fields):
            col = (row % 3) * 2
            line = row // 3
            tk.Label(contact, text=label, bg=CLR_BG, anchor='w',
                     font=('Segoe UI', 10)).grid(row=line, column=col, sticky='w', pady=4, padx=(0, 6))
            tk.Entry(contact, textvariable=self._contact_search[key], width=12,
                     font=('Courier New', 10)).grid(
                     row=line, column=col + 1, sticky='w', pady=4, padx=(0, 14))

    # ══════════════════════════════════════════════════════════════════════
    # Hardware connect/disconnect
    # ══════════════════════════════════════════════════════════════════════
    def _toggle_connect(self, key):
        obj = {'biologic': self.bl, 'motor': self.motor,
               'temp': self.tc, 'mfc': self.mfc}[key]
        if obj is not None:
            self._do_disconnect(key)
        else:
            self._do_connect(key)

    def _refresh_port_choices(self):
        ports = sorted(p.device for p in serial.tools.list_ports.comports())
        self._available_ports = ports
        for key in ['motor', 'temp', 'mfc']:
            box = getattr(self, '_hw_port_box', {}).get(key)
            current = self._serial_port_var[key].get()
            values = list(ports)
            if current and current not in values:
                values.insert(0, current)
            if box is not None:
                box['values'] = values if values else ['']
            if not current and ports:
                self._serial_port_var[key].set(ports[0])
        if hasattr(self, '_port_status_var'):
            self._port_status_var.set(
                f"Serial ports: {', '.join(ports)}" if ports else 'Serial ports: none detected'
            )

    def _autodetect_serial_devices(self):
        self._refresh_port_choices()
        ports = list(self._available_ports)
        if not ports:
            messagebox.showwarning("Auto detect", "No COM ports are available.")
            return
        self._port_status_var.set('Serial autodetect running...')

        def worker():
            detected = {}
            for port in ports:
                if 'motor' not in detected and self._probe_motor_port(port):
                    detected['motor'] = port
                if 'mfc' not in detected and self._probe_mfc_port(port):
                    detected['mfc'] = port
                if 'temp' not in detected and self._probe_temp_port(port):
                    detected['temp'] = port
            self.after(0, lambda: self._finish_serial_autodetect(detected))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_serial_autodetect(self, detected):
        if detected:
            for key, port in detected.items():
                self._serial_port_var[key].set(port)
            found = ', '.join(f"{key}={port}" for key, port in detected.items())
            self._port_status_var.set(f'Autodetect found: {found}')
            self._log(f"[Auto-detect] Serial devices found: {found}")
        else:
            self._port_status_var.set('Autodetect did not find matching devices')
            self._log("[Auto-detect] No serial devices matched current probes")

    def _probe_motor_port(self, port):
        cls = MODS.get('motor')
        if cls is None:
            return False
        motor = None
        try:
            motor = cls(port=port, timeout=0.25)
            motor.connect()
            pos = motor.get_position('X')
            return not np.isnan(pos)
        except Exception:
            return False
        finally:
            if motor is not None:
                try:
                    motor.disconnect()
                except Exception:
                    pass

    def _probe_mfc_port(self, port):
        cls = MODS.get('mfc')
        if cls is None:
            return False
        mfc = None
        try:
            mfc = cls(port=port, timeout=0.4)
            mfc.connect()
            value = mfc.get_setpoint('A')
            return not np.isnan(value)
        except Exception:
            return False
        finally:
            if mfc is not None:
                try:
                    mfc.disconnect()
                except Exception:
                    pass

    def _probe_temp_port(self, port):
        cls = MODS.get('temp')
        if cls is None:
            return False
        tc = None
        try:
            tc = cls(port=port)
            tc.connect()
            value = tc.get_temperature()
            return np.isfinite(value)
        except Exception:
            return False
        finally:
            if tc is not None:
                try:
                    tc.disconnect()
                except Exception:
                    pass

    def _autodetect_biologic(self):
        """서브넷을 병렬 스캔해서 포트 5000에 응답하는 BioLogic 장비 IP를 찾는다."""
        current_ip = self._biologic_ip.get()
        parts = current_ip.rsplit('.', 1)
        subnet = parts[0] + '.' if len(parts) == 2 else '192.168.1.'

        self._hw_status['biologic'].config(text='● Scanning...', fg=CLR_ORANGE)
        self.update_idletasks()

        def probe(ip, result):
            try:
                with socket.create_connection((ip, 5000), timeout=0.5):
                    result.append(ip)
            except OSError:
                pass

        def scan():
            result = []
            threads = []
            for last in range(1, 255):
                ip = f"{subnet}{last}"
                t = threading.Thread(target=probe, args=(ip, result), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=1.0)
            found = result[0] if result else None
            self.after(0, lambda: self._autodetect_done(found))

        threading.Thread(target=scan, daemon=True).start()

    def _autodetect_done(self, ip):
        if ip:
            self._biologic_ip.set(ip)
            self._hw_status['biologic'].config(text=f'● Found: {ip}', fg=CLR_BLUE)
            self._log(f"[Auto-detect] BioLogic found at {ip}")
        else:
            self._hw_status['biologic'].config(text='● Not connected', fg=CLR_RED)
            messagebox.showwarning("Auto-detect", "서브넷에서 BioLogic 장비를 찾지 못했습니다.")

    def _do_connect(self, key):
        cls_map = {'biologic': 'biologic', 'motor': 'motor',
                   'temp': 'temp', 'mfc': 'mfc'}
        cls = MODS.get(cls_map[key])
        if cls is None:
            messagebox.showerror("Import error", f"Module for '{key}' failed to import.")
            return
        try:
            if key == 'biologic':
                obj = cls(ip=self._biologic_ip.get())
            elif key in self._serial_port_var:
                obj = cls(port=self._serial_port_var[key].get())
            else:
                obj = cls()
            obj.connect()
            if   key == 'biologic': self.bl    = obj
            elif key == 'motor':    self.motor  = obj
            elif key == 'temp':     self.tc     = obj
            elif key == 'mfc':      self.mfc    = obj
            self._hw_status[key].config(text='● Connected', fg=CLR_GREEN)
            self._hw_btn[key].config(text='Disconnect')
            port_note = f" on {self._serial_port_var[key].get()}" if key in self._serial_port_var else ''
            self._log(f"[HW] {key} connected{port_note}")
        except Exception as e:
            messagebox.showerror("Connection error", str(e))
            self._log(f"[HW] {key} connect failed: {e}")

    def _do_disconnect(self, key):
        obj = {'biologic': self.bl, 'motor': self.motor,
               'temp': self.tc, 'mfc': self.mfc}[key]
        try:
            obj.disconnect()
        except Exception:
            pass
        if   key == 'biologic': self.bl    = None
        elif key == 'motor':    self.motor  = None
        elif key == 'temp':     self.tc     = None
        elif key == 'mfc':      self.mfc    = None
        self._hw_status[key].config(text='● Not connected', fg=CLR_RED)
        self._hw_btn[key].config(text='Connect')
        self._log(f"[HW] {key} disconnected")

    def _connect_all(self):
        for key in ['biologic', 'motor', 'temp', 'mfc']:
            self._do_connect(key)

    def _disconnect_all(self):
        for key in ['biologic', 'motor', 'temp', 'mfc']:
            obj = {'biologic': self.bl, 'motor': self.motor,
                   'temp': self.tc, 'mfc': self.mfc}[key]
            if obj is not None:
                self._do_disconnect(key)

    def _do_readback(self):
        try:
            if self.tc:
                self._rb_temp.set(f"{self.tc.get_temperature():.1f}")
        except Exception as e:
            self._rb_temp.set(f"ERR: {e}")
        try:
            if self.mfc:
                self._rb_fa.set(f"{self.mfc.get_flow('A'):.2f}")
                self._rb_fb.set(f"{self.mfc.get_flow('B'):.2f}")
        except Exception as e:
            self._rb_fa.set("ERR"); self._rb_fb.set("ERR")
        for ax, var in [('X', self._rb_x), ('Y', self._rb_y), ('Z', self._rb_z)]:
            try:
                if self.motor:
                    var.set(f"{self.motor.get_position(ax):.3f}")
                else:
                    var.set('?')
            except Exception:
                var.set('ERR')

    def _update_manual_current_state(self):
        if self.tc:
            try:
                self._manual_current['temp'].set(f"{self.tc.get_temperature():.1f}")
            except Exception:
                self._manual_current['temp'].set('ERR')
        else:
            self._manual_current['temp'].set('-')

        if self.mfc:
            try:
                self._manual_current['gas_a'].set(f"{self.mfc.get_flow('A'):.2f}")
                self._manual_current['gas_b'].set(f"{self.mfc.get_flow('B'):.2f}")
            except Exception:
                self._manual_current['gas_a'].set('ERR')
                self._manual_current['gas_b'].set('ERR')
        else:
            self._manual_current['gas_a'].set('-')
            self._manual_current['gas_b'].set('-')

        for axis in ['X', 'Y', 'Z']:
            key = axis.lower()
            if self.motor:
                try:
                    self._manual_current[key].set(f"{self.motor.get_position(axis):.3f}")
                except Exception:
                    self._manual_current[key].set('ERR')
            else:
                self._manual_current[key].set('-')

    def _run_manual_action(self, action):
        if self.running:
            messagebox.showwarning("Manual control", "Automatic run is active. Stop it before manual control.")
            return
        def _worker():
            try:
                action()
            except Exception as exc:
                self._manual_status_var.set(f'Manual command failed: {exc}')
                self._log(f"[Manual] command failed: {exc}")
        threading.Thread(target=_worker, daemon=True).start()

    def _copy_current_to_target(self):
        self._update_manual_current_state()
        mapping = [
            ('temp', 'temp'),
            ('x', 'x'),
            ('y', 'y'),
            ('z', 'z'),
            ('gas_a', 'gas_a'),
            ('gas_b', 'gas_b'),
        ]
        for src, dst in mapping:
            value = self._manual_current[src].get()
            if value not in ('-', 'ERR', '?'):
                self._manual_target[dst].set(value)
        self._manual_status_var.set('Copied current state into target settings')

    def _manual_refresh_state(self):
        self._manual_status_var.set('Refreshing current state...')
        self._do_readback()
        self._update_manual_current_state()
        self._manual_update_ocv()
        self._manual_status_var.set('Current state updated')

    def _manual_apply_temperature(self):
        if self.tc is None:
            self._manual_status_var.set('Temperature controller is not connected')
            return
        target = float(self._manual_target['temp'].get())
        ramp_rate = float(self._manual_target['temp_ramp'].get())
        self._manual_status_var.set(
            f'Setting temperature to {target:.1f} C at {ramp_rate:.2f} C/min'
        )
        self.tc.set_ramp_rate(ramp_rate)
        self.tc.set_temperature(target)
        self._manual_refresh_state()
        self._manual_status_var.set(
            f'Temperature setpoint sent: {target:.1f} C at {ramp_rate:.2f} C/min'
        )

    def _manual_move_stage(self):
        if self.motor is None:
            self._manual_status_var.set('Motor controller is not connected')
            return
        self._manual_status_var.set('Moving tip to target position')
        for axis in ['X', 'Y', 'Z']:
            target = float(self._manual_target[axis.lower()].get())
            self.motor.move_abs_wait(axis, target)
        self._manual_refresh_state()
        self._manual_status_var.set('Tip move complete')

    def _manual_set_gas(self):
        if self.mfc is None:
            self._manual_status_var.set('MFC is not connected')
            return
        gas_a = float(self._manual_target['gas_a'].get())
        gas_b = float(self._manual_target['gas_b'].get())
        self._manual_status_var.set(f'Setting gas flows to A={gas_a:.2f}, B={gas_b:.2f} sccm')
        self.mfc.set_flow('A', gas_a)
        self.mfc.set_flow('B', gas_b)
        self._manual_refresh_state()
        self._manual_status_var.set('Gas flow setpoints sent')

    def _manual_apply_all(self):
        self._manual_status_var.set('Applying all manual targets...')
        self._manual_apply_temperature()
        self._manual_set_gas()
        self._manual_move_stage()
        self._manual_status_var.set('All manual targets applied')

    def _manual_stop_stage(self):
        if self.motor is None:
            self._manual_status_var.set('Motor controller is not connected')
            return
        for axis in ['X', 'Y', 'Z']:
            try:
                self.motor.stop(axis)
            except Exception:
                pass
        self._manual_refresh_state()
        self._manual_status_var.set('Tip stop command sent')

    def _manual_run_quick_eis(self):
        if self.bl is None:
            self._manual_status_var.set('BioLogic is not connected')
            return

        label = self._quick_eis['label'].get().strip() or 'manual_eis'
        v_dc = float(self._quick_eis['v_dc'].get())
        amp_mv = float(self._quick_eis['amp_mv'].get())
        f_high = float(self._quick_eis['f_high'].get())
        f_low = float(self._quick_eis['f_low'].get())
        n_pts = int(float(self._quick_eis['n_pts'].get()))

        self._monitor_reset()
        self._queue_monitor_event('row_start', label=label, row_index=1, total=1)
        self._queue_monitor_event('step', label=label, step='manual_peis',
                                  message='Quick EIS running')
        self._manual_status_var.set(f'Running Quick EIS: {label}')

        try:
            eis_data = self.bl.run_peis(
                v_dc=v_dc,
                f_high=f_high,
                f_low=f_low,
                n_pts=n_pts,
                amplitude_mv=amp_mv,
                on_segment=lambda data, _segment: self._queue_monitor_event(
                    'eis_data', label=label, step='manual_peis', data=data
                ),
            )
            # Some easy-biologic versions do not emit streaming callbacks reliably.
            # Always push the final parsed dataset once so Live Monitor is populated.
            self._queue_monitor_event(
                'eis_data', label=label, step='manual_peis_done', data=eis_data
            )
            result_root = self._result_dir.get() if hasattr(self, '_result_dir') else os.path.join(os.getcwd(), 'results')
            manual_result_dir = os.path.join(result_root, 'Manual EIS')
            os.makedirs(manual_result_dir, exist_ok=True)
            ts = time.strftime('%Y%m%d_%H%M%S')
            eis_path = os.path.join(manual_result_dir, f"{label}_PEIS_{ts}.txt")
            np.savetxt(eis_path, eis_data, header='freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm', comments='')
            self._queue_monitor_event('sequence_done', label=label)
            self._queue_monitor_event('row_done', label=label)
            self._manual_status_var.set(
                f'Quick EIS complete: {len(eis_data)} points saved to {os.path.basename(eis_path)}'
            )
            self._log(
                f"[Manual EIS] {label}: Vdc={v_dc:.3f} V, amp={amp_mv:.1f} mV, "
                f"{len(eis_data)} pts, saved={eis_path}"
            )
        except Exception as exc:
            self._queue_monitor_event('error', label=label, message='Quick EIS failed')
            self._manual_status_var.set(f'Quick EIS failed: {exc}')
            self._log(f"[Manual EIS] {label} failed: {exc}")

    def _manual_update_ocv(self):
        if self.bl is None:
            self._manual_ocv_var.set('OCV: -')
            return None
        try:
            ocv = self.bl.get_ocv()
            self._manual_ocv_var.set(f'OCV: {ocv:+.4f} V')
            return ocv
        except Exception as exc:
            self._manual_ocv_var.set(f'OCV: ERR ({exc})')
            return None

    def _manual_find_contact_z(self):
        if self.motor is None:
            self._manual_status_var.set('Motor controller is not connected')
            return
        if self.bl is None:
            self._manual_status_var.set('BioLogic is not connected')
            return

        base_z = float(self._manual_target['z'].get())
        start_offset = float(self._contact_search['start_offset'].get())
        step_mm = float(self._contact_search['step_mm'].get())
        max_drop_mm = float(self._contact_search['max_drop_mm'].get())
        ocv_threshold = float(self._contact_search['ocv_threshold'].get())
        settle_s = float(self._contact_search['settle_s'].get())
        engage_mm = float(self._contact_search['engage_mm'].get())

        start_z = base_z + abs(start_offset)
        self._manual_status_var.set(f'Contact search starting from Z={start_z:.3f} mm')
        self.motor.move_abs_wait('Z', start_z)
        self._manual_target['z'].set(f'{start_z:.3f}')
        self._manual_refresh_state()

        max_steps = max(1, int(round(max_drop_mm / step_mm)))
        found_contact = None

        for idx in range(max_steps + 1):
            z_here = start_z - idx * step_mm
            self.motor.move_abs_wait('Z', z_here)
            time.sleep(settle_s)
            ocv = self._manual_update_ocv()
            self._manual_status_var.set(
                f'Contact search Z={z_here:.3f} mm, OCV={ocv:+.4f} V' if ocv is not None
                else f'Contact search Z={z_here:.3f} mm, OCV unavailable'
            )
            if ocv is not None and abs(ocv) <= ocv_threshold:
                found_contact = z_here
                break

        if found_contact is None:
            self._manual_status_var.set('Contact search did not find a valid OCV threshold')
            self._manual_refresh_state()
            return

        measure_z = found_contact - abs(engage_mm)
        self.motor.move_abs_wait('Z', measure_z)
        self._manual_target['z'].set(f'{measure_z:.3f}')
        self._manual_refresh_state()
        self._manual_status_var.set(
            f'Contact found at Z={found_contact:.3f} mm, measurement Z set to {measure_z:.3f} mm'
        )

    # ══════════════════════════════════════════════════════════════════════
    # Conditions table
    # ══════════════════════════════════════════════════════════════════════
    def _load_csv(self):
        path = filedialog.askopenfilename(filetypes=[('CSV', '*.csv'), ('All', '*.*')])
        if not path:
            return
        try:
            self.condition_df = pd.read_csv(path)
            self._refresh_tree()
            self._log(f"[CSV] Loaded: {path}  ({len(self.condition_df)} rows)")
        except Exception as e:
            messagebox.showerror("Load error", str(e))

    def _load_template(self):
        tpl = os.path.join(os.path.dirname(__file__), 'conditions_template.csv')
        if os.path.exists(tpl):
            self.condition_df = pd.read_csv(tpl)
            self._refresh_tree()
            self._log(f"[CSV] Template loaded ({len(self.condition_df)} rows)")
        else:
            messagebox.showwarning("Not found", "conditions_template.csv not found.")

    def _save_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        self._tree_to_df()
        self.condition_df.to_csv(path, index=False)
        self._log(f"[CSV] Saved: {path}")

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for _, row in self.condition_df.iterrows():
            vals = [str(row.get(c, '')) for c in self._tree_cols]
            self.tree.insert('', 'end', values=vals)

    def _tree_to_df(self):
        rows = []
        for iid in self.tree.get_children():
            vals = self.tree.item(iid, 'values')
            rows.append(dict(zip(self._tree_cols, vals)))
        self.condition_df = pd.DataFrame(rows)

    def _parse_number_list(self, text, cast=float):
        items = []
        for token in str(text).replace('\n', ',').split(','):
            token = token.strip()
            if not token:
                continue
            items.append(cast(token))
        return items

    def _parse_gas_pairs(self, text):
        pairs = []
        raw = str(text).replace('\n', ';')
        for token in raw.split(';'):
            token = token.strip()
            if not token:
                continue
            parts = [p.strip() for p in token.split(':')]
            if len(parts) != 2:
                raise ValueError(f"Invalid gas pair '{token}'. Use A:B format.")
            pairs.append((float(parts[0]), float(parts[1])))
        return pairs

    def _generate_condition_rows_from_config(self, config, use_map):
        use_temp = use_map['temperature'].get()
        use_gas = use_map['gas'].get()
        use_tip = use_map['tip'].get()
        temperatures = (
            self._parse_number_list(config['temperatures'].get(), float)
            if use_temp else [None]
        )
        voltages = self._parse_number_list(config['voltages'].get(), float)
        gas_pairs = self._parse_gas_pairs(config['gas_pairs'].get()) if use_gas else [(None, None)]
        e_start = int(float(config['electrode_start'].get())) if use_tip else 1
        e_end = int(float(config['electrode_end'].get())) if use_tip else 1
        x1 = float(config['x1'].get()) if use_tip else None
        y1 = float(config['y1'].get()) if use_tip else None
        xn = float(config['xn'].get()) if use_tip else None
        yn = float(config['yn'].get()) if use_tip else None
        z_seed = float(config['z_seed'].get()) if use_tip else None
        dv = float(config['dv'].get())
        hold_time = float(config['hold_time'].get())
        post_peis_hold_time = float(config['post_peis_hold_time'].get())
        peis_f_high = float(config['peis_f_high'].get())
        peis_f_low = float(config['peis_f_low'].get())
        peis_n_pts = int(float(config['peis_n_pts'].get()))
        ca_duration = float(config['ca_duration'].get())
        ca_dt = float(config['ca_dt'].get())
        temp_ramp_rate = float(config['temp_ramp_rate'].get()) if use_temp else None
        stable_time = float(config['stable_time'].get()) if use_temp else None
        gas_stable_time = float(config['gas_stable_time'].get()) if use_gas else None

        if not voltages:
            raise ValueError("Voltage list must not be empty.")
        if use_tip and e_end < e_start:
            raise ValueError("Electrode end must be greater than or equal to electrode start.")

        electrode_ids = list(range(e_start, e_end + 1))
        count = len(electrode_ids)
        rows = []
        for temp in temperatures:
            for gas_a, gas_b in gas_pairs:
                for electrode in electrode_ids:
                    if use_tip:
                        frac = 0.0 if count == 1 else (electrode - e_start) / (e_end - e_start)
                        x_pos = x1 + frac * (xn - x1)
                        y_pos = y1 + frac * (yn - y1)
                    else:
                        x_pos = None
                        y_pos = None
                    for v_dc in voltages:
                        label_parts = []
                        if temp is not None:
                            label_parts.append(f"T{temp:g}")
                        if gas_a is not None and gas_b is not None:
                            label_parts.append(f"GA{gas_a:g}_GB{gas_b:g}")
                        if use_tip:
                            label_parts.append(f"E{electrode}")
                        label_parts.append(f"V{v_dc:+.3f}")
                        label = '_'.join(label_parts)
                        rows.append({
                            'Label': label,
                            'Temperature_C': temp if temp is not None else 'None',
                            'RampRate_C_per_min': temp_ramp_rate if temp_ramp_rate is not None else 'None',
                            'GasA_sccm': gas_a if gas_a is not None else 'None',
                            'GasB_sccm': gas_b if gas_b is not None else 'None',
                            'X_mm': round(x_pos, 6) if x_pos is not None else 'None',
                            'Y_mm': round(y_pos, 6) if y_pos is not None else 'None',
                            'Z_mm': z_seed if z_seed is not None else 'None',
                            'V_dc': v_dc,
                            'dV': dv,
                            'HoldTime_s': hold_time,
                            'PostPEIS_HoldTime_s': post_peis_hold_time,
                            'PEIS_fHigh': peis_f_high,
                            'PEIS_fLow': peis_f_low,
                            'PEIS_nPts': peis_n_pts,
                            'CA_duration_s': ca_duration,
                            'CA_dt': ca_dt,
                            'StableTime_s': stable_time if stable_time is not None else 'None',
                            'GasStableTime_s': gas_stable_time if gas_stable_time is not None else 'None',
                            'Skip': 0,
                        })
        return rows, temperatures, gas_pairs, electrode_ids, voltages

    def _generate_full_auto_conditions(self, append=False):
        try:
            use_temp = self._full_auto_use['temperature'].get()
            use_gas = self._full_auto_use['gas'].get()
            use_tip = self._full_auto_use['tip'].get()
            temperatures = (
                self._parse_number_list(self._full_auto['temperatures'].get(), float)
                if use_temp else [None]
            )
            voltages = self._parse_number_list(
                self._full_auto['voltages'].get(), float
            )
            gas_pairs = self._parse_gas_pairs(self._full_auto['gas_pairs'].get()) if use_gas else [(None, None)]
            e_start = int(float(self._full_auto['electrode_start'].get())) if use_tip else 1
            e_end = int(float(self._full_auto['electrode_end'].get())) if use_tip else 1
            x1 = float(self._full_auto['x1'].get()) if use_tip else None
            y1 = float(self._full_auto['y1'].get()) if use_tip else None
            xn = float(self._full_auto['xn'].get()) if use_tip else None
            yn = float(self._full_auto['yn'].get()) if use_tip else None
            z_seed = float(self._full_auto['z_seed'].get()) if use_tip else None
            dv = float(self._full_auto['dv'].get())
            hold_time = float(self._full_auto['hold_time'].get())
            post_peis_hold_time = float(self._full_auto['post_peis_hold_time'].get())
            peis_f_high = float(self._full_auto['peis_f_high'].get())
            peis_f_low = float(self._full_auto['peis_f_low'].get())
            peis_n_pts = int(float(self._full_auto['peis_n_pts'].get()))
            ca_duration = float(self._full_auto['ca_duration'].get())
            ca_dt = float(self._full_auto['ca_dt'].get())
            temp_ramp_rate = float(self._full_auto['temp_ramp_rate'].get()) if use_temp else None
            stable_time = float(self._full_auto['stable_time'].get()) if use_temp else None
            gas_stable_time = float(self._full_auto['gas_stable_time'].get()) if use_gas else None
        except ValueError as exc:
            messagebox.showerror("Semi-auto input error", str(exc))
            return

        if not voltages:
            messagebox.showwarning(
                "Full-auto input",
                "Voltage list must not be empty."
            )
            return
        if use_tip and e_end < e_start:
            messagebox.showwarning(
                "Electrode range",
                "Electrode end must be greater than or equal to electrode start."
            )
            return

        electrode_ids = list(range(e_start, e_end + 1))
        count = len(electrode_ids)
        rows = []
        for temp in temperatures:
            for gas_a, gas_b in gas_pairs:
                for electrode in electrode_ids:
                    if use_tip:
                        frac = 0.0 if count == 1 else (electrode - e_start) / (e_end - e_start)
                        x_pos = x1 + frac * (xn - x1)
                        y_pos = y1 + frac * (yn - y1)
                    else:
                        x_pos = None
                        y_pos = None
                    for v_dc in voltages:
                        label_parts = []
                        if temp is not None:
                            label_parts.append(f"T{temp:g}")
                        if gas_a is not None and gas_b is not None:
                            label_parts.append(f"GA{gas_a:g}_GB{gas_b:g}")
                        if use_tip:
                            label_parts.append(f"E{electrode}")
                        label_parts.append(f"V{v_dc:+.3f}")
                        label = '_'.join(label_parts)
                        rows.append({
                            'Label': label,
                            'Temperature_C': temp if temp is not None else 'None',
                            'RampRate_C_per_min': temp_ramp_rate if temp_ramp_rate is not None else 'None',
                            'GasA_sccm': gas_a if gas_a is not None else 'None',
                            'GasB_sccm': gas_b if gas_b is not None else 'None',
                            'X_mm': round(x_pos, 6) if x_pos is not None else 'None',
                            'Y_mm': round(y_pos, 6) if y_pos is not None else 'None',
                            'Z_mm': z_seed if z_seed is not None else 'None',
                            'V_dc': v_dc,
                            'dV': dv,
                            'HoldTime_s': hold_time,
                            'PostPEIS_HoldTime_s': post_peis_hold_time,
                            'PEIS_fHigh': peis_f_high,
                            'PEIS_fLow': peis_f_low,
                            'PEIS_nPts': peis_n_pts,
                            'CA_duration_s': ca_duration,
                            'CA_dt': ca_dt,
                            'StableTime_s': stable_time if stable_time is not None else 'None',
                            'GasStableTime_s': gas_stable_time if gas_stable_time is not None else 'None',
                            'Skip': 0,
                        })

        new_df = pd.DataFrame(rows, columns=self._tree_cols)
        if append and not self.condition_df.empty:
            self._tree_to_df()
            new_df = pd.concat([self.condition_df, new_df], ignore_index=True)

        self.condition_df = new_df
        self._refresh_tree()
        self._full_auto_summary.set(
            f'Generated {len(rows)} rows from '
            f'{len(temperatures)} temperatures × {len(gas_pairs)} gas pairs × '
            f'{len(electrode_ids)} electrodes × {len(voltages)} voltages'
        )
        self._log(f"[Semi-auto] Generated {len(rows)} rows into CSV List table")

    def _update_full_auto_field_states(self):
        groups = {
            'temperature': ['temperatures', 'temp_ramp_rate', 'stable_time'],
            'gas': ['gas_pairs', 'gas_stable_time'],
            'tip': ['z_seed', 'electrode_start', 'electrode_end', 'x1', 'y1', 'xn', 'yn'],
        }
        for key, fields in groups.items():
            enabled = self._full_auto_use[key].get()
            state = 'normal' if enabled else 'disabled'
            for field in fields:
                entry = self._full_auto_entries.get(field)
                if entry is not None:
                    entry.config(state=state)

    def _generate_adaptive_full_auto_conditions(self, append=False):
        try:
            rows, temperatures, gas_pairs, electrode_ids, voltages = (
                self._generate_condition_rows_from_config(
                    self._adaptive_full_auto,
                    self._adaptive_full_auto_use,
                )
            )
            normal_eis_floor_hz = float(self._adaptive_full_auto['normal_eis_floor_hz'].get())
        except ValueError as exc:
            messagebox.showerror("Full-auto input error", str(exc))
            return

        new_df = pd.DataFrame(rows, columns=self._tree_cols)
        if append and not self.condition_df.empty:
            self._tree_to_df()
            new_df = pd.concat([self.condition_df, new_df], ignore_index=True)

        self.condition_df = new_df
        self._refresh_tree()
        self._adaptive_full_auto_summary.set(
            f"Generated {len(rows)} base rows for future Full-auto use "
            f"(normal EIS below {normal_eis_floor_hz:g} Hz; runtime adaptive hookup not live yet)"
        )
        self._log(
            f"[Full-auto] Generated {len(rows)} base rows into CSV List table "
            f"with planned normal EIS floor {normal_eis_floor_hz:g} Hz "
            f"from {len(temperatures)} temperatures x {len(gas_pairs)} gas pairs x "
            f"{len(electrode_ids)} electrodes x {len(voltages)} voltages"
        )

    def _update_adaptive_full_auto_field_states(self):
        groups = {
            'temperature': ['temperatures', 'temp_ramp_rate', 'stable_time'],
            'gas': ['gas_pairs', 'gas_stable_time'],
            'tip': ['z_seed', 'electrode_start', 'electrode_end', 'x1', 'y1', 'xn', 'yn'],
        }
        for key, fields in groups.items():
            enabled = self._adaptive_full_auto_use[key].get()
            state = 'normal' if enabled else 'disabled'
            for field in fields:
                entry = self._adaptive_full_auto_entries.get(field)
                if entry is not None:
                    entry.config(state=state)

    def _add_row(self):
        defaults = {'Label': 'new_row', 'Temperature_C': 600, 'RampRate_C_per_min': 5.0,
                    'GasA_sccm': 100, 'GasB_sccm': 0,
                    'X_mm': 0, 'Y_mm': 0, 'Z_mm': 0,
                    'V_dc': 0.3, 'dV': 0.03, 'HoldTime_s': 60, 'PostPEIS_HoldTime_s': 30,
                    'PEIS_fHigh': 100000, 'PEIS_fLow': 0.1, 'PEIS_nPts': 60,
                    'CA_duration_s': 200, 'CA_dt': 0.01,
                    'StableTime_s': 120, 'GasStableTime_s': 600, 'Skip': 0}
        vals = [str(defaults.get(c, '')) for c in self._tree_cols]
        self.tree.insert('', 'end', values=vals)

    def _del_row(self):
        sel = self.tree.selection()
        for iid in sel:
            self.tree.delete(iid)

    def _edit_cell(self, event):
        """더블클릭으로 셀 편집."""
        region = self.tree.identify_region(event.x, event.y)
        if region != 'cell':
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        col_idx = int(col_id.replace('#', '')) - 1
        x, y, w, h = self.tree.bbox(row_id, col_id)
        cur_val = self.tree.item(row_id, 'values')[col_idx]

        entry = tk.Entry(self.tree, font=('Segoe UI', 10))
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, cur_val)
        entry.focus()

        def save(e=None):
            new_val = entry.get()
            vals = list(self.tree.item(row_id, 'values'))
            vals[col_idx] = new_val
            self.tree.item(row_id, values=vals)
            entry.destroy()

        entry.bind('<Return>', save)
        entry.bind('<FocusOut>', save)

    # ══════════════════════════════════════════════════════════════════════
    # Run automation
    # ══════════════════════════════════════════════════════════════════════
    def _browse_result(self):
        d = filedialog.askdirectory()
        if d:
            self._result_dir.set(d)

    def _start_run(self):
        self._tree_to_df()
        if self.condition_df.empty:
            messagebox.showwarning("No conditions", "먼저 조건 CSV를 불러오세요.")
            return
        if self.bl is None:
            messagebox.showwarning("Hardware", "BioLogic가 연결되지 않았습니다.")
            return

        self.stop_flag.clear()
        self.running = True
        self._monitor_reset()
        self._btn_start.config(state='disabled')
        self._btn_stop.config(state='normal')

        t = threading.Thread(target=self._run_worker, daemon=True)
        t.start()

    def _stop_run(self):
        self.stop_flag.set()
        self._log("[RUN] Stop requested — will stop after current step.")
        self._status_var.set("Stopping...")

    def _run_worker(self):
        df = self.condition_df.copy()
        if 'Skip' in df.columns:
            df = df[df['Skip'].astype(str) != '1'].reset_index(drop=True)

        total = len(df)
        result_root = self._result_dir.get()
        os.makedirs(result_root, exist_ok=True)
        prev_temp = None
        prev_gas = None
        prev_pos = None
        sequence = MODS.get('sequence')

        for idx, row in df.iterrows():
            if self.stop_flag.is_set():
                self._log("[RUN] Stopped by user.")
                break

            label = str(row.get('Label', f'row{idx+1}'))
            self._log(f"\n{'='*50}")
            self._log(f"[{idx+1}/{total}] {label}")
            self._queue_monitor_event('row_start', label=label, row_index=idx + 1, total=total)
            self._status_var.set(f"Row {idx+1}/{total}: {label}")
            self._progress_var.set((idx / total) * 100)
            self._progress_lbl.config(text=f"{idx+1} / {total}")

            try:
                target_temp = _parse_optional_float(row.get('Temperature_C'), default=None)
                temp_changed = prev_temp != target_temp
                if self.tc and target_temp is not None and temp_changed:
                    ramp_rate = _parse_optional_float(row.get('RampRate_C_per_min', 5.0), default=5.0)
                    self._queue_monitor_event('step', label=label, step='temperature',
                                              message=f'Temperature ramp to {target_temp:.0f} C')
                    self.tc.set_ramp_rate(ramp_rate)
                    self.tc.set_temperature(target_temp)
                    stable_s = _parse_optional_float(row.get('StableTime_s', 120), default=120)
                    self._log(
                        f"  Waiting for temperature stabilization "
                        f"({target_temp:.0f} C, ramp {ramp_rate:.2f} C/min, {stable_s:.0f} s) ..."
                    )
                    self.tc.wait_stable(target_temp, tol=2.0,
                                        stable_time=stable_s,
                                        poll=10)
                    prev_temp = target_temp
                elif self.tc and target_temp is not None:
                    self._log(f"  Temperature unchanged ({target_temp:.0f} C) - skipping stabilization wait")
                elif self.tc:
                    self._log("  Temperature step skipped for this row")

                gas_a = _parse_optional_float(row.get('GasA_sccm'), default=None)
                gas_b = _parse_optional_float(row.get('GasB_sccm'), default=None)
                gas_now = (gas_a, gas_b)
                gas_changed = prev_gas != gas_now
                if self.mfc and any(v is not None for v in gas_now) and gas_changed:
                    self._queue_monitor_event('step', label=label, step='gas',
                                              message='Gas flow stabilization')
                    if gas_a is not None:
                        self.mfc.set_flow('A', gas_a)
                    if gas_b is not None:
                        self.mfc.set_flow('B', gas_b)
                    gas_wait = _parse_optional_float(row.get('GasStableTime_s', 600), default=600)
                    if gas_wait > 0:
                        self._log(f"  Waiting for gas stabilization ({gas_wait:.0f} s) ...")
                        time.sleep(gas_wait)
                    prev_gas = gas_now
                elif self.mfc and any(v is not None for v in gas_now):
                    self._log("  Gas conditions unchanged - skipping gas stabilization")
                elif self.mfc:
                    self._log("  Gas step skipped for this row")

                pos_now = tuple(
                    _parse_optional_float(row.get(f'{ax}_mm'), default=None)
                    for ax in ['X', 'Y', 'Z']
                )
                pos_changed = prev_pos != pos_now
                if self.motor and any(v is not None for v in pos_now) and pos_changed:
                    self._queue_monitor_event('step', label=label, step='tip_position',
                                              message='Tip moving')
                    for ax in ['X', 'Y', 'Z']:
                        col = f'{ax}_mm'
                        target = _parse_optional_float(row.get(col), default=None)
                        if target is not None:
                            self.motor.move_abs_wait(ax, target)
                    prev_pos = pos_now
                elif self.motor and any(v is not None for v in pos_now):
                    self._log("  Tip position unchanged - skipping move")
                elif self.motor:
                    self._log("  Tip move skipped for this row")

                if sequence and self.bl:
                    save_dir = result_root
                    sequence(
                        biologic    = self.bl,
                        v_dc        = float(row['V_dc']),
                        dv          = float(row.get('dV', 0.03)),
                        hold_time   = float(row.get('HoldTime_s', 60)),
                        post_peis_hold_time = float(row.get('PostPEIS_HoldTime_s', 30)),
                        peis_f_high = float(row.get('PEIS_fHigh', 1e5)),
                        peis_f_low  = float(row.get('PEIS_fLow',  0.1)),
                        peis_npts   = int(row.get('PEIS_nPts', 60)),
                        ca_duration = float(row.get('CA_duration_s', 200)),
                        ca_dt       = float(row.get('CA_dt', 0.01)),
                        save_dir    = save_dir,
                        label       = label,
                        monitor_callback = self._queue_monitor_event,
                    )
                    self._log("  Measurement completed")
                    self._queue_monitor_event('row_done', label=label)

            except Exception:
                self._log(f"  [ERROR] {traceback.format_exc()}")
                self._queue_monitor_event('error', label=label, message='Measurement step failed')

        self._progress_var.set(100)
        self._progress_lbl.config(text=f"{total} / {total}")
        self._status_var.set("Done")
        self._log("\nRun completed.")
        self.running = False
        self._btn_start.config(state='normal')
        self._btn_stop.config(state='disabled')
        self._queue_monitor_event('run_done')

    def _queue_monitor_event(self, event, **payload):
        self.monitor_queue.put((event, payload))

    def _monitor_reset(self):
        self._monitor_label = ''
        self._monitor_step = 'Idle'
        self._monitor_dc_points = []
        self._monitor_eis_points = []
        self._monitor_dc_title = 'CA / CP Current Monitor'
        self._monitor_eis_title = 'Impedance Monitor'
        self._monitor_last_current = None
        self._monitor_last_voltage = None
        self._monitor_last_impedance = None
        if hasattr(self, '_monitor_run_var'):
            self._monitor_run_var.set('Run: idle')
            self._monitor_step_var.set('Step: idle')
            self._monitor_dc_var.set('Current: -')
            self._monitor_eis_var.set('Impedance: -')
        self._redraw_monitor()

    def _poll_monitor(self):
        try:
            while True:
                event, payload = self.monitor_queue.get_nowait()
                self._apply_monitor_event(event, payload)
        except queue.Empty:
            pass
        self.after(150, self._poll_monitor)

    def _apply_monitor_event(self, event, payload):
        label = payload.get('label')
        if label:
            self._monitor_label = label

        if event == 'row_start':
            self._monitor_dc_points = []
            self._monitor_eis_points = []
            self._monitor_step = 'Preparing row'
            self._monitor_run_var.set(
                f"Run: {payload.get('row_index', '?')} / {payload.get('total', '?')}  |  {self._monitor_label}"
            )
        elif event == 'step':
            self._monitor_step = payload.get('message', payload.get('step', 'Running'))
        elif event == 'dc_data':
            data = payload.get('data')
            if isinstance(data, np.ndarray) and data.size:
                self._monitor_dc_title = 'CA / CP Current Monitor'
                self._monitor_dc_points.extend((float(row[0]), float(row[2])) for row in data)
                self._monitor_last_voltage = float(data[-1, 1])
                self._monitor_last_current = float(data[-1, 2])
                self._monitor_step = payload.get('step', self._monitor_step)
        elif event == 'eis_data':
            data = payload.get('data')
            if isinstance(data, np.ndarray) and data.size:
                self._monitor_eis_title = 'Impedance Monitor (Nyquist)'
                self._monitor_eis_points.extend((float(row[1]), float(row[2])) for row in data)
                self._monitor_last_impedance = (
                    float(data[-1, 1]), float(data[-1, 2]), float(data[-1, 0])
                )
                self._monitor_step = payload.get('step', self._monitor_step)
        elif event == 'row_done':
            self._monitor_step = 'Row complete'
        elif event == 'sequence_done':
            self._monitor_step = 'Measurement sequence complete'
        elif event == 'error':
            self._monitor_step = payload.get('message', 'Error')
        elif event == 'run_done':
            self._monitor_step = 'Run complete'

        self._refresh_monitor_labels()
        self._redraw_monitor()

    def _refresh_monitor_labels(self):
        run_label = self._monitor_label or 'idle'
        self._monitor_run_var.set(f"Run: {run_label}")
        self._monitor_step_var.set(f"Step: {self._monitor_step}")
        if self._monitor_last_current is None:
            self._monitor_dc_var.set('Current: -')
        else:
            volts = self._monitor_last_voltage if self._monitor_last_voltage is not None else float('nan')
            self._monitor_dc_var.set(
                f"Current: {self._monitor_last_current:.4e} A at {volts:.4f} V"
            )
        if self._monitor_last_impedance is None:
            self._monitor_eis_var.set('Impedance: -')
        else:
            rez, imz, freq = self._monitor_last_impedance
            self._monitor_eis_var.set(
                f"Impedance: Re={rez:.4g} Ohm, -Im={imz:.4g} Ohm at {freq:.4g} Hz"
            )

    def _redraw_monitor(self):
        if hasattr(self, '_dc_canvas'):
            self._draw_line_plot(
                self._dc_canvas,
                self._monitor_dc_points,
                title=self._monitor_dc_title,
                x_label='Time (s)',
                y_label='Current (A)',
                line_color=CLR_BLUE,
            )
        if hasattr(self, '_eis_canvas'):
            self._draw_line_plot(
                self._eis_canvas,
                self._monitor_eis_points,
                title=self._monitor_eis_title,
                x_label='Re(Z) (Ohm)',
                y_label='-Im(Z) (Ohm)',
                line_color=CLR_GOLD,
            )

    def _draw_line_plot(self, canvas, points, title, x_label, y_label, line_color):
        canvas.delete('all')
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 240)
        pad_l, pad_r, pad_t, pad_b = 58, 18, 24, 42
        x0, y0 = pad_l, height - pad_b
        x1, y1 = width - pad_r, pad_t

        canvas.create_rectangle(x0, y1, x1, y0, outline='#b0bec5')
        canvas.create_text(x0, 8, text=title, anchor='nw', fill='#37474f',
                           font=('Segoe UI', 10, 'bold'))
        canvas.create_text((x0 + x1) / 2, height - 14, text=x_label,
                           fill='#546e7a', font=('Segoe UI', 9))
        canvas.create_text(14, (y0 + y1) / 2, text=y_label,
                           fill='#546e7a', font=('Segoe UI', 9), angle=90)

        if len(points) < 2:
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                               text='Waiting for live data...',
                               fill='#90a4ae', font=('Segoe UI', 10))
            return

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        if min_x == max_x:
            min_x -= 1.0
            max_x += 1.0
        if min_y == max_y:
            delta = abs(min_y) * 0.1 if min_y else 1.0
            min_y -= delta
            max_y += delta

        x_span = max_x - min_x
        y_span = max_y - min_y
        x_pad = x_span * 0.05
        y_pad = y_span * 0.08
        min_x -= x_pad
        max_x += x_pad
        min_y -= y_pad
        max_y += y_pad

        def map_x(val):
            return x0 + (val - min_x) / (max_x - min_x) * (x1 - x0)

        def map_y(val):
            return y0 - (val - min_y) / (max_y - min_y) * (y0 - y1)

        for frac in (0.0, 0.5, 1.0):
            gx = x0 + frac * (x1 - x0)
            gy = y0 - frac * (y0 - y1)
            canvas.create_line(gx, y1, gx, y0, fill='#eceff1')
            canvas.create_line(x0, gy, x1, gy, fill='#eceff1')

        canvas.create_text(x0, y0 + 14, text=f"{min_x:.3g}", anchor='w',
                           fill='#607d8b', font=('Courier New', 8))
        canvas.create_text(x1, y0 + 14, text=f"{max_x:.3g}", anchor='e',
                           fill='#607d8b', font=('Courier New', 8))
        canvas.create_text(x0 - 6, y0, text=f"{min_y:.3g}", anchor='e',
                           fill='#607d8b', font=('Courier New', 8))
        canvas.create_text(x0 - 6, y1, text=f"{max_y:.3g}", anchor='e',
                           fill='#607d8b', font=('Courier New', 8))

        coords = []
        for px, py in points[-400:]:
            coords.extend((map_x(px), map_y(py)))
        canvas.create_line(*coords, fill=line_color, width=2, smooth=False)
        canvas.create_oval(coords[-2] - 3, coords[-1] - 3,
                           coords[-2] + 3, coords[-1] + 3,
                           fill=line_color, outline=line_color)

    # ══════════════════════════════════════════════════════════════════════
    # Log
    # ══════════════════════════════════════════════════════════════════════
    def _log(self, msg: str):
        ts  = time.strftime('%H:%M:%S')
        self.log_queue.put(f"[{ts}] {msg}\n")

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state='normal')
                self.log_text.insert('end', msg)
                self.log_text.see('end')
                self.log_text.config(state='disabled')
        except queue.Empty:
            pass
        self.after(200, self._poll_log)

    def _clear_log(self):
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

    def on_close(self):
        if self.running:
            if not messagebox.askyesno("종료", "측정 중입니다. 강제 종료하시겠습니까?"):
                return
            self.stop_flag.set()
        try:
            self._disconnect_all()
        finally:
            try:
                self.quit()
            except Exception:
                pass
            try:
                self.destroy()
            except Exception:
                pass
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)


if __name__ == '__main__':
    app = MicroprobGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
