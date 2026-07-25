"""번들 레코드 모델.

두 모양이 있다. **같은 질문에 다른 방식으로 답하므로** 한 파일에 두되 섞지 않는다.

    Bundle       이름 붙은 리소스 군 (AVM 모듈 · tumblebug 템플릿 · AWS 패턴)
    Companion    앵커 타입 하나에 대한 동시 출현 통계 (코퍼스 실측)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kbcommon.basis import OBSERVED, basis_of, is_fact

#: 구성원 등급. 순서가 곧 강도다.
ALWAYS = "always"
REQUIRED = "required"
OPTIONAL = "optional"
TIERS = (ALWAYS, REQUIRED, OPTIONAL)

_TIER_RANK = {ALWAYS: 0, REQUIRED: 1, OPTIONAL: 2}


@dataclass(frozen=True)
class Member:
    """번들 구성원 하나. **한 타입은 한 줄이고, 개수는 `count`가 말한다.**

    `count`가 없던 시절 tumblebug 파서는 VM 28대짜리 템플릿을 `core::vm` 28줄로
    담았다. 화면에 같은 줄이 28번 찍히는 것도 문제지만, 더 나쁜 건 **원본의
    `nodeGroupSize`를 버려서 대수 자체가 틀렸다**는 것이다(그룹 17개 × 2대 = 34대를
    17로 셌다). 대수는 사이징에 쓰는 값이라 없어지면 안 된다.
    """

    type_id: str
    tier: str
    note: str | None = None
    #: 이 번들이 이 타입을 **몇 개** 만드는가. 1이면 산출물에 적지 않는다.
    count: int = 1

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"unknown tier: {self.tier!r} (allowed: {TIERS})")
        if self.count < 1:
            raise ValueError(f"count must be 1 or more: {self.type_id} = {self.count}")

    @property
    def rank(self) -> int:
        return _TIER_RANK[self.tier]


@dataclass(frozen=True)
class Bundle:
    """이름 붙은 리소스 군.

    `anchor`는 이 번들이 **무엇을 중심으로** 도는가다. AVM `res` 모듈이라면 그 모듈이
    감싸는 리소스이고, 유스케이스 템플릿이라면 없을 수도 있다(여러 리소스가 대등).
    """

    id: str
    name: str
    provider: str
    evidence: str
    members: tuple[Member, ...]
    anchor: str | None = None
    description: str | None = None
    #: **원본이 스스로 단 경고.** 값과 떼어 놓으면 안 된다 — tumblebug의 `sg-default`는
    #: "전 포트를 연다, 프로덕션엔 쓰지 말라"고 자기가 적어 두었다.
    caveat: str | None = None

    def __post_init__(self) -> None:
        """같은 타입이 두 줄이면 **빌드를 세운다.**

        조용히 합치지 않는 이유는, 합치려면 note를 어떻게 할지 정해야 하는데
        그건 **파서가 원본을 보고 내릴 판단**이지 모델이 대신할 일이 아니기
        때문이다. 여기서 죽으면 어느 파서가 개수를 흘리는지 그 자리에서 드러난다.
        """
        seen: dict[str, str] = {}
        for member in self.members:
            if member.type_id in seen:
                raise ValueError(
                    f"{self.id}: '{member.type_id}' appears twice"
                    f" ({seen[member.type_id]} · {member.tier})."
                    " Counts go in Member.count."
                )
            seen[member.type_id] = member.tier

    @property
    def basis(self) -> str:
        return basis_of(self.evidence)

    @property
    def is_fact(self) -> bool:
        return is_fact(self.basis)

    def by_tier(self, tier: str) -> tuple[Member, ...]:
        return tuple(m for m in self.members if m.tier == tier)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "evidence": self.evidence,
            "anchor": self.anchor,
            "description": self.description,
            "caveat": self.caveat,
            # count는 1이면 적지 않는다 — 거의 모든 구성원이 1이라, 적으면 산출물
            # 대부분이 `"count": 1`로 채워져 정작 1이 아닌 곳이 안 보인다.
            "members": [
                {"typeId": m.type_id, "tier": m.tier, "note": m.note}
                | ({"count": m.count} if m.count != 1 else {})
                for m in sorted(self.members, key=lambda x: (x.rank, x.type_id))
            ],
        }

    @classmethod
    def from_dict(cls, row: dict) -> Bundle:
        return cls(
            id=row["id"],
            name=row["name"],
            provider=row["provider"],
            evidence=row["evidence"],
            anchor=row.get("anchor"),
            description=row.get("description"),
            caveat=row.get("caveat"),
            members=tuple(
                Member(m["typeId"], m["tier"], m.get("note"), m.get("count", 1))
                for m in row["members"]
            ),
        )


@dataclass(frozen=True)
class Companion:
    """앵커 하나와 함께 나온 타입의 **빈도**.

    `samples`를 반드시 함께 담는다. 비율만 담으면 6/17을 35.3%로 읽게 되고, 그건
    숫자에 없는 확신을 준다.
    """

    anchor: str
    type_id: str
    hits: int
    samples: int
    evidence: str

    @property
    def ratio(self) -> float:
        return self.hits / self.samples if self.samples else 0.0

    @property
    def basis(self) -> str:
        return basis_of(self.evidence)

    def to_dict(self) -> dict:
        return {
            "anchor": self.anchor,
            "typeId": self.type_id,
            "hits": self.hits,
            "samples": self.samples,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, row: dict) -> Companion:
        return cls(
            anchor=row["anchor"],
            type_id=row["typeId"],
            hits=row["hits"],
            samples=row["samples"],
            evidence=row["evidence"],
        )


@dataclass
class BundleSet:
    """한 소스에서 나온 번들·동시출현 묶음."""

    bundles: list[Bundle] = field(default_factory=list)
    companions: list[Companion] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)

    def add(self, bundle: Bundle) -> None:
        self.bundles.append(bundle)

    def to_dict(self) -> dict:
        return {
            "bundles": [b.to_dict() for b in sorted(self.bundles, key=lambda b: b.id)],
            "cooccurrence": [
                c.to_dict()
                for c in sorted(self.companions, key=lambda c: (c.anchor, -c.hits))
            ],
            "_coverage": self.coverage,
            "_source": self.provenance,
        }


def observed_only(companions: list[Companion]) -> list[Companion]:
    """실측 근거만 남긴다 — 판정에 쓰면 안 되는 것을 걸러내는 데 쓴다."""
    return [c for c in companions if c.basis == OBSERVED]
