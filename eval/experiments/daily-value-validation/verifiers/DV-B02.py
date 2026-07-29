from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：python verifier.py <attrs-workspace>", file=sys.stderr)
        return 2

    repo_root = Path(sys.argv[1]).resolve()
    source_root = repo_root / "src"
    if not source_root.is_dir():
        print(f"未找到 attrs 源码目录：{source_root}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(source_root))
    import attr

    imported_from = Path(attr.__file__).resolve()
    if source_root not in imported_from.parents:
        print(f"attrs 导入来源不属于目标工作树：{imported_from}", file=sys.stderr)
        return 2

    checks = [
        _check_optional_pipe_value(attr),
        _check_optional_pipe_none(attr),
        _check_context_forwarding(attr),
        _check_plain_callable_compatibility(attr),
    ]
    failures = [item for item in checks if not item["passed"]]
    payload = {
        "schema_version": 1,
        "repository": "python-attrs/attrs",
        "issue_number": 1348,
        "python": sys.version.split()[0],
        "attr_import": str(imported_from.relative_to(repo_root)),
        "checks": checks,
        "passed": not failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def _check_optional_pipe_value(attr) -> dict[str, object]:
    try:

        @attr.define
        class Example:
            value: int | None = attr.field(
                converter=attr.converters.optional(attr.converters.pipe(str, int)),
                default=None,
            )

        actual = Example("7").value
        return _result("optional_pipe_value", actual == 7, actual=actual, expected=7)
    except Exception as exc:
        return _error("optional_pipe_value", exc)


def _check_optional_pipe_none(attr) -> dict[str, object]:
    try:

        @attr.define
        class Example:
            value: int | None = attr.field(
                converter=attr.converters.optional(attr.converters.pipe(str, int)),
                default=None,
            )

        actual = Example().value
        return _result("optional_pipe_none", actual is None, actual=actual, expected=None)
    except Exception as exc:
        return _error("optional_pipe_none", exc)


def _check_context_forwarding(attr) -> dict[str, object]:
    try:

        def contextual(value, instance, field):
            return f"{instance.prefix}:{field.name}:{value}"

        converter = attr.Converter(contextual, takes_self=True, takes_field=True)

        @attr.define
        class Example:
            prefix: str
            value: str | None = attr.field(
                converter=attr.converters.optional(
                    attr.converters.pipe(str, converter)
                ),
                default=None,
            )

        actual = Example("scope", "item").value
        expected = "scope:value:item"
        return _result(
            "context_forwarding",
            actual == expected,
            actual=actual,
            expected=expected,
        )
    except Exception as exc:
        return _error("context_forwarding", exc)


def _check_plain_callable_compatibility(attr) -> dict[str, object]:
    try:
        converter = attr.converters.optional(int)
        actual = [converter(None), converter("9")]
        expected = [None, 9]
        return _result(
            "plain_callable_compatibility",
            actual == expected,
            actual=actual,
            expected=expected,
        )
    except Exception as exc:
        return _error("plain_callable_compatibility", exc)


def _result(name: str, passed: bool, *, actual, expected) -> dict[str, object]:
    return {"name": name, "passed": passed, "actual": actual, "expected": expected}


def _error(name: str, exc: Exception) -> dict[str, object]:
    return {
        "name": name,
        "passed": False,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


if __name__ == "__main__":
    raise SystemExit(main())
