from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .parallel_review import (
    AggregatedReviewFinding,
    ParallelReviewAggregate,
    ParallelReviewFinding,
    ParallelReviewResult,
    ReviewTopology,
    ReviewVerdictValue,
)


REVIEWER_TOPOLOGY_EVAL_SCHEMA_VERSION = 1
DEFAULT_PROVIDER_SESSION_LIMIT = 90

EvalCaseKind: TypeAlias = Literal[
    "clean",
    "correctness",
    "verification_adequacy",
    "security_design",
]
FindingSeverity: TypeAlias = Literal["blocker", "major", "minor", "suggestion"]

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_FINDING_COMPONENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,119}$")
_LOCATION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:/#-]{0,199}$")
_SEVERITY_RANK: dict[FindingSeverity, int] = {
    "suggestion": 0,
    "minor": 1,
    "major": 2,
    "blocker": 3,
}
_BLOCKER_MAJOR = frozenset({"blocker", "major"})


class ReviewerTopologyPublicCase(BaseModel):
    """可进入 Reviewer 公共输入的数据集 case 身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    category: EvalCaseKind
    task: str = ""
    acceptance: list[str] = Field(default_factory=list, max_length=100)
    before_files: dict[str, str] = Field(default_factory=dict, max_length=500)
    after_files: dict[str, str] = Field(default_factory=dict, max_length=500)
    verification: dict[str, object] = Field(default_factory=dict)
    routing_facts: dict[str, object] = Field(default_factory=dict)
    workspace_fixture_sha256: str | None = None
    evidence_snapshot_sha256: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_compact_case(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        _move_alias(payload, "case_kind", "category")
        payload.pop("schema_version", None)
        return payload

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _normalize_identifier(value, "case_id")

    @field_validator("workspace_fixture_sha256", "evidence_snapshot_sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        return _normalize_sha256(value) if value is not None else None

    @field_validator("task")
    @classmethod
    def validate_task(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) > 20_000:
            raise ValueError("public case task 过长")
        return normalized

    @field_validator("acceptance")
    @classmethod
    def validate_acceptance(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("acceptance 不能包含空项")
        return normalized

    @field_validator("before_files", "after_files")
    @classmethod
    def validate_fixture_files(cls, value: dict[str, str]) -> dict[str, str]:
        return {_normalize_repo_path(path): content for path, content in value.items()}

    @model_validator(mode="after")
    def validate_fixture_diff(self) -> Self:
        if self.before_files or self.after_files:
            paths = set(self.before_files) | set(self.after_files)
            if not any(self.before_files.get(path) != self.after_files.get(path) for path in paths):
                raise ValueError("public case fixture 必须包含真实 diff")
        return self

    @property
    def case_kind(self) -> EvalCaseKind:
        return self.category


class ReviewerTopologyPublicDataset(BaseModel):
    """不含 ground truth 的公共案例集。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = REVIEWER_TOPOLOGY_EVAL_SCHEMA_VERSION
    dataset_id: str = "reviewer-topology-eval-v1"
    cases: list[ReviewerTopologyPublicCase] = Field(min_length=1)

    @field_validator("dataset_id")
    @classmethod
    def validate_dataset_id(cls, value: str) -> str:
        return _normalize_identifier(value, "dataset_id")

    @model_validator(mode="after")
    def validate_unique_cases(self) -> Self:
        _require_unique(
            [case.case_id for case in self.cases],
            "public dataset case_id",
        )
        fixture_hashes = [
            case.workspace_fixture_sha256
            for case in self.cases
            if case.workspace_fixture_sha256 is not None
        ]
        _require_unique(fixture_hashes, "public dataset workspace fixture")
        return self


class ReviewerTopologyGroundTruthFinding(BaseModel):
    """不进入 prompt 的 finding 真值及允许的等价写法。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    category: str
    rule_id: str
    normalized_path: str
    normalized_location: str
    severity_min: FindingSeverity
    severity_max: FindingSeverity
    severity_aliases: list[str] = Field(default_factory=list, max_length=50)
    category_aliases: list[str] = Field(default_factory=list, max_length=50)
    rule_aliases: list[str] = Field(default_factory=list, max_length=50)
    path_aliases: list[str] = Field(default_factory=list, max_length=100)
    location_aliases: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def accept_manifest_field_names(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        _move_alias(payload, "rule", "rule_id")
        _move_alias(payload, "path", "normalized_path")
        _move_alias(payload, "location", "normalized_location")
        _move_alias(payload, "rule_id_aliases", "rule_aliases")
        _move_alias(payload, "allowed_rule_aliases", "rule_aliases")
        _move_alias(payload, "allowed_category_aliases", "category_aliases")
        _move_alias(
            payload,
            "allowed_alternative_locations",
            "location_aliases",
        )
        severity = payload.pop("severity", None)
        severity_range = payload.pop("expected_severity_range", None)
        if severity_range is None:
            severity_range = payload.pop("severity_range", None)
        if severity_range is None and severity is not None:
            severity_range = severity
        if severity_range is not None:
            severity_min, severity_max = _parse_severity_range(severity_range)
            payload.setdefault("severity_min", severity_min)
            payload.setdefault("severity_max", severity_max)
        if "finding_id" not in payload and "rule_id" in payload:
            payload["finding_id"] = payload["rule_id"]
        return payload

    @field_validator("finding_id")
    @classmethod
    def validate_finding_id(cls, value: str) -> str:
        return _normalize_identifier(value, "finding_id")

    @field_validator("category", "rule_id")
    @classmethod
    def validate_component(cls, value: str) -> str:
        return _normalize_finding_component(value)

    @field_validator("normalized_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_repo_path(value)

    @field_validator("normalized_location")
    @classmethod
    def validate_location(cls, value: str) -> str:
        return _normalize_location(value)

    @field_validator("category_aliases", "rule_aliases")
    @classmethod
    def validate_component_aliases(cls, value: list[str]) -> list[str]:
        return sorted({_normalize_finding_component(item) for item in value})

    @field_validator("location_aliases")
    @classmethod
    def validate_location_aliases(cls, value: list[str]) -> list[str]:
        return sorted({_normalize_location_alias(item) for item in value})

    @field_validator("path_aliases")
    @classmethod
    def validate_path_aliases(cls, value: list[str]) -> list[str]:
        return sorted({_normalize_repo_path(item) for item in value})

    @field_validator("severity_aliases")
    @classmethod
    def validate_severity_aliases(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().lower() for item in value})
        if any(not item for item in normalized):
            raise ValueError("severity_aliases 不能包含空项")
        return normalized

    @model_validator(mode="after")
    def validate_range_and_aliases(self) -> Self:
        if _SEVERITY_RANK[self.severity_min] > _SEVERITY_RANK[self.severity_max]:
            raise ValueError("severity_min 不能高于 severity_max")
        return self

    def matches_core_identity(
        self,
        finding: ParallelReviewFinding | AggregatedReviewFinding,
    ) -> bool:
        return (
            finding.normalized_path in {self.normalized_path, *self.path_aliases}
            and finding.category in {self.category, *self.category_aliases}
            and finding.rule_id in {self.rule_id, *self.rule_aliases}
        )

    def matches_exact_core_identity(
        self,
        finding: ParallelReviewFinding | AggregatedReviewFinding,
    ) -> bool:
        return (
            finding.normalized_path == self.normalized_path
            and finding.category == self.category
            and finding.rule_id == self.rule_id
        )

    def matches_location(
        self,
        finding: ParallelReviewFinding | AggregatedReviewFinding,
    ) -> bool:
        return finding.normalized_location in {
            self.normalized_location,
            *self.location_aliases,
        }

    def matches_exact_location(
        self,
        finding: ParallelReviewFinding | AggregatedReviewFinding,
    ) -> bool:
        return finding.normalized_location == self.normalized_location

    def accepts_severity(self, severity: FindingSeverity) -> bool:
        if severity in self.severity_aliases:
            return True
        rank = _SEVERITY_RANK[severity]
        return _SEVERITY_RANK[self.severity_min] <= rank <= _SEVERITY_RANK[self.severity_max]

    @property
    def expects_blocker_major(self) -> bool:
        return self.severity_min in _BLOCKER_MAJOR


class ReviewerTopologyGroundTruthCase(BaseModel):
    """与公共 case 通过 hash 绑定、但不得暴露给 Reviewer 的真值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    workspace_fixture_sha256: str | None = None
    evidence_snapshot_sha256: str | None = None
    expected_verdict: ReviewVerdictValue = "request_changes"
    expected_findings: list[ReviewerTopologyGroundTruthFinding] = Field(
        default_factory=list,
        max_length=200,
    )
    forbidden_false_blocker_conditions: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="before")
    @classmethod
    def accept_manifest_case(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        _move_alias(payload, "findings", "expected_findings")
        payload.pop("schema_version", None)
        return payload

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _normalize_identifier(value, "case_id")

    @field_validator("workspace_fixture_sha256", "evidence_snapshot_sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        return _normalize_sha256(value) if value is not None else None

    @field_validator("forbidden_false_blocker_conditions")
    @classmethod
    def validate_conditions(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("forbidden false-blocker condition 不能为空")
        if len(normalized) != len(set(normalized)):
            raise ValueError("forbidden false-blocker condition 不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_findings(self) -> Self:
        _require_unique(
            [finding.finding_id for finding in self.expected_findings],
            "ground truth finding_id",
        )
        for index, left in enumerate(self.expected_findings):
            for right in self.expected_findings[index + 1 :]:
                if _ground_truth_identities_overlap(left, right):
                    raise ValueError("ground truth finding alias 不能形成歧义匹配")
        return self


class ReviewerTopologyGroundTruthDataset(BaseModel):
    """与公共数据集分离保存的 ground-truth manifest。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = REVIEWER_TOPOLOGY_EVAL_SCHEMA_VERSION
    dataset_id: str = "reviewer-topology-eval-v1"
    cases: list[ReviewerTopologyGroundTruthCase] = Field(min_length=1)

    @field_validator("dataset_id")
    @classmethod
    def validate_dataset_id(cls, value: str) -> str:
        return _normalize_identifier(value, "dataset_id")

    @model_validator(mode="after")
    def validate_unique_cases(self) -> Self:
        _require_unique(
            [case.case_id for case in self.cases],
            "ground truth case_id",
        )
        return self


class ReviewerTopologyEvaluationDataset(BaseModel):
    """在评测进程内校验 public dataset 与 ground truth 的完整绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    public_dataset: ReviewerTopologyPublicDataset
    ground_truth: ReviewerTopologyGroundTruthDataset

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        public_by_id = {case.case_id: case for case in self.public_dataset.cases}
        truth_by_id = {case.case_id: case for case in self.ground_truth.cases}
        if self.public_dataset.dataset_id != self.ground_truth.dataset_id:
            raise ValueError("public dataset 与 ground truth dataset_id 不一致")
        if set(truth_by_id) != set(public_by_id):
            raise ValueError("ground truth 必须精确覆盖全部 public case")
        for case_id, truth in truth_by_id.items():
            public_case = public_by_id[case_id]
            required_commitment_fields = {
                "expected_verdict",
                "expected_findings",
                "forbidden_false_blocker_conditions",
            }
            if not required_commitment_fields.issubset(truth.model_fields_set):
                raise ValueError(
                    f"case {case_id} 的 ground truth commitment 必须显式声明"
                    " expected_verdict、findings 和"
                    " forbidden_false_blocker_conditions"
                )
            if (
                public_case.workspace_fixture_sha256 is not None
                and truth.workspace_fixture_sha256 is not None
                and public_case.workspace_fixture_sha256 != truth.workspace_fixture_sha256
            ) or (
                public_case.evidence_snapshot_sha256 is not None
                and truth.evidence_snapshot_sha256 is not None
                and public_case.evidence_snapshot_sha256 != truth.evidence_snapshot_sha256
            ):
                raise ValueError(f"case {case_id} 的 public/ground truth hash 绑定不一致")
            if public_case.case_kind == "clean":
                if truth.expected_verdict != "approve" or truth.expected_findings:
                    raise ValueError("clean case 必须声明 approve 且 expected findings 为空")
                if truth.forbidden_false_blocker_conditions != [
                    "any blocker finding",
                    "any major finding",
                ]:
                    raise ValueError(
                        "clean case 必须冻结 blocker/major false-positive 条件"
                    )
            elif truth.expected_verdict != "request_changes" or not truth.expected_findings:
                raise ValueError("缺陷 case 必须声明 request_changes 和 expected finding")
            elif truth.forbidden_false_blocker_conditions:
                raise ValueError(
                    "缺陷 case 的 forbidden false-blocker 条件必须显式为空"
                )
        return self


class ReviewerTopologyCaseScore(BaseModel):
    """单个 case 的可复算 finding/verdict 评分。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = REVIEWER_TOPOLOGY_EVAL_SCHEMA_VERSION
    case_id: str
    topology: ReviewTopology
    expected_verdict: ReviewVerdictValue
    actual_verdict: ReviewVerdictValue
    verdict_correct: bool
    expected_finding_count: int = Field(ge=0)
    predicted_finding_count: int = Field(ge=0)
    true_positive_count: int = Field(ge=0)
    exact_true_positive_count: int = Field(ge=0)
    alias_true_positive_count: int = Field(ge=0)
    severity_mismatch_count: int = Field(ge=0)
    severity_range_accuracy: float = Field(ge=0.0, le=1.0)
    false_positive_count: int = Field(ge=0)
    false_negative_count: int = Field(ge=0)
    finding_precision: float = Field(ge=0.0, le=1.0)
    finding_recall: float = Field(ge=0.0, le=1.0)
    expected_blocker_major_count: int = Field(ge=0)
    true_positive_blocker_major_count: int = Field(ge=0)
    blocker_major_recall: float = Field(ge=0.0, le=1.0)
    clean_false_blocker_count: int = Field(ge=0)
    clean_false_major_count: int = Field(ge=0)
    clean_has_false_blocker: bool
    clean_has_false_major: bool
    raw_finding_count: int = Field(ge=0)
    unique_raw_finding_count: int = Field(ge=0)
    duplicate_finding_count: int = Field(ge=0)
    duplicate_ratio: float = Field(ge=0.0, le=1.0)
    true_positive_finding_ids: list[str]
    exact_true_positive_finding_ids: list[str]
    alias_true_positive_finding_ids: list[str]
    severity_mismatch_finding_ids: list[str]
    true_positive_blocker_major_finding_ids: list[str]
    false_negative_finding_ids: list[str]
    unique_true_positive_finding_ids: list[str]
    unique_true_positive_blocker_major_finding_ids: list[str]

    @field_validator(
        "true_positive_finding_ids",
        "exact_true_positive_finding_ids",
        "alias_true_positive_finding_ids",
        "severity_mismatch_finding_ids",
        "true_positive_blocker_major_finding_ids",
        "false_negative_finding_ids",
        "unique_true_positive_finding_ids",
        "unique_true_positive_blocker_major_finding_ids",
    )
    @classmethod
    def validate_sorted_ids(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("case score finding ids 必须唯一排序")
        return value


class ReviewerTopologySummary(BaseModel):
    """同一 topology 全部 case 的微平均汇总。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = REVIEWER_TOPOLOGY_EVAL_SCHEMA_VERSION
    topology: ReviewTopology
    case_count: int = Field(ge=1)
    expected_finding_count: int = Field(ge=0)
    predicted_finding_count: int = Field(ge=0)
    true_positive_count: int = Field(ge=0)
    exact_true_positive_count: int = Field(ge=0)
    alias_true_positive_count: int = Field(ge=0)
    severity_mismatch_count: int = Field(ge=0)
    severity_range_accuracy: float = Field(ge=0.0, le=1.0)
    false_positive_count: int = Field(ge=0)
    false_negative_count: int = Field(ge=0)
    finding_precision: float = Field(ge=0.0, le=1.0)
    finding_recall: float = Field(ge=0.0, le=1.0)
    expected_blocker_major_count: int = Field(ge=0)
    true_positive_blocker_major_count: int = Field(ge=0)
    blocker_major_recall: float = Field(ge=0.0, le=1.0)
    clean_case_count: int = Field(ge=0)
    clean_false_blocker_case_count: int = Field(ge=0)
    clean_false_major_case_count: int = Field(ge=0)
    clean_false_blocker_count: int = Field(ge=0)
    clean_false_major_count: int = Field(ge=0)
    raw_finding_count: int = Field(ge=0)
    unique_raw_finding_count: int = Field(ge=0)
    duplicate_finding_count: int = Field(ge=0)
    duplicate_ratio: float = Field(ge=0.0, le=1.0)
    verdict_correct_count: int = Field(ge=0)
    verdict_accuracy: float = Field(ge=0.0, le=1.0)
    unique_true_positive_count: int = Field(ge=0)
    unique_true_positive_blocker_major_count: int = Field(ge=0)
    unique_true_positive_keys: list[str]
    unique_true_positive_blocker_major_keys: list[str]


class ProviderSessionBudgetExceeded(RuntimeError):
    """provider session 硬预算已经耗尽。"""


class ProviderSessionBudget:
    """线程安全的 provider session 启动预算。"""

    def __init__(self, max_sessions: int = DEFAULT_PROVIDER_SESSION_LIMIT) -> None:
        if not isinstance(max_sessions, int) or isinstance(max_sessions, bool) or max_sessions < 1:
            raise ValueError("max_sessions 必须是正整数")
        self._max_sessions = max_sessions
        self._used_sessions = 0
        self._lock = Lock()

    @property
    def max_sessions(self) -> int:
        return self._max_sessions

    @property
    def used_sessions(self) -> int:
        with self._lock:
            return self._used_sessions

    @property
    def remaining_sessions(self) -> int:
        with self._lock:
            return self._max_sessions - self._used_sessions

    def reserve_session(self) -> int:
        """在真正启动 provider 前预留名额，返回从 1 开始的 session 序号。"""

        return self.reserve()

    def reserve_provider_session(self) -> int:
        return self.reserve()

    def reserve(self, count: int = 1) -> int:
        """原子预留一批 session，返回预留后的累计使用量。"""

        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("count 必须是正整数")
        with self._lock:
            requested_total = self._used_sessions + count
            if requested_total > self._max_sessions:
                raise ProviderSessionBudgetExceeded(
                    "provider session 硬预算已耗尽，已在启动前 fail-closed"
                )
            self._used_sessions = requested_total
            return self._used_sessions


def load_public_dataset(
    path: str | Path,
) -> ReviewerTopologyPublicDataset:
    """从 JSON 文件加载只含公共输入的评测数据集。"""

    payload = _load_json_payload(path)
    if isinstance(payload, list):
        payload = {"cases": payload}
    return ReviewerTopologyPublicDataset.model_validate(payload)


def load_ground_truth(
    path: str | Path,
    expected_sha256: str | None = None,
) -> ReviewerTopologyGroundTruthDataset:
    """加载私有 ground truth，并可在解析前校验文件内容哈希。"""

    resolved = Path(path)
    raw = resolved.read_bytes()
    if expected_sha256 is not None:
        normalized_expected = _normalize_expected_file_sha256(expected_sha256)
        actual = hashlib.sha256(raw).hexdigest()
        if actual != normalized_expected:
            raise ValueError("ground truth 文件 sha256 与预注册值不一致")
    payload = _decode_json_payload(raw, resolved)
    if isinstance(payload, list):
        payload = {"cases": payload}
    return ReviewerTopologyGroundTruthDataset.model_validate(payload)


def score_case(
    case_id: str,
    topology: ReviewTopology,
    aggregate: ParallelReviewAggregate,
    results: Iterable[ParallelReviewResult],
    ground_truth_case: ReviewerTopologyGroundTruthCase | None,
) -> ReviewerTopologyCaseScore:
    """供 harness 使用的窄入口，只消费私有真值与结构化 Review 工件。"""

    normalized_case_id = _normalize_identifier(case_id, "case_id")
    if ground_truth_case is None:
        validated_truth = ReviewerTopologyGroundTruthCase(
            case_id=normalized_case_id,
            evidence_snapshot_sha256=aggregate.evidence_snapshot_sha256,
            expected_verdict="approve",
            expected_findings=[],
        )
    else:
        validated_truth = ReviewerTopologyGroundTruthCase.model_validate(
            ground_truth_case.model_dump(mode="json")
        )
    if normalized_case_id != validated_truth.case_id:
        raise ValueError("case_id 与 ground_truth_case 不一致")
    inferred_kind: EvalCaseKind = (
        "clean"
        if (not validated_truth.expected_findings and validated_truth.expected_verdict == "approve")
        else "correctness"
    )
    public_case = ReviewerTopologyPublicCase(
        case_id=validated_truth.case_id,
        category=inferred_kind,
        workspace_fixture_sha256=(validated_truth.workspace_fixture_sha256),
        evidence_snapshot_sha256=(validated_truth.evidence_snapshot_sha256),
    )
    return score_reviewer_topology_case(
        public_case=public_case,
        ground_truth=validated_truth,
        topology=topology,
        results=results,
        aggregate=aggregate,
    )


def summarize_topology_scores(
    scores: Iterable[ReviewerTopologyCaseScore],
    *,
    single_scores: Mapping[str, ReviewerTopologyCaseScore] | None = None,
) -> ReviewerTopologySummary:
    """供 harness 使用的 topology 汇总入口。"""

    return summarize_reviewer_topology(
        scores,
        single_scores=single_scores,
    )


def score_reviewer_topology_case(
    *,
    public_case: ReviewerTopologyPublicCase,
    ground_truth: ReviewerTopologyGroundTruthCase,
    topology: ReviewTopology,
    results: Iterable[ParallelReviewResult],
    aggregate: ParallelReviewAggregate,
    single_true_positive_finding_ids: Iterable[str] = (),
) -> ReviewerTopologyCaseScore:
    """对一个 topology/case 的结构化结果执行确定性评分。"""

    public_case = ReviewerTopologyPublicCase.model_validate(public_case.model_dump(mode="json"))
    ground_truth = ReviewerTopologyGroundTruthCase.model_validate(
        ground_truth.model_dump(mode="json")
    )
    aggregate = ParallelReviewAggregate.model_validate(aggregate.model_dump(mode="json"))
    validated_results = [
        ParallelReviewResult.model_validate(result.model_dump(mode="json")) for result in results
    ]
    _validate_case_score_binding(
        public_case=public_case,
        ground_truth=ground_truth,
        results=validated_results,
        aggregate=aggregate,
    )

    (
        matched,
        false_positive_ids,
        match_kinds,
        severity_mismatch_ids,
    ) = _match_aggregate_findings(
        ground_truth.expected_findings,
        aggregate.findings,
    )
    true_positive_ids = sorted(matched)
    exact_true_positive_ids = sorted(
        finding_id
        for finding_id, match_kind in match_kinds.items()
        if match_kind == "exact"
    )
    alias_true_positive_ids = sorted(
        finding_id
        for finding_id, match_kind in match_kinds.items()
        if match_kind == "alias"
    )
    false_negative_ids = sorted(
        finding.finding_id
        for finding in ground_truth.expected_findings
        if finding.finding_id not in matched
    )
    expected_blocker_major_ids = {
        finding.finding_id
        for finding in ground_truth.expected_findings
        if finding.expects_blocker_major
    }
    true_positive_blocker_major_ids = sorted(expected_blocker_major_ids.intersection(matched))
    baseline_ids = set(single_true_positive_finding_ids)
    unique_true_positive_ids = sorted(set(true_positive_ids) - baseline_ids)
    unique_blocker_major_ids = sorted(set(true_positive_blocker_major_ids) - baseline_ids)

    raw_findings = [
        finding
        for result in validated_results
        if result.status == "completed"
        for finding in result.findings
    ]
    semantic_raw_keys = {
        _semantic_finding_key(finding, ground_truth.expected_findings) for finding in raw_findings
    }
    duplicate_count = len(raw_findings) - len(semantic_raw_keys)
    clean = public_case.case_kind == "clean"
    false_blocker_count = (
        sum(finding.severity == "blocker" for finding in aggregate.findings) if clean else 0
    )
    false_major_count = (
        sum(finding.severity == "major" for finding in aggregate.findings) if clean else 0
    )
    true_positive_count = len(true_positive_ids)
    predicted_count = len(aggregate.findings)
    expected_count = len(ground_truth.expected_findings)

    return ReviewerTopologyCaseScore(
        case_id=public_case.case_id,
        topology=topology,
        expected_verdict=ground_truth.expected_verdict,
        actual_verdict=aggregate.verdict,
        verdict_correct=aggregate.verdict == ground_truth.expected_verdict,
        expected_finding_count=expected_count,
        predicted_finding_count=predicted_count,
        true_positive_count=true_positive_count,
        exact_true_positive_count=len(exact_true_positive_ids),
        alias_true_positive_count=len(alias_true_positive_ids),
        severity_mismatch_count=len(severity_mismatch_ids),
        severity_range_accuracy=_ratio(
            true_positive_count,
            true_positive_count + len(severity_mismatch_ids),
            empty_value=1.0,
        ),
        false_positive_count=len(false_positive_ids),
        false_negative_count=len(false_negative_ids),
        finding_precision=_ratio(
            true_positive_count,
            predicted_count,
            empty_value=1.0 if expected_count == 0 else 0.0,
        ),
        finding_recall=_ratio(
            true_positive_count,
            expected_count,
            empty_value=1.0,
        ),
        expected_blocker_major_count=len(expected_blocker_major_ids),
        true_positive_blocker_major_count=len(true_positive_blocker_major_ids),
        blocker_major_recall=_ratio(
            len(true_positive_blocker_major_ids),
            len(expected_blocker_major_ids),
            empty_value=1.0,
        ),
        clean_false_blocker_count=false_blocker_count,
        clean_false_major_count=false_major_count,
        clean_has_false_blocker=false_blocker_count > 0,
        clean_has_false_major=false_major_count > 0,
        raw_finding_count=len(raw_findings),
        unique_raw_finding_count=len(semantic_raw_keys),
        duplicate_finding_count=duplicate_count,
        duplicate_ratio=_ratio(
            duplicate_count,
            len(raw_findings),
            empty_value=0.0,
        ),
        true_positive_finding_ids=true_positive_ids,
        exact_true_positive_finding_ids=exact_true_positive_ids,
        alias_true_positive_finding_ids=alias_true_positive_ids,
        severity_mismatch_finding_ids=sorted(severity_mismatch_ids),
        true_positive_blocker_major_finding_ids=(true_positive_blocker_major_ids),
        false_negative_finding_ids=false_negative_ids,
        unique_true_positive_finding_ids=unique_true_positive_ids,
        unique_true_positive_blocker_major_finding_ids=(unique_blocker_major_ids),
    )


def summarize_reviewer_topology(
    scores: Iterable[ReviewerTopologyCaseScore],
    *,
    single_scores: Mapping[str, ReviewerTopologyCaseScore] | None = None,
) -> ReviewerTopologySummary:
    """汇总同一 topology 的 case score，并可相对 single 计算 unique TP。"""

    validated = [
        ReviewerTopologyCaseScore.model_validate(score.model_dump(mode="json")) for score in scores
    ]
    if not validated:
        raise ValueError("topology summary 至少需要一个 case score")
    topology = validated[0].topology
    if any(score.topology != topology for score in validated):
        raise ValueError("topology summary 不能混合不同 topology")
    _require_unique([score.case_id for score in validated], "case score case_id")

    unique_keys: list[str] = []
    unique_blocker_major_keys: list[str] = []
    for score in validated:
        if single_scores is None:
            unique_ids = score.unique_true_positive_finding_ids
            unique_major_ids = score.unique_true_positive_blocker_major_finding_ids
        else:
            baseline = single_scores.get(score.case_id)
            if baseline is None:
                raise ValueError(f"single_scores 缺少 case：{score.case_id}")
            baseline_ids = set(baseline.true_positive_finding_ids)
            unique_ids = sorted(set(score.true_positive_finding_ids) - baseline_ids)
            unique_major_ids = sorted(
                set(score.true_positive_blocker_major_finding_ids) - baseline_ids
            )
        unique_keys.extend(f"{score.case_id}:{item}" for item in unique_ids)
        unique_blocker_major_keys.extend(f"{score.case_id}:{item}" for item in unique_major_ids)

    expected = sum(score.expected_finding_count for score in validated)
    predicted = sum(score.predicted_finding_count for score in validated)
    true_positive = sum(score.true_positive_count for score in validated)
    exact_true_positive = sum(
        score.exact_true_positive_count for score in validated
    )
    alias_true_positive = sum(
        score.alias_true_positive_count for score in validated
    )
    severity_mismatch = sum(
        score.severity_mismatch_count for score in validated
    )
    false_positive = sum(score.false_positive_count for score in validated)
    false_negative = sum(score.false_negative_count for score in validated)
    expected_major = sum(score.expected_blocker_major_count for score in validated)
    true_positive_major = sum(score.true_positive_blocker_major_count for score in validated)
    raw = sum(score.raw_finding_count for score in validated)
    unique_raw = sum(score.unique_raw_finding_count for score in validated)
    duplicates = sum(score.duplicate_finding_count for score in validated)
    clean_scores = [
        score
        for score in validated
        if score.expected_finding_count == 0 and score.expected_verdict == "approve"
    ]
    verdict_correct = sum(score.verdict_correct for score in validated)

    return ReviewerTopologySummary(
        topology=topology,
        case_count=len(validated),
        expected_finding_count=expected,
        predicted_finding_count=predicted,
        true_positive_count=true_positive,
        exact_true_positive_count=exact_true_positive,
        alias_true_positive_count=alias_true_positive,
        severity_mismatch_count=severity_mismatch,
        severity_range_accuracy=_ratio(
            true_positive,
            true_positive + severity_mismatch,
            empty_value=1.0,
        ),
        false_positive_count=false_positive,
        false_negative_count=false_negative,
        finding_precision=_ratio(
            true_positive,
            predicted,
            empty_value=1.0 if expected == 0 else 0.0,
        ),
        finding_recall=_ratio(
            true_positive,
            expected,
            empty_value=1.0,
        ),
        expected_blocker_major_count=expected_major,
        true_positive_blocker_major_count=true_positive_major,
        blocker_major_recall=_ratio(
            true_positive_major,
            expected_major,
            empty_value=1.0,
        ),
        clean_case_count=len(clean_scores),
        clean_false_blocker_case_count=sum(score.clean_has_false_blocker for score in clean_scores),
        clean_false_major_case_count=sum(score.clean_has_false_major for score in clean_scores),
        clean_false_blocker_count=sum(score.clean_false_blocker_count for score in clean_scores),
        clean_false_major_count=sum(score.clean_false_major_count for score in clean_scores),
        raw_finding_count=raw,
        unique_raw_finding_count=unique_raw,
        duplicate_finding_count=duplicates,
        duplicate_ratio=_ratio(duplicates, raw, empty_value=0.0),
        verdict_correct_count=verdict_correct,
        verdict_accuracy=verdict_correct / len(validated),
        unique_true_positive_count=len(unique_keys),
        unique_true_positive_blocker_major_count=len(unique_blocker_major_keys),
        unique_true_positive_keys=sorted(unique_keys),
        unique_true_positive_blocker_major_keys=sorted(unique_blocker_major_keys),
    )


def _validate_case_score_binding(
    *,
    public_case: ReviewerTopologyPublicCase,
    ground_truth: ReviewerTopologyGroundTruthCase,
    results: list[ParallelReviewResult],
    aggregate: ParallelReviewAggregate,
) -> None:
    if public_case.case_id != ground_truth.case_id:
        raise ValueError("public case 与 ground truth 身份不一致")
    if (
        public_case.workspace_fixture_sha256 is not None
        and ground_truth.workspace_fixture_sha256 is not None
        and public_case.workspace_fixture_sha256 != ground_truth.workspace_fixture_sha256
    ):
        raise ValueError("public case 与 ground truth fixture hash 不一致")
    expected_evidence_sha256 = (
        public_case.evidence_snapshot_sha256 or ground_truth.evidence_snapshot_sha256
    )
    if (
        expected_evidence_sha256 is not None
        and aggregate.evidence_snapshot_sha256 != expected_evidence_sha256
    ):
        raise ValueError("aggregate 未绑定当前 case evidence snapshot")
    result_ids = sorted(result.result_id for result in results)
    if result_ids != aggregate.observed_result_ids:
        raise ValueError("results 与 aggregate observed_result_ids 不一致")
    if any(
        result.evidence_snapshot_sha256 != aggregate.evidence_snapshot_sha256
        or result.review_plan_id != aggregate.review_plan_id
        or result.run_id != aggregate.run_id
        or result.iteration != aggregate.iteration
        for result in results
    ):
        raise ValueError("reviewer result 未绑定当前 aggregate/case")


def _match_aggregate_findings(
    expected: list[ReviewerTopologyGroundTruthFinding],
    predicted: list[AggregatedReviewFinding],
) -> tuple[
    dict[str, str],
    list[str],
    dict[str, Literal["exact", "alias"]],
    list[str],
]:
    matched: dict[str, str] = {}
    match_kinds: dict[str, Literal["exact", "alias"]] = {}
    unmatched = list(predicted)
    for exact_only in (True, False):
        remaining: list[AggregatedReviewFinding] = []
        for finding in unmatched:
            candidates = _matching_truth_candidates(
                expected,
                finding,
                matched_finding_ids=set(matched),
                exact_only=exact_only,
            )
            if len(candidates) == 1:
                matched[candidates[0].finding_id] = finding.finding_id
                match_kinds[candidates[0].finding_id] = (
                    "exact" if exact_only else "alias"
                )
            else:
                remaining.append(finding)
        unmatched = remaining
    severity_mismatch_ids: list[str] = []
    for finding in unmatched:
        candidates: list[ReviewerTopologyGroundTruthFinding] = []
        for exact_only in (True, False):
            candidates = _matching_truth_candidates(
                expected,
                finding,
                matched_finding_ids=set(matched),
                exact_only=exact_only,
                require_allowed_severity=False,
            )
            if candidates:
                break
        if (
            len(candidates) == 1
            and not candidates[0].accepts_severity(finding.severity)
        ):
            severity_mismatch_ids.append(candidates[0].finding_id)
    return (
        matched,
        [finding.finding_id for finding in unmatched],
        match_kinds,
        sorted(set(severity_mismatch_ids)),
    )


def _semantic_finding_key(
    finding: ParallelReviewFinding,
    expected: list[ReviewerTopologyGroundTruthFinding],
) -> tuple[str, ...]:
    for exact_only in (True, False):
        matches = _matching_truth_candidates(
            expected,
            finding,
            matched_finding_ids=set(),
            exact_only=exact_only,
            require_allowed_severity=False,
        )
        if len(matches) == 1:
            return ("ground-truth", matches[0].finding_id)
        if exact_only and matches:
            break
    return (
        "output",
        finding.category,
        finding.rule_id,
        finding.normalized_path,
        finding.normalized_location,
    )


def _matching_truth_candidates(
    expected: list[ReviewerTopologyGroundTruthFinding],
    finding: ParallelReviewFinding | AggregatedReviewFinding,
    *,
    matched_finding_ids: set[str],
    exact_only: bool,
    require_allowed_severity: bool = True,
) -> list[ReviewerTopologyGroundTruthFinding]:
    exact_core_matches = [truth for truth in expected if truth.matches_exact_core_identity(finding)]
    if not exact_only and exact_core_matches:
        return []
    core_matches = [
        truth
        for truth in expected
        if truth.finding_id not in matched_finding_ids
        and (
            truth.matches_exact_core_identity(finding)
            if exact_only
            else truth.matches_core_identity(finding)
        )
        and (not require_allowed_severity or truth.accepts_severity(finding.severity))
    ]
    return _disambiguate_by_location(core_matches, finding)


def _disambiguate_by_location(
    candidates: list[ReviewerTopologyGroundTruthFinding],
    finding: ParallelReviewFinding | AggregatedReviewFinding,
) -> list[ReviewerTopologyGroundTruthFinding]:
    if len(candidates) <= 1:
        return candidates
    exact_matches = [truth for truth in candidates if truth.matches_exact_location(finding)]
    if exact_matches:
        return exact_matches
    return [truth for truth in candidates if truth.matches_location(finding)]


def _ground_truth_identities_overlap(
    left: ReviewerTopologyGroundTruthFinding,
    right: ReviewerTopologyGroundTruthFinding,
) -> bool:
    return (
        not {left.normalized_path, *left.path_aliases}.isdisjoint(
            {right.normalized_path, *right.path_aliases}
        )
        and not {left.category, *left.category_aliases}.isdisjoint(
            {right.category, *right.category_aliases}
        )
        and not {left.rule_id, *left.rule_aliases}.isdisjoint({right.rule_id, *right.rule_aliases})
        and not {
            left.normalized_location,
            *left.location_aliases,
        }.isdisjoint(
            {
                right.normalized_location,
                *right.location_aliases,
            }
        )
    )


def _parse_severity_range(value: object) -> tuple[object, object]:
    if isinstance(value, str):
        return value, value
    if isinstance(value, Mapping):
        return value.get("min"), value.get("max")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    raise ValueError("severity range 必须是 severity、[min, max] 或映射")


def _load_json_payload(path: str | Path) -> object:
    resolved = Path(path)
    return _decode_json_payload(resolved.read_bytes(), resolved)


def _decode_json_payload(raw: bytes, path: Path) -> object:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} 必须是 UTF-8 JSON") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 不是合法 JSON：{exc.msg}") from exc


def _normalize_expected_file_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.removeprefix("sha256:")
    return _normalize_sha256(normalized)


def _move_alias(
    payload: dict[object, object],
    alias: str,
    canonical: str,
) -> None:
    if alias not in payload:
        return
    if canonical in payload:
        raise ValueError(f"{alias} 与 {canonical} 不能同时提供")
    payload[canonical] = payload.pop(alias)


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} 必须唯一")


def _normalize_identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} 必须是安全标识符")
    return normalized


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError("sha256 必须是 64 位小写十六进制")
    return normalized


def _normalize_finding_component(value: str) -> str:
    normalized = re.sub(r"\s+", "-", value.strip().lower())
    if not _FINDING_COMPONENT_PATTERN.fullmatch(normalized):
        raise ValueError("finding component 包含非法字符")
    return normalized


def _normalize_repo_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if (
        not normalized
        or len(normalized) > 500
        or path.is_absolute()
        or normalized == "."
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
        or "\0" in normalized
    ):
        raise ValueError("finding path 必须是安全的仓库相对路径")
    return path.as_posix()


def _normalize_location(value: str) -> str:
    normalized = re.sub(r"\s+", "", value.strip().lower())
    if not normalized or not _LOCATION_PATTERN.fullmatch(normalized):
        raise ValueError("finding location 包含非法字符")
    return normalized


def _normalize_location_alias(value: str) -> str:
    normalized = re.sub(r"\s+", "", value.strip().lower())
    if not normalized or len(normalized) > 200 or "\0" in normalized:
        raise ValueError("finding location alias 包含非法字符")
    return normalized


def _ratio(numerator: int, denominator: int, *, empty_value: float) -> float:
    if denominator == 0:
        return empty_value
    return numerator / denominator


# 保留简短别名，方便预注册脚本使用而不复制评测实现。
PublicDatasetCase = ReviewerTopologyPublicCase
PublicDataset = ReviewerTopologyPublicDataset
GroundTruthFinding = ReviewerTopologyGroundTruthFinding
GroundTruthCase = ReviewerTopologyGroundTruthCase
GroundTruthDataset = ReviewerTopologyGroundTruthDataset
TopologyCaseScore = ReviewerTopologyCaseScore
TopologySummary = ReviewerTopologySummary
score_topology_case = score_reviewer_topology_case
summarize_topology = summarize_reviewer_topology
