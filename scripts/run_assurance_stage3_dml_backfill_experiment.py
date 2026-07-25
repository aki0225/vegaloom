from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


EXPERIMENT_SCHEMA_VERSION = 1
EXPERIMENT_ID = "AV-STAGE3-001"
THREAT_ID = "T-DB-DML-SCOPE"
SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SOURCE_PROJECT_ROOT
LOCAL_VALIDATION_ROOT = Path(".local-validation")
POLICY_RELATIVE_PATH = "docs/ASSURANCE-STAGE3-DML-BACKFILL-PREREGISTRATION.md"
PRODUCER = "scripts/run_assurance_stage3_dml_backfill_experiment.py"
HEAD_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
INJECTED_PROCESS_EXIT_CODE = 97
INTERNAL_WORKER_SCRIPT_ENV = "VEGA_STAGE3_INTERNAL_WORKER_SCRIPT"
INTERNAL_WORKER_DATABASE_ENV = "VEGA_STAGE3_INTERNAL_WORKER_DATABASE"
# 子进程从已跟踪脚本重新导入实现，并在首批提交后硬退出，避免同进程异常伪装崩溃恢复。
INTERNAL_WORKER_BOOTSTRAP = """
import importlib.util
import os
import sys
from pathlib import Path

script = Path(os.environ["VEGA_STAGE3_INTERNAL_WORKER_SCRIPT"])
spec = importlib.util.spec_from_file_location(
    "vega_stage3_internal_worker",
    script,
)
if spec is None or spec.loader is None:
    raise SystemExit(98)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module._run_injected_first_batch_worker(
    Path(os.environ["VEGA_STAGE3_INTERNAL_WORKER_DATABASE"])
)
"""

BASELINE_ROWS = [
    {
        "id": 101,
        "tenant_id": "tenant-a",
        "legacy_handle": "ada",
        "canonical_handle": None,
        "backfill_version": 0,
    },
    {
        "id": 102,
        "tenant_id": "tenant-a",
        "legacy_handle": "lin",
        "canonical_handle": None,
        "backfill_version": 0,
    },
    {
        "id": 201,
        "tenant_id": "tenant-b",
        "legacy_handle": "sentinel",
        "canonical_handle": None,
        "backfill_version": 0,
    },
    {
        "id": 202,
        "tenant_id": "tenant-b",
        "legacy_handle": "kept",
        "canonical_handle": "keep-b",
        "backfill_version": 1,
    },
]
EXPECTED_FINAL_ROWS = [
    {
        **row,
        "canonical_handle": (
            f"cust-a-{row['id']:04d}"
            if row["id"] in {101, 102}
            else row["canonical_handle"]
        ),
        "backfill_version": 1 if row["id"] in {101, 102} else row["backfill_version"],
    }
    for row in BASELINE_ROWS
]
EXPECTED_OUT_OF_SCOPE_ROWS = [
    copy.deepcopy(row) for row in BASELINE_ROWS if row["tenant_id"] != "tenant-a"
]
FROZEN_PLAN = {
    "tenant_id": "tenant-a",
    "target_ids": [101, 102],
    "execution_order": [101, 102],
    "row_budget": 2,
    "batch_size": 1,
    "mapping": {
        "101": "cust-a-0101",
        "102": "cust-a-0102",
    },
}
DANGEROUS_UPDATE_SQL = (
    "UPDATE customer "
    "SET canonical_handle = 'cust-' || printf('%04d', id), "
    "backfill_version = 1 "
    "WHERE canonical_handle IS NULL"
)
SAFE_UPDATE_SQL = (
    "UPDATE customer "
    "SET canonical_handle = ?, backfill_version = 1 "
    "WHERE tenant_id = ? AND id = ? "
    "AND canonical_handle IS NULL AND backfill_version = 0"
)
SAFE_UPDATE_WITHOUT_TENANT_SQL = (
    "UPDATE customer "
    "SET canonical_handle = ?, backfill_version = 1 "
    "WHERE id = ? "
    "AND canonical_handle IS NULL AND backfill_version = 0"
)


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _plan_digest(plan: dict[str, Any]) -> str:
    canonical_plan = {
        "tenant_id": plan.get("tenant_id"),
        "target_ids": list(plan.get("target_ids", [])),
        "execution_order": list(plan.get("execution_order", [])),
        "row_budget": plan.get("row_budget"),
        "batch_size": plan.get("batch_size"),
        "mapping": {
            str(key): value
            for key, value in sorted(
                dict(plan.get("mapping", {})).items(),
                key=lambda item: str(item[0]),
            )
        },
    }
    return _sha256_payload(canonical_plan)


FIXTURE_SHA256 = _sha256_payload(BASELINE_ROWS)
FROZEN_PLAN_DIGEST = _plan_digest(FROZEN_PLAN)
_POLICY_PATH = SOURCE_PROJECT_ROOT / POLICY_RELATIVE_PATH


def _policy_sha256() -> str | None:
    try:
        if not _POLICY_PATH.is_file():
            return None
        return hashlib.sha256(_POLICY_PATH.read_bytes()).hexdigest()
    except OSError:
        return None


POLICY_SHA256 = _policy_sha256()


def run_experiment(output_dir: Path) -> dict[str, Any]:
    """运行固定 SQLite DML/backfill 危险与安全双生实验。"""

    output_dir = _validate_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    output_dir = _validate_output_dir(output_dir)
    snapshot = _repository_snapshot()

    dangerous = _run_dangerous_twin(output_dir / "dangerous.sqlite")
    _write_json(output_dir / "dangerous-oracle.json", dangerous["oracle"])

    safe = _run_safe_twin(output_dir)
    _write_json(output_dir / "safe-oracle.json", safe["oracle"])
    _write_json(output_dir / "reconciliation.json", safe["reconciliation"])

    evidence = _build_evidence(output_dir, snapshot, dangerous, safe)
    evidence_bindings_valid = (
        _snapshot_is_trusted(snapshot)
        and _evidence_entries_are_bound(evidence, snapshot, output_dir)
    )
    safe["evidence_bindings_valid"] = evidence_bindings_valid
    safe["decision"] = (
        "candidate-passed-local"
        if (
            safe["dry_run"]["issues"] == []
            and safe["dry_run"]["database_unchanged"]
            and safe["interruption"]["status"] == "interrupted"
            and safe["interruption"]["updated_ids"] == [101]
            and safe["interruption"]["verification"]["status"] == "interrupted"
            and safe["recovery"]["status"] == "verified"
            and safe["recovery"]["updated_ids"] == [102]
            and safe["recovery"]["verification"]["status"] == "verified"
            and safe["repeat"]["status"] == "verified"
            and safe["repeat"]["updated_ids"] == []
            and safe["repeat"]["verification"]["status"] == "verified"
            and safe["oracle"]["passed"]
            and safe["reconciliation"]["passed"]
            and evidence_bindings_valid
        )
        else "inconclusive"
    )
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
        "plan_digest": FROZEN_PLAN_DIGEST,
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
        "interruption": safe["interruption"],
        "recovery": safe["recovery"],
        "reconciliation": safe["reconciliation"],
        "evidence": evidence,
        "limitations": [
            "只覆盖固定双租户 SQLite fixture、受控子进程硬退出和单行 batch。",
            "未覆盖 PostgreSQL/MySQL、生产数据规模、锁等待、复制延迟或在线迁移。",
            "未覆盖并发写入、消息重复投递、跨服务事务、机器重启或非受控崩溃恢复。",
            "checkpoint 只服务本实验，不能视为通用 backfill runner。",
            "本 artifact 保持 inconclusive / insufficient，不能改变 Vega 成功语义。",
            "外部测试、静态检查与跨平台 CI 仍需在同一提交上单独验证。",
        ],
        "artifacts": {
            "result": "result.json",
            "report": "report.md",
            "dangerous_database": "dangerous.sqlite",
            "dangerous_oracle": "dangerous-oracle.json",
            "safe_dry_run_database": "safe-dry-run.sqlite",
            "safe_interrupted_database": "safe-interrupted.sqlite",
            "safe_recovered_database": "safe-recovered.sqlite",
            "safe_database": "safe.sqlite",
            "safe_oracle": "safe-oracle.json",
            "reconciliation": "reconciliation.json",
        },
    }
    _write_json(output_dir / "result.json", result)
    (output_dir / "report.md").write_text(_render_report(result), encoding="utf-8")
    return result


def _run_dangerous_twin(database_path: Path) -> dict[str, Any]:
    _create_fixture_database(database_path)
    detector_issues = _detect_update_scope(DANGEROUS_UPDATE_SQL)
    connection = sqlite3.connect(database_path)
    try:
        cursor = connection.execute(DANGEROUS_UPDATE_SQL)
        connection.commit()
        updated_rows = max(cursor.rowcount, 0)
    finally:
        connection.close()

    application_rows = _application_rows(database_path)
    oracle = _independent_oracle(database_path)
    expected_issue = (
        f"{THREAT_ID}:unbounded_update:"
        "tenant_or_target_scope_missing"
    )
    decision = (
        "reject"
        if (
            detector_issues == [expected_issue]
            and updated_rows == 3
            and not oracle["scope_preserved"]
        )
        else "inconclusive"
    )
    return {
        "statement": "dangerous-unbounded-update",
        "detector_issues": detector_issues,
        "forced_execution": {
            "status": "applied",
            "updated_rows": updated_rows,
        },
        "application_view_matches_baseline": application_rows == BASELINE_ROWS,
        "oracle": oracle,
        "decision": decision,
    }


def _run_safe_twin(output_dir: Path) -> dict[str, Any]:
    database_path = output_dir / "safe.sqlite"
    _create_fixture_database(database_path)
    plan = copy.deepcopy(FROZEN_PLAN)

    dry_run = _run_dry_run(database_path, plan, SAFE_UPDATE_SQL)
    # 每个阶段保留独立数据库副本，后续证据校验不依赖已经继续变化的同一文件。
    shutil.copy2(database_path, output_dir / "safe-dry-run.sqlite")

    interruption = _run_initial_backfill(
        database_path,
        plan,
        SAFE_UPDATE_SQL,
        interrupt_after_batches=1,
    )
    shutil.copy2(database_path, output_dir / "safe-interrupted.sqlite")

    recovery = _run_recovery(database_path, plan, SAFE_UPDATE_SQL)
    shutil.copy2(database_path, output_dir / "safe-recovered.sqlite")

    repeat = _run_recovery(database_path, plan, SAFE_UPDATE_SQL)
    oracle = _independent_oracle(database_path)
    checkpoint = _checkpoint_row(database_path)
    reconciliation = {
        "passed": (
            oracle["passed"]
            and checkpoint is not None
            and checkpoint["plan_digest"] == FROZEN_PLAN_DIGEST
            and checkpoint["target_count"] == 2
            and checkpoint["completed_count"] == 2
            and checkpoint["status"] == "completed"
        ),
        "rows": oracle["rows"],
        "target_completed_count": oracle["target_completed_count"],
        "out_of_scope_rows": oracle["out_of_scope_rows"],
        "scope_preserved": oracle["scope_preserved"],
        "checkpoint": checkpoint,
        "database_sha256": oracle["database_sha256"],
    }
    return {
        "plan": plan,
        "dry_run": dry_run,
        "interruption": interruption,
        "recovery": recovery,
        "repeat": repeat,
        "oracle": oracle,
        "reconciliation": reconciliation,
        "evidence_bindings_valid": False,
        "decision": "inconclusive",
    }


def _create_fixture_database(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE customer ("
            "id INTEGER PRIMARY KEY, "
            "tenant_id TEXT NOT NULL, "
            "legacy_handle TEXT NOT NULL, "
            "canonical_handle TEXT, "
            "backfill_version INTEGER NOT NULL DEFAULT 0)"
        )
        connection.executemany(
            "INSERT INTO customer ("
            "id, tenant_id, legacy_handle, canonical_handle, backfill_version"
            ") VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["id"],
                    row["tenant_id"],
                    row["legacy_handle"],
                    row["canonical_handle"],
                    row["backfill_version"],
                )
                for row in BASELINE_ROWS
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _run_dry_run(
    database_path: Path,
    plan: dict[str, Any],
    update_sql: str,
) -> dict[str, Any]:
    before_hash = _sha256_file(database_path)
    connection = sqlite3.connect(database_path)
    try:
        target_ids = [int(item) for item in plan.get("target_ids", [])]
        placeholders = ",".join("?" for _ in target_ids) or "NULL"
        tenant_id = plan.get("tenant_id")
        rows = connection.execute(
            "SELECT id FROM customer "
            "WHERE tenant_id = ? "
            f"AND id IN ({placeholders}) "
            "AND canonical_handle IS NULL "
            "AND backfill_version = 0 "
            "ORDER BY id",
            (tenant_id, *target_ids),
        ).fetchall()
        candidate_rows = [int(row[0]) for row in rows]
        out_of_scope_rows = _out_of_scope_rows(connection)
    finally:
        connection.close()
    after_hash = _sha256_file(database_path)

    candidate_count = len(candidate_rows)
    row_budget = int(plan.get("row_budget", -1))
    issues = _plan_issues(plan)
    issues.extend(_detect_update_scope(update_sql))
    if candidate_rows != list(FROZEN_PLAN["target_ids"]):
        issues.append(f"{THREAT_ID}:dry_run:candidate_rows_mismatch")
    if candidate_count > row_budget:
        issues.append(f"{THREAT_ID}:plan:row_budget_exceeded")
    if out_of_scope_rows != EXPECTED_OUT_OF_SCOPE_ROWS:
        issues.append(f"{THREAT_ID}:dry_run:out_of_scope_snapshot_mismatch")
    if before_hash != after_hash:
        issues.append(f"{THREAT_ID}:dry_run:database_changed")
    return {
        "candidate_rows": candidate_rows,
        "candidate_count": candidate_count,
        "row_budget": row_budget,
        "within_budget": candidate_count <= row_budget,
        "plan_digest": _plan_digest(plan),
        "write_count": 0,
        "database_unchanged": before_hash == after_hash,
        "issues": _unique(issues),
    }


def _run_initial_backfill(
    database_path: Path,
    plan: dict[str, Any],
    update_sql: str,
    *,
    interrupt_after_batches: int | None,
) -> dict[str, Any]:
    dry_run = _run_dry_run(database_path, plan, update_sql)
    database_fact = _classify_target_rows(database_path, plan)
    issues = list(dry_run["issues"])
    if database_fact["conflict_ids"]:
        issues.append(f"{THREAT_ID}:preflight:conflicting_existing_value")
    if database_fact["missing_ids"]:
        issues.append(f"{THREAT_ID}:preflight:target_missing")
    if issues:
        return {
            "status": "rejected",
            "issues": _unique(issues),
            "updated_ids": [],
            "skipped_exact_ids": database_fact["exact_ids"],
            "conflict_ids": database_fact["conflict_ids"],
            "dry_run": dry_run,
            "database_fact_before": database_fact,
            "verification": {
                "status": "rejected",
                "target_mapping_verified": False,
            },
        }

    plan_digest = _plan_digest(plan)
    _initialize_checkpoint(database_path, plan_digest, len(plan["target_ids"]))
    if interrupt_after_batches == 1:
        result = _run_first_batch_in_subprocess(database_path, plan, update_sql)
    elif interrupt_after_batches is None:
        result = _process_targets(database_path, plan, update_sql)
    else:
        result = {
            "status": "rejected",
            "issues": [f"{THREAT_ID}:interruption:unsupported_injection_point"],
            "updated_ids": [],
            "skipped_exact_ids": [],
            "conflict_ids": [],
            "verification": {
                "status": "rejected",
                "target_mapping_verified": False,
            },
        }
    result["dry_run"] = dry_run
    return result


def _run_recovery(
    database_path: Path,
    plan: dict[str, Any],
    update_sql: str,
) -> dict[str, Any]:
    issues = _plan_issues(plan)
    issues.extend(_detect_update_scope(update_sql))
    checkpoint_before = _checkpoint_row(database_path)
    # checkpoint 只提供诊断；是否继续由重新读取的数据库事实决定。
    database_fact_before = _classify_target_rows(database_path, plan)
    if checkpoint_before is None:
        issues.append(f"{THREAT_ID}:checkpoint:missing")
    elif checkpoint_before["plan_digest"] != _plan_digest(plan):
        issues.append(f"{THREAT_ID}:checkpoint:plan_digest_mismatch")
    elif checkpoint_before["target_count"] != len(plan["target_ids"]):
        issues.append(f"{THREAT_ID}:checkpoint:target_count_mismatch")
    elif not 0 <= checkpoint_before["completed_count"] <= checkpoint_before["target_count"]:
        issues.append(f"{THREAT_ID}:checkpoint:completed_count_invalid")
    if database_fact_before["conflict_ids"]:
        issues.append(f"{THREAT_ID}:recovery:conflicting_existing_value")
    if database_fact_before["missing_ids"]:
        issues.append(f"{THREAT_ID}:recovery:target_missing")
    if issues:
        return {
            "status": "rejected",
            "issues": _unique(issues),
            "updated_ids": [],
            "skipped_exact_ids": database_fact_before["exact_ids"],
            "conflict_ids": database_fact_before["conflict_ids"],
            "checkpoint_before": checkpoint_before,
            "database_fact_before": database_fact_before,
            "verification": {
                "status": "rejected",
                "target_mapping_verified": False,
            },
        }

    result = _process_targets(database_path, plan, update_sql)
    result["checkpoint_before"] = checkpoint_before
    result["database_fact_before"] = database_fact_before
    return result


def _run_first_batch_in_subprocess(
    database_path: Path,
    plan: dict[str, Any],
    update_sql: str,
) -> dict[str, Any]:
    if plan != FROZEN_PLAN or update_sql != SAFE_UPDATE_SQL:
        return {
            "status": "rejected",
            "issues": [f"{THREAT_ID}:interruption:non_frozen_worker_input"],
            "updated_ids": [],
            "skipped_exact_ids": [],
            "conflict_ids": [],
            "verification": {
                "status": "rejected",
                "target_mapping_verified": False,
            },
        }
    worker_environment = os.environ.copy()
    worker_environment[INTERNAL_WORKER_SCRIPT_ENV] = str(
        SOURCE_PROJECT_ROOT / PRODUCER
    )
    worker_environment[INTERNAL_WORKER_DATABASE_ENV] = str(database_path)
    result = subprocess.run(
        [sys.executable, "-c", INTERNAL_WORKER_BOOTSTRAP],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=worker_environment,
    )
    fact_after = _classify_target_rows(database_path, plan)
    interrupted = (
        result.returncode == INJECTED_PROCESS_EXIT_CODE
        and fact_after["exact_ids"] == [101]
        and fact_after["pending_ids"] == [102]
        and not fact_after["conflict_ids"]
        and not fact_after["missing_ids"]
    )
    return {
        "status": "interrupted" if interrupted else "rejected",
        "issues": (
            []
            if interrupted
            else [f"{THREAT_ID}:interruption:process_boundary_not_observed"]
        ),
        "updated_ids": [101] if interrupted else [],
        "skipped_exact_ids": [],
        "conflict_ids": fact_after["conflict_ids"],
        "process_exit_code": result.returncode,
        "process_boundary_observed": interrupted,
        "checkpoint_after": _checkpoint_row(database_path),
        "database_fact_after": fact_after,
        "verification": {
            "status": "interrupted" if interrupted else "rejected",
            "target_mapping_verified": False,
            "reason": "injected_child_process_exit_after_committed_batch",
        },
    }


def _run_injected_first_batch_worker(database_path: Path) -> None:
    batch = _apply_target_batch(
        database_path,
        FROZEN_PLAN,
        SAFE_UPDATE_SQL,
        101,
        checkpoint_status="interrupted",
    )
    os._exit(
        INJECTED_PROCESS_EXIT_CODE
        if batch["status"] == "updated"
        else INJECTED_PROCESS_EXIT_CODE + 1
    )


def _process_targets(
    database_path: Path,
    plan: dict[str, Any],
    update_sql: str,
) -> dict[str, Any]:
    updated_ids: list[int] = []
    skipped_exact_ids: list[int] = []

    for target_id in plan["execution_order"]:
        current_fact = _classify_single_target(database_path, plan, int(target_id))
        if current_fact == "exact":
            skipped_exact_ids.append(int(target_id))
            continue
        if current_fact != "pending":
            return _failed_processing_result(
                database_path,
                plan,
                updated_ids,
                skipped_exact_ids,
                int(target_id),
            )

        batch = _apply_target_batch(
            database_path,
            plan,
            update_sql,
            int(target_id),
            checkpoint_status="running",
        )
        if batch["status"] == "updated":
            updated_ids.append(int(target_id))
        elif batch["status"] == "already_exact":
            skipped_exact_ids.append(int(target_id))
        else:
            return _failed_processing_result(
                database_path,
                plan,
                updated_ids,
                skipped_exact_ids,
                int(target_id),
            )
    fact_after = _classify_target_rows(database_path, plan)
    verified = (
        fact_after["exact_ids"] == list(plan["execution_order"])
        and not fact_after["pending_ids"]
        and not fact_after["conflict_ids"]
        and not fact_after["missing_ids"]
    )
    if verified:
        _mark_checkpoint_completed(database_path, plan)
    return {
        "status": "verified" if verified else "rejected",
        "issues": [] if verified else [f"{THREAT_ID}:verification:target_mismatch"],
        "updated_ids": updated_ids,
        "skipped_exact_ids": skipped_exact_ids,
        "conflict_ids": fact_after["conflict_ids"],
        "checkpoint_after": _checkpoint_row(database_path),
        "database_fact_after": fact_after,
        "verification": {
            "status": "verified" if verified else "rejected",
            "target_mapping_verified": verified,
        },
    }


def _failed_processing_result(
    database_path: Path,
    plan: dict[str, Any],
    updated_ids: list[int],
    skipped_exact_ids: list[int],
    target_id: int,
) -> dict[str, Any]:
    fact_after = _classify_target_rows(database_path, plan)
    return {
        "status": "rejected",
        "issues": [f"{THREAT_ID}:execution:target_conflict_or_race"],
        "updated_ids": updated_ids,
        "skipped_exact_ids": skipped_exact_ids,
        "conflict_ids": sorted(set([*fact_after["conflict_ids"], target_id])),
        "checkpoint_after": _checkpoint_row(database_path),
        "database_fact_after": fact_after,
        "verification": {
            "status": "rejected",
            "target_mapping_verified": False,
        },
    }


def _apply_target_batch(
    database_path: Path,
    plan: dict[str, Any],
    update_sql: str,
    target_id: int,
    *,
    checkpoint_status: str,
) -> dict[str, Any]:
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        state = _classify_single_target_connection(connection, plan, target_id)
        if state == "exact":
            connection.rollback()
            return {"status": "already_exact", "updated_rows": 0}
        if state != "pending":
            connection.rollback()
            return {"status": "conflict", "updated_rows": 0}

        expected_value = str(plan["mapping"][str(target_id)])
        cursor = connection.execute(
            update_sql,
            (expected_value, plan["tenant_id"], target_id),
        )
        updated_rows = max(cursor.rowcount, 0)
        state_after = _classify_single_target_connection(
            connection,
            plan,
            target_id,
        )
        if updated_rows != 1 or state_after != "exact":
            connection.rollback()
            return {"status": "conflict", "updated_rows": updated_rows}

        fact_after = _classify_target_rows_connection(connection, plan)
        completed_count = len(fact_after["exact_ids"])
        # 行更新与 checkpoint 计数必须在同一事务提交，任一写入失败都整体回滚。
        checkpoint_cursor = connection.execute(
            "UPDATE backfill_checkpoint "
            "SET completed_count = ?, status = ? "
            "WHERE experiment_id = ? AND plan_digest = ?",
            (
                completed_count,
                checkpoint_status,
                EXPERIMENT_ID,
                _plan_digest(plan),
            ),
        )
        if checkpoint_cursor.rowcount != 1:
            raise sqlite3.IntegrityError(
                "checkpoint update did not match exactly one row"
            )
        connection.commit()
        return {"status": "updated", "updated_rows": updated_rows}
    except sqlite3.DatabaseError:
        connection.rollback()
        raise
    finally:
        connection.close()


def _initialize_checkpoint(
    database_path: Path,
    plan_digest: str,
    target_count: int,
) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS backfill_checkpoint ("
            "experiment_id TEXT PRIMARY KEY, "
            "plan_digest TEXT NOT NULL, "
            "target_count INTEGER NOT NULL, "
            "completed_count INTEGER NOT NULL, "
            "status TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO backfill_checkpoint ("
            "experiment_id, plan_digest, target_count, completed_count, status"
            ") VALUES (?, ?, ?, 0, 'running')",
            (EXPERIMENT_ID, plan_digest, target_count),
        )
        row = connection.execute(
            "SELECT plan_digest, target_count FROM backfill_checkpoint "
            "WHERE experiment_id = ?",
            (EXPERIMENT_ID,),
        ).fetchone()
        if row != (plan_digest, target_count):
            raise sqlite3.IntegrityError(
                "checkpoint does not match the frozen plan"
            )
        connection.commit()
    finally:
        connection.close()


def _mark_checkpoint_completed(
    database_path: Path,
    plan: dict[str, Any],
) -> None:
    checkpoint = _checkpoint_row(database_path)
    completed_count = len(plan["target_ids"])
    if (
        checkpoint is not None
        and checkpoint["completed_count"] == completed_count
        and checkpoint["status"] == "completed"
    ):
        return
    connection = sqlite3.connect(database_path)
    try:
        cursor = connection.execute(
            "UPDATE backfill_checkpoint "
            "SET completed_count = ?, status = 'completed' "
            "WHERE experiment_id = ? AND plan_digest = ?",
            (completed_count, EXPERIMENT_ID, _plan_digest(plan)),
        )
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError(
                "checkpoint completion did not match exactly one row"
            )
        connection.commit()
    finally:
        connection.close()


def _checkpoint_row(database_path: Path) -> dict[str, Any] | None:
    connection = sqlite3.connect(database_path)
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'backfill_checkpoint'"
        ).fetchone()
        if table_exists is None:
            return None
        row = connection.execute(
            "SELECT plan_digest, target_count, completed_count, status "
            "FROM backfill_checkpoint WHERE experiment_id = ?",
            (EXPERIMENT_ID,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "plan_digest": str(row[0]),
        "target_count": int(row[1]),
        "completed_count": int(row[2]),
        "status": str(row[3]),
    }


def _classify_target_rows(
    database_path: Path,
    plan: dict[str, Any],
) -> dict[str, list[int]]:
    connection = sqlite3.connect(database_path)
    try:
        return _classify_target_rows_connection(connection, plan)
    finally:
        connection.close()


def _classify_target_rows_connection(
    connection: sqlite3.Connection,
    plan: dict[str, Any],
) -> dict[str, list[int]]:
    result = {
        "exact_ids": [],
        "pending_ids": [],
        "conflict_ids": [],
        "missing_ids": [],
    }
    for target_id in plan.get("execution_order", []):
        state = _classify_single_target_connection(connection, plan, int(target_id))
        result[f"{state}_ids"].append(int(target_id))
    return result


def _classify_single_target(
    database_path: Path,
    plan: dict[str, Any],
    target_id: int,
) -> str:
    connection = sqlite3.connect(database_path)
    try:
        return _classify_single_target_connection(connection, plan, target_id)
    finally:
        connection.close()


def _classify_single_target_connection(
    connection: sqlite3.Connection,
    plan: dict[str, Any],
    target_id: int,
) -> str:
    row = connection.execute(
        "SELECT tenant_id, canonical_handle, backfill_version "
        "FROM customer WHERE id = ?",
        (target_id,),
    ).fetchone()
    if row is None:
        return "missing"
    tenant_id, canonical_handle, version = row
    expected_value = plan.get("mapping", {}).get(str(target_id))
    if (
        tenant_id == plan.get("tenant_id")
        and canonical_handle == expected_value
        and int(version) == 1
    ):
        return "exact"
    if (
        tenant_id == plan.get("tenant_id")
        and canonical_handle is None
        and int(version) == 0
    ):
        return "pending"
    return "conflict"


def _plan_issues(plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if plan.get("tenant_id") != FROZEN_PLAN["tenant_id"]:
        issues.append(f"{THREAT_ID}:plan:tenant_scope_mismatch")
    if list(plan.get("target_ids", [])) != FROZEN_PLAN["target_ids"]:
        issues.append(f"{THREAT_ID}:plan:target_set_out_of_scope")
    if list(plan.get("execution_order", [])) != FROZEN_PLAN["execution_order"]:
        issues.append(f"{THREAT_ID}:plan:execution_order_mismatch")
    if plan.get("row_budget") != FROZEN_PLAN["row_budget"]:
        issues.append(f"{THREAT_ID}:plan:row_budget_mismatch")
    if plan.get("batch_size") != FROZEN_PLAN["batch_size"]:
        issues.append(f"{THREAT_ID}:plan:batch_size_mismatch")
    if dict(plan.get("mapping", {})) != FROZEN_PLAN["mapping"]:
        issues.append(f"{THREAT_ID}:plan:mapping_mismatch")
    return issues


def _detect_update_scope(update_sql: str) -> list[str]:
    normalized = " ".join(update_sql.lower().split())
    has_tenant = re.search(r"\btenant_id\s*=\s*\?", normalized) is not None
    has_target = re.search(r"\bid\s*=\s*\?", normalized) is not None
    has_pending_state = (
        "canonical_handle is null" in normalized
        and "backfill_version = 0" in normalized
    )
    if not has_tenant and not has_target:
        return [
            f"{THREAT_ID}:unbounded_update:"
            "tenant_or_target_scope_missing"
        ]
    issues: list[str] = []
    if not has_tenant:
        issues.append(f"{THREAT_ID}:bounded_update:tenant_scope_missing")
    if not has_target:
        issues.append(f"{THREAT_ID}:bounded_update:target_scope_missing")
    if not has_pending_state:
        issues.append(f"{THREAT_ID}:bounded_update:state_scope_missing")
    return issues


def _application_rows(database_path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database_path)
    try:
        return _customer_rows(connection)
    finally:
        connection.close()


def _customer_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, tenant_id, legacy_handle, canonical_handle, "
        "backfill_version FROM customer ORDER BY id"
    ).fetchall()
    return [
        {
            "id": int(row[0]),
            "tenant_id": str(row[1]),
            "legacy_handle": str(row[2]),
            "canonical_handle": row[3],
            "backfill_version": int(row[4]),
        }
        for row in rows
    ]


def _out_of_scope_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, tenant_id, legacy_handle, canonical_handle, "
        "backfill_version FROM customer "
        "WHERE tenant_id <> 'tenant-a' ORDER BY id"
    ).fetchall()
    return [
        {
            "id": int(row[0]),
            "tenant_id": str(row[1]),
            "legacy_handle": str(row[2]),
            "canonical_handle": row[3],
            "backfill_version": int(row[4]),
        }
        for row in rows
    ]


def _independent_oracle(database_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database_path)
    try:
        # Oracle 直接执行冻结 SQL，不复用 backfill、detector、checkpoint 或应用读取 helper。
        rows = [
            {
                "id": int(row[0]),
                "tenant_id": str(row[1]),
                "legacy_handle": str(row[2]),
                "canonical_handle": row[3],
                "backfill_version": int(row[4]),
            }
            for row in connection.execute(
                "SELECT id, tenant_id, legacy_handle, canonical_handle, "
                "backfill_version FROM customer ORDER BY id"
            )
        ]
        target_completed_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM customer "
                "WHERE tenant_id = 'tenant-a' "
                "AND id IN (101, 102) "
                "AND canonical_handle IS NOT NULL "
                "AND backfill_version = 1"
            ).fetchone()[0]
        )
        out_of_scope_rows = [
            {
                "id": int(row[0]),
                "tenant_id": str(row[1]),
                "legacy_handle": str(row[2]),
                "canonical_handle": row[3],
                "backfill_version": int(row[4]),
            }
            for row in connection.execute(
                "SELECT id, tenant_id, legacy_handle, canonical_handle, "
                "backfill_version FROM customer "
                "WHERE tenant_id <> 'tenant-a' ORDER BY id"
            )
        ]
    finally:
        connection.close()
    scope_preserved = out_of_scope_rows == EXPECTED_OUT_OF_SCOPE_ROWS
    return {
        "name": "independent-sql-oracle",
        "passed": rows == EXPECTED_FINAL_ROWS,
        "rows": rows,
        "target_completed_count": target_completed_count,
        "out_of_scope_rows": out_of_scope_rows,
        "scope_preserved": scope_preserved,
        "database_sha256": _sha256_file(database_path),
    }


def _build_evidence(
    output_dir: Path,
    snapshot: dict[str, Any],
    dangerous: dict[str, Any],
    safe: dict[str, Any],
) -> list[dict[str, Any]]:
    reconciliation_path = output_dir / "reconciliation.json"
    cases = [
        (
            "EV-STAGE3-DANGER",
            "dangerous-twin",
            dangerous,
            dangerous["oracle"],
            "dangerous.sqlite",
            ["T-DATA-SCOPE", "T-DATA-INTEGRITY"],
            ["dangerous.sqlite", "dangerous-oracle.json"],
        ),
        (
            "EV-STAGE3-DRY-RUN",
            "dry-run",
            safe["dry_run"],
            _phase_oracle(output_dir / "safe-dry-run.sqlite", "read-only"),
            "safe-dry-run.sqlite",
            ["T-DATA-SCOPE"],
            ["safe-dry-run.sqlite"],
        ),
        (
            "EV-STAGE3-INTERRUPTION",
            "interruption",
            safe["interruption"],
            _phase_oracle(output_dir / "safe-interrupted.sqlite", "interrupted"),
            "safe-interrupted.sqlite",
            ["T-DATA-PARTIAL"],
            ["safe-interrupted.sqlite"],
        ),
        (
            "EV-STAGE3-RECOVERY",
            "recovery",
            safe["recovery"],
            _phase_oracle(output_dir / "safe-recovered.sqlite", "verified"),
            "safe-recovered.sqlite",
            ["T-DATA-PARTIAL", "T-DATA-RETRY"],
            ["safe-recovered.sqlite"],
        ),
        (
            "EV-STAGE3-REPEAT",
            "repeat",
            safe["repeat"],
            {
                **safe["oracle"],
                "database_artifact": "safe.sqlite",
            },
            "safe.sqlite",
            ["T-DATA-RETRY"],
            ["safe.sqlite", "safe-oracle.json"],
        ),
        (
            "EV-STAGE3-RECONCILIATION",
            "reconciliation",
            safe["reconciliation"],
            {
                **safe["oracle"],
                "database_artifact": "safe.sqlite",
                "report_artifact": "reconciliation.json",
                "report_sha256": _sha256_file(reconciliation_path),
            },
            "safe.sqlite",
            ["T-DATA-INTEGRITY", "T-DATA-SCOPE"],
            ["safe.sqlite", "safe-oracle.json", "reconciliation.json"],
        ),
    ]
    evidence: list[dict[str, Any]] = []
    for (
        evidence_id,
        kind,
        case_result,
        oracle,
        database_artifact,
        covers,
        artifacts,
    ) in cases:
        evidence.append(
            {
                "id": evidence_id,
                "kind": kind,
                "producer": PRODUCER,
                "command": (
                    "python scripts/run_assurance_stage3_dml_backfill_experiment.py "
                    "--output-dir <repo-relative-local-validation-dir>"
                ),
                "environment": {
                    "python": platform.python_version(),
                    "sqlite": sqlite3.sqlite_version,
                    "platform": sys.platform,
                },
                "snapshot": copy.deepcopy(snapshot),
                "input": {
                    "fixture_sha256": FIXTURE_SHA256,
                    "plan_digest": FROZEN_PLAN_DIGEST,
                },
                "oracle": {
                    **oracle,
                    "database_artifact": database_artifact,
                },
                "result": copy.deepcopy(case_result),
                "covers": covers,
                "artifacts": artifacts,
                "artifact_hashes": {
                    artifact: _sha256_file(output_dir / artifact)
                    for artifact in artifacts
                },
                "limitations": [
                    "固定 SQLite fixture。",
                    "不能替代外部质量门禁或生产演练。",
                ],
            }
        )
    return evidence


def _phase_oracle(database_path: Path, status: str) -> dict[str, Any]:
    return {
        "name": "direct-sql-phase-snapshot",
        "status": status,
        "database_artifact": database_path.name,
        "database_sha256": _sha256_file(database_path),
    }


def _evidence_entries_are_bound(
    evidence: list[dict[str, Any]],
    snapshot: dict[str, Any],
    output_dir: Path,
) -> bool:
    try:
        validated_output = _validate_output_dir(output_dir)
    except (OSError, ValueError):
        return False
    expected_kinds = {
        "dangerous-twin",
        "dry-run",
        "interruption",
        "recovery",
        "repeat",
        "reconciliation",
    }
    if {entry.get("kind") for entry in evidence} != expected_kinds:
        return False
    return all(
        _evidence_entry_is_bound(entry, snapshot, validated_output)
        for entry in evidence
    )


def _evidence_entry_is_bound(
    entry: dict[str, Any],
    snapshot: dict[str, Any],
    output_dir: Path,
) -> bool:
    if entry.get("snapshot") != snapshot:
        return False
    evidence_input = entry.get("input", {})
    if evidence_input.get("fixture_sha256") != FIXTURE_SHA256:
        return False
    if evidence_input.get("plan_digest") != FROZEN_PLAN_DIGEST:
        return False
    if not _declared_artifacts_are_bound(entry, output_dir):
        return False
    oracle = entry.get("oracle", {})
    if not _artifact_hash_matches(
        output_dir,
        oracle.get("database_artifact"),
        oracle.get("database_sha256"),
    ):
        return False
    report_artifact = oracle.get("report_artifact")
    return report_artifact is None or _artifact_hash_matches(
        output_dir,
        report_artifact,
        oracle.get("report_sha256"),
    )


def _declared_artifacts_are_bound(
    entry: dict[str, Any],
    output_dir: Path,
) -> bool:
    artifacts = entry.get("artifacts")
    artifact_hashes = entry.get("artifact_hashes")
    if not isinstance(artifacts, list) or not artifacts:
        return False
    if not isinstance(artifact_hashes, dict):
        return False
    if not all(_safe_artifact_name(item) for item in artifacts):
        return False
    artifact_names = [str(item) for item in artifacts]
    if len(set(artifact_names)) != len(artifact_names):
        return False
    if set(artifact_hashes) != set(artifact_names):
        return False
    return all(
        _artifact_hash_matches(
            output_dir,
            artifact_name,
            artifact_hashes.get(artifact_name),
        )
        for artifact_name in artifact_names
    )


def _artifact_hash_matches(
    output_dir: Path,
    artifact_name: Any,
    expected_sha256: Any,
) -> bool:
    if not _safe_artifact_name(artifact_name):
        return False
    artifact_path = output_dir / str(artifact_name)
    return (
        artifact_path.is_file()
        and isinstance(expected_sha256, str)
        and SHA256_PATTERN.fullmatch(expected_sha256) is not None
        and _sha256_file(artifact_path) == expected_sha256
    )


def _safe_artifact_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return (
        not path.is_absolute()
        and len(path.parts) == 1
        and path.name == value
        and value not in {".", ".."}
    )


def _repository_snapshot() -> dict[str, Any]:
    current_policy_sha256 = _policy_sha256()
    try:
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SOURCE_PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=SOURCE_PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        head = head_result.stdout.strip().lower()
        worktree_clean = not status_result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = "unavailable"
        worktree_clean = False
    return {
        "head": head,
        "worktree_clean": worktree_clean,
        "policy": {
            "path": POLICY_RELATIVE_PATH,
            "sha256": current_policy_sha256,
        },
        "fixture_sha256": FIXTURE_SHA256,
        "plan_digest": FROZEN_PLAN_DIGEST,
    }


def _snapshot_is_trusted(snapshot: dict[str, Any]) -> bool:
    # 脏工作区不能声称 artifact 与当前 HEAD 一致，候选结论必须保持 fail-closed。
    current_policy_sha256 = _policy_sha256()
    return (
        bool(HEAD_PATTERN.fullmatch(str(snapshot.get("head", ""))))
        and snapshot.get("worktree_clean") is True
        and isinstance(POLICY_SHA256, str)
        and SHA256_PATTERN.fullmatch(POLICY_SHA256) is not None
        and current_policy_sha256 == POLICY_SHA256
        and snapshot.get("policy")
        == {
            "path": POLICY_RELATIVE_PATH,
            "sha256": POLICY_SHA256,
        }
        and snapshot.get("fixture_sha256") == FIXTURE_SHA256
        and snapshot.get("plan_digest") == FROZEN_PLAN_DIGEST
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _render_report(result: dict[str, Any]) -> str:
    dangerous = result["dangerous_twin"]
    safe = result["safe_twin"]
    return "\n".join(
        [
            "# Assurance Stage 3 有界 DML/Backfill 实验",
            "",
            f"- 实验：`{result['experiment_id']}`",
            f"- Threat：`{result['threat_id']}`",
            f"- SQLite：`{result['engine']['version']}`",
            f"- 总体结论：`{result['overall_decision']}`",
            f"- 候选结论：`{result['candidate_decision']}`",
            f"- 证据充分性：`{result['evidence_adequacy']}`",
            f"- Runtime 集成：`{result['runtime_integration']}`",
            "- 外部质量门禁："
            f"`{result['external_quality_gates']['status']}`",
            "",
            "## 危险双生",
            "",
            f"- detector：`{', '.join(dangerous['detector_issues']) or 'none'}`",
            f"- 强制执行更新行数：`{dangerous['forced_execution']['updated_rows']}`",
            f"- 范围外快照保持：`{dangerous['oracle']['scope_preserved']}`",
            f"- 判定：`{dangerous['decision']}`",
            "",
            "## 安全双生",
            "",
            f"- dry-run 候选：`{safe['dry_run']['candidate_rows']}`",
            f"- 中断前已提交：`{safe['interruption']['updated_ids']}`",
            f"- 恢复更新：`{safe['recovery']['updated_ids']}`",
            f"- 重复执行更新：`{safe['repeat']['updated_ids']}`",
            f"- 独立 SQL oracle：`{safe['oracle']['passed']}`",
            f"- evidence 绑定：`{safe['evidence_bindings_valid']}`",
            f"- 判定：`{safe['decision']}`",
            "",
            "## 限制",
            "",
            *[f"- {item}" for item in result["limitations"]],
            "",
        ]
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


def _default_output_dir() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return LOCAL_VALIDATION_ROOT / f"assurance-stage3-dml-backfill-{timestamp}"


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="运行独立的 Assurance Stage 3 SQLite 有界 DML/backfill 双生实验。"
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
    except (
        OSError,
        ValueError,
        sqlite3.DatabaseError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"实验无法完成：{_safe_exception_message(exc)}")
        return 2
    artifact_path = (
        validated_output_dir.relative_to(PROJECT_ROOT.resolve(strict=True))
        / "result.json"
    )
    print(
        "Assurance Stage 3 有界 DML/backfill 实验执行完成："
        f"decision={result['overall_decision']}，"
        f"candidate={result['candidate_decision']}，"
        f"artifact={artifact_path.as_posix()}"
    )
    return _result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
