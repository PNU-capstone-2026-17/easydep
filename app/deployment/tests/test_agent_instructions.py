"""에이전트 프롬프트 정책 테스트.

프롬프트는 코드처럼 조용히 망가지므로, 의도적으로 넣은 정책이 사라지지 않게 고정한다.
"""

from __future__ import annotations

from nim_agent.agent import INSTRUCTIONS
from nim_agent.tools import record_plan, web_search


def test_record_plan_is_scoped_to_multi_step_work() -> None:
    """단순 조회에 계획을 쓰면 사후 합리화(이미 한 일을 계획서로 적기)가 된다."""
    assert "record_plan은 언제 쓰나" in INSTRUCTIONS
    assert "단순 조회에는 record_plan을 쓰지 마세요" in INSTRUCTIONS
    assert "실행을 시작하기 전에" in INSTRUCTIONS


def test_record_plan_tool_description_carries_the_same_rule() -> None:
    """도구 설명(@function_tool docstring)이 LLM에게 가장 가깝게 작동한다."""
    description = record_plan.description
    assert "실행을 시작하기 전에" in description
    assert "호출하지 마세요" in description


def test_multi_step_workflow_still_plans_first() -> None:
    """cloud_sizing처럼 실제로 여러 단계를 조율하는 작업에서는 계획을 유지한다."""
    sizing = INSTRUCTIONS.split("# 클라우드 리소스 산정(cloud_sizing) 워크플로")[1]
    assert "record_plan을 쓰는 대표적인 경우" in sizing
    assert "다른 도구를 부르기 전에 record_plan을 호출해" in sizing


def test_web_search_defers_to_dedicated_cloud_tools() -> None:
    """검색으로 클라우드 단가를 뒤지면 기준이 섞이고 검색 폭주가 난다.

    워크플로 지시만으로는 부족했다(모델이 그 워크플로로 인식 못 하면 안 읽힘).
    도구 설명은 항상 읽히므로 여기에 규칙을 둔다.
    """
    description = web_search.description
    assert "cost_recommend_specs" in description
    assert "kb_" in description and "cap_" in description
    assert "찾지 마세요" in description


def test_kb_queries_skip_planning() -> None:
    kb_section = INSTRUCTIONS.split("# 클라우드 지식 질의")[1]
    assert "record_plan 없이 바로" in kb_section


def test_all_four_axes_are_routed() -> None:
    axes = INSTRUCTIONS.split("질문의 **축**에 따라 도구가 나뉩니다:")[1]
    assert "kb_* 도구" in axes  # 관계
    assert "cap_* 도구" in axes  # 용량·제약
    assert "cost_* 도구" in axes  # 스펙·가격
    assert "cb-tumblebug MCP 전용" in axes  # 현재 상태·실행


def test_mcp_spec_recommendation_is_same_axis_not_a_different_one() -> None:
    """MCP의 recommend_vm_spec은 '현재 상태'가 아니라 cost_*와 **같은 축**이다.

    예전 표는 MCP를 통째로 '현재 상태'에 넣어, 같은 질문에 답하는 두 소스를
    다른 축인 것처럼 서술했다.
    """
    axes = INSTRUCTIONS.split("질문의 **축**에 따라 도구가 나뉩니다:")[1]
    cost_axis, mcp_axis = axes.split("4. **현재 상태·실행**")
    assert "recommend_vm_spec" in cost_axis  # 같은 축 안에서 대안으로 언급
    assert "같은 스펙" in cost_axis  # costkb가 그 카탈로그의 미러라 문자 그대로 같다
    assert "recommend_vm_spec" not in mcp_axis  # 4번 축의 도구가 아니다


def test_missing_mcp_axis_is_declined_not_substituted() -> None:
    """MCP가 기본으로 꺼졌으므로 4번 축은 보통 답할 수 없다 —
    지식베이스나 검색으로 메우면 없는 배포 상태를 지어내게 된다."""
    axes = INSTRUCTIONS.split("질문의 **축**에 따라 도구가 나뉩니다:")[1]
    mcp_axis = axes.split("4. **현재 상태·실행**")[1]
    assert "답할 수 없다고 사용자에게 알리세요" in mcp_axis
    assert "메우려 하지 마세요" in mcp_axis


def test_mcp_axis_covers_execution_not_just_state() -> None:
    """create_infra_dynamic 같은 '실행'은 상태 조회가 아니다 — 표에 자리가 있어야 한다."""
    axes = INSTRUCTIONS.split("질문의 **축**에 따라 도구가 나뉩니다:")[1]
    assert "현재 상태·실행" in axes
    assert "실제로 만들기" in axes


def test_evidence_claim_is_scoped_to_the_kbs_that_have_it() -> None:
    """costkb는 카탈로그 스냅샷이라 evidence/confidence가 없다 —
    '각 지식베이스는 근거와 신뢰도를 담고 있다'는 일반화는 거짓이 된다."""
    section = INSTRUCTIONS.split("# 클라우드 지식 질의")[1].split("질문의 **축**")[0]
    assert "kb_* / cap_* 지식베이스는" in section
    assert "cost_* 는" in section and "한계 고지" in section


def test_work_method_no_longer_orders_plan_before_every_task() -> None:
    """예전 지시("2. record_plan 도구로 ... 먼저 기록하세요")가 남아 있으면 안 된다."""
    work_method = INSTRUCTIONS.split("# 작업 방식")[1].split("##")[0]
    assert "record_plan" not in work_method
