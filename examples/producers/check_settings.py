"""Produce domain data without constructing a GitHub report or workflow identity."""

import argparse
import json
from pathlib import Path


def main() -> int:
    """Check one setting and write a native producer report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("settings", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    enabled = json.loads(args.settings.read_text()).get("enabled") is True
    report = {
        "reportVersion": 0,
        "status": "passed" if enabled else "failed",
        "metrics": [{"label": "Settings checked", "value": "1"}],
        "findings": [],
    }
    if not enabled:
        report["failureKind"] = "quality"
        report["findings"] = [
            {
                "id": "enabled-setting",
                "severity": "error",
                "message": "The enabled setting must be true.",
                "group": "Configuration",
            }
        ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    return int(not enabled)


if __name__ == "__main__":
    raise SystemExit(main())
