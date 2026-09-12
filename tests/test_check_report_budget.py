import json

from scripts.check_report import combine

from quality_graph_core.adapters import adapt_native
from tests.test_adapters import context


def test_omission_counts_survive_a_full_notes_budget() -> None:
    report = combine(
        [
            (
                "in-process",
                {
                    "status": "failed",
                    "failureKind": "quality",
                    "diagnostics": [
                        {"kind": "command", "message": f"test {index}", "detail": "trace"}
                        for index in range(100)
                    ],
                    "notes": ["context"] * 100,
                },
            ),
            (
                "Docker setup",
                {
                    "status": "failed",
                    "failureKind": "infrastructure",
                    "diagnostics": [
                        {
                            "kind": "infrastructure",
                            "message": "timeout",
                            "detail": "health check timed out",
                        }
                    ],
                },
            ),
        ]
    )
    result = adapt_native(context(succeeded=False), json.dumps(report).encode())
    assert len(result.diagnostics) == 100
    assert result.diagnostics[0].detail == "health check timed out"
    assert len(result.notes) == 100
    assert "in-process: 1 diagnostics omitted." in result.notes


def test_truncated_group_names_keep_distinct_finding_ids_and_valid_notes() -> None:
    source = {
        "status": "failed",
        "failureKind": "quality",
        "findings": [{"id": "same-finding", "severity": "error", "message": "Failure"}],
        "notes": ["n" * 1000],
    }
    report = combine([("g" * 255 + "first", source), ("g" * 255 + "second", source)])
    result = adapt_native(context(succeeded=False), json.dumps(report).encode())
    assert len({finding.id for finding in result.findings}) == 2
    assert {finding.group for finding in result.findings} == {"g" * 255}
    assert all(len(note) == 1000 for note in result.notes)
