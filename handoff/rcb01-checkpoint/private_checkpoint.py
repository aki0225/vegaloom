from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


EXPERIMENT_ID = "RCB-01"
LOCAL_ROOT_RELATIVE = Path(".local-validation") / "rcb-01"
FORMAL_RUNS_RELATIVE = LOCAL_ROOT_RELATIVE / "formal-runs"
ARCHIVE_CHECKPOINT_PATH = "checkpoint.json"
ARCHIVE_MANIFEST_PATH = "artifact-manifest.json"
ALLOWED_RUN_SUBDIRECTORIES = {"candidate-worktree", "execution"}


class CheckpointError(RuntimeError):
    """检查点不满足冻结约束。"""


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"无法读取 JSON：{path.name}") from exc
    if not isinstance(payload, dict):
        raise CheckpointError(f"JSON 顶层必须是对象：{path.name}")
    return payload


def _validate_relative_archive_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if (
        not path
        or candidate.is_absolute()
        or "\\" in path
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise CheckpointError(f"归档包含不安全路径：{path!r}")
    return candidate


def _validate_resume_state(payload: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise CheckpointError("resume-state 的实验标识不匹配")
    generator_revision = payload.get("generator_revision")
    if not isinstance(generator_revision, str) or len(generator_revision) != 40:
        raise CheckpointError("resume-state 缺少合法 generator_revision")

    consumed = payload.get("consumed_run_labels")
    if not isinstance(consumed, list) or not consumed:
        raise CheckpointError("resume-state 缺少已消费序号")
    if not all(isinstance(label, str) for label in consumed):
        raise CheckpointError("已消费序号必须全部是字符串")

    sequences: list[int] = []
    for label in consumed:
        prefix, separator, _ = label.partition("-")
        if not separator or not prefix.isdigit():
            raise CheckpointError(f"无法解析已消费序号：{label}")
        sequences.append(int(prefix))
    if sequences != list(range(1, len(consumed) + 1)):
        raise CheckpointError("已消费序号必须从 1 开始连续排列")

    next_run = payload.get("next_run")
    if not isinstance(next_run, dict):
        raise CheckpointError("resume-state 缺少下一运行")
    expected_sequence = len(consumed) + 1
    if next_run.get("sequence") != expected_sequence:
        raise CheckpointError("下一运行序号与已消费数量不一致")
    next_label = next_run.get("label")
    if (
        not isinstance(next_label, str)
        or not next_label.startswith(f"{expected_sequence:02d}-")
    ):
        raise CheckpointError("下一运行标签与序号不一致")
    if next_run.get("confirmation") != f"RCB-01-RUN-{expected_sequence}":
        raise CheckpointError("下一运行确认字符串不一致")

    for key in ("runner_sha256", "freeze_sha256"):
        value = payload.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise CheckpointError(f"resume-state 缺少合法 {key}")
    return consumed, next_run


def _git_text(repo_root: Path, *arguments: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise CheckpointError(
            f"Git 检查失败：git {' '.join(arguments)}：{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _validate_source_contract(
    *,
    repo_root: Path,
    state: dict[str, Any],
    runner_path: Path,
    freeze_path: Path,
) -> None:
    actual_head = _git_text(repo_root, "rev-parse", "HEAD")
    if actual_head != state["generator_revision"]:
        raise CheckpointError(
            "源工作区 HEAD 与 resume-state 的 generator_revision 不一致"
        )
    if _sha256_file(runner_path) != state["runner_sha256"]:
        raise CheckpointError("源 Runner 哈希与 resume-state 不一致")
    if _sha256_file(freeze_path) != state["freeze_sha256"]:
        raise CheckpointError("源 Freeze 哈希与 resume-state 不一致")


def _collect_artifacts(
    repo_root: Path,
    consumed_labels: list[str],
) -> dict[str, bytes]:
    formal_runs = repo_root / FORMAL_RUNS_RELATIVE
    if not formal_runs.is_dir():
        raise CheckpointError("源工作区缺少 formal-runs")

    root_entries = list(formal_runs.iterdir())
    unexpected_root_files = sorted(entry.name for entry in root_entries if not entry.is_dir())
    if unexpected_root_files:
        raise CheckpointError(
            "formal-runs 包含未登记文件：" + "、".join(unexpected_root_files)
        )
    actual_labels = sorted(entry.name for entry in root_entries if entry.is_dir())
    if actual_labels != consumed_labels:
        raise CheckpointError(
            "formal-runs 与 resume-state 不一致："
            f"实际 {actual_labels}，期望 {consumed_labels}"
        )

    artifacts: dict[str, bytes] = {}
    for label in consumed_labels:
        run_root = formal_runs / label
        entries = list(run_root.iterdir())
        unexpected_directories = sorted(
            entry.name
            for entry in entries
            if entry.is_dir() and entry.name not in ALLOWED_RUN_SUBDIRECTORIES
        )
        if unexpected_directories:
            raise CheckpointError(
                f"{label} 包含未登记目录：" + "、".join(unexpected_directories)
            )
        if not (run_root / "run-registration.json").is_file():
            raise CheckpointError(f"{label} 缺少 run-registration.json")

        files = sorted(entry for entry in entries if entry.is_file())
        execution_root = run_root / "execution"
        if execution_root.exists():
            if not execution_root.is_dir() or execution_root.is_symlink():
                raise CheckpointError(f"{label} 的 execution 不是普通目录")
            files.extend(
                sorted(
                    entry
                    for entry in execution_root.rglob("*")
                    if entry.is_file()
                )
            )

        for source in files:
            if source.is_symlink():
                raise CheckpointError(f"拒绝归档符号链接：{source.name}")
            relative = source.relative_to(formal_runs).as_posix()
            archive_path = f"formal-runs/{relative}"
            _validate_relative_archive_path(archive_path)
            artifacts[archive_path] = source.read_bytes()

    return artifacts


def _core_checkpoint_payload(
    *,
    state: dict[str, Any],
    artifact_manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "artifact_manifest_sha256": artifact_manifest_sha256,
        "consumed_run_labels": state["consumed_run_labels"],
        "created_at": datetime.now(UTC).isoformat(),
        "experiment_id": EXPERIMENT_ID,
        "freeze_sha256": state["freeze_sha256"],
        "generator_revision": state["generator_revision"],
        "next_run": state["next_run"],
        "runner_sha256": state["runner_sha256"],
        "schema_version": 1,
    }


def export_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve(strict=True)
    resume_state_path = Path(args.resume_state).resolve(strict=True)
    runner_path = Path(args.runner).resolve(strict=True)
    freeze_path = Path(args.freeze).resolve(strict=True)
    output_path = Path(args.output).resolve(strict=False)
    if output_path.exists():
        raise CheckpointError(f"拒绝覆盖已有检查点：{output_path.name}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    state = _read_json_object(resume_state_path)
    consumed, next_run = _validate_resume_state(state)
    _validate_source_contract(
        repo_root=repo_root,
        state=state,
        runner_path=runner_path,
        freeze_path=freeze_path,
    )
    artifacts = _collect_artifacts(repo_root, consumed)
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "files": [
            {
                "path": path,
                "sha256": _sha256_bytes(payload),
                "size": len(payload),
            }
            for path, payload in sorted(artifacts.items())
        ],
        "schema_version": 1,
    }
    manifest_bytes = _json_bytes(manifest)
    checkpoint = _core_checkpoint_payload(
        state=state,
        artifact_manifest_sha256=_sha256_bytes(manifest_bytes),
    )
    checkpoint_bytes = _json_bytes(checkpoint)

    with zipfile.ZipFile(
        output_path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.writestr(ARCHIVE_CHECKPOINT_PATH, checkpoint_bytes)
        archive.writestr(ARCHIVE_MANIFEST_PATH, manifest_bytes)
        for path, payload in sorted(artifacts.items()):
            archive.writestr(path, payload)

    return {
        "archive": output_path.name,
        "artifact_file_count": len(artifacts),
        "artifact_manifest_sha256": checkpoint["artifact_manifest_sha256"],
        "consumed_run_labels": consumed,
        "next_run": next_run,
        "sha256": _sha256_file(output_path),
        "size": output_path.stat().st_size,
        "status": "created",
    }


def _read_and_validate_archive(
    *,
    archive_path: Path,
    state: dict[str, Any],
    runner_path: Path,
    freeze_path: Path,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    bundle = state.get("private_artifact_bundle")
    if not isinstance(bundle, dict):
        raise CheckpointError("resume-state 没有登记私有 Artifact 归档")
    if archive_path.stat().st_size != bundle.get("size"):
        raise CheckpointError("私有 Artifact 归档大小不一致")
    if _sha256_file(archive_path) != bundle.get("sha256"):
        raise CheckpointError("私有 Artifact 归档 SHA-256 不一致")
    if _sha256_file(runner_path) != state["runner_sha256"]:
        raise CheckpointError("恢复目标 Runner 哈希不一致")
    if _sha256_file(freeze_path) != state["freeze_sha256"]:
        raise CheckpointError("恢复目标 Freeze 哈希不一致")

    with zipfile.ZipFile(archive_path, mode="r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise CheckpointError("私有归档包含重复路径")
        for name in names:
            _validate_relative_archive_path(name)
        if ARCHIVE_CHECKPOINT_PATH not in names or ARCHIVE_MANIFEST_PATH not in names:
            raise CheckpointError("私有归档缺少检查点元数据")

        checkpoint_bytes = archive.read(ARCHIVE_CHECKPOINT_PATH)
        manifest_bytes = archive.read(ARCHIVE_MANIFEST_PATH)
        try:
            checkpoint = json.loads(checkpoint_bytes.decode("utf-8"))
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckpointError("私有归档元数据不是合法 UTF-8 JSON") from exc
        if not isinstance(checkpoint, dict) or not isinstance(manifest, dict):
            raise CheckpointError("私有归档元数据顶层必须是对象")
        if checkpoint.get("schema_version") != 1:
            raise CheckpointError("私有归档检查点版本不受支持")
        if (
            manifest.get("schema_version") != 1
            or manifest.get("experiment_id") != EXPERIMENT_ID
        ):
            raise CheckpointError("Artifact 清单版本或实验标识不匹配")

        if checkpoint.get("artifact_manifest_sha256") != _sha256_bytes(manifest_bytes):
            raise CheckpointError("Artifact 清单哈希不一致")
        if (
            checkpoint.get("artifact_manifest_sha256")
            != bundle.get("artifact_manifest_sha256")
        ):
            raise CheckpointError("Artifact 清单哈希与 resume-state 不一致")
        expected_core = {
            "consumed_run_labels": state["consumed_run_labels"],
            "experiment_id": EXPERIMENT_ID,
            "freeze_sha256": state["freeze_sha256"],
            "generator_revision": state["generator_revision"],
            "next_run": state["next_run"],
            "runner_sha256": state["runner_sha256"],
        }
        for key, expected in expected_core.items():
            if checkpoint.get(key) != expected:
                raise CheckpointError(f"私有归档的 {key} 与恢复状态不一致")

        manifest_entries = manifest.get("files")
        if not isinstance(manifest_entries, list):
            raise CheckpointError("Artifact 清单缺少 files")
        artifacts: dict[str, bytes] = {}
        for entry in manifest_entries:
            if not isinstance(entry, dict):
                raise CheckpointError("Artifact 清单条目必须是对象")
            path = entry.get("path")
            if not isinstance(path, str):
                raise CheckpointError("Artifact 清单条目缺少路径")
            relative = _validate_relative_archive_path(path)
            if (
                len(relative.parts) < 3
                or relative.parts[0] != "formal-runs"
                or relative.parts[1] not in state["consumed_run_labels"]
            ):
                raise CheckpointError(f"Artifact 不在 formal-runs 下：{path}")
            if "candidate-worktree" in relative.parts:
                raise CheckpointError("私有归档不得包含 candidate-worktree")
            if path in artifacts:
                raise CheckpointError(f"Artifact 清单包含重复路径：{path}")
            try:
                payload = archive.read(path)
            except KeyError as exc:
                raise CheckpointError(f"私有归档缺少 Artifact：{path}") from exc
            if len(payload) != entry.get("size"):
                raise CheckpointError(f"Artifact 大小不一致：{path}")
            if _sha256_bytes(payload) != entry.get("sha256"):
                raise CheckpointError(f"Artifact SHA-256 不一致：{path}")
            artifacts[path] = payload

        if len(artifacts) != bundle.get("artifact_file_count"):
            raise CheckpointError("Artifact 文件数量与 resume-state 不一致")
        expected_names = {
            ARCHIVE_CHECKPOINT_PATH,
            ARCHIVE_MANIFEST_PATH,
            *artifacts.keys(),
        }
        if set(names) != expected_names:
            raise CheckpointError("私有归档包含清单之外的文件")
        for label in state["consumed_run_labels"]:
            registration = f"formal-runs/{label}/run-registration.json"
            if registration not in artifacts:
                raise CheckpointError(f"私有归档缺少已消费登记：{label}")
        return artifacts, checkpoint


def restore_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    archive_path = Path(args.archive).resolve(strict=True)
    destination = Path(args.destination).resolve(strict=False)
    resume_state_path = Path(args.resume_state).resolve(strict=True)
    runner_path = Path(args.runner).resolve(strict=True)
    freeze_path = Path(args.freeze).resolve(strict=True)
    if destination.exists():
        raise CheckpointError("恢复目标 formal-runs 已存在，拒绝覆盖")

    state = _read_json_object(resume_state_path)
    consumed, next_run = _validate_resume_state(state)
    artifacts, checkpoint = _read_and_validate_archive(
        archive_path=archive_path,
        state=state,
        runner_path=runner_path,
        freeze_path=freeze_path,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f"{destination.name}.restore-",
            dir=destination.parent,
        )
    )
    try:
        for label in consumed:
            (stage / label).mkdir()
        for archive_path_text, payload in artifacts.items():
            relative = PurePosixPath(archive_path_text)
            target = stage.joinpath(*relative.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(payload)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)

    return {
        "artifact_file_count": len(artifacts),
        "artifact_manifest_sha256": checkpoint["artifact_manifest_sha256"],
        "consumed_run_labels": consumed,
        "next_run": next_run,
        "status": "restored",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RCB-01 私有 Artifact 检查点导出与恢复工具。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export",
        help="从权威运行目录生成不进入公开 Git 的私有检查点。",
    )
    export_parser.add_argument("--repo-root", required=True)
    export_parser.add_argument("--resume-state", required=True)
    export_parser.add_argument("--runner", required=True)
    export_parser.add_argument("--freeze", required=True)
    export_parser.add_argument("--output", required=True)

    restore_parser = subparsers.add_parser(
        "restore",
        help="校验并恢复私有检查点中的原始 Artifact。",
    )
    restore_parser.add_argument("--archive", required=True)
    restore_parser.add_argument("--destination", required=True)
    restore_parser.add_argument("--resume-state", required=True)
    restore_parser.add_argument("--runner", required=True)
    restore_parser.add_argument("--freeze", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "export":
            result = export_checkpoint(args)
        else:
            result = restore_checkpoint(args)
    except (CheckpointError, OSError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(_json_bytes(result).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
