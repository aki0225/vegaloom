"""独立验证 Dormice CLI 的 timeout 参数边界。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


INVALID_VALUES = (
    "not-a-number",
    "10m",
    "0",
    "-1",
    "1.5",
    "Infinity",
)
VALID_VALUES = {
    "1": 1,
    "60": 60,
    "1e2": 100,
    "0x10": 16,
    "+1": 1,
    "86401": 86401,
}
FORBIDDEN_ERROR_FRAGMENTS = (
    "abortsignal",
    "rangeerror",
    'the value of "delay"',
    "traceback",
)


@dataclass
class RequestLedger:
    """保存哨兵收到的请求，避免 oracle 依赖目标仓库测试 helper。"""

    requests: list[dict[str, Any]] = field(default_factory=list)
    accepted_connections: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record_connection(self) -> None:
        with self.lock:
            self.accepted_connections += 1

    def append(self, request: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(request)

    def snapshot(self) -> tuple[int, list[dict[str, Any]]]:
        with self.lock:
            return self.accepted_connections, list(self.requests)


class SentinelHandler(BaseHTTPRequestHandler):
    """只接受本地 CLI 的 JSON 请求，并返回固定 exec 结果。"""

    server: "SentinelServer"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 固定接口
        length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(length)
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"invalid_json": True}
        self.server.ledger.append(
            {
                "path": self.path,
                "authorization": self.headers.get("authorization"),
                "body": body,
            }
        )
        payload = json.dumps(
            {
                "exitCode": 0,
                "stdout": "oracle-ok\n",
                "stderr": "",
                "stdoutTruncated": False,
                "stderrTruncated": False,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        """禁止把本地端口和请求细节写入公开验证输出。"""


class SentinelServer(ThreadingHTTPServer):
    """为类型检查补充 ledger 字段。"""

    ledger: RequestLedger

    def get_request(self) -> tuple[Any, Any]:
        request = super().get_request()
        self.ledger.record_connection()
        return request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    return parser.parse_args()


def run_cli(
    cli_path: Path,
    endpoint: str,
    timeout_value: str | None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "DORMICE_ENDPOINT": endpoint,
            "DORMICE_API_TOKEN": "oracle-token",
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
        }
    )
    command = [
        "node",
        str(cli_path),
        "sandbox",
        "exec",
        "oracle-timeout",
        "echo oracle",
    ]
    if timeout_value is not None:
        command.extend(["--timeout", timeout_value])
    return subprocess.run(
        command,
        cwd=cli_path.parents[3],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )


def normalize_stderr(value: str) -> tuple[str, list[str]]:
    without_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    normalized = without_ansi.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized, [line for line in normalized.split("\n") if line.strip()]


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    cli_path = repo / "packages" / "cli" / "dist" / "main.js"
    if not cli_path.is_file():
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "cli_build_missing",
                },
                ensure_ascii=False,
            )
        )
        return 2

    ledger = RequestLedger()
    server = SentinelServer(("127.0.0.1", 0), SentinelHandler)
    server.ledger = ledger
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"

    invalid_results: list[dict[str, Any]] = []
    valid_results: list[dict[str, Any]] = []
    try:
        for value in INVALID_VALUES:
            before_connections, before_requests = ledger.snapshot()
            result = run_cli(cli_path, endpoint, value)
            after_connections, after_requests = ledger.snapshot()
            stderr, error_lines = normalize_stderr(result.stderr)
            stderr_lower = stderr.lower()
            invalid_results.append(
                {
                    "value": value,
                    "exit_nonzero": result.returncode != 0,
                    "stdout_empty": result.stdout == "",
                    "accepted_connections": (
                        after_connections - before_connections
                    ),
                    "request_count": len(after_requests) - len(before_requests),
                    "single_error_line": len(error_lines) == 1,
                    "mentions_timeout": (
                        "--timeout" in stderr_lower or "seconds" in stderr_lower
                    ),
                    "internal_error_absent": not any(
                        fragment in stderr_lower
                        for fragment in FORBIDDEN_ERROR_FRAGMENTS
                    ),
                    "environment_error_absent": (
                        "dormice_endpoint" not in stderr_lower
                        and "dormice_api_token" not in stderr_lower
                    ),
                }
            )

        for value, expected_timeout in VALID_VALUES.items():
            before_connections, before_requests = ledger.snapshot()
            result = run_cli(cli_path, endpoint, value)
            after_connections, after_requests = ledger.snapshot()
            new_requests = after_requests[len(before_requests) :]
            request = new_requests[0] if len(new_requests) == 1 else {}
            body = request.get("body") if isinstance(request, dict) else None
            valid_results.append(
                {
                    "value": value,
                    "exit_zero": result.returncode == 0,
                    "stdout_exact": result.stdout == "oracle-ok\n",
                    "stderr_empty": result.stderr == "",
                    "accepted_connections": (
                        after_connections - before_connections
                    ),
                    "request_count": len(new_requests),
                    "request_path_ok": request.get("path") == "/execCommand",
                    "authorization_ok": (
                        request.get("authorization") == "Bearer oracle-token"
                    ),
                    "timeout_matches": (
                        isinstance(body, dict)
                        and body.get("timeoutSeconds") == expected_timeout
                    ),
                    "body_exact": body
                    == {
                        "name": "oracle-timeout",
                        "command": "echo oracle",
                        "timeoutSeconds": expected_timeout,
                    },
                }
            )

        before_connections, before_requests = ledger.snapshot()
        default_result = run_cli(cli_path, endpoint, None)
        after_connections, after_requests = ledger.snapshot()
        default_new_requests = after_requests[len(before_requests) :]
        default_request = (
            default_new_requests[0] if len(default_new_requests) == 1 else {}
        )
        default_body = (
            default_request.get("body")
            if isinstance(default_request, dict)
            else None
        )
        default_result_record = {
            "exit_zero": default_result.returncode == 0,
            "stdout_exact": default_result.stdout == "oracle-ok\n",
            "stderr_empty": default_result.stderr == "",
            "accepted_connections": after_connections - before_connections,
            "request_count": len(default_new_requests),
            "request_path_ok": default_request.get("path") == "/execCommand",
            "timeout_is_sdk_default": (
                isinstance(default_body, dict)
                and default_body.get("timeoutSeconds") == 300
            ),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    invalid_passed = all(
        item["exit_nonzero"]
        and item["stdout_empty"]
        and item["accepted_connections"] == 0
        and item["request_count"] == 0
        and item["single_error_line"]
        and item["mentions_timeout"]
        and item["internal_error_absent"]
        and item["environment_error_absent"]
        for item in invalid_results
    )
    valid_passed = all(
        item["exit_zero"]
        and item["stdout_exact"]
        and item["stderr_empty"]
        and item["accepted_connections"] == 1
        and item["request_count"] == 1
        and item["request_path_ok"]
        and item["authorization_ok"]
        and item["timeout_matches"]
        and item["body_exact"]
        for item in valid_results
    )
    default_passed = all(
        (
            default_result_record["exit_zero"],
            default_result_record["stdout_exact"],
            default_result_record["stderr_empty"],
            default_result_record["accepted_connections"] == 1,
            default_result_record["request_count"] == 1,
            default_result_record["request_path_ok"],
            default_result_record["timeout_is_sdk_default"],
        )
    )
    passed = invalid_passed and valid_passed and default_passed
    total_connections, all_requests = ledger.snapshot()
    print(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "invalid_results": invalid_results,
                "valid_results": valid_results,
                "default_result": default_result_record,
                "total_connections": total_connections,
                "total_requests": len(all_requests),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "cli_timeout",
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)
    except Exception as exc:  # pragma: no cover - 只用于保留可诊断失败
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)
