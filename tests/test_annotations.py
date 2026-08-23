from io import StringIO

import pytest

from quality_graph.annotations import (
    MAX_STEP_ANNOTATIONS,
    grouped_annotations,
    publish_annotations,
    workflow_annotation_command,
)
from quality_graph.result import Annotation, Severity, SourceLocation


def annotation(line: int, message: str = "Failure") -> Annotation:
    return Annotation(
        level=Severity.ERROR,
        message=message,
        location=SourceLocation(path="src/app.py", start_line=line, end_line=line),
        title="Lint,check",
    )


def test_annotations_group_duplicate_locations_and_apply_limit() -> None:
    source = [annotation(1, "First"), annotation(1, "Second")]
    source.extend(annotation(line) for line in range(2, MAX_STEP_ANNOTATIONS + 3))

    batch = grouped_annotations(source)

    assert len(batch.annotations) == MAX_STEP_ANNOTATIONS
    assert batch.annotations[0].message == "First\nSecond"
    assert batch.omitted == 2


def test_workflow_command_escapes_untrusted_values() -> None:
    command = workflow_annotation_command(annotation(1, "bad%\r\nvalue"))

    assert command == (
        "::error file=src/app.py,line=1,endLine=1,title=Lint%2Ccheck::bad%25%0D%0Avalue"
    )


def test_publisher_reports_omitted_annotations() -> None:
    output = StringIO()

    publish_annotations((annotation(line) for line in range(1, 13)), stream=output)

    assert output.getvalue().count("::error ") == MAX_STEP_ANNOTATIONS
    assert output.getvalue().endswith(
        "::notice::Additional diagnostics are available in the Job Summary.\n"
    )


def test_warning_annotation_supports_columns_without_a_title() -> None:
    value = Annotation(
        level=Severity.WARNING,
        message="Warning",
        location=SourceLocation(
            path="src/app.py",
            start_line=1,
            end_line=1,
            start_column=2,
            end_column=3,
        ),
    )

    assert workflow_annotation_command(value) == (
        "::warning file=src/app.py,line=1,endLine=1,col=2,endColumn=3::Warning"
    )


def test_publisher_uses_stderr_and_omits_no_notice_for_short_batches(
    capsys: pytest.CaptureFixture[str],
) -> None:
    publish_annotations((annotation(1),))

    captured = capsys.readouterr()
    assert captured.err.count("::error ") == 1
    assert "::notice::" not in captured.err
