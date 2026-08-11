from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from live_sample_validation import write_validation_suite  # noqa: E402


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"live_sample_validation_suite_{timestamp}"
    paths = write_validation_suite(out_dir)
    summary = {
        "timestamp": timestamp,
        "output_dir": str(out_dir),
        "manifest_path": str(paths["manifest"]),
        "markdown_path": str(paths["markdown"]),
    }
    summary_path = out_dir / "live_sample_validation_suite_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
