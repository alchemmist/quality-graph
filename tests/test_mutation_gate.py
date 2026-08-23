import json
from pathlib import Path

import pytest
from scripts.mutation_gate import main, mutation_score


def test_mutation_score_counts_all_gate_verdicts() -> None:
    assert mutation_score({"killed": 9, "survived": 1, "suspicious": 0, "timeout": 0}) == 0.9
    assert mutation_score({"killed": 0, "survived": 0, "suspicious": 0, "timeout": 0}) == 1.0


def test_gate_reports_score_and_rejects_result_below_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "stats.json"
    path.write_text(json.dumps({"killed": 8, "survived": 2, "suspicious": 0, "timeout": 0}))
    monkeypatch.setattr("sys.argv", ["mutation_gate", str(path)])

    with pytest.raises(SystemExit, match="1"):
        main()

    assert "80.00%" in capsys.readouterr().out
