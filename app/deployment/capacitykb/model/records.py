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

    backend: str | None = None
    """**이 레코드를 만들어낸 상류 파이프라인.** 값은 소스가 쓰는 말 그대로.

    같은 `evidence` 라벨이라도 신선도가 다를 수 있어서 둔다. GCP(KCC)가 실례다 —
    CRD가 스스로 라벨로 밝히는 백엔드가 셋이고, 그중 `tf2crd`(우리 GCP 제약의 55%)는
    **2023-09-26에 나온 terraform-provider-google-beta 4.84.0을 벤더링한 것**에서
    스키마를 뽑는다. 실측으로 허용값이 낡은 리소스 5/5가 전부 tf2crd였다.

    이게 없으면 4,421건이 전부 같은 얼굴이라 **절반이 2년 8개월 묵었다는 걸
    사용자가 알 방법이 없다.** 등급(`tier`)이 아니라 **사실**을 적는 칸이다 —
    위아래를 이름에 박으면 나중에 판단이 바뀔 때 이름이 거짓이 된다.

    값 예: `direct` | `tf2crd` | `dcl2crd` (KCC). 안 쓰는 소스는 None.
    """

    conditions: tuple[dict, ...] = ()
    """**이 제약이 언제 적용되는가.** 조건들의 **논리곱**(전부 성립해야 적용).

    각 조건은 `{"property": ..., "op": ..., "value": ...}`. `op`는 두 가지다:

    - `eq` — 그 속성이 이 값일 때 (`VolumeType=gp2`, `Region=us-east-1`)
    - `matches` — 그 속성이 이 정규식에 걸릴 때 (`EngineVersion` 같은 버전 표현)

    비어 있으면 무조건이다.

    필요한 이유는 **봉투 붕괴** 때문이다. EBS 볼륨 크기 한도는 종류마다 다른데
    (gp2 16,384 / gp3 65,536 / standard 1,024 GiB), 이걸 min/max 한 쌍으로 뭉개면
    최소 중 최소·최대 중 최대를 취해 **어떤 실제 설정에도 해당하지 않는 범위**가 된다.
    `standard` 볼륨에 5,000 GiB는 불가능한데 그 봉투는 통과시킨다.

    **왜 단일 조건에서 목록으로 넓혔나.** 설계 문서에 "필요해지면 그때 넓힌다"고
    적어 뒀는데 그때가 왔다 — cfn-lint의 RDS 인스턴스 클래스는 조건이 둘이다
    (`Engine` **그리고** `EngineVersion`, 938블록). 게다가 둘째는 등호가 아니라
    패턴이라 `op`도 함께 넓혀야 했다.
    """

    value_type: str | None = None
    unit: str | None = None
    conditional: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.basis:
            object.__setattr__(self, "basis", basis_of(self.evidence))

    @property
    def key(self) -> tuple:
        """중복 판정 키. 같은 키면 **사실인 쪽**을 남긴다.

        조건이 키에 들어가야 한다 — 안 그러면 볼륨 종류별 6개 레코드가 하나로
        접혀서, 조건부 제약을 담으려던 것이 도로 봉투가 된다.
        """
        if not self.conditions:
            return (self.type_id, self.property, self.kind)
        # 조건 순서가 달라도 같은 제약이므로 정렬해서 키를 만든다.
        parts = sorted(
            (c.get("property"), c.get("op"), json.dumps(c.get("value"), sort_keys=True))
            for c in self.conditions
        )
        return (self.type_id, self.property, self.kind, tuple(parts))

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
            "backend": self.backend,
            "conditions": list(self.conditions),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Constraint:
        if "condition" in data:
            # **조용히 무시하면 봉투가 도로 붙는다.** 단일 `condition`을 목록
            # `conditions`로 넓힐 때 실제로 겪었다 — 낡은 산출물을 그대로 읽었더니
            # 조건이 전부 사라져 리전별 38개 레코드가 중복 키로 하나에 접혔다.
            # 값이 틀린 게 아니라 **어떤 실제 설정에도 해당하지 않는 범위**가 된다.
            raise ValueError(
                "낡은 산출물입니다 — `condition`(단일)은 `conditions`(목록)으로 "
                "바뀌었습니다. `python -m capacitykb build` 로 다시 만드세요. "
                "그냥 읽으면 조건이 사라져 봉투 붕괴가 됩니다."
            )
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
            backend=data.get("backend"),
            conditions=tuple(data.get("conditions") or ()),
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

    coverage: list[dict] = field(default_factory=list)
    """**무엇을 훑었는가.** `_coverage`로 직렬화된다.

    이게 없으면 "제약이 없다"와 "우리가 안 봤다"가 구분되지 않는다. 실측상 그 차이가
    크다 — graphkb가 아는 벤더 타입 5,547종 중 3,634종에 제약 레코드가 하나도 없고,
    GCP는 **527종 전부**가 그렇다(capacitykb가 GCP를 아예 안 읽는다). 커버리지가
    없으면 "GCP 디스크 크기 제한"을 물었을 때 "제약 없음"이라고 답하게 된다.

    항목 하나가 한 번의 수집 범위다:
        {"provider": "azure", "scope": ["microsoft.network", ...], "types": 278}

    `type_ids`를 함께 적으면 **제약이 하나도 안 나온 타입도 이름으로 찾을 수 있다.**
    이게 없으면 훑긴 훑었는데 아무것도 안 나온 타입이 "타입을 찾을 수 없습니다"로
    답해진다 — 실재하고 우리가 읽기까지 한 타입인데 없다고 말하는 셈이라,
    에이전트가 그대로 옮기면 거짓말이 된다. GCP에서 실제로 39종이 그랬다.
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

    def covers(self, type_id: str) -> bool:
        """이 타입이 **수집 범위 안**이었는가.

        범위 안인데 제약이 없으면 "제약 없음"이고, 범위 밖이면 "모른다"다.
        기록이 아예 없으면(옛 산출물) 판단하지 않고 True로 둔다 — 없는 정보를
        근거로 "안 봤다"고 단정할 수는 없다.
        """
        if not self.coverage:
            return True
        provider, _, rest = type_id.partition("::")
        for entry in self.coverage:
            if entry.get("provider") != provider:
                continue
            scope = entry.get("scope")
            if not scope:
                return True  # 그 프로바이더 전체를 훑었다
            head = rest.split("/", 1)[0].lower()
            if any(head == s.lower() for s in scope):
                return True
        return False

    def merge(self, other: CapacitySet) -> None:
        for constraint in other.constraints:
            self.add_constraint(constraint)
        for quota in other.quotas:
            self.add_quota(quota)
        for entry in other.coverage:
            if entry not in self.coverage:
                self.coverage.append(entry)
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
        if self.coverage:
            out["_coverage"] = self.coverage
        return out

    @classmethod
    def from_dict(cls, data: dict) -> CapacitySet:
        result = cls()
        for item in data.get("constraints", []):
            result.add_constraint(Constraint.from_dict(item))
        for item in data.get("quotas", []):
            result.add_quota(Quota.from_dict(item))
        result.provenance = list(data.get("_source") or [])
        result.coverage = list(data.get("_coverage") or [])
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
        from kbcommon.artifact import load_json

        return cls.from_dict(load_json(path))
