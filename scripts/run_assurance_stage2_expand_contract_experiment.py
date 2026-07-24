from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


EXPERIMENT_SCHEMA_VERSION = 2
EXPERIMENT_ID = "AV-STAGE2-002"
THREAT_ID = "T-DB-MIG-COMPAT"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_VALIDATION_ROOT = Path(".local-validation")
BASELINE_CUSTOMERS = ((1, "Ada"), (2, "Lin"))
EXPAND_SQL = "ALTER TABLE customer ADD COLUMN external_id TEXT"
CONTRACT_TABLE = "customer__contract_tmp"
EXPECTED_ROWS = [
    {"id": 1, "display_name": "Ada", "external_id": "cust-0001"},
    {"id": 2, "display_name": "Lin", "external_id": "cust-0002"},
]
# SQLite 会把 INTEGER PRIMARY KEY 的 notnull 报为 0，因此必须结合 primary_key 判断原约束。
EXPECTED_CONTRACT_COLUMNS = [
    {
        "name": "id",
        "type": "INTEGER",
        "not_null": False,
        "default": None,
        "primary_key": True,
    },
    {
        "name": "display_name",
        "type": "TEXT",
        "not_null": True,
        "default": None,
        "primary_key": False,
    },
    {
        "name": "external_id",
        "type": "TEXT",
        "not_null": True,
        "default": None,
        "primary_key": False,
    },
]


def run_experiment(output_dir: Path) -> dict[str, Any]:
    """运行独立的 SQLite expand/backfill/contract 双生实验。"""

    output_dir = _validate_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    output_dir = _validate_output_dir(output_dir)
    dangerous = _run_dangerous_twin(output_dir / "dangerous.sqlite")
    safe = _run_safe_twin(output_dir / "safe.sqlite")
    candidate_decision = (
        "continue-experiment"
        if (
            dangerous["decision"] == "reject"
            and safe["decision"] == "candidate-passed-local"
        )
        else "not-established"
    )
    result = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "threat_id": THREAT_ID,
        "engine": {
            "name": "sqlite",
            "version": sqlite3.sqlite_version,
        },
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "overall_decision": "inconclusive",
        "candidate_decision": candidate_decision,
        "evidence_adequacy": "insufficient",
        "runtime_integration": "disabled",
        "decision_scope": "experiment-execution-only",
        "external_quality_gates": {
            "status": "not_evaluated",
            "required": [
                "negative_controls",
                "full_test_suite",
                "static_checks",
                "cross_platform_ci",
            ],
        },
        "dangerous_twin": dangerous,
        "safe_twin": safe,
        "limitations": [
            "只覆盖 SQLite、固定两行 fixture 和单表表重建。",
            "bounded fixture data preparation 不是通用 backfill runner 或 Stage 3 数据修改能力。",
            "未覆盖 PostgreSQL/MySQL、在线 DDL、锁时间、权限、真实滚动发布或生产数据规模。",
            "未覆盖并发写入、分批 checkpoint、失败恢复、复制延迟或生产观测。",
            "本 artifact 只裁决双生实验执行；完整测试、静态检查与跨平台 CI 必须在 eval 中单独关闭。",
            "只有外部门禁全部关闭后，候选才可提升为 continue-experiment / requires_staged_rollout；"
            "当前 artifact 保持 inconclusive / insufficient。",
        ],
        "artifacts": {
            "result": "result.json",
            "report": "report.md",
            "dangerous_database": "dangerous.sqlite",
            "safe_database": "safe.sqlite",
        },
    }
    _write_json(output_dir / "result.json", result)
    (output_dir / "report.md").write_text(_render_report(result), encoding="utf-8")
    return result


def _run_dangerous_twin(database_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database_path)
    try:
        _create_baseline(connection)
        expand = _apply_expand(connection)
        detector_issues = _contract_precondition_issues(connection)
        before_contract = _database_snapshot(connection)
        execution = _apply_contract(connection)
        after_contract = _database_snapshot(connection)
    finally:
        connection.close()

    oracle = _independent_oracle(database_path)
    expected_issue = (
        f"{THREAT_ID}:contract_before_backfill:"
        "external_id_contains_null_rows"
    )
    rejected = (
        expand["status"] == "applied"
        and detector_issues == [expected_issue]
        and execution["status"] == "failed"
        and before_contract == after_contract
        and oracle["schema_mode"] == "expanded_nullable"
        and oracle["external_ids"] == [None, None]
        and not oracle["temp_table_present"]
    )
    return {
        "phase_order": ["expand", "contract", "backfill"],
        "expand": expand,
        "detector_issues": detector_issues,
        "execution": execution,
        "post_failure_matches_pre_contract_snapshot": (
            before_contract == after_contract
        ),
        "backfill_executed": False,
        "oracle": oracle,
        "decision": "reject" if rejected else "inconclusive",
    }


def _run_safe_twin(database_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database_path)
    try:
        _create_baseline(connection)
        matrix = {
            "old_app_on_old_schema": _case_result(
                _old_app_reads(connection),
                _expected_old_app_rows(),
            ),
            "new_app_on_old_schema": _case_result(
                _new_app_reads(connection),
                _expected_new_app_rows("old_fallback"),
            ),
        }
        expand = _apply_expand(connection)
        matrix["old_app_on_expanded_schema"] = _case_result(
            _old_app_reads(connection),
            _expected_old_app_rows(),
        )
        matrix["new_app_on_expanded_schema"] = _case_result(
            _new_app_reads(connection),
            _expected_new_app_rows("expanded_nullable"),
        )
        first_backfill = _backfill_external_ids(connection)
        second_backfill = _backfill_external_ids(connection)
        matrix["old_app_on_backfilled_schema"] = _case_result(
            _old_app_reads(connection),
            _expected_old_app_rows(),
        )
        matrix["new_app_on_backfilled_schema"] = _case_result(
            _new_app_reads(connection),
            _expected_new_app_rows("backfilled_nullable"),
        )
        detector_issues = _contract_precondition_issues(connection)
        if detector_issues:
            first_contract = _skipped_contract("contract_precondition_failed")
            second_contract = _skipped_contract("contract_precondition_failed")
            matrix["old_app_on_contract_schema"] = _skipped_case_result(
                "contract_precondition_failed"
            )
            matrix["new_app_on_contract_schema"] = _skipped_case_result(
                "contract_precondition_failed"
            )
        else:
            first_contract = _apply_contract(connection)
            if first_contract["status"] in {"applied", "already_contracted"}:
                matrix["old_app_on_contract_schema"] = _case_result(
                    _old_app_reads(connection),
                    _expected_old_app_rows(),
                )
                matrix["new_app_on_contract_schema"] = _case_result(
                    _new_app_reads(connection),
                    _expected_new_app_rows("contracted_not_null"),
                )
                second_contract = _apply_contract(connection)
            else:
                matrix["old_app_on_contract_schema"] = _skipped_case_result(
                    "contract_not_applied"
                )
                matrix["new_app_on_contract_schema"] = _skipped_case_result(
                    "contract_not_applied"
                )
                second_contract = _skipped_contract("contract_not_applied")
    finally:
        connection.close()

    oracle = _independent_oracle(database_path)
    backfill_idempotent = (
        first_backfill["status"] == "applied"
        and first_backfill["updated_rows"] == len(BASELINE_CUSTOMERS)
        and second_backfill["status"] == "already_backfilled"
        and second_backfill["updated_rows"] == 0
    )
    contract_idempotent = (
        first_contract["status"] == "applied"
        and second_contract["status"] == "already_contracted"
    )
    all_matrix_passed = all(case["passed"] for case in matrix.values())
    decision = (
        "candidate-passed-local"
        if (
            expand["status"] == "applied"
            and not detector_issues
            and backfill_idempotent
            and contract_idempotent
            and all_matrix_passed
            and oracle["passed"]
        )
        else "inconclusive"
    )
    return {
        "phase_order": ["expand", "backfill", "contract"],
        "expand": expand,
        "backfill": {
            "first_run": first_backfill,
            "second_run": second_backfill,
            "idempotent": backfill_idempotent,
        },
        "detector_issues": detector_issues,
        "contract": {
            "first_run": first_contract,
            "second_run": second_contract,
            "idempotent": contract_idempotent,
        },
        "matrix": matrix,
        "oracle": oracle,
        "decision": decision,
    }


def _create_baseline(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE customer (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO customer (id, display_name) VALUES (?, ?)",
        BASELINE_CUSTOMERS,
    )
    connection.commit()


def _apply_expand(connection: sqlite3.Connection) -> dict[str, Any]:
    if _column_exists(connection, "customer", "external_id"):
        return {"status": "already_expanded"}
    connection.execute(EXPAND_SQL)
    connection.commit()
    return {"status": "applied"}


def _backfill_external_ids(connection: sqlite3.Connection) -> dict[str, Any]:
    updated_rows = 0
    for expected in EXPECTED_ROWS:
        cursor = connection.execute(
            "UPDATE customer SET external_id = ? "
            "WHERE id = ? AND external_id IS NULL",
            (expected["external_id"], expected["id"]),
        )
        updated_rows += max(cursor.rowcount, 0)
    connection.commit()
    return {
        "status": "applied" if updated_rows else "already_backfilled",
        "updated_rows": updated_rows,
    }


def _contract_precondition_issues(connection: sqlite3.Connection) -> list[str]:
    if not _column_exists(connection, "customer", "external_id"):
        return [f"{THREAT_ID}:contract_before_expand:external_id_missing"]

    rows = _stored_rows(connection)
    if any(row["external_id"] is None for row in rows):
        return [
            f"{THREAT_ID}:contract_before_backfill:"
            "external_id_contains_null_rows"
        ]
    if len({row["external_id"] for row in rows}) != len(rows):
        return [
            f"{THREAT_ID}:contract_after_wrong_backfill:"
            "external_id_not_unique"
        ]
    if rows != EXPECTED_ROWS:
        return [
            f"{THREAT_ID}:contract_after_wrong_backfill:"
            "external_id_mapping_mismatch"
        ]
    return []


def _apply_contract(connection: sqlite3.Connection) -> dict[str, Any]:
    if _table_exists(connection, CONTRACT_TABLE):
        return {
            "status": "failed",
            "error_type": "ContractTempTableExists",
            "error_message": f"{CONTRACT_TABLE} 已存在，拒绝继续 contract。",
        }
    if _contract_schema_is_complete(connection):
        return {"status": "already_contracted"}

    try:
        connection.execute("BEGIN IMMEDIATE")
        _create_contract_table(connection)
        connection.execute(
            f"INSERT INTO {CONTRACT_TABLE} (id, display_name, external_id) "
            "SELECT id, display_name, external_id FROM customer ORDER BY id"
        )
        connection.execute("DROP TABLE customer")
        connection.execute(f"ALTER TABLE {CONTRACT_TABLE} RENAME TO customer")
        connection.commit()
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    return {"status": "applied"}


def _create_contract_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"CREATE TABLE {CONTRACT_TABLE} ("
        "id INTEGER PRIMARY KEY, "
        "display_name TEXT NOT NULL, "
        "external_id TEXT NOT NULL UNIQUE)"
    )


def _old_app_reads(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, display_name FROM customer ORDER BY id"
    ).fetchall()
    return [{"id": row[0], "display_name": row[1]} for row in rows]


def _new_app_reads(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _column_exists(connection, "customer", "external_id"):
        return [
            {**row, "external_id": None, "schema_mode": "old_fallback"}
            for row in _old_app_reads(connection)
        ]
    schema_mode = _schema_mode(connection)
    return [
        {**row, "schema_mode": schema_mode}
        for row in _stored_rows(connection)
    ]


def _expected_old_app_rows() -> list[dict[str, Any]]:
    return [
        {"id": customer_id, "display_name": display_name}
        for customer_id, display_name in BASELINE_CUSTOMERS
    ]


def _expected_new_app_rows(schema_mode: str) -> list[dict[str, Any]]:
    if schema_mode in {"old_fallback", "expanded_nullable"}:
        external_ids: list[str | None] = [None, None]
    else:
        external_ids = ["cust-0001", "cust-0002"]
    return [
        {
            "id": customer_id,
            "display_name": display_name,
            "external_id": external_id,
            "schema_mode": schema_mode,
        }
        for (customer_id, display_name), external_id in zip(
            BASELINE_CUSTOMERS,
            external_ids,
            strict=True,
        )
    ]


def _case_result(
    rows: list[dict[str, Any]],
    expected_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "passed": rows == expected_rows,
        "row_count": len(rows),
        "rows": rows,
    }


def _skipped_case_result(reason: str) -> dict[str, Any]:
    return {
        "passed": False,
        "status": "skipped",
        "reason": reason,
        "row_count": 0,
        "rows": [],
    }


def _skipped_contract(reason: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
    }


def _stored_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, display_name, external_id FROM customer ORDER BY id"
    ).fetchall()
    return [
        {
            "id": row[0],
            "display_name": row[1],
            "external_id": row[2],
        }
        for row in rows
    ]


def _database_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        "table_sql": _customer_table_sql(connection),
        "columns": _table_columns(connection),
        "rows": _stored_rows(connection),
        "tables": _table_names(connection),
    }


def _independent_oracle(database_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database_path)
    try:
        # Oracle 有意不复用 NewApp、detector 或 contract 的读取 helper，避免同根缺陷自证通过。
        columns = [
            {
                "name": column[1],
                "type": column[2],
                "not_null": bool(column[3]),
                "default": column[4],
                "primary_key": bool(column[5]),
            }
            for column in connection.execute("PRAGMA table_info(customer)")
        ]
        column_map = {column["name"]: column for column in columns}
        rows = [
            {
                "id": row[0],
                "display_name": row[1],
                "external_id": row[2],
            }
            for row in connection.execute(
                "SELECT id, display_name, external_id FROM customer ORDER BY id"
            )
        ]
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            )
        ]
        table_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'customer'"
        ).fetchone()
        table_sql = str(table_sql_row[0]) if table_sql_row else ""
        external_ids = [row["external_id"] for row in rows]
        null_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM customer WHERE external_id IS NULL"
            ).fetchone()[0]
        )
        distinct_count, total_count = connection.execute(
            "SELECT COUNT(DISTINCT external_id), COUNT(*) FROM customer"
        ).fetchone()
        distinct_count = int(distinct_count)
        total_count = int(total_count)
        not_null_columns = {
            name: bool(column["not_null"])
            for name, column in column_map.items()
        }
        column_types = {
            name: str(column["type"]).strip().upper()
            for name, column in column_map.items()
        }
        unique_external_id = False
        for index in connection.execute("PRAGMA index_list(customer)"):
            is_unique = bool(index[2])
            is_partial = bool(index[4]) if len(index) > 4 else False
            if not is_unique or is_partial:
                continue
            index_columns = [
                str(column[0])
                for column in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (str(index[1]),),
                )
            ]
            if index_columns == ["external_id"]:
                unique_external_id = True
                break
        external_column = column_map.get("external_id")
        if external_column is None:
            schema_mode = "old"
        elif external_column["not_null"]:
            schema_mode = "contracted_not_null"
        else:
            schema_mode = (
                "backfilled_nullable" if null_count == 0 else "expanded_nullable"
            )
        temp_table_present = CONTRACT_TABLE in tables
        stored_rows_passed = rows == EXPECTED_ROWS
        schema_columns_passed = columns == EXPECTED_CONTRACT_COLUMNS
        passed = (
            schema_mode == "contracted_not_null"
            and schema_columns_passed
            and unique_external_id
            and stored_rows_passed
            and null_count == 0
            and distinct_count == total_count == len(EXPECTED_ROWS)
            and not temp_table_present
        )
        return {
            "passed": passed,
            "schema_mode": schema_mode,
            "table_sql": table_sql,
            "columns": columns,
            "schema_columns_passed": schema_columns_passed,
            "not_null_columns": not_null_columns,
            "column_types": column_types,
            "unique_external_id": unique_external_id,
            "rows": rows,
            "stored_rows_passed": stored_rows_passed,
            "external_ids": external_ids,
            "null_external_id_count": null_count,
            "distinct_external_id_count": distinct_count,
            "total_row_count": total_count,
            "tables": tables,
            "temp_table_present": temp_table_present,
        }
    finally:
        connection.close()


def _schema_mode(connection: sqlite3.Connection) -> str:
    columns = _table_columns(connection)
    external = next(
        (column for column in columns if column["name"] == "external_id"),
        None,
    )
    if external is None:
        return "old"
    if external["not_null"]:
        return "contracted_not_null"
    null_count = connection.execute(
        "SELECT COUNT(*) FROM customer WHERE external_id IS NULL"
    ).fetchone()[0]
    return "backfilled_nullable" if null_count == 0 else "expanded_nullable"


def _customer_table_sql(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'customer'"
    ).fetchone()
    return str(row[0]) if row else ""


def _table_columns(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "name": column[1],
            "type": column[2],
            "not_null": bool(column[3]),
            "default": column[4],
            "primary_key": bool(column[5]),
        }
        for column in connection.execute("PRAGMA table_info(customer)")
    ]


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' ORDER BY name"
        )
    ]


def _external_id_is_unique(connection: sqlite3.Connection) -> bool:
    for index in connection.execute("PRAGMA index_list(customer)"):
        is_unique = bool(index[2])
        is_partial = bool(index[4]) if len(index) > 4 else False
        if not is_unique or is_partial:
            continue
        columns = [
            str(column[2])
            for column in connection.execute(f"PRAGMA index_info({index[1]})")
        ]
        if columns == ["external_id"]:
            return True
    return False


def _contract_schema_is_complete(connection: sqlite3.Connection) -> bool:
    columns = _table_columns(connection)
    return (
        columns == EXPECTED_CONTRACT_COLUMNS
        and _external_id_is_unique(connection)
    )


def _column_exists(
    connection: sqlite3.Connection,
    table: str,
    column_name: str,
) -> bool:
    return any(
        column[1] == column_name
        for column in connection.execute(f"PRAGMA table_info({table})")
    )


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _validate_output_dir(output_dir: Path) -> Path:
    workspace = PROJECT_ROOT.resolve(strict=True)
    root = workspace / LOCAL_VALIDATION_ROOT
    candidate = Path(
        os.path.abspath(
            output_dir if output_dir.is_absolute() else workspace / output_dir
        )
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "实验输出目录必须位于 Vega 仓库根目录的 .local-validation/ 下。"
        ) from exc
    _reject_link_or_reparse_components(workspace, candidate)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(
            "实验输出目录必须位于 Vega 仓库根目录的 .local-validation/ 下。"
        ) from exc
    return candidate


def _reject_link_or_reparse_components(
    workspace: Path,
    candidate: Path,
) -> None:
    current = workspace
    for part in candidate.relative_to(workspace).parts:
        current /= part
        if os.path.lexists(current) and _is_link_or_reparse_point(current):
            raise ValueError(
                "实验输出目录必须位于 Vega 仓库根目录的 .local-validation/ 下，"
                "且路径组件不能是符号链接、junction 或 reparse point。"
            )


def _is_link_or_reparse_point(path: Path) -> bool:
    stat_result = path.lstat()
    file_attributes = int(getattr(stat_result, "st_file_attributes", 0))
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(stat_result.st_mode) or bool(file_attributes & reparse_flag)


def _render_report(result: dict[str, Any]) -> str:
    dangerous = result["dangerous_twin"]
    safe = result["safe_twin"]
    return "\n".join(
        [
            "# Assurance Stage 2 expand/backfill/contract 实验",
            "",
            f"- 实验：`{result['experiment_id']}`",
            f"- Threat：`{result['threat_id']}`",
            f"- SQLite：`{result['engine']['version']}`",
            f"- 总体结论：`{result['overall_decision']}`",
            f"- 候选结论：`{result['candidate_decision']}`",
            f"- 证据充分性：`{result['evidence_adequacy']}`",
            f"- Runtime 集成：`{result['runtime_integration']}`",
            f"- 决策范围：`{result['decision_scope']}`",
            "- 外部质量门禁："
            f"`{result['external_quality_gates']['status']}`",
            "",
            "## 危险双生",
            "",
            f"- 顺序：`{' -> '.join(dangerous['phase_order'])}`",
            f"- detector：`{', '.join(dangerous['detector_issues']) or 'none'}`",
            f"- 实际 contract：`{dangerous['execution']['status']}`",
            "- rollback 后事实保持不变："
            f"`{dangerous['post_failure_matches_pre_contract_snapshot']}`",
            f"- 临时表残留：`{dangerous['oracle']['temp_table_present']}`",
            f"- 判定：`{dangerous['decision']}`",
            "",
            "## 安全双生",
            "",
            f"- 顺序：`{' -> '.join(safe['phase_order'])}`",
            f"- 首次数据准备更新行数：`{safe['backfill']['first_run']['updated_rows']}`",
            f"- 第二次数据准备更新行数：`{safe['backfill']['second_run']['updated_rows']}`",
            f"- 数据准备幂等：`{safe['backfill']['idempotent']}`",
            f"- contract 幂等：`{safe['contract']['idempotent']}`",
            f"- 兼容矩阵全部通过：`{all(case['passed'] for case in safe['matrix'].values())}`",
            f"- 独立 SQL oracle：`{safe['oracle']['passed']}`",
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


def _default_output_dir() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return LOCAL_VALIDATION_ROOT / f"assurance-stage2-expand-contract-{timestamp}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "运行独立的 Assurance Stage 2 SQLite "
            "expand/backfill/contract 危险/安全双生实验。"
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_default_output_dir(),
        help="必须位于 Vega 仓库根目录 .local-validation/ 下的空输出目录。",
    )
    args = parser.parse_args()
    try:
        validated_output_dir = _validate_output_dir(args.output_dir)
        result = run_experiment(validated_output_dir)
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(f"实验无法完成：{_safe_exception_message(exc)}")
        return 2
    artifact_path = (
        validated_output_dir.relative_to(PROJECT_ROOT.resolve(strict=True))
        / "result.json"
    )
    print(
        "Assurance Stage 2 expand/backfill/contract 实验执行完成："
        f"decision={result['overall_decision']}，"
        f"candidate={result['candidate_decision']}，"
        f"artifact={artifact_path.as_posix()}"
    )
    return _result_exit_code(result)


def _safe_exception_message(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    return f"{type(exc).__name__}，详细本地路径已省略。"


def _result_exit_code(result: dict[str, Any]) -> int:
    external_status = result.get("external_quality_gates", {}).get("status")
    return (
        0
        if result.get("overall_decision") == "continue-experiment"
        and external_status == "passed"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
