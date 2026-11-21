import json
from pathlib import Path

import pytest
import yaml

from qg_github.compiler import compile_graph
from quality_graph_core.collection import collect_report
from quality_graph_core.graph import AdapterKind, Graph, LuaAdapter
from quality_graph_core.lua import transform
from quality_graph_core.result import FailureKind
from quality_graph_core.workspace import read_lua_file
from tests.test_adapters import context

CODE = "return {reportVersion=0,status=json.decode(input).status}"


def graph(config: dict[str, str], root: Path) -> Graph:
    return Graph.from_yaml(
        yaml.safe_dump(
            {
                "version": 0,
                "runtime": {"action": "owner/runtime@" + "a" * 40},
                "profiles": {"default": {}},
                "nodes": {"lint": {"run": "lint", "results": {"lua": config}}},
            }
        ),
        lambda path: read_lua_file(root, path),
    )


def test_inline_and_file_reports_match_and_source_changes_identity(tmp_path: Path) -> None:
    script = tmp_path / "tool.lua"
    script.write_text(CODE)
    (tmp_path / "out").write_text('{"status":"passed"}')
    file = {"file": "tool.lua", "input": "out"}
    declaration = graph(file, tmp_path)
    for spec in [declaration, graph({"code": CODE, "input": "out"}, tmp_path)]:
        adapter = spec.nodes[0].result
        assert isinstance(adapter, LuaAdapter)
        assert (
            collect_report(context(), adapter.kind, tmp_path, adapter.path, lua=adapter).status
            == "passed"
        )
        assert (
            collect_report(
                context(succeeded=False), adapter.kind, tmp_path, adapter.path, lua=adapter
            ).failure_kind
            is FailureKind.COMMAND
        )
    script.write_text(CODE + "\n")
    assert (
        compile_graph(declaration).graph_digest != compile_graph(graph(file, tmp_path)).graph_digest
    )
    bound = declaration.nodes[0].result
    assert (
        collect_report(context(), bound.kind, tmp_path, bound.path, lua=bound).failure_kind
        is FailureKind.ADAPTER
    )
    script.unlink()
    assert (
        collect_report(context(), bound.kind, tmp_path, bound.path, lua=bound).failure_kind
        is FailureKind.ADAPTER
    )


@pytest.mark.parametrize(
    "code",
    [
        "not Lua",
        "return os.time()",
        'return require("io")',
        "return json.decode(input)",
        "return {}",
        'return {reportVersion=0,status="passed",controls={}}',
        "while true do end",
        'local x="aaa"; while true do x=x..x end',
        'local x="aa"; for i=1,20 do x=x..x end; return {reportVersion=0,notes={x}}',
    ],
)
def test_invalid_or_exhausted_adapter_cannot_pass(tmp_path: Path, code: str) -> None:
    (tmp_path / "out").write_text("malformed")
    result = collect_report(
        context(), AdapterKind.LUA, tmp_path, "out", lua=LuaAdapter(AdapterKind.LUA, "out", code)
    )
    assert result.failure_kind is FailureKind.ADAPTER
    assert result.diagnostics[0].detail


def test_json_mapping() -> None:
    payload = b'{"items":[null,{}],"empty":[],"blank":{}}'
    value = json.loads(
        transform(
            "local t=json.decode(input); t.reportVersion=0; t.extra=json.array(); return t", payload
        )
    )
    assert value == json.loads(payload) | {"reportVersion": 0, "extra": []}


@pytest.mark.parametrize("file", ["../outside.lua", "link.lua", "dir/script.lua"])
def test_file_boundaries(tmp_path: Path, file: str) -> None:
    (tmp_path / "link.lua").symlink_to(__file__)
    (tmp_path / "dir").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        graph({"input": "out", "file": file}, tmp_path)


def test_conflicting_sources(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        graph({"file": "tool.lua", "code": CODE, "input": "out"}, tmp_path)
