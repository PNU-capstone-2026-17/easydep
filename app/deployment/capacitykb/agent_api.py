"""에이전트용 사전 정의 질의 API (capacitykb).

graphkb의 agent_api와 같은 관례: 예외 대신 에이전트가 그대로 읽을 수 있는
한국어 텍스트를 반환하고, 산출물이 없으면 빌드 명령을 안내한다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from capacitykb.model import CapacitySet
from capacitykb.query import (
    brief,
    check_value,
    find_quota,
    immutable_properties,
    limits_for,
    resolve_type,
)
from kbcommon.basis import describe
from kbcommon.display import backend_caveat, display, evidence_name

DEFAULT_OUTPUT_DIR = Path("output")
CAPACITY_FILES = (
    "aws-capacity.json",
    "azure-capacity.json",
    "azure-quota.json",
    "gcp-capacity.json",
    "aws-limits.json",
    "aws-tf.json",
    "aws-regions.json",
)

#: 조건이 이보다 많으면 값을 나열하지 않고 "몇 가지에서 되는지"로 요약한다.
#: 리전은 38가지라 나열이 무의미하고, 볼륨 종류는 6가지라 값이 보여야 한다.
_SUMMARIZE_OVER = 8

_MISSING_MESSAGE = (
    "용량·제약 산출물이 없습니다. 먼저 `python -m capacitykb build --source "
    "cfn|azure|azure-quota` 로 생성하세요."
)


@lru_cache(maxsize=4)
def _load_merged_cached(output_dir: str) -> CapacitySet | None:
    base = Path(output_dir)
    merged = CapacitySet()
    found = False
    for name in CAPACITY_FILES:
        path = base / name
        if path.exists():
            merged.merge(CapacitySet.load(path))
            found = True
    return merged if found else None


def load_merged(output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> CapacitySet | None:
    """output/의 용량 산출물을 모두 병합해 반환한다 (없으면 None, 캐시됨)."""
    return _load_merged_cached(str(output_dir))


def _resolve(capacity: CapacitySet, name: str):
    try:
        return resolve_type(capacity, name), None
    except ValueError as exc:
        return None, str(exc)


def _describe(constraint) -> str:
    unit = f" {constraint.unit}" if constraint.unit else ""
    labels = {
        "min": "최소",
        "max": "최대",
        "min_length": "최소 길이",
        "max_length": "최대 길이",
        "min_items": "최소 개수",
        "max_items": "최대 개수",
        "pattern": "패턴",
        "enum": "허용값",
        "default": "기본값",
        "required": "필수",
        "mutability": "변경",
        # 형제 속성과의 관계. value가 관련 속성 목록이라 라벨만으로 뜻이 통해야 한다.
        "exactly_one_of": "이 중 정확히 하나만",
        "at_least_one_of": "이 중 최소 하나는",
        "conflicts_with": "함께 못 쓰는 속성",
        "required_with": "함께 있어야 하는 속성",
    }
    mutability_labels = {
        "create_only": "생성 후 변경 불가 (바꾸면 리소스 재생성)",
        "conditional_create_only": "조건부 변경 불가 (경우에 따라 재생성)",
        "read_only": "읽기 전용 (설정 불가)",
        # 불변/가변 이분법에 안 담기는 축. "늘리는 건 되고 줄이면 재생성" 같은 것이라,
        # 무엇이 조건인지는 note에 붙는 판정 함수 이름으로만 알 수 있다.
        "update_restricted": "조건부 재생성 (어떤 방향으로 바꾸느냐에 따라 다름)",
    }
    if constraint.kind == "mutability":
        text = mutability_labels.get(constraint.value, constraint.value)
    elif constraint.kind == "required":
        text = "필수 항목"
    else:
        # **긴 목록을 통째로 찍지 않는다.** 리전별 인스턴스 타입을 그대로 내보냈더니
        # 도구 응답 하나가 377,439자였다 — 모델 컨텍스트를 통째로 먹고 실측이 멈췄다.
        # check 경로에는 요약을 넣어 놓고 이쪽에 안 넣은 게 원인이었다.
        text = f"{labels.get(constraint.kind, constraint.kind)} {brief(constraint.value)}{unit}"
    tags = []
    # **조건을 안 보여주면 39줄이 전부 똑같아 보인다.** 리전별 허용값을 넣고서야
    # 드러났다 — 어느 조건에서의 값인지가 그 줄의 뜻 전부인 경우가 있다.
    cond = constraint.condition
    if cond:
        tags.append(f"{cond.get('property')}={cond.get('value')!r} 일 때")
    if constraint.conditional:
        tags.append("조건부")
    tags.append(f"근거 {evidence_name(constraint.evidence)}, {describe(constraint.basis)}")
    suffix = f" ({', '.join(tags)})"
    note = f"\n    ※ {constraint.note}" if constraint.note else ""
    return f"  - {constraint.property}: {text}{suffix}{note}"


def _backend_footer(constraints) -> str | None:
    """상류가 낡은 레코드가 섞여 있으면 **블록당 한 번** 밝힌다.

    줄마다 붙이지 않는 이유: `backend`는 CRD 단위라 한 타입 안에서는 대개 전부
    같은 값이다. 줄마다 달면 2,453줄에 똑같은 경고가 붙어 노이즈가 되고, 노이즈가
    되면 진짜 경고가 안 보인다(costkb 추천의 성능 주석에서 이미 겪은 실패다).
    """
    stale = {}
    for c in constraints:
        caveat = backend_caveat(getattr(c, "backend", None))
        if caveat:
            stale[caveat] = stale.get(caveat, 0) + 1
    if not stale:
        return None
    total = sum(stale.values())
    text = "; ".join(stale)
    return (
        f"{total}건은 {text}."
        if total == len(constraints)
        else f"위 {len(constraints)}건 중 {total}건은 {text}."
    )


def _nothing_found(capacity: CapacitySet, type_id: str, what: str) -> str:
    """"없다"와 "안 봤다"를 구분해 답한다.

    둘을 같은 문장으로 답하면 침묵이 사실로 읽힌다. 실측상 graphkb가 아는 벤더
    타입 5,547종 중 3,634종에 제약 레코드가 없고, 그중 GCP 527종은 **capacitykb가
    아예 안 읽어서** 없는 것이다. "제약 없음"이라고 답하면 그건 거짓이다.
    """
    if capacity.covers(type_id):
        return f"{what} 에 대해 알려진 제약이 없습니다 (수집 범위 안이므로 '없음'이 답입니다)."
    provider = type_id.split("::", 1)[0]
    scanned = ", ".join(
        f"{e['provider']}({'전체' if not e.get('scope') else '/'.join(e['scope'])})"
        for e in capacity.coverage
    )
    return "\n".join([
        f"{what} 는 **수집 범위 밖**이라 제약을 모릅니다 — 제약이 없다는 뜻이 아닙니다.",
        f"  지금 수집한 범위: {scanned or '(기록 없음)'}",
        f"  {provider} 쪽을 넓히려면 capacitykb 빌드 범위를 늘려야 합니다.",
    ])


def property_limits(
    resource_type: str,
    property_name: str | None = None,
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """리소스 타입(또는 특정 속성)의 제약을 요약해 반환한다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    found = limits_for(capacity, type_id, prop=property_name)
    if not found:
        target = f"{display(type_id)}.{property_name}" if property_name else display(type_id)
        return _nothing_found(capacity, type_id, target)
    header = display(type_id) + (f".{property_name}" if property_name else "")
    lines = [f"{header} 제약 {len(found)}건:"]
    lines.extend(_describe(c) for c in found)
    footer = _backend_footer(found)
    if footer:
        lines.append(f"  ⚠ {footer}")
    return "\n".join(lines)


def check(
    resource_type: str,
    property_name: str,
    value: float | str,
    *,
    context: dict | None = None,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """값이 허용 범위인지 판정한다.

    Args:
        context: 함께 정한 다른 속성들 (`{"VolumeType": "gp2"}`). 한도가 다른 속성에
            따라 달라지면 이걸 줘야 판정할 수 있다 — EBS 볼륨 크기 상한은
            gp2 16,384 / gp3 65,536 / standard 1,024 GiB로 제각각이라, 문맥 없이는
            어느 값을 적용할지 정할 수 없다.
    """
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    result = check_value(capacity, type_id, property_name, value, context=context)
    target = f"{display(type_id)}.{property_name} = {value}"

    if result.verdict == "unknown":
        if result.unresolved:
            # 조건을 모를 뿐 아는 건 있다. 이걸 "모른다"로 뭉개면 답을 쥐고도 안 내놓는
            # 것이 된다 — 어느 조건에서 얼마인지를 보여주고, 무엇을 알려주면 판정할 수
            # 있는지까지 말한다.
            asked = sorted({u.split(" =", 1)[0] for u in result.unresolved})
            head = (
                f"조건에 따라 다릅니다: {target} 는 {', '.join(asked)} 에 따라 달라져서, "
                "그 값을 알아야 확정할 수 있습니다."
            )
            allowed = [u for u in result.unresolved if u.endswith("→ 가능")]
            denied = [u for u in result.unresolved if u.endswith("→ 불가")]
            # 조건이 몇 개 안 되면 **값을 그대로** 보여준다. 볼륨 종류 6가지에
            # "2가지에서 가능"만 말하면 정작 한도 숫자가 사라진다.
            # 리전 38개처럼 많을 때만 세어서 요약한다.
            if not (allowed or denied) or len(result.unresolved) <= _SUMMARIZE_OVER:
                return "\n".join([head, *(f"  - {u}" for u in result.unresolved)])
            # 조건이 38개(리전)씩 되면 전부 나열하는 게 답이 아니다.
            # **어디서 되고 어디서 안 되는지**를 세어 주는 편이 질문에 가깝다.
            lines = [
                head + f" 조건 {len(result.unresolved)}가지 중 "
                f"**{len(allowed)}가지에서 가능**, {len(denied)}가지에서 불가입니다."
            ]
            for label, items in (("가능", allowed), ("불가", denied)):
                if not items:
                    continue
                names = [u.split(" 일 때", 1)[0].split("= ", 1)[-1].strip("'")
                         for u in items[:12]]
                more = f" 외 {len(items) - 12}곳" if len(items) > 12 else ""
                lines.append(f"  {label}: {', '.join(names)}{more}")
            return "\n".join(lines)
        if not result.references:
            return (
                f"{display(type_id)}.{property_name} 에 대해 알려진 제약이 없어 판정할 수 "
                "없습니다. 지식베이스에 없는 값이므로 공식 문서를 확인하세요."
            )
        # 확정 근거는 없지만 참고 정보는 쥐고 있다 — 예전엔 이걸 버리고 "모른다"고 답했다.
        return "\n".join([
            f"확정 판정 불가: {target} 를 판정할 확정 제약이 없습니다. "
            "다만 아래 참고 정보가 있으니 함께 보세요.",
            "  참고(신뢰도가 낮아 확정 판정엔 쓰지 않음):",
            *(f"  - {r}" for r in result.references),
        ])
    if result.verdict == "violation":
        lines = [f"불가: {target} 는 제약을 위반합니다."]
        lines.extend(f"  - {v}" for v in result.violations)
    elif result.verdict == "advisory":
        # 확정 제약은 없지만 참고 정보상 벗어남 → "가능"이라고 하면 거짓 긍정이 된다
        lines = [
            f"판정 보류: {target} 는 확정된 제약을 위반하진 않지만, "
            "신뢰도가 낮은 참고 정보상 범위를 벗어납니다. 공식 문서 확인을 권합니다."
        ]
    else:
        lines = [f"가능: {target} 는 알려진 제약 {result.checked}건을 만족합니다."]

    if result.advisories or result.references:
        lines.append("  참고(신뢰도가 낮아 확정 판정엔 쓰지 않음):")
        lines.extend(f"  - {a}" for a in result.advisories)
        lines.extend(f"  - {r}" for r in result.references)
    if result.unresolved:
        lines.append("  다른 조건에서는 한도가 다릅니다:")
        lines.extend(f"  - {u}" for u in result.unresolved)
    return "\n".join(lines)


def immutable(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """변경 시 리소스가 재생성되는 속성들을 반환한다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    found = immutable_properties(capacity, type_id)
    if not found:
        if not capacity.covers(type_id):
            return _nothing_found(capacity, type_id, display(type_id))
        return f"{display(type_id)} 에 변경 불가로 알려진 속성이 없습니다."

    # 부모가 이미 불변이면 자식은 접는다. GCP를 넣고 나서 이게 문제가 됐다 —
    # KCC는 `Immutable.`을 부모와 자식 모두에 달아서 실측상 2,003건 중 913건(45.6%)이
    # 부모의 자식이었고, DataprocWorkflowTemplate은 185줄 중 179줄이 그랬다.
    # 값을 지우는 게 아니라 표시만 접는다(레코드는 원본 그대로 남는다).
    names = {c.property for c in found}
    shown = [c for c in found if not any(c.property.startswith(p + ".") for p in names)]
    folded = len(found) - len(shown)

    head = f"{display(type_id)} 의 변경 시 재생성되는 속성 {len(shown)}개"
    if folded:
        head += f" (하위 속성 {folded}개는 부모가 이미 불변이라 접었습니다)"
    # 낡음 고지를 **머리줄에** 붙인다. 꼬리말로 뒀더니 모델이 값만 옮기고 경고는
    # 뺐다 — 프롬프트로 "⚠는 반드시 옮기라"고 지시해도 그랬다(실측). 개수는 모델이
    # 반드시 옮기는 정보라, 거기 붙이면 경고만 따로 떼기 어렵다.
    caveat = _backend_footer(shown)
    lines = [f"{head}:" if not caveat else f"{head}. ⚠ {caveat}"]
    lines.extend(_describe(c) for c in shown)
    return "\n".join(lines)


def allowed_values(
    resource_type: str,
    property_name: str,
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """속성의 허용값(enum)·패턴·기본값을 반환한다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    found = [
        c
        for c in limits_for(capacity, type_id, prop=property_name)
        if c.kind in ("enum", "pattern", "default")
    ]
    if not found:
        if not capacity.covers(type_id):
            return _nothing_found(capacity, type_id, f"{display(type_id)}.{property_name}")
        return f"{display(type_id)}.{property_name} 에 알려진 허용값/패턴 정보가 없습니다."
    lines = [f"{display(type_id)}.{property_name} 허용값 정보:"]
    lines.extend(_describe(c) for c in found)
    return "\n".join(lines)


def service_quota(
    keyword: str, *, limit: int = 15, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """서비스 쿼터(계정/구독 단위 상한)를 키워드로 찾는다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    found = find_quota(capacity, keyword)
    if not found:
        return (
            f"'{keyword}' 에 해당하는 쿼터가 없습니다. "
            "현재 쿼터 데이터는 Azure만 수록돼 있습니다."
        )
    shown = found[: max(1, limit)]
    lines = [f"'{keyword}' 쿼터 {len(found)}건 중 {len(shown)}건:"]
    for quota in shown:
        parts = [f"기본 {quota.default}"] if quota.default is not None else []
        if quota.maximum is not None:
            parts.append(f"최대 {quota.maximum}")
        detail = ", ".join(parts) if parts else "값 없음"
        note = f" ※ {quota.note}" if quota.note else ""
        lines.append(
            f"  - [{quota.provider}] {quota.name}: {detail} "
            f"(출처 {quota.source_doc}, {describe(quota.basis)}){note}"
        )
    return "\n".join(lines)


def type_summary(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str | None:
    """이 타입에 대해 capacitykb가 **무엇을 쥐고 있는지** 한두 줄로. 모르면 None.

    판정을 하지 않는다 — 어느 도구를 부르면 되는지만 가리킨다. 실측에서
    "af-south-1에서 p5.48xlarge 되나"를 물었을 때 에이전트가
    `kb_describe_type('AWS::EC2::Instance')`을 부르고는 **제약 얘기가 한 글자도
    없으니 근거가 없다고 판단**해, 웹검색 13회를 14분간 돌고 "지식베이스에
    없습니다"라고 답했다. 그 순간 KB는 리전별 허용값 39건을 쥐고 있었다.

    데이터를 늘리는 것과 데이터에 닿게 하는 것은 다른 일이라, 다른 축의 입구에
    이 줄을 붙인다.

    산출물이 없으면 None이다 — 빌드 안내는 `cap_*` 도구의 몫이지 그래프 도구가
    떠들 일이 아니다.
    """
    capacity = load_merged(output_dir)
    if capacity is None:
        return None
    type_id, _ = _resolve(capacity, resource_type)
    if type_id is None or not capacity.covers(type_id):
        return None

    immutables = immutable_properties(capacity, type_id)
    others = [c for c in limits_for(capacity, type_id) if c.kind != "mutability"]
    if not immutables and not others:
        # **"제약 없음"과 "안 봤음"을 구분해서 말한다.** 침묵하면 모델이 둘을
        # 같은 뜻으로 읽고, 우리가 수록 범위 안이라고 아는 타입까지 웹으로 간다.
        return "용량·제약(capacitykb): 수록 범위 안이지만 이 타입에 걸린 제약은 없습니다."

    parts = []
    if others:
        by_prop = {}
        for c in others:
            by_prop.setdefault(c.property, 0)
            by_prop[c.property] += 1
        # **중첩 경로보다 최상위 속성을 먼저 보인다.** 개수순으로만 정렬했더니
        # `addonsConfig.dnsCacheConfig` 같은 게 알파벳 순으로 앞자리를 먹고
        # 정작 `InstanceType`·`Size`가 "외 159개"에 묻혔다. 사람이 묻는 것은
        # 최상위 속성이다.
        def rank(item):
            prop, count = item
            depth = prop.count("/") + prop.count(".")
            return (depth, -count, prop)

        top = sorted(by_prop.items(), key=rank)
        named = ", ".join(f"{p}({n}건)" if n > 1 else p for p, n in top[:4])
        more = f" 외 {len(top) - 4}개 속성" if len(top) > 4 else ""
        parts.append(f"제약 {len(others)}건 — {named}{more}")
    if immutables:
        names = [c.property for c in immutables]
        shown = ", ".join(names[:3])
        more = f" 외 {len(names) - 3}개" if len(names) > 3 else ""
        parts.append(f"변경 시 재생성되는 속성 {len(names)}개 — {shown}{more}")

    return (
        f"용량·제약(capacitykb): {' · '.join(parts)}\n"
        "  → 값이 되는지 판정은 cap_check_value(조건은 context로), "
        "허용값은 cap_allowed_values, 한도 전체는 cap_property_limits."
    )
