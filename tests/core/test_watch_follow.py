from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega import cli


@pytest.mark.parametrize("replacement_count", [1, 3])
@pytest.mark.parametrize("json_output", [False, True])
def test_watch_follow_child_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_count: int,
    json_output: bool,
) -> None:
    """真实读取安全进度文件，仅以确定性快照替代运行状态和等待。"""
    monkeypatch.chdir(tmp_path)
    parent = "20261104-120000-parent"
    children = ["20261104-120001-child-a", "20261104-120002-child-b"]
    for run_id in [parent, *children]:
        (tmp_path / "runs" / run_id).mkdir(parents=True)
    trace = tmp_path / "runs" / parent / "trace.jsonl"
    trace.write_text(json.dumps({
        "event": "change_run_started", "ts": "2026-11-04T12:00:00+00:00",
    }) + "\n", encoding="utf-8")
    event = {
        "version": 2, "step": "worker", "event": "command_started",
        "ts": "2026-11-04T12:00:01+00:00",
        "command": "private-command", "reasoning": "private-reasoning",
    }
    for child, count in zip(children, [3, replacement_count], strict=True):
        (tmp_path / "runs" / child / "progress.jsonl").write_text(
            (json.dumps(event) + "\n") * count, encoding="utf-8",
        )
    poll = 0

    def status(*_args):
        return {
            "kind": "agent", "status": "stopped" if poll == 5 else "running",
            "current_step": "stopped" if poll == 5 else "acting",
            "last_child_run": children[0 if poll in {0, 1, 5} else 1],
        }

    def advance(_interval):
        nonlocal poll
        poll += 1
        assert poll <= 5
        if poll == 3:
            with (tmp_path / "runs" / children[1] / "progress.jsonl").open(
                "a", encoding="utf-8",
            ) as stream:
                stream.write((json.dumps(event) + "\n") * 3)
            with trace.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "event": "change_contract_approved",
                    "ts": "2026-11-04T12:00:00+00:00",
                }) + "\n")

    monkeypatch.setattr(cli, "run_status_payload", status)
    monkeypatch.setattr(cli.time, "sleep", advance)
    args = ["watch", "--run", parent, "--follow", "--limit", "2"]
    result = CliRunner().invoke(cli.app, args + (["--json"] if json_output else []))
    assert result.exit_code == 0, result.output
    assert poll == 5
    assert "private-command" not in result.output
    assert "private-reasoning" not in result.output
    if json_output:
        snapshots = [json.loads(line) for line in result.output.splitlines()]
        assert len(snapshots) == 6
        assert snapshots[0] == snapshots[1]
        assert snapshots[3] == snapshots[4]
        assert len(snapshots[0]["progress"]) == 2
        assert len(snapshots[2]["progress"]) == 2
        assert snapshots[2]["progress"][-1]["child_run"] == children[1]
        assert snapshots[-1]["status"] == "stopped"
    else:
        assert result.output.count(f"child={children[0]}") == 2
        assert result.output.count(f"child={children[1]}") == replacement_count + 3
        assert "agent / 已启动" not in result.output
        assert result.output.count("agent / 更新执行计划") == 1
        assert result.output.splitlines()[-1] == f"run={parent} status=stopped step=stopped"
