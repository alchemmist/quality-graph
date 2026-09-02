from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from typing import TYPE_CHECKING, cast

import pytest

from qg_github.commands import CommandOutcome, handle_command
from qg_github.compiler import compile_graph
from qg_github.controls import render_control
from qg_github.github import HttpGitHubPort
from quality_graph_core.graph import Graph
from quality_graph_core.result import (
    Control,
    ControlKind,
    FailureKind,
    Finding,
    JsonValue,
    Provenance,
    Result,
    ResultStatus,
    Severity,
    SourceLocation,
)
from tests.test_graph import GRAPH

if TYPE_CHECKING:
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


def event(body: str, *, actor: str = "admin", comment_id: int = 10) -> dict[str, object]:
    return {
        "action": "created",
        "issue": {"number": 42, "pull_request": {"url": "pull"}},
        "comment": {"id": comment_id, "body": body, "user": {"login": actor}},
        "sender": {"login": actor},
    }


def command_archive() -> bytes:
    digest = compile_graph(Graph.from_yaml(GRAPH)).graph_digest
    value = Result(
        "lint",
        "Lint",
        ResultStatus.FAILED,
        Provenance("owner/repository", "a" * 40, 100, 1, digest, 42),
        FailureKind.QUALITY,
        findings=(
            Finding(
                "finding",
                Severity.ERROR,
                "Failure",
                location=SourceLocation("src/app.py", 1, 1),
            ),
        ),
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("lint.json", value.to_json())
    return output.getvalue()


def configure(
    fake_github: FakeGitHubScenario,
    *,
    permission: str = "admin",
    permission_failure: bool = False,
    workflow_runs: list[dict[str, object]] | None = None,
    comment: tuple[str, str] = ("/qg ignore finding", "admin"),
) -> None:
    content = command_archive()
    fake_github.reset(
        {
            "contents": {f"{'d' * 40}:qg.yaml": GRAPH},
            "permissions": {"admin": permission, "contributor": "write"},
            "comments": [
                {
                    "id": 10,
                    "issue_number": 42,
                    "body": comment[0],
                    "user": {"login": comment[1]},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
            "workflow_runs": workflow_runs
            or [{"id": 100, "run_attempt": 1, "pull_requests": [{"number": 42}]}],
            "run_artifacts": {
                "100": [
                    {
                        "id": 1,
                        "name": "quality-result-lint-1",
                        "size_in_bytes": len(content),
                        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                        "expired": False,
                    }
                ]
            },
            "downloads": {"1": base64.b64encode(content).decode()},
            "failures": (
                [
                    {
                        "method": "GET",
                        "path": "/repos/owner/repository/collaborators/admin/permission",
                        "status": 503,
                    }
                ]
                if permission_failure
                else []
            ),
        }
    )


def port(fake_github: FakeGitHubScenario) -> HttpGitHubPort:
    return HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)


def test_authorized_command_records_immutable_approval_and_reruns_over_http(
    fake_github: FakeGitHubScenario,
) -> None:
    configure(fake_github)

    outcome = handle_command(port(fake_github), event("/qg ignore finding"))
    state = fake_github.snapshot()

    assert outcome == CommandOutcome(handled=True, authorized=True, changed=True)
    comments = cast("list[dict[str, JsonValue]]", state["comments"])
    reactions = cast("list[dict[str, JsonValue]]", state["reactions"])
    assert "quality-graph:approval" in cast("str", comments[-1]["body"])
    assert [reaction["content"] for reaction in reactions] == ["eyes", "hooray"]
    assert state["reruns"] == [100]


def test_command_authorization_fails_closed_over_http(
    fake_github: FakeGitHubScenario,
) -> None:
    configure(fake_github)

    outcome = handle_command(
        port(fake_github),
        event("/qg ignore finding", actor="contributor"),
    )
    state = fake_github.snapshot()

    assert outcome == CommandOutcome(handled=True)
    comments = cast("list[dict[str, JsonValue]]", state["comments"])
    reactions = cast("list[dict[str, JsonValue]]", state["reactions"])
    assert len(comments) == 1
    assert [reaction["content"] for reaction in reactions] == ["confused"]
    assert state["reruns"] == []


@pytest.mark.parametrize(("body", "text"), [("/qg help", "commands"), ("/qg", "available")])
def test_informational_commands_reply_without_repository_mutation(
    fake_github: FakeGitHubScenario,
    body: str,
    text: str,
) -> None:
    fake_github.reset(
        {
            "comments": [
                {
                    "id": 10,
                    "issue_number": 42,
                    "body": body,
                    "user": {"login": "contributor"},
                }
            ]
        }
    )

    outcome = handle_command(port(fake_github), event(body, actor="contributor"))
    state = fake_github.snapshot()

    assert outcome == CommandOutcome(handled=True, authorized=True)
    comments = cast("list[dict[str, JsonValue]]", state["comments"])
    reactions = cast("list[dict[str, JsonValue]]", state["reactions"])
    assert text.lower() in cast("str", comments[-1]["body"]).lower()
    assert [reaction["content"] for reaction in reactions] == ["+1"]


def test_permission_failure_rejects_command_without_partial_state(
    fake_github: FakeGitHubScenario,
) -> None:
    configure(fake_github, permission_failure=True)

    outcome = handle_command(port(fake_github), event("/qg ignore finding"))
    state = fake_github.snapshot()

    assert outcome == CommandOutcome(handled=True)
    comments = cast("list[dict[str, JsonValue]]", state["comments"])
    reactions = cast("list[dict[str, JsonValue]]", state["reactions"])
    assert len(comments) == 1
    assert [reaction["content"] for reaction in reactions] == ["confused"]
    assert state["reruns"] == []


def test_command_finds_the_pull_request_run_after_the_first_http_page(
    fake_github: FakeGitHubScenario,
) -> None:
    unrelated = [
        {"id": identifier, "run_attempt": 1, "pull_requests": [{"number": 7}]}
        for identifier in range(1, 101)
    ]
    configure(
        fake_github,
        workflow_runs=[
            *unrelated,
            {"id": 100, "run_attempt": 1, "pull_requests": [{"number": 42}]},
        ],
    )

    outcome = handle_command(port(fake_github), event("/qg ignore finding"))

    assert outcome == CommandOutcome(handled=True, authorized=True, changed=True)
    assert fake_github.snapshot()["reruns"] == [100]


def test_unauthorized_checkbox_edit_is_rolled_back_before_command_rejection(
    fake_github: FakeGitHubScenario,
) -> None:
    control = render_control(Control(ControlKind.FINDING, "finding"))
    previous = f"Dashboard\n\n{control}"
    updated = previous.replace("- [ ]", "- [x]")
    configure(
        fake_github,
        comment=(updated, "github-actions[bot]"),
    )
    checkbox_event = {
        "action": "edited",
        "issue": {"number": 42, "pull_request": {"url": "pull"}},
        "comment": {
            "id": 10,
            "body": updated,
            "user": {"login": "github-actions[bot]"},
        },
        "changes": {"body": {"from": previous}},
        "sender": {"login": "contributor"},
    }

    outcome = handle_command(port(fake_github), checkbox_event)
    state = fake_github.snapshot()

    assert outcome == CommandOutcome(handled=True)
    comments = cast("list[dict[str, JsonValue]]", state["comments"])
    reactions = cast("list[dict[str, JsonValue]]", state["reactions"])
    assert comments[0]["body"] == previous
    assert [reaction["content"] for reaction in reactions] == ["confused"]
    assert state["reruns"] == []
