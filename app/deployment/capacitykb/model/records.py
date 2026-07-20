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

from kbcommon.basis import basis_of, is_fact

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

    basis: str = ""
    """**원본이 그렇게 적었는가(`stated`), 우리가 짐작했는가(`inferred`).**

    evidence 라벨에서 정해지므로 비워 두면 채워진다(`kbcommon/basis.py`).
    예전의 `confidence`(0.5~1.0)를 대신한다 — 그 숫자는 척도의 정의가 없었다.
    """

    value_type: str | None = None
    unit: str | None = None
    conditional: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.basis:
            object.__setattr__(self, "basis", basis_of(self.evidence))

    @property
    def key(self) -> tuple[str, str, str]:
        """중복 판정 키. 같은 키면 **사실인 쪽**을 남긴다."""
        return (self.type_id, self.property, self.kind)

    @property
    def is_fact(self) -> bool:
        return is_fact(self.basis)

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
            "basis": self.basis,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Constraint:
        return cls(
            type_id=data["type_id"],
            property=data["property"],
            kind=data["kind"],
            value=data["value"],
            evidence=data["evidence"],
            basis=data.get("basis", ""),
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

    basis: str = ""
    """근거의 성격. evidence에서 정해진다 — `kbcommon/basis.py` 참고."""

    scope: str | None = None
    default: float | str | None = None
    maximum: float | str | None = None
    unit: str | None = None
    type_id: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.basis:
            object.__setattr__(self, "basis", basis_of(self.evidence))

    @property
    def key(self) -> tuple[str, str | None, str]:
        return (self.provider, self.scope, self.name)

    @property
    def is_fact(self) -> bool:
        return is_fact(self.basis)

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
            "basis": self.basis,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Quota:
        return cls(
            provider=data["provider"],
            name=data["name"],
            source_doc=data["source_doc"],
            evidence=data["evidence"],
            basis=data.get("basis", ""),
            scope=data.get("scope"),
            default=data.get("default"),
            maximum=data.get("maximum"),
            unit=data.get("unit"),
            type_id=data.get("type_id"),
            note=data.get("note"),
        )


@dataclass
class CapacitySet:
    """제약/쿼터 레코드 컨테이너. 같은 키는 **사실인 쪽**을 유지한다."""

    constraints: list[Constraint] = field(default_factory=list)
    quotas: list[Quota] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    """어느 원본에서 나왔는지 (`kbcommon.fetch.describe_source*`). `_source`로 직렬화된다.

    비어 있으면 "입력을 모른다"는 뜻이고, 그러면 어떤 수치도 재현·반증할 수 없다.
    """

    _c_index: dict[tuple[str, str, str], int] = field(default_factory=dict, repr=False)
    _q_index: dict[tuple[str, str | None, str], int] = field(
        default_factory=dict, repr=False
    )

    def add_constraint(self, constraint: Constraint) -> None:
        """제약을 추가한다. 중복 키는 **사실인 쪽**만 남긴다.

        예전엔 confidence가 높은 쪽이었는데 그 숫자는 정의가 없어 우열이 임의였다.
        지금 기준은 하나다 — 원본이 명시한 것이 짐작을 이긴다.
        """
        existing = self._c_index.get(constraint.key)
        if existing is None:
            self._c_index[constraint.key] = len(self.constraints)
            self.constraints.append(constraint)
        elif constraint.is_fact and not self.constraints[existing].is_fact:
            self.constraints[existing] = constraint

    def add_quota(self, quota: Quota) -> None:
        existing = self._q_index.get(quota.key)
        if existing is None:
            self._q_index[quota.key] = len(self.quotas)
            self.quotas.append(quota)
        elif quota.is_fact and not self.quotas[existing].is_fact:
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
        seen = {(p.get("source"), p.get("sha256")) for p in self.provenance}
        for p in other.provenance:
            if (p.get("source"), p.get("sha256")) not in seen:
                self.provenance.append(p)
                seen.add((p.get("source"), p.get("sha256")))

    def to_dict(self) -> dict:
        out: dict = {
            "constraints": [c.to_dict() for c in self.constraints],
            "quotas": [q.to_dict() for q in self.quotas],
        }
        if self.provenance:
            out["_source"] = self.provenance
        return out

    @classmethod
    def from_dict(cls, data: dict) -> CapacitySet:
        result = cls()
        for item in data.get("constraints", []):
            result.add_constraint(Constraint.from_dict(item))
        for item in data.get("quotas", []):
            result.add_quota(Quota.from_dict(item))
        result.provenance = list(data.get("_source") or [])
        return result

    def validate(self) -> None:
        """직렬화 결과를 번들 스키마로 검증한다. 위반 시 ValidationError."""
        jsonschema.validate(self.to_dict(), _schema())

    def save(self, path: Path):
        """검증 후 원자적으로 저장한다.

        스키마(레코드 하나의 형태)와 불변식(레코드 사이의 정합성)을 둘 다 통과해야
        쓴다. 예전엔 여기서 바로 `write_text`를 해서, 쓰다 끊기면 잘린 JSON이 남고
        이후 모든 로드가 죽었다 — `kbcommon/artifact.py`가 그래서 있다.

        Returns:
            불변식 결과. `report` 등급 위반은 **호출자가 알려야 한다.**
        """
        from capacitykb.invariants import INVARIANTS
        from kbcommon.artifact import write_dataset

        return write_dataset(path, self.to_dict(), _schema(), INVARIANTS)

    @classmethod
    def load(cls, path: Path) -> CapacitySet:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
