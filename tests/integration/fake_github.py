"""Run a deterministic stateful fake of the GitHub REST surface."""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.parse
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, ClassVar, cast, override

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from quality_graph_core.result import JsonValue

DEFAULT_REPOSITORY = "owner/repository"
DEFAULT_BOT_LOGIN = "github-actions[bot]"
REPOSITORY_PATH = re.compile(r"^/repos/(?P<repository>[^/]+/[^/]+)(?P<path>/.*)$")


@dataclass
class FakeGitHubState:
    """Store observable repository state for integration scenarios."""

    graph: str = ""
    artifacts: list[dict[str, JsonValue]] = field(default_factory=list)
    downloads: dict[int, bytes] = field(default_factory=dict)
    repository: str = DEFAULT_REPOSITORY
    bot_login: str = DEFAULT_BOT_LOGIN
    pulls: dict[int, dict[str, JsonValue]] = field(default_factory=dict)
    commit_pulls: dict[str, list[dict[str, JsonValue]]] = field(default_factory=dict)
    comments: dict[int, dict[str, JsonValue]] = field(default_factory=dict)
    checks: dict[int, dict[str, JsonValue]] = field(default_factory=dict)
    labels: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    issue_labels: dict[int, set[str]] = field(default_factory=dict)
    permissions: dict[str, str] = field(default_factory=dict)
    workflow_runs: list[dict[str, JsonValue]] = field(default_factory=list)
    workflow_jobs: dict[int, list[dict[str, JsonValue]]] = field(default_factory=dict)
    workflow_job_snapshots: dict[int, list[list[dict[str, JsonValue]]]] = field(
        default_factory=dict
    )
    run_artifacts: dict[int, list[dict[str, JsonValue]]] = field(default_factory=dict)
    pull_files: dict[int, list[dict[str, JsonValue]]] = field(default_factory=dict)
    comparisons: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    contents: dict[str, str] = field(default_factory=dict)
    reruns: list[int] = field(default_factory=list)
    reactions: dict[int, dict[str, JsonValue]] = field(default_factory=dict)
    failures: dict[tuple[str, str], int] = field(default_factory=dict)
    delays: dict[tuple[str, str], float] = field(default_factory=dict)
    requests: list[dict[str, JsonValue]] = field(default_factory=list)
    next_comment_id: int = 1
    next_check_id: int = 1
    next_reaction_id: int = 1

    def __post_init__(self) -> None:
        """Populate the default repository scenario."""
        if self.graph:
            self.contents.setdefault(f"{'d' * 40}:qg.yaml", self.graph)
        if self.artifacts:
            self.run_artifacts.setdefault(10, self.artifacts)
        if not self.pulls:
            self.pulls[42] = {
                "number": 42,
                "body": "",
                "head": {"sha": "a" * 40},
                "base": {"sha": "d" * 40},
            }
        if not self.workflow_runs:
            self.workflow_runs = [{"id": 10, "run_attempt": 1, "pull_requests": [{"number": 42}]}]
        if not self.permissions:
            self.permissions["admin"] = "admin"
        self._advance_identifiers()

    @property
    def job_snapshots(self) -> list[list[dict[str, JsonValue]]]:
        """Expose the default run snapshots for legacy scenarios."""
        return self.workflow_job_snapshots.setdefault(10, [])

    def reset(self, payload: Mapping[str, JsonValue]) -> None:
        """Replace state from one JSON-compatible scenario fixture."""
        fresh = FakeGitHubState(repository=_string(payload.get("repository"), DEFAULT_REPOSITORY))
        fresh.bot_login = _string(payload.get("bot_login"), DEFAULT_BOT_LOGIN)
        fresh.pulls = _objects_by_integer(
            payload.get("pulls", list(fresh.pulls.values())), "number"
        )
        fresh.commit_pulls = _object_lists(payload.get("commit_pulls", {}))
        fresh.comments = _objects_by_integer(payload.get("comments", []), "id")
        fresh.checks = _objects_by_integer(payload.get("checks", []), "id")
        fresh.labels = _objects_by_string(payload.get("labels", []), "name")
        fresh.issue_labels = _integer_sets(payload.get("issue_labels", {}))
        fresh.permissions = _string_mapping(payload.get("permissions", {}))
        fresh.workflow_runs = _object_list(payload.get("workflow_runs", fresh.workflow_runs))
        fresh.workflow_jobs = _integer_object_lists(payload.get("workflow_jobs", {}))
        fresh.workflow_job_snapshots = _job_snapshots(payload.get("workflow_job_snapshots", {}))
        fresh.run_artifacts = _integer_object_lists(payload.get("run_artifacts", {}))
        fresh.pull_files = _integer_object_lists(payload.get("pull_files", {}))
        fresh.comparisons = _object_mapping(payload.get("comparisons", {}))
        fresh.contents = _string_mapping(payload.get("contents", {}))
        fresh.downloads = {
            int(identifier): base64.b64decode(value)
            for identifier, value in _string_mapping(payload.get("downloads", {})).items()
        }
        fresh.failures = _failures(payload.get("failures", []))
        fresh.delays = _delays(payload.get("request_delays", []))
        fresh._advance_identifiers()
        self.__dict__.update(fresh.__dict__)

    def snapshot(self) -> dict[str, JsonValue]:
        """Return complete observable state as JSON-compatible data."""
        return {
            "repository": self.repository,
            "pulls": cast("JsonValue", list(self.pulls.values())),
            "comments": cast("JsonValue", list(self.comments.values())),
            "checks": cast("JsonValue", list(self.checks.values())),
            "labels": cast("JsonValue", list(self.labels.values())),
            "issue_labels": {
                str(number): cast("JsonValue", sorted(labels))
                for number, labels in self.issue_labels.items()
            },
            "permissions": cast("JsonValue", self.permissions),
            "workflow_runs": cast("JsonValue", self.workflow_runs),
            "workflow_jobs": {
                str(identifier): cast("JsonValue", jobs)
                for identifier, jobs in self.workflow_jobs.items()
            },
            "reruns": cast("JsonValue", self.reruns),
            "reactions": cast("JsonValue", list(self.reactions.values())),
            "requests": cast("JsonValue", self.requests),
        }

    def record(self, method: str, path: str) -> None:
        """Record one request for ordering and budget assertions."""
        self.requests.append({"method": method, "path": path})

    def _advance_identifiers(self) -> None:
        self.next_comment_id = max(self.comments, default=0) + 1
        self.next_check_id = max(self.checks, default=0) + 1
        self.next_reaction_id = max(self.reactions, default=0) + 1


class FakeGitHubHandler(BaseHTTPRequestHandler):
    """Serve the GitHub REST subset exercised by Quality Graph."""

    state: ClassVar[FakeGitHubState]
    state_lock: ClassVar[threading.RLock]

    @override
    def do_GET(self) -> None:
        self._dispatch("GET")

    @override
    def do_POST(self) -> None:
        self._dispatch("POST")

    @override
    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    @override
    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    @override
    def log_message(self, _format_value: str, *_arguments: object) -> None:
        return

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        payload = self._payload()
        repository_match = REPOSITORY_PATH.fullmatch(parsed.path)
        with self.state_lock:
            if repository_match is not None:
                self.state.record(method, parsed.path)
            delay = self.state.delays.pop((method, parsed.path), 0.0)
        if delay:
            time.sleep(delay)
        with self.state_lock:
            failure = self.state.failures.get((method, parsed.path))
            if failure is not None:
                self._json({"message": "configured failure"}, status=HTTPStatus(failure))
                return
            response = self._special(method, parsed.path, payload)
            if (
                response is None
                and repository_match is not None
                and repository_match.group("repository") == self.state.repository
            ):
                response = self._repository(
                    method,
                    repository_match.group("path"),
                    payload,
                    urllib.parse.parse_qs(parsed.query),
                )
            if response is None:
                self._unknown(method, parsed.path)
            else:
                self._send(*response)

    def _special(
        self, method: str, path: str, payload: JsonValue
    ) -> tuple[HTTPStatus, JsonValue | bytes] | None:
        if method == "GET" and path == "/health":
            return HTTPStatus.OK, {"status": "ok"}
        if method == "POST" and path == "/_test/reset":
            self.state.reset(_object(payload))
            return HTTPStatus.NO_CONTENT, None
        if method == "GET" and path == "/_test/state":
            return HTTPStatus.OK, self.state.snapshot()
        if method == "GET" and path == "/user":
            return HTTPStatus.OK, {"login": self.state.bot_login}
        return None

    def _repository(
        self,
        method: str,
        path: str,
        payload: JsonValue,
        query: dict[str, list[str]],
    ) -> tuple[HTTPStatus, JsonValue | bytes] | None:
        for response in (
            self._pull_routes(method, path, payload, query),
            self._comment_routes(method, path, payload, query),
            self._label_routes(method, path, payload, query),
            self._action_routes(method, path, query),
            self._check_routes(method, path, payload, query),
        ):
            if response is not None:
                return response
        return None

    def _pull_routes(
        self, method: str, path: str, payload: JsonValue, query: dict[str, list[str]]
    ) -> tuple[HTTPStatus, JsonValue | bytes] | None:
        if match := re.fullmatch(r"/pulls/(?P<number>\d+)", path):
            pull = self.state.pulls.get(int(match.group("number")))
            if method == "GET":
                return _optional(pull)
            if method == "PATCH" and pull is not None:
                pull.update(_object(payload))
                return HTTPStatus.OK, pull
        if method == "GET" and (match := re.fullmatch(r"/pulls/(?P<number>\d+)/files", path)):
            return _page(self.state.pull_files.get(int(match.group("number")), []), query)
        if method == "GET" and (match := re.fullmatch(r"/commits/(?P<sha>[^/]+)/pulls", path)):
            return HTTPStatus.OK, self.state.commit_pulls.get(match.group("sha"), [])
        if method == "GET" and (match := re.fullmatch(r"/compare/(?P<reference>.+)", path)):
            return _optional(
                self.state.comparisons.get(urllib.parse.unquote(match.group("reference")))
            )
        if method == "GET" and (match := re.fullmatch(r"/contents/(?P<path>.+)", path)):
            ref = query.get("ref", [""])[0]
            content = self.state.contents.get(f"{ref}:{urllib.parse.unquote(match.group('path'))}")
            if content is None:
                return HTTPStatus.NOT_FOUND, {"message": "not found"}
            return HTTPStatus.OK, {"content": base64.b64encode(content.encode()).decode()}
        return None

    def _comment_routes(
        self, method: str, path: str, payload: JsonValue, query: dict[str, list[str]]
    ) -> tuple[HTTPStatus, JsonValue | bytes] | None:
        if match := re.fullmatch(r"/issues/(?P<number>\d+)/comments", path):
            number = int(match.group("number"))
            if method == "GET":
                return _page(
                    [
                        value
                        for value in self.state.comments.values()
                        if value.get("issue_number") == number
                    ],
                    query,
                )
            if method == "POST":
                comment = {
                    "id": self.state.next_comment_id,
                    "issue_number": number,
                    "body": _object(payload).get("body"),
                    "user": {"login": self.state.bot_login},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
                self.state.comments[self.state.next_comment_id] = comment
                self.state.next_comment_id += 1
                return HTTPStatus.CREATED, comment
        if match := re.fullmatch(r"/issues/comments/(?P<identifier>\d+)", path):
            identifier = int(match.group("identifier"))
            comment = self.state.comments.get(identifier)
            if method == "GET":
                return _optional(comment)
            if method == "PATCH" and comment is not None:
                comment.update(_object(payload))
                return HTTPStatus.OK, comment
            if method == "DELETE" and comment is not None:
                del self.state.comments[identifier]
                return HTTPStatus.NO_CONTENT, None
        if match := re.fullmatch(
            r"/issues/comments/(?P<identifier>\d+)/reactions(?:/(?P<reaction>\d+))?", path
        ):
            comment_id = int(match.group("identifier"))
            reaction_id = match.group("reaction")
            if method == "GET":
                return _page(
                    [
                        value
                        for value in self.state.reactions.values()
                        if value.get("comment_id") == comment_id
                    ],
                    query,
                )
            if method == "POST":
                reaction = {
                    "id": self.state.next_reaction_id,
                    "comment_id": comment_id,
                    "content": _object(payload).get("content"),
                    "user": {"login": self.state.bot_login},
                }
                self.state.reactions[self.state.next_reaction_id] = reaction
                self.state.next_reaction_id += 1
                return HTTPStatus.CREATED, reaction
            if method == "DELETE" and reaction_id is not None:
                self.state.reactions.pop(int(reaction_id), None)
                return HTTPStatus.NO_CONTENT, None
        return None

    def _label_routes(
        self, method: str, path: str, payload: JsonValue, query: dict[str, list[str]]
    ) -> tuple[HTTPStatus, JsonValue | bytes] | None:
        if method == "POST" and path == "/labels":
            label = _object(payload)
            self.state.labels[str(label.get("name"))] = label
            return HTTPStatus.CREATED, label
        if method == "GET" and (match := re.fullmatch(r"/labels/(?P<name>.+)", path)):
            return _optional(self.state.labels.get(urllib.parse.unquote(match.group("name"))))
        if match := re.fullmatch(r"/issues/(?P<number>\d+)/labels(?:/(?P<name>.+))?", path):
            labels = self.state.issue_labels.setdefault(int(match.group("number")), set())
            if method == "GET":
                return _page([{"name": name} for name in sorted(labels)], query)
            if method == "POST":
                labels.update(str(name) for name in _array(_object(payload).get("labels", [])))
                return HTTPStatus.OK, [{"name": name} for name in sorted(labels)]
            if method == "DELETE" and match.group("name") is not None:
                labels.discard(urllib.parse.unquote(match.group("name")))
                return HTTPStatus.NO_CONTENT, None
        if method == "GET" and (
            match := re.fullmatch(r"/collaborators/(?P<login>[^/]+)/permission", path)
        ):
            permission = self.state.permissions.get(match.group("login"))
            return _optional(None if permission is None else {"permission": permission})
        return None

    def _action_routes(
        self, method: str, path: str, query: dict[str, list[str]]
    ) -> tuple[HTTPStatus, JsonValue | bytes] | None:
        if method == "GET" and re.fullmatch(r"/actions/workflows/[^/]+/runs", path):
            status, page = _page(self.state.workflow_runs, query)
            return status, {"total_count": len(self.state.workflow_runs), "workflow_runs": page}
        if method == "GET" and (match := re.fullmatch(r"/actions/runs/(?P<id>\d+)/jobs", path)):
            identifier = int(match.group("id"))
            snapshots = self.state.workflow_job_snapshots.get(identifier)
            jobs = (
                snapshots.pop(0)
                if snapshots is not None and len(snapshots) > 1
                else snapshots[0]
                if snapshots
                else self.state.workflow_jobs.get(identifier, [])
            )
            status, page = _page(jobs, query)
            return status, {"total_count": len(jobs), "jobs": page}
        if method == "GET" and (
            match := re.fullmatch(r"/actions/runs/(?P<id>\d+)/artifacts", path)
        ):
            values = self.state.run_artifacts.get(int(match.group("id")), [])
            status, page = _page(values, query)
            return status, {"total_count": len(values), "artifacts": page}
        if method == "GET" and (match := re.fullmatch(r"/actions/artifacts/(?P<id>\d+)/zip", path)):
            return _optional(self.state.downloads.get(int(match.group("id"))))
        if method == "POST" and (
            match := re.fullmatch(r"/actions/runs/(?P<id>\d+)/rerun-failed-jobs", path)
        ):
            self.state.reruns.append(int(match.group("id")))
            return HTTPStatus.NO_CONTENT, None
        return None

    def _check_routes(
        self, method: str, path: str, payload: JsonValue, query: dict[str, list[str]]
    ) -> tuple[HTTPStatus, JsonValue | bytes] | None:
        if method == "GET" and re.fullmatch(r"/commits/[^/]+/check-runs", path):
            name = query.get("check_name", [None])[0]
            values = [
                check
                for check in self.state.checks.values()
                if name is None or check.get("name") == name
            ]
            return HTTPStatus.OK, {"total_count": len(values), "check_runs": values}
        if method == "POST" and path == "/check-runs":
            check = _object(payload) | {"id": self.state.next_check_id}
            self.state.checks[self.state.next_check_id] = check
            self.state.next_check_id += 1
            return HTTPStatus.CREATED, check
        if method == "PATCH" and (match := re.fullmatch(r"/check-runs/(?P<id>\d+)", path)):
            check = self.state.checks.get(int(match.group("id")))
            if check is None:
                return HTTPStatus.NOT_FOUND, {"message": "not found"}
            check.update(_object(payload))
            return HTTPStatus.OK, check
        return None

    def _payload(self) -> JsonValue:
        length = int(self.headers.get("Content-Length", "0"))
        return None if length == 0 else cast("JsonValue", json.loads(self.rfile.read(length)))

    def _send(self, status: HTTPStatus, value: JsonValue | bytes) -> None:
        if status == HTTPStatus.NO_CONTENT:
            self.send_response(status)
            self.end_headers()
            return
        content = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header(
            "Content-Type", "application/zip" if isinstance(value, bytes) else "application/json"
        )
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, value: JsonValue, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, value)

    def _unknown(self, method: str, path: str) -> None:
        self._json({"message": f"unknown {method} {path}"}, status=HTTPStatus.NOT_FOUND)


class FakeGitHubScenario:
    """Control one fake GitHub adapter through its public test interface."""

    def __init__(self, base_url: str, state: FakeGitHubState | None = None) -> None:
        """Bind control operations to one adapter URL."""
        self.base_url = base_url
        self.state = state

    def reset(self, payload: Mapping[str, JsonValue] | None = None) -> None:
        response = httpx.post(f"{self.base_url}/_test/reset", json=dict(payload or {}))
        response.raise_for_status()

    def snapshot(self) -> dict[str, JsonValue]:
        response = httpx.get(f"{self.base_url}/_test/state")
        response.raise_for_status()
        return cast("dict[str, JsonValue]", response.json())

    def __getattr__(self, name: str) -> object:
        """Proxy legacy in-process state access during migration."""
        if self.state is None:
            raise AttributeError(name)
        return getattr(self.state, name)


class FakeGitHubServer(AbstractContextManager[FakeGitHubScenario]):
    """Run the fake GitHub adapter in the pytest process."""

    def __init__(self, state: FakeGitHubState | None = None) -> None:
        """Bind one isolated in-process server."""
        self.state = state or FakeGitHubState()
        handler = type(
            "BoundFakeGitHubHandler",
            (FakeGitHubHandler,),
            {"state": self.state, "state_lock": threading.RLock()},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        host, port = self.server.server_address
        self.scenario = FakeGitHubScenario(f"http://{host}:{port}", self.state)

    @property
    def base_url(self) -> str:
        """Return the adapter URL before entering the context manager."""
        return self.scenario.base_url

    @override
    def __enter__(self) -> FakeGitHubScenario:
        self.thread.start()
        return self.scenario

    @override
    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool | None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        return None


def serve() -> None:
    """Run the standalone Docker adapter."""
    state = FakeGitHubState()
    handler = type(
        "StandaloneFakeGitHubHandler",
        (FakeGitHubHandler,),
        {"state": state, "state_lock": threading.RLock()},
    )
    server = ThreadingHTTPServer(
        (
            os.environ.get("FAKE_GITHUB_HOST", "0.0.0.0"),
            int(os.environ.get("FAKE_GITHUB_PORT", "8080")),
        ),
        handler,
    )
    server.serve_forever()


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = "expected object"
        raise TypeError(message)
    return value


def _array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        message = "expected array"
        raise TypeError(message)
    return value


def _string(value: JsonValue, default: str) -> str:
    return value if isinstance(value, str) else default


def _object_list(value: JsonValue) -> list[dict[str, JsonValue]]:
    return [_object(item) for item in _array(value)]


def _objects_by_integer(value: JsonValue, key: str) -> dict[int, dict[str, JsonValue]]:
    return {int(item[key]): item for item in _object_list(value)}


def _objects_by_string(value: JsonValue, key: str) -> dict[str, dict[str, JsonValue]]:
    return {str(item[key]): item for item in _object_list(value)}


def _object_mapping(value: JsonValue) -> dict[str, dict[str, JsonValue]]:
    return {key: _object(item) for key, item in _object(value).items()}


def _object_lists(value: JsonValue) -> dict[str, list[dict[str, JsonValue]]]:
    return {key: _object_list(items) for key, items in _object(value).items()}


def _integer_object_lists(value: JsonValue) -> dict[int, list[dict[str, JsonValue]]]:
    return {int(key): items for key, items in _object_lists(value).items()}


def _integer_sets(value: JsonValue) -> dict[int, set[str]]:
    return {
        int(key): {str(item) for item in _array(items)} for key, items in _object(value).items()
    }


def _string_mapping(value: JsonValue) -> dict[str, str]:
    return {key: item for key, item in _object(value).items() if isinstance(item, str)}


def _job_snapshots(value: JsonValue) -> dict[int, list[list[dict[str, JsonValue]]]]:
    return {
        int(key): [_object_list(snapshot) for snapshot in _array(snapshots)]
        for key, snapshots in _object(value).items()
    }


def _failures(value: JsonValue) -> dict[tuple[str, str], int]:
    return {
        (str(item["method"]).upper(), str(item["path"])): int(item["status"])
        for item in _object_list(value)
    }


def _delays(value: JsonValue) -> dict[tuple[str, str], float]:
    return {
        (str(item["method"]).upper(), str(item["path"])): float(item["seconds"])
        for item in _object_list(value)
    }


def _optional(value: JsonValue | bytes) -> tuple[HTTPStatus, JsonValue | bytes]:
    return (
        (HTTPStatus.NOT_FOUND, {"message": "not found"})
        if value is None
        else (HTTPStatus.OK, value)
    )


def _page(
    values: list[dict[str, JsonValue]], query: dict[str, list[str]]
) -> tuple[HTTPStatus, JsonValue]:
    try:
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["30"])[0])
    except ValueError:
        return HTTPStatus.BAD_REQUEST, {"message": "invalid pagination parameters"}
    if page < 1 or per_page < 1:
        return HTTPStatus.BAD_REQUEST, {"message": "invalid pagination parameters"}
    start = (page - 1) * per_page
    return HTTPStatus.OK, values[start : start + per_page]


if __name__ == "__main__":
    serve()
