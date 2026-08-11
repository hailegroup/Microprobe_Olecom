# -*- coding: utf-8 -*-
"""
Microprobe Automated Measurement System
========================================
Reads a condition CSV and runs overnight measurements:
  - For each row: set T → set gas flows → move stage → Rapid EIS sequence

CSV columns (all required):
  Temperature_C  : target temperature
  GasA_sccm      : Gas A flow (sccm)
  GasB_sccm      : Gas B flow (sccm)
  X_mm, Y_mm, Z_mm : stage position (mm)
  V_dc           : DC bias voltage (V)
  dV             : perturbation amplitude (V)
  HoldTime_s     : CA hold duration before PEIS (s)
  PEIS_fHigh     : PEIS upper frequency (Hz)
  PEIS_fLow      : PEIS lower frequency (Hz)
  PEIS_nPts      : number of PEIS frequency points
  CA_duration_s  : CA perturbation duration (s)
  StableTime_s   : seconds to hold at temperature before measuring (s)
  Label          : string label for file naming
  Skip           : 0/1 — skip this row if 1

Usage
-----
  python run_automation.py conditions.csv [--result-dir ./results]
"""

import os
import sys
import time
import argparse
import traceback

import numpy as np
import pandas as pd

from driver_biologic import BioLogicController
from driver_motor    import MDriveMotor
from driver_temp     import WatlowController
from driver_mfc      import AeraMFC
from measurement_sequence import rapid_eis_sequence


def move_stage(motor, row, tol_mm=0.01):
    """Move all three axes to position specified in CSV row."""
    for ax in ['X', 'Y', 'Z']:
        col = f'{ax}_mm'
        if col in row and not pd.isna(row[col]):
            target = float(row[col])
            current = motor.get_position(ax)
            if abs(current - target) > tol_mm:
                print(f"  Moving {ax}: {current:.3f} → {target:.3f} mm")
                motor.move_abs_wait(ax, target)
            else:
                print(f"  {ax} already at {current:.3f} mm (within {tol_mm} mm)")


def set_gas(mfc, row):
    """Set gas flows from CSV row."""
    for ch, col in [('A', 'GasA_sccm'), ('B', 'GasB_sccm')]:
        if col in row and not pd.isna(row[col]):
            mfc.set_flow(ch, float(row[col]))


def main():
    parser = argparse.ArgumentParser(description='Microprobe Automated Measurement')
    parser.add_argument('csv', help='Path to conditions CSV file')
    parser.add_argument('--result-dir', default='./results', help='Output directory for raw data')
    args = parser.parse_args()

    # Load conditions
    df = pd.read_csv(args.csv)
    print(f"Loaded {len(df)} condition row(s) from {args.csv}")

    # Skip flagged rows
    if 'Skip' in df.columns:
        df = df[df['Skip'] != 1].reset_index(drop=True)
        print(f"  {len(df)} row(s) after Skip filter")

    result_root = args.result_dir
    os.makedirs(result_root, exist_ok=True)

    # Connect all hardware
    print("\nConnecting hardware ...")
    bl    = BioLogicController()
    motor = MDriveMotor()
    tc    = WatlowController()
    mfc   = AeraMFC()

    bl.connect()
    motor.connect()
    tc.connect()
    mfc.connect()
    print("All hardware connected.\n")

    results = []

    try:
        prev_temp = None

        for idx, row in df.iterrows():
            label = str(row.get('Label', f'row{idx+1}'))
            print(f"\n{'#'*60}")
            print(f"Row {idx+1}/{len(df)}: {label}")
            print(f"{'#'*60}")

            # ── Temperature ─────────────────────────────────────────────
            target_temp = float(row['Temperature_C'])
            stable_time = float(row.get('StableTime_s', 120))
            if prev_temp != target_temp:
                tc.set_temperature(target_temp)
                tc.wait_stable(target_temp, tol=2.0, stable_time=stable_time)
                prev_temp = target_temp
            else:
                print(f"  Temperature already at {target_temp:.0f}°C — skipping ramp")

            # ── Gas flows ────────────────────────────────────────────────
            set_gas(mfc, row)
            time.sleep(30)   # allow flows to stabilize

            # ── Stage position ───────────────────────────────────────────
            move_stage(motor, row)

            # ── Rapid EIS measurement ────────────────────────────────────
            save_dir = os.path.join(result_root, label)
            eis_data, ca_data = rapid_eis_sequence(
                biologic    = bl,
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

            results.append({
                'Label':       label,
                'Temperature': target_temp,
                'GasA_sccm':  float(row.get('GasA_sccm', float('nan'))),
                'GasB_sccm':  float(row.get('GasB_sccm', float('nan'))),
                'V_dc':       float(row['V_dc']),
                'Status':     'OK',
            })
            print(f"  Row {idx+1} done.")

    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
    except Exception:
        print(f"\n[!] Error in row {idx+1}:")
        traceback.print_exc()
        if results:
            results[-1]['Status'] = 'ERROR'
    finally:
        # Save summary
        if results:
            df_res = pd.DataFrame(results)
            summary_path = os.path.join(result_root, 'measurement_log.csv')
            df_res.to_csv(summary_path, index=False)
            print(f"\nMeasurement log saved: {summary_path}")

        # Disconnect
        try:
            bl.disconnect()
            motor.disconnect()
            tc.disconnect()
            mfc.disconnect()
        except Exception:
            pass
        print("Done.")


if __name__ == '__main__':
    main()
