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

from app.core.cloudkb.appkb.contract import (
    REQUIRED_WHY,
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
    matching = [p for p in problems if p.startswith(f"[required] {field}")]
    assert len(matching) == 1
    assert REQUIRED_WHY[field] in matching[0]


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

    대신 `SUGGESTED_WHY`에 권고로 남는다. 필수를 **줄이는** 방향이라 기존 명세는
    전부 그대로 유효하고, 그래서 schemaVersion을 올리지 않았다.
    """
    from app.core.cloudkb.appkb.contract import SUGGESTED_WHY

    assert validate_request(_spec(expectedConcurrentUsers=None)) == []
    assert not any(f in SUGGESTED_WHY for f in ("provider", "region"))
    assert "expectedConcurrentUsers" in SUGGESTED_WHY
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
    for field in ("schemaVersion", *REQUIRED_WHY):
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
    # **2026-07-29 정정**: 규모 신호는 더 이상 하한을 정하지 않는다. 예전 소비자
    # ("사이징 최소치")는 `users <= 500 → (2,4)`라는 출처 없는 계수였고, 그건 KB가
    # 명시적으로 배제한 변환이었다. 지금 소비자는 되묻기의 근거다.
    "expectedConcurrentUsers": "sizing_floor.undecided_note(하한이 없을 때 되묻기의 "
                               "근거) · verify_against_requirements(규모 판정 불가 "
                               "명시)",
    "approxRequestsPerSecond": "sizing_floor.undecided_note · "
                               "verify_against_requirements — 규모 판정 불가 명시",
    "minVCpu": "nim_agent.sizing_floor.resolve(층 2 — 스펙 선택의 하한) · "
               "design_tools._attach_values(하한이 있어야 스펙을 고른다) · "
               "verify._CLOSES(없으면 스펙 선택 자체가 안 열린다)",
    "minMemoryGiB": "nim_agent.sizing_floor.resolve — minVCpu와 같은 층·같은 소비자",
    "multiZone": "design_tools._subnet_notes · verify_against_requirements",
    "trafficPattern": "verify_against_requirements — 버스트 적합 판정(⑥-A에서 열림)",
    "stateless": "verify_against_requirements — 서버리스 적합 판정(⑥-A에서 열림)",
    "dataResidency": "design_tools.compose(리전 원본 표시 이름 대조 노트 — envkb) · "
                     "verify_against_requirements(**판정 불가 명시** — 국가 판정 "
                     "소스가 없어 대조 자료까지가 소비다. 2층 보강에서 열림)",
    "lowCarbonPreferred": "design_tools._global_notices(선택 리전의 탄소집약도와 "
                          "**같은 프로바이더 안에서 더 낮은 리전 수** — envkb) · "
                          "verify_against_requirements(**판정이 아니라 자료** — "
                          "옮길지는 지연·레지던시와의 상충이라 우리가 못 잰다). "
                          "관심사 `cn.carbon-constraint`가 이 칸으로 흘러든다 — "
                          "탄소 161건이 질의응답에만 쓰이던 것을 계획에 이었다",
    "meta": "자유 메타 — 계약이 뜻을 정하지 않는 유일한 칸",
}


def test_every_field_has_a_declared_consumer() -> None:
    assert set(request_schema()["properties"]) == set(_CONSUMERS)
    assert all(v.strip() for v in _CONSUMERS.values())
