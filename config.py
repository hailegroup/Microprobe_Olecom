# -*- coding: utf-8 -*-
"""
Hardware configuration for the microprobe scanning system.
Edit this file to match your physical setup.
"""

# ── Serial ports ───────────────────────────────────────────────────────────
COM_PORTS = {
    'motor':    'COM9',    # IMS MDrive stepper motors
    'temp':     'COM3',    # Watlow EZ-ZONE temperature controller (Standard Bus via pywatlow)
    'mfc':      'COM5',    # Aera mass flow controllers (addresses 7 and 8)
}

SERIAL_BAUD = {
    'motor': 9600,
    'temp':  9600,   # Watlow EZ-ZONE: 9600, 8N1
    'mfc':   9600,
}

# ── BioLogic potentiostat ──────────────────────────────────────────────────
BIOLOGIC_IP   = '192.109.209.128'
BIOLOGIC_PORT = 5000   # default easy-biologic port

# BioLogic control backend for the Win7 runner.
#
# IMPORTANT:
#   OLE-COM and easy-biologic should not be mixed in the same instrument session.
#   After easy-biologic talks to the potentiostat, EC-Lab/OLE-COM may require an
#   instrument power-cycle before it can reconnect cleanly.  Keep the production
#   GUI on OLE-COM so Connect, OCV contact checks, quick runs, and full-auto all
#   use the same EC-Lab-controlled path.
#
# Supported values:
#   'olecom'        : Win7 production mode; requires EC-Lab OLE-COM/comtypes.
#   'easybiologic'  : legacy/debug mode only.
BIOLOGIC_BACKEND = 'olecom'
BIOLOGIC_ALLOW_EASY_FALLBACK = False
BIOLOGIC_OLECOM_DEVICE_INDEX = 0
# Win7/EC-Lab can show a visible EC-Lab window without registering an active
# OLE-COM object in the Running Object Table.  The GUI Connect button may create
# one persistent OLE-COM session, but measurement scripts should still avoid
# creating extra hidden EC-Lab instances unless explicitly requested.
BIOLOGIC_OLECOM_CREATE_ECLAB_IF_MISSING = True
BIOLOGIC_OLECOM_TRUST_TEST_CONNECTION = True
BIOLOGIC_OLECOM_DISCONNECT_DEVICE_ON_GUI_DISCONNECT = False

# Default OLE-COM hybrid settings used by GUI quick/full-auto wrappers.
# BIOLOGIC_OLECOM_BANDWIDTH is the PEIS bandwidth; CA bandwidth and I-Range
# are independently configurable per-row from the GUI and fall back to
# these defaults when a row/field doesn't specify one.
BIOLOGIC_OLECOM_BANDWIDTH = 4
BIOLOGIC_OLECOM_CA_BANDWIDTH = 4
BIOLOGIC_OLECOM_CA_I_RANGE = 'Auto'
BIOLOGIC_OLECOM_PEIS_POINTS_PER_DECADE = 10
BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ = 0.1
BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ = 0.5
BIOLOGIC_OLECOM_PRE_MAX_S = 300.0
BIOLOGIC_OLECOM_MIN_FFT_DURATION_S = 100.0
BIOLOGIC_OLECOM_SCOUT_MAX_S = 300.0
BIOLOGIC_OLECOM_SCOUT_TAIL_WINDOW_S = 20.0
BIOLOGIC_OLECOM_SCOUT_TAIL_REL_SHIFT_LIMIT = 0.03
BIOLOGIC_OLECOM_SCOUT_TAIL_REL_SLOPE_LIMIT = 0.03

# ── Motor calibration (steps per mm) ──────────────────────────────────────
STEPS_PER_MM = {
    'X': 40314.96,
    'Y': 48377.95,
    'Z': 48377.95,
}

# Stage-travel safety policy used when moving between sample locations.
# Keep disabled until the real Z sign convention / clearance height is confirmed.
STAGE_SAFE_MOVE = {
    'enabled': True,
    'clearance_z_mm': None,      # absolute safe Z for XY travel; overrides relative lift when set
    'lift_delta_mm': 0.5,        # relative lift if clearance_z_mm is not configured
    'positive_z_is_up': False,   # False: increasing Z moves the tip down / closer to sample
    'settle_s': 0.1,
    # Lead-screw/coupling backlash makes a stepper's commanded step count
    # diverge from true position by an amount that depends on which
    # direction the axis was previously moving. Jogging to (target - this)
    # before every real target move, then always finishing the last leg in
    # the increasing direction, takes up that slack the same way every time
    # regardless of prior motion -- ported from the original LabVIEW
    # program, which did this unconditionally on every X/Y/Z move. Applies
    # to every real target position, not the transient Z travel-clearance
    # hop (that one only needs to clear the sample, not be precise). Set to
    # 0 to disable. X/Y only -- see 'z_backlash_overshoot_mm' below for Z.
    'backlash_overshoot_mm': 0.3,
    # Z's own overshoot, kept separate from X/Y and defaulted to 0 (off):
    # X/Y overshoot legs happen either at the safe travel-clearance height or
    # purely laterally, well clear of the sample either way, but a Z target
    # can be right at (or a hair above) the sample surface -- e.g. driving
    # to a previously-measured electrode contact height. Overshooting there
    # would force every such move to first retract, then finish with a
    # guaranteed >= backlash-overshoot-sized final descent onto the sample,
    # instead of whatever small approach was actually needed. Not worth that
    # risk for a compensation that mainly matters for repeated XY revisits.
    'z_backlash_overshoot_mm': 0.0,
}

# ── Watlow EZ-ZONE ────────────────────────────────────────────────────────
WATLOW_MODBUS_ADDR = 1   # default slave address
WATLOW_PID_LOW  = {'P': 40,  'I': 108, 'D': 18}   # below 400 °C
WATLOW_PID_HIGH = {'P': 24,  'I': 12,  'D': 2}    # above  400 °C
WATLOW_PID_SWITCH_TEMP = 400.0   # °C
TEMP_SAFETY_ENABLED = True
TEMP_SAFETY_ACTIVE_TARGET_C = 150.0
TEMP_SAFETY_MIN_VALID_C = 50.0
TEMP_SAFETY_MAX_DROP_C = 80.0
TEMP_SAFETY_POLL_S = 5.0
TEMP_SAFETY_SHUTDOWN_SETPOINT_C = 25.0

# ── Aera MFC ──────────────────────────────────────────────────────────────
MFC_ADDRESS = {
    'A': 7,   # Gas A
    'B': 8,   # Gas B
}
MFC_FULL_SCALE_SCCM = {
    'A': 100.0,    # ID 007, S/N C090818032
    'B': 1000.0,   # ID 008, S/N J080310023
}

# Safety gate for digital write commands to the Aera MFCs.
# Keep disabled until the command protocol is validated on the real hardware.
MFC_WRITE_ENABLED = True

# The DMFC utility manual says flow setpoint/output display defaults to percent.
# Lab readback also indicates the write setting is a raw 0-100 value, not sccm.
# GUI/CSV conditions therefore use raw DMFC setting values (0-100).
MFC_SETPOINT_COMMAND_UNIT = 'percent'
MFC_MIN_DIGITAL_SETTING_PERCENT = 2.0
MFC_SETPOINT_TOLERANCE_PERCENT = 0.25
MFC_SETPOINT_READBACK_TIMEOUT_S = 4.0
MFC_SETPOINT_READBACK_POLL_S = 0.5
MFC_SETPOINT_WRITE_RETRIES = 1

# Some DMFCs require switching from analog to digital control before serial
# setpoints take effect. Leave disabled until the exact command behavior is
# validated on the old instrument PC, because digital mode can override analog
# flow control from the external system.
# Digital control switch policy before setpoint writes:
#   'once'   : send SRS only once per channel/connection, then only update SFD
#   'always' : send SRS before every SFD write
#   False    : never send SRS automatically
# Re-sending SRS every manual gas edit can make the hardware behave unlike the
# DMFC utility, which normally stays in digital/manual mode and just changes the
# target setpoint.
MFC_SEND_DIGITAL_CONTROL_ON_WRITE = 'once'

# ── Vision / camera (probe & electrode tracking) ───────────────────────────
# Camera device used by both the GUI Image Monitor tab and run_automation.py's
# headless drift-correction. The two must never open the camera at the same
# time (a single cv2.VideoCapture device index cannot generally be shared
# across processes) -- always stop the GUI's Image Monitor camera before
# launching an automation run that has any AutoTrackXY=1 rows.
VISION_CAMERA_BACKEND = 'dshow'   # 'dshow' | 'msmf' | 'any' (mirrors gui.py's Image Monitor backend choices)
VISION_CAMERA_INDEX = 0

# Default file locations for the calibration/layout artifacts produced by the
# GUI's probe-calibration and DXF-layout-alignment workflows, consumed by
# run_automation.py at startup. Relative paths resolve against the working
# directory the script is launched from; use absolute paths for unattended
# overnight runs so the artifacts are found regardless of launch location.
VISION_PIXEL_STAGE_CALIBRATION_PATH = 'vision_calibration/pixel_stage_calibration.json'
VISION_ELECTRODE_LAYOUT_PATH = 'vision_calibration/electrode_layout.json'        # DXF-derived positions (mm)
VISION_ELECTRODE_ALIGNMENT_PATH = 'vision_calibration/electrode_alignment.json'  # affine + last-known pixel positions

# Remembers the Semi-auto/Full-auto condition-generator tabs' field values
# (temperatures, gas pairs, voltages, contact-search/PEIS/CA params, the
# temperature/gas/tip/CA enable toggles, tip-source mode, omit-electrodes
# text) across GUI restarts -- written on close, loaded at startup. Does
# NOT include per-DXF-layout-dependent state (e.g. diameter-exclusion
# checkboxes), which wouldn't make sense to carry over to a different
# layout. See gui.py::_save_condition_generator_settings /
# _load_condition_generator_settings.
GUI_LAST_SESSION_PATH = 'gui_last_session.json'

# Master enable gate for AutoTrackXY in run_automation.py. A CSV row also
# needs its own AutoTrackXY=1 flag; both must be true for anything to move
# the stage from a vision correction. Keep False until the probe-calibration
# + DXF-alignment workflow has been validated on real hardware; mirrors the
# MFC_WRITE_ENABLED / STAGE_SAFE_MOVE['enabled'] convention below.
VISION_AUTO_TRACK_XY_ENABLED = True

# Drift-correction cadence during unattended runs. Electrodes drift
# gradually, so re-detecting every row is unnecessary overhead/risk;
# re-check whichever of these two conditions is met first for a given run.
# Reasoned placeholders, not measured -- easy to retune once real drift-rate
# data exists.
VISION_DRIFT_RECHECK_EVERY_N_ROWS = 5
VISION_DRIFT_RECHECK_EVERY_S = 20 * 60

# Sanity gate on an accepted drift correction. If the newly re-fit position
# for an electrode differs from the row's own CSV X_mm/Y_mm by more than
# this, treat the correction as untrustworthy: log a warning and fall back to
# the CSV value instead of applying it. Conservative default pending real
# drift data.
VISION_DRIFT_MAX_CORRECTION_MM = 0.5

# Minimum number of electrodes that must be confidently re-detected in a
# drift-check cycle (by Hough search in a tight ROI around each electrode's
# last known position) before the global affine re-fit is trusted at all;
# below this, skip the refit and keep using the last-known-good positions.
# RANSAC needs >=3 points to fit meaningfully and has no redundancy to
# reject a bad detection right at 3; the source project's tracker used a
# bare minimum of 2, which is too low for unattended (no-operator) use.
VISION_DRIFT_MIN_CONFIDENT_ELECTRODES = 4

# Fraction of ALL electrodes in the loaded layout that must be confidently
# re-detected before a drift-check refit is trusted -- scales the
# redundancy requirement with layout size instead of staying fixed at
# VISION_DRIFT_MIN_CONFIDENT_ELECTRODES regardless of how many electrodes
# are actually in play. The two combine as
# max(VISION_DRIFT_MIN_CONFIDENT_ELECTRODES, ceil(layout.n * this)), so a
# small layout still gets the flat floor above, while a large layout
# requires proportionally more redundancy.
VISION_DRIFT_MIN_CONFIDENT_ELECTRODES_FRACTION = 0.2

# Minimum fraction of confidently-redetected electrodes that must be RANSAC
# inliers on the refit for the result to be trusted (guards against a refit
# dominated by outliers/false positives).
VISION_DRIFT_MIN_INLIER_FRACTION = 0.6

# Reject a per-electrode redetection if it lands farther than this many
# electrode-radii from the position projected from the *prior* cycle's
# affine fit -- guards against a Hough hit snapping onto a neighboring
# electrode (most likely while the probe arm occludes/confuses the true
# one) and dragging that electrode's tracked position, and the whole
# affine re-fit, toward the wrong spot. Radius-relative so it scales with
# zoom/electrode size rather than a fixed pixel count. UNMEASURED --
# provisional, tune once real drift-event imagery exists.
VISION_DRIFT_MAX_PROJECTED_DEVIATION_RADII = 1.5

# EMA smoothing factor for how much a single new detection can pull an
# electrode's tracked pixel position per drift-check cycle (see
# Circle.update_smoothed, vision/layout_alignment.py). Lower = slower to
# follow a new detection = more resistant to a one-off bad/occluded hit
# yanking the tracked position, at the cost of responding more slowly to
# genuine drift. UNMEASURED -- provisional; halved from the function's own
# un-configured default (0.3) as a first cut at "reduce drift rate".
VISION_DRIFT_EMA_ALPHA = 0.15

# Electrodes whose known stage X lies more than this many mm to the
# "covered" side of the probe tip's current stage X are skipped entirely
# for this cycle's redetection (falls back to the freshly projected
# position instead) -- the probe arm occludes/visually confuses the Hough
# search for electrodes it's currently over. UNMEASURED -- provisional,
# tune to the probe arm's actual physical footprint.
VISION_PROBE_OCCLUSION_X_MARGIN_MM = 0.5

# Probe-tip detector preprocessing (see vision/probe_detector.py::ProbeDetector).
# Higher CLAHE clip than the electrode-detection default (2.0) because the
# probe/background contrast is typically weaker than an electrode disc's.
VISION_PROBE_CLAHE_CLIP = 4.0

# Per-electrode Z-height store + pooled Z-parallax slope, persisted across
# sessions/runs (see vision/electrode_z_seed.py::ElectrodeZCalibrationStore).
# Fed by AutoContactZ contact searches: each search's own OCV-confirmed Z
# seeds that electrode's real height, and the probe-tip pixel displacement
# between search-start and contact (XY held fixed throughout a search, so
# any displacement is attributable to Z alone) is a direct parallax-slope
# measurement, pooled across contact events.
VISION_ELECTRODE_Z_SEED_PATH = 'vision_calibration/electrode_z_seed.json'

# Per-touch log of the parallax/XY-bias corrections actually applied when
# targeting an electrode, plus that touch's own raw XY-bias sample delta
# (the same delta_x_px/delta_y_px that gets folded into electrode_z_seed.json's
# xy_bias_samples) -- lets a run be reviewed afterward to see whether the
# applied correction tracked the real, observed drift over time. See
# gui.py::_image_log_xy_correction_sample.
VISION_XY_CORRECTION_LOG_CSV_PATH = 'vision_calibration/xy_correction_log.csv'

# How far (in mm) an electrode's own known Z may lie from the pixel-stage
# calibration's reference Z before the parallax correction is trusted to
# extrapolate that far; beyond this, skip the correction for that electrode
# and fall back to the uncorrected projection. UNMEASURED -- provisional,
# sized to the rig's known worst-case Z excursion (0.5 mm safe-travel
# clearance lift + up to ~0.6 mm AutoContactZ search band); retune once real
# parallax data exists.
VISION_Z_PARALLAX_MAX_EXTRAPOLATION_MM = 0.5

# Maximum allowed Z spread (in mm) across the pixel-stage probe-tip
# calibration's own reference points. The base calibration is only valid at
# the single Z it was captured at, so all calibration points must share a
# consistent Z; solving is refused if this tolerance is exceeded. UNMEASURED
# -- provisional, pending real data on how much the probe's detected pixel
# actually shifts per mm of Z (see VISION_Z_PARALLAX_MAX_EXTRAPOLATION_MM).
VISION_PROBE_CALIBRATION_Z_TOLERANCE_MM = 0.5

# Minimum pixel displacement a single Z-contact search's start/end probe-
# tip samples must show before the resulting parallax sample is trusted.
# Below this, the two detections more likely both landed on the same
# nearby feature (most likely the electrode itself, given the tight
# electrode-sized search ROI -- see gui.py::_image_probe_tip_pixel_now)
# than represent a real, tiny parallax shift -- treated as a probe-tip
# detection failure for this sample, not a valid (if small) measurement.
# UNMEASURED -- provisional; tune from real numbers via the Image Monitor
# tab's Start/Contact ROI debug panels.
VISION_PARALLAX_MIN_SAMPLE_PIXEL_DELTA_PX = 0

# Maximum plausible probe-tip-detection-to-electrode-center pixel distance
# for an XY-bias sample (see gui.py::_image_recalibrate_xy_bias_from_contact)
# -- a fixed pixel count, not scaled by the electrode's own tracked radius.
# Exists because at a true confirmed contact the probe tip and electrode
# center should coincide almost exactly, so a detection that far off is
# far more likely a false detection (e.g. ProbeDetector locking onto a
# reflection -- a known limitation, see
# project_probe_detector_reflection_limitation.md) than a genuine offset.
# None disables rejection (accept every detection). UNMEASURED --
# provisional; tune once the XY-bias status messages/log show how far
# genuine detections actually land.
VISION_XY_BIAS_MAX_SAMPLE_PIXEL_DELTA_PX = 20.0

# Temperature tolerance (deg C) for grouping XY-bias samples into the same
# "setpoint bucket" in ElectrodeZCalibrationStore.get_xy_bias. Each sample
# is tagged with the row's exact target temperature
# (gui.py::_active_temperature_target_c), not a fluctuating live PV
# reading, so this is deliberately wider than float-precision noise --
# lets nearby setpoints across different CSVs/runs (e.g. 100 vs 105) share
# a bucket instead of needing an exact match.
XY_BIAS_TEMPERATURE_BUCKET_TOL_C = 10.0
