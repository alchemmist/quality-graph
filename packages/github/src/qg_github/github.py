"""Provide the narrow injected GitHub transport seam."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, cast

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quality_graph_core.result import JsonValue

REQUEST_TIMEOUT_SECONDS = 30.0
GITHUB_PAGE_SIZE = 100


class GitHubPort(Protocol):
    """Describe the single request interface required by GitHub modules."""

    repository: str

    def request(self, method: str, path: str, payload: JsonValue = None) -> JsonValue:
        """Execute one GitHub request and return decoded JSON."""
        ...

    def download(self, path: str) -> bytes:
        """Download one authenticated binary response."""
        ...


class GitHubError(RuntimeError):
    """Represent a failed GitHub transport request."""

    def __init__(self, method: str, path: str, status_code: int) -> None:
        """Create an error without exposing response or credential content."""
        super().__init__(f"GitHub {method} {path} failed: HTTP {status_code}")
        self.status_code = status_code


class HttpGitHubPort:
    """Send repository-scoped requests through GitHub's HTTP interface."""

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        base_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
    ) -> None:
        """Configure an explicit repository, token, URL, and optional client."""
        self.repository = repository
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> HttpGitHubPort:
        """Create the production adapter from GitHub Action environment values."""
        values = environment if environment is not None else os.environ
        return cls(
            values["GITHUB_REPOSITORY"],
            values["GITHUB_TOKEN"],
            base_url=values.get("GITHUB_API_URL", "https://api.github.com"),
        )

    def request(self, method: str, path: str, payload: JsonValue = None) -> JsonValue:
        """Execute one bounded request and decode its JSON response."""
        api_path = path if path == "/user" else f"/repos/{self.repository}{path}"
        try:
            response = self.client.request(
                method,
                f"{self.base_url}{api_path}",
                content=None if payload is None else json.dumps(payload).encode(),
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        except httpx.RequestError as error:
            message = f"GitHub {method} {path} transport failed: {type(error).__name__}"
            raise RuntimeError(message) from error
        if response.status_code == HTTPStatus.NO_CONTENT:
            return None
        if response.status_code == HTTPStatus.NOT_FOUND and method in {"GET", "DELETE"}:
            return None
        if response.is_error:
            raise GitHubError(method, path, response.status_code)
        try:
            return cast("JsonValue", response.json())
        except json.JSONDecodeError as error:
            message = f"GitHub {method} {path} returned invalid JSON"
            raise RuntimeError(message) from error

    def download(self, path: str) -> bytes:
        """Download one repository-scoped binary response with redirects."""
        api_path = f"/repos/{self.repository}{path}"
        try:
            response = self.client.get(
                f"{self.base_url}{api_path}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                follow_redirects=True,
            )
        except httpx.RequestError as error:
            message = f"GitHub download {path} transport failed: {type(error).__name__}"
            raise RuntimeError(message) from error
        if response.is_error:
            method = "GET"
            raise GitHubError(method, path, response.status_code)
        return response.content


@dataclass
class MemoryGitHubPort:
    """Provide deterministic queued responses for pure lifecycle tests."""

    responses: dict[tuple[str, str], list[JsonValue]] = field(default_factory=dict)
    downloads: dict[str, bytes] = field(default_factory=dict)
    requests: list[tuple[str, str, JsonValue]] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    repository: str = "owner/repository"

    def enqueue(self, method: str, path: str, *responses: JsonValue) -> None:
        """Queue one or more responses for an exact request."""
        self.responses.setdefault((method, path), []).extend(responses)

    def request(self, method: str, path: str, payload: JsonValue = None) -> JsonValue:
        """Record a request and return its next configured response."""
        self.requests.append((method, path, payload))
        queued = self.responses.get((method, path))
        if not queued:
            message = f"Unexpected GitHub request: {method} {path}"
            raise AssertionError(message)
        if len(queued) == 1:
            return queued[0]
        return queued.pop(0)

    def download(self, path: str) -> bytes:
        """Record and return one configured binary download."""
        self.downloaded.append(path)
        if path not in self.downloads:
            message = f"Unexpected GitHub download: {path}"
            raise AssertionError(message)
        return self.downloads[path]


def paged(port: GitHubPort, path: str) -> tuple[dict[str, JsonValue], ...]:
    """Read all pages from one GitHub list endpoint."""
    result: list[dict[str, JsonValue]] = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        page_path = f"{path}{separator}per_page={GITHUB_PAGE_SIZE}&page={page}"
        items = _array(port.request("GET", page_path), page_path)
        result.extend(_object(item, page_path) for item in items)
        if len(items) < GITHUB_PAGE_SIZE:
            return tuple(result)
        page += 1


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must return an object"
        raise TypeError(message)
    return value


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{context} must return an array"
        raise TypeError(message)
    return value
