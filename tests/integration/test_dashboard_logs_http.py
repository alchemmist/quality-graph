from __future__ import annotations

import base64
from typing import TYPE_CHECKING, cast

import pytest

from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_run
from quality_graph_core.result import JsonValue
from tests.integration.test_github_lifecycle_http import state, workflow_event

if TYPE_CHECKING:
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration

RUN_URL = "https://github.com/owner/repository/actions/runs/10"
FORMAT_URL = f"{RUN_URL}/job/101"
LINT_URL = f"{RUN_URL}/job/102"


def scenario() -> dict[str, JsonValue]:
    fixture = state()
    return {
        "contents": {f"{'d' * 40}:qg.yaml": fixture.graph},
        "run_artifacts": {"10": fixture.artifacts},
        "downloads": {
            str(identifier): base64.b64encode(content).decode()
            for identifier, content in fixture.downloads.items()
        },
        "workflow_jobs": {
            "10": [
                {
                    "id": 101,
                    "run_id": 10,
                    "run_attempt": 1,
                    "name": "Formatting",
                    "html_url": FORMAT_URL,
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 102,
                    "run_id": 10,
                    "run_attempt": 1,
                    "name": "Lint",
                    "html_url": LINT_URL,
                    "status": "completed",
                    "conclusion": "failure",
                },
            ]
        },
    }


def dashboard_body(fake_github: FakeGitHubScenario) -> str:
    comments = cast("list[dict[str, JsonValue]]", fake_github.snapshot()["comments"])
    return cast("str", comments[0]["body"])


def test_final_dashboard_links_each_node_to_its_github_job(
    fake_github: FakeGitHubScenario,
) -> None:
    fake_github.reset(scenario())
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    publish_workflow_run(port, workflow_event())

    body = dashboard_body(fake_github)
    format_row = next(line for line in body.splitlines() if line.startswith("| Formatting |"))
    lint_row = next(line for line in body.splitlines() if line.startswith("| Lint |"))
    assert f"[Logs]({FORMAT_URL})" in format_row
    assert f"[Logs]({LINT_URL})" in lint_row
