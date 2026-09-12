from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from qg_github.runtime import CollectionRequest, collect, publish_collection
from quality_graph_core.graph import AdapterKind
from quality_graph_core.result import FailureKind, ResultStatus
from tests.test_runtime import environment, event

pytestmark = pytest.mark.integration
RUNNER = Path(__file__).resolve().parents[2] / "scripts/check_report.py"


def run_report(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    values = dict(os.environ)
    values.pop("PYTEST_ADDOPTS", None)
    values.pop("QG_FAKE_GITHUB_URL", None)
    return subprocess.run(
        [sys.executable, str(RUNNER), "--output", "report.json", *arguments],
        cwd=tmp_path,
        env=values,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def summary(tmp_path: Path, code: int) -> tuple[str, FailureKind | None]:
    request = CollectionRequest.from_environment(
        environment(tmp_path, outcome="failure" if code else "success"),
        event(),
    )
    request = replace(request, adapter=AdapterKind.NATIVE, report_path="report.json")
    result = collect(request)
    publish_collection(request, result)
    return (tmp_path / "summary.md").read_text(), result.failure_kind


def configure_pytest(tmp_path: Path, *, fail: bool) -> Path:
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = integration: probe\n")
    (tmp_path / "test_probe.py").write_text(
        "import pytest\npytestmark = pytest.mark.integration\n"
        "def test_expected_value():\n"
        + ("    assert 1 == 2, 'domain mismatch'\n" if fail else "    assert 1 == 1\n")
    )
    compose = tmp_path / "compose.py"
    compose.write_text("import sys\nprint('compose', sys.argv)\n")
    return compose


@pytest.mark.parametrize("fail", [False, True])
def test_both_pytest_reports_reach_rendered_summary(tmp_path: Path, *, fail: bool) -> None:
    compose = configure_pytest(tmp_path, fail=fail)
    outcome = run_report(tmp_path, "--suite", "medium", "--compose", f"{sys.executable} {compose}")
    assert outcome.returncode == int(fail), outcome.stdout + outcome.stderr
    assert (tmp_path / "report.in-process.xml").is_file()
    assert (tmp_path / "report.docker.xml").is_file()
    rendered, failure = summary(tmp_path, outcome.returncode)
    assert "in-process / Tests" in rendered
    assert "docker / Tests" in rendered
    assert "in-process / Failures" in rendered
    assert "docker / Failures" in rendered
    assert failure is (FailureKind.QUALITY if fail else None)
    if fail:
        assert "#### in-process" in rendered
        assert "#### docker" in rendered
        assert "test_expected_value" in rendered
        assert "assert 1 == 2" in rendered
        assert "domain mismatch" in rendered
        assert "The declared command failed." not in rendered


@pytest.mark.parametrize("phase", ["up", "down"])
def test_container_failures_are_actionable_without_junit(tmp_path: Path, phase: str) -> None:
    compose = configure_pytest(tmp_path, fail=False)
    (tmp_path / "report.docker.xml").write_text("stale results")
    compose.write_text(
        "import sys\n"
        "print('container service unavailable')\n"
        f"raise SystemExit(7 if {phase!r} in sys.argv else 0)\n"
    )
    outcome = run_report(tmp_path, "--suite", "medium", "--compose", f"{sys.executable} {compose}")
    assert outcome.returncode == 1
    rendered, failure = summary(tmp_path, outcome.returncode)
    assert failure is not None
    assert "container service unavailable" in rendered
    assert ("Docker setup" if phase == "up" else "Docker cleanup") in rendered
    assert "in-process / Tests" in rendered
    assert (tmp_path / "report.docker.xml").exists() is (phase == "down")


@pytest.mark.parametrize("case", ["missing", "malformed", "contradiction"])
def test_bad_test_reports_do_not_hide_command_failures(tmp_path: Path, case: str) -> None:
    code = "print('specific tool error')"
    if case != "missing":
        content = (
            "bad XML" if case == "malformed" else '<testsuite><testcase name="pass" /></testsuite>'
        )
        code += f"; from pathlib import Path; Path('tests.xml').write_text({content!r})"
    if case == "contradiction":
        code += "; raise SystemExit(2)"
    outcome = run_report(tmp_path, "--junit", "tests.xml", "--", sys.executable, "-c", code)
    assert outcome.returncode == 1
    rendered, failure = summary(tmp_path, outcome.returncode)
    assert failure is not None
    assert "specific tool error" in rendered


def test_custom_data_report_binds_identity_and_formats_findings(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "reportVersion": 0,
                "status": "failed",
                "failureKind": "quality",
                "metrics": [{"label": "Files checked", "value": "5"}],
                "findings": [
                    {
                        "id": "custom-check",
                        "severity": "error",
                        "message": "Invalid setting",
                        "group": "Configuration",
                    }
                ],
                "diagnostics": [
                    {
                        "kind": "command",
                        "message": "Expected enabled=true",
                        "detail": "enabled=false",
                    }
                ],
            }
        )
    )
    rendered, failure = summary(tmp_path, 1)
    assert failure is FailureKind.QUALITY
    assert "Files checked" in rendered
    assert "#### Configuration" in rendered
    assert "Invalid setting" in rendered
    assert "enabled=false" in rendered


def test_unconfigured_exit_failure_is_not_repeated(tmp_path: Path) -> None:
    request = CollectionRequest.from_environment(environment(tmp_path, outcome="failure"), event())
    result = collect(request)
    assert result.status is ResultStatus.FAILED
    publish_collection(request, result)
    rendered = (tmp_path / "summary.md").read_text()
    assert rendered.count("The declared command failed.") == 1


@pytest.mark.parametrize("phase", ["up", "down", "both"])
def test_mass_failures_do_not_hide_container_errors(tmp_path: Path, phase: str) -> None:
    compose = configure_pytest(tmp_path, fail=True)
    (tmp_path / "test_probe.py").write_text(
        "import pytest\npytestmark = pytest.mark.integration\n"
        "@pytest.mark.parametrize('case', range(100))\n"
        "def test_expected_value(case):\n"
        "    assert False, f'domain mismatch {case}'\n"
    )
    compose.write_text(
        "import sys\n"
        "print('container health check timed out' if 'up' in sys.argv "
        "else 'container cleanup failed')\n"
        f"raise SystemExit(7 if {phase!r} == 'both' or {phase!r} in sys.argv else 0)\n"
    )
    outcome = run_report(tmp_path, "--suite", "medium", "--compose", f"{sys.executable} {compose}")
    assert outcome.returncode == 1
    reason = "container cleanup failed" if phase == "down" else "container health check timed out"
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["diagnostics"]) == 100
    assert any(reason in item["detail"] for item in report["diagnostics"])
    assert any("in-process" in note and "omitted" in note for note in report["notes"])
    if phase == "down":
        groups = [item["message"].split(":", 1)[0] for item in report["diagnostics"]]
        assert abs(groups.count("in-process") - groups.count("docker")) <= 1
        assert any("docker" in note and "omitted" in note for note in report["notes"])
    rendered, failure = summary(tmp_path, outcome.returncode)
    assert failure is FailureKind.INFRASTRUCTURE
    assert reason in rendered
    assert "omitted" in rendered
    artifact = json.loads((tmp_path / "runner/quality-graph/lint.json").read_text())
    assert any(reason in item["detail"] for item in artifact["diagnostics"])

    if phase == "both":
        assert "container cleanup failed" in rendered
        assert "in-process: 2 diagnostics omitted." in report["notes"]
