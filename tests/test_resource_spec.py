"""제약 구조화(A 트랙)의 규율 — `steps/step_resource.py`.

이 파일이 지키는 것은 하나로 모인다: **못 알아들은 것을 채우지 않는다.**

  - 계약을 만족할 때만 `resource_spec`이 존재한다(반쯤 채운 사양은 없느니만 못하다).
  - 단가를 예산으로, 아무 숫자나 규모로 세지 않는다(둘 다 실측 코퍼스에서 나온 함정이다).
  - 모호하면 값이 아니라 **질문**이 된다.
  - 되묻기 문구는 지어내지 않고 계약의 `why()`를 그대로 쓴다.
"""
from __future__ import annotations

from app.core import cloud_contract
from app.requirements.agent.steps import step_resource as sr


def _state(*texts: str, constraints: str = "") -> dict:
    state: dict = {
        "classified": [
            {"id": f"NFR-{i:02d}", "text": text, "type": "NFR"}
            for i, text in enumerate(texts, start=1)
        ]
    }
    if constraints:
        state["resource_constraints_text"] = constraints
    return state


def _intake(*texts: str, constraints: str = "") -> tuple[dict, dict]:
    result = sr.build_resource_spec(_state(*texts, constraints=constraints))
    return result, result["resource_intake"]


# --- 산출물이 존재하는 조건 --------------------------------------------------
def test_a_half_filled_spec_never_leaves_this_step():
    """필수 칸이 비면 산출물을 내지 않는다 — 뒤 단계가 그걸 사양으로 알고 조인을 돌린다."""
    result, intake = _intake("The system shall support 100 concurrent users.")

    assert "resource_spec" not in result
    assert intake["valid"] is False
    # 그래도 **작업 기록은 남는다** — 왜 못 채웠는지가 사라지면 빈 칸의 뜻을 알 수 없다.
    assert intake["draft"]["expectedConcurrentUsers"] == 100
    assert intake["errors"]


def test_a_complete_contract_produces_the_artifact():
    result, intake = _intake(
        "The service shall support 5 000 concurrent users.",
        constraints="Deploy on aws in Seoul. Budget is $3,000 per month.",
    )

    assert intake["valid"] and not intake["errors"]
    assert result["resource_spec"]["schemaVersion"] == sr.SCHEMA_VERSION
    # 지명은 **코드로** 담기고 원문은 따로 남는다 — 조인이 코드로만 돈다.
    assert result["resource_spec"]["region"] == "ap-northeast-2"
    assert result["resource_spec"]["regionAsWritten"] == "Seoul"


# --- 함정 (둘 다 실측 코퍼스에서 나왔다) -------------------------------------
def test_a_unit_price_is_not_a_monthly_budget():
    """`$0.02 per GB-month`(iot_telemetry/NFR-08)는 예산이 아니라 단가다."""
    _result, intake = _intake(
        constraints="Storage cost shall not exceed $0.02 per GB-month.",
    )

    assert "monthlyBudgetUSD" not in intake["draft"]
    assert any(r["field"] == "monthlyBudgetUSD" and "단가" in r["why"]
               for r in intake["rejected"])


def test_a_number_with_a_concurrency_word_is_not_a_user_count():
    """`100 simultaneous icons`(PURE/tcs)는 동시 사용자가 아니다. 주체를 본다."""
    _result, intake = _intake(
        "The TCS shall provide the capability of displaying overlays each containing "
        "100 simultaneous icons of known fire support coordination measures.",
        "The control software shall allow simultaneous operation of up to six active "
        "control nodes.",
    )

    assert "expectedConcurrentUsers" not in intake["draft"]


def test_a_spelled_out_number_and_thin_spaces_still_count():
    """`one million` · `10 000`(얇은 공백)이 실측 코퍼스의 실제 표기다."""
    _result, intake = _intake(
        "The service shall scale to one million concurrent video streams.",
        "The platform shall support up to 10 000 concurrent shoppers.",
    )

    seen = {c["value"] for c in intake["provenance"]
            if c["field"] == "expectedConcurrentUsers"}
    assert seen == {1_000_000, 10_000}
    # 서로 다른 두 값이 나왔으므로 **채우지 않고 묻는다.**
    assert "expectedConcurrentUsers" not in intake["draft"]
    assert any(q["kind"] == sr.AMBIGUOUS for q in intake["questions"])


# --- 모호하면 묻는다 ---------------------------------------------------------
def test_a_currency_we_cannot_convert_becomes_a_question():
    """환율 소스가 없다 — 계약이 환산을 거부하므로 값이 아니라 질문이 된다."""
    _result, intake = _intake(constraints="월 예산은 300만원이다.")

    assert "monthlyBudgetUSD" not in intake["draft"]
    assert any("USD" in r["why"] for r in intake["rejected"])
    assert any(q["field"] == "monthlyBudgetUSD" and q["kind"] == sr.UNCONVERTIBLE
               for q in intake["questions"])


def test_a_place_name_that_matches_many_providers_is_not_narrowed():
    """프로바이더를 모르면 '서울'은 여러 곳에 걸린다. 우리가 고르면 사용자 뜻인 양 보인다."""
    _result, intake = _intake(constraints="Deploy in Seoul.")

    assert "region" not in intake["draft"]
    assert any(r["field"] == "region" for r in intake["rejected"])


def test_the_constraint_text_wins_but_the_conflict_is_recorded():
    """조용히 덮으면 요구사항 쪽이 사라진다. 이기되 충돌을 남긴다."""
    _result, intake = _intake(
        "The system shall support 100 concurrent users.",
        constraints="We expect 9 000 concurrent users.",
    )

    assert intake["draft"]["expectedConcurrentUsers"] == 9_000
    assert any(q["kind"] == sr.CONFLICT and q["field"] == "expectedConcurrentUsers"
               for q in intake["questions"])


# --- 되묻기는 계약의 말로 한다 -----------------------------------------------
def test_questions_quote_the_contract_reason_verbatim():
    """이유 없이 물으면 사용자가 아무 값이나 채운다 — `REQUIRED_WHY`의 존재 이유다."""
    _result, intake = _intake("The system shall support 100 concurrent users.")

    asked = {q["field"]: q for q in intake["questions"]}
    for name in ("provider", "region", "monthlyBudgetUSD"):
        assert name in asked, name
        assert asked[name]["why"] == cloud_contract.why(name)
        assert asked[name]["why"] in asked[name]["question"]


def test_every_asked_field_is_one_the_contract_actually_has():
    """계약에 없는 칸을 물으면 사용자가 채워도 갈 곳이 없다."""
    _result, intake = _intake(constraints="Deploy on aws with a $1,000 per month budget.")

    known = cloud_contract.schema_fields()
    assert {q["field"] for q in intake["questions"]} <= known
    assert set(intake["draft"]) <= known


# --- 어디서 캐고 어디서 안 캐는가 --------------------------------------------
def test_budget_and_region_are_not_mined_from_requirement_prose():
    """실측: 요구사항 산문에 provider·region·예산은 0건이다. 없는 곳을 뒤지면 오탐만 남는다."""
    _result, intake = _intake(
        "RideNow shall scale elastically across multiple cloud regions to handle peak "
        "demand spikes.",
        "The subscription shall cost $9 per month for each user.",
    )

    assert "region" not in intake["draft"]
    assert "monthlyBudgetUSD" not in intake["draft"]
    # 규모·부하 모양은 요구사항에서 캔다 — 거기 실제로 있기 때문이다.
    assert intake["draft"]["trafficPattern"] == "spiky"


def test_evidence_points_at_the_matched_span_not_the_sentence():
    """근거가 문장 앞부분이면 값이 왜 들어갔는지 안 보인다(멀쩡한 추출이 오탐처럼 읽혔다)."""
    _result, intake = _intake(
        "SensorGrid shall achieve ninety-nine point nine-five percent monthly "
        "availability, with automatic failover across availability zones.",
    )

    evidence = [c for c in intake["provenance"] if c["field"] == "multiZone"]
    assert evidence and "availability zones" in evidence[0]["as_written"].lower()


def test_a_burst_that_is_not_load_is_not_a_traffic_shape():
    """`TCS data burst messages`(PURE)는 메시지 형식이지 부하 모양이 아니다.

    낱말 하나만 보던 판이 PURE 7,659문장에서 낸 유일한 후보가 이것이었다. 부하 맥락을
    함께 요구하도록 좁힌 뒤 오탐이 사라졌고, 참 사례("peak demand spikes")는 남았다.
    """
    _result, intake = _intake(
        "Where applicable, TCS data burst messages shall comply with Variable Message "
        "Formats.",
    )
    assert "trafficPattern" not in intake["draft"]

    _result, intake = _intake("RideNow shall handle peak demand spikes without degradation.")
    assert intake["draft"]["trafficPattern"] == "spiky"


# --- 되묻기의 왕복 -----------------------------------------------------------
# 질문만 내고 답을 받을 자리가 없으면 이 단계는 반쪽이다. 답은 **값이 아니라 답**이라
# 산문과 같은 규율로 정규화된다 — 그게 이 절이 지키는 것이다.
def test_an_answer_completes_the_contract():
    """답이 들어오면 그 자리에서 계약이 서고 산출물이 생긴다."""
    state = _state("The system shall support 100 concurrent users.")
    state["resource_answers"] = {
        "provider": "aws", "region": "Seoul", "monthlyBudgetUSD": "3000",
    }
    result = sr.build_resource_spec(state)

    assert result["resource_intake"]["valid"]
    spec = result["resource_spec"]
    assert spec["provider"] == "aws"
    # 답도 해석기를 거친다 — "Seoul"은 여전히 코드로 풀려야 한다.
    assert spec["region"] == "ap-northeast-2"
    assert spec["monthlyBudgetUSD"] == 3000.0
    assert spec["expectedConcurrentUsers"] == 100


def test_an_answer_is_still_normalised_and_can_be_rejected():
    """질문에 답했다는 사실이 모호함을 없애 주지는 않는다."""
    state = _state("x")
    state["resource_answers"] = {
        "provider": "Amazon Web Services",   # 아는 id가 아니다
        "monthlyBudgetUSD": "300만원",        # 환산 거부
        "trafficPattern": "가끔 몰림",         # enum이 아니다
    }
    intake = sr.build_resource_spec(state)["resource_intake"]

    assert "provider" not in intake["draft"]
    assert "monthlyBudgetUSD" not in intake["draft"]
    assert "trafficPattern" not in intake["draft"]
    whys = " ".join(r["why"] for r in intake["rejected"])
    assert "프로바이더" in whys and "USD" in whys and "spiky" in whys
    # 거절됐으므로 그 칸은 **다시 질문으로 나간다** — 조용히 사라지지 않는다.
    assert {"provider", "monthlyBudgetUSD"} <= {q["field"] for q in intake["questions"]}


def test_a_bare_number_answer_is_read_without_demanding_context():
    """`monthlyBudgetUSD` 질문에 "3000"이라고 답한 것은 월 예산 3000 USD다.

    산문에서는 월 단위 표시가 없으면 버리지만, 답은 **무엇을 묻는지 이미 아는 자리**다.
    통화·단가 판정만 그대로 남는다(그건 문맥이 아니라 계약이 거부하는 것이라서).
    """
    kept = sr.answer_field("monthlyBudgetUSD", "3000")
    assert [c.value for c in kept.found] == [3000.0]

    unit_price = sr.answer_field("monthlyBudgetUSD", "$0.02 per GB")
    assert not unit_price.found and unit_price.rejected


def test_an_answer_outranks_the_prose_but_the_conflict_is_recorded():
    """가장 늦고 명시적인 말이 이긴다. 밀려난 쪽은 질문으로 남는다."""
    state = _state("The system shall support 100 concurrent users.",
                   constraints="We expect 9 000 concurrent users.")
    state["resource_answers"] = {"expectedConcurrentUsers": "250"}
    intake = sr.build_resource_spec(state)["resource_intake"]

    assert intake["draft"]["expectedConcurrentUsers"] == 250
    conflict = [q for q in intake["questions"] if q["kind"] == sr.CONFLICT]
    assert conflict and "되묻기의 답" in conflict[0]["question"]


def test_an_answer_to_a_field_the_contract_lacks_is_dropped():
    """계약에 없는 칸을 받아 두면 스키마 검증에서 스펙 전체가 무효가 된다."""
    state = _state("x")
    state["resource_answers"] = {"favouriteColour": "blue"}
    intake = sr.build_resource_spec(state)["resource_intake"]

    assert "favouriteColour" not in intake["draft"]
    assert any(r["field"] == "favouriteColour" for r in intake["rejected"])


def test_a_korean_place_name_also_leaves_its_original_wording():
    """별칭 해석("서울" → ap-northeast-2)은 코드만 보면 어디서 왔는지 알 수 없다."""
    state = _state("x")
    state["resource_answers"] = {"provider": "aws", "region": "서울"}
    intake = sr.build_resource_spec(state)["resource_intake"]

    assert intake["draft"]["region"] == "ap-northeast-2"
    assert intake["draft"]["regionAsWritten"] == "서울"
