"""Format staged blobs without staging unrelated working-tree changes."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import which

TOOL_ROOT = Path(__file__).resolve().parents[1]
GIT = Path(which("git") or "/usr/bin/git")
UV = Path(which("uv") or "/usr/bin/uv")


@dataclass(frozen=True)
class StagedFile:
    """Carry one regular staged file and its original worktree state."""

    path: Path
    mode: str
    worktree_clean: bool


def main() -> int:
    """Format supported staged files and update only their index blobs."""
    root = Path(git_output("rev-parse", "--show-toplevel").decode().strip())
    staged = _staged_files(root)
    if not staged:
        return 0
    with tempfile.TemporaryDirectory(prefix="quality-graph-format-") as temporary:
        mirror = Path(temporary)
        for item in staged:
            target = mirror / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git_output("show", f":{item.path.as_posix()}", cwd=root))
        _format(mirror, staged)
        for item in staged:
            formatted = (mirror / item.path).read_bytes()
            object_id = (
                git_output(
                    "hash-object",
                    "-w",
                    "--stdin",
                    cwd=root,
                    data=formatted,
                )
                .decode()
                .strip()
            )
            git_output(
                "update-index",
                "--cacheinfo",
                item.mode,
                object_id,
                item.path.as_posix(),
                cwd=root,
            )
            if item.worktree_clean:
                (root / item.path).write_bytes(formatted)
    return 0


def _staged_files(root: Path) -> tuple[StagedFile, ...]:
    names = (
        git_output(
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=ACMR",
            "-z",
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    result: list[StagedFile] = []
    for name in names:
        if not name or Path(name).suffix not in {".py", ".md"}:
            continue
        metadata = git_output("ls-files", "-s", "--", name, cwd=root).decode().split()
        if not metadata or not metadata[0].startswith("100"):
            continue
        clean = (
            _run(
                GIT,
                "diff",
                "--quiet",
                "--",
                name,
                cwd=root,
                check=False,
            ).returncode
            == 0
        )
        result.append(StagedFile(Path(name), metadata[0], clean))
    return tuple(result)


def _format(mirror: Path, staged: tuple[StagedFile, ...]) -> None:
    python_files = [str(mirror / item.path) for item in staged if item.path.suffix == ".py"]
    markdown_files = [str(mirror / item.path) for item in staged if item.path.suffix == ".md"]
    if python_files:
        _run_tool(
            "--group",
            "format",
            "ruff",
            "check",
            "--config",
            str(TOOL_ROOT / "pyproject.toml"),
            "--fix",
            "--fix-only",
            "--exit-zero",
            *python_files,
        )
        _run_tool(
            "--group",
            "format",
            "ruff",
            "format",
            "--config",
            str(TOOL_ROOT / "pyproject.toml"),
            *python_files,
        )
    if markdown_files:
        _run_tool("--group", "format", "mdformat", *markdown_files)


def _run_tool(*arguments: str) -> None:
    _run(
        UV,
        "run",
        "--project",
        str(TOOL_ROOT),
        "--locked",
        *arguments,
    )


def git_output(
    *arguments: str,
    cwd: Path | None = None,
    data: bytes | None = None,
) -> bytes:
    """Run one argv-only Git command and return its output."""
    return _run(
        GIT,
        *arguments,
        cwd=cwd,
        input_data=data,
        stdout=subprocess.PIPE,
    ).stdout


def _run(
    executable: Path,
    *arguments: str,
    cwd: Path | None = None,
    input_data: bytes | None = None,
    stdout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        (str(executable), *arguments),
        cwd=cwd,
        input=input_data,
        stdout=stdout,
        check=check,
    )


if __name__ == "__main__":
    sys.exit(main())
