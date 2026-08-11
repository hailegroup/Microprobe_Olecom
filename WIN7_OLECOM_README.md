# Win7 BioLogic OLE-COM Runner Notes

This folder is configured to use EC-Lab OLE-COM as the production BioLogic backend.

## Important

- Open EC-Lab first and connect the SP-200 channel before using the GUI.
- Do not use easy-biologic in the same instrument power session. If easy-biologic was used, power-cycle the potentiostat before OLE-COM work.
- GUI BioLogic Connect, AutoContact OCV reads, Quick EIS, Quick Rapid EIS, and Full-auto hybrid runs all route through OLE-COM when `BIOLOGIC_BACKEND = 'olecom'` in `config.py`.
- The GUI disables background scalar polling during measurements. Live decisions use only the data produced by the active EC-Lab technique, which avoids the OCV polling scatter we saw in CA curves.

## First checks on the Win7 PC

1. Run `RUN_OLECOM_CHECK_STAY_OPEN_WIN7.bat`.
2. If all ProgIDs fail, right-click `RUN_OLECOM_REGISTER_ADMIN_WIN7.bat` and choose `Run as administrator`.
3. If EC-Lab shows `EC-Lab Express firmware loaded`, power-cycle the BioLogic before retrying OLE-COM.
4. Launch the GUI with `Launch_Microprobe_GUI_STAY_OPEN_WIN7.bat`.

## Measurement policy

- Hybrid runs use one EC-Lab CA technique for pre plus scout, then stop during/after scout once FFT and tail guards clear.
- PEIS LF is selected from the CA FFT result, clamped by config:
  - deepest default: `BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ = 0.05`
  - overlap upper default: `BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ = 0.5`
- PEIS amplitude is tied to `dV`.
- Current range remains Auto unless the OLE MPS writer is intentionally changed.
