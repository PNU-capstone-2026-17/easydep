"""에이전트용 사전 정의 질의 API (capacitykb).

graphkb의 agent_api와 같은 관례: 예외 대신 에이전트가 그대로 읽을 수 있는
한국어 텍스트를 반환하고, 산출물이 없으면 빌드 명령을 안내한다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from capacitykb.model import CapacitySet
from capacitykb.query import (
    _cond_text,
    brief,
    check_value,
    find_quota,
    immutable_properties,
    limits_for,
    resolve_type,
)
from kbcommon import artifact
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
    "aws-conditional.json",
    "azure-mutability.json",
    "azure-secret.json",
    "alibaba-capacity.json",
    "tencent-capacity.json",
    "ibm-capacity.json",
    "ncp-capacity.json",
    "openstack-capacity.json",
    "oracle-capacity.json",
    "nhn-capacity.json",
)

#: 조건이 이보다 많으면 값을 나열하지 않고 "몇 가지에서 되는지"로 요약한다.
#: 리전은 38가지라 나열이 무의미하고, 볼륨 종류는 6가지라 값이 보여야 한다.
_SUMMARIZE_OVER = 8

_MISSING_MESSAGE = (
    "no capacity/constraint artifact. build it first with `python -m capacitykb "
    "build --source cfn|azure|azure-quota`."
)


def _plural(n: int, one: str, many: str) -> str:
    """`1 constraints` 같은 비문을 막는다.

    한국어에는 수 일치가 없어서 `f"{n}건"` 하나로 끝났다. 영어로 옮기면서
    그 자리가 전부 복수형 리터럴이 됐고, n=1일 때 문장이 깨진다. 모델이 읽는
    문장이므로 문법이 흐트러지면 신뢰도 신호가 같이 흐트러진다.
    """
    return f"{n} {one if n == 1 else many}"


def _recreating(n: int) -> str:
    """불변 속성 개수 문구. 명사와 동사가 **함께** 수 일치해야 한다."""
    if n == 1:
        return "1 property that recreates the resource when changed"
    return f"{n} properties that recreate the resource when changed"


@lru_cache(maxsize=4)
def _load_merged_cached(output_dir: str) -> CapacitySet | None:
    base = Path(output_dir)
    merged = CapacitySet()
    found = False
    for name in CAPACITY_FILES:
        # output/ 이 먼저, 없으면 저장소에 커밋된 data/*.gz (kbcommon/artifact.py).
        path = artifact.resolve(base, name)
        if path is not None:
            merged.merge(CapacitySet.from_dict(artifact.load_json(path)))
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
        "min": "min",
        "max": "max",
        "min_length": "min length",
        "max_length": "max length",
        "min_items": "min items",
        "max_items": "max items",
        "pattern": "pattern",
        "enum": "allowed values",
        "default": "default",
        "required": "required",
        "mutability": "mutability",
        # 형제 속성과의 관계. value가 관련 속성 목록이라 라벨만으로 뜻이 통해야 한다.
        "exactly_one_of": "exactly one of these",
        "at_least_one_of": "at least one of these",
        "conflicts_with": "properties it cannot be used with",
        "required_with": "properties it must be used with",
    }
    mutability_labels = {
        "create_only": "cannot be changed after creation (changing it recreates the resource)",
        "conditional_create_only": "conditionally unchangeable (recreates in some cases)",
        # **원본이 "바꿀 수 있다"고 적은 것.** 짐작으로 바꿀 수 있다는 뜻이 아니라,
        # 명세가 update를 허용 목록에 넣었다는 뜻이다.
        "updatable": "can be changed after creation (the spec marks update as allowed)",
        "read_only": "read-only (cannot be set)",
        # 불변/가변 이분법에 안 담기는 축. "늘리는 건 되고 줄이면 재생성" 같은 것이라,
        # 무엇이 조건인지는 note에 붙는 판정 함수 이름으로만 알 수 있다.
        "update_restricted": "conditional recreate (depends on which direction you change it)",
    }
    if constraint.kind == "mutability":
        text = mutability_labels.get(constraint.value, constraint.value)
    elif constraint.kind == "required":
        text = "required"
    elif constraint.kind == "secret":
        # 비밀값 — 배포 때 넣지만 API로 다시 못 읽는다. Key Vault 등으로 따로
        # 관리해야 한다는 신호라, 값(True)이 아니라 이 사실을 문장으로 보여준다.
        text = "secret value (set at deploy time, cannot be read back through the API — keep it safe)"
    else:
        # **긴 목록을 통째로 찍지 않는다.** 리전별 인스턴스 타입을 그대로 내보냈더니
        # 도구 응답 하나가 377,439자였다 — 모델 컨텍스트를 통째로 먹고 실측이 멈췄다.
        # check 경로에는 요약을 넣어 놓고 이쪽에 안 넣은 게 원인이었다.
        text = f"{labels.get(constraint.kind, constraint.kind)} {brief(constraint.value)}{unit}"
    tags = []
    # **조건을 안 보여주면 39줄이 전부 똑같아 보인다.** 리전별 허용값을 넣고서야
    # 드러났다 — 어느 조건에서의 값인지가 그 줄의 뜻 전부인 경우가 있다.
    if constraint.conditions:
        tags.append(
            "when " + " and ".join(_cond_text(c) for c in constraint.conditions)
        )
    if constraint.conditional:
        tags.append("conditional")
    tags.append(
        f"evidence {evidence_name(constraint.evidence)}, {describe(constraint.basis)}"
    )
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
        f"all {_plural(total, 'entry', 'entries')}: {text}."
        if total == len(constraints)
        else f"{total} of the {len(constraints)} entries above: {text}."
    )


def _nothing_found(capacity: CapacitySet, type_id: str, what: str) -> str:
    """"없다"와 "안 봤다"를 구분해 답한다.

    둘을 같은 문장으로 답하면 침묵이 사실로 읽힌다. 실측상 graphkb가 아는 벤더
    타입 5,547종 중 3,634종에 제약 레코드가 없고, 그중 GCP 527종은 **capacitykb가
    아예 안 읽어서** 없는 것이다. "제약 없음"이라고 답하면 그건 거짓이다.
    """
    if capacity.covers(type_id):
        return f"{what}: no known constraint (it is inside the collected scope, so 'none' is the answer)."
    provider = type_id.split("::", 1)[0]
    scanned = ", ".join(
        f"{e['provider']}({'all' if not e.get('scope') else '/'.join(e['scope'])})"
        for e in capacity.coverage
    )
    return "\n".join([
        f"{what} is **outside the collected scope**, so we do not know its constraints — that does not mean there are none.",
        f"  scope collected so far: {scanned or '(no record)'}",
        f"  to widen {provider}, the capacitykb build scope has to be extended.",
    ])


def _property_hint(capacity: CapacitySet, type_id: str, prop: str) -> str:
    """없는 속성 이름을 받았을 때 **우리가 아는 이름을 가리킨다.** 없으면 빈 문자열.

    실측: 에이전트가 RDS에 `InstanceType`을 물었다. EC2가 그 이름을 쓰니 자연스러운
    시도인데 RDS는 `DBInstanceClass`다. 우리는 "알려진 제약이 없어 판정할 수
    없습니다"라고만 답했고 — 938건을 쥐고도 막다른 길을 줬다. `p5.48xlarge`를
    타입으로 물었을 때와 같은 실패다.

    닮은 이름이 없으면 몇 개라도 보여준다. "그 이름이 아니다"만 알아도 다음 시도가
    달라진다.
    """
    import difflib

    known = sorted({c.property for c in limits_for(capacity, type_id)})
    if not known:
        return ""
    close = difflib.get_close_matches(prop, known, n=5, cutoff=0.5)
    if close:
        return f"  this type has no such property. did you mean: {', '.join(close)}"
    shown = ", ".join(known[:6])
    more = f" and {len(known) - 6} more" if len(known) > 6 else ""
    return f"  this type has no such property. properties we know: {shown}{more}"


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
        base = _nothing_found(capacity, type_id, target)
        hint = _property_hint(capacity, type_id, property_name) if property_name else ""
        return f"{base}\n{hint}" if hint else base
    header = display(type_id) + (f".{property_name}" if property_name else "")
    lines = [f"{header} — {_plural(len(found), 'constraint', 'constraints')}:"]
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
            asked = result.missing
            head = (
                f"depends on the condition: {target} varies with "
                f"{', '.join(asked)}, so we need those values to be definite."
            )
            allowed = [u for u in result.unresolved if u.endswith("→ allowed")]
            denied = [u for u in result.unresolved if u.endswith("→ not allowed")]
            # 조건이 몇 개 안 되면 **값을 그대로** 보여준다. 볼륨 종류 6가지에
            # "2가지에서 가능"만 말하면 정작 한도 숫자가 사라진다.
            # 리전 38개처럼 많을 때만 세어서 요약한다.
            if not (allowed or denied) or len(result.unresolved) <= _SUMMARIZE_OVER:
                return "\n".join([head, *(f"  - {u}" for u in result.unresolved)])
            # 조건이 38개(리전)씩 되면 전부 나열하는 게 답이 아니다.
            # **어디서 되고 어디서 안 되는지**를 세어 주는 편이 질문에 가깝다.
            lines = [
                head + f" of the {len(result.unresolved)} conditions, "
                f"**{len(allowed)} allow it**, {len(denied)} do not."
            ]
            for label, items in (("allowed", allowed), ("not allowed", denied)):
                if not items:
                    continue
                # 조건 절만 남긴다. 예전에는 `"= "`로 잘라 값만 뽑았는데, 조건이
                # 둘이 되고 정규식이 들어오면서 그 파싱이 통째로 무의미해졌다.
                names = [u.split(": ", 1)[0].removeprefix("when ") for u in items[:8]]
                more = f" and {len(items) - 8} more" if len(items) > 8 else ""
                lines.append(f"  {label}: {'; '.join(names)}{more}")
            return "\n".join(lines)
        if not result.references and not result.unevaluated and result.excluded:
            # 제약은 있는데 **준 조합에 걸리는 게 없다.** "모른다"와 다른 말이다.
            given = ", ".join(f"{k}={v!r}" for k, v in (context or {}).items())
            return (
                f"no verdict possible: {target} — this property has "
                f"{result.excluded} conditional constraints, but none of them match "
                f"the combination you gave ({given or 'no condition'}). the knowledge "
                "base does not know that combination, so re-check the values (engine "
                "and version spelling, etc.) or see the official documentation."
            )
        if not result.references and not result.unevaluated:
            hint = _property_hint(capacity, type_id, property_name)
            if hint:
                return (
                    f"{display(type_id)}.{property_name}: no known constraint.\n"
                    + hint
                )
            return (
                f"{display(type_id)}.{property_name}: no known constraint, so no "
                "verdict. it is not in the knowledge base — check the official "
                "documentation."
            )
        if not result.references:
            # 제약을 쥐고 있는데 평가만 못 한다. 이걸 "제약이 없다"로 답하면 거짓이다.
            return "\n".join([
                f"no definite verdict: {target} — there is a constraint, but we cannot read its regex.",
                *(f"  - {u}" for u in result.unevaluated),
                "  the regex in the source schema does not run on any engine (an "
                "upstream error). check the official documentation.",
            ])
        # 확정 근거는 없지만 참고 정보는 쥐고 있다 — 예전엔 이걸 버리고 "모른다"고 답했다.
        return "\n".join([
            f"no definite verdict: {target} — there is no confirmed constraint to "
            "judge it by. the reference information below is what we do have.",
            "  for reference (not stated by the source; not used in the verdict):",
            *(f"  - {r}" for r in result.references),
        ])
    if result.verdict == "violation":
        lines = [f"not allowed: {target} violates a constraint."]
        lines.extend(f"  - {v}" for v in result.violations)
    elif result.verdict == "advisory":
        # 확정 제약은 없지만 참고 정보상 벗어남 → "가능"이라고 하면 거짓 긍정이 된다
        lines = [
            f"verdict withheld: {target} does not violate any confirmed constraint, "
            "but it falls outside reference information pulled from prose that the "
            "source never stated. we suggest checking the official documentation."
        ]
    else:
        lines = [f"allowed: {target} satisfies the {result.checked} known constraints."]

    if result.unevaluated:
        lines.append("  ※ the constraints below could not go into the verdict — we cannot read their regex:")
        lines.extend(f"  - {u}" for u in result.unevaluated)
    if result.advisories or result.references:
        lines.append("  for reference (not stated by the source; not used in the verdict):")
        lines.extend(f"  - {a}" for a in result.advisories)
        lines.extend(f"  - {r}" for r in result.references)
    if result.unresolved:
        lines.append("  under other conditions the limit differs:")
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
        return f"{display(type_id)}: no property is known to be unchangeable."

    # 부모가 이미 불변이면 자식은 접는다. GCP를 넣고 나서 이게 문제가 됐다 —
    # KCC는 `Immutable.`을 부모와 자식 모두에 달아서 실측상 2,003건 중 913건(45.6%)이
    # 부모의 자식이었고, DataprocWorkflowTemplate은 185줄 중 179줄이 그랬다.
    # 값을 지우는 게 아니라 표시만 접는다(레코드는 원본 그대로 남는다).
    names = {c.property for c in found}
    shown = [c for c in found if not any(c.property.startswith(p + ".") for p in names)]
    folded = len(found) - len(shown)

    head = f"{display(type_id)} — {_recreating(len(shown))}"
    if folded:
        head += (
            f" ({_plural(folded, 'child property', 'child properties')} folded away"
            " — the parent is already immutable)"
        )
    # 낡음 고지를 **머리줄에** 붙인다. 꼬리말로 뒀더니 모델이 값만 옮기고 경고는
    # 뺐다 — 프롬프트로 "⚠는 반드시 옮기라"고 지시해도 그랬다(실측). 개수는 모델이
    # 반드시 옮기는 정보라, 거기 붙이면 경고만 따로 떼기 어렵다.
    caveat = _backend_footer(shown)
    lines = [f"{head}:" if not caveat else f"{head}. ⚠ {caveat}"]
    lines.extend(_describe(c) for c in shown)
    return "\n".join(lines)


def secrets(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """배포 때 넣지만 API로 다시 못 읽는 속성들(비밀번호·키·연결 문자열).

    배포 계획에 쓰인다 — 이 값들은 잃어버리면 다시 조회할 수 없으므로 Key Vault
    등으로 따로 관리해야 한다. 지금은 Azure만 이 표시(`x-ms-secret`)를 준다.
    """
    from capacitykb.query import secret_properties

    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    found = secret_properties(capacity, type_id)
    if not found:
        if not capacity.covers(type_id):
            return _nothing_found(capacity, type_id, display(type_id))
        # **"없다"의 뜻을 좁혀서 말한다.** 비밀값 표시는 Azure만 준다. AWS·GCP 타입에
        # 대고 "비밀값 없음"이라고 하면 없는 걸 확인해 준 것처럼 읽힌다.
        provider = type_id.split("::", 1)[0]
        if provider != "azure":
            return (
                f"{display(type_id)}: no secret-value information — only the Azure "
                f"specification carries this marker right now ({provider} is not "
                "included). that is 'this axis is not tracked', not 'no secret values'."
            )
        return f"{display(type_id)}: no property is marked as a secret value."

    lines = [
        f"{display(type_id)} — "
        f"{_plural(len(found), 'secret-value property', 'secret-value properties')} "
        "(set at deploy time, cannot be read back through the API — manage them in "
        "Key Vault or similar):"
    ]
    lines.extend(_describe(c) for c in found)
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
        return f"{display(type_id)}.{property_name}: no known allowed-value or pattern information."
    lines = [f"{display(type_id)}.{property_name} — allowed values:"]
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
            f"no quota matches '{keyword}'. "
            "quota data currently covers Azure only."
        )
    shown = found[: max(1, limit)]
    lines = [f"'{keyword}' — {len(shown)} of {len(found)} quotas:"]
    for quota in shown:
        parts = [f"default {quota.default}"] if quota.default is not None else []
        if quota.maximum is not None:
            parts.append(f"max {quota.maximum}")
        detail = ", ".join(parts) if parts else "no value"
        note = f" ※ {quota.note}" if quota.note else ""
        lines.append(
            f"  - [{quota.provider}] {quota.name}: {detail} "
            f"(source {quota.source_doc}, {describe(quota.basis)}){note}"
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
        return "capacity & constraints (capacitykb): inside the collected scope, but no constraint is attached to this type."

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
        named = ", ".join(f"{p}({n})" if n > 1 else p for p, n in top[:4])
        more = f" and {len(top) - 4} more properties" if len(top) > 4 else ""
        parts.append(f"{len(others)} constraints — {named}{more}")
    if immutables:
        names = [c.property for c in immutables]
        shown = ", ".join(names[:3])
        more = f" and {len(names) - 3} more" if len(names) > 3 else ""
        parts.append(f"{_recreating(len(names))} — {shown}{more}")

    return (
        f"capacity & constraints (capacitykb): {' · '.join(parts)}\n"
        "  → for a verdict on whether a value works use cap_check_value (conditions "
        "go in context), for allowed values cap_allowed_values, for every limit "
        "cap_property_limits."
    )


@lru_cache(maxsize=2)
def _value_index(output_dir: str) -> tuple[dict, dict] | None:
    """허용값 → 그 값이 나오는 (타입, 속성). 그리고 (타입, 속성)별 enum 제약 수.

    **없던 방향이다.** 지금까지 KB는 "타입을 주면 제약을 준다"만 할 수 있었다.
    실측에서 에이전트는 `p5.48xlarge`를 **타입 이름으로** 물었고 — 사람도 그렇게
    묻는다 — 우리는 "노드를 찾을 수 없습니다"만 내놨다. 값에서 속성으로 가는
    길이 없어서, 에이전트는 올바른 질문을 하고도 막다른 길을 만났다.

    싸다: 등장 78,482회지만 고유 값은 6,197개뿐이다(실측).
    """
    capacity = load_merged(output_dir)
    if capacity is None:
        return None
    index: dict[str, dict[tuple[str, str], int]] = {}
    totals: dict[tuple[str, str], int] = {}
    for c in capacity.constraints:
        if c.kind != "enum" or not isinstance(c.value, list):
            continue
        key = (c.type_id, c.property)
        totals[key] = totals.get(key, 0) + 1
        for v in c.value:
            if isinstance(v, str):
                slot = index.setdefault(v.lower(), {})
                slot[key] = slot.get(key, 0) + 1
    return index, totals


def value_lookup(
    name: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str | None:
    """이 이름이 어떤 속성의 **허용값**인지. 아니면 None.

    조건부일 때 "조건 38가지 중 14가지에서 허용"까지 말한다 — 그게 보통
    사람이 물은 것(`p5.48xlarge` 어느 리전에서 되나) 그 자체다.
    """
    built = _value_index(str(output_dir))
    if not built:
        return None
    index, totals = built
    hits = index.get(name.lower())
    if not hits:
        return None

    # 많이 나오는 (타입, 속성)이 먼저다. p5.48xlarge는 EC2::Instance에서 14번,
    # EMR::Cluster에서 12번 나온다 — 사람이 물은 쪽은 앞이다. 짐작이 아니라 사실이다.
    # 동점이면 중첩 경로보다 최상위 속성을 앞세운다. gp3는 8곳에서 전부 1번씩
    # 나오는데, 알파벳순으로 뒀더니 `EbsBlockDevice/VolumeType`이 앞자리를 먹었다.
    def rank(item):
        (type_id, prop), count = item
        return (-count, prop.count("/") + prop.count("."), type_id, prop)

    ranked = sorted(hits.items(), key=rank)
    lines = [f"'{name}' is not a type but a **value**. where it appears in capacitykb:"]
    for (type_id, prop), allowed in ranked[:4]:
        total = totals.get((type_id, prop), allowed)
        where = (
            f"allowed under {allowed} of {total} conditions"
            if total > 1
            else "included in the allowed values"
        )
        lines.append(f"  - {display(type_id)}.{prop} — {where}")
    if len(ranked) > 4:
        lines.append(f"  … and {len(ranked) - 4} more places")
    top_type, top_prop = ranked[0][0]
    lines.append(
        f"  → for which condition allows it, "
        f"cap_check_value('{display(top_type)}', '{top_prop}', '{name}') "
        "— pass the condition (region, volume type, etc.) in context and you get a "
        "definite verdict."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 리전 — botocore endpoints.json (`output/aws-endpoints.json`)
#
# 이 산출물은 CapacitySet(제약·쿼터)과 모양이 달라 `CAPACITY_FILES`에 넣지 않는다.
# "속성에 걸린 제약"이 아니라 "무엇이 어디에 있는가"라서 억지로 끼워 넣으면
# `type_id`·`property` 칸을 거짓으로 채우게 된다.
# --------------------------------------------------------------------------

ENDPOINTS_FILE = "aws-endpoints.json"

_ENDPOINTS_MISSING = (
    "no region artifact. build it with `python -m capacitykb build --source "
    "aws-endpoints`."
)

#: 엔드포인트가 목록에 없다는 것은 **못 쓴다는 뜻이 아니다.** 이 문장을 답변에
#: 반드시 함께 내보낸다 — 빼면 침묵이 "없음"으로 읽힌다.
_ABSENCE_CAVEAT = (
    "※ a region not listed here is not 'unusable' — **this data does not know**. "
    "a global service has only one endpoint, and the marker that tells one apart is "
    "on only 22 of the 307 services in the source."
)


#: **서비스 소재**(where_available)는 출처가 botocore라 AWS 전용이다. 리전 *이름*은
#: 이제 cloudinfo로 프로바이더 10곳을 알지만(`region_lookup`), 이 축은 아니다.
#: 밝히지 않으면 AWS만 본 답이 전체를 본 답처럼 보인다.
_AWS_ONLY_CAVEAT = (
    "※ which regions a service is in is included for **AWS only** (the source is the "
    "AWS SDK). this tool does not answer service availability for other providers."
)


@lru_cache(maxsize=4)
def _endpoints(output_dir: str) -> dict | None:
    path = artifact.resolve(Path(output_dir), ENDPOINTS_FILE)
    if path is None:
        return None
    try:
        return artifact.load_json(path)
    except Exception:
        return None


def _service_id(name: str, services: dict) -> tuple[str | None, str]:
    """CFN 타입이나 서비스 이름을 botocore 서비스 id로. 못 붙이면 (None, 이유).

    붙이는 방법은 둘뿐이다 — 그대로 일치, 하이픈 빼고 일치(`acmpca` → `acm-pca`).
    실측으로 우리 네임스페이스 281개 중 182개(65%)가 이렇게 붙고 **충돌은 0건**이다.
    남는 99개는 철자가 아니라 이름 자체가 다르다(`cloudwatch`는 원본에서
    `monitoring`, `cognito`는 `cognito-idp`). 규칙으로 못 맞히는 것을 짐작으로
    붙이면 **엉뚱한 서비스의 리전을 자신 있게 답하게 된다.**
    """
    text = name.strip()
    match = re.match(r"^(?:aws::)?AWS::([^:]+)::", text)
    key = (match.group(1) if match else text).lower()

    if key in services:
        return key, ""
    flat = {s.replace("-", ""): s for s in services}
    if key in flat:
        return flat[key], ""
    return None, key


def where_available(name: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> str:
    """이 서비스의 엔드포인트가 어느 리전에 있는가.

    `AWS::EC2::Instance` 처럼 CFN 타입으로 물어도 되고 `ec2` 처럼 서비스 이름으로
    물어도 된다. **없는 리전은 답하지 않는다** — 위 `_ABSENCE_CAVEAT` 참조.
    """
    data = _endpoints(str(output_dir))
    if data is None:
        return _ENDPOINTS_MISSING

    services = data.get("services", {}).get("aws") or {}
    service, unmatched = _service_id(name, services)
    if service is None:
        import difflib

        # 비슷한 이름을 **제안**한다. 골라서 답하지는 않는다 — 제안은 사용자가
        # 확인할 수 있지만, 골라 버리면 틀렸을 때 확인할 방법이 없다.
        near = difflib.get_close_matches(unmatched, services, n=4, cutoff=0.6)
        lines = [
            f"our data cannot pin down which AWS SDK service '{name}' is "
            f"(name looked up: '{unmatched}').",
            "  some service names are not regular — CloudWatch is `monitoring`, "
            "Cognito is `cognito-idp`, Certificate Manager is `acm`.",
        ]
        if near:
            lines.append(f"  similar names: {', '.join(near)}")
        lines.append(
            "  → ask again with the SDK service name. matching it by guess would "
            "confidently answer with the wrong service's regions, so we did not."
        )
        return "\n".join(lines)

    body = services[service]
    regions = body.get("regions") or []
    partitions = data.get("partitions", {}).get("aws", {}).get("regions") or {}

    if body.get("global"):
        return (
            f"{service}: a **global service** — the source states it is not tied to "
            f"a region (partitionEndpoint={body.get('partition_endpoint')}).\n"
            "  it is not something you pick a region to deploy into."
        )

    if not regions:
        return (
            f"{service}: no endpoint found in the standard partition.\n"
            f"  {_ABSENCE_CAVEAT}"
        )

    lines = [
        f"{service} — {_plural(len(regions), 'region', 'regions')} with an endpoint"
    ]
    for code in regions[:12]:
        label = (partitions.get(code) or {}).get("description")
        lines.append(f"  - {code}" + (f" ({label})" if label else ""))
    if len(regions) > 12:
        lines.append(f"  … and {len(regions) - 12} more")
    lines.append(f"  {_ABSENCE_CAVEAT}")
    lines.append(f"  {_AWS_ONLY_CAVEAT}")
    return "\n".join(lines)


# `region_lookup`(지명 → 리전 코드)은 envkb.regions로 이사했다(재편 계획 ⑤) —
# 리전 카탈로그는 envkb의 산출물이고, capacitykb가 그걸 임포트하면 KB→KB가 된다.


# --------------------------------------------------------------------------
# 작업 소요 — Azure LRO (`output/azure-operations.json`)
#
# 속성이 아니라 **작업**에 붙는 사실이라 CapacitySet에 안 들어간다.
# aws-endpoints와 같은 이유로 여기서 따로 읽는다.
# --------------------------------------------------------------------------

OPERATIONS_FILE = "azure-operations.json"

_OPERATIONS_MISSING = (
    "no operation-info artifact. build it with `python -m capacitykb build --source "
    "azure-operations`."
)


@lru_cache(maxsize=4)
def _operations(output_dir: str) -> dict[str, dict]:
    path = artifact.resolve(Path(output_dir), OPERATIONS_FILE)
    if path is None:
        return {}
    try:
        data = artifact.load_json(path)
    except Exception:
        return {}
    return {r["type_id"]: r for r in data.get("types") or []}


def operation_time(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """이 리소스의 생성·삭제·수정이 오래 걸리는가, 어떤 액션이 있는가.

    배포 스크립트의 타임아웃과 단계 나누기에 쓴다. 지금은 Azure만 이 표시를 준다.
    """
    index = _operations(str(output_dir))
    if not index:
        return _OPERATIONS_MISSING

    capacity = load_merged(output_dir)
    type_id = None
    if capacity is not None:
        type_id, _ = _resolve(capacity, resource_type)
    if type_id is None:
        # 제약이 하나도 없는 타입일 수 있다 — 작업 정보는 따로 있을 수 있으므로
        # 이름을 그대로/접두사 붙여 한 번 더 본다.
        for candidate in (resource_type, f"azure::{resource_type}"):
            if candidate in index:
                type_id = candidate
                break
    if type_id is None or type_id not in index:
        provider = (type_id or resource_type).split("::", 1)[0]
        if provider != "azure":
            return (
                f"no operation-duration information for '{resource_type}' — only "
                "**Azure** carries this marker right now. that is 'not tracked', "
                "not 'fast'."
            )
        return (
            f"{display(type_id or resource_type)}: no operation-duration information "
            "(the source did not describe this type's operations)."
        )

    record = index[type_id]
    lines = [f"{display(type_id)} — operation duration:"]
    for field, label in (("create", "create"), ("delete", "delete"), ("update", "update")):
        value = record.get(field)
        if value is None:
            # **없음과 즉시를 구분한다.** 원본이 말 안 한 것을 "빠르다"로 읽으면
            # 타임아웃을 짧게 잡게 된다.
            lines.append(f"  - {label}: the source does not say (unknown)")
        elif value:
            lines.append(f"  - {label}: **takes a long time** (asynchronous — you must wait for completion)")
        else:
            lines.append(f"  - {label}: immediate (the response is the completion)")

    actions = record.get("actions") or []
    if actions:
        slow = [a for a in actions if a["long_running"]]
        lines.append(f"  {len(actions)} actions ({len(slow)} long-running):")
        for action in actions[:8]:
            mark = "long-running" if action["long_running"] else "immediate"
            lines.append(f"    - {action['name']}: {mark}")
        if len(actions) > 8:
            lines.append(f"    … and {len(actions) - 8} more")
    return "\n".join(lines)
