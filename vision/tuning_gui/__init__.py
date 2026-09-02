# -*- coding: utf-8 -*-
"""
Interactive parameter tuning windows for the electrode/probe detectors,
originally vendored from the sibling "Image Detection" project
(/Users/merrill/Documents/MIT/Haile Lab/Image Detection/ on this machine)
as OpenCV-window-based (cv2.namedWindow/createTrackbar/waitKey) tools,
then rebuilt here as native Tkinter `tk.Toplevel` windows
(`tuning_gui.CircleTuningWindow`, `probe_gui.ProbeTuningWindow`), launched
in-process by gui.py's "Tune Circle Params" / "Tune Probe Params" buttons.

Why native Tkinter instead of the original cv2 windows: cv2's own GUI
functions need a GUI-capable OpenCV build, but this project deliberately
runs `opencv-python-headless` on the Windows 7 instrument PC (see
requirements-win7-optional-vision.txt) to avoid a real DLL-load-order
conflict with Anaconda's MKL-linked numpy there -- headless wheels have no
GUI backend at all, so cv2.namedWindow/imshow/createTrackbar would fail
with "The function is not implemented" on that machine, at any OpenCV
version. Tkinter needs no such backend, and every actual image-processing
call these windows make (CLAHE, bilateral filter, Canny, HoughCircles,
threshold, findContours, convexHull, convexityDefects) is a plain cv2
`imgproc` function, present in headless builds too -- only display goes
through Tkinter (the same PIL/ImageTk PhotoImage pipeline gui.py's Image
Monitor tab already uses for the live camera view). This also means these
windows run in-process, not as a subprocess: no captured frame needs to be
written to disk just to hand off to a child process (a snapshot is still
saved to vision_calibration/ for record-keeping, but the windows
themselves work from the in-memory frame directly), and there's no
risk of freezing the main Tkinter event loop since these are ordinary
Toplevel windows within it.

Kept as a deliberately separate subpackage rather than folded into this
project's own vision/probe_detector.py and vision/circle_detector.py:
those two modules are this project's own, independently ported/simplified
detection code, actually used by the live GUI/tracking pipeline.
`vision/tuning_gui/probe_detector.py` here is a fuller, separate copy
(including the ROI-based search this project's ported detector doesn't
need, since it always operates on a live-tracked electrode/full frame, not
a user-drawn tuning ROI) -- kept apart so the interactive tuner and the
production detector can't drift into each other under shared code neither
fully owns. `vision/tuning_gui/stored_params.py` reads/writes the
`<image>_params.json` / `<image>_probe_params.json` files these windows
save, in the same format `vision/circle_detector.py::load_hough_params`
and `vision/probe_detector.py::load_probe_params` already consume via the
main tab's "Load Hough/Probe Params" buttons -- unaffected by this
rewrite.
"""
