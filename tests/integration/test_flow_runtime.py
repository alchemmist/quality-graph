from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from qg_cli.cli import main
from qg_github.runtime import CollectionRequest, collect, publish_collection
from quality_graph_core.result import (
    Annotation,
    Control,
    ControlKind,
    Result,
    ResultStatus,
    Severity,
    SourceLocation,
)
from tests.test_cli import PROVENANCE_ARGUMENTS
from tests.test_runtime import environment

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("presentation", ["none", "release"])
def test_non_pr_collection_strips_producer_controls_and_preserves_raw_reports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    presentation: str,
) -> None:
    values = environment(tmp_path) | {
        "QG_FLOW_ID": "main",
        "QG_OPERATION_ID": "lint",
        "QG_PRESENTATION": presentation,
        "QG_ADAPTER": "native",
        "QG_REPORT_PATH": "report.json",
        "QG_APPROVAL_NODE": "true",
    }
    request = CollectionRequest.from_environment(values, {})
    producer = Result(
        "lint",
        "Lint",
        ResultStatus.PASSED,
        request.context.provenance,
        summary="Native result",
        annotations=(Annotation(Severity.NOTICE, "Notice", SourceLocation("app.py", 1, 1)),),
        controls=(Control(ControlKind.NODE, "lint"),),
    )
    (tmp_path / "report.json").write_text(producer.to_json())

    collected = collect(request)
    assert publish_collection(request, collected) == 0

    assert collected.controls == ()
    assert collected.annotations == producer.annotations
    assert collected.provenance.flow_id == "main"
    assert collected.provenance.operation_id == "lint"
    assert "::notice" not in capsys.readouterr().out
    assert Result.from_json(request.result_path.read_text()) == collected
    assert "Native result" in (tmp_path / "summary.md").read_text()


def test_collection_rejects_unknown_presentation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported collection presentation"):
        CollectionRequest.from_environment(environment(tmp_path) | {"QG_PRESENTATION": "other"}, {})


def test_cli_emits_explicit_flow_provenance_for_native_producers(tmp_path: Path) -> None:
    path = tmp_path / "native.json"
    assert (
        main(
            [
                "result",
                "emit",
                "--node-id",
                "verify",
                "--title",
                "Verify",
                "--status",
                "passed",
                *PROVENANCE_ARGUMENTS,
                "--flow-id",
                "review",
                "--operation-id",
                "lint",
                "--output",
                str(path),
            ]
        )
        == 0
    )
    assert main(["result", "validate", str(path)]) == 0
    value = Result.from_json(path.read_text())
    assert value.node_id == "verify"
    assert value.provenance.flow_id == "review"
    assert value.provenance.operation_id == "lint"
