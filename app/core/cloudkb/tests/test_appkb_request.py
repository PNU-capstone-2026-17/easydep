"""클라우드 리소스 제약 계약(RESOURCE_SPEC, appkb/request.json).

여기 테스트가 지키는 것 셋:
1. **필수 누락이 '왜'와 함께 나오는가** — 이 문장이 그대로 상류(easydep clarify
   게이트)의 되묻기 질문이 된다. "provider is required"로 되물으면 사용자는
   아무 값이나 채운다.
2. **설계 계약과 칸 정의가 같은가** — 같은 칸을 두 파일이 다르게 정의하면
   드리프트한다. $ref 배관 대신 대조 테스트로 고정한다.
3. **모든 칸에 소비자가 있는가** — multiZone을 받아 놓고 안 읽던 결함의 일반화.
   소비자 없는 칸은 계약에 들어오지 못한다.
"""

from __future__ import annotations

import pytest

from app.core import cloud_contract, input_registry
from app.core.cloudkb.appkb.contract import request_schema, schema, validate_request

#: 필수 목록의 진실은 **스키마**다(2026-08-01). 예전에는 `contract.REQUIRED_WHY`가
#: 사본을 들고 있었고, 그 사본이 "왜"까지 겸하는 바람에 질문 문구·근거·계층을 담을
#: 자리가 없었다. 지금 "왜"는 `app/core/input_registry.py`에 있다.
_REQUIRED = tuple(f for f in request_schema()["required"] if f != "schemaVersion")


def _spec(**overrides) -> dict:
    base = {
        "schemaVersion": "3",
        "provider": "aws",
        "region": "ap-northeast-2",
        "workloads": ["vm"],
        "containerRegistry": "depkb-registry",
        "monthlyBudgetUSD": 500,
        "expectedConcurrentUsers": 200,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


# --- 필수와 되묻기 문장 ---------------------------------------------------------

def test_minimal_valid_spec_passes() -> None:
    assert validate_request(_spec()) == []


@pytest.mark.parametrize("field", sorted(_REQUIRED))
def test_missing_required_says_why(field: str) -> None:
    """누락 메시지에 칸 이름과 **왜 필요한지**가 같이 있어야 한다.

    **층이 갈렸다**(2026-08-01): 모양 검증(`validate_request`)은 이유를 모르고
    칸 이름만 말한다. 이유를 붙이는 것은 레지스트리를 아는 `cloud_contract`다 —
    그래야 같은 문장이 되묻기·화면·검증에서 한 곳에서 나온다.
    """
    problems = cloud_contract.validate(_spec(**{field: None}))
    matching = [p for p in problems if p.startswith(f"[required] {field}")]
    assert len(matching) == 1
    assert cloud_contract.why(field) in matching[0]
    assert cloud_contract.question(field), f"{field}에 사용자에게 할 말이 없다"


def test_either_scale_signal_is_accepted() -> None:
    """규모 신호는 동시 사용자 **또는** RPS로 온다 — 둘 다 받는다.

    (필수 여부는 `test_scale_signal_is_no_longer_required`가 따로 본다. 여기서
    지키는 것은 **두 표현을 다 받는가**이고, 그건 재판정 뒤에도 그대로다.)
    """
    assert validate_request(_spec()) == []
    rps_only = _spec(expectedConcurrentUsers=None, approxRequestsPerSecond=30.0)
    assert validate_request(rps_only) == []


def test_scale_signal_is_no_longer_required() -> None:
    """**2026-07-29 재판정.** 규모 신호는 필수가 아니다.

    계약의 판정식은 "그 칸이 없으면 뒤 단계 산출물의 요구사항 부합을 잴 수 없는 것만
    필수"인데, 규모를 거기 걸어 보면 **이것으로 서는 판정이 하나도 없다** — `verify`의
    규모 줄은 "스펙이 충분한지 판정할 수 없다"이고, 동시 사용자를 스펙으로 바꾸는
    변환은 소스가 없어 KB에서 배제돼 있다.

    대신 **권고 계층**에 남는다. 필수를 줄이는 방향이라 그때는 기존 명세가 전부
    그대로 유효해서 schemaVersion을 올리지 않았다(판 2에서 `workloads`를 **더한**
    것과 방향이 반대다 — 그쪽은 판을 올렸다).
    """
    assert validate_request(_spec(expectedConcurrentUsers=None)) == []
    assert not any(f in cloud_contract.suggested_fields({})
                   for f in ("provider", "region"))
    # **2026-08-01에 한 단계 더 내려갔다**: 권고 → 맥락. 권고의 정의는 "채우면
    # 이름 붙은 판정이 하나 선다"인데 이 값으로 서는 판정이 없다(`verify`의 규모
    # 줄은 문자 그대로 *"cannot judge"*다). 그렇다고 빼지는 않았다 —
    # `sizing_floor.undecided_note`가 되묻기의 근거로 쓴다.
    assert "expectedConcurrentUsers" not in cloud_contract.suggested_fields({})
    assert "expectedConcurrentUsers" in cloud_contract.context_fields({})
    assert input_registry.tier_of("expectedConcurrentUsers") == \
        input_registry.CONTEXT
    # jsonschema의 뭉개진 anyOf 문구가 새어 나오면 안 된다(anyOf 자체가 사라졌다).
    assert not any("is not valid under any"
                   in p for p in validate_request(_spec(expectedConcurrentUsers=None)))


def test_unknown_field_is_rejected() -> None:
    """소비자 없는 칸은 계약이 받지 않는다 — 받아 놓고 안 읽는 칸이 그렇게 생긴다."""
    problems = validate_request(_spec(runtime="python3.12"))
    assert any("runtime" in p for p in problems)


def test_wrong_shape_surfaces_field_path() -> None:
    problems = validate_request(_spec(monthlyBudgetUSD=-10))
    assert any("monthlyBudgetUSD" in p for p in problems)


def test_non_dict_input_is_reported_not_crashed() -> None:
    assert validate_request(["not", "a", "dict"])  # type: ignore[arg-type]


def test_empty_spec_lists_every_required_at_once() -> None:
    """상류는 에이전트다 — 하나씩 흘리면 되묻기가 N번 왕복된다. 한 번에 다."""
    problems = validate_request({})
    for field in ("schemaVersion", *_REQUIRED):
        assert any(field in p for p in problems), field
    # 규모 신호는 2026-07-29에 필수에서 내려왔다 — 여기 없는 것이 맞다.
    assert not any("no scale signal" in p for p in problems)


# --- 설계 계약과의 드리프트 방지 ------------------------------------------------

def test_design_requirements_mirror_request_contract() -> None:
    """schema.json의 requirements는 request.json의 **투영**이다 — 같은 칸은
    정의가 바이트까지 같아야 한다. 두 벌이면 드리프트한다."""
    design_props = schema()["properties"]["requirements"]["properties"]
    request_props = request_schema()["properties"]
    shared = set(design_props) & set(request_props)
    # 투영이 빈 껍데기가 아닌지 — 값 조인·판정에 쓰는 칸이 전부 내려와야 한다.
    assert {"provider", "region", "expectedConcurrentUsers",
            "approxRequestsPerSecond", "monthlyBudgetUSD", "multiZone"} <= shared
    for name in sorted(shared):
        assert design_props[name] == request_props[name], name


def test_request_schema_rejects_extras_like_design_schema() -> None:
    assert request_schema()["additionalProperties"] is False


# --- 소비자 대응표 --------------------------------------------------------------

def test_every_field_has_a_declared_consumer() -> None:
    """**모든 칸에 소비자가 있는가** — multiZone을 받아 놓고 안 읽던 결함의 일반화.

    2026-08-01까지 이 검사는 **여기 손으로 적은 표**와 스키마를 대조했다. 표가
    사본이라 소비자를 바꾸면 두 곳을 고쳐야 했고, 게다가 그 표는 테스트 안에만
    있어서 **되묻기가 그 문장을 쓸 수 없었다** — 사용자에게 "왜 필요한지"를
    말해야 하는 자리에서 정작 그 문장에 손이 안 닿았다.

    지금은 `app/core/input_registry.py`가 항목마다 `opens`(소비자)와
    `basis`(그 소비자가 실재한다는 좌표)를 들고 있고, 소비자 없는 항목은 **만들
    수조차 없다**. 여기서는 그 규율이 스키마를 다 덮는지만 본다.
    """
    schema_fields = set(request_schema()["properties"])
    asked = {a.spec_field: a for a in input_registry.ASKS if a.spec_field}
    declined = input_registry.NOT_ASKED
    assert schema_fields == set(asked) | set(declined), (
        f"소비자가 선언되지 않은 칸: {schema_fields - set(asked) - set(declined)}")
    for field, ask in asked.items():
        assert ask.opens.strip(), field
        assert ask.basis, field
    for field, why in declined.items():
        assert why.strip(), f"{field}: 안 묻는 이유가 비어 있다"
