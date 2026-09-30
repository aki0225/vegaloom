from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
NATIVE = re.compile(r"^(?:python |git |& \$|\$\w+ = (?:&|\(&|python ))")
GUARD = "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"


@pytest.mark.parametrize("version", ["0.7.1", "1.2.3rc1", None, "../bad"])
def test_ci_reads_declared_version(tmp_path, version):
    reader = WORKFLOW.parents[2] / "scripts/project_version.py"
    content = "[project]\n" + (f'version = "{version}"\n' if version is not None else "")
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    result = subprocess.run([sys.executable, str(reader)], cwd=tmp_path, capture_output=True, text=True)
    if version in ("0.7.1", "1.2.3rc1"):
        assert result.returncode == 0 and result.stdout.strip() == version
    else:
        assert result.returncode != 0 and not result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows安装版本断言")
@pytest.mark.parametrize("case", ["match", "missing-wheel", "wrong-version"])
def test_windows_artifact_name_and_installed_version_follow_project(tmp_path, case):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    body = next(s["run"] for s in workflow["jobs"]["windows"]["steps"]
                if s["name"] == "构建并安装 Windows wheel")
    assert '$expectedVersion = python scripts/project_version.py\n' + GUARD in body
    artifact = re.search(r'pip install "(dist/[^"\n]+)"', body).group(1)
    check = re.search(r'if \(\$version.Trim\(\).*?\n}', body, re.S).group(0)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.3.4"\n', encoding="utf-8")
    (tmp_path / "dist").mkdir()
    if case != "missing-wheel":
        (tmp_path / "dist/vegaloom-2.3.4-py3-none-any.whl").touch()
    reader = WORKFLOW.parents[2] / "scripts/project_version.py"
    script = (
        f"$expectedVersion = & '{sys.executable}' '{reader}'\n{GUARD}\n"
        f'if (!(Test-Path "{artifact}")) {{ exit 8 }}\n'
        f'$version = "{"0.0.0" if case == "wrong-version" else "2.3.4"}"\n'
        + check + '\nWrite-Output "OK"\n'
    )
    result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) is (case == "match")
    # Linux其他安装step从构建step写出的同一期望值读取，保留确定制品及安装后断言。
    linux = "\n".join(s.get("run", "") for job in workflow["jobs"].values() for s in job.get("steps", [])
                      if s.get("shell", "bash") == "bash")
    assert 'VEGA_PACKAGE_VERSION=%s' in linux
    assert 'dist/vegaloom-${VEGA_PACKAGE_VERSION}.tar.gz' in linux
    assert linux.count('= "$VEGA_PACKAGE_VERSION"') == 4


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
    assert len(positions) == (3 if step_index == 0 else 12)
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
