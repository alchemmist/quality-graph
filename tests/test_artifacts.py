import hashlib
import io
import stat
import zipfile
from dataclasses import replace

import pytest

from qg_github.artifacts import (
    MAX_ARTIFACT_ARCHIVE_BYTES,
    MAX_ARTIFACT_FILES,
    ArtifactError,
    ArtifactExpectation,
    download_results,
)
from qg_github.github import MemoryGitHubPort
from quality_graph_core.result import Provenance, Result, ResultStatus


def result(node: str = "lint", *, attempt: int = 1) -> Result:
    return Result(
        node,
        node.title(),
        ResultStatus.PASSED,
        Provenance("owner/repository", "a" * 40, 10, attempt, "b" * 64, 42),
    )


def archive(value: Result, *, name: str = "result.json") -> bytes:
    return raw_archive(value.to_json().encode(), name=name)


def raw_archive(value: bytes, *, name: str = "result.json") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(name, value)
    return output.getvalue()


def expectation() -> ArtifactExpectation:
    return ArtifactExpectation(
        "owner/repository",
        42,
        "a" * 40,
        10,
        "b" * 64,
        frozenset({"lint", "format"}),
    )


def artifact(artifact_id: int, name: str, content: bytes) -> dict[str, object]:
    return {
        "id": artifact_id,
        "name": name,
        "size_in_bytes": len(content),
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "expired": False,
    }


def artifacts_path(page: int = 1) -> str:
    return f"/actions/runs/10/artifacts?per_page=100&page={page}"


def test_downloader_selects_newest_attempt_and_ignores_unrelated_artifacts() -> None:
    port = MemoryGitHubPort()
    old = archive(result(attempt=1))
    new = archive(result(attempt=2))
    port.enqueue(
        "GET",
        artifacts_path(),
        {
            "artifacts": [
                artifact(1, "coverage", b"ignored"),
                artifact(2, "quality-result-lint-1", old),
                artifact(3, "quality-result-lint-2", new),
                artifact(4, "quality-result-lint-1", old),
            ]
        },
    )
    port.downloads.update(
        {
            "/actions/artifacts/2/zip": old,
            "/actions/artifacts/3/zip": new,
            "/actions/artifacts/4/zip": old,
        }
    )

    results = download_results(port, expectation())

    assert results["lint"].provenance.run_attempt == 2
    assert port.downloaded == [
        "/actions/artifacts/2/zip",
        "/actions/artifacts/3/zip",
        "/actions/artifacts/4/zip",
    ]


def test_downloader_follows_artifact_pagination() -> None:
    port = MemoryGitHubPort()
    unrelated = [artifact(index, f"unrelated-{index}", b"") for index in range(100)]
    content = archive(result())
    port.enqueue("GET", artifacts_path(), {"artifacts": unrelated})
    port.enqueue(
        "GET",
        artifacts_path(2),
        {"artifacts": [artifact(101, "quality-result-lint-1", content)]},
    )
    port.downloads["/actions/artifacts/101/zip"] = content

    assert download_results(port, expectation())["lint"].node_id == "lint"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"expired": True}, "expired"),
        ({"name": 1}, "artifact name"),
        ({"id": "invalid"}, "artifact id"),
        ({"size_in_bytes": MAX_ARTIFACT_ARCHIVE_BYTES + 1}, "size limit"),
        ({"digest": "invalid"}, "invalid digest"),
    ],
)
def test_downloader_rejects_invalid_artifact_metadata(
    changes: dict[str, object], message: str
) -> None:
    port = MemoryGitHubPort()
    content = archive(result())
    metadata = artifact(1, "quality-result-lint-1", content)
    metadata.update(changes)
    port.enqueue("GET", artifacts_path(), {"artifacts": [metadata]})

    with pytest.raises(ArtifactError, match=message):
        download_results(port, expectation())


def test_downloader_rejects_unknown_node_and_stale_provenance() -> None:
    port = MemoryGitHubPort()
    content = archive(result("unknown"))
    port.enqueue(
        "GET",
        artifacts_path(),
        {"artifacts": [artifact(1, "quality-result-unknown-1", content)]},
    )
    with pytest.raises(ArtifactError, match="unknown graph node"):
        download_results(port, expectation())

    stale = archive(replace(result(), provenance=replace(result().provenance, head_sha="c" * 40)))
    other = MemoryGitHubPort()
    other.enqueue(
        "GET",
        artifacts_path(),
        {"artifacts": [artifact(2, "quality-result-lint-1", stale)]},
    )
    other.downloads["/actions/artifacts/2/zip"] = stale
    with pytest.raises(ArtifactError, match="provenance"):
        download_results(other, expectation())


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not zip", "valid ZIP"),
        (archive(result(), name="../result.json"), "unsafe path"),
        (archive(result(), name="result.txt"), "exactly one JSON"),
    ],
)
def test_downloader_rejects_invalid_archives(content: bytes, message: str) -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        artifacts_path(),
        {"artifacts": [artifact(1, "quality-result-lint-1", content)]},
    )
    port.downloads["/actions/artifacts/1/zip"] = content

    with pytest.raises(ArtifactError, match=message):
        download_results(port, expectation())


def test_downloader_rejects_digest_download_and_result_limits() -> None:
    content = archive(result())
    port = MemoryGitHubPort()
    metadata = artifact(1, "quality-result-lint-1", content)
    metadata["digest"] = "sha256:" + "0" * 64
    port.enqueue("GET", artifacts_path(), {"artifacts": [metadata]})
    port.downloads["/actions/artifacts/1/zip"] = content
    with pytest.raises(ArtifactError, match="digest"):
        download_results(port, expectation())

    oversized = content + b"x" * (MAX_ARTIFACT_ARCHIVE_BYTES + 1)
    large = MemoryGitHubPort()
    large.enqueue(
        "GET",
        artifacts_path(),
        {"artifacts": [artifact(2, "quality-result-lint-1", content)]},
    )
    large.downloads["/actions/artifacts/2/zip"] = oversized
    with pytest.raises(ArtifactError, match="archive size"):
        download_results(large, expectation())


def test_downloader_rejects_archive_file_count_symlink_and_invalid_json() -> None:
    cases: list[tuple[bytes, str]] = []
    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as bundle:
        for index in range(MAX_ARTIFACT_FILES + 1):
            bundle.writestr(f"{index}.json", "{}")
    cases.append((many.getvalue(), "too many files"))

    link = io.BytesIO()
    with zipfile.ZipFile(link, "w") as bundle:
        info = zipfile.ZipInfo("result.json")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, "target")
    cases.append((link.getvalue(), "symbolic link"))
    cases.append((raw_archive(b"not result JSON"), "invalid result JSON"))

    for artifact_id, (content, message) in enumerate(cases, 1):
        port = MemoryGitHubPort()
        port.enqueue(
            "GET",
            artifacts_path(),
            {"artifacts": [artifact(artifact_id, "quality-result-lint-1", content)]},
        )
        port.downloads[f"/actions/artifacts/{artifact_id}/zip"] = content
        with pytest.raises(ArtifactError, match=message):
            download_results(port, expectation())


def test_downloader_rejects_uncompressed_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("qg_github.artifacts.MAX_ARTIFACT_CONTENT_BYTES", 10)
    content = raw_archive(b"x" * 11)
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        artifacts_path(),
        {"artifacts": [artifact(1, "quality-result-lint-1", content)]},
    )
    port.downloads["/actions/artifacts/1/zip"] = content
    with pytest.raises(ArtifactError, match="uncompressed size"):
        download_results(port, expectation())


@pytest.mark.parametrize("response", [[], {"artifacts": {}}, {"artifacts": [1]}])
def test_downloader_narrows_artifact_api_responses(response: object) -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", artifacts_path(), response)
    with pytest.raises(ArtifactError):
        download_results(port, expectation())
