from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def run_timestamped_json_command(
    command: list[str],
    *,
    workspace: Path,
    prompt_path: Path,
    preflight_path: Path,
    event_path: Path,
    stderr_path: Path,
    result_path: Path,
    timeout_seconds: int,
    expected_environment_fingerprint: str | None = None,
) -> dict[str, Any]:
    """运行一次正式 Worker，并给每个 JSONL 事件补充本地接收时间。"""
    validate_worker_command(command)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds 必须是正整数")
    for path in (event_path, stderr_path, result_path):
        if path.exists():
            raise ValueError(f"正式运行产物已存在，禁止隐藏重跑：{path.name}")
    preflight = _read_json(preflight_path)
    fingerprint = preflight.get("environment_fingerprint")
    if preflight.get("schema_version") != 2 or preflight.get("status") != "ready":
        raise ValueError("V2 preflight 未达到 ready")
    if not isinstance(fingerprint, str) or SHA256_PATTERN.fullmatch(fingerprint) is None:
        raise ValueError("V2 preflight 缺少有效 environment_fingerprint")
    if expected_environment_fingerprint not in (None, fingerprint):
        raise ValueError("正式运行环境与冻结 environment_fingerprint 不一致")

    prompt = prompt_path.read_text(encoding="utf-8")
    for path in (event_path, stderr_path, result_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    process = _start_process(command, workspace.resolve())
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("Worker 子进程管道未建立")
    event_timing = {
        "event_count": 0,
        "invalid_event_count": 0,
        "first_received_at": None,
        "last_received_at": None,
    }
    stdout_thread = threading.Thread(
        target=_record_events,
        args=(process.stdout, event_path, event_timing),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_copy_stream,
        args=(process.stderr, stderr_path),
        daemon=True,
    )
    started_at = _now()
    started = time.monotonic()
    stdout_thread.start()
    stderr_thread.start()
    try:
        process.stdin.write(prompt)
    except BrokenPipeError:
        # Provider 或 CLI 可能在读取 prompt 前失败；仍需封存退出码和 stderr。
        pass
    finally:
        process.stdin.close()
    timed_out = False
    termination_confirmed = True
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        termination_confirmed = _terminate_owned_process(process)
    stdout_thread.join(timeout=10)
    stderr_thread.join(timeout=10)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        termination_confirmed = False
    payload = {
        "schema_version": 2,
        "experiment_version": "V2",
        "started_at": started_at,
        "finished_at": _now(),
        "wall_clock_seconds": round(time.monotonic() - started, 3),
        "timeout_seconds": timeout_seconds,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "termination_confirmed": termination_confirmed,
        "environment_fingerprint": fingerprint,
        "preflight_sha256": _sha256_bytes(preflight_path.read_bytes()),
        "prompt_sha256": _sha256_text(prompt),
        "command_fingerprint": _fingerprint(command),
        "event_timing": event_timing,
        "provider_request_performed": True,
    }
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def validate_worker_command(command: list[str]) -> None:
    if not command or "--json" not in command:
        raise ValueError("Worker 命令必须显式启用 --json")
    forbidden = {
        "--ignore-user-config",
        "--dangerously-bypass-approvals-and-sandbox",
    }
    present = sorted(forbidden.intersection(command))
    if present:
        raise ValueError(f"Worker 命令包含禁止参数：{present}")


def _start_process(command: list[str], workspace: Path) -> subprocess.Popen[str]:
    group_options: dict[str, Any] = (
        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    return subprocess.Popen(
        command,
        cwd=workspace,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **group_options,
    )


def _terminate_owned_process(process: subprocess.Popen[str]) -> bool:
    if process.poll() is not None:
        return True
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return True
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
    return process.poll() is not None


def _record_events(stream: TextIO, path: Path, state: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for line in stream:
            received_at = _now()
            state["event_count"] += 1
            state["first_received_at"] = state["first_received_at"] or received_at
            state["last_received_at"] = received_at
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError
                record = {
                    "schema_version": 2,
                    "sequence": state["event_count"],
                    "received_at": received_at,
                    "event": event,
                }
            except (json.JSONDecodeError, ValueError):
                state["invalid_event_count"] += 1
                record = {
                    "schema_version": 2,
                    "sequence": state["event_count"],
                    "received_at": received_at,
                    "event": None,
                    "invalid_raw_sha256": _sha256_text(line.rstrip("\r\n")),
                }
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()


def _copy_stream(stream: TextIO, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        for chunk in iter(lambda: stream.read(4096), ""):
            output.write(chunk)
            output.flush()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} 必须是 JSON object")
    return payload


def _fingerprint(payload: Any) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _sha256_text(normalized)


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="记录 V2 Codex Worker JSONL 接收时间。")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument("--expected-environment-fingerprint")
    parser.add_argument("worker_command", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.worker_command[1:] if args.worker_command[:1] == ["--"] else args.worker_command
    try:
        payload = run_timestamped_json_command(
            command,
            workspace=args.workspace,
            prompt_path=args.prompt,
            preflight_path=args.preflight,
            event_path=args.events,
            stderr_path=args.stderr,
            result_path=args.result,
            timeout_seconds=args.timeout_seconds,
            expected_environment_fingerprint=args.expected_environment_fingerprint,
        )
        if not payload["termination_confirmed"]:
            return 4
        if payload["timed_out"]:
            return 3
        if payload["event_timing"]["invalid_event_count"]:
            return 5
        return 0 if payload["exit_code"] == 0 else int(payload["exit_code"] or 1)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"V2 Worker 运行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
