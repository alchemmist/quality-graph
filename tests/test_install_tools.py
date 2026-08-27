import os
import subprocess
from pathlib import Path


def test_tool_installer_retries_transient_go_failures(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    count = tmp_path / "count"
    go = fake_bin / "go"
    go.write_text(
        "#!/usr/bin/env bash\n"
        "value=0\n"
        '[ ! -f "$FAKE_GO_COUNT" ] || value=$(cat "$FAKE_GO_COUNT")\n'
        "value=$((value + 1))\n"
        'printf \'%s\' "$value" >"$FAKE_GO_COUNT"\n'
        '[ "$value" -gt 2 ]\n'
    )
    go.chmod(0o755)
    tools = tmp_path / "tools"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_GO_COUNT": str(count),
        "QUALITY_GRAPH_TOOLS_BIN": str(tools),
        "QUALITY_GRAPH_TOOL_RETRY_DELAY": "0",
    }

    completed = subprocess.run(
        ["/bin/bash", "scripts/install-tools.sh"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0
    assert count.read_text() == "5"
    assert (tools / "versions").is_file()
    assert completed.stderr.count("Retrying ") == 2
