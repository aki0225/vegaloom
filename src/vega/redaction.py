from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar


REDACTION_TEXT = "[REDACTED]"

_SENSITIVE_TEXT_FIELD_NAMES = (
    "api_key",
    "api-key",
    "apikey",
    "openai_api_key",
    "vega_api_key",
    "x-api-key",
    "authorization",
    "access_token",
    "access-token",
    "refresh_token",
    "refresh-token",
    "id_token",
    "id-token",
    "auth_token",
    "auth-token",
    "_authToken",
    "authToken",
    "npmAuthToken",
    "npm_auth_token",
    "pypiToken",
    "pypi_token",
    "token",
    "identitytoken",
    "identityToken",
    "identity_token",
    "identity-token",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "database_url",
    "database_uri",
    "db_url",
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "client-secret",
    "private_key",
    "private-key",
)

_ENV_FILE_NAMES = frozenset({".env", ".envrc"})
_PRIVATE_KEY_NAMES = frozenset(
    {
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "private-key",
        "private_key",
    }
)
_CREDENTIAL_FILE_NAMES = frozenset(
    {
        "client_secret.json",
        "credentials.json",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "secret.json",
        "secrets.json",
        "service-account.json",
        "service_account.json",
    }
)
_SENSITIVE_SUFFIXES = (
    ".key",
    ".pem",
    ".p8",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".der",
)

T = TypeVar("T")


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


_SENSITIVE_NORMALIZED_KEYS = frozenset(_normalize_key(name) for name in _SENSITIVE_TEXT_FIELD_NAMES)
_SENSITIVE_FIELD_PATTERN = "|".join(
    re.escape(name) for name in sorted(_SENSITIVE_TEXT_FIELD_NAMES, key=len, reverse=True)
)
_QUOTED_KEY_VALUE_PATTERN = re.compile(
    rf"(?i)(?P<prefix>[\"']?(?:{_SENSITIVE_FIELD_PATTERN})[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])"
    r"(?P<value>.*?)"
    r"(?P=quote)"
)
_UNQUOTED_KEY_VALUE_PATTERN = re.compile(
    rf"(?i)(?P<prefix>[\"']?(?:{_SENSITIVE_FIELD_PATTERN})[\"']?\s*[:=]\s*)"
    r"(?P<value>(?![\s\"'])[^\r\n,}&#]+)"
)
_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"(?im)(?P<prefix>\bauthorization\s*:\s*)"
    r"(?:(?P<scheme>Bearer|Basic)\s+)?"
    r"[^\r\n]+"
)
_NETRC_PASSWORD_PATTERN = re.compile(
    r"(?im)(?P<prefix>(?<![\w-])password[ \t]+)(?P<value>(?![:=])[^\s#]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer)\s+([A-Za-z0-9._~+/=-]{8,})")
_URL_QUERY_PATTERN = re.compile(
    rf"(?i)([?&](?:{_SENSITIVE_FIELD_PATTERN}|key)=)([^&#\s]+)"
)
_HTTP_USERINFO_PATTERN = re.compile(
    r"(?i)(?P<prefix>\bhttps?://[^:/@\s]+:)(?P<password>[^@\s/?#]+)(?=@)"
)
_DOCKER_AUTH_FIELD_PATTERN = re.compile(
    r"(?i)(?P<prefix>[\"'](?:auth|identitytoken|identity_token|identity-token)[\"']\s*:\s*)"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
)
_DATABASE_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?P<prefix>\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|"
    r"redis|rediss|amqp|amqps)://[^:/@\s]+:)"
    r"(?P<password>[^@\s/]+)"
    r"(?=@)"
)
_ENV_REFERENCE_PATTERN = re.compile(
    r"(?:\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\}|"
    r"%[A-Za-z_][A-Za-z0-9_]*%)"
)
_PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----"
    r".*?"
    r"-----END (?P=label)-----",
    flags=re.IGNORECASE | re.DOTALL,
)
_DIRECT_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{10,}\b"),
)
_SENSITIVE_SEARCH_EXCLUDE_GLOBS = tuple(
    dict.fromkeys(
        [
            *(f"!**/{name}" for name in sorted(_ENV_FILE_NAMES)),
            "!**/.env.*",
            *(f"!**/{name}" for name in sorted(_PRIVATE_KEY_NAMES)),
            "!**/ssh_host_*_key",
            *(f"!**/{name}" for name in sorted(_CREDENTIAL_FILE_NAMES)),
            "!**/.docker/config.json",
            *(f"!**/*{suffix}" for suffix in _SENSITIVE_SUFFIXES),
        ]
    )
)


def redact_text(text: str, *, placeholder: str = REDACTION_TEXT) -> str:
    """脱敏单段文本，适合写入 state、trace、prompt、report 等出站边界前调用。"""
    redacted = _PEM_PRIVATE_KEY_PATTERN.sub(placeholder, text)
    redacted = _QUOTED_KEY_VALUE_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}{placeholder}{match.group('quote')}",
        redacted,
    )
    redacted = _UNQUOTED_KEY_VALUE_PATTERN.sub(
        lambda match: _replace_unquoted_key_value(match, placeholder),
        redacted,
    )
    redacted = _NETRC_PASSWORD_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{placeholder}",
        redacted,
    )
    redacted = _AUTHORIZATION_HEADER_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix')}"
            f"{match.group('scheme') + ' ' if match.group('scheme') else ''}"
            f"{placeholder}"
        ),
        redacted,
    )
    redacted = _BEARER_PATTERN.sub(lambda match: f"{match.group(1)} {placeholder}", redacted)
    redacted = _URL_QUERY_PATTERN.sub(lambda match: f"{match.group(1)}{placeholder}", redacted)
    redacted = _HTTP_USERINFO_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{placeholder}",
        redacted,
    )
    redacted = _DOCKER_AUTH_FIELD_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}{placeholder}{match.group('quote')}"
        ),
        redacted,
    )
    redacted = _DATABASE_CREDENTIAL_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{placeholder}",
        redacted,
    )
    for pattern in _DIRECT_SECRET_PATTERNS:
        redacted = pattern.sub(placeholder, redacted)
    return redacted


def redact_value(value: Any, *, placeholder: str = REDACTION_TEXT) -> Any:
    """递归脱敏 JSON-like payload；返回新对象，不修改调用方传入的数据。"""
    if isinstance(value, str):
        return redact_text(value, placeholder=placeholder)
    if isinstance(value, Mapping):
        return {
            _redact_mapping_key(key, placeholder): _redact_sensitive_field_value(item, placeholder)
            if is_sensitive_key(key)
            else redact_value(item, placeholder=placeholder)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, placeholder=placeholder) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, placeholder=placeholder) for item in value)
    if isinstance(value, set):
        return {redact_value(item, placeholder=placeholder) for item in value}
    return value


def redact_payload(payload: Any, *, placeholder: str = REDACTION_TEXT) -> Any:
    """兼容别名；第二轮集成优先使用 redact_value。"""
    return redact_value(payload, placeholder=placeholder)


def is_sensitive_key(key: object) -> bool:
    """判断 JSON-like 字段名是否应整体替换为占位符。"""
    return _normalize_key(str(key)) in _SENSITIVE_NORMALIZED_KEYS


def sensitive_path_reason(path: str | Path) -> str | None:
    """返回敏感目标路径的拒绝原因；仅基于路径字符串判断，不读取文件。"""
    special_path_reason = _special_path_reason(path)
    if special_path_reason:
        return special_path_reason

    parts = _path_parts(path)
    if any(part in _ENV_FILE_NAMES or part.startswith(".env.") for part in parts):
        return "environment_file"

    name = parts[-1] if parts else ""
    if name in _PRIVATE_KEY_NAMES or (name.startswith("ssh_host_") and name.endswith("_key")):
        return "private_key_name"
    if name in _CREDENTIAL_FILE_NAMES:
        return "credential_file_name"
    if len(parts) >= 2 and parts[-2:] == [".docker", "config.json"]:
        return "docker_credential_config"
    if name.endswith(_SENSITIVE_SUFFIXES):
        return "sensitive_key_suffix"
    return None


def is_sensitive_path(path: str | Path) -> bool:
    """判断目标路径是否应拒绝读取。"""
    return sensitive_path_reason(path) is not None


def assert_not_sensitive_path(path: str | Path) -> None:
    """供读取边界调用：敏感路径直接抛出结构化原因，调用方负责转为 ToolResult。"""
    reason = sensitive_path_reason(path)
    if reason:
        raise ValueError(f"拒绝读取敏感路径（{reason}）")


def sensitive_search_exclude_globs() -> tuple[str, ...]:
    """返回供搜索工具使用的排除 glob，避免先读取再过滤已知敏感文件。"""
    return _SENSITIVE_SEARCH_EXCLUDE_GLOBS


def write_redacted_text(path: Path, text: str) -> None:
    """统一文本 artifact 写盘边界。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact_text(text), encoding="utf-8")


def write_redacted_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    """统一 JSON artifact 写盘边界。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redact_value(payload), ensure_ascii=False, indent=indent) + "\n",
        encoding="utf-8",
    )


def write_redacted_json_once(path: Path, payload: Any, *, indent: int = 2) -> None:
    """独占创建 JSON artifact，避免重试覆盖既有证据。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(redact_value(payload), ensure_ascii=False, indent=indent) + "\n"
        )


def append_redacted_jsonl(path: Path, payload: Any) -> None:
    """统一 JSONL artifact 追加边界。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(redact_value(payload), ensure_ascii=False) + "\n")


def should_include_memory_entry(entry: object, *, include_sensitive: bool = False) -> bool:
    """accepted memory 注入前的默认过滤：sensitivity=sensitive 不自动进入上下文。"""
    return include_sensitive or _entry_sensitivity(entry) != "sensitive"


def filter_memory_entries(
    entries: Iterable[T],
    *,
    include_sensitive: bool = False,
) -> list[T]:
    """过滤 memory entries，默认排除 sensitivity=sensitive 的 accepted memory。"""
    return [
        entry
        for entry in entries
        if should_include_memory_entry(entry, include_sensitive=include_sensitive)
    ]


def filter_sensitive_memory_entries(
    entries: Iterable[T],
    *,
    include_sensitive: bool = False,
) -> list[T]:
    """集成用稳定接口：默认过滤 sensitivity=sensitive 的 memory entries。"""
    return filter_memory_entries(entries, include_sensitive=include_sensitive)


def _redact_sensitive_field_value(value: Any, placeholder: str) -> Any:
    if value is None:
        return None
    return placeholder


def _redact_mapping_key(key: object, placeholder: str) -> object:
    if isinstance(key, str):
        return redact_text(key, placeholder=placeholder)
    return key


def _replace_unquoted_key_value(match: re.Match[str], placeholder: str) -> str:
    prefix = match.group("prefix")
    value = match.group("value")
    if value.strip() == placeholder:
        return match.group(0)
    if "authorization" in prefix.lower():
        scheme = value.strip().partition(" ")[0].lower()
        if scheme in {"bearer", "basic"}:
            return match.group(0)
    if _looks_like_source_binding(prefix, value):
        return match.group(0)
    return f"{prefix}{placeholder}"


def _looks_like_source_binding(prefix: str, value: str) -> bool:
    candidate = f"{prefix}{value}".strip()
    if _ENV_REFERENCE_PATTERN.fullmatch(value.strip()):
        return True
    try:
        module = ast.parse(candidate, mode="exec")
    except SyntaxError:
        return False
    if len(module.body) != 1:
        return False
    statement = module.body[0]
    if isinstance(statement, ast.Assign):
        target = statement.targets[0] if len(statement.targets) == 1 else None
        return (
            isinstance(target, ast.Name)
            and _is_safe_source_value(statement.value)
            and (
                not target.id.isupper()
                or not isinstance(statement.value, ast.Name)
            )
        )
    if isinstance(statement, ast.AnnAssign):
        if not isinstance(statement.target, ast.Name):
            return False
        if statement.value is None:
            return _looks_like_type_annotation(statement.annotation)
        return _is_safe_source_value(statement.value)
    return False


def _is_safe_source_value(value: ast.expr) -> bool:
    return not (
        isinstance(value, ast.Constant)
        and isinstance(value.value, (str, bytes))
    )


def _looks_like_type_annotation(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Name):
        return annotation.id in {
            "Any",
            "SecretStr",
            "bool",
            "bytes",
            "float",
            "int",
            "object",
            "str",
        }
    if isinstance(annotation, (ast.Attribute, ast.Subscript)):
        return True
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _looks_like_type_annotation(annotation.left) and _looks_like_type_annotation(
            annotation.right
        )
    if isinstance(annotation, ast.Constant):
        return annotation.value is None
    return False


def _entry_sensitivity(entry: object) -> str:
    if isinstance(entry, str):
        return entry.lower()
    if isinstance(entry, Mapping):
        return str(entry.get("sensitivity", "internal")).lower()
    return str(getattr(entry, "sensitivity", "internal")).lower()


def _path_parts(path: str | Path) -> list[str]:
    normalized = str(path).replace("\\", "/").lower()
    return [part for part in normalized.split("/") if part]


def _special_path_reason(path: str | Path) -> str | None:
    normalized = str(path).replace("\\", "/")
    remainder = normalized
    if re.match(r"^[A-Za-z]:", normalized):
        remainder = normalized[2:]
        if remainder and not remainder.startswith("/"):
            return "windows_drive_relative_path"
    if ":" in remainder:
        return "windows_alternate_data_stream"
    return None


__all__ = [
    "REDACTION_TEXT",
    "append_redacted_jsonl",
    "assert_not_sensitive_path",
    "filter_memory_entries",
    "filter_sensitive_memory_entries",
    "is_sensitive_key",
    "is_sensitive_path",
    "redact_payload",
    "redact_text",
    "redact_value",
    "sensitive_search_exclude_globs",
    "sensitive_path_reason",
    "should_include_memory_entry",
    "write_redacted_json",
    "write_redacted_text",
]
