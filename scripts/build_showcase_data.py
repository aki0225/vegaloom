from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_RECORD = Path("eval/real-world-runs.md")
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


def build_payload() -> dict[str, Any]:
    """返回人工核准的公开案例；这里不从 Markdown 猜测事实。"""
    cases = [
        {
            "id": "anyio-1231",
            "kind": "标准路径",
            "title": "AnyIO #1231",
            "subtitle": "边界明确的小范围修复",
            "issue_url": "https://github.com/agronholm/anyio/issues/1231",
            "source_record": SOURCE_RECORD.as_posix(),
            "source_heading": "2026-08-01 独立 fresh auto Dogfood：AnyIO #1231",
            "status": "ready_to_commit",
            "status_label": "可以人工检查后提交",
            "summary": "一轮完成小范围补丁，验证、范围门禁和独立审查均有可核对结果。",
            "changed_files": 3,
            "diff_summary": "+23 / -1",
            "change_summary": (
                "只修改 Trio 后端、任务组回归测试和版本记录，共 3 个预注册文件。"
            ),
            "gate_summary": "Workspace、三阶段 Scope 与 Risk Gate 全部通过。",
            "verification_summary": (
                "5 条命令全部通过；相关测试 24 passed，完整任务组测试 "
                "496 passed、10 skipped、4 xfailed。"
            ),
            "reviewer_summary": "Reviewer 返回 approve，findings 为 0。",
            "evidence_limit": (
                "这是 Issue 已明确期望行为的单案例，不证明未知缺陷发现能力、"
                "模型未见过上游修复或跨仓库成功率。"
            ),
            "next_step": "人工检查 3 个文件的 Diff，再决定是否提交。",
            "timeline": [
                {"label": "Baseline", "result": "冻结真实仓库修订与允许路径"},
                {"label": "Worker", "result": "3 个文件，+23 / -1"},
                {"label": "Gates", "result": "范围、工作区与风险检查通过"},
                {"label": "Verify", "result": "5 条验证命令通过"},
                {"label": "Review", "result": "approve，0 findings"},
                {"label": "Finish", "result": "ready_to_commit"},
            ],
            "limitations": [
                "Issue 正文和任务合同已经明确期望行为。",
                "单次运行不构成跨仓库成功率统计。",
                "无法证明模型训练数据未包含上游修复。",
            ],
        },
        {
            "id": "packaging-1232",
            "kind": "恢复路径",
            "title": "packaging #1232",
            "subtitle": "宿主机关机后保留候选并恢复",
            "issue_url": "https://github.com/pypa/packaging/issues/1232",
            "source_record": SOURCE_RECORD.as_posix(),
            "source_heading": "2026-08-02 主机断电恢复 Dogfood：packaging #1232",
            "status": "ready_to_commit",
            "status_label": "恢复证据完整，可人工检查",
            "summary": (
                "Worker 执行期间宿主机关机；原候选保持不变，用户确认继续后重新建立验证和审查证据。"
            ),
            "changed_files": 3,
            "diff_summary": "+20 / -1",
            "change_summary": (
                "中断前已在 3 个允许文件留下候选；恢复前后 Diff 对象保持一致。"
            ),
            "gate_summary": "恢复后的三阶段 Scope 与 Risk Gate 全部通过。",
            "verification_summary": (
                "独立 hash/equality oracle、5311 项完整测试、Ruff 和 "
                "git diff --check 全部通过。"
            ),
            "reviewer_summary": "Reviewer 返回 approve，findings 为 0。",
            "evidence_limit": (
                "这是单次宿主机中断恢复，不证明重复崩溃、任意中断点的一致性，"
                "也不代表无中断任务的总体成功率。"
            ),
            "next_step": "人工确认恢复链路和 3 个文件的 Diff，再决定是否提交。",
            "timeline": [
                {"label": "Baseline", "result": "冻结任务、路径与初始现场"},
                {"label": "Worker", "result": "宿主机关机，候选保留"},
                {"label": "Recover", "result": "旧轮次冻结为 interrupted"},
                {"label": "Verify", "result": "5311 passed 与静态检查通过"},
                {"label": "Review", "result": "approve，0 findings"},
                {"label": "Finish", "result": "ready_to_commit"},
            ],
            "limitations": [
                "Issue 已明确期望行为，不是盲目根因发现。",
                "只验证一次宿主机关机后的恢复。",
                "不证明重复崩溃或任意中断点都可一致恢复。",
            ],
        },
        {
            "id": "crwp-v1-02",
            "kind": "停止路径",
            "title": "CRWP-V1-02",
            "subtitle": "900 秒到达后停止后续流程",
            "issue_url": "https://github.com/sequelize/sequelize/issues/18265",
            "source_record": SOURCE_RECORD.as_posix(),
            "source_heading": "2026-08-04 执行结果：CRWP-V1-02 Sequelize #18265",
            "status": "needs_human",
            "status_label": "证据不足，交还人工",
            "summary": (
                "Worker 到达冻结的 900 秒 timeout；Vega 终止受控进程，未继续验证或启动 Reviewer。"
            ),
            "changed_files": 0,
            "diff_summary": "0 个文件修改",
            "change_summary": "目标仓库保持 clean，没有文件被 Worker 修改。",
            "gate_summary": "Workspace、Scope 与 Risk Gate 均未启动。",
            "verification_summary": "Verification 未启动，没有测试结果可用于证明修复。",
            "reviewer_summary": "Reviewer 未启动，不能给出 approve 或缺陷结论。",
            "evidence_limit": (
                "这里只能证明 Vega 在 timeout 后停止并保留现场；不能解释为 Worker "
                "已经修复或无法修复目标缺陷，也不能与其他 Case 合并计算成功率。"
            ),
            "next_step": "人工查看保留现场，决定是否调整任务合同或改为人工处理。",
            "timeline": [
                {"label": "Baseline", "result": "控制基线与负向扫描通过"},
                {"label": "Worker", "result": "900 秒 timeout"},
                {"label": "Stop", "result": "受控进程已确认终止"},
                {"label": "Verify", "result": "未启动"},
                {"label": "Review", "result": "未启动"},
                {"label": "Finish", "result": "needs_human"},
            ],
            "limitations": [
                "输出读取线程关闭超时，不能宣称保存了外部进程全部输出。",
                "没有文件修改，也没有验证或 Reviewer 结果。",
                "按预注册不选择性重跑、延长 timeout 或更换结果。",
            ],
        },
    ]
    return {
        "schema_version": 1,
        "generated_from": SOURCE_RECORD.as_posix(),
        "evidence_through": "2026-08-04",
        "cases": cases,
    }


_SOURCE_CHECKS = {
    "anyio-1231": (
        "共 `23` 行新增、`1` 行删除",
        "（`24 passed, 486 deselected`）",
        "Finish 为 `ready_to_commit`",
    ),
    "packaging-1232": (
        "第 1 轮外部 Worker 运行期间宿主机关机",
        "（`5311 passed`）",
        "Finish 为\n  `ready_to_commit`",
    ),
    "crwp-v1-02": (
        "`900` 秒 timeout",
        "Worker 没有修改文件",
        "Finish 为 `needs_human`",
    ),
}


def validate_payload(payload: dict[str, Any], source_text: str) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError("展示案例 schema_version 必须为 1")
    if payload.get("generated_from") != SOURCE_RECORD.as_posix():
        raise ValueError("展示案例必须明确指向公开证据记录")

    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ValueError("展示站 V1 必须恰好包含 3 个主案例")

    seen_ids: set[str] = set()
    required_fields = {
        "id",
        "kind",
        "title",
        "subtitle",
        "issue_url",
        "source_record",
        "source_heading",
        "status",
        "status_label",
        "summary",
        "changed_files",
        "diff_summary",
        "change_summary",
        "gate_summary",
        "verification_summary",
        "reviewer_summary",
        "evidence_limit",
        "next_step",
        "timeline",
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

        if case["status"] not in _ALLOWED_STATUS:
            raise ValueError(f"未知 Finish 状态：{case['status']}")
        if case["source_record"] != SOURCE_RECORD.as_posix():
            raise ValueError(f"{case_id} 的来源不在公开证据记录中")
        if case["source_heading"] not in source_text:
            raise ValueError(f"{case_id} 的来源标题不存在")
        if not isinstance(case["limitations"], list) or not case["limitations"]:
            raise ValueError(f"{case_id} 必须披露证据限制")
        if not isinstance(case["timeline"], list) or len(case["timeline"]) < 6:
            raise ValueError(f"{case_id} 必须包含完整阶段时间线")

        for expected in _SOURCE_CHECKS[case_id]:
            if expected not in source_text:
                raise ValueError(f"{case_id} 的来源证据缺失：{expected!r}")

    stopped = next(case for case in cases if case["id"] == "crwp-v1-02")
    if stopped["changed_files"] != 0 or stopped["status"] != "needs_human":
        raise ValueError("CRWP-V1-02 必须保留 0 文件修改和 needs_human 事实")
    if "未启动" not in stopped["verification_summary"]:
        raise ValueError("CRWP-V1-02 不得伪造验证结果")
    if "未启动" not in stopped["reviewer_summary"]:
        raise ValueError("CRWP-V1-02 不得伪造 Reviewer 结果")

    serialized = json.dumps(payload, ensure_ascii=False)
    for pattern in (*_PATH_PATTERNS, *_SECRET_PATTERNS, *_UNSAFE_MODEL_CONTENT):
        match = pattern.search(serialized)
        if match:
            raise ValueError(f"公开案例包含禁止内容：{match.group(0)!r}")
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
    source_path = REPO_ROOT / SOURCE_RECORD
    output_path = args.output
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path

    source_text = source_path.read_text(encoding="utf-8")
    payload = build_payload()
    validate_payload(payload, source_text)
    expected = render_payload(payload)

    if args.check:
        if not output_path.exists():
            print(f"展示案例文件不存在：{output_path.relative_to(REPO_ROOT)}")
            return 1
        actual = output_path.read_text(encoding="utf-8")
        if actual != expected:
            print("展示案例与人工核准清单不一致，请重新运行生成命令")
            return 1
        print("展示案例数据与公开证据及人工核准清单一致")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(expected, encoding="utf-8", newline="\n")
    print(f"已生成展示案例：{output_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
