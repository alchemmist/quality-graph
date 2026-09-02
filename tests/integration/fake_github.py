"""Run a deterministic local fake of the GitHub endpoints used by Quality Graph."""

from __future__ import annotations

import base64
import json
import threading
import urllib.parse
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, ClassVar, cast, override

if TYPE_CHECKING:
    from types import TracebackType

    from quality_graph_core.result import JsonValue


@dataclass
class FakeGitHubState:
    """Store observable repository state served over real HTTP."""

    graph: str
    artifacts: list[dict[str, JsonValue]]
    downloads: dict[int, bytes]
    comments: list[dict[str, JsonValue]] = field(default_factory=list)
    checks: list[dict[str, JsonValue]] = field(default_factory=list)
    job_snapshots: list[list[dict[str, JsonValue]]] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)
    repository_labels: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    reactions: list[tuple[int, str]] = field(default_factory=list)
    reruns: list[int] = field(default_factory=list)
    next_comment_id: int = 100


class FakeGitHubHandler(BaseHTTPRequestHandler):
    """Handle the constrained GitHub surface exercised by integration tests."""

    state: ClassVar[FakeGitHubState]

    @override
    def do_GET(self) -> None:
        """Serve repository, Actions, comment, label, and permission reads."""
        path = _repository_path(self.path)
        if path == "/pulls/42":
            self._json({"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}})
        elif path.startswith("/contents/qg.yaml"):
            self._json({"content": base64.b64encode(self.state.graph.encode()).decode()})
        elif path.startswith("/actions/"):
            self._get_actions(path)
        elif path.startswith(f"/commits/{'a' * 40}/check-runs"):
            self._json({"total_count": len(self.state.checks), "check_runs": self.state.checks})
        elif path.startswith("/issues/42/comments"):
            self._json(self.state.comments)
        elif path.startswith("/issues/42/labels"):
            self._json([{"name": name} for name in sorted(self.state.labels)])
        elif path.startswith("/labels/"):
            self._get_label(path)
        elif path == "/collaborators/admin/permission":
            self._json({"permission": "admin"})
        else:
            self._unknown("GET", path)

    @override
    def do_POST(self) -> None:
        """Serve managed state, checks, labels, reruns, and reactions."""
        path = _repository_path(self.path)
        payload = self._payload()
        if path == "/issues/42/comments":
            self._create_comment(payload)
        elif path == "/check-runs":
            check = payload | {"id": len(self.state.checks) + 1}
            self.state.checks.append(check)
            self._json(check, status=HTTPStatus.CREATED)
        elif path == "/issues/42/labels":
            labels = cast("list[JsonValue]", payload["labels"])
            self.state.labels.update(cast("str", label) for label in labels)
            self._json([{"name": name} for name in sorted(self.state.labels)])
        elif path == "/labels":
            name = cast("str", payload["name"])
            self.state.repository_labels[name] = payload
            self._json(payload, status=HTTPStatus.CREATED)
        elif path == "/actions/runs/10/rerun-failed-jobs":
            self.state.reruns.append(10)
            self._empty()
        elif path.startswith("/issues/comments/") and path.endswith("/reactions"):
            comment_id = int(path.split("/")[3])
            self.state.reactions.append((comment_id, cast("str", payload["content"])))
            self._json({"id": len(self.state.reactions)}, status=HTTPStatus.CREATED)
        else:
            self._unknown("POST", path)

    @override
    def do_PATCH(self) -> None:
        """Update a known managed comment."""
        path = _repository_path(self.path)
        payload = self._payload()
        if path.startswith("/issues/comments/"):
            comment_id = int(path.split("/")[3])
            comment = next(item for item in self.state.comments if item["id"] == comment_id)
            comment["body"] = payload["body"]
            self._json(comment)
        elif path.startswith("/check-runs/"):
            check_id = int(path.split("/")[2])
            check = next(item for item in self.state.checks if item["id"] == check_id)
            check.update(payload)
            self._json(check)
        else:
            self._unknown("PATCH", path)

    @override
    def do_DELETE(self) -> None:
        """Remove one Quality Graph-owned label."""
        path = _repository_path(self.path)
        if path.startswith("/issues/42/labels/"):
            name = urllib.parse.unquote(path.removeprefix("/issues/42/labels/"))
            self.state.labels.discard(name)
            self._empty()
        else:
            self._unknown("DELETE", path)

    @override
    def log_message(self, _format_value: str, *_arguments: object) -> None:
        """Keep deterministic tests free from server access logs."""

    def _get_actions(self, path: str) -> None:
        if path.startswith("/actions/workflows/quality-graph.yml/runs"):
            self._json(
                {"workflow_runs": [{"id": 10, "run_attempt": 1, "pull_requests": [{"number": 42}]}]}
            )
        elif path.startswith("/actions/runs/10/artifacts"):
            self._json({"artifacts": self.state.artifacts})
        elif path.startswith("/actions/runs/10/jobs"):
            snapshots = self.state.job_snapshots
            jobs = snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]
            self._json({"total_count": len(jobs), "jobs": jobs})
        elif path.startswith("/actions/artifacts/") and path.endswith("/zip"):
            artifact_id = int(path.split("/")[3])
            self._bytes(self.state.downloads[artifact_id])
        else:
            self._unknown("GET", path)

    def _get_label(self, path: str) -> None:
        name = urllib.parse.unquote(path.removeprefix("/labels/"))
        label = self.state.repository_labels.get(name)
        status = HTTPStatus.OK if label is not None else HTTPStatus.NOT_FOUND
        self._json(label, status=status)

    def _create_comment(self, payload: dict[str, JsonValue]) -> None:
        comment: dict[str, JsonValue] = {
            "id": self.state.next_comment_id,
            "body": payload["body"],
            "user": {"login": "github-actions[bot]"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        self.state.next_comment_id += 1
        self.state.comments.append(comment)
        self._json(comment, status=HTTPStatus.CREATED)

    def _payload(self) -> dict[str, JsonValue]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        value = cast("JsonValue", json.loads(self.rfile.read(length)))
        if not isinstance(value, dict):
            message = "request payload must be an object"
            raise TypeError(message)
        return value

    def _json(self, value: JsonValue, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _bytes(self, value: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(value)))
        self.end_headers()
        self.wfile.write(value)

    def _empty(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _unknown(self, method: str, path: str) -> None:
        self._json({"message": f"unknown {method} {path}"}, status=HTTPStatus.NOT_FOUND)


class FakeGitHubServer(AbstractContextManager[FakeGitHubState]):
    """Serve one fake repository in a background HTTP thread."""

    def __init__(self, state: FakeGitHubState) -> None:
        """Bind a state object before starting the server."""
        self.state = state
        handler = type("BoundFakeGitHubHandler", (FakeGitHubHandler,), {"state": state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        """Return the local API base URL."""
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @override
    def __enter__(self) -> FakeGitHubState:
        """Start serving and return mutable observable state."""
        self.thread.start()
        return self.state

    @override
    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Stop the server and wait for its thread."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        return None


def _repository_path(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    prefix = "/repos/owner/repository"
    path = parsed.path.removeprefix(prefix)
    return f"{path}?{parsed.query}" if parsed.query else path
