"""Authenticate and dispatch pull-request Quality Graph commands."""

from __future__ import annotations

import base64
import re
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum

from quality_graph.approvals import ApprovalRecord, append_approval_record
from quality_graph.artifacts import ArtifactExpectation, download_results
from quality_graph.compiler import compile_graph
from quality_graph.controls import control_states, decode_control_marker
from quality_graph.github import GITHUB_PAGE_SIZE, GitHubPort
from quality_graph.graph import Graph
from quality_graph.policy import ApprovalTarget, effective_graph
from quality_graph.result import ControlKind, JsonValue

COMMAND_RE = re.compile(
    r"^/qg(?:\s+(help|status|ignore|remove-ignore|ignore-file|remove-ignore-file)"
    r"(?:\s+(\S+))?)?$"
)
DEFAULT_BOT_LOGIN = "github-actions[bot]"


class CommandName(StrEnum):
    """Represent the supported administrator command surface."""

    HELP = "help"
    STATUS = "status"
    IGNORE = "ignore"
    REMOVE_IGNORE = "remove-ignore"
    IGNORE_FILE = "ignore-file"
    REMOVE_IGNORE_FILE = "remove-ignore-file"


@dataclass(frozen=True)
class Command:
    """Carry one parsed canonical command."""

    name: CommandName
    arguments: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Render the canonical command text."""
        suffix = f" {','.join(self.arguments)}" if self.arguments else ""
        return f"/qg {self.name.value}{suffix}"


@dataclass(frozen=True)
class CommandRequest:
    """Carry one command candidate extracted from an issue-comment event."""

    comment_id: int
    body: str
    actor: str | None
    pull_request: int | None
    previous_dashboard_body: str | None = None


@dataclass(frozen=True)
class CommandOutcome:
    """Describe whether a command was handled and changed approval state."""

    handled: bool
    authorized: bool = False
    changed: bool = False


@dataclass(frozen=True)
class CommandContext:
    """Carry the latest graph, run, and active semantic targets."""

    graph: Graph
    run_id: int
    targets: frozenset[ApprovalTarget]


def parse_command(value: str) -> Command | None:
    """Parse one complete canonical `/qg` comment."""
    match = COMMAND_RE.fullmatch(value.strip())
    if match is None:
        return None
    name = CommandName(match.group(1) or "status")
    raw = match.group(2)
    if name in {CommandName.HELP, CommandName.STATUS}:
        return Command(name) if raw is None else None
    if raw is None:
        return None
    arguments = tuple(item.strip() for item in raw.split(","))
    if not all(arguments) or "all" in arguments:
        return None
    return Command(name, arguments)


def command_request(
    event_value: JsonValue,
    *,
    bot_logins: tuple[str, ...] = (DEFAULT_BOT_LOGIN,),
) -> CommandRequest | None:
    """Extract a direct command or one canonical checkbox transition."""
    event = _object(event_value, "issue comment event")
    comment = _object(event.get("comment"), "issue comment")
    comment_id = _integer(comment.get("id"), "comment id")
    body = _string(comment.get("body"), "comment body").strip()
    issue = _object(event.get("issue"), "issue")
    number = _optional_integer(issue.get("number"), "issue number")
    pull_request = number if issue.get("pull_request") is not None else None
    author = comment.get("user")
    author_login = (
        _optional_string(author.get("login"), "comment author")
        if isinstance(author, dict)
        else None
    )
    if body == "/qg" or body.startswith("/qg "):
        return CommandRequest(comment_id, body, author_login, pull_request)
    if event.get("action") != "edited" or author_login not in bot_logins:
        return None
    changes = _object(event.get("changes"), "comment changes")
    body_change = _object(changes.get("body"), "comment body change")
    previous = _string(body_change.get("from"), "previous comment body")
    before = control_states(previous)
    after = control_states(body)
    changed = [marker for marker in before.keys() & after.keys() if before[marker] != after[marker]]
    if len(changed) != 1:
        return None
    commands = decode_control_marker(changed[0])
    if commands is None:
        return None
    apply, reverse = commands
    sender = _object(event.get("sender"), "event sender")
    actor = _optional_string(sender.get("login"), "event sender login")
    selected = apply if after[changed[0]] else reverse
    return CommandRequest(comment_id, selected, actor, pull_request, previous)


def handle_command(port: GitHubPort, event_value: JsonValue) -> CommandOutcome:
    """Authenticate, record, and trigger one Quality Graph command."""
    request = command_request(event_value)
    if request is None:
        return CommandOutcome(handled=False)
    if request.previous_dashboard_body is not None:
        port.request(
            "PATCH",
            f"/issues/comments/{request.comment_id}",
            {"body": request.previous_dashboard_body},
        )
    command = parse_command(request.body)
    if command is None or request.pull_request is None or request.actor is None:
        _react(port, request.comment_id, "x")
        return CommandOutcome(handled=True)
    if command.name in {CommandName.HELP, CommandName.STATUS}:
        reply = (
            _help_body()
            if command.name is CommandName.HELP
            else "Quality Graph command handling is available."
        )
        _reply(port, request.pull_request, reply)
        _react(port, request.comment_id, "+1")
        return CommandOutcome(handled=True, authorized=True)
    context = _command_context(port, request.pull_request)
    if not _authorized(port, request.actor, context.graph.administrator_roles):
        _react(port, request.comment_id, "confused")
        return CommandOutcome(handled=True)
    targets = _command_targets(command, context.targets)
    _react(port, request.comment_id, "eyes")
    record = ApprovalRecord(
        command.name in {CommandName.IGNORE, CommandName.IGNORE_FILE},
        targets,
        request.actor,
        request.comment_id,
    )
    append_approval_record(port, request.pull_request, record)
    port.request("POST", f"/actions/runs/{context.run_id}/rerun-failed-jobs")
    _react(port, request.comment_id, "hooray")
    return CommandOutcome(handled=True, authorized=True, changed=True)


def _command_context(port: GitHubPort, number: int) -> CommandContext:
    pull = _object(port.request("GET", f"/pulls/{number}"), "pull request")
    head = _object(pull.get("head"), "pull request head")
    base = _object(pull.get("base"), "pull request base")
    head_sha = _string(head.get("sha"), "pull request head SHA")
    base_sha = _string(base.get("sha"), "pull request base SHA")
    graph = Graph.from_yaml(_repository_file(port, "quality-graph.yml", base_sha))
    compiled = compile_graph(graph)
    run = _latest_run(port, number)
    run_id = _integer(run.get("id"), "workflow run id")
    attempt = _integer(run.get("run_attempt", 1), "workflow run attempt")
    expectation = ArtifactExpectation(
        port.repository,
        number,
        head_sha,
        run_id,
        compiled.graph_digest,
        frozenset(node.id for node in graph.nodes),
    )
    results = download_results(port, expectation)
    if any(result.provenance.run_attempt > attempt for result in results.values()):
        message = "result artifact attempt exceeds the latest workflow attempt"
        raise ValueError(message)
    state = effective_graph(graph, results, set())
    return CommandContext(graph, run_id, state.targets)


def _latest_run(port: GitHubPort, number: int) -> dict[str, JsonValue]:
    path = (
        "/actions/workflows/quality-graph.yml/runs?event=pull_request"
        f"&per_page={GITHUB_PAGE_SIZE}&page=1"
    )
    response = _object(port.request("GET", path), "workflow runs")
    runs = [
        run
        for value in _array(response.get("workflow_runs", []), "workflow runs")
        for run in (_object(value, "workflow run"),)
        if any(
            _object(pull, "workflow pull request").get("number") == number
            for pull in _array(run.get("pull_requests", []), "workflow pull requests")
        )
    ]
    if not runs:
        message = f"no Quality Graph workflow run found for PR #{number}"
        raise ValueError(message)
    return max(runs, key=lambda run: _integer(run.get("id"), "workflow run id"))


def _authorized(port: GitHubPort, actor: str, roles: tuple[str, ...]) -> bool:
    login = urllib.parse.quote(actor, safe="")
    try:
        response = port.request("GET", f"/collaborators/{login}/permission")
    except RuntimeError:
        return False
    data = _object(response, "collaborator permission")
    permission = _optional_string(data.get("permission"), "collaborator permission")
    return permission in roles


def _command_targets(
    command: Command,
    active: frozenset[ApprovalTarget],
) -> tuple[ApprovalTarget, ...]:
    targets: list[ApprovalTarget] = []
    for argument in command.arguments:
        if command.name in {CommandName.IGNORE_FILE, CommandName.REMOVE_IGNORE_FILE}:
            candidates: tuple[ApprovalTarget, ...] = (ApprovalTarget(ControlKind.FILE, argument),)
        else:
            candidates = (
                ApprovalTarget(ControlKind.FINDING, argument),
                ApprovalTarget(ControlKind.NODE, argument),
            )
        selected = next((candidate for candidate in candidates if candidate in active), None)
        if selected is None:
            message = f"unknown or non-approvable Quality Graph target: {argument}"
            raise ValueError(message)
        targets.append(selected)
    return tuple(dict.fromkeys(targets))


def _repository_file(port: GitHubPort, path: str, ref: str) -> str:
    encoded_path = urllib.parse.quote(path, safe="")
    encoded_ref = urllib.parse.quote(ref, safe="")
    response = _object(
        port.request("GET", f"/contents/{encoded_path}?ref={encoded_ref}"),
        "repository file",
    )
    content = _string(response.get("content"), "repository file content")
    return base64.b64decode(content.replace("\n", ""), validate=True).decode()


def _react(port: GitHubPort, comment_id: int, content: str) -> None:
    port.request(
        "POST",
        f"/issues/comments/{comment_id}/reactions",
        {"content": content},
    )


def _reply(port: GitHubPort, number: int, body: str) -> None:
    port.request("POST", f"/issues/{number}/comments", {"body": body})


def _help_body() -> str:
    return """Quality Graph commands:

- `/qg status`
- `/qg ignore <finding-or-node>`
- `/qg remove-ignore <finding-or-node>`
- `/qg ignore-file <path>`
- `/qg remove-ignore-file <path>`"""


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise TypeError(message)
    return value


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{context} must be an array"
        raise TypeError(message)
    return value


def _string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise TypeError(message)
    return value


def _optional_string(value: JsonValue, context: str) -> str | None:
    return None if value is None else _string(value, context)


def _integer(value: JsonValue, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{context} must be an integer"
        raise TypeError(message)
    return value


def _optional_integer(value: JsonValue, context: str) -> int | None:
    return None if value is None else _integer(value, context)
