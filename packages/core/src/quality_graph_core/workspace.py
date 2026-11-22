"""Read bounded adapter inputs without following workspace symlinks."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def read_bounded(root: Path, relative: str, maximum: int) -> bytes:
    """Walk through pinned directory descriptors; reject special files and races."""
    path = PurePosixPath(relative)
    if not path.parts or path.is_absolute() or ".." in path.parts:
        message = "Lua adapter paths must be workspace-relative"
        raise ValueError(message)
    with ExitStack() as stack:
        directory = os.open(root, os.O_DIRECTORY)
        stack.callback(os.close, directory)
        for part in path.parts[:-1]:
            directory = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, directory)
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                message = f"Lua adapter requires a regular file: {relative}"
                raise ValueError(message)
            data = handle.read(maximum + 1)
    if len(data) > maximum:
        message = f"Lua adapter file exceeds {maximum} bytes: {relative}"
        raise ValueError(message)
    return data


def read_lua_file(root: Path, relative: str) -> str:
    """Read a UTF-8 source file as inert data."""
    return read_bounded(root, relative, 65_536).decode()
