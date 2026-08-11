# Windows 7 Runner Setup

Use this when Codex runs on the newer computer but the motor only works on the old Windows 7 instrument computer.

## Intended workflow

1. Edit and debug code on the newer Codex computer.
2. Package or copy `Microprobe Python` to the Windows 7 computer.
3. Run `START_HERE_WIN7_RUNNER.bat` on the Windows 7 computer.
4. Save results either locally on the Windows 7 computer or into a shared/network folder.
5. Bring results back to the newer computer for analysis/plotting/reporting.

## First-time setup on Windows 7

1. Install Python 3.8.10. Match 32-bit/64-bit to the BioLogic EC-Lab/EC-Lib stack on that computer.
2. Copy this whole `Microprobe Python` folder to the Windows 7 computer.
3. Run `INSTALL_WIN7_RUNNER.bat`.
4. Edit `config.py` if COM ports or BioLogic IP differ on the Windows 7 computer.
5. Run `START_HERE_WIN7_RUNNER.bat`.

## Hardware checks before full automation

1. Connect BioLogic, motor, temperature controller, and MFC from the GUI hardware panel.
2. Motor: use Manual tab `Refresh Current State`, then try a tiny safe move.
3. Z contact: set a safe seed Z above the sample, then use Manual tab `Find Contact Z`.
4. MFC read-only: run `RUN_MFC_READBACK_DIAGNOSTIC_WIN7.bat`.
5. MFC write: keep `MFC_WRITE_ENABLED = False` until readback/channel mapping is verified. Only then enable writes in `config.py`.
6. End-to-end: run one electrode, one voltage, one temperature condition before using a full ladder.

## Notes

- The old computer does not need Codex.
- The Image/OM tab is optional. The GUI now starts even if OpenCV/Pillow/scikit-image are missing.
- If you need the Image/OM tab later, install `requirements-win7-optional-vision.txt` after the hardware runner works.
- Current Z convention in `config.py`: increasing Z moves the tip down / closer to the sample.
- AutoContactZ defaults: start 0.200 mm above seed, step 0.020 mm toward contact, search span 0.400 mm, safety beyond seed 0.200 mm, OCV threshold 0.200 V, confirm candidate contact for 10 s, engage 0.100 mm.
- GUI Semi-auto rows use the fixed CSV Rapid EIS protocol. GUI Full-auto/ADAPT rows use the live seeded protocol: stable pre-CA -> dV scout CA -> FFT LF recommendation -> PEIS, with PEIS overlap floor 0.5 Hz and deepest guard 0.05 Hz.
