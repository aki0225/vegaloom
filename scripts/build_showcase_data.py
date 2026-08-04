from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path("examples/evidence")
DEFAULT_OUTPUT = Path("site/data/cases.json")

_ALLOWED_STATUS = {"ready_to_commit", "request_changes", "needs_human"}
_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(r"\\\\[^\\\s]+\\[^\\\s]+"),
    re.compile(r"(?<![A-Za-z0-9:])/(?:home|Users)/[^/\s\"'`<>]+(?:/|$)"),
)
_SECRET_PATTERNS = (
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|session[_-]?cookie)\s*[:=]"),
)
_UNSAFE_MODEL_CONTENT = (
    re.compile(r"(?i)\bworker prompt\b"),
    re.compile(r"(?i)\bchain[- ]of[- ]thought\b"),
    re.compile(r"中间推理"),
    re.compile(r"原始提示词"),
)

_SOURCE_CHECKS = {
    "pycodestyle-1187-rejection": (
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "reviewer-rejection/verification-summary.md",
            "`768 passed, 5 skipped`",
        ),
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "reviewer-rejection/verification-summary.md",
            "固定测试通过不代表行为合同完整",
        ),
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "reviewer-rejection/final-report.md",
            "隔离 reviewer 返回 `request_changes`",
        ),
    ),
    "pycodestyle-1187-success": (
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "success/verification-summary.md",
            "`768 passed, 5 skipped`",
        ),
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "success/verification-summary.md",
            "8 个正反断言全部通过",
        ),
        (
            "examples/evidence/real-world-pycodestyle-1187/success/final-report.md",
            "Finish 状态：`ready_to_commit`",
        ),
    ),
    "click-2939-success": (
        (
            "examples/evidence/real-world-click-2939/"
            "success/verification-summary.md",
            "`688 passed, 72 skipped, 1 xfailed`",
        ),
        (
            "examples/evidence/real-world-click-2939/"
            "success/verification-summary.md",
            "独立 oracle",
        ),
        (
            "examples/evidence/real-world-click-2939/success/final-report.md",
            "Finish 状态：`ready_to_commit`",
        ),
    ),
}


def _read_json(relative_path: str) -> dict[str, Any]:
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _source_links(base_path: str) -> list[dict[str, str]]:
    labels = (
        ("diff", "实际 Diff", "diff.patch"),
        ("verification", "验证摘要", "verification-summary.md"),
        ("review", "Reviewer 结论", "review-verdict.json"),
        ("gate", "Gate 结果", "gate-result.json"),
        ("finish", "最终报告", "final-report.md"),
    )
    return [
        {
            "kind": kind,
            "label": label,
            "path": f"{base_path}/{filename}",
        }
        for kind, label, filename in labels
    ]


def _scope_summary(gate: dict[str, Any]) -> str:
    scope_gate = gate["scope_gate"]
    stages = ("pre_verification", "post_verification", "pre_review")
    changed_files = sorted(
        {
            path
            for stage in stages
            for path in scope_gate[stage]["changed_files"]
        }
    )
    if any(scope_gate[stage]["status"] != "success" for stage in stages):
        return "至少一个 Scope Gate 未通过。"
    return (
        "pre-verification、post-verification、pre-review 均通过；"
        f"变更仅涉及 {', '.join(changed_files)}。"
    )


def _risk_summary(gate: dict[str, Any]) -> str:
    risk_gate = gate["risk_gate"]
    return (
        f"{risk_gate['status']} · {risk_gate['risk']} · "
        f"{risk_gate['recommendation']}"
    )


def build_payload() -> dict[str, Any]:
    """从已提交的脱敏证据包生成展示数据。"""
    rejection_base = (
        "examples/evidence/real-world-pycodestyle-1187/reviewer-rejection"
    )
    success_base = "examples/evidence/real-world-pycodestyle-1187/success"
    click_base = "examples/evidence/real-world-click-2939/success"

    rejection_review = _read_json(f"{rejection_base}/review-verdict.json")
    rejection_gate = _read_json(f"{rejection_base}/gate-result.json")
    rejection_finding = rejection_review["findings"][0]

    success_review = _read_json(f"{success_base}/review-verdict.json")
    success_gate = _read_json(f"{success_base}/gate-result.json")

    click_review = _read_json(f"{click_base}/review-verdict.json")
    click_gate = _read_json(f"{click_base}/gate-result.json")

    cases = [
        {
            "id": "pycodestyle-1187-rejection",
            "kind": "Reviewer 阻断",
            "title": "pycodestyle #1187",
            "subtitle": "768 项测试通过，仍发现语义回归",
            "issue_url": "https://github.com/PyCQA/pycodestyle/issues/1187",
            "status": "request_changes",
            "status_label": "测试全绿，但不能交付",
            "summary": (
                "初始补丁修复了反向内建类型比较，却删除了原有歧义场景豁免。"
                "固定测试全部通过，隔离 Reviewer 仍识别出新的 E721 误报。"
            ),
            "diff": {
                "file": "pycodestyle.py",
                "summary": "1 个文件 · 删除 3 行保护逻辑",
                "excerpt": (
                    "-        inst = match.group(1)\n"
                    "-        if inst and inst.isidentifier() and inst not in SINGLETONS:\n"
                    "-            return  # Allow comparison for types which are not obvious"
                ),
            },
            "verification": {
                "headline": "768 passed, 5 skipped",
                "checks": [
                    "初始定向 oracle 能识别反向内建类型比较。",
                    "三阶段 Scope Gate 只检测到 pycodestyle.py。",
                    "固定测试没有覆盖普通小写变量的歧义比较。",
                ],
            },
            "review": {
                "verdict": rejection_review["verdict"],
                "severity": rejection_finding["severity"],
                "title": rejection_finding["title"],
                "evidence": rejection_finding["evidence"],
                "recommendation": rejection_finding["recommendation"],
            },
            "gates": {
                "scope": _scope_summary(rejection_gate),
                "risk": _risk_summary(rejection_gate),
                "finish": "needs_human · 保留候选现场 · 未自动提交",
            },
            "source_links": _source_links(rejection_base),
            "limitations": [
                "公开包是脱敏摘要，不包含 Worker 或 Reviewer 的完整会话。",
                "该案例证明 Reviewer 阻止了已知语义回归，不构成通用缺陷发现率。",
            ],
        },
        {
            "id": "pycodestyle-1187-success",
            "kind": "修正后通过",
            "title": "pycodestyle #1187",
            "subtitle": "补齐正反行为合同后的新候选",
            "issue_url": "https://github.com/PyCQA/pycodestyle/issues/1187",
            "status": "ready_to_commit",
            "status_label": "证据完整，可人工检查",
            "summary": (
                "新的隔离候选保留普通小写变量豁免，只对明确的内建类型或类型命名报告 E721；"
                "正反行为、静态检查、范围门禁和独立评审均通过。"
            ),
            "diff": {
                "file": "pycodestyle.py",
                "summary": "1 个文件 · 明确类型识别边界",
                "excerpt": (
                    "-        inst = match.group(1)\n"
                    "+        compared = match.group(1)\n"
                    "+        inst = match.group(2)\n"
                    "         if inst and inst.isidentifier() and inst not in SINGLETONS:\n"
                    "-            return  # Allow comparison for types which are not obvious\n"
                    "+            if not compared:\n"
                    "+                return  # Allow comparison for types which are not obvious\n"
                    "+            if (\n"
                    "+                    compared not in BUILTIN_TYPE_NAMES and\n"
                    "+                    not compared.lstrip('_')[:1].isupper()):\n"
                    "+                return  # Allow comparison for types which are not obvious"
                ),
            },
            "verification": {
                "headline": "768 passed, 5 skipped",
                "checks": [
                    "8 个正反行为断言全部通过。",
                    "compileall、pycodestyle 自检与 git diff --check 通过。",
                    "Scope Gate 始终只检测到 pycodestyle.py。",
                ],
            },
            "review": {
                "verdict": success_review["verdict"],
                "severity": "none",
                "title": "明确行为合同得到满足",
                "evidence": success_review["summary"],
                "recommendation": "人工检查最终 Diff 后再决定是否提交。",
            },
            "gates": {
                "scope": _scope_summary(success_gate),
                "risk": _risk_summary(success_gate),
                "finish": "ready_to_commit · 未自动提交",
            },
            "source_links": _source_links(success_base),
            "limitations": [
                "这是新的隔离候选，不是对原拒绝现场的自动覆盖或静默续跑。",
                "公开 Git 历史不能独立证明 follow-up 行为合同的预注册顺序。",
            ],
        },
        {
            "id": "click-2939-success",
            "kind": "标准闭环",
            "title": "Click #2939",
            "subtitle": "真实 Diff、独立 oracle 与完整测试",
            "issue_url": "https://github.com/pallets/click/issues/2939",
            "status": "ready_to_commit",
            "status_label": "证据完整，可人工检查",
            "summary": (
                "Worker 将 stdin 文件迭代与 prompt EOF 的语义分离；独立 oracle、完整测试、"
                "三阶段范围门禁和只读 Reviewer 共同约束最终结论。"
            ),
            "diff": {
                "file": "src/click/testing.py",
                "summary": "2 个预注册文件 · 修复 EOF 语义",
                "excerpt": (
                    "+        def input_readline() -> str:\n"
                    "+            line = text_input.readline()\n"
                    "+\n"
                    '+            if line == "":\n'
                    "+                raise EOFError()"
                ),
            },
            "verification": {
                "headline": "688 passed, 72 skipped, 1 xfailed",
                "checks": [
                    "基线独立 oracle 在 Click 8.2.1 上稳定失败。",
                    "修复后 stdin 链式迭代正常结束且不输出 Aborted!。",
                    "prompt EOF 仍保持既有中止语义，git diff --check 通过。",
                ],
            },
            "review": {
                "verdict": click_review["verdict"],
                "severity": "none",
                "title": "stdin 迭代与 prompt EOF 语义已分离",
                "evidence": "；".join(click_review["checked_items"]),
                "recommendation": "人工复核两个允许文件的 Diff 后再决定是否提交。",
            },
            "gates": {
                "scope": _scope_summary(click_gate),
                "risk": _risk_summary(click_gate),
                "finish": "ready_to_commit · 未自动提交",
            },
            "source_links": _source_links(click_base),
            "limitations": [
                "公开包不包含原始运行目录、完整模型输出或 prompt 全文。",
                "单案例不能外推为任意仓库或任意任务的成功率。",
            ],
        },
    ]

    return {
        "schema_version": 2,
        "generated_from": [
            "examples/evidence/real-world-pycodestyle-1187",
            "examples/evidence/real-world-click-2939",
        ],
        "evidence_through": "2026-08-04",
        "cases": cases,
    }


def _validate_source_path(relative_path: str) -> Path:
    if Path(relative_path).is_absolute():
        raise ValueError("公开证据路径必须是仓库相对路径")
    resolved = (REPO_ROOT / relative_path).resolve()
    evidence_root = (REPO_ROOT / EVIDENCE_ROOT).resolve()
    if not resolved.is_relative_to(evidence_root):
        raise ValueError(f"公开证据路径逃逸 examples/evidence：{relative_path}")
    if not resolved.is_file():
        raise ValueError(f"公开证据文件不存在：{relative_path}")
    return resolved


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 2:
        raise ValueError("展示案例 schema_version 必须为 2")

    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ValueError("展示站 V1.1 必须恰好包含 3 个主案例")

    expected_status = {
        "pycodestyle-1187-rejection": "request_changes",
        "pycodestyle-1187-success": "ready_to_commit",
        "click-2939-success": "ready_to_commit",
    }
    seen_ids: set[str] = set()
    required_fields = {
        "id",
        "kind",
        "title",
        "subtitle",
        "issue_url",
        "status",
        "status_label",
        "summary",
        "diff",
        "verification",
        "review",
        "gates",
        "source_links",
        "limitations",
    }

    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("每个案例必须是对象")
        missing = sorted(required_fields - case.keys())
        if missing:
            raise ValueError(f"案例缺少字段：{missing}")

        case_id = case["id"]
        if case_id in seen_ids:
            raise ValueError(f"案例 id 重复：{case_id}")
        seen_ids.add(case_id)
        if case_id not in expected_status:
            raise ValueError(f"未知展示案例：{case_id}")
        if case["status"] not in _ALLOWED_STATUS:
            raise ValueError(f"未知 Finish 状态：{case['status']}")
        if case["status"] != expected_status[case_id]:
            raise ValueError(f"{case_id} 的真实终态被改写")

        if not isinstance(case["limitations"], list) or not case["limitations"]:
            raise ValueError(f"{case_id} 必须披露证据限制")
        if not isinstance(case["source_links"], list) or len(case["source_links"]) < 4:
            raise ValueError(f"{case_id} 必须链接可复核的证据文件")

        source_paths: dict[str, Path] = {}
        for source in case["source_links"]:
            kind = source.get("kind")
            path = source.get("path")
            if not isinstance(kind, str) or not isinstance(path, str):
                raise ValueError(f"{case_id} 的证据链接格式无效")
            if kind in source_paths:
                raise ValueError(f"{case_id} 的证据类型重复：{kind}")
            source_paths[kind] = _validate_source_path(path)

        diff_source = source_paths.get("diff")
        if diff_source is None:
            raise ValueError(f"{case_id} 缺少 Diff 证据")
        excerpt = case["diff"].get("excerpt")
        if not isinstance(excerpt, str) or excerpt not in diff_source.read_text(
            encoding="utf-8"
        ):
            raise ValueError(f"{case_id} 的 Diff 摘录与证据文件不一致")

        review_source = source_paths.get("review")
        if review_source is None:
            raise ValueError(f"{case_id} 缺少 Reviewer 证据")
        review_payload = json.loads(review_source.read_text(encoding="utf-8"))
        if case["review"].get("verdict") != review_payload.get("verdict"):
            raise ValueError(f"{case_id} 的 Reviewer verdict 与证据文件不一致")

        for source_path, expected in _SOURCE_CHECKS[case_id]:
            source_text = _validate_source_path(source_path).read_text(encoding="utf-8")
            if expected not in source_text:
                raise ValueError(f"{case_id} 的来源证据缺失：{expected!r}")

    if seen_ids != set(expected_status):
        raise ValueError("展示案例集合与人工核准清单不一致")

    rejection = next(
        case for case in cases if case["id"] == "pycodestyle-1187-rejection"
    )
    if rejection["review"]["severity"] != "major":
        raise ValueError("Reviewer 阻断案例必须保留 major finding")
    if "needs_human" not in rejection["gates"]["finish"]:
        raise ValueError("Reviewer 阻断案例必须保留 needs_human 终态")

    for text in _iter_strings(payload):
        for pattern in (*_PATH_PATTERNS, *_SECRET_PATTERNS, *_UNSAFE_MODEL_CONTENT):
            match = pattern.search(text)
            if match:
                raise ValueError(f"公开案例包含禁止内容：{match.group(0)!r}")

    serialized = json.dumps(payload, ensure_ascii=False)
    if re.search(r"(?i)success[_ -]?rate|成功率\s*[:=]\s*\d", serialized):
        raise ValueError("展示案例不得生成总体成功率")


def render_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成或核对 GitHub Pages 展示案例")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只核对已提交的数据，不写入文件",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出路径，默认 site/data/cases.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.output
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path

    payload = build_payload()
    validate_payload(payload)
    expected = render_payload(payload)

    if args.check:
        if not output_path.exists():
            print(f"展示案例文件不存在：{output_path.relative_to(REPO_ROOT)}")
            return 1
        actual = output_path.read_text(encoding="utf-8")
        if actual != expected:
            print("展示案例与脱敏证据包及人工核准清单不一致，请重新运行生成命令")
            return 1
        print("展示案例数据与脱敏证据包及人工核准清单一致")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(expected, encoding="utf-8", newline="\n")
    print(f"已生成展示案例：{output_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
