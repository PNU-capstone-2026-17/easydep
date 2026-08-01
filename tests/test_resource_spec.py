"""제약 구조화 에이전트의 규율 — `steps/step_resource.py`.

이 파일이 지키는 것은 하나로 모인다: **못 알아들은 것을 채우지 않는다.**

에이전트가 도구를 어떤 순서로 부를지는 모델이 정하므로 여기서 고정하지 않는다. 대신
**어떤 순서로 불러도 통과할 수 없는 문**을 검사한다:

  - 계약을 만족할 때만 `resource_spec`이 존재한다(반쯤 채운 사양은 없느니만 못하다).
  - 인용 못 하는 값은 버려진다 — 인용은 사용자가 쓴 것 **또는 도구가 알아 온 것**이어야
    한다. 그 둘을 가르는 표시가 근거에 남는다.
  - 리전은 카탈로그가 아는 **코드**여야 하고, 프로바이더는 조인이 도는 축이어야 한다.
  - 값이 갈리면 덮지 않는다. 못 채우고 묻지도 않은 채로 끝낼 수 없다.
  - 되묻기 문구는 지어내지 않고 계약의 `why()`를 그대로 쓴다.

모델은 스크립트로 대신한다 — 도구 호출 순서를 우리가 정해 놓고 **환경이 어떻게 답하는지**
를 보는 것이 목적이라, 진짜 모델을 부르면 재현이 안 되고 검사하려는 것도 흐려진다.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from app.core import cloud_contract, regions
from app.requirements.agent.steps import resource_tools
from app.requirements.agent.steps import step_resource as sr


# --- 가짜 모델 ---------------------------------------------------------------
class _Script:
    """정해진 도구 호출을 차례로 내는 모델. 마지막에는 도구 없이 답해 루프를 끝낸다."""

    def __init__(self, *turns: list[tuple[str, dict]]) -> None:
        self.turns = list(turns)
        self.results: list[str] = []   # 환경이 뭐라고 답했는지(되먹임 검사용)
        self.prompts: list[str] = []   # 브리핑에 무엇이 실렸는지

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        for message in messages:
            kind = getattr(message, "type", "")
            if kind == "human":
                self.prompts.append(str(message.content))
            elif kind == "tool":
                self.results.append(str(message.content))
        if not self.turns:
            return AIMessage(content="done")
        calls = self.turns.pop(0)
        return AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
            for i, (name, args) in enumerate(calls)
        ])


@pytest.fixture
def run(monkeypatch):
    """스크립트를 물려 단계를 돌리고 (결과, intake, 스크립트)를 돌려준다."""
    def go(*turns, texts=(), constraints="", answers=None):
        script = _Script(*turns)
        monkeypatch.setattr("app.requirements.agent.llm.build_llm", lambda: script)
        monkeypatch.setattr(sr.settings, "resource_agent_llm", True)
        state: dict = {"classified": [
            {"id": f"NFR-{i:02d}", "text": text, "type": "NFR"}
            for i, text in enumerate(texts, start=1)
        ]}
        if constraints:
            state["resource_constraints_text"] = constraints
        if answers:
            state["resource_answers"] = answers
        result = sr.build_resource_spec(state)
        return result, result["resource_intake"], script
    return go


def _record(field, value, evidence):
    return ("record_field", {"field": field, "value": value, "evidence": evidence})


CONSTRAINTS = ("Deploy on aws in Seoul, on a managed Kubernetes cluster. "
               "The monthly budget is at most 500 USD. "
               "We expect about 300 concurrent users.")

#: 계약을 세우는 최소 스크립트. 리전은 **카탈로그를 거쳐** 코드로 들어가고,
#: 위상축(`workloads`)도 **목록 도구를 거쳐야** 들어간다 — 둘 다 모델이 외워
#: 쓰면 안 되는 값이라 같은 대조를 받는다(판 2, 2026-08-01).
def _complete_turns():
    return (
        [_record("provider", "aws", "Deploy on aws"),
         ("resolve_region", {"place": "Seoul", "provider": "aws"}),
         ("list_workload_kinds", {"provider": "aws"})],
        [_record("region", "ap-northeast-2", "ap-northeast-2"),
         _record("regionAsWritten", "Seoul", "in Seoul"),
         _record("workloads", "k8sCluster", "- k8sCluster"),
         _record("monthlyBudgetUSD", "500", "at most 500 USD"),
         _record("expectedConcurrentUsers", "300", "about 300 concurrent users")],
        [("finish", {"understanding":
                     "AWS Seoul, managed Kubernetes, $500/month, ~300 users."})],
    )


# --- 산출물이 존재하는 조건 --------------------------------------------------
def test_a_complete_contract_produces_the_artifact(run):
    result, intake, _ = run(*_complete_turns(), constraints=CONSTRAINTS)

    assert intake["valid"] and not intake["errors"]
    spec = result["resource_spec"]
    assert spec["schemaVersion"] == sr.SCHEMA_VERSION
    assert spec["provider"] == "aws"
    # 지명은 **코드로** 담기고 원문은 따로 남는다 — 조인이 코드로만 돈다.
    assert spec["region"] == "ap-northeast-2"
    assert spec["regionAsWritten"] == "Seoul"
    assert spec["monthlyBudgetUSD"] == 500.0
    assert spec["expectedConcurrentUsers"] == 300


def test_a_half_filled_spec_never_leaves_this_step(run):
    """필수 칸이 비면 산출물을 내지 않는다 — 뒤 단계가 그걸 사양으로 알고 조인을 돌린다."""
    result, intake, _ = run(
        [_record("expectedConcurrentUsers", "100", "100 concurrent users")],
        texts=("The system shall support 100 concurrent users.",),
    )

    assert "resource_spec" not in result
    assert intake["valid"] is False and intake["errors"]
    # 그래도 **작업 기록은 남는다** — 왜 못 채웠는지가 사라지면 빈 칸의 뜻을 알 수 없다.
    assert intake["draft"]["expectedConcurrentUsers"] == 100


def test_the_confirmation_the_agent_read_back_is_kept(run):
    """확인은 질문과 다른 일이다 — 채운 칸을 잘못 읽지 않았는지 사용자가 볼 자리."""
    _result, intake, _ = run(*_complete_turns(), constraints=CONSTRAINTS)
    assert "500" in intake["understanding"]


def test_a_closing_summary_written_as_prose_is_still_the_confirmation(run):
    """진짜 모델은 `finish` 대신 산문으로 끝내기도 한다(2026-07-29 실측).

    형식이 어긋났다고 버리면 **확인이 통째로 사라진다** — 되읽기는 이 단계가 사용자에게
    돌려주는 유일한 확인 수단이다.
    """
    turns = _complete_turns()[:2]   # finish를 부르지 않고 멈춘다
    _result, intake, _ = run(*turns, constraints=CONSTRAINTS)

    assert intake["understanding"] == "done"


def test_a_finish_call_leaked_into_the_prose_is_unwrapped(run):
    """같은 실측에서 요약이 `{"understanding": …, "finish": {}}`로 나왔다.

    도구 호출을 산문으로 흘린 것이라 껍데기만 벗긴다 — 자연어를 뜯어보는 것이 아니라
    잘못 나온 호출을 되돌리는 것이다.
    """
    session = sr._Session([])
    session.said('{"understanding": "AWS Seoul, $500/month.", "finish": {}}')
    assert session.understanding == "AWS Seoul, $500/month."

    # JSON이 아니면 손대지 않는다.
    plain = sr._Session([])
    plain.said("AWS Seoul, $500/month.")
    assert plain.understanding == "AWS Seoul, $500/month."


# --- 지어냄을 막는 문 --------------------------------------------------------
def test_a_value_whose_quote_is_nowhere_is_dropped(run):
    """**인용 대조가 이 층의 지어냄 방지 장치다.** 버리되 조용히 버리지 않는다."""
    _result, intake, _ = run(
        [_record("monthlyBudgetUSD", "9999", "the budget is 9999 dollars")],
        constraints="Deploy on aws in Seoul.",
    )

    assert "monthlyBudgetUSD" not in intake["draft"]
    assert any("지어낸 것으로 본다" in r["why"] for r in intake["rejected"])


def test_a_quote_from_a_tool_result_counts_as_seen(run):
    """리전 코드는 사용자가 쓴 적이 없다 — 카탈로그가 답한 것이다.

    원문만 대조하면 도구가 알아 온 것을 전부 지어냄으로 몰게 된다. 그래서 건초더미에
    도구 출력도 들어가고, **어디서 왔는지는 근거가 구별한다.**
    """
    _result, intake, _ = run(*_complete_turns(), constraints=CONSTRAINTS)

    by_field = {c["field"]: c for c in intake["provenance"]}
    assert by_field["region"]["how"] == "tool", "카탈로그가 답한 것이 사용자 말로 남았다"
    assert by_field["provider"]["how"] == "user"
    assert by_field["monthlyBudgetUSD"]["how"] == "user"


def test_a_tool_derived_value_keeps_the_whole_tool_output_as_its_basis(run):
    """인용 조각만으로는 근거가 반쪽이다.

    실측(2026-07-29): `700,000 KRW`를 환산한 `483.0`이 근거에 값만 남아 **환율도 기준일도
    출처도 사라졌다.** 핀을 못 박는 소스는 쓴 값과 시각을 남긴다는 것이 이 저장소의
    규율인데, 그 규율이 근거에서 증발한 자리다.
    """
    _result, intake, _ = run(*_complete_turns(), constraints=CONSTRAINTS)

    by_field = {c["field"]: c for c in intake["provenance"]}
    assert "South Korea" in by_field["region"]["via"], "도구가 말한 맥락이 사라졌다"
    # 사용자가 쓴 값에는 붙이지 않는다 — 원문이 이미 근거다.
    assert by_field["provider"]["via"] == ""


def test_a_place_name_cannot_be_written_into_the_region_field(run):
    """지명을 코드 자리에 넣으면 뒤 단계 조인이 **오류 없이** 빈 답이 된다."""
    _result, intake, _ = run(
        [_record("provider", "aws", "Deploy on aws"),
         _record("region", "Seoul", "in Seoul")],
        constraints=CONSTRAINTS,
    )

    assert "region" not in intake["draft"]
    assert any(r["field"] == "region" and "코드" in r["why"] for r in intake["rejected"])


def test_a_provider_we_do_not_know_is_rejected(run):
    """조인이 실제로 도는 축만 받는다 — 그럴듯한 이름은 빈 칸보다 나쁘다."""
    _result, intake, _ = run(
        [_record("provider", "Amazon Web Services", "Deploy on aws")],
        constraints=CONSTRAINTS,
    )

    assert "provider" not in intake["draft"]
    assert any("프로바이더" in r["why"] for r in intake["rejected"])


def test_a_field_the_contract_lacks_is_dropped(run):
    """계약에 없는 칸을 받아 두면 스키마 검증에서 스펙 전체가 무효가 된다."""
    _result, intake, _ = run(
        [_record("favouriteColour", "blue", "Deploy on aws")],
        constraints=CONSTRAINTS,
    )

    assert "favouriteColour" not in intake["draft"]
    assert any(r["field"] == "favouriteColour" for r in intake["rejected"])


def test_a_second_different_value_does_not_overwrite_the_first(run):
    """조용히 덮으면 밀려난 값이 사라진다. 값이 갈리는 것은 정보가 아니라 질문이다."""
    _result, intake, script = run(
        [_record("expectedConcurrentUsers", "300", "about 300 concurrent users")],
        [_record("expectedConcurrentUsers", "9000", "9 000 concurrent users")],
        constraints=CONSTRAINTS + " Peak load reaches 9 000 concurrent users.",
    )

    assert intake["draft"]["expectedConcurrentUsers"] == 300
    assert any("already" in r and "ask_user" in r for r in script.results)


# --- 타입은 계약이 정한다 ----------------------------------------------------
def test_values_are_marshalled_to_the_type_the_contract_declares():
    """`"3,000"`이 문자열로 들어가면 스키마 검증이 스펙 전체를 무효로 만든다."""
    assert sr._coerce("monthlyBudgetUSD", "3,000") == (3000.0, "")
    assert sr._coerce("expectedConcurrentUsers", "300") == (300, "")
    assert sr._coerce("trafficPattern", "Spiky") == ("spiky", "")
    assert sr._coerce("multiZone", "true") == (True, "")

    # 못 맞추면 사유를 돌려준다 — 그 사유가 에이전트에게 되먹여져 다음 행동이 된다.
    assert sr._coerce("monthlyBudgetUSD", "about five hundred")[0] is None
    assert sr._coerce("trafficPattern", "가끔 몰림")[0] is None
    assert sr._coerce("expectedConcurrentUsers", "-3")[0] is None
    assert sr._coerce("favouriteColour", "blue")[1] == "계약에 없는 칸이다"


# --- 되묻기 -----------------------------------------------------------------
def test_questions_carry_both_the_ask_and_the_reason(run):
    """이유 없이 물으면 사용자가 아무 값이나 채운다 — 이 구조의 출발점이다.

    **2026-08-01에 둘로 갈렸다.** 예전에는 이유 문장이 곧 질문이었고, 그래서 계약의
    영어 근거 문장(`"it is the axis for every value join"`)이 그대로 화면에 나갔다.
    지금은 레지스트리가 `question`(사용자에게 하는 말)과 `opens`(왜 필요한가)를
    따로 들고, 되묻기가 **둘 다** 싣는다.
    """
    _result, intake, _ = run(texts=("The system shall support 100 concurrent users.",))

    asked = {q["field"]: q for q in intake["questions"]}
    for name in ("provider", "region", "workloads", "monthlyBudgetUSD"):
        assert name in asked, name
        assert asked[name]["why"] == cloud_contract.why(name)
        assert asked[name]["question"] == cloud_contract.question(name)
        assert asked[name]["question"] and asked[name]["why"]
        assert asked[name]["question"] != asked[name]["why"]


def test_a_question_offers_the_values_it_accepts(run):
    """고를 것이 있는 칸은 **무엇을 고를 수 있는지**를 함께 낸다.

    위상축이 특히 그렇다 — 사용자가 우리 어휘를 모르는데 목록 없이 물으면
    실측 없는 이름이 돌아온다. 목록은 프로바이더가 정해진 뒤에야 나온다.
    """
    _result, intake, _ = run(
        [_record("provider", "aws", "Deploy on aws")], constraints=CONSTRAINTS)
    asked = {q["field"]: q for q in intake["questions"]}
    assert "k8sCluster" in asked["workloads"]["choices"]
    assert "steady" in asked["trafficPattern"]["choices"]


def test_a_required_field_is_asked_even_if_the_agent_forgets(run):
    """되묻기가 사용자에게 가느냐를 모델의 재량에 맡기지 않는다."""
    _result, intake, _ = run(
        [_record("provider", "aws", "Deploy on aws")],
        constraints=CONSTRAINTS,
    )

    required = [q for q in intake["questions"] if q["kind"] == sr.MISSING]
    assert {"region", "monthlyBudgetUSD"} <= {q["field"] for q in required}
    # 2026-07-29부터 **권고 질문**이 함께 나간다(필수는 아니지만 채우면 판정이 하나
    # 열리는 칸). 둘을 같은 얼굴로 물으면 사용자가 전부 필수로 읽으므로 kind로 가른다.
    suggested = [q for q in intake["questions"] if q["kind"] == sr.SUGGESTED]
    assert suggested and "minVCpu" in {q["field"] for q in suggested}
    assert {q["kind"] for q in intake["questions"]} <= {sr.MISSING, sr.SUGGESTED}


def test_the_agent_can_ask_in_its_own_words(run):
    """되묻기는 폴백이 아니라 **행동**이다 — 모호하면 고르지 않고 묻는다."""
    _result, intake, _ = run(
        [("resolve_region", {"place": "Seoul"})],
        [("ask_user", {"field": "provider",
                       "question": "Seoul exists at several providers — which one?"})],
        constraints="Deploy in Seoul.",
    )

    asked = [q for q in intake["questions"] if q["kind"] == sr.ASKED]
    assert asked and asked[0]["field"] == "provider"
    # 에이전트가 물은 칸은 기계가 다시 묻지 않는다 — 같은 것을 두 번 묻게 된다.
    assert [q["field"] for q in intake["questions"]].count("provider") == 1


def test_asking_about_a_field_the_contract_lacks_is_refused(run):
    """계약에 없는 칸을 물으면 사용자가 답해도 갈 곳이 없다(화면이 칸 이름을 키로 쓴다)."""
    _result, intake, script = run(
        [("ask_user", {"field": "favouriteColour", "question": "What colour?"})],
        constraints=CONSTRAINTS,
    )

    assert not [q for q in intake["questions"] if q["field"] == "favouriteColour"]
    assert any("not a field of the contract" in r for r in script.results)


def test_every_asked_field_is_one_the_contract_actually_has(run):
    _result, intake, _ = run(constraints=CONSTRAINTS)

    known = cloud_contract.schema_fields()
    assert {q["field"] for q in intake["questions"]} <= known
    assert set(intake["draft"]) <= known


def test_finishing_with_an_unfilled_unasked_field_is_refused(run):
    """못 채우고 묻지도 않은 채로 끝나면 빈 칸이 사용자에게 영영 안 보인다."""
    _result, _intake, script = run(
        [("finish", {"understanding": "All good."})],
        constraints=CONSTRAINTS,
    )

    assert any(r.startswith("Not finished:") for r in script.results)


# --- 되묻기의 왕복 -----------------------------------------------------------
def test_an_earlier_answer_is_part_of_what_the_agent_perceives(run):
    """답은 사용자가 **그 칸을 두고** 한 말이라 가장 세다. 안 보여 주면 또 묻게 된다."""
    _result, _intake, script = run(
        constraints="Deploy somewhere sensible.",
        answers={"provider": "aws", "monthlyBudgetUSD": "500"},
    )

    briefing = script.prompts[0]
    assert "Answers the user already gave" in briefing
    assert "provider: aws" in briefing


def test_an_answer_is_still_resolved_and_can_be_rejected(run):
    """질문에 답했다는 사실이 모호함을 없애 주지는 않는다 — 답도 같은 문을 지난다."""
    _result, intake, _ = run(
        [_record("region", "서울", "서울")],
        texts=("x",),
        answers={"region": "서울"},
    )

    assert "region" not in intake["draft"]
    assert any(r["field"] == "region" for r in intake["rejected"])


# --- 읽을 수단이 없으면 없다고 말한다 ----------------------------------------
def test_with_the_agent_switched_off_nothing_is_read_and_it_says_so(monkeypatch):
    """자연어를 읽는 수단이 없는데 읽은 척하지 않는다."""
    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    intake = sr.build_resource_spec(
        {"classified": [], "resource_constraints_text": CONSTRAINTS}
    )["resource_intake"]

    assert intake["degraded"]
    assert set(intake["draft"]) == {"schemaVersion"}
    assert {"provider", "region", "monthlyBudgetUSD"} <= {q["field"]
                                                          for q in intake["questions"]}


def test_a_dead_agent_still_leaves_the_questions(run, monkeypatch):
    """호출이 죽어도 못 채운 칸은 되묻기로 나간다 — 실패가 침묵이 되면 안 된다."""
    class _Dead:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            raise RuntimeError("endpoint down")

    monkeypatch.setattr("app.requirements.agent.llm.build_llm", _Dead)
    monkeypatch.setattr(sr.settings, "resource_agent_llm", True)
    intake = sr.build_resource_spec(
        {"classified": [], "resource_constraints_text": CONSTRAINTS}
    )["resource_intake"]

    assert "endpoint down" in intake["degraded"]
    assert intake["questions"]


def test_the_turn_budget_stops_a_runaway_without_leaking_a_half_spec(run, monkeypatch):
    """상한이 하는 일은 폭주를 막는 것뿐 — 못 채운 칸은 그대로 질문이 된다."""
    monkeypatch.setattr(sr.settings, "resource_agent_max_turns", 2)
    result, intake, _ = run(
        [("check_contract", {})], [("check_contract", {})], [("check_contract", {})],
        constraints=CONSTRAINTS,
    )

    assert "resource_spec" not in result
    assert intake["questions"]


# --- 도구 -------------------------------------------------------------------
def test_the_region_tool_does_not_narrow_an_ambiguous_place():
    """'서울'은 프로바이더 여러 곳에 걸린다. 우리가 고르면 사용자 뜻인 양 보인다."""
    ambiguous = resource_tools.resolve_region.invoke({"place": "Seoul"})
    assert "ambiguous" in ambiguous

    narrowed = resource_tools.resolve_region.invoke({"place": "Seoul", "provider": "aws"})
    assert "exactly one" in narrowed and "ap-northeast-2" in narrowed


def test_the_region_tool_says_it_did_not_understand():
    """빈 결과는 "그런 리전이 없다"가 아니라 "우리가 못 알아들었다"이다."""
    out = resource_tools.resolve_region.invoke({"place": "Narnia"})
    assert "did not understand" in out


def test_the_provider_tool_answers_from_the_join_axis():
    listed = resource_tools.list_cloud_providers.invoke({})
    assert set(listed.split(", ")) == set(regions.providers())


def test_currency_conversion_refuses_to_guess_a_rate(monkeypatch):
    """환율을 못 가져오면 지어내지 않고 **USD로 달라고 하라**고 말한다.

    이 도구가 있는 이유가 여기 있다 — 예전 판은 "계약이 환산을 거부한다"고 적어 두고
    타 통화 금액을 통째로 버렸다. 그건 계약의 뜻이 아니라 우리에게 소스가 없다는 뜻이었다.
    """
    def dead(*a, **kw):
        raise OSError("no network")

    monkeypatch.setattr("httpx.get", dead)
    out = resource_tools.convert_to_usd.invoke({"amount": 3_000_000, "currency": "KRW"})
    assert "Do not guess" in out and "Ask the user" in out

    assert "not an ISO 4217" in resource_tools.convert_to_usd.invoke(
        {"amount": 100, "currency": "원"})
