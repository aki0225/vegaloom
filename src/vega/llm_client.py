from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from .redaction import redact_text, redact_value


DEFAULT_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "xhigh"
DEFAULT_PROVIDER_ALIAS = "ciii"
DEFAULT_TIMEOUT_SECONDS = 180.0


class LLMRequestError(RuntimeError):
    def __init__(self, kind: str, http_status: int | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.http_status = http_status


@dataclass(frozen=True)
class LLMClient:
    api_key: str | None
    base_url: str | None
    model: str = DEFAULT_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    provider_alias: str = DEFAULT_PROVIDER_ALIAS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMClient":
        source = os.environ if env is None else env
        return cls(
            api_key=source.get("VEGA_API_KEY") or None,
            base_url=source.get("VEGA_BASE_URL") or None,
            model=source.get("VEGA_MODEL") or DEFAULT_MODEL,
            reasoning_effort=source.get("VEGA_REASONING_EFFORT")
            or DEFAULT_REASONING_EFFORT,
            provider_alias=source.get("VEGA_PROVIDER_ALIAS") or DEFAULT_PROVIDER_ALIAS,
            timeout_seconds=_parse_timeout(source.get("VEGA_TIMEOUT_SECONDS")),
        )

    def available(self) -> bool:
        return bool(self.api_key and self.base_url)

    def generate_plan(self, task_text: str) -> str:
        safe_task_text = redact_text(task_text)
        return self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你只输出简体中文 Markdown 计划。"
                        "不要使用第一人称，不要描述模型思考过程。"
                        "不要把整段回答包在 Markdown 代码块中。"
                        "不要提出自动改代码、自动提交、自动发布、数据库、Web UI、"
                        "多 Agent 编排或自动写长期记忆。"
                    ),
                },
                {
                    "role": "user",
                    "content": "为这个 Vega 研发变更审查任务生成计划。\n\n任务：\n"
                    + safe_task_text,
                },
            ]
        )

    def generate_report(
        self,
        task_text: str,
        tool_results: list[Any],
        required_sections: list[str] | None = None,
    ) -> str:
        sections = required_sections or ["摘要", "上下文", "证据", "发现", "风险", "建议修改", "验证", "记忆提案"]
        safe_task_text = redact_text(task_text)
        serialized_results = json.dumps(
            redact_value([self._serialize_tool_result(result) for result in tool_results]),
            ensure_ascii=False,
            indent=2,
        )
        section_text = "、".join(sections)
        return self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你基于只读工具结果写简体中文研发变更审查报告。"
                        "不要声称已经修改文件，不要包含密钥。"
                        "不要把整段回答包在 Markdown 代码块中。"
                        "报告标题必须是 # 工程变更报告。"
                        f"二级标题必须覆盖这些章节：{section_text}。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "请根据任务和工具结果写报告。结论只能基于给定工具结果；"
                        "如果证据不足，需要明确说明。\n\n"
                        f"任务：\n{safe_task_text}\n\n工具结果：\n{serialized_results}"
                    ),
                },
            ]
        )

    def _chat(self, messages: list[dict[str, str]]) -> str:
        if not self.available():
            raise RuntimeError("LLM client 未配置")

        assert self.base_url is not None
        assert self.api_key is not None
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        safe_messages = [
            {"role": item["role"], "content": redact_text(item["content"])}
            for item in messages
        ]
        payload = {
            "model": self.model,
            "messages": safe_messages,
            "reasoning_effort": self.reasoning_effort,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise LLMRequestError("http_error", http_status=exc.code) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError | socket.timeout):
                raise LLMRequestError("timeout") from exc
            raise LLMRequestError("url_error") from exc
        except TimeoutError as exc:
            raise LLMRequestError("timeout") from exc

        return self._extract_chat_content(data)

    @staticmethod
    def _extract_chat_content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM 响应缺少 choices[0].message.content") from exc

        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            ).strip()
        else:
            text = ""

        if not text:
            raise RuntimeError("LLM 响应内容为空")
        return redact_text(_strip_wrapping_markdown_fence(text)) + "\n"

    @staticmethod
    def _serialize_tool_result(result: Any) -> dict[str, Any]:
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if isinstance(result, dict):
            return result
        return {"value": str(result)}


def _parse_timeout(value: str | None) -> float:
    if not value:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        parsed = float(value)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if parsed <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return parsed


def _strip_wrapping_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 2 or not lines[0].startswith("```") or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()
