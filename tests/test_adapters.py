import json
from dataclasses import replace
from pathlib import Path

import pytest

from quality_graph_core.adapters import (
    MAX_REPORT_BYTES,
    MAX_SUMMARY_CHARACTERS,
    AdapterContext,
    AdapterError,
    adapt_exit,
    adapt_junit,
    adapt_native,
    adapt_sarif,
    adapter_failure,
    read_report,
)
from quality_graph_core.result import FailureKind, Provenance, ResultStatus, Severity


def context(*, succeeded: bool = True) -> AdapterContext:
    return AdapterContext(
        "lint",
        "Lint",
        succeeded,
        Provenance(
            "owner/repository",
            "a" * 40,
            100,
            1,
            "b" * 64,
            42,
        ),
    )


def test_exit_adapter_preserves_success_and_command_failure() -> None:
    passed = adapt_exit(context(), "Clean\n")
    failed = adapt_exit(context(succeeded=False), "Failure")

    assert passed.status is ResultStatus.PASSED
    assert passed.summary == "Clean"
    assert failed.failure_kind is FailureKind.COMMAND
    assert failed.diagnostics[0].detail == "Failure"


def test_exit_adapter_bounds_large_output() -> None:
    result = adapt_exit(context(succeeded=False), "x" * (MAX_SUMMARY_CHARACTERS + 100))

    assert len(result.summary) == MAX_SUMMARY_CHARACTERS
    assert result.summary.endswith("characters omitted._")


def test_native_adapter_validates_identity_provenance_and_command_outcome() -> None:
    passed = adapt_exit(context())

    assert adapt_native(context(), passed.to_json().encode()) == passed
    reconciled = adapt_native(context(succeeded=False), passed.to_json().encode())
    assert reconciled.failure_kind is FailureKind.COMMAND
    assert reconciled.diagnostics[0].message.startswith("The declared command failed")

    with pytest.raises(AdapterError, match="identity"):
        adapt_native(context(), replace(passed, node_id="test").to_json().encode())
    other = replace(passed, provenance=replace(passed.provenance, run_attempt=2))
    with pytest.raises(AdapterError, match="provenance"):
        adapt_native(context(), other.to_json().encode())
    with pytest.raises(AdapterError, match="invalid"):
        adapt_native(context(), b"not json")


def test_sarif_adapter_translates_findings_fingerprints_and_locations() -> None:
    report = {
        "version": "2.1.0",
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "F401",
                        "level": "error",
                        "message": {"text": "Unused import"},
                        "partialFingerprints": {"primaryLocationLineHash": "stable"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "src/app.py"},
                                    "region": {
                                        "startLine": 3,
                                        "endLine": 3,
                                        "startColumn": 2,
                                        "endColumn": 4,
                                    },
                                }
                            }
                        ],
                    },
                    {
                        "level": "warning",
                        "message": {"markdown": "Warning"},
                    },
                ]
            }
        ],
    }

    result = adapt_sarif(context(), json.dumps(report).encode())

    assert result.status is ResultStatus.FAILED
    assert result.metrics[0].value == "2"
    assert result.findings[0].rule_id == "F401"
    assert result.findings[0].location.path == "src/app.py"
    assert result.findings[1].severity is Severity.WARNING
    assert len(result.annotations) == 1


def test_sarif_adapter_handles_clean_report_and_command_failure() -> None:
    report = json.dumps({"runs": [{"results": []}]}).encode()

    assert adapt_sarif(context(), report).status is ResultStatus.PASSED
    failed = adapt_sarif(context(succeeded=False), report)
    assert failed.failure_kind is FailureKind.QUALITY


def test_sarif_adapter_defaults_to_notice_and_semantic_fingerprint() -> None:
    report = json.dumps(
        {
            "runs": [
                {
                    "results": [
                        {
                            "ruleId": "note",
                            "message": {"text": "Notice"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "src/app.py"},
                                        "region": {"startLine": 1},
                                    }
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    ).encode()

    result = adapt_sarif(context(), report)

    assert result.status is ResultStatus.PASSED
    assert result.findings[0].severity is Severity.NOTICE
    assert result.findings[0].fingerprint is not None


@pytest.mark.parametrize("report", [b"not-json", b"{}", b'{"runs":{}}'])
def test_sarif_adapter_rejects_malformed_reports(report: bytes) -> None:
    with pytest.raises(AdapterError):
        adapt_sarif(context(), report)


@pytest.mark.parametrize(
    "report",
    [
        {"runs": [1]},
        {"runs": [{"results": [{"message": {"text": 1}}]}]},
        {
            "runs": [
                {
                    "results": [
                        {
                            "message": {"text": "Failure"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "src/app.py"},
                                        "region": {"startLine": "one"},
                                    }
                                }
                            ],
                        }
                    ]
                }
            ]
        },
    ],
)
def test_sarif_adapter_narrows_nested_values(report: dict[str, object]) -> None:
    with pytest.raises(AdapterError):
        adapt_sarif(context(), json.dumps(report).encode())


def test_junit_adapter_translates_failures_errors_and_skips() -> None:
    report = b"""<testsuites><testsuite name="suite">
      <testcase classname="tests.TestCase" name="passes" />
      <testcase classname="tests.TestCase" name="fails">
        <failure type="AssertionError" message="Expected true" />
      </testcase>
      <testcase name="errors"><error>Exploded</error></testcase>
      <testcase name="skips"><skipped /></testcase>
    </testsuite></testsuites>"""

    result = adapt_junit(context(), report)

    assert result.status is ResultStatus.FAILED
    assert result.metrics[0].value == "4"
    assert result.metrics[1].value == "2"
    assert result.metrics[2].value == "1"
    assert result.findings[0].group == "tests.TestCase"
    assert result.findings[1].message == "Exploded"


def test_junit_adapter_handles_clean_report_and_command_failure() -> None:
    report = b'<testsuite><testcase name="passes" /></testsuite>'

    assert adapt_junit(context(), report).status is ResultStatus.PASSED
    result = adapt_junit(context(succeeded=False), report)
    assert result.failure_kind is FailureKind.QUALITY


@pytest.mark.parametrize("report", [b"not xml", b"<coverage />"])
def test_junit_adapter_rejects_malformed_reports(report: bytes) -> None:
    with pytest.raises(AdapterError):
        adapt_junit(context(), report)


def test_report_reader_enforces_workspace_presence_and_size(tmp_path: Path) -> None:
    report = tmp_path / "reports" / "result.json"
    report.parent.mkdir()
    report.write_bytes(b"result")

    assert read_report(tmp_path, "reports/result.json") == b"result"
    with pytest.raises(AdapterError, match="does not exist"):
        read_report(tmp_path, "missing.json")
    with pytest.raises(AdapterError, match="escapes"):
        read_report(tmp_path, "../outside.json")

    with report.open("wb") as oversized:
        oversized.seek(MAX_REPORT_BYTES)
        oversized.write(b"x")
    with pytest.raises(AdapterError, match="exceeds"):
        read_report(tmp_path, "reports/result.json")


def test_adapter_failure_is_a_distinct_portable_result() -> None:
    result = adapter_failure(context(), AdapterError("missing report"))

    assert result.failure_kind is FailureKind.ADAPTER
    assert result.diagnostics[0].kind.value == "adapter"
