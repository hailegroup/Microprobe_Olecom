# Microprobe Python

Control GUI and automation scripts for the scanning microprobe electrochemical
measurement rig (motor stage, Watlow temperature controller, Aera mass flow
controllers, BioLogic potentiostat). See `WIN7_RUNNER_SETUP.md` and
`WIN7_OLECOM_README.md` for hardware/environment details this file doesn't
repeat, and [`docs/architecture/README.md`](docs/architecture/README.md) for
a code-level map of the repository (what each module does and how the
pieces fit together).

## Starting the GUI

### Production (Windows 7 instrument PC)

Double-click one of these in the project folder (or the desktop shortcut,
`Launch_Microprobe_GUI - Shortcut.lnk`):

- **`Launch_Microprobe_GUI.bat`** — normal launch. Closes its window once the
  GUI exits.
- **`Launch_Microprobe_GUI_STAY_OPEN_WIN7.bat`** — same, but keeps the console
  window open after the GUI closes so you can read any error output. Use this
  one when debugging a launch failure.
- **`Launch_Microprobe_GUI.vbs`** — runs `Launch_Microprobe_GUI.bat` silently
  (no console window at all). Use once you trust the launch works cleanly.
- **`START_HERE_WIN7_RUNNER.bat`** — launches the GUI using the
  `.venv_win7` virtual environment created by `INSTALL_WIN7_RUNNER.bat` (see
  first-time setup below). Prefer this if you set up that dedicated
  environment instead of relying on a system/Anaconda Python.

All four ultimately run `python gui.py` from the project folder; they only
differ in how they locate a Python interpreter and whether they keep a
console window open.

### First-time setup on a fresh Windows 7 instrument PC

1. Install Python 3.8.10 (match 32-bit/64-bit to the BioLogic EC-Lab/EC-Lib
   stack on that machine).
2. Copy the whole `Microprobe Python` folder to the instrument PC.
3. Run `INSTALL_WIN7_RUNNER.bat` — creates `.venv_win7` and installs
   `requirements-win7-runner.txt` (hardware acquisition packages only; kept
   deliberately light).
4. Edit `config.py` if COM ports or the BioLogic IP differ on this machine.
5. Run `START_HERE_WIN7_RUNNER.bat`.
6. If BioLogic OLE-COM reports a missing `galvani`/`comtypes` import, run
   `INSTALL_OLECOM_DEPS_CURRENT_PY_WIN7.bat` once, then relaunch the GUI. See
   `WIN7_OLECOM_README.md` for the full OLE-COM connection checklist
   (`RUN_OLECOM_CHECK_STAY_OPEN_WIN7.bat`, EC-Lab must be open first, etc.).
7. Optional: the "Image Monitor" tab (camera/electrode/probe vision features)
   needs `requirements-win7-optional-vision.txt`. The GUI starts fine without
   it — that tab just reports the feature unavailable. Install it only after
   the hardware runner already works. See `IMAGE_MONITOR_GUIDE.md` for how to
   use that tab once it's working.

### Development machine (not the instrument PC)

```
pip install -r requirements.txt
python gui.py
```

This works even without the real hardware or EC-Lab connected — hardware
tabs simply show as disconnected until you press Connect. Camera/vision
dependencies (`opencv-python`, `Pillow`, and `ezdxf` for the DXF-layout
workflow) are already included in `requirements.txt` here, unlike the
intentionally minimal Win7 runner requirements above.

## If the GUI won't launch

- Use the `_STAY_OPEN_` batch file variant so the error message stays on
  screen instead of the window closing immediately.
- Confirm a Python interpreter is actually being found — the `.bat` files
  search `.venv_win7`, Anaconda (`C:\anaconda`), then `PATH`, then a handful
  of common install locations, in that order.
- See `WIN7_RUNNER_SETUP.md` for the hardware bring-up checklist to run once
  the GUI itself opens (Connect each device, verify a tiny motor move, Z
  contact search, MFC readback before enabling MFC writes).
