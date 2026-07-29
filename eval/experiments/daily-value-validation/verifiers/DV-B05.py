from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：python verifier.py <click-workspace>", file=sys.stderr)
        return 2

    repo_root = Path(sys.argv[1]).resolve()
    source_root = repo_root / "src"
    if not source_root.is_dir():
        print(f"未找到 Click 源码目录：{source_root}", file=sys.stderr)
        return 2

    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source_root))

    import click
    from click.testing import CliRunner

    imported_from = Path(click.__file__).resolve()
    if source_root not in imported_from.parents:
        print(f"Click 导入来源不属于目标工作树：{imported_from}", file=sys.stderr)
        return 2

    checks = [
        _run_case(click, CliRunner, kind, color=color, err=err)
        for kind in ("confirm", "prompt")
        for color in (False, True)
        for err in (False, True)
    ]
    failures = [item for item in checks if not item["passed"]]
    payload = {
        "schema_version": 1,
        "repository": "pallets/click",
        "issue_number": 3572,
        "python": sys.version.split()[0],
        "click_import": str(imported_from.relative_to(repo_root)),
        "checks": checks,
        "passed": not failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def _run_case(
    click, cli_runner_type, kind: str, *, color: bool, err: bool
) -> dict[str, object]:
    label = click.style("Hello", fg="green", bold=True)
    styled_label = "\x1b[32m\x1b[1mHello\x1b[0m"

    @click.command()
    def command() -> None:
        if kind == "confirm":
            click.confirm(label, err=err)
        else:
            click.prompt(label, err=err)

    user_input = "y\n" if kind == "confirm" else "Bob\n"
    suffix = " [y/N]: y\n" if kind == "confirm" else ": Bob\n"
    expected = f"{styled_label if color else 'Hello'}{suffix}"

    result = cli_runner_type().invoke(command, input=user_input, color=color)
    selected_stream = result.stderr if err else result.stdout
    other_stream = result.stdout if err else result.stderr
    passed = (
        result.exit_code == 0
        and result.exception is None
        and result.output == expected
        and selected_stream == expected
        and other_stream == ""
    )
    return {
        "kind": kind,
        "color": color,
        "err": err,
        "exit_code": result.exit_code,
        "output": result.output,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "expected": expected,
        "passed": passed,
    }


if __name__ == "__main__":
    raise SystemExit(main())
