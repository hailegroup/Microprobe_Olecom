# -*- coding: utf-8 -*-
"""
Persistent, disk-backed per-electrode Z-height store and pooled Z-parallax
slope, fed by AutoContactZ contact searches (both the GUI's manual/alignment
workflow and run_automation.py's headless rows).

Unlike run_automation.py's contact_z_cache/exact_contact_z_cache (plain
local dicts, discarded when a run ends, keyed by rounded XY position), this
store survives across sessions/runs and is keyed by electrode identity
(layout_index, the same 0-based convention used throughout the DXF-layout
vision code: layout_index = electrode_id - 1, electrode_id from a row's
Label via E<N>).

See vision_stage_mapper.py for the parallax-slope math itself
(ProbeZParallaxCalibration, solve_parallax_slope_from_samples,
correct_pixel_for_z_parallax, parallax_within_trusted_range) and the
XY-bias math (ProbeXYBiasCalibration, solve_xy_bias_from_samples,
correct_pixel_for_xy_bias) -- this module only owns accumulating the raw
inputs to that math and persisting them.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from vision_stage_mapper import (
    ProbeXYBiasCalibration,
    ProbeZParallaxCalibration,
    solve_parallax_slope_from_samples,
    solve_xy_bias_from_samples,
)


@dataclass
class ElectrodeZSeed:
    z_mm: float
    source: str
    updated_at: str
    # The electrode's physical position in the DXF layout's own template
    # coordinate frame (LayoutModel.template[layout_index]) at the moment
    # of this seed, if a layout was loaded -- feeds the Z-surface plane fit
    # below. None for seeds recorded without a layout loaded (or before
    # this field existed); such seeds still work as exact measurements,
    # they just can't contribute to the plane fit.
    xy_mm: Optional[Tuple[float, float]] = None


@dataclass
class ParallaxSample:
    delta_z_mm: float
    delta_pixel_x: float
    delta_pixel_y: float
    recorded_at: str


@dataclass
class XYBiasSample:
    """One AutoContactZ touch's (observed_probe_pixel - electrode_tracked_
    pixel) delta, plus the furnace temperature at the time (None if
    unknown) -- see vision_stage_mapper.py::solve_xy_bias_from_samples for
    how these get fit into a correction, optionally as a function of
    temperature_c."""
    delta_x_px: float
    delta_y_px: float
    recorded_at: str
    temperature_c: Optional[float] = None


@dataclass
class ZPlaneFit:
    """
    A tilted-plane model Z = a*u + b*v + c fit over the DXF layout's own
    template (u, v) coordinates from 3+ electrodes' measured Z seeds.

    Fitting in template space (rather than stage mm) is deliberate: this
    project's layout->pixel and pixel->stage transforms are both affine, and
    an affine map of a true physical tilt-plane is still exactly a plane --
    so a plane fit in template coordinates is equivalent to fitting in real
    stage space, without needing the pixel-stage calibration or DXF
    alignment to already be solved just to build this model.
    """
    a: float
    b: float
    c: float
    n_points: int

    def evaluate(self, xy_mm: Tuple[float, float]) -> float:
        return self.a * float(xy_mm[0]) + self.b * float(xy_mm[1]) + self.c


class ElectrodeZCalibrationStore:
    """
    Bundles everything that accumulates over a sample's working life:
      - electrode_z_seeds: best-known real height per electrode (latest
        contact wins).
      - parallax_samples: every contact search's own probe-tip pixel
        displacement (kept indefinitely; more samples -> a more robust
        pooled slope fit).
      - z_parallax: the slope fit from parallax_samples, refit automatically
        whenever a new sample is added and at least 3 are present.
      - xy_bias_samples: every contact search's own (observed - expected)
        probe-tip pixel delta, tagged with the furnace temperature at the
        time (kept indefinitely, at every temperature ever visited --
        never pruned or reset). See get_xy_bias for how these are fit --
        each discrete temperature setpoint gets its own constant-offset
        fit from only its own samples, rather than one fit pooling every
        temperature together, since the instrument is run at a handful of
        fixed setpoints, not a temperature continuum. Layered on top of,
        and never overwrites, the manually-solved pixel<->stage affine
        calibration itself.
    """

    def __init__(self):
        self.electrode_z_seeds: Dict[int, ElectrodeZSeed] = {}
        self.parallax_samples: List[ParallaxSample] = []
        self.z_parallax: Optional[ProbeZParallaxCalibration] = None
        self.z_plane: Optional[ZPlaneFit] = None
        self.xy_bias_samples: List[XYBiasSample] = []

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get_seed(self, layout_index: int) -> Optional[float]:
        """Exact measurement only -- None if this electrode hasn't itself
        been contact-measured, even if a Z-surface estimate is available."""
        seed = self.electrode_z_seeds.get(int(layout_index))
        return None if seed is None else float(seed.z_mm)

    def get_seed_or_estimate(
        self, layout_index: int, xy_mm: Optional[Tuple[float, float]] = None,
    ) -> Optional[float]:
        """
        This electrode's own exact measurement if it has one; otherwise a
        Z-surface plane estimate at xy_mm if the plane is fit and xy_mm is
        given; otherwise None ("not enough information yet", not an error
        -- callers should fall back to the uncorrected projection).
        """
        exact = self.get_seed(layout_index)
        if exact is not None:
            return exact
        if self.z_plane is not None and xy_mm is not None:
            return float(self.z_plane.evaluate(xy_mm))
        return None

    def get_xy_bias(
        self,
        temperature_c: Optional[float] = None,
        *,
        bucket_tol_c: float = 1.0,
    ) -> Optional['ProbeXYBiasCalibration']:
        """
        Fit the XY-bias correction for temperature_c's own setpoint
        "bucket" -- every recorded sample within bucket_tol_c of
        temperature_c (each sample is tagged with the row's exact target
        temperature, not a fluctuating live PV reading, so repeat visits
        to the same setpoint should already match almost exactly; this
        tolerance only absorbs float-precision noise, not real
        temperature variation).

        Resolution order:
          1. temperature_c's own bucket, if it has >= 3 samples.
          2. Otherwise, whichever OTHER bucket (>= 3 samples) is
             numerically closest to temperature_c -- a same-instrument
             correction from a nearby setpoint is a better starting guess
             than none, e.g. right after a temperature change, before
             this setpoint has its own 3 samples yet.
          3. Otherwise (temperature_c is None, or nothing anywhere is
             bucketed with enough samples yet), every sample pooled
             together regardless of temperature -- this store's original,
             temperature-agnostic behavior.
          4. None if there are fewer than 3 samples in total -- "not
             enough information yet," not an error.

        xy_bias_samples is never pruned or reset (see
        record_xy_bias_sample) -- revisiting a temperature across
        separate runs/sessions keeps strengthening that setpoint's own
        bucket rather than losing history or being diluted by other
        temperatures' data.
        """
        if len(self.xy_bias_samples) < 3:
            return None

        buckets = self._xy_bias_buckets(bucket_tol_c)

        def _fit(samples: List['XYBiasSample']) -> Optional['ProbeXYBiasCalibration']:
            try:
                return solve_xy_bias_from_samples(
                    [(s.delta_x_px, s.delta_y_px) for s in samples]
                )
            except Exception:
                # A degenerate fit must never raise out to the caller --
                # "not enough information yet," not an error.
                return None

        if temperature_c is not None:
            for bucket_temp, bucket_samples in buckets.items():
                if abs(bucket_temp - temperature_c) <= bucket_tol_c and len(bucket_samples) >= 3:
                    fit = _fit(bucket_samples)
                    if fit is not None:
                        return fit
            candidates = sorted(
                (abs(bucket_temp - temperature_c), bucket_temp)
                for bucket_temp, bucket_samples in buckets.items()
                if len(bucket_samples) >= 3
            )
            if candidates:
                fit = _fit(buckets[candidates[0][1]])
                if fit is not None:
                    return fit

        return _fit(self.xy_bias_samples)

    def xy_bias_sample_count_near(self, temperature_c: Optional[float], *, bucket_tol_c: float = 1.0) -> int:
        """How many recorded xy_bias_samples fall in temperature_c's own
        bucket (see get_xy_bias) -- for status-message reporting, e.g.
        "N samples at this temperature so far." 0 if temperature_c is
        None or nothing has been recorded at that setpoint yet."""
        if temperature_c is None:
            return 0
        for bucket_temp, bucket_samples in self._xy_bias_buckets(bucket_tol_c).items():
            if abs(bucket_temp - temperature_c) <= bucket_tol_c:
                return len(bucket_samples)
        return 0

    def _xy_bias_buckets(self, bucket_tol_c: float) -> Dict[float, List['XYBiasSample']]:
        """Group xy_bias_samples with a known temperature_c into buckets
        keyed by (approximately) that temperature -- simple greedy
        clustering: samples within bucket_tol_c of an existing bucket's
        key join it, otherwise they start a new bucket. Samples with
        temperature_c=None are excluded (they only ever feed the
        all-samples fallback in get_xy_bias, not any specific bucket)."""
        buckets: Dict[float, List['XYBiasSample']] = {}
        for sample in self.xy_bias_samples:
            if sample.temperature_c is None:
                continue
            for bucket_temp in buckets:
                if abs(bucket_temp - sample.temperature_c) <= bucket_tol_c:
                    buckets[bucket_temp].append(sample)
                    break
            else:
                buckets[sample.temperature_c] = [sample]
        return buckets

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_contact(
        self,
        layout_index: int,
        z_mm: float,
        *,
        source: str,
        parallax_sample: Optional[Tuple[float, Tuple[float, float], float, Tuple[float, float]]] = None,
        xy_mm: Optional[Tuple[float, float]] = None,
    ) -> bool:
        """
        Update layout_index's Z seed with a fresh contact measurement
        (latest always wins, mirroring run_automation.py's contact_z_cache
        overwrite semantics), and, if a parallax_sample is given, append a
        new parallax sample and attempt a slope refit.

        z_mm is the electrode's final seed height (e.g. find_contact_z's
        post-engage measure_z) and is intentionally independent of
        parallax_sample, which is the raw (z_start, pixel_start, z_end,
        pixel_end) tuple returned by find_contact_z/_execute_contact_z_search
        -- z_end there is the pre-engage contact Z, the actual height at
        which pixel_end was sampled, which is not necessarily equal to
        z_mm (the engage overtravel sits between them). Conflating the two
        would bias every parallax sample by the engage distance.

        xy_mm is this electrode's physical position in the DXF layout's own
        template coordinate frame (LayoutModel.template[layout_index]), if a
        layout is loaded -- feeds the Z-surface plane fit (see ZPlaneFit),
        which lets every OTHER electrode in the layout get a Z estimate too,
        not just ones that have been individually contact-measured.

        Returns True if the parallax slope was (re)fit as a result of this
        call, False otherwise (including when this call only updated the Z
        seed and contributed no parallax sample). The Z-plane refit is
        attempted independently of this return value.
        """
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        self.electrode_z_seeds[int(layout_index)] = ElectrodeZSeed(
            z_mm=float(z_mm), source=str(source), updated_at=now,
            xy_mm=None if xy_mm is None else (float(xy_mm[0]), float(xy_mm[1])),
        )
        self._refit_z_plane()

        if parallax_sample is None:
            return False

        z_start, pixel_start, z_end, pixel_end = parallax_sample
        self.parallax_samples.append(
            ParallaxSample(
                delta_z_mm=float(z_end) - float(z_start),
                delta_pixel_x=float(pixel_end[0]) - float(pixel_start[0]),
                delta_pixel_y=float(pixel_end[1]) - float(pixel_start[1]),
                recorded_at=now,
            )
        )
        return self._refit_parallax()

    def _refit_z_plane(self) -> bool:
        points = [
            (seed.xy_mm[0], seed.xy_mm[1], seed.z_mm)
            for seed in self.electrode_z_seeds.values()
            if seed.xy_mm is not None
        ]
        if len(points) < 3:
            return False
        try:
            design = np.asarray([[u, v, 1.0] for u, v, _z in points], dtype=float)
            heights = np.asarray([z for _u, _v, z in points], dtype=float)
            if np.linalg.matrix_rank(design) < 3:
                # Degenerate (e.g. all points collinear) -- not enough
                # independent geometry yet to pin down a plane. Leave any
                # previously-good fit in place rather than clobber it.
                return False
            coeffs, *_ = np.linalg.lstsq(design, heights, rcond=None)
            self.z_plane = ZPlaneFit(
                a=float(coeffs[0]), b=float(coeffs[1]), c=float(coeffs[2]),
                n_points=len(points),
            )
            return True
        except Exception:
            return False

    def _refit_parallax(self) -> bool:
        if len(self.parallax_samples) < 3:
            return False
        samples = [
            (s.delta_z_mm, s.delta_pixel_x, s.delta_pixel_y)
            for s in self.parallax_samples
        ]
        try:
            self.z_parallax = solve_parallax_slope_from_samples(samples)
            return True
        except Exception:
            # A degenerate/failed refit must never clobber a previously-good
            # fit or block the seed update this call already made.
            return False

    def record_xy_bias_sample(
        self,
        *,
        observed_px: float,
        observed_py: float,
        expected_px: float,
        expected_py: float,
        temperature_c: Optional[float] = None,
        bucket_tol_c: float = 1.0,
    ) -> bool:
        """
        Append one AutoContactZ touch's XY-bias sample (the probe's own
        ROI-detected pixel at the touch vs. the electrode's own tracked
        pixel at that same touch -- same frame, same actual Z, no
        calibration-reference-Z assumption involved). Never overwritten or
        pruned -- see get_xy_bias for how the growing sample history gets
        turned into a correction.

        Returns True if temperature_c's own bucket now has >= 3 samples
        (i.e. get_xy_bias(temperature_c) will use a fit specific to this
        setpoint rather than falling back to the nearest other one), False
        otherwise (including when temperature_c is None).
        """
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        self.xy_bias_samples.append(
            XYBiasSample(
                delta_x_px=float(observed_px) - float(expected_px),
                delta_y_px=float(observed_py) - float(expected_py),
                recorded_at=now,
                temperature_c=None if temperature_c is None else float(temperature_c),
            )
        )
        if temperature_c is None:
            return False
        buckets = self._xy_bias_buckets(bucket_tol_c)
        for bucket_temp, bucket_samples in buckets.items():
            if abs(bucket_temp - temperature_c) <= bucket_tol_c:
                return len(bucket_samples) >= 3
        return False

    def clear(self) -> None:
        """Wipe every seed, sample, and fit back to a fresh store -- the
        caller is responsible for persisting this (via save()) if the
        cleared state should also replace what's on disk."""
        self.electrode_z_seeds = {}
        self.parallax_samples = []
        self.z_parallax = None
        self.z_plane = None
        self.xy_bias_samples = []

    def refit_z_plane(self) -> bool:
        """Public trigger for the same plane refit record_contact() already
        runs automatically -- useful after a manual clear() + reseed, or to
        force-refresh/display the current fit on demand."""
        return self._refit_z_plane()

    # ------------------------------------------------------------------
    # Persistence -- tolerant load, matching this project's calibration
    # loading fallback style everywhere else (missing/corrupt file -> empty
    # store, never raises).
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            'electrode_z_seeds': {
                str(layout_index): {
                    'z_mm': seed.z_mm,
                    'source': seed.source,
                    'updated_at': seed.updated_at,
                    'xy_mm': None if seed.xy_mm is None else [seed.xy_mm[0], seed.xy_mm[1]],
                }
                for layout_index, seed in self.electrode_z_seeds.items()
            },
            'parallax_samples': [
                {
                    'delta_z_mm': s.delta_z_mm,
                    'delta_pixel_x': s.delta_pixel_x,
                    'delta_pixel_y': s.delta_pixel_y,
                    'recorded_at': s.recorded_at,
                }
                for s in self.parallax_samples
            ],
            'xy_bias_samples': [
                {
                    'delta_x_px': s.delta_x_px,
                    'delta_y_px': s.delta_y_px,
                    'recorded_at': s.recorded_at,
                    'temperature_c': s.temperature_c,
                }
                for s in self.xy_bias_samples
            ],
        }

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, payload: dict) -> "ElectrodeZCalibrationStore":
        store = cls()
        for layout_index_str, entry in payload.get('electrode_z_seeds', {}).items():
            xy_mm = entry.get('xy_mm')
            store.electrode_z_seeds[int(layout_index_str)] = ElectrodeZSeed(
                z_mm=float(entry['z_mm']),
                source=str(entry.get('source', 'unknown')),
                updated_at=str(entry.get('updated_at', '')),
                xy_mm=None if not xy_mm else (float(xy_mm[0]), float(xy_mm[1])),
            )
        for entry in payload.get('parallax_samples', []):
            store.parallax_samples.append(
                ParallaxSample(
                    delta_z_mm=float(entry['delta_z_mm']),
                    delta_pixel_x=float(entry['delta_pixel_x']),
                    delta_pixel_y=float(entry['delta_pixel_y']),
                    recorded_at=str(entry.get('recorded_at', '')),
                )
            )
        for entry in payload.get('xy_bias_samples', []):
            temperature_c = entry.get('temperature_c')
            store.xy_bias_samples.append(
                XYBiasSample(
                    delta_x_px=float(entry['delta_x_px']),
                    delta_y_px=float(entry['delta_y_px']),
                    recorded_at=str(entry.get('recorded_at', '')),
                    temperature_c=None if temperature_c is None else float(temperature_c),
                )
            )
        store._refit_parallax()
        store._refit_z_plane()
        # No xy_bias refit needed on load -- get_xy_bias() computes its
        # fit lazily from xy_bias_samples at read time (parameterized by
        # the caller's current temperature), unlike z_parallax/z_plane
        # which are cached, eagerly-refit single values.
        return store

    @classmethod
    def load(cls, path: str) -> "ElectrodeZCalibrationStore":
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
            return cls.from_dict(payload)
        except Exception:
            return cls()
