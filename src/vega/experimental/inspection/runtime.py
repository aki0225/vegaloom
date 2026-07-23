from __future__ import annotations

from datetime import datetime
from inspect import signature
from pathlib import Path
from time import monotonic
from typing import Protocol

from . import eval as eval_runner
from .context_loader import load_target_context, parse_target_files
from .llm_client import LLMClient, LLMRequestError
from .loop_spec import default_engineering_change_spec
from ...models import LoopSpec, RunState, ToolResult
from ...redaction import assert_not_sensitive_path, is_sensitive_path, redact_text
from .reviewer import (
    UNANSWERED_TASK_DECLARATION,
    extract_task_questions,
    run_review,
    write_review,
)
from .state import StateStore
from .tool_broker import ToolBroker
from ...trace import TraceWriter
from ...run_utils import create_run_dir


class RuntimeLLMClient(Protocol):
    model: str
    provider_alias: str

    def available(self) -> bool: ...

    def generate_plan(self, task_text: str) -> str: ...

    def generate_report(
        self,
        task_text: str,
        tool_results: list[object],
        required_sections: list[str] | None = None,
    ) -> str: ...


class _RuntimeBudget:
    def __init__(self, spec: LoopSpec) -> None:
        self.max_steps = spec.budget.max_steps
        self.max_tool_calls = spec.budget.max_tool_calls
        self.max_minutes = spec.budget.max_minutes
        self.steps_used = 0
        self.tool_calls_used = 0
        self.deadline = monotonic() + max(0, self.max_minutes) * 60
        self.failure: dict[str, object] | None = None

    def begin_step(self, step: str) -> bool:
        if not self._within_time(step):
            return False
        if self.steps_used >= self.max_steps:
            self.failure = {
                "kind": "max_steps",
                "limit": self.max_steps,
                "used": self.steps_used,
                "operation": step,
            }
            return False
        self.steps_used += 1
        return True

    def reserve_tool_call(self, tool: str, operation: str) -> bool:
        if not self._within_time(f"{tool}:{operation}"):
            return False
        if self.tool_calls_used >= self.max_tool_calls:
            self.failure = {
                "kind": "max_tool_calls",
                "limit": self.max_tool_calls,
                "used": self.tool_calls_used,
                "operation": f"{tool}:{operation}",
            }
            return False
        self.tool_calls_used += 1
        return True

    def _within_time(self, operation: str) -> bool:
        if monotonic() < self.deadline:
            return True
        self.failure = {
            "kind": "max_minutes",
            "limit": self.max_minutes,
            "used": self.max_minutes,
            "operation": operation,
        }
        return False


class EngineeringChangeRuntime:
    def __init__(
        self,
        workspace: Path,
        loop_spec: LoopSpec | None = None,
        llm_client: RuntimeLLMClient | None = None,
    ) -> None:
        self.workspace = workspace
        self.loop_spec = loop_spec or default_engineering_change_spec()
        self._llm_client = llm_client

    @property
    def llm_client(self) -> RuntimeLLMClient:
        if self._llm_client is None:
            self._llm_client = LLMClient.from_env()
        return self._llm_client

    def run(self, task_file: Path, repo_path: Path) -> Path:
        assert_not_sensitive_path(task_file)
        task_path = task_file.resolve(strict=True)
        assert_not_sensitive_path(task_path)
        base_run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{self.loop_spec.name}"
        run_id, run_dir = create_run_dir(self.workspace, base_run_id)

        trace = TraceWriter(run_dir / "trace.jsonl")
        state = RunState(
            run_id=run_id,
            loop_name=self.loop_spec.name,
            repo_path=str(repo_path),
            task_file=str(task_path),
        )
        store = StateStore(run_dir)
        broker = ToolBroker(repo_path)
        budget = _RuntimeBudget(self.loop_spec)
        trace.write(
            "llm_config",
            llm_available=self.llm_client.available(),
            model=self.llm_client.model,
            provider_alias=self.llm_client.provider_alias,
        )
        trace.write(
            "loop_spec_loaded",
            name=self.loop_spec.name,
            allowed_tools=self.loop_spec.tools.allowed,
            git_checks=self.loop_spec.inspect.git_checks,
            budget=self.loop_spec.budget.model_dump(mode="json"),
        )

        state.status = "running"
        if not budget.begin_step("task_loaded"):
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        state.current_step = "task_loaded"
        raw_task_text = task_path.read_text(encoding="utf-8")
        target_files = parse_target_files(
            raw_task_text,
            self.loop_spec.inspect.target_section_names,
        )
        task_text = redact_text(raw_task_text)
        trace.write("task_loaded", task_file=str(task_path))
        trace.write(
            "target_files_parsed",
            count=len(target_files),
            targets=[
                "[SENSITIVE_PATH]" if is_sensitive_path(target) else redact_text(target)
                for target in target_files
            ],
        )
        store.save(state)

        if not budget.begin_step("plan"):
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        state.current_step = "plan"
        plan = redact_text(self._make_plan(task_text, trace))
        run_dir.joinpath("plan.md").write_text(plan, encoding="utf-8")
        trace.write("plan_written", file="plan.md")
        store.save(state)

        if not budget.begin_step("inspect"):
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        state.current_step = "inspect"
        self._inspect_targets(repo_path, target_files, state, trace, budget)
        if budget.failure:
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        self._search_context(broker, state, trace, budget)
        if budget.failure:
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        self._run_git_checks(broker, state, trace, budget)
        if budget.failure:
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        store.save(state)

        if not budget.begin_step("report"):
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        state.current_step = "report"
        report = redact_text(self._make_report(task_text, state, trace))
        run_dir.joinpath("report.md").write_text(report, encoding="utf-8")
        trace.write("report_written", file="report.md")

        store.save(state)

        if not budget.begin_step("review"):
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        state.current_step = "review"
        review_results = run_review(task_text, state, self.loop_spec, report)
        state.review_results.extend(review_results)
        write_review(run_dir, review_results)
        trace.write("review_written", file="review.md", results=review_results)
        store.save(state)

        if not budget.begin_step("eval"):
            return self._finish_budget_exceeded(run_dir, state, store, trace, budget)
        state.current_step = "eval"
        run_dir.joinpath("eval.md").write_text("# Eval\n\n(pending)\n", encoding="utf-8")
        store.save(state)
        eval_results = eval_runner.run_basic_eval(run_dir, self.loop_spec)
        state.eval_results.extend(eval_results)
        eval_runner.write_eval(run_dir, eval_results)
        trace.write("eval_written", file="eval.md", results=eval_results)

        state.status = "failed" if _has_failures(eval_results) else "success"
        state.current_step = "done"
        store.save(state)
        trace.write("run_finished", status=state.status)
        return run_dir

    def _inspect_targets(
        self,
        repo_path: Path,
        target_files: list[str],
        state: RunState,
        trace: TraceWriter,
        budget: _RuntimeBudget,
    ) -> None:
        if not _tool_allowed(self.loop_spec, "file.read"):
            trace.write("tool_skipped", tool="file.read", reason="not_allowed")
            return
        for target in target_files:
            if not budget.reserve_tool_call("file.read", target):
                return
            for result in load_target_context(repo_path, [target]):
                state.tool_results.append(result)
                trace.write(
                    "tool_call",
                    tool=result.tool,
                    target=(result.output or {}).get("path")
                    if isinstance(result.output, dict)
                    else None,
                    status=result.status,
                )

    def _search_context(
        self,
        broker: ToolBroker,
        state: RunState,
        trace: TraceWriter,
        budget: _RuntimeBudget,
    ) -> None:
        if not _tool_allowed(self.loop_spec, "file.search"):
            trace.write("tool_skipped", tool="file.search", reason="not_allowed")
            return
        for query in self.loop_spec.inspect.search_queries:
            if not budget.reserve_tool_call("file.search", query):
                return
            result = broker.file_search(query)
            state.tool_results.append(result)
            trace.write("tool_call", tool=result.tool, query=query, status=result.status)

    def _run_git_checks(
        self,
        broker: ToolBroker,
        state: RunState,
        trace: TraceWriter,
        budget: _RuntimeBudget,
    ) -> None:
        if not _tool_allowed(self.loop_spec, "repo.run_check"):
            trace.write("tool_skipped", tool="repo.run_check", reason="not_allowed")
            return
        for check_id in self.loop_spec.inspect.git_checks:
            if not budget.reserve_tool_call("repo.run_check", check_id):
                return
            result = broker.run_check(check_id)
            state.tool_results.append(result)
            trace.write("tool_call", tool=result.tool, check_id=check_id, status=result.status)

    def _finish_budget_exceeded(
        self,
        run_dir: Path,
        state: RunState,
        store: StateStore,
        trace: TraceWriter,
        budget: _RuntimeBudget,
    ) -> Path:
        failure = budget.failure or {
            "kind": "unknown",
            "limit": 0,
            "used": 0,
            "operation": state.current_step,
        }
        result = (
            "FAIL: runtime budget 超限："
            f"{failure['kind']}，used={failure['used']}，limit={failure['limit']}，"
            f"停止于 {failure['operation']}"
        )
        state.status = "failed"
        state.current_step = "budget_exceeded"
        state.eval_results.append(result)
        eval_runner.write_eval(run_dir, state.eval_results)
        store.save(state)
        trace.write(
            "budget_exceeded",
            **failure,
            steps_used=budget.steps_used,
            tool_calls_used=budget.tool_calls_used,
        )
        trace.write("eval_written", file="eval.md", results=state.eval_results)
        trace.write("run_finished", status=state.status)
        return run_dir

    def _make_plan(self, task_text: str, trace: TraceWriter) -> str:
        if self.llm_client.available():
            try:
                return self.llm_client.generate_plan(task_text)
            except Exception as exc:
                self._trace_llm_failure(trace, "llm_plan_failed", exc)
        return self._fallback_plan(task_text)

    @staticmethod
    def _fallback_plan(task_text: str) -> str:
        return (
            "# 计划\n\n"
            "1. 读取任务文件，明确目标、约束和需要回答的问题。\n"
            "2. 按 loop YAML 配置读取目标文件、搜索关键词并收集 git 检查结果。\n"
            "3. 通过 ToolBroker 记录所有只读工具调用，保持工具边界可审计。\n"
            "4. 基于证据生成工程审查报告。\n"
            "5. 执行 reviewer pass 与 eval，确认报告、工具策略和 artifact 完整性。\n\n"
            "## 任务摘录\n\n"
            f"{task_text[:1200]}\n"
        )

    def _make_report(self, task_text: str, state: RunState, trace: TraceWriter) -> str:
        if self.llm_client.available():
            try:
                if len(signature(self.llm_client.generate_report).parameters) >= 3:
                    return self.llm_client.generate_report(
                        task_text,
                        state.tool_results,
                        self.loop_spec.report.required_sections,
                    )
                return self.llm_client.generate_report(task_text, state.tool_results)
            except Exception as exc:
                self._trace_llm_failure(trace, "llm_report_failed", exc)
        return self._fallback_report(task_text, state)

    def _trace_llm_failure(self, trace: TraceWriter, event: str, exc: Exception) -> None:
        payload = {
            "llm_available": True,
            "model": self.llm_client.model,
            "provider_alias": self.llm_client.provider_alias,
            "error_type": type(exc).__name__,
        }
        if isinstance(exc, LLMRequestError):
            payload["error_kind"] = exc.kind
            if exc.http_status is not None:
                payload["http_status"] = exc.http_status
        trace.write(event, **payload)

    def _fallback_report(self, task_text: str, state: RunState) -> str:
        questions = extract_task_questions(task_text)
        section_content = {
            "摘要": [
                "本次运行使用 deterministic fallback 整理审查证据，未生成可替代人工判断的任务答案。",
                "运行只读收集上下文，不写入目标仓库、不提交、不发布。",
            ],
            "上下文": _context_lines(task_text, questions),
            "证据": [_summarize_tool_result(result) for result in state.tool_results],
            "发现": _finding_lines(questions),
            "风险": [
                "证据来自当前工具结果，若目标文件缺失或搜索词不足，结论需要人工补充确认。",
                "当前 runtime 不应用补丁，因此建议修改需要由后续人工或独立流程处理。",
            ],
            "建议修改": [
                "优先根据报告中的证据确认真实问题，再决定是否进入代码变更流程。",
            ],
            "验证": [
                "本次运行会生成 review.md 和 eval.md，用于检查报告覆盖、工具边界和 artifact 完整性。",
            ],
        }

        lines = [
            f"# {self.loop_spec.report.title}",
            "",
            f"> {UNANSWERED_TASK_DECLARATION}",
            "",
        ]
        for section in self.loop_spec.report.required_sections:
            lines.append(f"## {section}")
            lines.append("")
            content = section_content.get(section, ["该章节由 loop 配置要求保留，当前没有额外内容。"])
            lines.extend(f"- {item}" for item in content if item)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _context_lines(task_text: str, questions: list[str]) -> list[str]:
    lines = ["任务摘要来自输入 task 文件，runtime 不额外推断未提供的业务背景。"]
    if questions:
        lines.append("任务问题：" + "；".join(questions))
    else:
        lines.append("任务没有显式 Questions 区块，报告按整体任务目标进行审查。")
    return lines


def _finding_lines(questions: list[str]) -> list[str]:
    if not questions:
        return ["已完成只读上下文收集；需要人工结合证据判断下一步变更。"]
    return [f"待回答问题（fallback 未形成答案）：{question}" for question in questions]


def _summarize_tool_result(result: ToolResult) -> str:
    if isinstance(result.output, dict):
        if result.tool == "file.search":
            matches = result.output.get("matches") or []
            return f"`file.search` 查询 `{result.output.get('query')}`，命中 {len(matches)} 条，状态 {result.status}。"
        if result.tool == "file.read":
            return f"`file.read` 读取 `{result.output.get('path')}`，状态 {result.status}。"
        if result.tool == "repo.run_check":
            return (
                f"`repo.run_check` 执行 `{result.output.get('check_id')}`，"
                f"退出码 {result.output.get('exit_code')}。"
            )
    if result.error:
        return f"`{result.tool}` 状态 {result.status}，错误：{result.error}。"
    return f"`{result.tool}` 状态 {result.status}。"


def _tool_allowed(spec: LoopSpec, tool_name: str) -> bool:
    return tool_name in set(spec.tools.allowed) and tool_name not in set(spec.tools.disabled)


def _has_failures(results: list[str]) -> bool:
    return any(result.startswith("FAIL:") for result in results)
