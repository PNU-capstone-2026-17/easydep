"""요구사항에서 도출한 capability의 보정된 선택적 판정 계약이다.

LLM의 자신감 주장을 수락 근거로 사용하지 않는다. 반복 proposal의 일치도를 원점수로
삼고, 검토자 라벨 개발 집합으로 보정한 뒤 동결 임계값을 적용한다.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "easydep-capability-threshold/v1"
DEFAULT_POLICY = Path(__file__).parents[1] / "knowledge" / "capability-threshold.json"
REALIZATION_CHANGING_FIELDS = frozenset({
    "provider", "region", "security", "availability", "scale", "budget"
})
MODELED_DEPENDENCY_CAPABILITY_IDS = frozenset({
    "persistent-block-storage",
    "load-balanced-ingress",
})
OUT_OF_SCOPE_DEPENDENCY_CAPABILITY_IDS = frozenset({
    "https-ingress",
    "https-load-balanced-ingress",
})
RECOGNIZED_DEPENDENCY_CAPABILITY_IDS = (
    MODELED_DEPENDENCY_CAPABILITY_IDS | OUT_OF_SCOPE_DEPENDENCY_CAPABILITY_IDS
)
_LINK_TOKEN = re.compile(r"[a-z0-9]+")


def link_dependency_capability(
    key: str, role: str, evidence_spans: Iterable[str] = ()
) -> str | None:
    """열린 need를 인식 가능한 안정 capability id 하나 또는 NIL에 보수적으로 연결한다."""
    observed = set(_LINK_TOKEN.findall(
        " ".join((key, role, *evidence_spans)).casefold().replace("_", " ")
    ))
    token_sets = {
        capability_id: set(_LINK_TOKEN.findall(capability_id))
        for capability_id in RECOGNIZED_DEPENDENCY_CAPABILITY_IDS
    }
    matched = {
        capability_id: tokens
        for capability_id, tokens in token_sets.items()
        if tokens <= observed
    }
    maximal = [
        capability_id
        for capability_id, tokens in matched.items()
        if not any(
            tokens < other_tokens
            for other_id, other_tokens in matched.items()
            if other_id != capability_id
        )
    ]
    return maximal[0] if len(maximal) == 1 else None


@dataclass(frozen=True)
class CalibrationPoint:
    """원점수와 검토자 정답 여부를 묶는 불변 보정 표본이다."""

    raw_score: float
    correct: bool


class CalibrationBlock(TypedDict):
    """동결 정책의 단조 보정 구간 하나다."""

    low: float
    high: float
    value: float
    count: int


class PolicyQualification(TypedDict):
    """자동 수락 임계값을 동결한 검토 표본 통계다."""

    selected: int
    correct: int
    precision: float
    wilsonLower95: float


class PolicyNoInferenceQualification(TypedDict):
    """추론 표본이 없어 자동 수락을 비활성화한 동결 근거다."""

    reason: str
    proposalCount: int
    inferredCount: int


class CapabilityPolicy(TypedDict):
    """capability 자동 수락 판단에 필요한 동결 정책 계약이다."""

    schemaVersion: str
    status: str
    version: str
    autoAcceptEnabled: bool
    acceptThreshold: float | None
    mapping: list[CalibrationBlock]
    method: NotRequired[str]
    sampleCount: NotRequired[int]
    targetPrecision: NotRequired[float]
    minimumWilsonLower95: NotRequired[float]
    qualification: NotRequired[
        PolicyQualification | PolicyNoInferenceQualification | None
    ]


class _CalibrationBlockModel(BaseModel):
    """보정 구간의 JSON 구조와 값 범위를 검증한다."""

    model_config = ConfigDict(extra="forbid", strict=True)

    low: float
    high: float
    value: float = Field(ge=0.0, le=1.0)
    count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_interval(self) -> _CalibrationBlockModel:
        """구간 하한이 상한보다 크지 않은지 확인한다."""
        if self.low > self.high:
            raise ValueError("capability calibration block low exceeds high")
        return self


class _CapabilityPolicyModel(BaseModel):
    """외부 JSON 정책을 판정 코드에 들이기 전 검증하는 모델이다."""

    model_config = ConfigDict(extra="allow", strict=True)

    schema_version: Literal["easydep-capability-threshold/v1"] = Field(
        alias="schemaVersion"
    )
    status: str = Field(min_length=1)
    version: str = Field(min_length=1)
    auto_accept_enabled: bool = Field(alias="autoAcceptEnabled")
    accept_threshold: float | None = Field(alias="acceptThreshold", ge=0.0, le=1.0)
    mapping: list[_CalibrationBlockModel]

    @model_validator(mode="after")
    def validate_acceptance(self) -> _CapabilityPolicyModel:
        """자동 수락 정책이면 임계값과 보정 구간이 모두 있는지 확인한다."""
        if self.auto_accept_enabled and (
            self.accept_threshold is None or not self.mapping
        ):
            raise ValueError(
                "enabled capability policy requires threshold and calibration mapping"
            )
        return self


class _PavaBlock(TypedDict):
    """PAVA 병합 중 사용하는 가변 누적 구간이다."""

    low: float
    high: float
    correct: int
    count: int


def validate_policy(policy: Mapping[str, object]) -> CapabilityPolicy:
    """capability 정책의 필수 키·타입·보정 구간을 검증한다.

    Args:
        policy: JSON에서 읽었거나 호출자가 전달한 정책 mapping이다.

    Returns:
        기존 키와 값을 바꾸지 않은 검증 완료 정책이다.

    Notes:
        extra metadata는 허용해 기존 정책 JSON을 보존하지만, 판정에 필요한 필수 구조는
        엄격하게 검사한다.
    """
    _CapabilityPolicyModel.model_validate(policy)
    return cast(CapabilityPolicy, dict(policy))


def _pava(points: Iterable[CalibrationPoint]) -> list[CalibrationBlock]:
    """인접 위반 구간을 합쳐 결정론적 단조 보정 mapping을 만든다."""
    grouped: dict[float, list[int]] = {}
    for point in points:
        grouped.setdefault(float(point.raw_score), []).append(int(point.correct))
    blocks: list[_PavaBlock] = [
        {"low": score, "high": score, "correct": sum(values), "count": len(values)}
        for score, values in sorted(grouped.items())
    ]
    index = 0
    while index < len(blocks) - 1:
        left, right = blocks[index], blocks[index + 1]
        left_mean = float(left["correct"]) / int(left["count"])
        right_mean = float(right["correct"]) / int(right["count"])
        if left_mean <= right_mean:
            index += 1
            continue
        blocks[index : index + 2] = [{
            "low": left["low"],
            "high": right["high"],
            "correct": int(left["correct"]) + int(right["correct"]),
            "count": int(left["count"]) + int(right["count"]),
        }]
        index = max(0, index - 1)
    return [{
        "low": float(block["low"]),
        "high": float(block["high"]),
        "value": float(block["correct"]) / int(block["count"]),
        "count": int(block["count"]),
    } for block in blocks]


def calibrated_score(
    raw_score: float, mapping: Iterable[CalibrationBlock]
) -> float | None:
    """원점수가 속한 동결 보정 구간의 점수를 반환한다."""
    for block in mapping:
        if float(block["low"]) <= raw_score <= float(block["high"]):
            return float(block["value"])
    return None


def wilson_lower(correct: int, count: int, z: float = 1.959963984540054) -> float:
    """이항 정답률의 Wilson 하한을 결정론적으로 계산한다."""
    if count <= 0:
        return 0.0
    proportion = correct / count
    denominator = 1 + z * z / count
    centre = proportion + z * z / (2 * count)
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * count)) / count)
    return (centre - margin) / denominator


def fit_policy(points: Iterable[CalibrationPoint], *, version: str) -> CapabilityPolicy:
    """검토 표본으로 단조 보정 mapping과 수락 임계값 정책을 적합한다."""
    observations = list(points)
    mapping = _pava(observations)
    candidates = sorted({block["value"] for block in mapping})
    threshold: float | None = None
    qualification: PolicyQualification | None = None
    scored = [(calibrated_score(item.raw_score, mapping), item.correct) for item in observations]
    for candidate in candidates:
        selected = [correct for score, correct in scored if score is not None and score >= candidate]
        if not selected:
            continue
        correct = sum(selected)
        precision = correct / len(selected)
        lower = wilson_lower(correct, len(selected))
        if precision >= 0.90 and lower >= 0.80:
            threshold = float(candidate)
            qualification = {
                "selected": len(selected), "correct": correct,
                "precision": precision, "wilsonLower95": lower,
            }
            break
    policy: CapabilityPolicy = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "frozen",
        "version": version,
        "method": "isotonic-pava",
        "sampleCount": len(observations),
        "targetPrecision": 0.90,
        "minimumWilsonLower95": 0.80,
        "autoAcceptEnabled": threshold is not None,
        "acceptThreshold": threshold,
        "mapping": mapping,
        "qualification": qualification,
    }
    return validate_policy(policy)


def load_policy(path: Path = DEFAULT_POLICY) -> CapabilityPolicy:
    """동결 capability 정책을 읽고 부재 시 명시적 미적합 정책을 반환한다."""
    if not path.is_file():
        return validate_policy({
            "schemaVersion": SCHEMA_VERSION,
            "status": "unfitted",
            "version": "unfitted",
            "autoAcceptEnabled": False,
            "acceptThreshold": None,
            "mapping": [],
        })
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("capability threshold policy must be a JSON object")
    return validate_policy(cast(dict[str, object], raw))


def decide(
    *, raw_score: float, origin: str, evidence_valid: bool,
    unresolved_fields: Iterable[str], impossible: bool = False,
    out_of_scope: bool = False,
    policy: CapabilityPolicy | Mapping[str, object] | None = None,
) -> tuple[str, str, float | None]:
    """보정된 선택 예측보다 먼저 hard safety gate를 적용한다."""
    unresolved = {str(item) for item in unresolved_fields}
    if impossible:
        return "abstained", "logically-impossible", None
    if out_of_scope:
        return "abstained", "model-out-of-scope", None
    if not evidence_valid:
        return "needsQuestion", "missing-or-ungrounded-evidence", None
    if unresolved & REALIZATION_CHANGING_FIELDS:
        return "needsQuestion", "realization-changing-ambiguity", None
    if origin == "explicit":
        return "accepted", "explicit-grounded-constraint", 1.0
    selected_policy = validate_policy(policy) if policy is not None else None
    if selected_policy is None:
        selected_policy = load_policy()
    score = calibrated_score(raw_score, selected_policy["mapping"])
    threshold = selected_policy["acceptThreshold"]
    if (
        selected_policy["autoAcceptEnabled"] is True
        and score is not None and threshold is not None and score >= float(threshold)
    ):
        return "accepted", "calibrated-threshold-met", score
    return "needsQuestion", "calibrated-threshold-not-met", score


def accepted_needs(needs: dict[str, Any]) -> dict[str, Any]:
    """수락 판정된 capability만 downstream용 사전으로 투영한다."""
    return {
        key: value for key, value in needs.items()
        if isinstance(value, dict) and value.get("decision", "accepted") == "accepted"
    }


def requires_persistent_storage(needs: dict[str, Any]) -> bool:
    """극성을 잃지 않고 현재 지원하는 영속 storage capability를 해석한다.

    ``required``는 Boolean metadata의 참·거짓이 아니라 제약 강도를 뜻한다. 따라서
    ``persistent_application_disk: false``가 명시되면 그 금지가 필수인 경우에도 disk
    realization을 만들지 않는다.
    """
    accepted = accepted_needs(needs)
    semantic_need = next(
        (
            value
            for value in accepted.values()
            if isinstance((value.get("metadata") or {}).get("applicationState"), dict)
            and (value.get("metadata") or {})["applicationState"].get("durability")
            == "persistent"
        ),
        None,
    )
    need = next(
        (
            value
            for value in accepted.values()
            if "persistent-block-storage"
            in (value.get("dependencyCapabilityIds") or [])
        ),
        semantic_need or accepted.get("persistent_storage") or {},
    )
    if need.get("required") is not True:
        return False
    metadata = need.get("metadata") or {}
    return metadata.get("persistent_application_disk") is not False


def requires_load_balanced_ingress(needs: dict[str, Any]) -> bool:
    """수락된 need가 모델링 범위의 load-balanced ingress를 요구하는지 반환한다."""
    for need in accepted_needs(needs).values():
        capability_ids = need.get("dependencyCapabilityIds") or []
        if "load-balanced-ingress" not in set(capability_ids):
            continue
        if need.get("required") is not True:
            continue
        return True
    return False
