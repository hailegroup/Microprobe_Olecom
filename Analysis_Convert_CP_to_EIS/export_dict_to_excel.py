# -*- coding: utf-8 -*-
"""
Created on Tue Mar 17 13:28:07 2026

@author: luvbl (Generated Using Microsoft CoPilot)
"""

import os
import pandas as pd
import numpy as np
from tkinter import Tk, filedialog


def _is_figure_like(obj):
    return hasattr(obj, "savefig")

def export_to_excel(summary_dict, raw_dict, figures, save_path=None, img_dir=None, total_arc_data=None):
    """
    Exports:
    - Sheet 1: Figures (6 figures arranged 2x3)
    - Sheet 2: Summary (dictionary)
    - Sheet 3: Raw Data (dictionary)

    save_path : full path for the .xlsx file. If None, asks via dialog.
    img_dir   : directory to save temporary figure PNGs. If None, uses current dir.
    """

    if save_path is None:
        # Hide the Tkinter root window and ask user
        root = Tk()
        root.withdraw()
        save_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            title="Save Excel File As"
        )
        if not save_path:
            print("Export cancelled.")
            return

    file_path = save_path

    # Create Excel writer
    with pd.ExcelWriter(file_path, engine="xlsxwriter") as writer:
        
        workbook = writer.book

        # ---------------------------------------------------------
        # 1. FIGURES SHEET
        # ---------------------------------------------------------
        worksheet_fig = workbook.add_worksheet("Figures")
        writer.sheets["Figures"] = worksheet_fig
        
        if figures:
            row_height = 30   # vertical spacing
            col_width = 16     # horizontal spacing

            valid_figures = [fig for fig in figures if _is_figure_like(fig)]

            for i, fig in enumerate(valid_figures):
                if img_dir:
                    img_path = os.path.join(img_dir, f"figure_{i+1}.png")
                else:
                    img_path = f"figure_{i+1}.png"
                fig.savefig(img_path, dpi=150, bbox_inches="tight")
        
                row = (i //2) * row_height
                col = (i % 2) * col_width
        
                worksheet_fig.insert_image(row, col, img_path)

        # --- Helper function to write a dictionary to a sheet ---
        def write_dict_to_sheet(data_dict, sheet_name):
            col_pointer = 0  # track next empty column

            for key, value in data_dict.items():

                # Convert value to DataFrame safely
                if isinstance(value, (int, float, complex, str)):
                    # Wrap scalars or strings into a 1-row DataFrame
                    df = pd.DataFrame([value])
                
                elif isinstance(value, dict):
                    # Convert dict to a single-row DataFrame
                    df = pd.DataFrame([value])
                
                else:
                    # Assume it's array-like (list, np.array, DataFrame, Series)
                    df = pd.DataFrame(value)


                # Transpose if more columns than rows
                if df.shape[1] > df.shape[0]:
                    df = df.T
                
                # Set column headers to the dictionary key
                df.columns = [key] * df.shape[1]

                # Write header
                df.to_excel(
                    writer,
                    sheet_name=sheet_name,
                    startrow=0,
                    startcol=col_pointer,
                    header=True,
                    index=False
                )
                
                # After writing df.to_excel(...)
                worksheet = writer.sheets[sheet_name]
                
                for j, col_name in enumerate(df.columns):
                    # Get the Excel column index
                    col_idx = col_pointer + j
                
                    # Compute max width: header vs. data
                    max_len = max(
                        len(str(col_name)),
                        *(len(str(x)) for x in df[col_name].values)
                    )
                
                    # Add a little padding
                    worksheet.set_column(col_idx, col_idx, max_len)


                # Move pointer to next empty column
                col_pointer += df.shape[1]

        # Write both dictionaries
        write_dict_to_sheet(summary_dict, "Summary")
        write_dict_to_sheet(raw_dict, "Raw Data")

        # ---------------------------------------------------------
        # 4. TOTAL ARC DATA SHEET (PEIS format)
        # ---------------------------------------------------------
        if total_arc_data is not None:
            # total_arc_data: dict with keys 'ref' and 'recovered'
            # each is a dict: {'freq': ..., 'ReZ': ..., 'ImZ': ...}
            ref       = total_arc_data.get('ref')
            recovered = total_arc_data.get('recovered')

            rows = []
            # Reference EIS (full arc)
            if ref is not None:
                for f, re, im in zip(ref['freq'], ref['ReZ'], ref['ImZ']):
                    rows.append({'Source': 'Reference PEIS',
                                 'freq/Hz': f,
                                 'Re(Z)/Ohm': re,
                                 'Im(Z)/Ohm': im})
            # Recovered from CP
            if recovered is not None:
                for f, re, im in zip(recovered['freq'], recovered['ReZ'], recovered['ImZ']):
                    rows.append({'Source': 'Recovered from CP',
                                 'freq/Hz': f,
                                 'Re(Z)/Ohm': re,
                                 'Im(Z)/Ohm': im})

            df_arc = pd.DataFrame(rows, columns=['Source', 'freq/Hz', 'Re(Z)/Ohm', 'Im(Z)/Ohm'])
            df_arc = df_arc.sort_values('freq/Hz', ascending=False).reset_index(drop=True)
            df_arc.to_excel(writer, sheet_name="Total Arc Data", index=False)

            # Auto-width columns
            ws_arc = writer.sheets["Total Arc Data"]
            for col_idx, col_name in enumerate(df_arc.columns):
                max_len = max(len(col_name),
                              df_arc[col_name].astype(str).map(len).max())
                ws_arc.set_column(col_idx, col_idx, max_len + 2)

    print(f"Excel file saved to: {file_path}")
