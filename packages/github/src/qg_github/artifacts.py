"""Download and validate untrusted portable result artifacts."""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from qg_github.github import GITHUB_PAGE_SIZE, GitHubPort
from quality_graph_core.result import JsonValue, Result

if TYPE_CHECKING:
    from collections.abc import Iterable

ARTIFACT_NAME_RE = re.compile(
    r"^quality-result-(?P<node>[a-z][a-z0-9-]{0,62})-"
    r"(?P<attempt>[1-9][0-9]*)$"
)
MAX_ARTIFACT_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_ARTIFACT_FILES = 100
MAX_ARTIFACT_CONTENT_BYTES = 50 * 1024 * 1024


class ArtifactError(ValueError):
    """Represent an invalid or incompatible result artifact."""


@dataclass(frozen=True)
class ArtifactExpectation:
    """Describe trusted workflow provenance required from every result."""

    repository: str
    pull_request: int
    head_sha: str
    workflow_run_id: int
    graph_digest: str
    node_ids: frozenset[str]


@dataclass(frozen=True)
class ArtifactDescriptor:
    """Carry validated GitHub artifact metadata."""

    id: int
    node_id: str
    attempt: int
    size: int
    digest: str


def download_results(
    port: GitHubPort,
    expectation: ArtifactExpectation,
) -> dict[str, Result]:
    """Download the newest valid attempt for every expected node."""
    selected: dict[str, tuple[int, Result]] = {}
    for descriptor in _artifact_descriptors(port, expectation.workflow_run_id):
        if descriptor.node_id not in expectation.node_ids:
            message = f"artifact targets unknown graph node: {descriptor.node_id}"
            raise ArtifactError(message)
        archive = port.download(f"/actions/artifacts/{descriptor.id}/zip")
        result = _result_from_archive(archive, descriptor)
        _validate_result(result, descriptor, expectation)
        current = selected.get(descriptor.node_id)
        if current is None or descriptor.attempt >= current[0]:
            selected[descriptor.node_id] = (descriptor.attempt, result)
    return {node_id: result for node_id, (_, result) in selected.items()}


def _artifact_descriptors(port: GitHubPort, run_id: int) -> tuple[ArtifactDescriptor, ...]:
    descriptors: list[ArtifactDescriptor] = []
    page = 1
    while True:
        path = f"/actions/runs/{run_id}/artifacts?per_page={GITHUB_PAGE_SIZE}&page={page}"
        response = _object(port.request("GET", path), "workflow artifacts")
        artifacts = _array(response.get("artifacts"), "workflow artifacts")
        for value in artifacts:
            descriptor = _artifact_descriptor(_object(value, "workflow artifact"))
            if descriptor is not None:
                descriptors.append(descriptor)
        if len(artifacts) < GITHUB_PAGE_SIZE:
            return tuple(descriptors)
        page += 1


def _artifact_descriptor(data: dict[str, JsonValue]) -> ArtifactDescriptor | None:
    name = _string(data.get("name"), "artifact name")
    match = ARTIFACT_NAME_RE.fullmatch(name)
    if match is None:
        return None
    if data.get("expired") is True:
        message = f"result artifact has expired: {name}"
        raise ArtifactError(message)
    artifact_id = _integer(data.get("id"), "artifact id")
    size = _integer(data.get("size_in_bytes"), "artifact size")
    if not 0 <= size <= MAX_ARTIFACT_ARCHIVE_BYTES:
        message = f"artifact exceeds the archive size limit: {name}"
        raise ArtifactError(message)
    digest = _string(data.get("digest"), "artifact digest")
    if not digest.startswith("sha256:") or len(digest) != len("sha256:") + 64:
        message = f"artifact has an invalid digest: {name}"
        raise ArtifactError(message)
    return ArtifactDescriptor(
        artifact_id,
        match.group("node"),
        int(match.group("attempt")),
        size,
        digest.removeprefix("sha256:"),
    )


def _result_from_archive(archive: bytes, descriptor: ArtifactDescriptor) -> Result:
    if len(archive) > MAX_ARTIFACT_ARCHIVE_BYTES:
        message = f"downloaded artifact exceeds the archive size limit: {descriptor.id}"
        raise ArtifactError(message)
    actual_digest = hashlib.sha256(archive).hexdigest()
    if actual_digest != descriptor.digest:
        message = f"artifact digest does not match metadata: {descriptor.id}"
        raise ArtifactError(message)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            files = tuple(info for info in bundle.infolist() if not info.is_dir())
            _validate_archive_files(files)
            if len(files) != 1 or not files[0].filename.endswith(".json"):
                message = "result artifact must contain exactly one JSON file"
                raise ArtifactError(message)
            return Result.from_json(bundle.read(files[0]))
    except zipfile.BadZipFile as error:
        message = f"result artifact is not a valid ZIP archive: {descriptor.id}"
        raise ArtifactError(message) from error
    except (TypeError, ValueError) as error:
        if isinstance(error, ArtifactError):
            raise
        message = f"result artifact contains invalid result JSON: {descriptor.id}"
        raise ArtifactError(message) from error


def _validate_archive_files(files: Iterable[zipfile.ZipInfo]) -> None:
    values = tuple(files)
    if len(values) > MAX_ARTIFACT_FILES:
        message = "result artifact contains too many files"
        raise ArtifactError(message)
    total = 0
    for info in values:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            message = f"result artifact contains an unsafe path: {info.filename}"
            raise ArtifactError(message)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            message = f"result artifact contains a symbolic link: {info.filename}"
            raise ArtifactError(message)
        total += info.file_size
        if total > MAX_ARTIFACT_CONTENT_BYTES:
            message = "result artifact exceeds the uncompressed size limit"
            raise ArtifactError(message)


def _validate_result(
    result: Result,
    descriptor: ArtifactDescriptor,
    expectation: ArtifactExpectation,
) -> None:
    provenance = result.provenance
    expected = (
        expectation.repository,
        expectation.pull_request,
        expectation.head_sha,
        expectation.workflow_run_id,
        descriptor.attempt,
        expectation.graph_digest,
    )
    observed = (
        provenance.repository,
        provenance.pull_request,
        provenance.head_sha,
        provenance.workflow_run_id,
        provenance.run_attempt,
        provenance.graph_digest,
    )
    if result.node_id != descriptor.node_id or observed != expected:
        message = f"artifact provenance does not match workflow metadata: {descriptor.id}"
        raise ArtifactError(message)


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise ArtifactError(message)
    return value


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{context} must be an array"
        raise ArtifactError(message)
    return value


def _string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise ArtifactError(message)
    return value


def _integer(value: JsonValue, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{context} must be an integer"
        raise ArtifactError(message)
    return value
