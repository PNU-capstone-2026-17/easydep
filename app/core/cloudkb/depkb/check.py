"""제약 검사기 — 구체 계획이 실측된 규칙을 어기는지 본다.

계획 P4(`archive/infra-intent-plan-2026-07-31.md`). 인프라 의도는 *"무엇이
필요한가"*를 말하고, 여기서는 사용자·에이전트가 채운 **구체 계획**이 그 규칙을
지키는지 본다. apply가 깨지기 전에 잡는 것이 목적이다 — 우리가 실측한 거부
코드가 곧 이 검사의 근거다.

## 검사는 실측된 것만 한다

규칙은 claims의 술어에서 오고(`Constraint`), 검사 함수는 부류별로 하나씩이다.
**부류에 검사 함수가 없으면 통과시키지 않고 `unchecked`로 보고한다** — 조용히
넘어가면 계획이 "검사 통과"로 읽힌다.

## 계획의 형태

    {"resources": [{"id": "subnet", "instances": [{"name": "...", "zone": "a"}]},
                   {"id": "loadBalancer", "instances": [{"name": "..."}]}]}

인스턴스가 없는 자원은 "안 만든다"는 뜻이고, 그 자체는 위반이 아니다(서버가
채울 수 있다). 필수 자원의 부재는 별도 검사(`missing_required`)가 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .infra_intent import Constraint, InfraIntent

_AT_LEAST = re.compile(r"(?:≥|이상|최소)\s*(\d+)|(\d+)\s*(?:개|둘|이상)")
_TWO_DIFFERENT = ("서로 다른", "다른 AZ", "different")


@dataclass(frozen=True)
class Violation:
    kind: str
    subject: str
    object: str
    rule: str
    detail: str  #: 무엇이 잘못됐는지 — 사람이 읽는 말


@dataclass(frozen=True)
class Report:
    ok: bool
    violations: tuple[Violation, ...]
    unchecked: tuple[str, ...]  #: 검사 함수가 없는 부류 — 통과가 아니다
    missing_required: tuple[str, ...]


def _instances(plan: dict, resource_id: str) -> list[dict]:
    for r in plan.get("resources", []):
        if r.get("id") == resource_id:
            return list(r.get("instances") or [])
    return []


def _min_count(rule: str) -> int:
    m = _AT_LEAST.search(rule)
    if not m:
        return 1
    return int(next(g for g in m.groups() if g))


def _check_placement(c: Constraint, plan: dict) -> Violation | None:
    """배치 조건 — 개수와 분산. aws EKS의 '서로 다른 AZ ≥2'가 원형이다."""
    items = _instances(plan, c.object)
    need = _min_count(c.rule)
    if len(items) < need:
        return Violation(c.kind, c.subject, c.object, c.rule,
                         f"{c.object}이(가) {len(items)}개인데 {need}개 이상 필요합니다")
    if any(k in c.rule for k in _TWO_DIFFERENT):
        zones = {i.get("zone") or i.get("availabilityZone") for i in items}
        if len(zones - {None}) < 2:
            return Violation(c.kind, c.subject, c.object, c.rule,
                             f"{c.object}이(가) 같은 영역에 몰려 있습니다 "
                             f"(zone={sorted(z for z in zones if z)})")
    return None


def _check_pair(c: Constraint, plan: dict) -> Violation | None:
    """쌍 호환 — 주체와 대상의 속성이 맞아야 한다. gcp 존 일치가 원형이다."""
    if "존" not in c.rule and "zone" not in c.rule.lower():
        return None  # 다른 쌍 호환(아키텍처 등)은 아직 검사 함수가 없다
    subjects = _instances(plan, c.subject)
    objects = _instances(plan, c.object)
    for s in subjects:
        for o in objects:
            sz, oz = s.get("zone"), o.get("zone")
            if sz and oz and sz != oz:
                return Violation(
                    c.kind, c.subject, c.object, c.rule,
                    f"{c.subject}({s.get('name', '?')})는 {sz}에 있는데 "
                    f"{c.object}({o.get('name', '?')})는 {oz}에 있습니다")
    return None


def _check_name(c: Constraint, plan: dict) -> Violation | None:
    """이름 조건 — azure GatewaySubnet이 원형이다."""
    m = re.search(r"정확히\s+(\S+?)(?:이라는|라는)", c.rule)
    required = m.group(1) if m else None
    if not required:
        return None
    items = _instances(plan, c.object)
    if items and not any(i.get("name") == required for i in items):
        return Violation(c.kind, c.subject, c.object, c.rule,
                         f"{c.object}의 이름이 정확히 `{required}`여야 합니다 "
                         f"(지금: {[i.get('name') for i in items]})")
    return None


#: 부류 → 검사 함수. 없는 부류는 `unchecked`로 보고한다(통과가 아니다).
CHECKERS = {
    "배치 조건": _check_placement,
    "카디널리티": _check_placement,
    "쌍 호환": _check_pair,
    "이름 조건": _check_name,
}


def check(intent: InfraIntent, plan: dict) -> Report:
    """구체 계획이 인프라 의도의 규칙을 지키는지 본다."""
    violations: list[Violation] = []
    unchecked: list[str] = []
    # 이중 생성 — 합성물(동반 정리의 대상)은 클라우드가 만든다. 계획이 그 자원의
    # 인스턴스를 직접 내면 같은 것이 둘 생긴다(합성 라운드 실측이 근거).
    for owner, synth in intent.cleanupCascades:
        if _instances(plan, synth):
            violations.append(Violation(
                "동반 정리", owner, synth,
                f"{synth}은(는) {owner}가 합성·정리한다",
                f"{synth}을(를) 계획이 직접 만들면 {owner}의 합성물과 "
                f"이중 생성이 됩니다 — 계획에서 빼세요"))
    for c in intent.constraints:
        fn = CHECKERS.get(c.kind)
        if fn is None:
            # 수명 조건처럼 생성 시점 계획으로는 판정할 수 없는 것도 여기 온다.
            unchecked.append(f"{c.kind}: {c.subject}→{c.object} — {c.rule}")
            continue
        hit = fn(c, plan)
        if hit:
            violations.append(hit)

    missing = tuple(
        r.id for r in intent.resources
        if r.role == "required" and not _instances(plan, r.id)
        and r.id not in {a.id for a in intent.autoFilled})

    return Report(ok=not violations and not missing,
                  violations=tuple(violations),
                  unchecked=tuple(unchecked),
                  missing_required=missing)
