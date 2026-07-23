from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPERIMENT_SCHEMA_VERSION = 1
THREAT_ID = "T-DB-MIG-COMPAT"
LOCAL_VALIDATION_ROOT = Path(".local-validation")
DANGEROUS_MIGRATION = "ALTER TABLE customer ADD COLUMN external_id TEXT NOT NULL"
SAFE_MIGRATION = "ALTER TABLE customer ADD COLUMN external_id TEXT"
ADD_COLUMN_NOT_NULL_PATTERN = re.compile(
    r"""
    ^\s*
    ALTER\s+TABLE\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)
    \s+ADD\s+COLUMN\s+(?P<column>[A-Za-z_][A-Za-z0-9_]*)
    (?:\s+[A-Za-z_][A-Za-z0-9_]*(?:\s*\([^)]*\))?)?
    \s+NOT\s+NULL
    \s*;?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
DEFAULT_PATTERN = re.compile(r"\bDEFAULT\b", re.IGNORECASE)


def run_experiment(output_dir: Path) -> dict[str, Any]:
    """运行一个 SQLite migration 危险/安全双生实验，仅写入 `.local-validation/`。"""

    output_dir = _validate_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    dangerous = _run_dangerous_twin(output_dir / "dangerous.sqlite")
    safe = _run_safe_twin(output_dir / "safe.sqlite")
    decision = (
        "continue-experiment"
        if dangerous["decision"] == "reject" and safe["decision"] == "passed-local"
        else "inconclusive"
    )
    result = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": "AV-STAGE2-001",
        "threat_id": THREAT_ID,
        "engine": {
            "name": "sqlite",
            "version": sqlite3.sqlite_version,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_decision": decision,
        "dangerous_twin": dangerous,
        "safe_twin": safe,
        "limitations": [
            "仅覆盖 SQLite 的单表 ADD COLUMN 语法和内存级兼容行为。",
            "NewApp/OldApp 是最小读取适配器，不代表任何目标项目的真实应用代码。",
            "未覆盖 PostgreSQL/MySQL 等生产引擎的锁语义、在线索引、权限、发布编排或真实数据规模。",
            "未覆盖 DML/backfill、恢复演练、并发写入、复制延迟或生产观测。",
            "因此安全双生案例只能支持 continue-experiment，不能证明生产迁移安全或接入 Runtime。",
        ],
    }
    result_path = output_dir / "result.json"
    report_path = output_dir / "report.md"
    report_path.write_text(_render_report(result), encoding="utf-8")
    result["artifacts"] = {
        "result": "result.json",
        "report": "report.md",
        "dangerous_database": "dangerous.sqlite",
        "safe_database": "safe.sqlite",
    }
    _write_json(result_path, result)
    return result


def _run_dangerous_twin(database_path: Path) -> dict[str, Any]:
    issues = detect_unsafe_add_column(DANGEROUS_MIGRATION)
    connection = sqlite3.connect(database_path)
    try:
        _create_baseline(connection)
        before = _database_snapshot(connection)
        execution = _execute_migration(connection, DANGEROUS_MIGRATION)
        after = _database_snapshot(connection)
    finally:
        connection.close()
    rejected = bool(issues) and execution["status"] == "failed" and before == after
    return {
        "migration_sha256": _sha256_text(DANGEROUS_MIGRATION),
        "detector_issues": issues,
        "execution": execution,
        "post_failure_matches_baseline": before == after,
        "decision": "reject" if rejected else "inconclusive",
    }


def _run_safe_twin(database_path: Path) -> dict[str, Any]:
    issues = detect_unsafe_add_column(SAFE_MIGRATION)
    connection = sqlite3.connect(database_path)
    try:
        _create_baseline(connection)
        matrix = {
            "old_app_on_old_schema": _case_result(_old_app_reads(connection)),
            "new_app_on_old_schema": _case_result(_new_app_reads(connection)),
        }
        first_apply = _apply_expand_only_migration(connection)
        matrix["old_app_on_new_schema"] = _case_result(_old_app_reads(connection))
        matrix["new_app_on_new_schema"] = _case_result(_new_app_reads(connection))
        second_apply = _apply_expand_only_migration(connection)
        snapshot = _database_snapshot(connection)
    finally:
        connection.close()
    all_matrix_passed = all(case["passed"] for case in matrix.values())
    idempotent = first_apply == "applied" and second_apply == "already_present"
    nullable_column = _column_is_nullable(snapshot["columns"], "external_id")
    decision = (
        "passed-local"
        if not issues and all_matrix_passed and idempotent and nullable_column
        else "inconclusive"
    )
    return {
        "migration_sha256": _sha256_text(SAFE_MIGRATION),
        "detector_issues": issues,
        "matrix": matrix,
        "first_apply": first_apply,
        "second_apply": second_apply,
        "idempotent_wrapper": idempotent,
        "nullable_column": nullable_column,
        "data_invariant": {
            "customer_ids": [row["id"] for row in snapshot["rows"]],
            "display_names": [row["display_name"] for row in snapshot["rows"]],
        },
        "decision": decision,
    }


def detect_unsafe_add_column(sql: str) -> list[str]:
    """识别本实验明确注册的 SQLite 兼容性危险语法，未知语法不作安全声明。"""

    normalized = " ".join(sql.split())
    if ADD_COLUMN_NOT_NULL_PATTERN.match(normalized) and not DEFAULT_PATTERN.search(normalized):
        return [
            f"{THREAT_ID}:add_column_not_null_without_default:"
            "existing_rows_would_reject_migration"
        ]
    return []


def _create_baseline(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE customer (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO customer (id, display_name) VALUES (?, ?)",
        [(1, "Ada"), (2, "Lin")],
    )
    connection.commit()


def _execute_migration(connection: sqlite3.Connection, sql: str) -> dict[str, Any]:
    try:
        connection.execute("BEGIN")
        connection.execute(sql)
        connection.commit()
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    return {"status": "passed"}


def _apply_expand_only_migration(connection: sqlite3.Connection) -> str:
    if _column_exists(connection, "customer", "external_id"):
        return "already_present"
    connection.execute(SAFE_MIGRATION)
    connection.commit()
    return "applied"


def _old_app_reads(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, display_name FROM customer ORDER BY id"
    ).fetchall()
    return [{"id": row[0], "display_name": row[1]} for row in rows]


def _new_app_reads(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _column_exists(connection, "customer", "external_id"):
        rows = _old_app_reads(connection)
        return [{**row, "external_id": None, "schema_mode": "old_fallback"} for row in rows]
    rows = connection.execute(
        "SELECT id, display_name, external_id FROM customer ORDER BY id"
    ).fetchall()
    return [
        {
            "id": row[0],
            "display_name": row[1],
            "external_id": row[2],
            "schema_mode": "expanded",
        }
        for row in rows
    ]


def _case_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected_ids = [1, 2]
    return {
        "passed": [row["id"] for row in rows] == expected_ids,
        "row_count": len(rows),
        "rows": rows,
    }


def _database_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    columns = connection.execute("PRAGMA table_info(customer)").fetchall()
    rows = _old_app_reads(connection)
    return {
        "columns": [
            {
                "name": column[1],
                "type": column[2],
                "not_null": bool(column[3]),
                "default": column[4],
            }
            for column in columns
        ],
        "rows": rows,
    }


def _column_exists(connection: sqlite3.Connection, table: str, column_name: str) -> bool:
    return any(column[1] == column_name for column in connection.execute(f"PRAGMA table_info({table})"))


def _column_is_nullable(columns: list[dict[str, Any]], column_name: str) -> bool:
    return any(column["name"] == column_name and not column["not_null"] for column in columns)


def _validate_output_dir(output_dir: Path) -> Path:
    root = (Path.cwd() / LOCAL_VALIDATION_ROOT).resolve()
    candidate = output_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("实验输出目录必须位于当前工作目录的 .local-validation/ 下。") from exc
    return candidate


def _render_report(result: dict[str, Any]) -> str:
    dangerous = result["dangerous_twin"]
    safe = result["safe_twin"]
    matrix = safe["matrix"]
    return "\n".join(
        [
            "# Assurance Stage 2 SQLite migration 实验",
            "",
            f"- 实验：`{result['experiment_id']}`",
            f"- Threat：`{result['threat_id']}`",
            f"- SQLite：`{result['engine']['version']}`",
            f"- 总体结论：`{result['overall_decision']}`",
            "",
            "## 危险双生",
            "",
            f"- detector：`{', '.join(dangerous['detector_issues']) or 'none'}`",
            f"- 实际执行：`{dangerous['execution']['status']}`",
            f"- 失败后 schema/data 与基线一致：`{dangerous['post_failure_matches_baseline']}`",
            f"- 判定：`{dangerous['decision']}`",
            "",
            "## 安全双生",
            "",
            f"- detector：`{', '.join(safe['detector_issues']) or 'none'}`",
            f"- OldApp/OldSchema：`{matrix['old_app_on_old_schema']['passed']}`",
            f"- NewApp/OldSchema：`{matrix['new_app_on_old_schema']['passed']}`",
            f"- OldApp/NewSchema：`{matrix['old_app_on_new_schema']['passed']}`",
            f"- NewApp/NewSchema：`{matrix['new_app_on_new_schema']['passed']}`",
            f"- 受控重跑：`{safe['idempotent_wrapper']}`",
            f"- 新列可空：`{safe['nullable_column']}`",
            f"- 判定：`{safe['decision']}`",
            "",
            "## 限制",
            "",
            *[f"- {item}" for item in result["limitations"]],
            "",
        ]
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="运行独立的 Assurance Stage 2 SQLite migration 危险/安全双生实验。"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_default_output_dir(),
        help="必须位于当前工作目录 .local-validation/ 下的空输出目录。",
    )
    args = parser.parse_args()
    try:
        result = run_experiment(args.output_dir)
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(f"实验无法完成：{exc}")
        return 2
    print(
        "Assurance Stage 2 SQLite 实验完成："
        f"decision={result['overall_decision']}，artifact={args.output_dir / 'result.json'}"
    )
    return 0 if result["overall_decision"] == "continue-experiment" else 1


def _default_output_dir() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return LOCAL_VALIDATION_ROOT / f"assurance-stage2-sqlite-{timestamp}"


if __name__ == "__main__":
    raise SystemExit(main())
