"""에이전트 프롬프트 정책 테스트.

프롬프트는 코드처럼 조용히 망가지므로, 의도적으로 넣은 정책이 사라지지 않게 고정한다.

**공백을 정규화하고 대조한다.** 지시문이 영어가 되면서 줄바꿈이 낱말 사이가 아니라
구절 중간에 걸리기 시작했다("before you start\\nexecuting"). 한국어일 땐 짧은 조각을
골라 피했지만, 그건 문구를 짧게 잡도록 강요해 검사를 약하게 만든다 — 정규화하면
지키고 싶은 문장을 통째로 걸 수 있다.
"""

from __future__ import annotations

from nim_agent.agent import INSTRUCTIONS
from nim_agent.tools import record_plan, web_search


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


PROMPT = flat(INSTRUCTIONS)

#: 축 표의 머리글. **번호가 아니라 이름으로 잡는다** — 앞에 축이 하나 들어오면
#: 번호가 밀리는데, 그때 테스트가 깨지는 것은 지시문 결함이 아니라 이 테스트의
#: 결함이다(실제로 5번 → 6번이 되며 두 건이 깨졌다).
_MCP_AXIS_HEADING = "**Live state & execution**"
_AXES_HEADING = "Tools are split by the **axis** of the question:"


def test_record_plan_is_scoped_to_multi_step_work() -> None:
    """단순 조회에 계획을 쓰면 사후 합리화(이미 한 일을 계획서로 적기)가 된다."""
    assert "## When to use record_plan" in PROMPT
    assert "Do not use record_plan for simple lookups" in PROMPT
    assert "before you start executing" in PROMPT


def test_record_plan_tool_description_carries_the_same_rule() -> None:
    """도구 설명(@function_tool docstring)이 LLM에게 가장 가깝게 작동한다."""
    description = flat(record_plan.description)
    assert "before you start executing" in description
    assert "Do not call this" in description


def test_multi_step_workflow_still_plans_first() -> None:
    """cloud_sizing처럼 실제로 여러 단계를 조율하는 작업에서는 계획을 유지한다."""
    sizing = PROMPT.split("# Cloud resource sizing (cloud_sizing) workflow")[1]
    assert "the canonical case for record_plan" in sizing
    assert "call record_plan before calling any other tool" in sizing


def test_web_search_defers_to_dedicated_cloud_tools() -> None:
    """검색으로 클라우드 단가를 뒤지면 기준이 섞이고 검색 폭주가 난다.

    워크플로 지시만으로는 부족했다(모델이 그 워크플로로 인식 못 하면 안 읽힘).
    도구 설명은 항상 읽히므로 여기에 규칙을 둔다.
    """
    description = flat(web_search.description)
    assert "cost_recommend_specs" in description
    assert "kb_" in description and "cap_" in description
    assert "Do not use this tool to find cloud facts" in description


def test_mandatory_four_are_the_whole_gate() -> None:
    """**필수 4칸을 다 줘도 시작 못 하는 모순이 있었다.**

    진입 계약은 "프로바이더·리전·예산·규모 넷이면 된다"고 하는데 워크플로 3단계는
    "구성요소별로" 사이징하라고 해서, 구성요소를 안 밝힌 요청은 어느 쪽도 만족
    못 했다. RS2를 5회 돌렸더니 **5회 전부** 구성요소를 되물었다(0/5) — 모델이
    모순을 합리적으로 해소한 것이고, 되묻기를 막을 게 아니라 모순을 닫아야 했다.

    RS1(넷 다 없음)은 여전히 되물어야 하므로, 조건을 "넷이 다 있을 때"로 건다.
    """
    sizing = PROMPT.split("# Cloud resource sizing (cloud_sizing) workflow")[1]
    assert "Those four are the whole gate." in sizing
    assert "Do not ask for a component list" in sizing
    # 진행하되 **추론이라고 밝히고** 진행한다 — 짐작을 사실로 승격하지 않는다.
    assert "say in your answer that the split is your inference" in sizing


def test_web_supplement_is_bounded_by_ask_and_count() -> None:
    """**모델면을 영어로 바꾸자 이 규칙이 처음으로 지켜졌고, 그래서 결함이 드러났다.**

    프롬프트는 "미수록이면 web_search로 보충하라"고만 했지 "요청받았을 때만"도
    "몇 번까지"도 말하지 않았다. 라이브 실측에서 P3는 묻지도 않은 검색으로 새고
    (도구가 답할 수 있었다), P4는 같은 질문에 검색을 16회·107초 돌렸다.
    지시를 지키는 모델에게 경계 없는 지시는 그대로 결함이 된다.
    """
    section = PROMPT.split("# When the knowledge base does not have it")[1]
    assert "only if the user asked you to go find it" in section
    assert "Two searches at most" in section
    # 없음의 종류를 뭉개면 "우리가 안 담았다"가 "그 클라우드에 없다"가 된다.
    assert "Never report our absence as the cloud's absence." in section

    description = flat(web_search.description)
    assert "Only search when the user asked you to go find it" in description
    assert "Two calls at most" in description


def test_kb_queries_skip_planning() -> None:
    kb_section = PROMPT.split("# Cloud knowledge queries")[1]
    assert "call the tool directly without record_plan" in kb_section


def test_all_axes_are_routed() -> None:
    axes = PROMPT.split(_AXES_HEADING)[1]
    assert "kb_* tools" in axes  # 관계
    assert "cap_* tools" in axes  # 용량·제약
    assert "cost_* tools" in axes  # 스펙·가격
    assert "perf_* tools" in axes  # 성능 특성
    assert "cb-tumblebug MCP only" in axes  # 현재 상태·실행


def test_cross_provider_perf_comparison_is_declared_impossible() -> None:
    """ACU는 Azure만·클럭은 AWS만이라 프로바이더 간 성능 비교는 축이 없다.
    도구로 답하지 말고 불가능하다고 말하도록 지시돼 있어야 한다."""
    axes = PROMPT.split(_AXES_HEADING)[1]
    perf_axis = axes.split("4. **Performance characteristics**")[1].split(
        "5. **App design artifacts"
    )[0]
    assert "Cross-provider performance comparison is impossible" in perf_axis
    assert "same provider only" in perf_axis


def test_mcp_spec_recommendation_is_same_axis_not_a_different_one() -> None:
    """MCP의 recommend_vm_spec은 '현재 상태'가 아니라 cost_*와 **같은 축**이다.

    예전 표는 MCP를 통째로 '현재 상태'에 넣어, 같은 질문에 답하는 두 소스를
    다른 축인 것처럼 서술했다.
    """
    axes = PROMPT.split(_AXES_HEADING)[1]
    cost_axis, mcp_axis = axes.split(_MCP_AXIS_HEADING)
    assert "recommend_vm_spec" in cost_axis  # 같은 축 안에서 대안으로 언급
    assert "the same specs" in cost_axis  # costkb가 그 카탈로그의 미러라 문자 그대로 같다
    assert "recommend_vm_spec" not in mcp_axis  # 4번 축의 도구가 아니다


def test_missing_mcp_axis_is_declined_not_substituted() -> None:
    """MCP가 기본으로 꺼졌으므로 4번 축은 보통 답할 수 없다 —
    지식베이스나 검색으로 메우면 없는 배포 상태를 지어내게 된다."""
    axes = PROMPT.split(_AXES_HEADING)[1]
    mcp_axis = axes.split(_MCP_AXIS_HEADING)[1]
    assert "tell the user that axis cannot be answered" in mcp_axis
    assert "do not try to fill it in" in mcp_axis


def test_mcp_axis_covers_execution_not_just_state() -> None:
    """create_infra_dynamic 같은 '실행'은 상태 조회가 아니다 — 표에 자리가 있어야 한다."""
    axes = PROMPT.split(_AXES_HEADING)[1]
    assert "Live state & execution" in axes
    assert "actually creating things" in axes


def test_evidence_claim_is_scoped_to_the_kbs_that_have_it() -> None:
    """costkb는 카탈로그 스냅샷이라 evidence/basis가 없다 —
    '각 지식베이스는 근거와 신뢰도를 담고 있다'는 일반화는 거짓이 된다."""
    section = PROMPT.split("# Cloud knowledge queries")[1].split(_AXES_HEADING)[0]
    assert "kb_* / cap_* knowledge bases" in section
    assert "cost_* is" in section and "limitation notice" in section


def test_work_method_no_longer_orders_plan_before_every_task() -> None:
    """예전 지시("2. record_plan 도구로 ... 먼저 기록하세요")가 남아 있으면 안 된다."""
    work_method = PROMPT.split("# How you work")[1].split("##")[0]
    assert "record_plan" not in work_method


def test_answer_language_is_english() -> None:
    """**모델이 보는 면은 한 언어여야 한다.**

    실측: 도구 설명이 영어인데 도구 결과 텍스트가 한국어였을 때, 모델은 영어 층을
    따르고 도구 결과에 박힌 한국어 지시를 **무시했다**(계획 게이트). 반쪽 번역은
    양극단보다 나쁘다 — 답변 언어까지 영어로 맞춘다.
    """
    work_method = PROMPT.split("# How you work")[1].split("##")[0]
    assert "in English" in work_method
    assert "in Korean" not in work_method


def test_no_tool_output_hands_the_model_our_own_tool_names() -> None:
    """**누출을 세는 코드와 누출을 만드는 코드가 같은 저장소에 있었다.**

    지시문은 "도구 이름을 답변에 쓰지 마세요"라고 하는데, `_perf_pointer`는 도구
    출력에 `perf_instance_profile('aws', …)`를 적어 보냈다 — 모델이 사용자에게
    그대로 옮기는 텍스트 흐름에 우리 손으로 내부 이름을 쥐여 준 것이다(실측
    2026-07-25, 누출 검출기를 만들고서야 보였다).

    라우팅 신호 자체는 필요하다. 다만 **축으로 가리키면 되지 이름을 적을 필요는
    없다.** 이 검사는 그 구분을 구조로 고정한다 — 새 교차 참조를 만들 때 같은
    실수를 반복하지 않도록.
    """
    from nim_agent.capacity_tools import _perf_pointer
    from nim_agent.graph_tools import _capacity_pointer
    from nim_agent.tools import LOCAL_TOOLS

    names = {tool.name for tool in LOCAL_TOOLS}
    samples = [
        _perf_pointer("aws::AWS::EC2::Instance"),
        _perf_pointer("AWS::EC2::Instance"),
        _capacity_pointer("AWS::EC2::Subnet"),
        _capacity_pointer("p5.48xlarge"),
    ]
    for text in samples:
        leaked = sorted(name for name in names if name in text)
        assert not leaked, f"도구 출력이 내부 이름을 흘린다: {leaked}\n{text[:200]}"


def test_style_rule_covers_the_shapes_that_actually_slip() -> None:
    """예시가 '조회했다' 서술만 다루면 실제로 새는 두 형태를 못 막는다 —
    실측에서 잡힌 것은 **출처 표기**("※ kb_describe_type 결과")와
    **예고**("cap_resolve_region 도구로 확인해야 합니다")였다."""
    style = PROMPT.split("# Answer style")[1]
    assert "attributing" in style and "promising" in style
    # 규칙이 315줄 프롬프트의 꼬리에만 있으면 늦게 읽힌다 — 앞에도 포인터를 둔다.
    head = PROMPT.split("# How you work")[1].split("##")[0]
    assert "never in ours" in head


def test_every_tool_family_has_a_place_in_the_axis_table() -> None:
    """**축 표에 자리가 없으면 그 도구는 못 찾아진다.**

    두 번 같은 모양으로 겪었다. `pattern_search`는 축 5(설계도 JSON) 안에만 있어
    "트레이드오프 지침 있어?"에 도달하지 못했고(X6 3/5), `sizing_*`은 축 표에 **0번**
    나오고 cloud_sizing 워크플로 안에만 있어 "클러스터에 서브넷 몇 개?"가 의존성
    축으로 샜다(CF6 0~2/5). 워크플로 문단은 모델이 그 작업으로 인식했을 때만 읽힌다.

    도구를 새로 만들거나 이름을 바꿀 때 이 검사가 자리를 잊지 않게 한다.
    """
    from nim_agent.tools import LOCAL_TOOLS

    axes = PROMPT.split("Tools are split by the **axis** of the question:")[1]
    families = {
        name.split("_")[0] + "_"
        for tool in LOCAL_TOOLS
        for name in (tool.name,)
        if "_" in name and name.split("_")[0] in {"kb", "cap", "cost", "perf", "bundle", "sizing", "pattern"}
    }
    missing = sorted(f for f in families if f not in axes)
    assert not missing, f"축 표에 자리가 없는 도구 계열: {missing}"
