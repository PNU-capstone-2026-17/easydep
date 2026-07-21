"""제약/쿼터 질의: 값 판정, 한도 조회, 불변 속성, 쿼터 검색.

**사실과 짐작을 가르는 것이 핵심이다.** 산문에서 뽑은 제약은 참고로는 쓸모 있지만
값을 **거부하는** 근거로 쓰기엔 약하다. 그래서 `check_value`는 사실(원본이 명시했거나
사람이 확인한 것)만으로 판정하고, 짐작은 "참고" 목록으로 따로 돌려준다.

예전에는 `min_confidence=0.8` 임계값이었는데, 그 선이 실제로 가른 것은
`cfn-description` 158건뿐이었고 그중 115건이 **정확히 0.8**이라 상수를 0.81로만
바꿔도 결과가 뒤집히는 상태였다. 지금 기준은 임의의 숫자가 아니라 "원본이 그렇게
적었는가"다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from capacitykb.model import CapacitySet, Constraint, Quota
from kbcommon.basis import describe
from kbcommon.display import evidence_name

def resolve_type(capacity: CapacitySet, name: str) -> str:
    """이름으로 타입 id를 찾는다.

    정확한 id를 우선하고, 아니면 접두사(`aws::` 등)를 뗀 뒤 대소문자 무시로 비교한다.
    후보가 여럿이면 후보 목록을 담은 ValueError.
    """
    known = {c.type_id for c in capacity.constraints} | {
        q.type_id for q in capacity.quotas if q.type_id
    }
    if name in known:
        return name
    lowered = name.lower()
    candidates = sorted(
        type_id
        for type_id in known
        if type_id.lower() == lowered or type_id.split("::", 1)[-1].lower() == lowered
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"타입을 찾을 수 없습니다: {name!r}")
    raise ValueError(f"이름이 모호합니다: {name!r} → 후보: {', '.join(candidates)}")


def limits_for(
    capacity: CapacitySet,
    type_id: str,
    *,
    prop: str | None = None,
    facts_only: bool = False,
) -> list[Constraint]:
    """타입(또는 특정 프로퍼티)의 제약을 반환한다. 사실이 먼저 온다."""
    found = (
        capacity.for_property(type_id, prop) if prop else capacity.for_type(type_id)
    )
    filtered = [c for c in found if c.is_fact] if facts_only else found
    return sorted(filtered, key=lambda c: (c.property, not c.is_fact, c.kind))


def immutable_properties(capacity: CapacitySet, type_id: str) -> list[Constraint]:
    """변경하면 리소스가 재생성되는 속성들 (배포 계획에 핵심)."""
    return sorted(
        (
            c
            for c in capacity.for_type(type_id)
            if c.kind == "mutability"
            and c.value in ("create_only", "conditional_create_only")
        ),
        key=lambda c: c.property,
    )


@dataclass(frozen=True, slots=True)
class CheckResult:
    """값 판정 결과."""

    ok: bool
    violations: list[str]
    advisories: list[str]  # 신뢰도가 낮아 판정엔 쓰지 않은 참고 정보 (전부 "벗어남")
    checked: int
    references: list[str] = field(default_factory=list)
    """신뢰도가 낮은 근거 중 **벗어나지 않은** 것들.

    예전에는 이걸 아예 버렸다. 그래서 "gp2 볼륨 30,000 GiB 되나?"에
    `unknown`("알려진 제약이 없습니다")으로 답했는데, 정작 데이터셋 안에는
    `gp2: 1 - 16,384 GiB`라고 적힌 설명문이 들어 있었다 — **답을 쥐고도 모른다고
    한 것이다.** 약한 근거가 의심을 키우는 데만 쓰이고 알려주는 데는 안 쓰이던
    비대칭을 없앤다. fail-open은 "짐작으로 막지 마라"이지 "짐작을 숨겨라"가 아니다.
    """

    @property
    def known(self) -> bool:
        """판정 근거가 하나라도 있었는지."""
        return self.checked > 0 or bool(self.advisories) or bool(self.references)

    @property
    def verdict(self) -> str:
        """판정 상태.

        - `violation`: 확정 제약을 위반 → 불가
        - `advisory`: 확정 제약 위반은 없으나 **낮은 신뢰도 정보상 벗어남** → 보류.
          여기서 "가능"이라고 답하면 거짓 긍정이 된다 (예: EBS 100TB는 설명문상
          최대 65,536 GiB를 넘지만 스키마에는 제약이 없다).
        - `ok`: 확정 제약을 실제로 검사했고 전부 통과
        - `unknown`: 검사할 근거가 없음
        """
        if self.violations:
            return "violation"
        if self.advisories:
            return "advisory"
        return "ok" if self.checked > 0 else "unknown"


def _violation(constraint: Constraint, value, label: str) -> str:
    unit = f" {constraint.unit}" if constraint.unit else ""
    note = f" — {constraint.note}" if constraint.note else ""
    return (
        f"{constraint.property}: {value}{unit}는 {label} {constraint.value}{unit}을(를) "
        f"벗어남 (근거 {evidence_name(constraint.evidence)}, {describe(constraint.basis)}){note}"
    )


def _reference(constraint: Constraint, label: str) -> str:
    """벗어나지 **않은** 약한 근거를 사람이 읽을 문장으로.

    note(변종별 범위표 등)를 반드시 함께 실어야 한다 — 그 표가 정작 사용자가
    물은 것에 답하는 경우가 있다(`Volume.Size`의 볼륨 종류별 상한).
    """
    unit = f" {constraint.unit}" if constraint.unit else ""
    note = f" — {constraint.note}" if constraint.note else ""
    return (
        f"{constraint.property}: {label} {constraint.value}{unit} "
        f"(근거 {evidence_name(constraint.evidence)}, {describe(constraint.basis)}){note}"
    )


def check_value(
    capacity: CapacitySet,
    type_id: str,
    prop: str,
    value,
    *,
    facts_only: bool = True,
) -> CheckResult:
    """프로퍼티에 넣으려는 값이 허용 범위인지 판정한다.

    **짐작으로는 값을 거부하지 않는다.** 산문에서 뽑은 제약은 advisories로만 돌려준다 —
    잘못 막는 것이 침묵보다 나쁘다는 원칙(fail-open)이 여기에도 적용된다.
    """
    violations: list[str] = []
    advisories: list[str] = []
    references: list[str] = []
    checked = 0

    for constraint in capacity.for_property(type_id, prop):
        weak = facts_only and not constraint.is_fact
        breached = False
        label = ""
        if constraint.kind == "min" and isinstance(value, (int, float)):
            breached, label = value < constraint.value, "최소"
        elif constraint.kind == "max" and isinstance(value, (int, float)):
            breached, label = value > constraint.value, "최대"
        elif constraint.kind == "max_length" and isinstance(value, str):
            breached, label = len(value) > constraint.value, "최대 길이"
        elif constraint.kind == "min_length" and isinstance(value, str):
            breached, label = len(value) < constraint.value, "최소 길이"
        elif constraint.kind == "enum" and isinstance(constraint.value, list):
            breached, label = value not in constraint.value, "허용값"
        elif constraint.kind == "pattern" and isinstance(value, str):
            try:
                breached = re.search(constraint.value, value) is None
                label = "패턴"
            except re.error:
                continue  # 스키마의 정규식이 파이썬에서 안 돌면 판정하지 않는다
        elif constraint.kind == "mutability" and constraint.value == "read_only":
            violations.append(f"{constraint.property}: 읽기 전용이라 설정할 수 없음")
            checked += 1
            continue
        else:
            continue

        if weak:
            if breached:
                advisories.append(_violation(constraint, value, label) + " [참고]")
            else:
                references.append(_reference(constraint, label))
            continue
        checked += 1
        if breached:
            violations.append(_violation(constraint, value, label))

    return CheckResult(
        ok=not violations,
        violations=violations,
        advisories=advisories,
        checked=checked,
        references=references,
    )


def find_quota(capacity: CapacitySet, keyword: str) -> list[Quota]:
    """이름/스코프에 키워드가 포함된 쿼터를 찾는다 (대소문자 무시)."""
    lowered = keyword.lower()
    return sorted(
        (
            q
            for q in capacity.quotas
            if lowered in q.name.lower()
            or (q.scope and lowered in q.scope.lower())
            or (q.type_id and lowered in q.type_id.lower())
        ),
        key=lambda q: q.name,
    )
