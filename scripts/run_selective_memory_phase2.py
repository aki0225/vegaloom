from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from eval.selective_memory.evaluator import (  # noqa: E402
    evaluate_cases,
    load_cases,
    load_golden,
)
from eval.selective_memory.generate_phase2_dataset import (  # noqa: E402
    generate_dataset,
)


TRACKED_EXPERIMENT_ROOT = PROJECT_ROOT / "eval" / "selective_memory"
PUBLISHED_DOC_ROOT = PROJECT_ROOT / "docs" / "experiments" / "selective-memory"
DEFAULT_WORKSPACE = PROJECT_ROOT / ".tmp" / "selective-memory" / "reproduction"


def _resolve_workspace(value: Path) -> Path:
    candidate = value if value.is_absolute() else PROJECT_ROOT / value
    resolved = candidate.resolve()
    temp_root = (PROJECT_ROOT / ".tmp").resolve()
    if not resolved.is_relative_to(temp_root):
        raise ValueError("复现工作区必须位于当前仓库的 .tmp/ 下")
    return resolved


def _json_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes().replace(b"\r\n", b"\n")
        for path in sorted(root.rglob("*.json"))
    }


def _assert_dataset_matches(generated_root: Path) -> None:
    for directory in ("cases", "golden"):
        expected = _json_files(TRACKED_EXPERIMENT_ROOT / directory)
        actual = _json_files(generated_root / directory)
        if actual != expected:
            missing = sorted(set(expected) - set(actual))
            unexpected = sorted(set(actual) - set(expected))
            changed = sorted(
                path
                for path in set(actual) & set(expected)
                if actual[path] != expected[path]
            )
            raise ValueError(
                f"{directory} 数据集与跟踪快照不一致："
                f"missing={missing}, unexpected={unexpected}, changed={changed}"
            )


def _stable_metrics(value: dict[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(value)
    for mode in ("A", "B", "C", "D"):
        stable["mode_metrics"][mode].pop("selector_policy_ns", None)
    return stable


def _assert_phase2_contract(metrics: dict[str, Any]) -> None:
    expected = {
        "case_count": 10,
        "checkpoint_count": 150,
        "labeled_checkpoint_count": 150,
        "mode_evaluation_count": 600,
        "llm_request_count": 0,
        "real_task_success_claimed": False,
        "decision": "candidate-for-shadow",
    }
    actual = {key: metrics[key] for key in expected}
    if actual != expected:
        raise ValueError(f"Phase 2 固定合同不一致：expected={expected}, actual={actual}")
    if not all(metrics["phase2_gates"].values()):
        failed = [
            name
            for name, passed in metrics["phase2_gates"].items()
            if not passed
        ]
        raise ValueError(f"Phase 2 门禁失败：{failed}")


def _assert_published_outputs(result_root: Path, metrics: dict[str, Any]) -> None:
    published_metrics = json.loads(
        (PUBLISHED_DOC_ROOT / "metrics.json").read_text(encoding="utf-8")
    )
    if _stable_metrics(published_metrics) != _stable_metrics(metrics):
        raise ValueError("公开 metrics.json 与当前 evaluator 的稳定指标不一致")

    for name in ("EVAL-REPORT.md", "PHASE-2-DECISION.md"):
        generated = (result_root / name).read_text(encoding="utf-8")
        published = (PUBLISHED_DOC_ROOT / name).read_text(encoding="utf-8")
        if generated != published:
            raise ValueError(f"公开 {name} 与当前 evaluator 输出不一致")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在仓库 .tmp/ 中重生成并核对 Selective Memory Phase 2",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help="复现工作区，必须位于当前仓库 .tmp/ 下",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        workspace = _resolve_workspace(args.workspace)
        dataset_root = workspace / "dataset"
        result_root = workspace / "results"
        checkpoint_count, label_count = generate_dataset(dataset_root)
        if (checkpoint_count, label_count) != (150, 150):
            raise ValueError(
                "数据集规模不一致："
                f"checkpoints={checkpoint_count}, labels={label_count}"
            )
        _assert_dataset_matches(dataset_root)

        metrics = evaluate_cases(
            load_cases(dataset_root / "cases"),
            load_golden(dataset_root / "golden"),
            result_root,
        )
        _assert_phase2_contract(metrics)
        _assert_published_outputs(result_root, metrics)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Selective Memory Phase 2 复现失败：{exc}", file=sys.stderr)
        return 1

    print(
        "Selective Memory Phase 2 复现通过："
        "cases=10, checkpoints=150, evaluations=600, llm_calls=0, "
        "decision=candidate-for-shadow"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
