"""capacitykb 산출물의 레코드 간 불변식.

제약은 `(type_id, property, kind)` 하나가 레코드 하나다. 그래서 같은 속성의
`default`와 `min`은 **서로 다른 레코드**이고, 스키마 검사로는 둘의 모순을 볼 수 없다.
여기가 그걸 보는 자리다. 얼개는 `kbcommon/invariants.py` 참고.
"""

from __future__ import annotations

import collections
from collections.abc import Iterable

from kbcommon.invariants import Invariant, Violation, one_confidence_per_evidence


def _by_property(dataset: dict) -> dict[tuple[str, str], dict[str, dict]]:
    """`(타입, 속성)` → `{kind: 제약}`. 모순은 이 묶음 안에서 보인다."""
    grouped: dict[tuple[str, str], dict[str, dict]] = collections.defaultdict(dict)
    for c in dataset.get("constraints") or []:
        grouped[(c.get("type_id"), c.get("property"))][c.get("kind")] = c
    return grouped


def _default_within_bounds(dataset: dict) -> Iterable[Violation]:
    for (type_id, prop), kinds in _by_property(dataset).items():
        default = kinds.get("default")
        if default is None or not isinstance(default.get("value"), (int, float)):
            continue
        for bound, bad in (("min", lambda d, b: d < b), ("max", lambda d, b: d > b)):
            limit = kinds.get(bound)
            if limit is None or not isinstance(limit.get("value"), (int, float)):
                continue
            if bad(default["value"], limit["value"]):
                yield Violation(
                    where=f"{type_id} {prop}",
                    detail=f"default={default['value']}인데 {bound}={limit['value']}",
                )


def _read_only_is_not_required(dataset: dict) -> Iterable[Violation]:
    for (type_id, prop), kinds in _by_property(dataset).items():
        mutability = kinds.get("mutability") or {}
        required = kinds.get("required") or {}
        if mutability.get("value") == "read_only" and required.get("value") is True:
            yield Violation(
                where=f"{type_id} {prop}",
                detail="읽기 전용인데 필수로 표시됨",
            )


INVARIANTS = (
    Invariant(
        name="default-within-bounds",
        question="기본값이 자기 속성의 min·max 안에 있는가?",
        severity="error",
        # 상류 스키마가 실제로 모순돼 있다(`Period: default=0, minimum=10`). 하지만
        # 그대로 실으면 에이전트가 **스키마가 거부할 값**을 권한다. 값을 지어낼 수는
        # 없으니 파서가 그런 default를 버린다 — 자세한 이유는 capacitykb/parsers/cfn.py.
        check=_default_within_bounds,
    ),
    Invariant(
        name="read-only-not-required",
        question="읽기 전용 속성이 필수로 표시되지 않았는가?",
        severity="error",
        # CFN의 `definitions.X.required`는 "응답에 늘 들어 있다"는 뜻인데, 파서가
        # 이를 "네가 채워야 한다"로 옮겼다. 사용자에게 못 채우는 칸을 채우라고 하게 된다.
        check=_read_only_is_not_required,
    ),
    Invariant(
        name="one-confidence-per-evidence",
        question="같은 근거 라벨에 신뢰도가 하나만 붙는가?",
        # report인 이유: 고치려면 라벨을 쪼개거나 척도를 다시 설계해야 한다(R4).
        # 지금 위반을 없애자고 숫자를 맞추면 근거 없는 수치가 하나 더 생길 뿐이다.
        severity="report",
        check=one_confidence_per_evidence("constraints", "quotas"),
    ),
)
