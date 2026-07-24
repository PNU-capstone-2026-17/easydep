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

from appkb.contract import (
    REQUIRED_WHY,
    SCALE_FIELDS,
    request_schema,
    schema,
    validate_request,
)


def _spec(**overrides) -> dict:
    base = {
        "schemaVersion": "1",
        "provider": "aws",
        "region": "ap-northeast-2",
        "monthlyBudgetUSD": 500,
        "expectedConcurrentUsers": 200,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


# --- 필수와 되묻기 문장 ---------------------------------------------------------

def test_minimal_valid_spec_passes() -> None:
    assert validate_request(_spec()) == []


@pytest.mark.parametrize("field", sorted(REQUIRED_WHY))
def test_missing_required_says_why(field: str) -> None:
    """누락 메시지에 칸 이름과 **왜 필요한지**가 같이 있어야 한다."""
    problems = validate_request(_spec(**{field: None}))
    matching = [p for p in problems if p.startswith(f"[필수] {field}")]
    assert len(matching) == 1
    assert REQUIRED_WHY[field] in matching[0]


def test_scale_signal_either_field_satisfies() -> None:
    """규모 신호는 동시 사용자 **또는** RPS — 하나면 된다."""
    assert validate_request(_spec()) == []
    rps_only = _spec(expectedConcurrentUsers=None, approxRequestsPerSecond=30.0)
    assert validate_request(rps_only) == []


def test_no_scale_signal_is_a_named_violation() -> None:
    problems = validate_request(_spec(expectedConcurrentUsers=None))
    scale = [p for p in problems if "규모 신호" in p]
    assert len(scale) == 1
    for field in SCALE_FIELDS:
        assert field in scale[0]
    # jsonschema의 뭉개진 anyOf 문구가 새어 나오면 안 된다.
    assert not any("is not valid under any" in p for p in problems)


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
    for field in ("schemaVersion", *REQUIRED_WHY):
        assert any(field in p for p in problems), field
    assert any("규모 신호" in p for p in problems)


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

#: 칸 → 그 칸을 읽는 곳. **여기 없는 칸은 계약에 넣을 수 없다** — 표가 스키마와
#: 어긋나면 아래 테스트가 실패한다. 소비자를 지우면 칸도 지워야 한다.
_CONSUMERS = {
    "schemaVersion": "appkb.contract.validate_request — 계약 버전 검사",
    "provider": "nim_agent.design_tools.compose(값 조인 라우팅) · "
                "appkb.verify.verify_against_requirements(프로바이더 대조)",
    "region": "design_tools._attach_values — costkb 리전 조인",
    "regionAsWritten": "되짚기용 원문 — 해석이 틀렸을 때 사람이 확인 "
                       "(REFINE_REQ가 원문을 남기는 것과 같은 이유)",
    "monthlyBudgetUSD": "appkb.verify.verify_against_requirements — 예산 비대칭 판정",
    "expectedConcurrentUsers": "design_tools._attach_values(사이징 최소치) · "
                               "verify_against_requirements(규모 판정 불가 명시)",
    "approxRequestsPerSecond": "verify_against_requirements — 규모 판정 불가 명시",
    "multiZone": "design_tools._subnet_notes · verify_against_requirements",
    "meta": "자유 메타 — 계약이 뜻을 정하지 않는 유일한 칸",
}


def test_every_field_has_a_declared_consumer() -> None:
    assert set(request_schema()["properties"]) == set(_CONSUMERS)
    assert all(v.strip() for v in _CONSUMERS.values())
