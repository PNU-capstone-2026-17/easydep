"""사이징 규칙 모델.

capacitykb의 `Constraint`와 같은 모양을 쓴다 — `kind`로 갈리고 `metric`·`value`·`unit`을
갖는다. 규칙의 종류가 다섯이라 각각 클래스를 만들면 소비자가 다섯 갈래로 갈린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kbcommon.basis import basis_of, is_fact

#: 규칙 종류.
RESERVED_IPS = "reserved_ips"
MINIMUM = "minimum"
REQUIRED_COUNT = "required_count"
REFERENCE_POINT = "reference_point"
PRESET = "preset"
KINDS = (RESERVED_IPS, MINIMUM, REQUIRED_COUNT, REFERENCE_POINT, PRESET)


@dataclass(frozen=True)
class Rule:
    """사이징 규칙 하나.

    `scope`는 이 규칙이 **어디에 걸리는가**다 — 프로바이더(`aws`), 개념
    (`k8s-node`), 워크로드(`workload:web`) 중 하나다.
    """

    id: str
    kind: str
    scope: str
    metric: str
    value: float | int | str
    evidence: str
    unit: str | None = None
    note: str | None = None
    #: **원본이 스스로 단 경고.** 값과 떼면 테스트용 숫자가 권장값이 된다.
    caveat: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind: {self.kind!r} (allowed: {KINDS})")

    @property
    def basis(self) -> str:
        return basis_of(self.evidence)

    @property
    def is_fact(self) -> bool:
        return is_fact(self.basis)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "scope": self.scope,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "evidence": self.evidence,
            "note": self.note,
            "caveat": self.caveat,
        }

    @classmethod
    def from_dict(cls, row: dict) -> Rule:
        return cls(
            id=row["id"],
            kind=row["kind"],
            scope=row["scope"],
            metric=row["metric"],
            value=row["value"],
            evidence=row["evidence"],
            unit=row.get("unit"),
            note=row.get("note"),
            caveat=row.get("caveat"),
        )


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)

    def to_dict(self) -> dict:
        return {
            "rules": [r.to_dict() for r in sorted(self.rules, key=lambda r: r.id)],
            "_coverage": self.coverage,
            "_source": self.provenance,
        }


def usable_ips(prefix_length: int, reserved: int) -> int:
    """서브넷 CIDR의 **실제로 쓸 수 있는** IP 수.

    `2^(32−prefix) − 예약`. 예약 수는 프로바이더마다 다르고(AWS·Azure 5, alibaba 4)
    **원본이 적어 둔 것만** 쓴다 — 모르면 이 함수를 부르지 않는다.

    프리픽스가 32에 가까우면 예약이 전체보다 커진다. 그때는 0을 준다 — 음수를
    돌려주면 "마이너스 3대 띄울 수 있다"는 답이 나온다.
    """
    if not 0 <= prefix_length <= 32:
        raise ValueError(f"Prefix length out of range: {prefix_length}")
    total = 1 << (32 - prefix_length)
    return max(0, total - reserved)
