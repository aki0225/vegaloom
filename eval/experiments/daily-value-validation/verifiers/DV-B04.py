from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：python verifier.py <click-worktree>", file=sys.stderr)
        return 2

    repo_root = Path(sys.argv[1]).resolve()
    source_root = repo_root / "src"
    if not source_root.is_dir():
        print(f"未找到 Click 源码目录：{source_root}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(source_root))

    import click
    from click.testing import CliRunner

    imported_from = Path(click.__file__).resolve()
    if source_root not in imported_from.parents:
        print(f"Click 导入来源不属于目标工作树：{imported_from}", file=sys.stderr)
        return 2

    checks = [
        _run_prompt_case(
            click,
            CliRunner,
            show_default="display-label",
            default="actual-default",
            user_input="\n",
            expected_prompt="(display-label)",
            forbidden_prompt="actual-default",
            expected_value="actual-default",
        ),
        _run_prompt_case(
            click,
            CliRunner,
            show_default="computed at runtime",
            default=None,
            user_input="provided-value\n",
            expected_prompt="(computed at runtime)",
            forbidden_prompt=None,
            expected_value="provided-value",
        ),
        _run_prompt_case(
            click,
            CliRunner,
            show_default="",
            default="hidden-default",
            user_input="\n",
            expected_prompt=None,
            forbidden_prompt="hidden-default",
            expected_value="hidden-default",
        ),
        _run_prompt_case(
            click,
            CliRunner,
            show_default=True,
            default="visible-default",
            user_input="\n",
            expected_prompt="visible-default",
            forbidden_prompt=None,
            expected_value="visible-default",
        ),
    ]

    failures = [item for item in checks if not item["passed"]]
    payload = {
        "schema_version": 1,
        "repository": "pallets/click",
        "issue_number": 2836,
        "python": sys.version.split()[0],
        "click_import": str(imported_from.relative_to(repo_root)),
        "checks": checks,
        "passed": not failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def _run_prompt_case(
    click,
    cli_runner_type,
    *,
    show_default,
    default,
    user_input: str,
    expected_prompt: str | None,
    forbidden_prompt: str | None,
    expected_value: str,
) -> dict[str, object]:
    @click.command()
    @click.option(
        "--name",
        prompt=True,
        default=default,
        show_default=show_default,
    )
    def command(name: str) -> None:
        click.echo(f"value={name}")

    result = cli_runner_type().invoke(command, input=user_input)
    prompt_line = result.output.splitlines()[0] if result.output.splitlines() else ""
    expected_value_line = f"value={expected_value}"
    passed = (
        result.exit_code == 0
        and (expected_prompt is None or expected_prompt in prompt_line)
        and (forbidden_prompt is None or forbidden_prompt not in prompt_line)
        and expected_value_line in result.output
    )
    return {
        "show_default": show_default,
        "default": default,
        "exit_code": result.exit_code,
        "prompt_line": prompt_line,
        "expected_prompt": expected_prompt,
        "forbidden_prompt": forbidden_prompt,
        "expected_value_line": expected_value_line,
        "passed": passed,
    }


if __name__ == "__main__":
    raise SystemExit(main())
