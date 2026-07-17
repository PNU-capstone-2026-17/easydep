"""공통 데이터 모델: 속성 제약(Constraint) / 서비스 쿼터(Quota).

**narrow 모델** — (type_id, property, kind) 하나당 레코드 1개.
근거: 같은 프로퍼티라도 제약 종류별로 근거·신뢰도가 다르다.
예) AWS::Lambda::Function.Timeout 은 min=1이 스키마 필드(신뢰도 1.0)인데
max=900은 설명문에서 추출(0.8) → 프로퍼티당 1행(wide) 모델로는 표현 불가.

`type_id`는 graphkb의 노드 id와 **동일한 규약**(`aws::AWS::EC2::Volume`,
`azure::Microsoft.Network/virtualNetworks`)을 쓴다. 두 지식베이스는 코드가
분리돼 있지만 이 규약 덕분에 질의 시점에 조인할 수 있다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import jsonschema

ConstraintKind = Literal[
    "min",
    "max",
    "min_length",
    "max_length",
    "min_items",
    "max_items",
    "pattern",
    "enum",
    "default",
    "required",
    "mutability",
]
Mutability = Literal["create_only", "conditional_create_only", "read_only"]


@lru_cache(maxsize=1)
def _schema() -> dict:
    """번들된 JSON Schema를 로드한다."""
    path = Path(__file__).with_name("schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class Constraint:
    """리소스 타입의 속성 하나에 걸린 제약 하나."""

    type_id: str
    property: str
    kind: str
    value: Any
    evidence: str
    confidence: float
    value_type: str | None = None
    unit: str | None = None
    conditional: bool = False
    note: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        """중복 판정 키. 같은 키면 confidence가 높은 쪽만 남긴다."""
        return (self.type_id, self.property, self.kind)

    def to_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "property": self.property,
            "kind": self.kind,
            "value": self.value,
            "value_type": self.value_type,
            "unit": self.unit,
            "conditional": self.conditional,
            "note": self.note,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Constraint:
        return cls(
            type_id=data["type_id"],
            property=data["property"],
            kind=data["kind"],
            value=data["value"],
            evidence=data["evidence"],
            confidence=data["confidence"],
            value_type=data.get("value_type"),
            unit=data.get("unit"),
            conditional=data.get("conditional", False),
            note=data.get("note"),
        )


@dataclass(frozen=True, slots=True)
class Quota:
    """계정/구독/리전 등 스코프 단위의 상한 (예: vNet당 서브넷 3,000개)."""

    provider: str
    name: str
    source_doc: str
    evidence: str
    confidence: float
    scope: str | None = None
    default: float | str | None = None
    maximum: float | str | None = None
    unit: str | None = None
    type_id: str | None = None
    note: str | None = None

    @property
    def key(self) -> tuple[str, str | None, str]:
        return (self.provider, self.scope, self.name)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "name": self.name,
            "scope": self.scope,
            "default": self.default,
            "maximum": self.maximum,
            "unit": self.unit,
            "type_id": self.type_id,
            "source_doc": self.source_doc,
            "note": self.note,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Quota:
        return cls(
            provider=data["provider"],
            name=data["name"],
            source_doc=data["source_doc"],
            evidence=data["evidence"],
            confidence=data["confidence"],
            scope=data.get("scope"),
            default=data.get("default"),
            maximum=data.get("maximum"),
            unit=data.get("unit"),
            type_id=data.get("type_id"),
            note=data.get("note"),
        )


@dataclass
class CapacitySet:
    """제약/쿼터 레코드 컨테이너. 같은 키는 confidence 높은 쪽을 유지한다."""

    constraints: list[Constraint] = field(default_factory=list)
    quotas: list[Quota] = field(default_factory=list)
    _c_index: dict[tuple[str, str, str], int] = field(default_factory=dict, repr=False)
    _q_index: dict[tuple[str, str | None, str], int] = field(
        default_factory=dict, repr=False
    )

    def add_constraint(self, constraint: Constraint) -> None:
        """제약을 추가한다. 중복 키는 confidence 높은 쪽만 남긴다."""
        existing = self._c_index.get(constraint.key)
        if existing is None:
            self._c_index[constraint.key] = len(self.constraints)
            self.constraints.append(constraint)
        elif constraint.confidence > self.constraints[existing].confidence:
            self.constraints[existing] = constraint

    def add_quota(self, quota: Quota) -> None:
        existing = self._q_index.get(quota.key)
        if existing is None:
            self._q_index[quota.key] = len(self.quotas)
            self.quotas.append(quota)
        elif quota.confidence > self.quotas[existing].confidence:
            self.quotas[existing] = quota

    def has_constraint(self, type_id: str, prop: str, kind: str) -> bool:
        """이미 같은 키의 제약이 있는지 (산문 추출의 R1 방어에 사용)."""
        return (type_id, prop, kind) in self._c_index

    def get_constraint(self, type_id: str, prop: str, kind: str) -> Constraint | None:
        index = self._c_index.get((type_id, prop, kind))
        return None if index is None else self.constraints[index]

    def for_type(self, type_id: str) -> list[Constraint]:
        return [c for c in self.constraints if c.type_id == type_id]

    def for_property(self, type_id: str, prop: str) -> list[Constraint]:
        return [
            c for c in self.constraints if c.type_id == type_id and c.property == prop
        ]

    def merge(self, other: CapacitySet) -> None:
        for constraint in other.constraints:
            self.add_constraint(constraint)
        for quota in other.quotas:
            self.add_quota(quota)

    def to_dict(self) -> dict:
        return {
            "constraints": [c.to_dict() for c in self.constraints],
            "quotas": [q.to_dict() for q in self.quotas],
        }

    @classmethod
    def from_dict(cls, data: dict) -> CapacitySet:
        result = cls()
        for item in data.get("constraints", []):
            result.add_constraint(Constraint.from_dict(item))
        for item in data.get("quotas", []):
            result.add_quota(Quota.from_dict(item))
        return result

    def validate(self) -> None:
        """직렬화 결과를 번들 스키마로 검증한다. 위반 시 ValidationError."""
        jsonschema.validate(self.to_dict(), _schema())

    def save(self, path: Path) -> None:
        """검증 후 JSON으로 저장한다."""
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        path.write_text(payload + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> CapacitySet:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
