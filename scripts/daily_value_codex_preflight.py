from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REQUIRED_DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "fast_mode",
    "goals",
    "hooks",
    "in_app_browser",
    "memories",
    "multi_agent",
    "plugins",
    "standalone_web_search",
)
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
SANDBOX_MODES = ("workspace-write", "read-only")
PROFILE_PATTERN = re.compile(r"[A-Za-z0-9._-]+\Z")


def inspect_codex_profile(
    codex_home: Path,
    model: str,
    profile_name: str,
) -> dict[str, Any]:
    config_path = codex_home / "config.toml"
    if not config_path.is_file():
        raise ValueError("CODEX_HOME 缺少 config.toml")
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    provider_name = config.get("model_provider")
    providers = config.get("model_providers")
    if not isinstance(provider_name, str) or not provider_name.strip():
        raise ValueError("Codex 配置没有固定 model_provider")
    if not isinstance(providers, dict) or not isinstance(providers.get(provider_name), dict):
        raise ValueError("model_provider 没有对应的 provider 配置")

    provider = providers[provider_name]
    base_url = provider.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("Provider 配置缺少 base_url")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Provider base_url 必须是有效的 HTTP(S) 地址")
    wire_api = provider.get("wire_api")
    if wire_api != "responses":
        raise ValueError("日用价值实验只接受 wire_api=responses")

    catalog_path = codex_home / "custom-models.json"
    if not catalog_path.is_file():
        raise ValueError("CODEX_HOME 缺少 custom-models.json")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not _catalog_contains_model(catalog, model):
        raise ValueError(f"自定义模型目录没有冻结模型：{model}")

    profile_path, profile = _load_execution_profile(codex_home, profile_name)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    profile_identity = {
        "model": model,
        "model_provider": provider_name,
        "provider_origin": origin,
        "wire_api": wire_api,
        "execution_profile": profile_name,
        "execution_profile_sha256": _sha256_bytes(profile_path.read_bytes()),
    }
    return {
        "model": model,
        "model_provider": provider_name,
        "provider_origin_sha256": _sha256_text(origin),
        "wire_api": wire_api,
        "execution_profile": profile_name,
        "execution_profile_sha256": _sha256_bytes(profile_path.read_bytes()),
        "execution_profile_features": profile["features"],
        "profile_fingerprint": _fingerprint(profile_identity),
        "config_sha256": _sha256_bytes(config_path.read_bytes()),
        "model_catalog_sha256": _sha256_bytes(catalog_path.read_bytes()),
        "global_agents_present": any(
            codex_home.joinpath(name).is_file()
            for name in ("AGENTS.override.md", "AGENTS.md")
        ),
    }


def build_exec_args(
    model: str,
    reasoning_effort: str,
    sandbox: str,
    profile_name: str,
) -> list[str]:
    if reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"不支持的 reasoning effort：{reasoning_effort}")
    if sandbox not in SANDBOX_MODES:
        raise ValueError(f"不支持的 sandbox：{sandbox}")
    if PROFILE_PATTERN.fullmatch(profile_name) is None:
        raise ValueError("Codex profile 名称非法")
    args = [
        "exec",
        "--cd",
        "<workspace>",
        "--model",
        model,
        "--sandbox",
        sandbox,
        "--profile",
        profile_name,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--ephemeral",
    ]
    validate_exec_args(args)
    return args


def validate_exec_args(args: list[str]) -> None:
    if "--ignore-user-config" in args:
        raise ValueError("禁止使用 --ignore-user-config：它会移除冻结的 Provider 路由")
    if "--dangerously-bypass-approvals-and-sandbox" in args:
        raise ValueError("禁止绕过审批与 sandbox")
    for required in ("--cd", "--profile", "--ephemeral"):
        if required not in args:
            raise ValueError(f"Codex 执行参数缺少 {required}")


def build_preflight(
    codex_home: Path,
    *,
    model: str,
    reasoning_effort: str,
    sandbox: str,
    profile_name: str,
    expected_profile_fingerprint: str | None = None,
) -> dict[str, Any]:
    profile = inspect_codex_profile(codex_home, model, profile_name)
    if (
        expected_profile_fingerprint is not None
        and profile["profile_fingerprint"] != expected_profile_fingerprint
    ):
        raise ValueError("当前 Provider profile 与冻结 fingerprint 不一致")
    exec_args = build_exec_args(model, reasoning_effort, sandbox, profile_name)
    return {
        "schema_version": 1,
        "status": "ready",
        "profile": profile,
        "reasoning_effort": reasoning_effort,
        "sandbox": sandbox,
        "disabled_features": list(REQUIRED_DISABLED_FEATURES),
        "exec_args": exec_args,
        "exec_args_fingerprint": _fingerprint(exec_args),
        "provider_request_performed": False,
        "notes": [
            "该预检只证明 Provider 路由未被配置隔离移除，不证明凭据或 Provider 当前可用。",
            "Native 与 Vega 必须使用相同 profile_fingerprint。",
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线核对日用价值实验的 Codex 执行配置。")
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", "")),
        help="Codex 配置目录，默认读取 CODEX_HOME。",
    )
    parser.add_argument("--model", required=True, help="冻结模型。")
    parser.add_argument("--profile", required=True, help="Native 与 Vega 共用的 Codex profile。")
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        required=True,
        help="冻结 reasoning effort。",
    )
    parser.add_argument(
        "--sandbox",
        choices=SANDBOX_MODES,
        required=True,
        help="Worker 使用 workspace-write，Reviewer 使用 read-only。",
    )
    parser.add_argument("--expected-profile-fingerprint", help="要求匹配的 Provider profile。")
    parser.add_argument("--output", type=Path, help="可选的本地 JSON 输出路径。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not str(args.codex_home):
            raise ValueError("未设置 CODEX_HOME")
        payload = build_preflight(
            args.codex_home,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            sandbox=args.sandbox,
            profile_name=args.profile,
            expected_profile_fingerprint=args.expected_profile_fingerprint,
        )
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"Codex 执行配置预检失败：{exc}", file=sys.stderr)
        return 1


def _catalog_contains_model(payload: Any, model: str) -> bool:
    if isinstance(payload, dict):
        if payload.get("slug") == model:
            return True
        return any(_catalog_contains_model(value, model) for value in payload.values())
    if isinstance(payload, list):
        return any(_catalog_contains_model(value, model) for value in payload)
    return False


def _load_execution_profile(
    codex_home: Path,
    profile_name: str,
) -> tuple[Path, dict[str, Any]]:
    if PROFILE_PATTERN.fullmatch(profile_name) is None:
        raise ValueError("Codex profile 名称非法")
    profile_path = codex_home / f"{profile_name}.config.toml"
    if not profile_path.is_file():
        raise ValueError(f"CODEX_HOME 缺少执行 profile：{profile_name}")
    profile = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    features = profile.get("features")
    if not isinstance(features, dict):
        raise ValueError("执行 profile 缺少 features")
    invalid_features = sorted(
        feature
        for feature in REQUIRED_DISABLED_FEATURES
        if features.get(feature) is not False
    )
    if invalid_features:
        raise ValueError(f"执行 profile 未关闭 feature：{invalid_features}")
    if profile.get("project_root_markers") != []:
        raise ValueError("执行 profile 必须设置 project_root_markers=[]")
    if profile.get("approval_policy") != "never":
        raise ValueError("执行 profile 必须设置 approval_policy=never")
    sandbox = profile.get("sandbox_workspace_write")
    if not isinstance(sandbox, dict) or sandbox.get("network_access") is not False:
        raise ValueError("执行 profile 必须关闭 workspace-write 网络")
    return profile_path, profile


def _fingerprint(payload: Any) -> str:
    normalized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(normalized)


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
