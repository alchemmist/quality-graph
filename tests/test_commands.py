import base64
import hashlib
import io
import zipfile

import pytest

from qg_github.commands import (
    Command,
    CommandName,
    CommandOutcome,
    command_request,
    handle_command,
    parse_command,
)
from qg_github.compiler import compile_graph
from qg_github.controls import render_control
from qg_github.github import GitHubError, MemoryGitHubPort
from quality_graph_core.graph import Graph
from quality_graph_core.result import (
    Control,
    ControlKind,
    FailureKind,
    Finding,
    Provenance,
    Result,
    ResultStatus,
    Severity,
    SourceLocation,
)
from tests.test_graph import GRAPH, NONE_PROJECTION_GRAPH

RUNS_PATH = "/actions/workflows/quality-graph.yml/runs?event=pull_request&per_page=100&page=1"


def direct_event(body: str = "/qg ignore finding") -> dict[str, object]:
    return {
        "action": "created",
        "issue": {"number": 42, "pull_request": {"url": "pull"}},
        "comment": {"id": 10, "body": body, "user": {"login": "admin"}},
        "sender": {"login": "admin"},
    }


def result_archive(*, attempt: int = 1, source: str = GRAPH) -> bytes:
    graph_digest = compile_graph(Graph.from_yaml(source)).graph_digest
    result = Result(
        "lint",
        "Lint",
        ResultStatus.FAILED,
        Provenance("owner/repository", "a" * 40, 100, attempt, graph_digest, 42),
        failure_kind=FailureKind.QUALITY,
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
        bundle.writestr("lint.json", result.to_json())
    return output.getvalue()


def configure_context(
    port: MemoryGitHubPort,
    *,
    permission: str = "admin",
    attempt: int = 1,
    source: str = GRAPH,
) -> None:
    port.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}},
    )
    content = base64.b64encode(source.encode()).decode()
    port.enqueue("GET", f"/contents/qg.yaml?ref={'d' * 40}", {"content": content})
    port.enqueue(
        "GET",
        RUNS_PATH,
        {"workflow_runs": [{"id": 100, "run_attempt": 1, "pull_requests": [{"number": 42}]}]},
    )
    archive = result_archive(attempt=attempt, source=source)
    port.enqueue(
        "GET",
        "/actions/runs/100/artifacts?per_page=100&page=1",
        {
            "artifacts": [
                {
                    "id": 1,
                    "name": f"quality-result-lint-{attempt}",
                    "size_in_bytes": len(archive),
                    "digest": f"sha256:{hashlib.sha256(archive).hexdigest()}",
                    "expired": False,
                }
            ]
        },
    )
    port.downloads["/actions/artifacts/1/zip"] = archive
    port.enqueue("GET", "/collaborators/admin/permission", {"permission": permission})


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("/qg", Command(CommandName.STATUS)),
        ("/qg help", Command(CommandName.HELP)),
        ("/qg ignore finding,node", Command(CommandName.IGNORE, ("finding", "node"))),
        ("/qg remove-ignore finding", Command(CommandName.REMOVE_IGNORE, ("finding",))),
        ("/qg ignore-file src/app.py", Command(CommandName.IGNORE_FILE, ("src/app.py",))),
        (
            "/qg remove-ignore-file src/app.py",
            Command(CommandName.REMOVE_IGNORE_FILE, ("src/app.py",)),
        ),
        ("/qg unknown", None),
        ("/qg ignore", None),
        ("/qg status extra", None),
        ("/qg ignore all", None),
    ],
)
def test_command_parser_canonicalizes_complete_comments(
    body: str, expected: Command | None
) -> None:
    assert parse_command(body) == expected
    if expected is not None:
        assert parse_command(expected.text) == expected


def test_command_request_extracts_direct_and_checkbox_commands() -> None:
    direct = command_request(direct_event())
    assert direct.body == "/qg ignore finding"
    assert direct.actor == "admin"

    unchecked = render_control(Control(ControlKind.FINDING, "finding"))
    checked = unchecked.replace("- [ ]", "- [x]")
    checkbox = {
        "action": "edited",
        "issue": {"number": 42, "pull_request": {}},
        "comment": {
            "id": 20,
            "body": checked,
            "user": {"login": "github-actions[bot]"},
        },
        "sender": {"login": "admin"},
        "changes": {"body": {"from": unchecked}},
    }
    request = command_request(checkbox)
    assert request.body == "/qg ignore finding"
    assert request.previous_dashboard_body == unchecked

    reverse_event = dict(checkbox)
    reverse_event["comment"] = {
        "id": 20,
        "body": unchecked,
        "user": {"login": "github-actions[bot]"},
    }
    reverse_event["changes"] = {"body": {"from": checked}}
    assert command_request(reverse_event).body == "/qg remove-ignore finding"


def test_command_request_ignores_ambiguous_or_forged_checkbox_edits() -> None:
    ordinary = direct_event("ordinary")
    ordinary["action"] = "edited"
    ordinary["comment"]["user"] = {"login": "github-actions[bot]"}
    ordinary["changes"] = {"body": {"from": "ordinary"}}
    assert command_request(ordinary) is None

    before = "- [ ] forged <!-- quality-graph-control:invalid -->"
    after = before.replace("- [ ]", "- [x]")
    forged = {
        "action": "edited",
        "issue": {"number": 42, "pull_request": {}},
        "comment": {
            "id": 20,
            "body": after,
            "user": {"login": "github-actions[bot]"},
        },
        "sender": {"login": "admin"},
        "changes": {"body": {"from": before}},
    }
    assert command_request(forged) is None


def test_handler_records_authorized_command_and_reruns_latest_failures() -> None:
    port = MemoryGitHubPort()
    configure_context(port)
    reactions = "/issues/comments/10/reactions"
    port.enqueue("POST", reactions, None, None)
    port.enqueue("POST", "/issues/42/comments", {"id": 200})
    port.enqueue("POST", "/actions/runs/100/rerun-failed-jobs", None)

    outcome = handle_command(port, direct_event())

    assert outcome == CommandOutcome(handled=True, authorized=True, changed=True)
    approval = next(request for request in port.requests if request[1] == "/issues/42/comments")
    assert "quality-graph:approval" in approval[2]["body"]
    assert port.requests[-1] == ("POST", reactions, {"content": "hooray"})


def test_handler_records_file_and_removal_targets() -> None:
    for body, phrase in [
        ("/qg ignore-file src/app.py", "approved"),
        ("/qg remove-ignore finding", "removed approval"),
    ]:
        port = MemoryGitHubPort()
        configure_context(port)
        port.enqueue("POST", "/issues/comments/10/reactions", None, None)
        port.enqueue("POST", "/issues/42/comments", {"id": 200})
        port.enqueue("POST", "/actions/runs/100/rerun-failed-jobs", None)

        assert handle_command(port, direct_event(body)).changed is True
        approval = next(request for request in port.requests if request[1] == "/issues/42/comments")
        assert phrase in approval[2]["body"]


def test_handler_rejects_unauthorized_and_unknown_targets() -> None:
    unauthorized = MemoryGitHubPort()
    configure_context(unauthorized, permission="read")
    unauthorized.enqueue("POST", "/issues/comments/10/reactions", None)

    assert handle_command(unauthorized, direct_event()).authorized is False
    assert unauthorized.requests[-1][2] == {"content": "confused"}

    unknown = MemoryGitHubPort()
    configure_context(unknown)
    with pytest.raises(ValueError, match="unknown or non-approvable"):
        handle_command(unknown, direct_event("/qg ignore missing"))


def test_handler_serves_help_status_and_rejects_invalid_requests() -> None:
    help_port = MemoryGitHubPort()
    help_port.enqueue("POST", "/issues/42/comments", None)
    help_port.enqueue("POST", "/issues/comments/10/reactions", None)
    assert handle_command(help_port, direct_event("/qg help")).authorized is True

    status_port = MemoryGitHubPort()
    status_port.enqueue("POST", "/issues/42/comments", None)
    status_port.enqueue("POST", "/issues/comments/10/reactions", None)
    assert handle_command(status_port, direct_event("/qg status")).authorized is True

    invalid = MemoryGitHubPort()
    invalid.enqueue("POST", "/issues/comments/10/reactions", None)
    assert handle_command(invalid, direct_event("/qg invalid")) == CommandOutcome(handled=True)
    ignored = {"comment": {"id": 1, "body": "ordinary"}, "issue": {}}
    assert handle_command(MemoryGitHubPort(), ignored).handled is False


def test_checkbox_is_rolled_back_before_authorization() -> None:
    unchecked = render_control(Control(ControlKind.FINDING, "finding"))
    checked = unchecked.replace("- [ ]", "- [x]")
    event = {
        "action": "edited",
        "issue": {"number": 42, "pull_request": {}},
        "comment": {"id": 20, "body": checked, "user": {"login": "github-actions[bot]"}},
        "sender": {"login": "admin"},
        "changes": {"body": {"from": unchecked}},
    }
    port = MemoryGitHubPort()
    port.enqueue("PATCH", "/issues/comments/20", None)
    configure_context(port, permission="read")
    port.enqueue("POST", "/issues/comments/20/reactions", None)

    handle_command(port, event)

    assert port.requests[0] == ("PATCH", "/issues/comments/20", {"body": unchecked})


def test_authorization_fails_closed_on_github_error() -> None:
    class ForbiddenPort(MemoryGitHubPort):
        def request(self, method: str, path: str, payload: object = None) -> object:
            if path.startswith("/collaborators/"):
                raise GitHubError(method, path, 403)
            return super().request(method, path, payload)

    port = ForbiddenPort()
    configure_context(port)
    port.enqueue("POST", "/issues/comments/10/reactions", None)

    assert handle_command(port, direct_event()).authorized is False


def test_command_context_accepts_none_projection_with_excluded_dependency() -> None:
    port = MemoryGitHubPort()
    configure_context(port, source=NONE_PROJECTION_GRAPH)
    port.enqueue("POST", "/issues/comments/10/reactions", None, None)
    port.enqueue("POST", "/issues/42/comments", {"id": 200})
    port.enqueue("POST", "/actions/runs/100/rerun-failed-jobs", None)

    outcome = handle_command(port, direct_event())

    assert outcome.changed is True


def test_command_context_rejects_missing_run_and_future_artifact_attempt() -> None:
    missing = MemoryGitHubPort()
    missing.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}},
    )
    content = base64.b64encode(GRAPH.encode()).decode()
    missing.enqueue(
        "GET",
        f"/contents/qg.yaml?ref={'d' * 40}",
        {"content": content},
    )
    missing.enqueue("GET", RUNS_PATH, {"workflow_runs": []})
    with pytest.raises(ValueError, match="no Quality Graph workflow run"):
        handle_command(missing, direct_event())

    malformed = MemoryGitHubPort()
    malformed.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}},
    )
    malformed.enqueue(
        "GET",
        f"/contents/qg.yaml?ref={'d' * 40}",
        {"content": content},
    )
    malformed.enqueue("GET", RUNS_PATH, {"workflow_runs": {}})
    with pytest.raises(TypeError, match="workflow runs must be an array"):
        handle_command(malformed, direct_event())

    future = MemoryGitHubPort()
    configure_context(future, attempt=2)
    with pytest.raises(ValueError, match="attempt exceeds"):
        handle_command(future, direct_event())


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"comment": [], "issue": {}},
        {"comment": {"id": "one", "body": "ordinary"}, "issue": {}},
        {"comment": {"id": 1, "body": 2}, "issue": {}},
        {"comment": {"id": 1, "body": "ordinary"}, "issue": []},
        {"comment": {"id": 1, "body": "ordinary"}, "issue": {"number": "42"}},
    ],
)
def test_command_request_narrows_untrusted_event_shapes(value: object) -> None:
    with pytest.raises(TypeError):
        command_request(value)
