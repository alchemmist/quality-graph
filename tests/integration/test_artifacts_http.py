from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from qg_github.artifacts import ArtifactError, ArtifactExpectation, download_results
from qg_github.github import HttpGitHubPort
from quality_graph_core.result import Provenance, Result, ResultStatus

if TYPE_CHECKING:
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


def result(*, attempt: int = 1, head_sha: str = "a" * 40) -> Result:
    return Result(
        "lint",
        "Lint",
        ResultStatus.PASSED,
        Provenance("owner/repository", head_sha, 10, attempt, "b" * 64, 42),
    )


def archive(value: Result, *, name: str = "result.json") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(name, value.to_json())
    return output.getvalue()


def metadata(identifier: int, name: str, content: bytes) -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "size_in_bytes": len(content),
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "expired": False,
    }


def expectation() -> ArtifactExpectation:
    return ArtifactExpectation(
        "owner/repository",
        42,
        "a" * 40,
        10,
        "b" * 64,
        frozenset({"lint"}),
    )


def test_artifact_download_selects_newest_attempt_across_http_pages(
    fake_github: FakeGitHubScenario,
) -> None:
    old = archive(result())
    new = archive(result(attempt=2))
    unrelated = [metadata(index, f"unrelated-{index}", b"") for index in range(1, 100)]
    fake_github.reset(
        {
            "run_artifacts": {
                "10": [
                    *unrelated,
                    metadata(100, "quality-result-lint-1", old),
                    metadata(101, "quality-result-lint-2", new),
                ]
            },
            "downloads": {
                "100": base64.b64encode(old).decode(),
                "101": base64.b64encode(new).decode(),
            },
        }
    )

    results = download_results(
        HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url),
        expectation(),
    )

    assert results["lint"].provenance.run_attempt == 2


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not zip", "valid ZIP"),
        (archive(result(), name="../result.json"), "unsafe path"),
        (
            archive(replace(result(), provenance=replace(result().provenance, head_sha="c" * 40))),
            "provenance",
        ),
    ],
)
def test_artifact_download_rejects_untrusted_http_content(
    fake_github: FakeGitHubScenario,
    content: bytes,
    message: str,
) -> None:
    fake_github.reset(
        {
            "run_artifacts": {
                "10": [metadata(1, "quality-result-lint-1", content)],
            },
            "downloads": {"1": base64.b64encode(content).decode()},
        }
    )

    with pytest.raises(ArtifactError, match=message):
        download_results(
            HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url),
            expectation(),
        )


def test_artifact_download_rejects_digest_mismatch_over_http(
    fake_github: FakeGitHubScenario,
) -> None:
    content = archive(result())
    descriptor = metadata(1, "quality-result-lint-1", content)
    descriptor["digest"] = "sha256:" + "0" * 64
    fake_github.reset(
        {
            "run_artifacts": {"10": [descriptor]},
            "downloads": {"1": base64.b64encode(content).decode()},
        }
    )

    with pytest.raises(ArtifactError, match="digest"):
        download_results(
            HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url),
            expectation(),
        )
