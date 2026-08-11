# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SampleRole:
    key: str
    display_name: str
    operator_request: str
    hypothesis: str


@dataclass(frozen=True)
class ValidationExperiment:
    key: str
    title: str
    goal: str
    applicable_roles: tuple[str, ...]
    command_hint: str
    expected_outputs: tuple[str, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationSuite:
    normal_sample: SampleRole
    hybrid_sample: SampleRole
    experiments: tuple[ValidationExperiment, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": {
                "normal_candidate": asdict(self.normal_sample),
                "hybrid_candidate": asdict(self.hybrid_sample),
            },
            "experiments": [asdict(exp) for exp in self.experiments],
        }


def build_default_validation_suite() -> ValidationSuite:
    normal = SampleRole(
        key="normal_candidate",
        display_name="Normal candidate sample",
        operator_request="Connect the sample you believe should be well served by normal EIS.",
        hypothesis=(
            "PEIS-only / normal-EIS should be sufficient, and CP-assisted hybrid evidence should add little value."
        ),
    )
    hybrid = SampleRole(
        key="hybrid_candidate",
        display_name="Hybrid candidate sample",
        operator_request="Connect the sample you believe still needs CP-assisted hybrid recovery.",
        hypothesis=(
            "CP-assisted FFT / hybrid logic should remain necessary because PEIS-only / normal-EIS would miss "
            "important low-frequency behavior."
        ),
    )
    experiments = (
        ValidationExperiment(
            key="buffered_peis_probe",
            title="Buffered PEIS mid-run chunk probe",
            goal=(
                "Verify whether true Nyquist / impedance rows arrive before PEIS completes, especially around the "
                "1 Hz region needed for possible early normal decisions."
            ),
            applicable_roles=("normal_candidate", "hybrid_candidate"),
            command_hint=(
                r"C:\Users\mmq8658\Desktop\Microprobe\.venv\Scripts\python.exe "
                r"tools\probe_biologic_buffered_peis.py"
            ),
            expected_outputs=(
                "scalar_live_samples.csv",
                "buffered_chunk_samples.csv",
                "buffered_nyquist_points.csv",
                "buffered_peis_probe_summary.json",
            ),
            notes=(
                "Run this first on both samples so we know whether live partial Nyquist evidence is available at all.",
            ),
        ),
        ValidationExperiment(
            key="live_polling_benchmark",
            title="Scalar live-polling benchmark",
            goal=(
                "Re-check whether scalar polling latency/stalls are acceptable on the current hardware setup before "
                "any stop logic is trusted."
            ),
            applicable_roles=("normal_candidate", "hybrid_candidate"),
            command_hint=(
                r"C:\Users\mmq8658\Desktop\Microprobe\.venv\Scripts\python.exe "
                r"tools\benchmark_biologic_live_polling.py"
            ),
            expected_outputs=(
                "ca_hold_live_samples.csv",
                "peis_live_samples.csv",
                "live_polling_benchmark_summary.json",
            ),
            notes=(
                "This does not prove early stop safety by itself, but it tells us whether the scalar control thread is healthy.",
            ),
        ),
        ValidationExperiment(
            key="stop_behavior_benchmark",
            title="Technique stop latency benchmark",
            goal=(
                "Measure how quickly CA/PEIS actually stop once a stop request is sent, so any future 1 Hz stop rule "
                "has a realistic actuation delay budget."
            ),
            applicable_roles=("normal_candidate", "hybrid_candidate"),
            command_hint=(
                r"C:\Users\mmq8658\Desktop\Microprobe\.venv\Scripts\python.exe "
                r"tools\benchmark_biologic_stop_behavior.py"
            ),
            expected_outputs=(
                "ca_hold_stop_samples.csv",
                "peis_stop_samples.csv",
                "stop_behavior_benchmark_summary.json",
            ),
        ),
        ValidationExperiment(
            key="manual_normal_vs_rapid_pair",
            title="Manual paired normal-vs-rapid comparison",
            goal=(
                "Acquire both normal-EIS and rapid/hybrid-style data on the same connected sample so we can compare "
                "mode judgment, fitting, and optimized-parameter stability without previous-sample priors."
            ),
            applicable_roles=("normal_candidate", "hybrid_candidate"),
            command_hint="GUI Manual Control: Run Quick EIS, then Run Quick Rapid EIS on the same sample/bias.",
            expected_outputs=(
                "manual normal PEIS txt",
                "manual rapid PEIS txt",
                "manual rapid CA txt",
            ),
            notes=(
                "This is the core pair we will use to judge whether a sample is truly normal-friendly or hybrid-needed.",
            ),
        ),
        ValidationExperiment(
            key="adaptive_bias_sweep",
            title="Adaptive bias sweep replay/validation",
            goal=(
                "Exercise current planner logic across multiple biases while saving adaptive summary output that can be "
                "compared against fitting/optimized-parameter decisions."
            ),
            applicable_roles=("normal_candidate", "hybrid_candidate"),
            command_hint=(
                r"C:\Users\mmq8658\Desktop\Microprobe\.venv\Scripts\python.exe "
                r"tools\run_adaptive_bias_sweep.py --help"
            ),
            expected_outputs=(
                "adaptive_runtime_summary.json",
                "per-point raw measurement files",
            ),
        ),
        ValidationExperiment(
            key="dt_sweep_probe",
            title="FFT dt sweep / staged-dt capability check",
            goal=(
                "Check whether smaller dt or staged early-dt acquisition is supported and whether HF FFT behavior "
                "actually improves without intolerable buffering."
            ),
            applicable_roles=("normal_candidate", "hybrid_candidate"),
            command_hint="Planned next live experiment: fixed dt sweep first, then staged-dt only if the hardware API supports it.",
            expected_outputs=(
                "dt_sweep_summary.json",
            ),
            notes=(
                "Blocked until live measurements are available again and until we confirm whether variable dt within one CA/CP program is supported.",
            ),
        ),
    )
    return ValidationSuite(normal_sample=normal, hybrid_sample=hybrid, experiments=experiments)


def render_suite_markdown(suite: ValidationSuite) -> str:
    lines = [
        "# Live Sample Validation Suite",
        "",
        "## What you will need to do",
        f"- {suite.normal_sample.operator_request}",
        f"- {suite.hybrid_sample.operator_request}",
        "- When asked, tell me which connected sample is the normal candidate and which is the hybrid candidate.",
        "",
        "## Planned experiments",
    ]
    for exp in suite.experiments:
        lines.append(f"### {exp.title}")
        lines.append(f"- Key: `{exp.key}`")
        lines.append(f"- Goal: {exp.goal}")
        lines.append(f"- Applies to: {', '.join(exp.applicable_roles)}")
        lines.append(f"- Command / path: `{exp.command_hint}`")
        lines.append("- Expected outputs:")
        for output in exp.expected_outputs:
            lines.append(f"  - `{output}`")
        if exp.notes:
            lines.append("- Notes:")
            for note in exp.notes:
                lines.append(f"  - {note}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_validation_suite(output_dir: Path) -> dict[str, Path]:
    suite = build_default_validation_suite()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "live_sample_validation_suite.json"
    markdown_path = output_dir / "live_sample_validation_suite.md"
    manifest_path.write_text(json.dumps(suite.to_dict(), indent=2), encoding="utf-8")
    markdown_path.write_text(render_suite_markdown(suite), encoding="utf-8")
    return {
        "manifest": manifest_path,
        "markdown": markdown_path,
    }
