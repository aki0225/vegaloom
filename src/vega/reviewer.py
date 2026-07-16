from __future__ import annotations

import re
from pathlib import Path

from .models import LoopSpec, RunState


QUESTION_SECTION_MARKERS = ("questions", "问题")
SECTION_STOP_MARKERS = ("output", "输出", "验收", "acceptance")
UNANSWERED_TASK_DECLARATION = "任务答案状态：未生成"


def extract_task_questions(task_text: str) -> list[str]:
    questions: list[str] = []
    in_questions = False

    for line in task_text.splitlines():
        stripped = line.strip()
        normalized = stripped.strip("#").strip().rstrip(":：").lower()

        if any(marker in normalized for marker in QUESTION_SECTION_MARKERS):
            in_questions = True
            continue

        if in_questions and any(marker == normalized for marker in SECTION_STOP_MARKERS):
            break

        if in_questions:
            if not stripped:
                if questions:
                    break
                continue
            item = _strip_list_marker(stripped)
            if item:
                questions.append(item)
            elif questions:
                break
        elif stripped.endswith(("?", "？")):
            questions.append(_strip_list_marker(stripped))

    return questions


def run_review(task_text: str, state: RunState, spec: LoopSpec, report_text: str) -> list[str]:
    if not spec.review.enabled:
        return ["PASS: reviewer 已禁用"]

    checks: list[str] = []
    enabled = set(spec.review.checks)

    if "report_answers_task" in enabled:
        checks.append(_check_report_answers_task(task_text, report_text))
    if "no_unsupported_file_changes" in enabled:
        checks.append(_check_no_unsupported_file_changes(report_text, state))
    if "no_forbidden_actions" in enabled:
        checks.append(_check_no_forbidden_actions(report_text, spec.review.forbidden_phrases))
    if "tool_policy_respected" in enabled:
        checks.append(_check_tool_policy_respected(state, spec))

    checks.append(_check_memory_policy(state))
    return checks


def write_review(run_dir: Path, results: list[str]) -> None:
    content = "# Review\n\n" + "\n".join(f"- {item}" for item in results) + "\n"
    run_dir.joinpath("review.md").write_text(content, encoding="utf-8")


def _check_report_answers_task(task_text: str, report_text: str) -> str:
    questions = extract_task_questions(task_text)
    if questions:
        missing = [
            question for question in questions if not _has_answer_signal(report_text, question)
        ]
        if missing:
            return f"FAIL: 报告没有覆盖 {len(missing)} 个任务问题"
    if UNANSWERED_TASK_DECLARATION in report_text:
        return "FAIL: 报告明确声明未生成任务答案"
    if not questions:
        return "PASS: 任务没有显式问题，跳过问题覆盖检查"
    return f"PASS: 报告覆盖 {len(questions)} 个任务问题"


def _check_no_unsupported_file_changes(report_text: str, state: RunState) -> str:
    forbidden_tools = [result.tool for result in state.tool_results if result.tool == "patch.apply"]
    if forbidden_tools:
        return "FAIL: 运行中出现 patch.apply，违反只读审查边界"

    suspicious = ["已经修改文件", "已修改文件", "applied patch", "modified files", "committed changes"]
    lower = report_text.lower()
    if any(phrase.lower() in lower for phrase in suspicious):
        return "FAIL: 报告疑似声称已经修改文件"
    return "PASS: 未发现自动修改文件声明"


def _check_no_forbidden_actions(report_text: str, forbidden_phrases: list[str]) -> str:
    matched = _find_non_negated_forbidden_phrases(report_text, forbidden_phrases)
    if matched:
        return f"FAIL: 报告出现禁用动作表述：{', '.join(matched)}"
    return "PASS: 未发现自动提交、发布或补丁动作表述"


def _find_non_negated_forbidden_phrases(text: str, forbidden_phrases: list[str]) -> list[str]:
    lower = text.lower()
    matched: list[str] = []
    negations = ["不", "未", "没有", "不会", "不要", "禁止", "避免", "no ", "not ", "without "]
    for phrase in forbidden_phrases:
        needle = phrase.lower()
        start = 0
        while True:
            index = lower.find(needle, start)
            if index < 0:
                break
            prefix = lower[max(0, index - 8):index]
            if not any(negation in prefix for negation in negations):
                matched.append(phrase)
                break
            start = index + len(needle)
    return matched


def _check_tool_policy_respected(state: RunState, spec: LoopSpec) -> str:
    allowed = set(spec.tools.allowed)
    used = {result.tool for result in state.tool_results}
    unexpected = sorted(used - allowed)
    if unexpected:
        return f"FAIL: 工具调用不在 allowlist 中：{', '.join(unexpected)}"
    return "PASS: 工具调用符合 allowlist"


def _check_memory_policy(state: RunState) -> str:
    if state.memory_proposals:
        return "FAIL: engineering-change 不应自动生成 Memory Proposal"
    return "PASS: engineering-change 未自动生成 Memory Proposal"


def _has_answer_signal(report_text: str, question: str) -> bool:
    report_lower = report_text.lower()
    question_lower = question.lower()
    if question_lower:
        for line in report_lower.splitlines():
            if question_lower not in line:
                continue
            remainder = line.replace(question_lower, " ")
            if _contains_answer_marker(remainder):
                return True
        report_lower = report_lower.replace(question_lower, " ")

    tokens = _extract_signal_tokens(question)
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token.lower() in report_lower)
    required = min(3, max(1 if len(tokens) == 1 else 2, (len(tokens) + 1) // 2))
    return hits >= required


def _contains_answer_marker(text: str) -> bool:
    unresolved_markers = (
        "待回答",
        "未回答",
        "无法回答",
        "需要人工",
        "pending",
        "unanswered",
        "unknown",
    )
    if any(marker in text for marker in unresolved_markers):
        return False
    answer_markers = (
        "答案",
        "结论",
        "已确认",
        "已检查",
        "已基于",
        "支持",
        "不支持",
        "存在",
        "不存在",
        "通过",
        "失败",
        " yes",
        " no",
    )
    return any(marker in text for marker in answer_markers)


def _extract_signal_tokens(text: str) -> list[str]:
    raw_tokens = re.findall(r"/[\w/.-]+|[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "does",
        "whether",
        "avoid",
        "是否",
        "有没有",
        "检查",
        "说明",
    }
    return [token for token in raw_tokens if token.lower() not in stop_words]


def _strip_list_marker(line: str) -> str:
    value = re.sub(r"^[-*]\s+", "", line)
    value = re.sub(r"^\d+[.)]\s+", "", value)
    return value.strip()
