"""Reconcile trusted GitLab merge-request publication from admitted evidence."""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING

from qg_gitlab.admission import BOOTSTRAP_KEY, Expectation, NodeEvidence, admit
from qg_gitlab.api import GitLabError, HttpGitLab, integer, object_value, string
from qg_gitlab.compiler import configuration, execution_graphs, graph_digest, publishes_mr
from qg_gitlab.markers import execution_marker, gate_marker
from quality_graph_core.graph import Graph, LabelSpec
from quality_graph_core.policy import ApprovalTarget, effective_graph
from quality_graph_core.result import ControlKind, ResultStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quality_graph_core.result import JsonValue, Result

SUMMARY_MARKER = "<!-- quality-graph:gitlab:summary -->"
RECORD_RE = re.compile(r"<!-- quality-graph:gitlab:record:(\{[^\n]+\}) -->")
COMMAND_RE = re.compile(
    r"^/qg(?:\s+(help|status|ignore|remove-ignore|ignore-file|remove-ignore-file)(?:\s+(\S+))?)?$"
)
STATUS_NAME = "Quality Graph"
MAINTAINER_ACCESS = 40
OWNER_ACCESS = 50
DEVELOPER_ACCESS = 30


@dataclass(frozen=True)
class Publication:
    """Report one reconciled merge-request outcome."""

    project: int
    merge_request: int
    pipeline: int
    state: str
    changed: bool


def _notes(api: HttpGitLab, project: int, mr: int) -> tuple[dict[str, JsonValue], ...]:
    return tuple(api.pages(f"/projects/{project}/merge_requests/{mr}/notes"))


def _owned(note: Mapping[str, JsonValue], actor: int) -> bool:
    author = note.get("author")
    return isinstance(author, dict) and author.get("id") == actor


def _ledger(
    notes: tuple[dict[str, JsonValue], ...], actor: int, expected: Expectation
) -> tuple[set[ApprovalTarget], set[int]]:
    approvals: set[ApprovalTarget] = set()
    processed: set[int] = set()
    for note in sorted(notes, key=lambda value: integer(value.get("id"), "note ID")):
        body = note.get("body")
        if (
            not _owned(note, actor)
            or note.get("created_at") != note.get("updated_at")
            or not isinstance(body, str)
        ):
            continue
        match = RECORD_RE.search(body)
        if match is None:
            continue
        try:
            record = object_value(json.loads(match.group(1)), "approval record")
            if (
                integer(record.get("version"), "record version") != 1
                or integer(record.get("projectId"), "record project") != expected.target_project_id
                or integer(record.get("mergeRequest"), "record MR") != expected.merge_request
            ):
                continue
            note_id = integer(record.get("sourceNoteId"), "source note ID")
            if note_id in processed:
                continue
            targets = record.get("targets", [])
            if not isinstance(targets, list) or not all(
                isinstance(target, str) for target in targets
            ):
                continue
            parsed = {
                ApprovalTarget.from_key(string(target, "approval target")) for target in targets
            }
            if record.get("operation") == "add":
                approvals.update(parsed)
            elif record.get("operation") == "remove":
                approvals.difference_update(parsed)
            processed.add(note_id)
        except (TypeError, ValueError):
            continue
    return approvals, processed


@dataclass(frozen=True)
class ApprovalAction:
    """Carry an authenticated command decision for the immutable ledger."""

    note_id: int
    actor: int
    operation: str
    targets: set[ApprovalTarget]
    message: str


def _record(api: HttpGitLab, expected: Expectation, action: ApprovalAction) -> None:
    record: dict[str, JsonValue] = {
        "version": 1,
        "projectId": expected.target_project_id,
        "mergeRequest": expected.merge_request,
        "sourceNoteId": action.note_id,
        "actorId": action.actor,
        "operation": action.operation,
        "targets": [str(target) for target in sorted(t.key for t in action.targets)],
    }
    marker = (
        "<!-- quality-graph:gitlab:record:"
        + json.dumps(record, sort_keys=True, separators=(",", ":"))
        + " -->"
    )
    api.request(
        "POST",
        f"/projects/{expected.target_project_id}/merge_requests/{expected.merge_request}/notes",
        {
            "body": f"{action.message}\n\n{marker}",
        },
    )


def _authorized(api: HttpGitLab, project: int, actor: int, graph: Graph) -> bool:
    try:
        membership = object_value(
            api.request("GET", f"/projects/{project}/members/all/{actor}"), "member"
        )
    except GitLabError as error:
        if error.status in {403, 404}:
            return False
        raise
    minimum = min(
        {"admin": OWNER_ACCESS, "maintain": MAINTAINER_ACCESS, "write": DEVELOPER_ACCESS}[role]
        for role in graph.administrator_roles
    )
    access = membership.get("access_level")
    return isinstance(access, int) and not isinstance(access, bool) and access >= minimum


def _command_targets(
    command: str, arguments: str | None, available: frozenset[ApprovalTarget]
) -> set[ApprovalTarget]:
    if arguments is None:
        return set()
    selected: set[ApprovalTarget] = set()
    for argument in arguments.split(","):
        matches = {
            target
            for target in available
            if target.target == argument
            and (
                target.kind is ControlKind.FILE
                if command.endswith("file")
                else target.kind in {ControlKind.FINDING, ControlKind.NODE}
            )
        }
        if not matches:
            return set()
        selected.update(matches)
    return selected


def _commands(
    api: HttpGitLab,
    expected: Expectation,
    evidence: Mapping[str, NodeEvidence],
    bot: int,
    notes: tuple[dict[str, JsonValue], ...],
) -> set[ApprovalTarget]:
    approvals, processed = _ledger(notes, bot, expected)
    results = {key: item.result for key, item in evidence.items() if item.result is not None}
    available = effective_graph(expected.graph, results, approvals).targets
    for note in sorted(notes, key=lambda item: integer(item.get("id"), "note ID")):
        note_id = integer(note.get("id"), "note ID")
        body = note.get("body")
        if note_id in processed or _owned(note, bot) or not isinstance(body, str):
            continue
        match = COMMAND_RE.fullmatch(body.strip())
        if match is None:
            continue
        fresh = object_value(
            api.request(
                "GET",
                f"/projects/{expected.target_project_id}/merge_requests/{expected.merge_request}/notes/{note_id}",
            ),
            "command note",
        )
        if fresh.get("body") != body or fresh.get("created_at") != fresh.get("updated_at"):
            continue
        actor = integer(object_value(fresh.get("author"), "command author").get("id"), "author ID")
        command = match.group(1) or "status"
        if command in {"help", "status"}:
            message = (
                "Quality Graph uses the managed summary for current results. "
                "Use `/qg ignore <target>` or `/qg remove-ignore <target>` "
                "for reversible approvals."
            )
            _record(api, expected, ApprovalAction(note_id, actor, "ack", set(), message))
            continue
        if not _authorized(api, expected.target_project_id, actor, expected.graph):
            _record(
                api,
                expected,
                ApprovalAction(
                    note_id,
                    actor,
                    "ack",
                    set(),
                    "This command requires a configured Quality Graph administrator role.",
                ),
            )
            continue
        targets = _command_targets(command, match.group(2), available)
        if not targets:
            _record(
                api,
                expected,
                ApprovalAction(
                    note_id,
                    actor,
                    "ack",
                    set(),
                    "No matching approvable targets exist in the current admitted results.",
                ),
            )
            continue
        remove = command.startswith("remove-")
        operation = "remove" if remove else "add"
        _record(
            api,
            expected,
            ApprovalAction(
                note_id,
                actor,
                operation,
                targets,
                f"Quality Graph recorded approval operation `{operation}` from user {actor}.",
            ),
        )
        if remove:
            approvals.difference_update(targets)
        else:
            approvals.update(targets)
    return approvals


def _state(
    graph: Graph, evidence: Mapping[str, NodeEvidence], results: Mapping[str, Result]
) -> str:
    bootstrap = evidence.get(BOOTSTRAP_KEY)
    if bootstrap is None or bootstrap.status is ResultStatus.FAILED:
        return "failed"
    if any(
        item.status in {ResultStatus.IN_PROGRESS, ResultStatus.WAITING}
        for item in evidence.values()
    ):
        return "pending"
    canceled = any(item.status is ResultStatus.CANCELLED for item in evidence.values())
    for node in graph.nodes:
        item = evidence[node.id]
        if item.status is ResultStatus.CANCELLED:
            continue
        if canceled and item.status is ResultStatus.SKIPPED:
            continue
        effective = results.get(node.id)
        if effective is None or (
            node.policy.blocking
            and effective.status not in {ResultStatus.PASSED, ResultStatus.SKIPPED}
        ):
            return "failed"
    return "canceled" if canceled else "success"


def _summary(
    expected: Expectation,
    evidence: Mapping[str, NodeEvidence],
    results: Mapping[str, Result],
    state: str,
) -> str:
    digest = graph_digest(expected.declaration)
    lines = [
        SUMMARY_MARKER,
        gate_marker(expected.project_id, expected.pipeline_id, expected.flow_id, digest),
        *(
            execution_marker(expected.project_id, expected.pipeline_id, item.job_id, digest)
            for item in evidence.values()
            if item.job_id is not None
        ),
        "## Quality Graph",
        "",
        f"Pipeline **{expected.pipeline_id}**: **{state}**",
        "",
        "| Check | Execution | Effective result |",
        "| --- | --- | --- |",
    ]
    details: list[str] = []
    bootstrap = evidence.get(BOOTSTRAP_KEY)
    if bootstrap is not None and bootstrap.reason:
        details.extend(("", f"**Publisher admission:** {html.escape(bootstrap.reason)}"))
    for node in expected.graph.nodes:
        item = evidence[node.id]
        result = results.get(node.id)
        label = html.escape(node.title).replace("|", "\\|")
        effective = result.status.value if result is not None else item.status.value
        lines.append(f"| {label} | {item.native_status or item.status.value} | {effective} |")
        if item.reason:
            details.extend(("", f"**{label}:** {html.escape(item.reason)}"))
        if result is not None:
            for finding in result.findings[:100]:
                details.extend(("", f"- `{finding.id}`: {html.escape(finding.message)}"))
        if item.job_url:
            details.extend(("", f"[Job logs: {label}]({item.job_url})"))
    lines.extend(details)
    lines.extend(("", "Use new `/qg` comments to approve or revoke findings, files and nodes."))
    return "\n".join(lines)[:900_000]


def _still_current(api: HttpGitLab, expected: Expectation) -> bool:
    mr = object_value(
        api.request(
            "GET", f"/projects/{expected.target_project_id}/merge_requests/{expected.merge_request}"
        ),
        "merge request",
    )
    head = mr.get("head_pipeline")
    return (
        mr.get("state") == "opened"
        and mr.get("sha") == expected.head_sha
        and isinstance(head, dict)
        and head.get("id") == expected.pipeline_id
    )


def _status_identity(value: Mapping[str, JsonValue], expected: Expectation, bot: int) -> bool:
    if value.get("name") != STATUS_NAME:
        return False
    if (
        integer(value.get("pipeline_id"), "status pipeline") != expected.pipeline_id
        or value.get("sha") != expected.head_sha
    ):
        return False
    author = object_value(value.get("author"), "status author")
    if integer(author.get("id"), "status author ID") != bot:
        message = "The Quality Graph status is owned by another GitLab actor"
        raise ValueError(message)
    return True


def _status(api: HttpGitLab, expected: Expectation, state: str, bot: int) -> bool:
    path = (
        f"/projects/{expected.project_id}/repository/commits/{expected.head_sha}/statuses"
        f"?pipeline_id={expected.pipeline_id}"
    )
    matches = [value for value in api.pages(path) if _status_identity(value, expected, bot)]
    if len(matches) > 1:
        message = "GitLab returned ambiguous current Quality Graph statuses"
        raise ValueError(message)
    if matches and matches[0].get("status") == state:
        return False
    created = object_value(
        api.request(
            "POST",
            f"/projects/{expected.project_id}/statuses/{expected.head_sha}",
            {
                "state": state,
                "name": STATUS_NAME,
                "pipeline_id": expected.pipeline_id,
                "description": "Quality Graph admitted result policy",
            },
        ),
        "created status",
    )
    if not _status_identity(created, expected, bot) or created.get("status") != state:
        message = "GitLab did not confirm the requested pipeline status"
        raise ValueError(message)
    return True


def _upsert(
    api: HttpGitLab,
    expected: Expectation,
    body: str,
    notes: tuple[dict[str, JsonValue], ...],
    bot: int,
) -> bool:
    managed = [
        note
        for note in notes
        if _owned(note, bot)
        and isinstance(note.get("body"), str)
        and SUMMARY_MARKER in str(note["body"])
    ]
    if len(managed) > 1:
        message = "GitLab has ambiguous publisher-owned summary notes"
        raise ValueError(message)
    endpoint = (
        f"/projects/{expected.target_project_id}/merge_requests/{expected.merge_request}/notes"
    )
    if managed:
        if managed[0].get("body") == body:
            return False
        api.request("PUT", f"{endpoint}/{integer(managed[0].get('id'), 'note ID')}", {"body": body})
    else:
        api.request("POST", endpoint, {"body": body})
    return True


def _labels(
    api: HttpGitLab, expected: Expectation, results: Mapping[str, Result], state: str
) -> None:
    selected: dict[str, tuple[LabelSpec, bool]] = {}
    if expected.graph.labels.enabled and expected.graph.labels.failing is not None:
        spec = expected.graph.labels.failing
        selected[spec.name] = (spec, state == "failed")
    for node in expected.graph.nodes:
        if isinstance(node.failing_label, LabelSpec):
            result = results.get(node.id)
            selected[node.failing_label.name] = (
                node.failing_label,
                result is not None and result.status is ResultStatus.FAILED,
            )
    if not selected:
        return
    project = expected.target_project_id
    existing = {
        label.get("name")
        for label in api.pages(f"/projects/{project}/labels")
        if isinstance(label.get("name"), str)
    }
    for name, (spec, _enabled) in selected.items():
        if name not in existing:
            api.request(
                "POST",
                f"/projects/{project}/labels",
                {"name": name, "color": f"#{spec.color}", "description": spec.description},
            )
    mr = object_value(
        api.request("GET", f"/projects/{project}/merge_requests/{expected.merge_request}"),
        "merge request",
    )
    current = mr.get("labels", [])
    if not isinstance(current, list):
        message = "GitLab labels must be an array"
        raise TypeError(message)
    add = [name for name, (_spec, enabled) in selected.items() if enabled and name not in current]
    remove = [
        name for name, (_spec, enabled) in selected.items() if not enabled and name in current
    ]
    if add or remove:
        api.request(
            "PUT",
            f"/projects/{project}/merge_requests/{expected.merge_request}",
            {"add_labels": ",".join(add), "remove_labels": ",".join(remove)},
        )


def expectation(api: HttpGitLab, project: int, mr_id: int) -> Expectation | None:
    """Resolve governance from the target branch and execution from current MR metadata."""
    mr = object_value(
        api.request("GET", f"/projects/{project}/merge_requests/{mr_id}"), "merge request"
    )
    head = mr.get("head_pipeline")
    if mr.get("state") != "opened" or not isinstance(head, dict):
        return None
    execution_project = integer(head.get("project_id", project), "pipeline project")
    pipeline_id = integer(head.get("id"), "pipeline ID")
    pipeline = object_value(
        api.request("GET", f"/projects/{execution_project}/pipelines/{pipeline_id}"), "MR pipeline"
    )
    if pipeline.get("source") != "merge_request_event":
        return None
    if (
        pipeline.get("sha") != mr.get("sha")
        or pipeline.get("project_id") != execution_project
        or pipeline.get("id") != pipeline_id
    ):
        message = "GitLab MR pipeline metadata is stale or mismatched"
        raise ValueError(message)
    branch = urllib.parse.quote(string(mr.get("target_branch"), "target branch"), safe="")
    target = object_value(
        api.request("GET", f"/projects/{project}/repository/branches/{branch}"), "target branch"
    )
    if target.get("protected") is not True:
        message = "GitLab publication requires a protected MR target branch"
        raise ValueError(message)
    target_sha = string(object_value(target.get("commit"), "target commit").get("id"), "target SHA")
    source = api.download(f"/projects/{project}/repository/files/qg.yaml/raw?ref={target_sha}")
    graph = Graph.from_yaml(source.decode())
    if configuration(graph)["server-url"] != api.server_url:
        message = "GitLab declaration targets another server instance"
        raise ValueError(message)
    if configuration(graph)["default-branch"] != mr.get("target_branch"):
        return None
    for flow_id, event, projected in execution_graphs(graph):
        if event == "pull-request" and publishes_mr(graph, flow_id):
            return Expectation(
                execution_project,
                project,
                mr_id,
                pipeline_id,
                string(mr.get("sha"), "MR SHA"),
                flow_id,
                graph,
                projected,
            )
    return None


def publish_merge_request(api: HttpGitLab, project: int, mr_id: int) -> Publication | None:
    """Publish one current MR snapshot after authenticating all evidence and commands."""
    expected = expectation(api, project, mr_id)
    if expected is None:
        return None
    bot = integer(
        object_value(api.request("GET", "/user"), "publisher user").get("id"), "publisher user ID"
    )
    if configuration(expected.declaration).get("publisher-user-id") != bot:
        message = "GitLab publisher identity does not match trusted configuration"
        raise ValueError(message)
    notes = _notes(api, project, mr_id)
    evidence = admit(api, expected)
    if not _still_current(api, expected):
        return None
    approvals = _commands(api, expected, evidence, bot, notes)
    admitted = {key: item.result for key, item in evidence.items() if item.result is not None}
    effective = effective_graph(expected.graph, admitted, approvals)
    state = _state(expected.graph, evidence, effective.results)
    if not _still_current(api, expected):
        return None
    changed = _status(api, expected, state, bot)
    if not _still_current(api, expected):
        return None
    changed = (
        _upsert(api, expected, _summary(expected, evidence, effective.results, state), notes, bot)
        or changed
    )
    _labels(api, expected, effective.results, state)
    return Publication(project, mr_id, expected.pipeline_id, state, changed)


def publish_from_environment() -> int:
    """Reconcile only projects explicitly allowed by trusted publisher configuration."""
    projects = json.loads(os.environ["QG_GITLAB_PROJECTS"])
    if not isinstance(projects, list) or not projects:
        message = "QG_GITLAB_PROJECTS must be a nonempty array of project IDs"
        raise ValueError(message)
    failures = 0
    server = os.environ.get("QG_GITLAB_SERVER_URL", os.environ.get("CI_SERVER_URL", ""))
    endpoint = os.environ.get("QG_GITLAB_API_URL")
    if endpoint is None and "QG_GITLAB_SERVER_URL" not in os.environ:
        endpoint = os.environ.get("CI_API_V4_URL")
    with HttpGitLab(
        server,
        os.environ["QG_GITLAB_TOKEN"],
        api_url=endpoint,
    ) as api:
        for project in projects:
            project_id = integer(project, "allowed project ID")
            for mr in api.pages(f"/projects/{project_id}/merge_requests?state=opened"):
                try:
                    outcome = publish_merge_request(
                        api, project_id, integer(mr.get("iid"), "MR IID")
                    )
                    if outcome is not None:
                        sys.stdout.write(
                            f"project={outcome.project} mr={outcome.merge_request} "
                            f"pipeline={outcome.pipeline} state={outcome.state}\n"
                        )
                except (GitLabError, OSError, TypeError, ValueError) as error:
                    failures += 1
                    sys.stderr.write(f"GitLab publication failed: {error}\n")
    return int(failures != 0)
