"""`RESOURCE_SPEC` 계약의 접근점 — 무엇을 받아야 하고 **왜** 받아야 하는가.

정의는 `app/core/cloudkb/appkb`에 있다(`request.json` 스키마와 `contract.py`의 검증).
여기서는 그것을 요구사항 에이전트가 쓸 수 있게 열어 주고, **생산자 쪽에 필요한 두
가지**를 덧붙인다.

  - `missing_fields()` — 무엇이 아직 안 채워졌는가. 검증 메시지 문자열을 파싱하지 않고
    칸 이름으로 돌려준다. 문자열을 파싱하면 메시지 문구를 고치는 순간 조용히 깨진다.
  - `why()` — 그 칸이 왜 필요한가. `REQUIRED_WHY`가 이미 갖고 있는 문장이고, 이것이
    그대로 사용자에게 되묻는 질문이 된다. **왜 없이 되물으면 사용자가 아무 값이나
    채운다**는 것이 그 상수의 존재 이유다.

`schema_fields()`를 노출하는 이유는 별도다: 관심사의 소비자 선언이 실재하는 칸인지
테스트가 확인하는데, 그 목록을 손으로 적어 두면 스키마가 바뀔 때 검사가 거짓이 된다.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.cloudkb.appkb import contract as _contract

#: 필수 칸 → **왜 필수인가.** 되묻기 질문의 원본이다.
REQUIRED_WHY: dict[str, str] = dict(_contract.REQUIRED_WHY)

#: 규모 신호 — 둘 중 하나면 된다. **2026-07-29에 필수에서 내려왔다**: 판정식을 다시
#: 걸어 보니 이 값으로 서는 판정이 하나도 없었다. 지금은 권고이고, 소비자는 계획의
#: 규모 진술과 되묻기의 근거다.
SCALE_FIELDS: tuple[str, ...] = tuple(_contract.SCALE_FIELDS)
SCALE_WHY: str = _contract.SCALE_WHY

#: 필수는 아니지만 **없으면 판정이 하나 닫히는** 칸 → 왜.
#:
#: 되묻기가 두 종류가 된다: 못 채우면 나아갈 수 없는 것(`REQUIRED_WHY`)과, 채우면
#: 판정이 하나 열리는 것(여기). 둘을 같은 얼굴로 물으면 사용자가 다 필수로 읽고,
#: 안 물으면 계획을 다 만든 뒤에야 "그걸 줬으면 판정이 섰다"를 알게 된다.
SUGGESTED_WHY: dict[str, str] = dict(_contract.SUGGESTED_WHY)


@lru_cache(maxsize=1)
def schema_fields() -> frozenset[str]:
    """`RESOURCE_SPEC`이 아는 칸 이름 전부(스키마에서 읽는다)."""
    return frozenset(_contract.request_schema().get("properties", {}))


@lru_cache(maxsize=1)
def _properties() -> dict[str, dict]:
    return dict(_contract.request_schema().get("properties", {}))


def field_type(field: str) -> str:
    """칸의 타입(`string`·`number`·`integer`·`boolean`). `enum` 칸이면 `"enum"`, 모르면 빈 문자열.

    **생산자가 스키마 사실을 옮겨 적지 않게 하려고 연다.** 이것이 없으면 부르는 쪽이
    "이 칸은 문자열", "이 칸은 steady|spiky"를 자기 목록으로 다시 적게 되고, 실제로 그
    목록이 스키마와 어긋났다(`regionAsWritten`이 빠져 있었다).
    """
    spec = _properties().get(field)
    if not spec:
        return ""
    if "enum" in spec:
        return "enum"
    kind = spec.get("type", "")
    return kind if isinstance(kind, str) else ""


def field_enum(field: str) -> tuple[str, ...]:
    """`enum` 칸이 허용하는 값들. 아니면 빈 튜플."""
    return tuple(_properties().get(field, {}).get("enum", ()))


def validate(spec: dict) -> list[str]:
    """계약 검증. 빈 목록이면 통과.

    문제를 예외가 아니라 **목록으로** 돌려주는 성질을 그대로 물려받는다 — 상류가
    에이전트라 첫 오류에서 멈추면 고치고 다시를 반복하게 된다.
    """
    return _contract.validate_request(spec)


def missing_fields(spec: dict) -> tuple[str, ...]:
    """아직 못 채운 **필수** 칸 — 검증 메시지가 아니라 칸 이름으로.

    규모 신호는 2026-07-29에 필수에서 내려왔으므로 여기 안 나온다. 권고 칸은
    `suggested_fields()`가 따로 준다 — 둘을 섞으면 사용자가 전부 필수로 읽는다.
    """
    return tuple(name for name in REQUIRED_WHY if name not in spec)


def suggested_fields(spec: dict) -> tuple[str, ...]:
    """비어 있는 **권고** 칸 — 채우면 판정이 하나 열린다.

    규모 신호 두 칸은 **하나만 있으면 됐다고 본다**(같은 것을 두 번 묻지 않는다).
    하한도 마찬가지로 한 축만 있으면 스펙 선택이 열리므로 더 묻지 않는다.
    """
    pairs = (SCALE_FIELDS, ("minVCpu", "minMemoryGiB"))
    satisfied: set[str] = set()
    for pair in pairs:
        if any(name in spec for name in pair):
            satisfied |= set(pair)
    return tuple(
        name for name in SUGGESTED_WHY
        if name not in spec and name not in satisfied
    )


def why(field: str) -> str:
    """그 칸이 왜 필요한가. 모르는 칸이면 빈 문자열.

    **모르는 칸에 그럴듯한 이유를 지어내지 않는다** — 되묻기 문구는 계약이 실제로 적어
    둔 이유일 때만 값이 있다.
    """
    if field in REQUIRED_WHY:
        return REQUIRED_WHY[field]
    if field in SUGGESTED_WHY:
        return SUGGESTED_WHY[field]
    if field in SCALE_FIELDS:
        return SCALE_WHY
    return ""
