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


def test_answer_language_is_still_korean() -> None:
    """1단계는 **모델이 읽는 면만** 영어로 옮긴다.

    답변 언어까지 같이 바꾸면 프로브 58건의 한국어 `want_any`가 통째로 죽어서,
    영어화가 라우팅을 개선했는지 잴 수 없게 된다 — 변수를 하나만 움직인다.
    """
    work_method = PROMPT.split("# How you work")[1].split("##")[0]
    assert "in Korean" in work_method
