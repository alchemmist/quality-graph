from __future__ import annotations

import base64
from typing import TYPE_CHECKING, cast

import pytest

from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_run, watch_workflow_run
from quality_graph_core.result import JsonValue, ResultStatus
from tests.integration.test_github_lifecycle_http import state, workflow_event

if TYPE_CHECKING:
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


def test_publication_lifecycle_converges_through_selected_http_adapter(
    fake_github: FakeGitHubScenario,
) -> None:
    fixture = state()
    fake_github.reset(
        {
            "contents": {f"{'d' * 40}:qg.yaml": fixture.graph},
            "run_artifacts": {"10": fixture.artifacts},
            "downloads": {
                str(identifier): base64.b64encode(content).decode()
                for identifier, content in fixture.downloads.items()
            },
            "workflow_job_snapshots": {
                "10": [
                    [
                        {"name": "Formatting", "status": "in_progress"},
                        {"name": "Lint", "status": "queued"},
                    ],
                    [
                        {"name": "Formatting", "status": "completed", "conclusion": "success"},
                        {"name": "Lint", "status": "completed", "conclusion": "failure"},
                    ],
                ]
            },
        }
    )
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    live = watch_workflow_run(
        port,
        workflow_event() | {"action": "requested"},
        sleep=lambda _: None,
    )
    completed = publish_workflow_run(port, workflow_event())
    observed = fake_github.snapshot()

    assert live.status is ResultStatus.FAILED
    assert completed.status is ResultStatus.FAILED
    checks = cast("list[dict[str, JsonValue]]", observed["checks"])
    comments = cast("list[dict[str, JsonValue]]", observed["comments"])
    assert len(checks) == 1
    assert checks[0]["status"] == "completed"
    assert checks[0]["conclusion"] == "failure"
    assert "waiting" not in cast("str", comments[0]["body"])
