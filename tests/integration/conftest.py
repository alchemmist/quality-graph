from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tests.integration.fake_github import FakeGitHubScenario, FakeGitHubServer

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def fake_github() -> Iterator[FakeGitHubScenario]:
    external = os.environ.get("QG_FAKE_GITHUB_URL")
    if external is not None:
        scenario = FakeGitHubScenario(external.rstrip("/"))
        scenario.reset()
        yield scenario
        return
    with FakeGitHubServer() as scenario:
        scenario.reset()
        yield scenario
