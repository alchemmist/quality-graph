import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("root", "forbidden"),
    [
        ("packages/core/src", ("qg_github", "qg_cli")),
        ("packages/github/src", ("qg_cli",)),
        ("apps/qg/src", ("qg_github",)),
    ],
)
def test_package_import_direction(root: str, forbidden: tuple[str, ...]) -> None:
    violations: list[str] = []
    for path in Path(root).rglob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        violations.extend(
            f"{path}: {imported}" for imported in imports if imported.startswith(forbidden)
        )

    assert violations == []
