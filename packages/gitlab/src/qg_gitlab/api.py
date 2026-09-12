"""Communicate with GitLab through a bounded, instance-scoped HTTP interface."""

from __future__ import annotations

import time
import urllib.parse
from http import HTTPStatus
from typing import TYPE_CHECKING, Self, cast

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from types import TracebackType

    from quality_graph_core.result import JsonValue

PAGE_SIZE = 100
MAX_PAGES = 1_000
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
RETRY_STATUSES = {409, 429, 502, 503, 504}


def object_value(value: JsonValue, context: str) -> dict[str, JsonValue]:
    """Require a JSON object at an external interface."""
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise TypeError(message)
    return value


def integer(value: JsonValue, context: str) -> int:
    """Require a positive integer identity without coercion."""
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{context} must be an integer"
        raise TypeError(message)
    if value < 1:
        message = f"{context} must be positive"
        raise ValueError(message)
    return value


def string(value: JsonValue, context: str) -> str:
    """Require a nonempty string at an external interface."""
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise TypeError(message)
    if not value:
        message = f"{context} must not be empty"
        raise ValueError(message)
    return value


def server_url(value: str) -> str:
    """Validate an explicitly configured instance or API URL."""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        message = "GitLab URL must be an absolute HTTP(S) URL without credentials"
        raise ValueError(message)
    return value.rstrip("/")


class GitLabError(RuntimeError):
    """Expose a bounded HTTP failure without credentials or response bodies."""

    def __init__(self, status: int, path: str) -> None:
        """Capture the status and path safe for user-facing diagnostics."""
        self.status = status
        super().__init__(f"GitLab API returned HTTP {status} for {path.split('?', 1)[0]}")


class HttpGitLab:
    """Own transport, pagination and bounded artifact downloads for one instance."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        api_url: str | None = None,
        job_token: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Construct an explicitly scoped client with no ambient credentials."""
        self.server_url = server_url(url)
        self.api_url = server_url(api_url or f"{self.server_url}/api/v4")
        self.client = httpx.Client(
            base_url=self.api_url,
            headers={"JOB-TOKEN" if job_token else "PRIVATE-TOKEN": token},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )

    def __enter__(self) -> Self:
        """Return the transport owned by this context."""
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close connection pools on every exit."""
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        data: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        """Make one bounded JSON request, retrying safe transient reads only."""
        self._path(path)
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            response = self.client.request(method, path, json=data)
            if response.status_code in RETRY_STATUSES and attempt + 1 < attempts:
                time.sleep(min(2**attempt, 4))
                continue
            if not response.is_success:
                raise GitLabError(response.status_code, path)
            if len(response.content) > MAX_RESPONSE_BYTES:
                message = "GitLab JSON response exceeds the size limit"
                raise ValueError(message)
            return (
                None
                if response.status_code == HTTPStatus.NO_CONTENT
                else cast("JsonValue", response.json())
            )
        message = "GitLab request exhausted its retry budget"
        raise RuntimeError(message)

    def pages(self, path: str) -> Iterator[dict[str, JsonValue]]:
        """Read every page without following server-supplied credential targets."""
        separator = "&" if "?" in path else "?"
        for page in range(1, MAX_PAGES + 1):
            value = self.request("GET", f"{path}{separator}per_page={PAGE_SIZE}&page={page}")
            if not isinstance(value, list):
                message = "GitLab list response must be an array"
                raise TypeError(message)
            for item in value:
                yield object_value(item, "GitLab list item")
            if len(value) < PAGE_SIZE:
                return
        message = "GitLab pagination exceeded its page limit"
        raise ValueError(message)

    def download(self, path: str) -> bytes:
        """Download an artifact without forwarding credentials through redirects."""
        self._path(path)
        with self.client.stream("GET", path) as response:
            if not response.is_success:
                raise GitLabError(response.status_code, path)
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    message = "GitLab artifact exceeds the size limit"
                    raise ValueError(message)
                chunks.append(chunk)
            return b"".join(chunks)

    @staticmethod
    def project_path(project: int | str) -> str:
        """Encode a project ID or complete nested namespace as one path segment."""
        return f"/projects/{urllib.parse.quote(str(project), safe='')}"

    @staticmethod
    def _path(path: str) -> None:
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            message = "GitLab request path must remain within the configured API"
            raise ValueError(message)
