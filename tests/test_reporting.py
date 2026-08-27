from dataclasses import replace
from pathlib import Path

from qg_github.reporting import (
    MAX_JOB_SUMMARY_CHARACTERS,
    MAX_SUMMARY_FINDINGS,
    append_job_summary,
    render_job_summary,
)
from quality_graph_core.result import (
    Control,
    ControlKind,
    Diagnostic,
    DiagnosticKind,
    FailureKind,
    Finding,
    Metric,
    Provenance,
    Result,
    ResultStatus,
    Severity,
    SourceLocation,
)


def result(*, findings: int = 1, summary: str = "Failure") -> Result:
    return Result(
        "lint",
        "Lint <unsafe>",
        ResultStatus.FAILED,
        Provenance("owner/repository", "a" * 40, 1, 1, "b" * 64, 42),
        FailureKind.QUALITY,
        summary,
        (Metric("Finding|count", "1\nitem"),),
        tuple(
            Finding(
                f"finding-{index}",
                Severity.ERROR,
                "Unsafe <message>",
                "RULE",
                location=SourceLocation("src/app.py", index + 1, index + 1),
            )
            for index in range(findings)
        ),
        diagnostics=(Diagnostic(DiagnosticKind.COMMAND, "Command failed", "bad ``` output"),),
        notes=("Administrator note <unsafe>",),
    )


def test_job_summary_renders_complete_safe_result() -> None:
    rendered = render_job_summary(result())

    assert '<a id="quality-graph-lint"></a>' in rendered
    assert "## ❌ Lint &lt;unsafe&gt;" in rendered
    assert "| Finding&#124;count | 1<br>item |" in rendered
    assert "Unsafe &lt;message&gt;" in rendered
    assert "`finding-0`" in rendered
    assert "`src/app.py:1`" in rendered
    assert "bad ` ` ` output" in rendered
    assert "Administrator note &lt;unsafe&gt;" in rendered


def test_job_summary_limits_findings_and_total_size() -> None:
    source = result(findings=MAX_SUMMARY_FINDINGS + 2, summary="x" * 200_000)
    large = replace(
        source,
        diagnostics=tuple(
            Diagnostic(DiagnosticKind.INFRASTRUCTURE, "Failure", "x" * 20_000) for _ in range(50)
        ),
    )
    rendered = render_job_summary(large)

    assert len(rendered) == MAX_JOB_SUMMARY_CHARACTERS
    assert rendered.endswith("characters omitted._\n")


def test_job_summary_renders_every_status_and_minimal_findings() -> None:
    provenance = Provenance("owner/repository", "a" * 40, 1, 1, "b" * 64)
    icons = {
        ResultStatus.WAITING: "⏳",
        ResultStatus.IN_PROGRESS: "🚀",
        ResultStatus.PASSED: "✅",
        ResultStatus.SKIPPED: "⏭️",
        ResultStatus.CANCELLED: "🚫",
    }
    for status, icon in icons.items():
        failure = FailureKind.CANCELLATION if status is ResultStatus.CANCELLED else None
        rendered = render_job_summary(Result("node", "Node", status, provenance, failure))
        assert f"## {icon} Node" in rendered

    minimal = replace(
        result(),
        findings=(Finding("minimal", Severity.NOTICE, "Notice"),),
        metrics=(),
        diagnostics=(),
        notes=(),
    )
    rendered = render_job_summary(minimal)
    assert "**notice**: Notice" in rendered
    assert "### Diagnostics" not in rendered
    assert "For repository administrators" not in rendered

    without_detail = replace(
        result(),
        diagnostics=(Diagnostic(DiagnosticKind.COMMAND, "Failure"),),
    )
    assert "**command:** Failure" in render_job_summary(without_detail)


def test_summary_appends_to_explicit_sink(tmp_path: Path) -> None:
    path = tmp_path / "summary.md"
    path.write_text("Existing\n")

    append_job_summary(path, result())

    assert path.read_text().startswith("Existing\n<a id=")


def test_job_summary_composes_custom_content_and_canonical_controls() -> None:
    source = replace(
        result(summary="| Custom | Table |\n| --- | --- |"),
        controls=(
            Control(ControlKind.FINDING, "finding-0", checked=True),
            Control(ControlKind.FILE, "src/app.py"),
            Control(ControlKind.NODE, "lint"),
        ),
    )

    rendered = render_job_summary(source)

    assert "| Custom | Table |" in rendered
    assert rendered.index("`finding-0` — **error**") < rendered.index(
        "For repository administrators"
    )
    assert "- [x] finding: `finding-0`" in rendered
    assert "apply: `/qg ignore finding-0`" in rendered
    assert "reverse: `/qg remove-ignore finding-0`" in rendered
    assert "- [ ] file: `src/app.py`" in rendered
    assert "- [ ] node: `lint`" in rendered
    assert rendered.rstrip().endswith("</details>")


def test_job_summary_bounds_controls_and_keeps_omission_reference() -> None:
    controls = tuple(
        Control(ControlKind.FILE, f"src/{index}-{'x' * 4_000}") for index in range(300)
    )

    rendered = render_job_summary(replace(result(), controls=controls))

    assert len(rendered) <= MAX_JOB_SUMMARY_CHARACTERS
    assert "additional actions are available in the result artifact" in rendered
    assert rendered.rstrip().endswith("</details>")
