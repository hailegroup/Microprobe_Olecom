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

import numpy as np
import pandas as pd
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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
        self.condition_df = pd.DataFrame()

        self._build_ui()
        self._poll_log()

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
        self.tab_run  = ttk.Frame(nb)

        nb.add(self.tab_hw,   text='  Hardware  ')
        nb.add(self.tab_cond, text='  Conditions  ')
        nb.add(self.tab_run,  text='  Run / Monitor  ')

        self._build_tab_hardware()
        self._build_tab_conditions()
        self._build_tab_run()

    # ── Tab 1: Hardware ────────────────────────────────────────────────
    def _build_tab_hardware(self):
        f = self.tab_hw
        pad = {'padx': 10, 'pady': 6}

        devices = [
            ('BioLogic SP-200',   'biologic', 'LAN  192.109.209.127'),
            ('IMS MDrive Motor',  'motor',    'COM9  9600 bps'),
            ('Watlow EZ-ZONE',    'temp',     'COM3  9600 bps  Modbus'),
            ('Aera MFC (×2)',     'mfc',      'COM5  9600 bps'),
        ]

        self._hw_status = {}
        self._hw_btn    = {}

        tk.Label(f, text="Hardware Connections",
                 font=('Segoe UI', 11, 'bold'), bg=CLR_BG).grid(
                 row=0, column=0, columnspan=4, sticky='w', **pad)

        headers = ['Device', 'Interface', 'Status', '']
        for c, h in enumerate(headers):
            tk.Label(f, text=h, font=('Segoe UI', 9, 'bold'),
                     bg=CLR_LGRAY, width=20 if c < 3 else 12,
                     relief='flat', anchor='w').grid(row=1, column=c, sticky='ew', padx=4, pady=2)

        for r, (name, key, iface) in enumerate(devices, start=2):
            tk.Label(f, text=name, anchor='w', bg=CLR_BG,
                     font=('Segoe UI', 10)).grid(row=r, column=0, sticky='w', **pad)
            tk.Label(f, text=iface, anchor='w', bg=CLR_BG,
                     font=('Courier', 9), fg='#555').grid(row=r, column=1, sticky='w', **pad)

            lbl = tk.Label(f, text='● Not connected', fg=CLR_RED,
                           bg=CLR_BG, font=('Segoe UI', 10))
            lbl.grid(row=r, column=2, sticky='w', **pad)
            self._hw_status[key] = lbl

            btn = ttk.Button(f, text='Connect',
                             command=lambda k=key: self._toggle_connect(k))
            btn.grid(row=r, column=3, **pad)
            self._hw_btn[key] = btn

        # Connect All / Disconnect All
        btn_frame = tk.Frame(f, bg=CLR_BG)
        btn_frame.grid(row=10, column=0, columnspan=4, pady=20, sticky='w', padx=10)
        ttk.Button(btn_frame, text='Connect All',
                   command=self._connect_all).pack(side='left', padx=5)
        ttk.Button(btn_frame, text='Disconnect All',
                   command=self._disconnect_all).pack(side='left', padx=5)

        # Live readback
        tk.Label(f, text="Live Readback",
                 font=('Segoe UI', 11, 'bold'), bg=CLR_BG).grid(
                 row=11, column=0, columnspan=4, sticky='w', padx=10, pady=(20,4))

        rb_frame = tk.Frame(f, bg=CLR_LGRAY, relief='groove', bd=1)
        rb_frame.grid(row=12, column=0, columnspan=4, sticky='ew', padx=10, pady=4)

        self._rb_temp = self._readback_row(rb_frame, 0, 'Temperature (°C)')
        self._rb_fa   = self._readback_row(rb_frame, 1, 'Gas A flow (sccm)')
        self._rb_fb   = self._readback_row(rb_frame, 2, 'Gas B flow (sccm)')

        ttk.Button(f, text='Read now', command=self._do_readback).grid(
            row=13, column=0, padx=10, pady=6, sticky='w')

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
        cols = ['Label', 'Temperature_C', 'GasA_sccm', 'GasB_sccm',
                'X_mm', 'Y_mm', 'Z_mm',
                'V_dc', 'dV', 'HoldTime_s',
                'CA_duration_s', 'StableTime_s', 'Skip']
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

    def _do_connect(self, key):
        cls_map = {'biologic': 'biologic', 'motor': 'motor',
                   'temp': 'temp', 'mfc': 'mfc'}
        cls = MODS.get(cls_map[key])
        if cls is None:
            messagebox.showerror("Import error", f"Module for '{key}' failed to import.")
            return
        try:
            obj = cls()
            obj.connect()
            if   key == 'biologic': self.bl    = obj
            elif key == 'motor':    self.motor  = obj
            elif key == 'temp':     self.tc     = obj
            elif key == 'mfc':      self.mfc    = obj
            self._hw_status[key].config(text='● Connected', fg=CLR_GREEN)
            self._hw_btn[key].config(text='Disconnect')
            self._log(f"[HW] {key} connected")
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
            self._rb_fa.set(f"ERR"); self._rb_fb.set(f"ERR")

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

    def _add_row(self):
        defaults = {'Label': 'new_row', 'Temperature_C': 600,
                    'GasA_sccm': 100, 'GasB_sccm': 0,
                    'X_mm': 0, 'Y_mm': 0, 'Z_mm': 0,
                    'V_dc': 0.3, 'dV': 0.03, 'HoldTime_s': 60,
                    'CA_duration_s': 200, 'StableTime_s': 120, 'Skip': 0}
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
        sequence = MODS.get('sequence')

        for idx, row in df.iterrows():
            if self.stop_flag.is_set():
                self._log("[RUN] Stopped by user.")
                break

            label = str(row.get('Label', f'row{idx+1}'))
            self._log(f"\n{'='*50}")
            self._log(f"[{idx+1}/{total}] {label}")
            self._status_var.set(f"Row {idx+1}/{total}: {label}")
            self._progress_var.set((idx / total) * 100)
            self._progress_lbl.config(text=f"{idx+1} / {total}")

            try:
                # 온도
                target_temp = float(row['Temperature_C'])
                if self.tc and prev_temp != target_temp:
                    self.tc.set_temperature(target_temp)
                    stable_s = float(row.get('StableTime_s', 120))
                    self._log(f"  온도 안정화 대기 ({target_temp:.0f}°C) ...")
                    self.tc.wait_stable(target_temp, tol=2.0,
                                        stable_time=stable_s,
                                        poll=10)
                    prev_temp = target_temp

                # 가스
                if self.mfc:
                    self.mfc.set_flow('A', float(row.get('GasA_sccm', 0)))
                    self.mfc.set_flow('B', float(row.get('GasB_sccm', 0)))
                    time.sleep(30)

                # 모터
                if self.motor:
                    for ax in ['X', 'Y', 'Z']:
                        col = f'{ax}_mm'
                        if col in row and str(row[col]).strip() not in ('', 'nan'):
                            self.motor.move_abs_wait(ax, float(row[col]))

                # 측정
                if sequence and self.bl:
                    save_dir = os.path.join(result_root, label)
                    sequence(
                        biologic    = self.bl,
                        v_dc        = float(row['V_dc']),
                        dv          = float(row.get('dV', 0.03)),
                        hold_time   = float(row.get('HoldTime_s', 60)),
                        peis_f_high = float(row.get('PEIS_fHigh', 1e5)),
                        peis_f_low  = float(row.get('PEIS_fLow',  0.1)),
                        peis_npts   = int(row.get('PEIS_nPts', 60)),
                        ca_duration = float(row.get('CA_duration_s', 200)),
                        ca_dt       = float(row.get('CA_dt', 0.01)),
                        save_dir    = save_dir,
                        label       = label,
                    )
                    self._log(f"  ✓ 측정 완료")

            except Exception:
                self._log(f"  [ERROR] {traceback.format_exc()}")

        self._progress_var.set(100)
        self._progress_lbl.config(text=f"{total} / {total}")
        self._status_var.set("Done")
        self._log("\n모든 측정 완료.")
        self.running = False
        self._btn_start.config(state='normal')
        self._btn_stop.config(state='disabled')

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
        self._disconnect_all()
        self.destroy()


if __name__ == '__main__':
    app = MicroprobGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
