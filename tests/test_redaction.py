from __future__ import annotations

from types import SimpleNamespace

import pytest

from vega.redaction import (
    REDACTION_TEXT,
    assert_not_sensitive_path,
    filter_sensitive_memory_entries,
    is_sensitive_path,
    redact_text,
    redact_value,
    sensitive_path_reason,
)


FAKE_SECRET = "sk-review-fake-secret-123456"


def test_redact_text_removes_common_secret_shapes() -> None:
    text = "\n".join(
        [
            f"api_key={FAKE_SECRET}",
            f"Authorization: Bearer {FAKE_SECRET}",
            f"url=https://example.test/callback?token={FAKE_SECRET}&safe=1",
            f"password: {FAKE_SECRET}",
            f"plain mention {FAKE_SECRET}",
        ]
    )

    redacted = redact_text(text)

    assert FAKE_SECRET not in redacted
    assert redacted.count(REDACTION_TEXT) >= 5
    assert "Bearer [REDACTED]" in redacted
    assert "token=[REDACTED]" in redacted
    assert "safe=1" in redacted


def test_redact_text_removes_quoted_secret_phrases_and_private_key_blocks() -> None:
    text = "\n".join(
        [
            '"password": "secret phrase with spaces"',
            "client_secret='another secret phrase'",
            "-----BEGIN PRIVATE KEY-----",
            "ZmFrZS1wcml2YXRlLWtleQ==",
            "-----END PRIVATE KEY-----",
        ]
    )

    redacted = redact_text(text)

    assert "secret phrase with spaces" not in redacted
    assert "another secret phrase" not in redacted
    assert "BEGIN PRIVATE KEY" not in redacted
    assert redacted.count(REDACTION_TEXT) == 3


def test_redact_text_removes_netrc_http_docker_and_package_manager_credentials() -> None:
    npm_token = "npm_" + "a" * 24
    pypi_token = "pypi-" + "b" * 24
    text = "\n".join(
        [
            "machine registry.example.test login build-bot password netrc-password",
            "index = https://user:http-password@example.test/simple",
            '{"auth": "ZG9ja2VyLXNlY3JldA==", "identitytoken": "docker-identity"}',
            f"//registry.npmjs.org/:_authToken={npm_token}",
            f"password = {pypi_token}",
        ]
    )

    redacted = redact_text(text)

    for secret in ("netrc-password", "http-password", "ZG9ja2VyLXNlY3JldA==", "docker-identity"):
        assert secret not in redacted
    assert npm_token not in redacted
    assert pypi_token not in redacted
    assert "machine registry.example.test login build-bot password [REDACTED]" in redacted
    assert "https://user:[REDACTED]@example.test/simple" in redacted
    assert '"auth": "[REDACTED]"' in redacted
    assert '"identitytoken": "[REDACTED]"' in redacted
    assert "//registry.npmjs.org/:_authToken=[REDACTED]" in redacted


def test_redact_text_preserves_source_expressions_with_credential_like_names() -> None:
    text = "\n".join(
        [
            "password: str",
            "password = candidate",
            "password = None",
            "password = False",
            "password = 0",
            'password = get_secret("name")',
            'PASSWORD = get_secret("name")',
            'PASSWORD = os.getenv("PASSWORD")',
            'PASSWORD = os.environ["PASSWORD"]',
            "password = compute_password(user)",
            "client_secret = settings.client_secret",
            "authToken = settings.auth_token",
            "database_url = build_database_url(settings)",
        ]
    )

    assert redact_text(text) == text


def test_redact_text_still_removes_explicit_literal_secrets() -> None:
    text = "\n".join(
        [
            'password = "literal password"',
            'PASSWORD = "uppercase literal password"',
            "client_secret = 'literal client secret'",
            "Authorization: Bearer bearer-token-value",
            "url=https://example.test/callback?token=query-token-value",
        ]
    )

    redacted = redact_text(text)

    assert "literal password" not in redacted
    assert "uppercase literal password" not in redacted
    assert "literal client secret" not in redacted
    assert "bearer-token-value" not in redacted
    assert "query-token-value" not in redacted
    assert "Bearer [REDACTED]" in redacted
    assert "token=[REDACTED]" in redacted


def test_redact_text_is_idempotent_for_unquoted_placeholders() -> None:
    text = "password=[REDACTED]"

    assert redact_text(text) == text


def test_redact_value_recurses_without_mutating_original_payload() -> None:
    payload = {
        "message": f"runner failed with {FAKE_SECRET}",
        "authorization": f"Bearer {FAKE_SECRET}",
        "nested": [
            {"password": FAKE_SECRET, "note": "keep me"},
            (f"url=https://example.test/?secret={FAKE_SECRET}",),
        ],
        f"artifact-{FAKE_SECRET}": "safe value",
        "none_secret": None,
    }

    redacted = redact_value(payload)

    assert payload["authorization"] == f"Bearer {FAKE_SECRET}"
    assert redacted["message"] == "runner failed with [REDACTED]"
    assert redacted["authorization"] == REDACTION_TEXT
    assert redacted["nested"][0]["password"] == REDACTION_TEXT
    assert redacted["nested"][0]["note"] == "keep me"
    assert redacted["nested"][1] == ("url=https://example.test/?secret=[REDACTED]",)
    assert redacted["none_secret"] is None
    assert all(FAKE_SECRET not in str(key) for key in redacted)
    assert FAKE_SECRET not in repr(redacted)


@pytest.mark.parametrize(
    "path, reason",
    [
        (".env", "environment_file"),
        ("config/.env.local", "environment_file"),
        ("C:/repo/.ssh/id_rsa", "private_key_name"),  # repo-path-policy: allow-test-fixture
        ("/etc/ssh/ssh_host_ed25519_key", "private_key_name"),
        ("certs/service.pem", "sensitive_key_suffix"),
        ("certs/service.key", "sensitive_key_suffix"),
        ("config/client_secret.json", "credential_file_name"),
        (".netrc", "credential_file_name"),
        ("home/user/.git-credentials", "credential_file_name"),
        ("project/.npmrc", "credential_file_name"),
        ("project/.pypirc", "credential_file_name"),
        ("~/.docker/config.json", "docker_credential_config"),
        (
            "C:/Users/test/.docker/config.json",  # repo-path-policy: allow-test-fixture
            "docker_credential_config",
        ),
        ("README.md:.env", "windows_alternate_data_stream"),
        ("C:outside.txt", "windows_drive_relative_path"),
    ],
)
def test_sensitive_path_detection_blocks_secret_targets(path: str, reason: str) -> None:
    assert is_sensitive_path(path)
    assert sensitive_path_reason(path) == reason
    with pytest.raises(ValueError, match=rf"拒绝读取敏感路径（{reason}）") as exc_info:
        assert_not_sensitive_path(path)
    assert path not in str(exc_info.value)


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/env-setup.md",
        "src/vega/redaction.py",
        "docker/config.json",
        "tests/fixtures/public.json",
    ],
)
def test_sensitive_path_detection_allows_normal_project_files(path: str) -> None:
    assert not is_sensitive_path(path)
    assert sensitive_path_reason(path) is None
    assert_not_sensitive_path(path)


def test_filter_sensitive_memory_entries_defaults_to_excluding_sensitive_entries() -> None:
    entries = [
        {"title": "public", "sensitivity": "public"},
        SimpleNamespace(title="internal", sensitivity="internal"),
        SimpleNamespace(title="sensitive", sensitivity="sensitive"),
        {"title": "implicit internal"},
    ]

    filtered = filter_sensitive_memory_entries(entries)

    assert [entry["title"] if isinstance(entry, dict) else entry.title for entry in filtered] == [
        "public",
        "internal",
        "implicit internal",
    ]
    assert filter_sensitive_memory_entries(entries, include_sensitive=True) == entries
