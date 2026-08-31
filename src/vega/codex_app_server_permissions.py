from __future__ import annotations

from .provider_session import ProviderSandbox

_SANDBOX_TYPES: dict[str, ProviderSandbox] = {
    "readOnly": "read-only",
    "workspaceWrite": "workspace-write",
    "dangerFullAccess": "danger-full-access",
    "externalSandbox": "external",
}


def require_thread_permissions(
    result: dict[str, object],
    *,
    requested_sandbox: str,
) -> tuple[ProviderSandbox, str]:
    """核对 App Server 实际生效权限，避免只相信请求参数。"""

    sandbox = result.get("sandbox")
    sandbox_type = sandbox.get("type") if isinstance(sandbox, dict) else None
    observed_sandbox = _SANDBOX_TYPES.get(str(sandbox_type))
    approval_policy = _approval_policy_name(result.get("approvalPolicy"))
    if observed_sandbox != requested_sandbox:
        raise RuntimeError(
            "App Server 实际 sandbox 与请求不一致："
            f"requested={requested_sandbox}，observed={observed_sandbox or 'unknown'}"
        )
    expected_approval = (
        "never" if requested_sandbox == "read-only" else "on-request"
    )
    if approval_policy != expected_approval:
        raise RuntimeError(
            "App Server 实际 approvalPolicy 与请求不一致："
            f"requested={expected_approval}，observed={approval_policy}"
        )
    return observed_sandbox, approval_policy


def _approval_policy_name(value: object) -> str:
    if isinstance(value, str) and value in {"untrusted", "on-request", "never"}:
        return value
    if isinstance(value, dict) and isinstance(value.get("granular"), dict):
        return "granular"
    return "unknown"
