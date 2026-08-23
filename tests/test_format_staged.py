from pathlib import Path

import pytest
from scripts.format_staged import git_output, main


def git(root: Path, *arguments: str) -> str:
    return git_output(*arguments, cwd=root).decode()


def test_staged_formatter_preserves_unstaged_hunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    source = tmp_path / "example.py"
    source.write_text("value=1\n")
    git(tmp_path, "add", "example.py")
    source.write_text("value=1\nextra=2\n")

    monkeypatch.chdir(tmp_path)
    assert main() == 0

    assert git(tmp_path, "show", ":example.py") == "value = 1\n"
    assert source.read_text() == "value=1\nextra=2\n"


def test_staged_formatter_updates_clean_worktree_and_ignores_other_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    source = tmp_path / "example.py"
    data = tmp_path / "data.txt"
    source.write_text("value=1\n")
    data.write_text("unchanged")
    git(tmp_path, "add", "example.py", "data.txt")

    monkeypatch.chdir(tmp_path)
    assert main() == 0

    assert source.read_text() == "value = 1\n"
    assert data.read_text() == "unchanged"
