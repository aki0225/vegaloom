from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from ..project_config import (
    VERIFICATION_TEMP_PLACEHOLDER,
    VERIFICATION_TEMP_ROOT,
    render_verification_command,
)
from ..redaction import write_redacted_json
from ..run_utils import resolve_run_dir


ASSURANCE_SCHEMA_VERSION = 1
MAX_ASSURANCE_INPUT_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 16 * 1024 * 1024

_STRICT_MODEL = ConfigDict(extra="forbid", strict=True)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitObjectId = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),
]
RunId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$"),
]
ClaimId = Annotated[
    str,
    StringConstraints(pattern=r"^C-[A-Z0-9][A-Z0-9._-]{0,99}$"),
]
ThreatId = Annotated[
    str,
    StringConstraints(pattern=r"^T-[A-Z0-9][A-Z0-9._-]{0,99}$"),
]
EvidenceId = Annotated[
    str,
    StringConstraints(pattern=r"^E-[A-Z0-9][A-Z0-9._-]{0,99}$"),
]
EvidenceKind = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9._-]*$", max_length=100),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
JsonScalar = str | int | float | bool | None
VerificationConclusion = Literal["verified", "failed", "unknown", "interrupted"]
AdequacyStatus = Literal[
    "sufficient_for_merge",
    "requires_staged_rollout",
    "insufficient",
    "human_required",
]
SourceKind = Literal[
    "user_requirement",
    "project_contract",
    "machine_policy",
    "public_contract",
    "test_oracle",
    "deterministic_detector",
    "llm_candidate",
]

_SOURCE_PREFIXES: dict[SourceKind, tuple[str, ...]] = {
    "user_requirement": ("task://",),
    "project_contract": ("file://", "policy://"),
    "machine_policy": ("policy://",),
    "public_contract": ("api://", "schema://", "file://"),
    "test_oracle": ("test://",),
    "deterministic_detector": ("detector://",),
    "llm_candidate": ("llm://",),
}
_TRIGGER_REFERENCE_PREFIXES = (
    "diff://",
    "file://",
    "policy://",
    "task://",
    "detector://",
)
_SNAPSHOT_ISSUES = {
    "head_sha": "snapshot_head_mismatch",
    "staged_diff_sha256": "snapshot_staged_diff_mismatch",
    "unstaged_diff_sha256": "snapshot_unstaged_diff_mismatch",
    "review_snapshot_id": "snapshot_review_id_mismatch",
    "project_policy_snapshot_sha256": "snapshot_project_policy_mismatch",
    "scope_policy_sha256": "snapshot_scope_policy_mismatch",
}


class AssuranceSnapshot(BaseModel):
    """一次 Assurance 判定必须绑定的完整工作区与策略快照。"""

    model_config = _STRICT_MODEL

    head_sha: GitObjectId
    staged_diff_sha256: Sha256
    unstaged_diff_sha256: Sha256
    review_snapshot_id: Sha256
    project_policy_snapshot_sha256: Sha256
    scope_policy_sha256: Sha256


class SourceReference(BaseModel):
    """Claim 或 Threat 的来源及可机器检查的引用。"""

    model_config = _STRICT_MODEL

    kind: SourceKind
    reference: NonEmptyText

    @model_validator(mode="after")
    def validate_reference_prefix(self) -> SourceReference:
        if not self.reference.startswith(_SOURCE_PREFIXES[self.kind]):
            allowed = ", ".join(_SOURCE_PREFIXES[self.kind])
            raise ValueError(f"{self.kind} 来源引用必须使用以下前缀：{allowed}")
        return self


class Claim(BaseModel):
    """一次变更需要保持或实现的可引用声明。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    id: ClaimId
    statement: NonEmptyText
    status: Literal["accepted", "candidate"]
    source: SourceReference

    @model_validator(mode="after")
    def keep_llm_claim_as_candidate(self) -> Claim:
        if self.source.kind == "llm_candidate" and self.status != "candidate":
            raise ValueError("LLM Claim 只能保持 candidate，不能直接 accepted")
        return self


class Threat(BaseModel):
    """可映射到最低证据组合的具体失败场景。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    id: ThreatId
    category: EvidenceKind
    source: SourceReference
    status: Literal["active", "candidate"]
    trigger: NonEmptyText
    affected_assets: list[ShortText] = Field(min_length=1, max_length=64)
    claim_refs: list[ClaimId] = Field(min_length=1, max_length=64)
    invariant: NonEmptyText
    failure_mode: EvidenceKind
    impact: ShortText
    exposure: ShortText
    blast_radius: ShortText
    reversibility: ShortText
    detectability: ShortText
    uncertainty: ShortText
    trigger_evidence: list[NonEmptyText] = Field(min_length=1, max_length=64)
    required_evidence: list[EvidenceKind] = Field(min_length=1, max_length=64)
    evidence_refs: list[EvidenceId] = Field(max_length=128)
    residual_risks: list[NonEmptyText] = Field(max_length=64)
    human_required: bool

    @field_validator("trigger_evidence")
    @classmethod
    def validate_trigger_references(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith(_TRIGGER_REFERENCE_PREFIXES):
                raise ValueError("trigger_evidence 必须使用受支持的结构化引用前缀")
        return values

    @model_validator(mode="after")
    def keep_llm_threat_as_candidate(self) -> Threat:
        if self.source.kind == "llm_candidate" and self.status != "candidate":
            raise ValueError("LLM Threat 只能保持 candidate，不能直接 active")
        return self


class EvidenceProducer(BaseModel):
    """生成结构化证据的工具及版本。"""

    model_config = _STRICT_MODEL

    runner: ShortText
    version: ShortText


class EvidenceOracle(BaseModel):
    """证据判定成功或失败所依据的 oracle。"""

    model_config = _STRICT_MODEL

    statement: NonEmptyText


class EvidenceExecutionResult(BaseModel):
    """证据命令的结构化执行结果。"""

    model_config = _STRICT_MODEL

    status: Literal["passed", "failed", "interrupted", "unknown"]
    exit_code: int | None
    duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_exit_code(self) -> EvidenceExecutionResult:
        if self.status == "passed" and self.exit_code != 0:
            raise ValueError("passed Evidence 的 exit_code 必须为 0")
        if self.status == "failed" and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("failed Evidence 必须有非零 exit_code")
        return self


class EvidenceArtifactRef(BaseModel):
    """指向当前 run/current iteration 结构化验证结果的哈希引用。"""

    model_config = _STRICT_MODEL

    artifact_type: Literal["verification_result"]
    run_id: RunId
    relative_path: NonEmptyText
    sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("artifact relative_path 必须使用 POSIX 分隔符")
        raw_parts = value.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise ValueError("artifact relative_path 不能包含空段、当前目录或上级目录")
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts:
            raise ValueError("artifact relative_path 必须是非空相对路径")
        if any(":" in part for part in path.parts):
            raise ValueError("artifact relative_path 不能包含盘符、URI scheme 或 NTFS ADS")
        return path.as_posix()


class EvidenceRecord(BaseModel):
    """绑定来源、快照、oracle、覆盖关系和 artifact 的结构化证据。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    id: EvidenceId
    kind: EvidenceKind
    producer: EvidenceProducer
    command: NonEmptyText
    environment: dict[ShortText, JsonScalar] = Field(min_length=1, max_length=64)
    run_id: RunId
    iteration: int = Field(ge=1)
    snapshot: AssuranceSnapshot
    input: dict[ShortText, JsonScalar] = Field(min_length=1, max_length=64)
    oracle: EvidenceOracle
    result: EvidenceExecutionResult
    covers: list[ThreatId] = Field(min_length=1, max_length=64)
    artifacts: list[EvidenceArtifactRef] = Field(min_length=1, max_length=4)
    limitations: list[NonEmptyText] = Field(max_length=64)


class AssuranceBundle(BaseModel):
    """Stage 1 的版本化输入 Bundle，不接受调用方自报 AdequacyResult。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    run_id: RunId
    iteration: int = Field(ge=1)
    snapshot: AssuranceSnapshot
    verification_conclusion: VerificationConclusion
    claims: list[Claim] = Field(max_length=128)
    threats: list[Threat] = Field(max_length=128)
    evidence: list[EvidenceRecord] = Field(max_length=128)


class AssuranceContext(BaseModel):
    """由可信调用方独立冻结的来源集合和工作区期望。"""

    model_config = _STRICT_MODEL

    run_id: RunId
    iteration: int = Field(ge=1)
    snapshot: AssuranceSnapshot
    accepted_claims_sha256: Sha256
    active_threats_sha256: Sha256
    allowed_evidence_contracts: list[Sha256] = Field(max_length=128)


class ThreatAdequacy(BaseModel):
    """单个 active Threat 的最低证据满足情况。"""

    model_config = _STRICT_MODEL

    threat_id: ThreatId
    status: Literal[
        "sufficient",
        "requires_staged_rollout",
        "insufficient",
        "human_required",
    ]
    required_evidence: list[EvidenceKind]
    satisfied_evidence: list[EvidenceKind]
    evidence_refs: list[EvidenceId]
    issues: list[str]


class AdequacyResult(BaseModel):
    """确定性校验器生成的独立 Assurance 结果 artifact。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1] = ASSURANCE_SCHEMA_VERSION
    artifact_schema_version: int | None
    run_id: RunId
    iteration: int
    snapshot: AssuranceSnapshot
    accepted_claims_sha256: Sha256
    active_threats_sha256: Sha256
    evidence_contracts_sha256: Sha256
    status: AdequacyStatus
    decision_source: Literal["deterministic_validator"] = "deterministic_validator"
    artifact_valid: bool
    legacy_artifact: bool
    verification_conclusion: VerificationConclusion | None
    merge_evidence_sufficient: bool
    input_sha256: Sha256
    claim_refs: list[ClaimId]
    threat_results: list[ThreatAdequacy]
    evidence_refs: list[EvidenceId]
    residual_risks: list[str]
    issues: list[str]


ModelT = TypeVar("ModelT", bound=BaseModel)


def build_assurance_context(
    *,
    run_id: str,
    iteration: int,
    snapshot: AssuranceSnapshot | dict[str, Any],
    claims: list[Claim | dict[str, Any]],
    threats: list[Threat | dict[str, Any]],
    evidence_contracts: list[EvidenceRecord | dict[str, Any]],
) -> AssuranceContext:
    """从可信规则或 detector 输出冻结独立期望，供后续 Bundle 校验。"""

    claim_models = [_coerce_model(Claim, item) for item in claims]
    threat_models = [_coerce_model(Threat, item) for item in threats]
    evidence_models = [
        _coerce_model(EvidenceRecord, item)
        for item in evidence_contracts
    ]
    snapshot_model = _coerce_model(AssuranceSnapshot, snapshot)
    return AssuranceContext(
        run_id=run_id,
        iteration=iteration,
        snapshot=snapshot_model,
        accepted_claims_sha256=_record_set_sha256(
            [item for item in claim_models if item.status == "accepted"]
        ),
        active_threats_sha256=_record_set_sha256(
            [item for item in threat_models if item.status == "active"]
        ),
        allowed_evidence_contracts=sorted(
            {_evidence_contract_sha256(item) for item in evidence_models}
        ),
    )


def evaluate_assurance_payload(
    payload: object,
    *,
    workspace: Path,
    expected: AssuranceContext,
) -> AdequacyResult:
    """解析并确定性校验 Assurance payload；任何不确定性都返回 fail-closed 结果。"""

    input_sha256 = _sha256_json(payload)
    if not isinstance(payload, dict):
        return _failed_result(
            expected,
            input_sha256,
            issues=["assurance_schema_invalid:root:model_type"],
        )

    raw_schema_version = payload.get("schema_version")
    if raw_schema_version is None:
        return _failed_result(
            expected,
            input_sha256,
            issues=["assurance_schema_version_missing"],
            legacy_artifact=True,
        )
    if type(raw_schema_version) is not int or raw_schema_version != ASSURANCE_SCHEMA_VERSION:
        version = raw_schema_version if type(raw_schema_version) is int else None
        return _failed_result(
            expected,
            input_sha256,
            issues=["assurance_schema_version_unsupported"],
            artifact_schema_version=version,
            legacy_artifact=bool(version is not None and version < ASSURANCE_SCHEMA_VERSION),
        )

    try:
        bundle = AssuranceBundle.model_validate(payload)
    except (ValidationError, RecursionError) as exc:
        issues = (
            _schema_issues(exc)
            if isinstance(exc, ValidationError)
            else ["assurance_schema_invalid:root:recursion"]
        )
        return _failed_result(
            expected,
            input_sha256,
            issues=issues,
            artifact_schema_version=ASSURANCE_SCHEMA_VERSION,
        )

    return _evaluate_bundle(
        bundle,
        workspace=workspace,
        expected=expected,
        input_sha256=input_sha256,
    )


def evaluate_assurance_artifact(
    path: Path,
    *,
    workspace: Path,
    expected: AssuranceContext,
    result_path: Path | None = None,
) -> AdequacyResult:
    """读取有界 Assurance input，并可选写出独立、脱敏的 AdequacyResult。"""

    result = _evaluate_assurance_bytes(
        path,
        workspace=workspace,
        expected=expected,
    )

    if result_path is not None:
        write_assurance_result(result_path, result)
    return result


def write_assurance_result(path: Path, result: AdequacyResult) -> None:
    """把确定性 Assurance 结果写入独立 JSON artifact。"""

    write_redacted_json(path, result.model_dump(mode="json"))


def _evaluate_assurance_bytes(
    path: Path,
    *,
    workspace: Path,
    expected: AssuranceContext,
) -> AdequacyResult:
    try:
        raw = _read_bounded_bytes(path, MAX_ASSURANCE_INPUT_BYTES)
    except _ArtifactReadError as exc:
        issue = (
            "assurance_artifact_too_large"
            if exc.code == "artifact_too_large"
            else "assurance_artifact_unreadable"
        )
        return _failed_result(
            expected,
            hashlib.sha256(b"").hexdigest(),
            issues=[issue],
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return _failed_result(
            expected,
            hashlib.sha256(raw).hexdigest(),
            issues=["assurance_artifact_invalid_json"],
        )
    return evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=expected,
    ).model_copy(update={"input_sha256": hashlib.sha256(raw).hexdigest()})


def _evaluate_bundle(
    bundle: AssuranceBundle,
    *,
    workspace: Path,
    expected: AssuranceContext,
    input_sha256: str,
) -> AdequacyResult:
    integrity_issues: list[str] = []
    adequacy_issues: list[str] = []
    _validate_bundle_context(bundle, expected, integrity_issues)

    claims, duplicate_claims = _unique_records(bundle.claims)
    threats, duplicate_threats = _unique_records(bundle.threats)
    evidence, duplicate_evidence = _unique_records(bundle.evidence)
    integrity_issues.extend(f"claim_id_duplicate:{item}" for item in duplicate_claims)
    integrity_issues.extend(f"threat_id_duplicate:{item}" for item in duplicate_threats)
    integrity_issues.extend(f"evidence_id_duplicate:{item}" for item in duplicate_evidence)

    accepted_claims = {
        claim_id
        for claim_id, claim in claims.items()
        if claim.status == "accepted"
    }
    active_threats = [
        threat
        for threat in bundle.threats
        if threat.status == "active"
    ]
    if _record_set_sha256(
        [item for item in bundle.claims if item.status == "accepted"]
    ) != expected.accepted_claims_sha256:
        integrity_issues.append("assurance_accepted_claims_hash_mismatch")
    if _record_set_sha256(active_threats) != expected.active_threats_sha256:
        integrity_issues.append("assurance_active_threats_hash_mismatch")

    reference_issues = _validate_all_threat_references(
        bundle.threats,
        claims,
        evidence,
        integrity_issues,
    )
    artifact_reader = _ArtifactReader()
    evidence_validity, derived_conclusions = _validate_evidence_records(
        bundle,
        workspace,
        expected,
        threats,
        evidence,
        artifact_reader,
        integrity_issues,
    )
    derived_verification = _aggregate_verification_conclusion(derived_conclusions)
    if bundle.verification_conclusion != derived_verification:
        integrity_issues.append(
            f"assurance_verification_conclusion_mismatch:{derived_verification}"
        )

    if not active_threats:
        adequacy_issues.append("active_threat_missing")

    threat_results: list[ThreatAdequacy] = []
    residual_risks: list[str] = []
    for threat in active_threats:
        threat_integrity = list(reference_issues.get(threat.id, ()))
        threat_gaps: list[str] = []
        referenced_evidence = [
            evidence[evidence_ref]
            for evidence_ref in threat.evidence_refs
            if evidence_ref in evidence
        ]
        satisfied = sorted(
            {
                record.kind
                for record in referenced_evidence
                if evidence_validity.get(record.id, False)
                and threat.id in record.covers
                and record.result.status == "passed"
            }
        )
        for required_kind in threat.required_evidence:
            if required_kind not in satisfied:
                threat_gaps.append(
                    f"threat:{threat.id}:required_evidence_missing:{required_kind}"
                )

        adequacy_issues.extend(threat_gaps)
        residual_risks.extend(threat.residual_risks)
        if threat_integrity or threat_gaps:
            threat_status = "insufficient"
        elif threat.human_required:
            threat_status = "human_required"
        elif threat.residual_risks:
            threat_status = "requires_staged_rollout"
        else:
            threat_status = "sufficient"
        threat_results.append(
            ThreatAdequacy(
                threat_id=threat.id,
                status=threat_status,
                required_evidence=list(threat.required_evidence),
                satisfied_evidence=satisfied,
                evidence_refs=list(threat.evidence_refs),
                issues=_ordered_unique([*threat_integrity, *threat_gaps]),
            )
        )

    if derived_verification != "verified":
        adequacy_issues.append(f"verification_conclusion:{derived_verification}")

    artifact_valid = not integrity_issues
    has_insufficient_threat = any(
        item.status == "insufficient" for item in threat_results
    )
    if (
        not artifact_valid
        or derived_verification != "verified"
        or not active_threats
        or has_insufficient_threat
    ):
        status: AdequacyStatus = "insufficient"
    elif any(item.status == "human_required" for item in threat_results):
        status = "human_required"
    elif any(item.status == "requires_staged_rollout" for item in threat_results):
        status = "requires_staged_rollout"
    else:
        status = "sufficient_for_merge"

    return AdequacyResult(
        artifact_schema_version=ASSURANCE_SCHEMA_VERSION,
        run_id=expected.run_id,
        iteration=expected.iteration,
        snapshot=expected.snapshot,
        accepted_claims_sha256=expected.accepted_claims_sha256,
        active_threats_sha256=expected.active_threats_sha256,
        evidence_contracts_sha256=_sha256_json(
            sorted(expected.allowed_evidence_contracts)
        ),
        status=status,
        artifact_valid=artifact_valid,
        legacy_artifact=False,
        verification_conclusion=derived_verification,
        merge_evidence_sufficient=(
            artifact_valid and status == "sufficient_for_merge"
        ),
        input_sha256=input_sha256,
        claim_refs=sorted(accepted_claims),
        threat_results=threat_results,
        evidence_refs=sorted(evidence),
        residual_risks=_ordered_unique(residual_risks),
        issues=_ordered_unique([*integrity_issues, *adequacy_issues]),
    )


def _validate_bundle_context(
    bundle: AssuranceBundle,
    expected: AssuranceContext,
    issues: list[str],
) -> None:
    if bundle.run_id != expected.run_id:
        issues.append("assurance_run_id_mismatch")
    if bundle.iteration != expected.iteration:
        issues.append("assurance_iteration_mismatch")
    _validate_snapshot(bundle.snapshot, expected.snapshot, "assurance_", issues)


def _validate_all_threat_references(
    threat_records: list[Threat],
    claims: dict[str, Claim],
    evidence: dict[str, EvidenceRecord],
    issues: list[str],
) -> dict[str, tuple[str, ...]]:
    by_threat: dict[str, tuple[str, ...]] = {}
    for threat in threat_records:
        local: list[str] = []
        for claim_ref in threat.claim_refs:
            claim = claims.get(claim_ref)
            if claim is None:
                local.append(f"threat:{threat.id}:claim_ref_missing:{claim_ref}")
            elif threat.status == "active" and claim.status != "accepted":
                local.append(f"threat:{threat.id}:claim_not_accepted:{claim_ref}")
        for evidence_ref in threat.evidence_refs:
            record = evidence.get(evidence_ref)
            if record is None:
                local.append(
                    f"threat:{threat.id}:evidence_ref_missing:{evidence_ref}"
                )
            elif threat.id not in record.covers:
                local.append(
                    f"threat:{threat.id}:evidence_cover_mismatch:{evidence_ref}"
                )
        issues.extend(local)
        by_threat[threat.id] = tuple(local)
    return by_threat


def _validate_evidence_records(
    bundle: AssuranceBundle,
    workspace: Path,
    expected: AssuranceContext,
    threats: dict[str, Threat],
    evidence: dict[str, EvidenceRecord],
    artifact_reader: _ArtifactReader,
    issues: list[str],
) -> tuple[dict[str, bool], list[VerificationConclusion]]:
    validity: dict[str, bool] = {}
    conclusions: list[VerificationConclusion] = []
    allowed_contracts = set(expected.allowed_evidence_contracts)
    for evidence_id, record in evidence.items():
        local: list[str] = []
        if record.run_id != bundle.run_id:
            local.append(f"evidence:{evidence_id}:run_id_mismatch")
        if record.iteration != bundle.iteration:
            local.append(f"evidence:{evidence_id}:iteration_mismatch")
        _validate_snapshot(
            record.snapshot,
            bundle.snapshot,
            f"evidence:{evidence_id}:",
            local,
        )
        if _evidence_contract_sha256(record) not in allowed_contracts:
            local.append(f"evidence:{evidence_id}:contract_not_allowed")
        for threat_id in record.covers:
            if threat_id not in threats:
                local.append(f"evidence:{evidence_id}:unknown_threat:{threat_id}")

        artifact_valid, conclusion = _validate_evidence_artifact(
            workspace,
            bundle,
            record,
            artifact_reader,
            local,
        )
        if conclusion is not None:
            conclusions.append(conclusion)
        validity[evidence_id] = artifact_valid and not local
        issues.extend(local)
    return validity, conclusions


def _validate_evidence_artifact(
    workspace: Path,
    bundle: AssuranceBundle,
    record: EvidenceRecord,
    artifact_reader: _ArtifactReader,
    issues: list[str],
) -> tuple[bool, VerificationConclusion | None]:
    prefix = f"evidence:{record.id}"
    if len(record.artifacts) != 1:
        issues.append(f"{prefix}:artifact_count_invalid")
        return False, None
    artifact_ref = record.artifacts[0]
    artifact_name = PurePosixPath(artifact_ref.relative_path).name
    if artifact_ref.run_id != bundle.run_id:
        issues.append(f"{prefix}:artifact_run_mismatch:{artifact_ref.run_id}")
        return False, None
    expected_path = f"iterations/{bundle.iteration:02d}/verification-result.json"
    if artifact_ref.relative_path != expected_path:
        issues.append(
            f"{prefix}:artifact_iteration_path_mismatch:{artifact_name}"
        )
        return False, None

    try:
        run_dir = resolve_run_dir(workspace, artifact_ref.run_id)
    except (FileNotFoundError, ValueError, OSError):
        issues.append(f"{prefix}:artifact_run_missing:{artifact_ref.run_id}")
        return False, None
    candidate = run_dir.joinpath(*PurePosixPath(artifact_ref.relative_path).parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(run_dir)
    except (FileNotFoundError, ValueError, OSError):
        issues.append(f"{prefix}:artifact_path_invalid:{artifact_name}")
        return False, None
    if not resolved.is_file():
        issues.append(f"{prefix}:artifact_not_file:{artifact_name}")
        return False, None
    try:
        raw = artifact_reader.read(resolved)
    except _ArtifactReadError as exc:
        issues.append(f"{prefix}:{exc.code}:{artifact_name}")
        return False, None
    if hashlib.sha256(raw).hexdigest() != artifact_ref.sha256:
        issues.append(f"{prefix}:artifact_hash_mismatch:{artifact_name}")
        return False, None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        issues.append(f"{prefix}:verification_artifact_invalid_json")
        return False, None
    conclusion = _validate_verification_payload(payload, bundle, record, issues)
    return conclusion is not None, conclusion


def _validate_verification_payload(
    payload: object,
    bundle: AssuranceBundle,
    record: EvidenceRecord,
    issues: list[str],
) -> VerificationConclusion | None:
    prefix = f"evidence:{record.id}:verification"
    if not isinstance(payload, dict):
        issues.append(f"{prefix}_schema_invalid")
        return None
    if type(payload.get("artifact_version")) is not int or payload["artifact_version"] != 2:
        issues.append(f"{prefix}_artifact_version_invalid")
    if payload.get("run_id") != bundle.run_id:
        issues.append(f"{prefix}_run_id_mismatch")
    if type(payload.get("iteration")) is not int or payload["iteration"] != bundle.iteration:
        issues.append(f"{prefix}_iteration_mismatch")
    if payload.get("shell_kind") not in {"cmd", "posix-sh"}:
        issues.append(f"{prefix}_shell_kind_invalid")
    if not isinstance(payload.get("repo_path"), str):
        issues.append(f"{prefix}_repo_path_invalid")

    commands = payload.get("commands")
    results = payload.get("results")
    command_count = payload.get("command_count")
    failed_count = payload.get("failed_count")
    selected_count = payload.get("selected_command_count")
    skipped_commands = payload.get("skipped_commands")
    interruption_status = payload.get("interruption_status")
    if not _is_string_list(commands):
        issues.append(f"{prefix}_commands_invalid")
        commands = []
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        issues.append(f"{prefix}_results_invalid")
        results = []
    for value, name in (
        (command_count, "command_count"),
        (failed_count, "failed_count"),
        (selected_count, "selected_command_count"),
    ):
        if not _is_non_negative_int(value):
            issues.append(f"{prefix}_{name}_invalid")
    if not _is_string_list(skipped_commands):
        issues.append(f"{prefix}_skipped_commands_invalid")
        skipped_commands = []
    if _is_non_negative_int(command_count):
        if command_count != len(commands):
            issues.append(f"{prefix}_command_count_mismatch")
        if command_count != len(results):
            issues.append(f"{prefix}_result_count_mismatch")
    if _is_non_negative_int(selected_count):
        expected_selected_count = len(commands)
        if interruption_status is not None:
            expected_selected_count += len(skipped_commands)
        if selected_count != expected_selected_count:
            issues.append(f"{prefix}_selected_command_count_mismatch")
    if interruption_status is None and skipped_commands != []:
        issues.append(f"{prefix}_skipped_commands_present")

    result_statuses: list[str] = []
    for index, item in enumerate(results):
        if index >= len(commands) or item.get("command") != commands[index]:
            issues.append(f"{prefix}_command_binding_mismatch")
            continue
        configured_command = commands[index]
        if item.get("configured_command") != configured_command:
            issues.append(f"{prefix}_configured_command_mismatch")
        command_index = item.get("command_index")
        if type(command_index) is not int or command_index != index + 1:
            issues.append(f"{prefix}_command_index_mismatch")
        try:
            expected_executed_command = render_verification_command(
                configured_command,
                payload["shell_kind"],
            )
        except ValueError:
            issues.append(f"{prefix}_executed_command_unrenderable")
        else:
            if item.get("executed_command") != expected_executed_command:
                issues.append(f"{prefix}_executed_command_mismatch")
        expected_verification_temp = None
        if VERIFICATION_TEMP_PLACEHOLDER in configured_command:
            expected_verification_temp = (
                VERIFICATION_TEMP_ROOT
                / bundle.run_id
                / f"iteration-{bundle.iteration}"
                / f"command-{index + 1}"
            ).as_posix()
        if item.get("verification_temp") != expected_verification_temp:
            issues.append(f"{prefix}_temp_path_mismatch")
        status = item.get("status")
        if status not in {"passed", "failed", "timeout"}:
            issues.append(f"{prefix}_result_status_invalid")
            continue
        result_statuses.append(status)
        returncode = item.get("returncode")
        if returncode is not None and type(returncode) is not int:
            issues.append(f"{prefix}_returncode_invalid")
        duration = item.get("duration_seconds")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration < 0
        ):
            issues.append(f"{prefix}_duration_invalid")
        if item.get("interruption_status") not in {
            None,
            "timed_out",
            "stopped",
            "termination-unconfirmed",
        }:
            issues.append(f"{prefix}_result_interruption_invalid")
    if _is_non_negative_int(failed_count):
        actual_failed = sum(status != "passed" for status in result_statuses)
        if failed_count != actual_failed:
            issues.append(f"{prefix}_failed_count_mismatch")

    if interruption_status not in {
        None,
        "timed_out",
        "stopped",
        "termination-unconfirmed",
    }:
        issues.append(f"{prefix}_interruption_status_invalid")
    if any(issue.startswith(prefix) for issue in issues):
        return None

    if interruption_status is not None or any(
        item.get("interruption_status") is not None for item in results
    ):
        conclusion: VerificationConclusion = "interrupted"
    elif not commands:
        conclusion = "unknown"
    elif failed_count:
        conclusion = "failed"
    else:
        conclusion = "verified"

    matching_indexes = [
        index for index, command in enumerate(commands) if command == record.command
    ]
    if len(matching_indexes) != 1:
        issues.append(f"{prefix}_evidence_command_mismatch")
        return None
    selected = results[matching_indexes[0]]
    if selected.get("interruption_status") is not None:
        evidence_status = "interrupted"
    elif selected.get("status") == "passed":
        evidence_status = "passed"
    elif selected.get("status") == "failed":
        evidence_status = "failed"
    else:
        evidence_status = "interrupted"
    if record.result.status != evidence_status:
        issues.append(f"{prefix}_evidence_status_mismatch")
    if record.result.exit_code != selected.get("returncode"):
        issues.append(f"{prefix}_evidence_exit_code_mismatch")
    if record.result.duration_seconds != selected.get("duration_seconds"):
        issues.append(f"{prefix}_evidence_duration_mismatch")
    if any(issue.startswith(prefix) for issue in issues):
        return None
    return conclusion


def _aggregate_verification_conclusion(
    conclusions: list[VerificationConclusion],
) -> VerificationConclusion:
    if not conclusions:
        return "unknown"
    if "interrupted" in conclusions:
        return "interrupted"
    if "failed" in conclusions:
        return "failed"
    if "unknown" in conclusions:
        return "unknown"
    return "verified"


def _validate_snapshot(
    actual: AssuranceSnapshot,
    expected: AssuranceSnapshot,
    prefix: str,
    issues: list[str],
) -> None:
    for field_name, issue in _SNAPSHOT_ISSUES.items():
        if getattr(actual, field_name) != getattr(expected, field_name):
            issues.append(f"{prefix}{issue}")


def _record_set_sha256(records: list[BaseModel]) -> str:
    payload = sorted(
        (record.model_dump(mode="json") for record in records),
        key=lambda item: str(item.get("id") or ""),
    )
    return _sha256_json(payload)


def _evidence_contract_sha256(record: EvidenceRecord) -> str:
    payload = record.model_dump(mode="json")
    payload.pop("result", None)
    for artifact in payload.get("artifacts", []):
        artifact.pop("sha256", None)
    return _sha256_json(payload)


def _unique_records(
    records: list[Any],
) -> tuple[dict[str, Any], list[str]]:
    result: dict[str, Any] = {}
    duplicates: list[str] = []
    for record in records:
        if record.id in result:
            duplicates.append(record.id)
            continue
        result[record.id] = record
    return result, sorted(set(duplicates))


def _schema_issues(exc: ValidationError) -> list[str]:
    issues: list[str] = []
    for error in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "root"
        issues.append(f"assurance_schema_invalid:{location}:{error['type']}")
    return _ordered_unique(issues)


def _failed_result(
    expected: AssuranceContext,
    input_sha256: str,
    *,
    issues: list[str],
    artifact_schema_version: int | None = None,
    legacy_artifact: bool = False,
) -> AdequacyResult:
    return AdequacyResult(
        artifact_schema_version=artifact_schema_version,
        run_id=expected.run_id,
        iteration=expected.iteration,
        snapshot=expected.snapshot,
        accepted_claims_sha256=expected.accepted_claims_sha256,
        active_threats_sha256=expected.active_threats_sha256,
        evidence_contracts_sha256=_sha256_json(
            sorted(expected.allowed_evidence_contracts)
        ),
        status="insufficient",
        artifact_valid=False,
        legacy_artifact=legacy_artifact,
        verification_conclusion=None,
        merge_evidence_sufficient=False,
        input_sha256=input_sha256,
        claim_refs=[],
        threat_results=[],
        evidence_refs=[],
        residual_risks=[],
        issues=_ordered_unique(issues),
    )


def _coerce_model(
    model_type: type[ModelT],
    value: ModelT | dict[str, Any],
) -> ModelT:
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)


def _sha256_json(payload: object) -> str:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        serialized = repr(type(payload))
    return hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _is_non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


@dataclass
class _ArtifactReader:
    remaining_bytes: int = MAX_TOTAL_EVIDENCE_BYTES
    cache: dict[Path, bytes] = field(default_factory=dict)
    failures: dict[Path, str] = field(default_factory=dict)

    def read(self, path: Path) -> bytes:
        cached = self.cache.get(path)
        if cached is not None:
            return cached
        cached_failure = self.failures.get(path)
        if cached_failure is not None:
            raise _ArtifactReadError(cached_failure)
        if self.remaining_bytes <= 0:
            self.failures[path] = "artifact_budget_exceeded"
            raise _ArtifactReadError("artifact_budget_exceeded")

        max_bytes = min(
            MAX_EVIDENCE_ARTIFACT_BYTES,
            max(self.remaining_bytes - 1, 0),
        )
        overflow_code = (
            "artifact_too_large"
            if max_bytes == MAX_EVIDENCE_ARTIFACT_BYTES
            else "artifact_budget_exceeded"
        )
        try:
            data = _read_bounded_bytes(
                path,
                max_bytes,
                overflow_code=overflow_code,
            )
        except _ArtifactReadError as exc:
            self.remaining_bytes = max(0, self.remaining_bytes - exc.bytes_read)
            self.failures[path] = exc.code
            raise
        self.remaining_bytes -= len(data)
        self.cache[path] = data
        return data


def _read_bounded_bytes(
    path: Path,
    max_bytes: int,
    *,
    overflow_code: str = "artifact_too_large",
) -> bytes:
    try:
        with path.open("rb") as stream:
            data = bytearray()
            read_limit = max_bytes + 1
            while len(data) < read_limit:
                chunk = stream.read(read_limit - len(data))
                if not chunk:
                    break
                data.extend(chunk)
    except OSError as exc:
        raise _ArtifactReadError("artifact_unreadable") from exc
    if len(data) > max_bytes:
        raise _ArtifactReadError(overflow_code, bytes_read=len(data))
    return bytes(data)


class _ArtifactReadError(RuntimeError):
    def __init__(self, code: str, *, bytes_read: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.bytes_read = bytes_read
