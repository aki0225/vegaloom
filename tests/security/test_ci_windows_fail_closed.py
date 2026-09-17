from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
NATIVE = re.compile(r"^(?:python |git |& \$|\$\w+ = (?:&|\(&))")
GUARD = "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pwsh 原生命令退出语义")
@pytest.mark.parametrize("step_index", [0, 1], ids=["tests", "wheel"])
@pytest.mark.parametrize("failure", ["first", "middle", "none"])
def test_windows_native_chain_fails_closed(tmp_path, step_index, failure):
    pwsh = shutil.which("pwsh")
    assert pwsh is not None, "Windows CI 必须提供 pwsh，不能静默跳过"
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [step for step in workflow["jobs"]["windows"]["steps"] if step.get("shell") == "pwsh"]
    assert len(steps) == 2
    body = steps[step_index]["run"]
    lines = [line.strip() for line in body.replace("`\n", " ").splitlines() if line.strip()]
    positions = [index for index, line in enumerate(lines) if NATIVE.match(line)]
    assert len(positions) == (3 if step_index == 0 else 11)
    assert "tests/security/test_ci_windows_fail_closed.py" in steps[0]["run"]
    assert "catch" not in body
    # 保留 YAML 的逐命令门禁及调用形态，仅替换有安装/构建副作用的 native 载荷。
    script = ["$ErrorActionPreference = 'stop'", "$PSNativeCommandUseErrorActionPreference = $false"]
    failing_index = {"first": 0, "middle": len(positions) // 2, "none": -1}[failure]
    python = sys.executable.replace("'", "''")
    for index, position in enumerate(positions):
        original = lines[position]
        assert lines[position + 1] == GUARD, original
        code = 7 if index == failing_index else 0
        script.append(f"Write-Output 'ENTER_{index}'")
        command = f"& '{python}' -c 'import sys; print(\"{{}}\"); sys.exit({code})'"
        if original.startswith("$capabilities ="):
            command = f'$capabilities = ({command}) -join "`n" | ConvertFrom-Json'
        elif original.startswith("$version ="):
            command = f"$version = {command}"
        elif original.endswith("| Out-Null"):
            command += " | Out-Null"
        script.extend([command, lines[position + 1]])
    script.extend([
        "Write-Output 'SUCCESS_SENTINEL'",
        "if ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }",
    ])
    path = tmp_path / "native-chain.ps1"
    path.write_text("\n".join(script), encoding="utf-8-sig")
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if failure == "none":
        assert result.returncode == 0, result.stderr
        assert "SUCCESS_SENTINEL" in result.stdout
    else:
        assert result.returncode == 7, result.stderr
        assert f"ENTER_{failing_index}" in result.stdout
        assert f"ENTER_{failing_index + 1}" not in result.stdout
        assert "SUCCESS_SENTINEL" not in result.stdout
