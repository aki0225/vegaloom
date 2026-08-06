from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def _argument_value(args: list[str], flag: str) -> str:
    try:
        index = args.index(flag)
        return args[index + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"缺少参数：{flag}") from exc


def _changed_files(prompt: str) -> list[str]:
    marker = "## 完整变更文件清单"
    if marker not in prompt:
        raise ValueError("Prompt 缺少完整变更文件清单")
    section = prompt.split(marker, 1)[1]
    section = section.split("\n## ", 1)[0]
    return re.findall(r"^- `([^`]+)`$", section, flags=re.MULTILINE)


def _first_candidate(prompt: str) -> str | None:
    marker = "## impact-candidates.json"
    if marker not in prompt:
        return None
    match = re.search(r'"path":\s*"([^"]+)"', prompt.split(marker, 1)[1])
    return match.group(1) if match is not None else None


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] != "exec":
        print("fake Codex 只接受 exec", file=sys.stderr)
        return 2
    try:
        if _argument_value(args, "--sandbox") != "read-only":
            raise ValueError("sandbox 不是 read-only")
        if _argument_value(args, "--model") != "gpt-5.6-sol":
            raise ValueError("model 不一致")
        if 'model_reasoning_effort="high"' not in args:
            raise ValueError("reasoning effort 不一致")
        if "--ephemeral" not in args or "--json" not in args or args[-1] != "-":
            raise ValueError("ephemeral/json/stdin 合同不完整")
        for feature in ("hooks", "memories", "plugins"):
            pairs = zip(args, args[1:], strict=False)
            if ("--disable", feature) not in pairs:
                raise ValueError(f"未禁用 {feature}")
        schema_path = Path(_argument_value(args, "--output-schema"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if "reviewed_files" not in schema.get("properties", {}):
            raise ValueError("output schema 缺少 reviewed_files")
        prompt = sys.stdin.buffer.read().decode("utf-8")
        changed_files = _changed_files(prompt)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"fake Codex 参数校验失败：{exc}", file=sys.stderr)
        return 2

    _emit({"type": "thread.started", "thread_id": "fake-thread"})
    _emit({"type": "turn.started"})
    if os.environ.get("RCB01_FAKE_MODE") == "invalid_json":
        print("{invalid-json", flush=True)
        return 0

    candidate = _first_candidate(prompt)
    if candidate is not None:
        item = {
            "id": "fake-command-1",
            "type": "command_execution",
            "command": f'Get-Content "{candidate}"',
        }
        _emit({"type": "item.started", "item": item})
        _emit(
            {
                "type": "item.completed",
                "item": {
                    **item,
                    "status": "completed",
                    "aggregated_output": "fake read-only output",
                },
            }
        )

    verdict = {
        "verdict": "approve",
        "summary": "fake runner 仅验证本地实验控制面，不代表真实审查结论。",
        "findings": [],
        "risk_disclosures": [],
        "reviewed_files": changed_files,
        "checked_items": ["需求覆盖", "测试覆盖", "项目规则", "安全风险"],
    }
    _emit(
        {
            "type": "item.completed",
            "item": {
                "id": "fake-message",
                "type": "agent_message",
                "text": json.dumps(verdict, ensure_ascii=False, separators=(",", ":")),
            },
        }
    )
    _emit(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 50,
                "reasoning_output_tokens": 25,
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
