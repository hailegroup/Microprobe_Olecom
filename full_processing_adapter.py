# -*- coding: utf-8 -*-
"""
Background full-processing bridge to the copied analysis project.

Unlike `analysis_adapter.py`, this path is allowed to do heavier work such as
Excel export and plotting because it is meant to run behind the live measurement
loop rather than block the next point decision.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional


BASE_DIR = Path(__file__).resolve().parent
ANALYSIS_DIR = BASE_DIR / "Analysis_Convert_CP_to_EIS"


@dataclass
class FullProcessingSettings:
    output_root: Optional[str] = None
    auto_trim: bool = False

    def __post_init__(self):
        if self.output_root is None:
            self.output_root = str(BASE_DIR / "result" / "full_processing")


@dataclass
class FullProcessingResult:
    sample_name: str
    status: str
    excel_path: Optional[str] = None
    fit_summary: Optional[Dict] = None
    output_dir: Optional[str] = None
    processing_latency_s: Optional[float] = None
    notes: Optional[list] = None

    def to_dict(self) -> Dict:
        return asdict(self)


def _load_processing_module():
    os.environ.setdefault("MPLBACKEND", "Agg")
    if str(ANALYSIS_DIR) not in sys.path:
        sys.path.insert(0, str(ANALYSIS_DIR))
    import run_trusted_sample_analysis as analysis_runner  # type: ignore
    return analysis_runner


def run_full_processing(
    dc_path,
    eis_path,
    *,
    sample_name: str,
    settings: Optional[FullProcessingSettings] = None,
) -> FullProcessingResult:
    settings = settings or FullProcessingSettings()
    analysis_runner = _load_processing_module()
    output_root = Path(settings.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    started_at = time.time()
    result = analysis_runner.recover_and_export(
        sample_name=sample_name,
        dc_path=Path(dc_path),
        eis_path=Path(eis_path),
        output_root=output_root,
        auto_trim=settings.auto_trim,
    )
    latency = time.time() - started_at

    fit_summary = result.get("fit_result")
    if fit_summary is not None:
        fit_summary = {str(k): str(v) for k, v in fit_summary.items()}

    return FullProcessingResult(
        sample_name=sample_name,
        status="completed",
        excel_path=str(result.get("excel_path")) if result.get("excel_path") is not None else None,
        fit_summary=fit_summary,
        output_dir=str(output_root / sample_name),
        processing_latency_s=latency,
        notes=[
            "full processing completed in the background",
            "this path may include heavier export and plotting work than the fast recommendation path",
        ],
    )


def write_full_processing_result_json(result: FullProcessingResult, output_path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path
