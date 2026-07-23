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

import regex
from dataclasses import dataclass, field

from capacitykb.model import CapacitySet, Constraint, Quota
from kbcommon.basis import describe
from kbcommon.display import evidence_name

def resolve_type(capacity: CapacitySet, name: str) -> str:
    """이름으로 타입 id를 찾는다.

    정확한 id를 우선하고, 아니면 접두사(`aws::` 등)를 뗀 뒤 대소문자 무시로 비교한다.
    후보가 여럿이면 후보 목록을 담은 ValueError.
    """
    known = (
        {c.type_id for c in capacity.constraints}
        | {q.type_id for q in capacity.quotas if q.type_id}
        # 훑었지만 제약이 하나도 안 나온 타입도 이름으로는 찾혀야 한다.
        # 안 그러면 "타입을 찾을 수 없습니다"라고 답하는데, 그건 거짓이다 —
        # 그 타입은 실재하고 우리가 읽기까지 했다. 답은 "제약이 없다"여야 한다.
        | {t for e in capacity.coverage for t in (e.get("type_ids") or ())}
    )
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


def secret_properties(capacity: CapacitySet, type_id: str) -> list[Constraint]:
    """배포 때 넣지만 API로 다시 못 읽는 속성들 (비밀번호·키·연결 문자열)."""
    return sorted(
        (c for c in capacity.for_type(type_id) if c.kind == "secret"),
        key=lambda c: c.property,
    )


@dataclass(frozen=True, slots=True)
class CheckResult:
    """값 판정 결과."""

    ok: bool
    violations: list[str]
    advisories: list[str]
    """판정엔 쓰지 않은 참고 정보 (전부 "벗어남").

    **가르는 기준은 `basis`이지 숫자가 아니다.** 예전엔 `confidence < 0.7`이었는데
    그 척도는 정의가 없었고, `check`의 0.8과도 어긋났다. 지금 기준은
    `is_fact` — "원본이 그렇게 적었는가"다. 그러니 사용자에게 보이는 말도
    '신뢰도가 낮다'가 아니라 **'원본이 명시한 것이 아니다'**여야 한다.
    """
    checked: int
    excluded: int = 0
    """조건이 **성립하지 않아** 판정에서 빠진 제약 수.

    이게 0이 아닌데 판정 근거도 없으면 "이 속성을 모른다"가 아니라 **"준 조합에
    걸리는 제약이 없다"**이다. 둘은 사용자에게 전혀 다른 말이다 — 앞은 우리가
    데이터가 없다는 뜻이고, 뒤는 조합 쪽을 다시 보라는 신호다(오타나 미지원 버전).

    실측: `Engine=aurora-postgresql, EngineVersion=16.4`를 주면 938건 중 하나도
    안 걸린다(원본에 `16.4`가 `16.4-limitless`로만 있다). 예전에는 그때
    "알려진 제약이 없어 판정할 수 없습니다"라고 답했다.
    """

    unevaluated: list[str] = field(default_factory=list)
    """제약을 **쥐고 있으나 평가하지 못한** 것들.

    벤더가 쓴 정규식이 어느 엔진에서도 안 도는 경우다(실측 11건 — `*{1,1000}`
    같은 상류 오류와 UTF-16 서로게이트 범위). 우리가 고칠 수 있는 종류가 아니라
    **밝히기만 한다.** 조용히 넘기면 "패턴 제약이 없다"로 읽힌다.
    """

    missing: list[str] = field(default_factory=list)
    """조건 판정에 **필요한데 문맥에 없던** 속성 이름들.

    표시 문자열을 되파싱해서 뽑던 것을 칸으로 옮겼다 — 조건이 둘 이상이 되면
    `"Engine='aurora-mysql' 그리고 EngineVersion가 ..."` 같은 문장이라 파싱이
    깨진다. 답에 "무엇을 알려주면 판정할 수 있는지"를 실으려면 구조가 필요하다.
    """

    unresolved: list[str] = field(default_factory=list)
    """조건부 제약인데 **조건을 몰라 판정에 못 쓴** 것들.

    버리면 안 된다 — "볼륨 종류에 따라 한도가 다르다"는 사실 자체가 답의 일부다.
    문맥을 주면(`context={"VolumeType": "gp2"}`) 판정에 들어간다.
    """

    references: list[str] = field(default_factory=list)
    """원본이 명시하지 않은 근거 중 **벗어나지 않은** 것들.

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


def brief(value) -> str:
    """제약 값을 한 줄로. **긴 목록을 통째로 찍지 않는다.**

    리전별 인스턴스 타입은 목록 하나가 600개가 넘는다. 그대로 찍으면 답변이
    목록으로 뒤덮여 정작 "되나 안 되나"가 안 보인다.
    """
    if isinstance(value, list) and len(value) > 6:
        return f"{len(value)}개 중 하나 (예: {', '.join(map(str, value[:3]))} …)"
    return str(value)


def _scope(constraint: Constraint) -> str:
    """이 제약이 **어느 조건에서의** 값인지. 조건이 없으면 빈 문자열.

    문맥으로 조건이 성립함을 이미 확인했더라도 밝혀야 한다. 안 밝히면
    "최대 16,384 GiB"가 보편 상한처럼 읽히고, 정작 답이 되는 사실
    (**gp3로 바꾸면 65,536**)이 안 보인다. 한도를 어겼다는 말보다
    어느 조건에서의 한도인지가 해결책을 가리킨다.
    """
    if not constraint.conditions:
        return ""
    return " 그리고 ".join(_cond_text(c) for c in constraint.conditions) + " 일 때, "


def _violation(constraint: Constraint, value, label: str) -> str:
    unit = f" {constraint.unit}" if constraint.unit else ""
    note = f" — {constraint.note}" if constraint.note else ""
    return (
        f"{constraint.property}: {value}{unit}는 {label} {brief(constraint.value)}{unit}"
        f"을(를) 벗어남 ({_scope(constraint)}근거 {evidence_name(constraint.evidence)}, "
        f"{describe(constraint.basis)}){note}"
    )


def _reference(constraint: Constraint, label: str) -> str:
    """벗어나지 **않은** 약한 근거를 사람이 읽을 문장으로.

    note(변종별 범위표 등)를 반드시 함께 실어야 한다 — 그 표가 정작 사용자가
    물은 것에 답하는 경우가 있다(`Volume.Size`의 볼륨 종류별 상한).
    """
    unit = f" {constraint.unit}" if constraint.unit else ""
    note = f" — {constraint.note}" if constraint.note else ""
    return (
        f"{constraint.property}: {label} {brief(constraint.value)}{unit} "
        f"({_scope(constraint)}근거 {evidence_name(constraint.evidence)}, "
        f"{describe(constraint.basis)}){note}"
    )


#: 정규식은 길다. 버전 패턴 정도는 괜찮지만 훨씬 긴 것도 있다.
_COND_VALUE_MAX = 44


def _cond_text(cond: dict) -> str:
    """조건 하나를 사람 말로.

    **뒤에 "일 때"가 붙는다는 전제**로 쓴다 — 여기에 "…할 때"까지 넣었더니
    `"…에 맞을 때 일 때"`가 됐다. 절만 만들고 시점 표현은 호출부가 붙인다.

    `matches`는 등호가 아니므로 `=`로 쓰지 않는다. 값이 정규식이라 등호로 쓰면
    "EngineVersion이 정확히 그 문자열일 때"로 읽힌다.
    """
    prop, value = cond.get("property"), cond.get("value")
    shown = repr(value)
    if len(shown) > _COND_VALUE_MAX:
        shown = shown[: _COND_VALUE_MAX - 1] + "…'"
    if cond.get("op") == "matches":
        return f"{prop}이 {shown} 패턴"
    return f"{prop}={shown}"


def _conditional(constraint: Constraint, breached: bool | None) -> str:
    unit = f" {constraint.unit}" if constraint.unit else ""
    label = {"min": "최소", "max": "최대", "enum": "허용값"}.get(
        constraint.kind, constraint.kind
    )
    verdict = "" if breached is None else ("  → 불가" if breached else "  → 가능")
    where = " 그리고 ".join(_cond_text(c) for c in constraint.conditions) or "무조건"
    return (
        f"{where} 일 때 "
        f"{label} {brief(constraint.value)}{unit} "
        f"(근거 {evidence_name(constraint.evidence)}, "
        f"{describe(constraint.basis)}){verdict}"
    )


def _breaches(constraint: Constraint, value) -> bool | None:
    """이 제약 하나만 놓고 볼 때 값이 벗어나는가. 판정할 수 없으면 None."""
    kind, limit = constraint.kind, constraint.value
    if kind == "min" and isinstance(value, (int, float)):
        return value < limit
    if kind == "max" and isinstance(value, (int, float)):
        return value > limit
    if kind == "enum" and isinstance(limit, list):
        return value not in limit
    if kind == "max_length" and isinstance(value, str):
        return len(value) > limit
    if kind == "min_length" and isinstance(value, str):
        return len(value) < limit
    return None


def _one_condition(cond: dict, context: dict | None) -> bool | None:
    """조건 하나가 성립하는가. 모르면 None."""
    prop = cond.get("property")
    if not context or prop not in context:
        return None
    actual = context[prop]
    op = cond.get("op")
    if op == "eq":
        return actual == cond.get("value")
    if op == "matches":
        # 정규식이 파이썬에서 안 돌면 **모른다**로 둔다. False로 치면 아는 제약을
        # 통째로 버리고, True로 치면 엉뚱한 조건의 한도를 적용한다.
        try:
            return regex.search(str(cond.get("value")), str(actual)) is not None
        except regex.error:
            return None
    return None  # 모르는 연산자를 짐작해서 처리하지 않는다


def _condition_holds(constraint: Constraint, context: dict | None) -> bool | None:
    """조건들이 **전부** 성립하는가. 모르면 None.

    - 조건이 없으면 언제나 성립(True).
    - 문맥에 그 속성이 없으면 **모른다**(None) — 성립한다고도, 안 한다고도 하면
      안 된다. True로 치면 gp2 볼륨에 gp3 한도를 적용하게 되고, False로 치면 아는
      제약을 통째로 버리게 된다.
    - 여럿일 때: **하나라도 확실히 안 맞으면 안 맞는다**(False). 안 맞는 게 없고
      모르는 게 있으면 모른다(None). 셋 다 아니면 성립(True).

      순서가 중요하다 — RDS는 `Engine`은 알고 `EngineVersion`은 모르는 경우가
      흔한데, 그때 Engine이 다르면 그 블록은 **확실히** 해당 없음이다. 이걸
      모름으로 접으면 938블록이 전부 미결로 남아 아무것도 못 답한다.
    """
    if not constraint.conditions:
        return True
    unknown = False
    for cond in constraint.conditions:
        held = _one_condition(cond, context)
        if held is False:
            return False
        if held is None:
            unknown = True
    return None if unknown else True


def check_value(
    capacity: CapacitySet,
    type_id: str,
    prop: str,
    value,
    *,
    facts_only: bool = True,
    context: dict | None = None,
) -> CheckResult:
    """프로퍼티에 넣으려는 값이 허용 범위인지 판정한다.

    **짐작으로는 값을 거부하지 않는다.** 산문에서 뽑은 제약은 advisories로만 돌려준다 —
    잘못 막는 것이 침묵보다 나쁘다는 원칙(fail-open)이 여기에도 적용된다.
    """
    violations: list[str] = []
    advisories: list[str] = []
    references: list[tuple[str, str]] = []
    unresolved: list[str] = []
    unevaluated: list[str] = []
    excluded = 0
    missing: set[str] = set()
    strong: set[str] = set()
    checked = 0

    for constraint in capacity.for_property(type_id, prop):
        holds = _condition_holds(constraint, context)
        if holds is False:
            excluded += 1
            continue  # 다른 종류에 걸린 제약이다
        if holds is None:
            # 조건을 모르면 판정에 **못 쓴다**. 다만 버리지도 않고, 한 걸음 더 가서
            # **그 조건이 성립한다면 어떻게 되는지**까지 계산해 둔다 —
            # "38개 리전마다 다릅니다"보다 "14곳에서 가능합니다"가 답에 가깝다.
            unresolved.append(_conditional(constraint, _breaches(constraint, value)))
            for cond in constraint.conditions:
                if _one_condition(cond, context) is None:
                    missing.add(str(cond.get("property")))
            continue
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
            # **벤더 문법 그대로 읽는다.** 파이썬 `re`로는 205건이 안 돌았는데
            # 그중 194건은 원본이 틀린 게 아니라 .NET/PCRE 문법(`\p{L}` 계열)이라
            # 파이썬이 못 읽는 것이었다. `regex`는 그 문법을 안다.
            #
            # `re.search` 의미(부분 일치)를 그대로 쓴다 — JSON Schema의 `pattern`은
            # 원래 앵커되지 않는다. 여기서 `fullmatch`로 바꾸면 우리가 원본에 없는
            # 엄격함을 지어내는 것이 된다.
            try:
                breached = regex.search(constraint.value, value) is None
                label = "패턴"
            except regex.error:
                # **조용히 넘기지 않는다.** 예전엔 `continue`라, 우리가 패턴을
                # 쥐고도 평가하지 못한다는 사실이 답변에서 통째로 사라졌다.
                unevaluated.append(
                    f"{constraint.property}: 패턴 제약이 있으나 우리가 읽을 수 없는 "
                    f"정규식입니다 (근거 {evidence_name(constraint.evidence)}) — "
                    f"{brief(constraint.value)}"
                )
                continue
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
                references.append((constraint.kind, _reference(constraint, label)))
            continue
        strong.add(constraint.kind)
        checked += 1
        if breached:
            violations.append(_violation(constraint, value, label))

    return CheckResult(
        ok=not violations,
        violations=violations,
        advisories=advisories,
        checked=checked,
        # 같은 종류(min/max)에 확정 근거가 이미 있으면 약한 참고는 노이즈다.
        # EBS에서 실제로 그랬다 — 종류별 확정 한도를 얻고 나서도 옛 봉투
        # (`최대 65536`, 어느 볼륨에도 안 맞는 값)가 계속 따라붙었다.
        references=[text for kind, text in references if kind not in strong],
        unresolved=unresolved,
        unevaluated=unevaluated,
        excluded=excluded,
        missing=sorted(missing),
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
