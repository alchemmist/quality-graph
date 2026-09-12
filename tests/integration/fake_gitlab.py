"""Run a stateful GitLab HTTP test service with the same local and Docker routes."""

from __future__ import annotations

import base64
import copy
import json
import os
import threading
import urllib.parse
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, ClassVar, cast, override

import httpx

if TYPE_CHECKING:
    from types import TracebackType

    from quality_graph_core.result import JsonValue


@dataclass
class FakeGitLabState:
    """Store API resources and observable mutations for deterministic scenarios."""

    values: dict[str, JsonValue] = field(default_factory=dict)
    requests: list[JsonValue] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def reset(self, values: dict[str, JsonValue]) -> None:
        """Replace only this server's scenario and clear request history."""
        with self.lock:
            self.values = copy.deepcopy(values)
            self.requests = []

    def snapshot(self) -> dict[str, JsonValue]:
        """Return a detached view of externally observable state."""
        with self.lock:
            return {**copy.deepcopy(self.values), "requests": copy.deepcopy(self.requests)}

    def route(self, method: str, path: str, data: JsonValue) -> tuple[int, JsonValue | bytes]:
        """Apply one GitLab request to a scenario without production policy logic."""
        with self.lock:
            self.requests.append({"method": method, "path": path, "body": data})
            failures = self.values.get("failures", {})
            if isinstance(failures, dict):
                failure = failures.get(f"{method} {path.split('?', 1)[0]}")
                if isinstance(failure, int):
                    return failure, {"message": "configured failure"}
            url = urllib.parse.urlsplit(path)
            pieces = url.path.removeprefix("/api/v4/").split("/")
            query = urllib.parse.parse_qs(url.query)
            if pieces == ["user"]:
                return 200, {"id": self.values.get("actor", 7), "username": "publisher"}
            if len(pieces) >= 2 and pieces[0] == "projects":
                return self._project(method, pieces[1:], query, data)
            return 404, {"message": "unknown GitLab route"}

    def _project(
        self, method: str, pieces: list[str], query: dict[str, list[str]], data: JsonValue
    ) -> tuple[int, JsonValue | bytes]:
        project = pieces[0]
        if len(pieces) == 4 and pieces[1] == "pipelines" and pieces[3] == "jobs":
            return self._jobs(project, pieces[2], query)
        if len(pieces) == 4 and pieces[1] == "jobs" and pieces[3] == "artifacts":
            return self._artifact(project, pieces[2])
        if len(pieces) >= 3 and pieces[1] == "merge_requests":
            return self._merge_request(method, pieces, query, data)
        key = "/projects/" + "/".join(pieces)
        resources = self.values.get("resources", {})
        if isinstance(resources, dict) and key in resources:
            resource = resources[key]
            if method == "GET":
                return 200, resource.encode() if key.endswith("/raw") and isinstance(
                    resource, str
                ) else resource
        return self._statuses(pieces, query, data)

    def _statuses(
        self, pieces: list[str], query: dict[str, list[str]], data: JsonValue
    ) -> tuple[int, JsonValue]:
        if len(pieces) >= 4 and pieces[1:3] == ["repository", "commits"]:
            statuses = self.values.get("statuses", [])
            return 200, self._page(statuses if isinstance(statuses, list) else [], query)
        if len(pieces) == 3 and pieces[1] == "statuses" and isinstance(data, dict):
            statuses = self.values.setdefault("statuses", [])
            if isinstance(statuses, list):
                statuses[:] = [
                    item
                    for item in statuses
                    if not isinstance(item, dict)
                    or item.get("name") != data.get("name")
                    or item.get("pipeline_id") != data.get("pipeline_id")
                ]
                result = {
                    **data,
                    "status": data.get("state"),
                    "author": {"id": self.values.get("actor", 7)},
                }
                statuses.append(result)
                return 201, result
        return 404, {"message": "unknown project route"}

    def _jobs(
        self, project: str, pipeline: str, query: dict[str, list[str]]
    ) -> tuple[int, JsonValue]:
        all_jobs = self.values.get("jobs", {})
        jobs = all_jobs.get(f"{project}:{pipeline}", []) if isinstance(all_jobs, dict) else []
        if not isinstance(jobs, list):
            return 500, {"message": "invalid job fixture"}
        if query.get("include_retried") != ["true"]:
            jobs = [
                job for job in jobs if not isinstance(job, dict) or job.get("retried") is not True
            ]
        return 200, self._page(jobs, query)

    def _artifact(self, project: str, job: str) -> tuple[int, JsonValue | bytes]:
        archives = self.values.get("artifacts", {})
        payload = archives.get(f"{project}:{job}") if isinstance(archives, dict) else None
        if isinstance(payload, str):
            return 200, base64.b64decode(payload)
        return 404, {"message": "artifact missing"}

    def _merge_request(
        self, method: str, pieces: list[str], query: dict[str, list[str]], data: JsonValue
    ) -> tuple[int, JsonValue]:
        project, _kind, mr = pieces[:3]
        key = f"{project}:{mr}"
        requests = self.values.get("merge_requests", {})
        if len(pieces) == 3 and isinstance(requests, dict) and key in requests:
            return 200, requests[key]
        all_notes = self.values.setdefault("notes", {})
        notes = all_notes.setdefault(key, []) if isinstance(all_notes, dict) else None
        if not isinstance(notes, list) or len(pieces) < 4 or pieces[3] != "notes":
            return 404, {"message": "unknown merge request route"}
        if method == "GET" and len(pieces) == 4:
            return 200, self._page(notes, query)
        if len(pieces) == 5:
            return self._note(method, notes, pieces[4], data)
        if method == "POST" and isinstance(data, dict):
            identities = [int(str(note.get("id"))) for note in notes if isinstance(note, dict)]
            identity = max(identities, default=0) + 1
            timestamp = datetime.now(UTC).isoformat()
            created: dict[str, JsonValue] = {
                **data,
                "id": identity,
                "author": {"id": self.values.get("actor", 7)},
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            notes.append(created)
            return 201, created
        return 404, {"message": "unknown note operation"}

    def _note(
        self, method: str, notes: list[JsonValue], identity: str, data: JsonValue
    ) -> tuple[int, JsonValue]:
        for note in notes:
            if isinstance(note, dict) and str(note.get("id")) == identity:
                if method == "PUT" and isinstance(data, dict):
                    note.update(data)
                    note["updated_at"] = datetime.now(UTC).isoformat()
                return 200, note
        return 404, {"message": "note missing"}

    @staticmethod
    def _page(items: list[JsonValue], query: dict[str, list[str]]) -> list[JsonValue]:
        size = int(query.get("per_page", ["20"])[0])
        page = int(query.get("page", ["1"])[0])
        return items[(page - 1) * size : page * size]


class FakeGitLabHandler(BaseHTTPRequestHandler):
    """Serve the test control plane and GitLab API over ordinary HTTP."""

    state: ClassVar[FakeGitLabState]

    def do_GET(self) -> None:
        """Handle one read request."""
        self._dispatch("GET")

    def do_POST(self) -> None:
        """Handle one create request."""
        self._dispatch("POST")

    def do_PUT(self) -> None:
        """Handle one update request."""
        self._dispatch("PUT")

    @override
    def log_message(self, _format: str, *args: str) -> None:
        """Keep the test service quiet and avoid recording credentials."""

    def _dispatch(self, method: str) -> None:
        size = int(self.headers.get("Content-Length", "0"))
        data = cast("JsonValue", json.loads(self.rfile.read(size))) if size else None
        if self.path == "/health":
            status, value = 200, {"healthy": True}
        elif self.path == "/_scenario" and method == "POST" and isinstance(data, dict):
            self.state.reset(data)
            status, value = 200, {}
        elif self.path == "/_snapshot":
            status, value = 200, self.state.snapshot()
        elif not (self.headers.get("PRIVATE-TOKEN") or self.headers.get("JOB-TOKEN")):
            status, value = 401, {"message": "authentication required"}
        else:
            status, value = self.state.route(method, self.path, data)
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/octet-stream" if isinstance(value, bytes) else "application/json",
        )
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@dataclass(frozen=True)
class FakeGitLabScenario:
    """Expose the same scenario interface for both server adapters."""

    base_url: str

    def reset(self, payload: dict[str, JsonValue]) -> None:
        """Replace the active scenario over HTTP."""
        response = httpx.post(f"{self.base_url}/_scenario", json=payload, timeout=30)
        response.raise_for_status()

    def snapshot(self) -> dict[str, JsonValue]:
        """Read state and request history over HTTP."""
        response = httpx.get(f"{self.base_url}/_snapshot", timeout=30)
        response.raise_for_status()
        return cast("dict[str, JsonValue]", response.json())


class FakeGitLabServer(AbstractContextManager[FakeGitLabScenario]):
    """Own an isolated threaded instance of the GitLab test service."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Allocate a private state model and HTTP listener."""
        handler = type("ScenarioHandler", (FakeGitLabHandler,), {"state": FakeGitLabState()})
        self.server = ThreadingHTTPServer((host, port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @override
    def __enter__(self) -> FakeGitLabScenario:
        """Start serving requests on the allocated port."""
        self.thread.start()
        return FakeGitLabScenario(f"http://127.0.0.1:{self.server.server_port}")

    @override
    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _error: BaseException | None,
        _trace: TracebackType | None,
    ) -> None:
        """Release the listener and wait for its serving thread."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def main() -> None:
    """Run the same route implementation as a standalone container service."""
    handler = type("ContainerHandler", (FakeGitLabHandler,), {"state": FakeGitLabState()})
    server = ThreadingHTTPServer(
        (
            os.environ.get("FAKE_GITLAB_HOST", "127.0.0.1"),
            int(os.environ.get("FAKE_GITLAB_PORT", "8080")),
        ),
        handler,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
