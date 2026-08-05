"""BASELINE 그래프 — 다단계 파이프라인의 대조군.

우리 시스템(intake→classify→identify_actors→identify_use_cases→check_coverage→generate_specs
→check_specs→identify_relationships→check_relationships→render_diagram)은 단계를 나눠
프롬프팅하고 각 단계에서 검증·반성·커버리지 강제·참조 가드를 건다.

baseline은 그 "분해 + 검증"을 전부 뺀 순진한 2콜이다. FR/NFR 분류(BERT)도 하지 않고
요구사항 전체를 그대로 준다:
    intake → baseline_generate(1콜: 액터+UC+명세) → baseline_diagram(1콜: 관계) → END
- 분류 없음(원문 요구사항 통째로 전달), 커버리지 강제 루프 없음, 명세 정적/의미 검증·반성 없음,
  관계 후보 마이닝·참조 가드 없음.
- render_diagram(PlantUML 렌더)만 "지능"이 아닌 공유 인프라라 재사용한다(관계 내용은 baseline 그대로).

산출 상태는 우리 파이프라인과 동일한 키(classified/actors/use_cases/use_case_specs/
relationships/diagram)라 같은 결정론 검증기로 채점할 수 있다(app/agent/compare.py). 단, baseline의
classified는 FR/NFR 분류가 아니라 추적용 번호(R1..)일 뿐이라 커버리지는 '전체 요구 대비 참조 비율'이다.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.requirements import prompts
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import ActorItem, AgentState, UseCaseItem, UseCaseSpecItem
from app.requirements.agent.steps.step1_requirements import intake
from app.requirements.agent.steps.step2_usecases import _uc_dict
from app.requirements.agent.steps.step3_specifications import _assemble
from app.requirements.agent.steps.step4_diagram import render_diagram
from app.requirements.schemas import BaselineModelResult, RelationshipModel


def baseline_generate(state: AgentState) -> dict:
    """1콜: 요구사항 전체를 그대로 주고 액터+UC+Cockburn 명세를 한 번에 생성(분류·검증 없음).

    FR/NFR 분류 단계 없이 원문 요구사항에 추적용 번호(R1..)만 붙여 통째로 준다. 번호는 커버리지
    측정용일 뿐 분류가 아니다(type은 편의상 FR로 두어 전체가 커버리지 대상이 되게 함).
    """
    reqs = state.get("raw_requirements") or []
    classified = [{"id": f"R{i}", "text": t, "type": "FR"} for i, t in enumerate(reqs, start=1)]
    listing = "\n".join(f"- {c['id']}: {c['text']}" for c in classified)  # 분류 없이 원문 그대로
    result: BaselineModelResult = invoke_structured(
        BaselineModelResult,
        [SystemMessage(content=prompts.BASELINE_MODEL_SYSTEM),
         HumanMessage(content=f"Requirements:\n{listing}")],
    )

    actors: list[ActorItem] = [
        {"name": a.name, "description": a.description,
         "parent_actor": a.parent_actor}
        for a in result.actors
    ]
    use_cases: list[UseCaseItem] = [
        _uc_dict(uc, f"UC{i}") for i, uc in enumerate(result.use_cases, start=1)
    ]
    name_to_id = {uc["name"]: uc["id"] for uc in use_cases}

    # 명세를 UC에 이름으로 매칭해 상태 dict로 조립(_assemble 재사용). 매칭 실패 시 임시 id 부여.
    specs: list[UseCaseSpecItem] = []
    extra = len(use_cases)
    for spec in result.specs:
        uid = name_to_id.get(spec.use_case_name)
        if uid is None:
            extra += 1
            uid = f"UC{extra}"
        specs.append(_assemble(spec, {"id": uid, "name": spec.use_case_name}))  # type: ignore[arg-type]

    return {"classified": classified, "actors": actors, "use_cases": use_cases,
            "use_case_specs": specs, "phase": "baseline_generate"}


def baseline_diagram(state: AgentState) -> dict:
    """1콜: 관계(association/include/extend/generalization)를 생성한다. 참조 가드/보강 없음."""
    actors = state.get("actors") or []
    use_cases = state.get("use_cases") or []
    actor_listing = "\n".join(f"- {a['name']}" for a in actors)
    uc_listing = "\n".join(
        f"- {u['name']} [primary: {u.get('primary_actor', '?')}; "
        f"supporting: {', '.join(u.get('supporting_actors', [])) or 'none'}]"
        for u in use_cases
    )
    result: RelationshipModel = invoke_structured(
        RelationshipModel,
        [SystemMessage(content=prompts.BASELINE_DIAGRAM_SYSTEM),
         HumanMessage(content=f"[ACTORS]\n{actor_listing}\n\n[USE CASES]\n{uc_listing}")],
    )

    # 원(raw) 관계를 그대로 상태에 둔다 — 우리 파이프라인과 달리 참조 가드/orphan 보강을 하지 않는다.
    rel = {
        "associations": [{"actor": a.actor, "use_case": a.use_case} for a in result.associations],
        "includes": [{"base_use_case": r.base_use_case, "included_use_case": r.included_use_case,
                      "rationale": r.rationale} for r in result.includes],
        "extends": [{"base_use_case": r.base_use_case, "extending_use_case": r.extending_use_case,
                     "extension_point": r.extension_point, "rationale": r.rationale}
                    for r in result.extends],
        "generalizations": [{"parent": g.parent, "child": g.child, "kind": g.kind,
                             "rationale": g.rationale} for g in result.generalizations],
        "derived_use_cases": [{"name": d.name, "origin": d.origin, "rationale": d.rationale}
                              for d in result.derived_use_cases],
    }
    # 렌더는 결정론 공유 인프라라 재사용(관계 내용은 baseline 그대로).
    render_state = {"actors": actors, "use_cases": use_cases, "relationships": rel}
    diagram = render_diagram(render_state)["diagram"]  # type: ignore[arg-type]
    return {"relationships": rel, "diagram": diagram, "phase": "baseline_diagram"}


def build_baseline_graph():
    """순진한 baseline 그래프를 컴파일해 반환한다(interrupt/게이트 없음, 체크포인터 불필요)."""
    builder = StateGraph(AgentState)
    builder.add_node("intake", intake)
    builder.add_node("baseline_generate", baseline_generate)  # 분류 없이 요구 전체를 원샷
    builder.add_node("baseline_diagram", baseline_diagram)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "baseline_generate")
    builder.add_edge("baseline_generate", "baseline_diagram")
    builder.add_edge("baseline_diagram", END)
    return builder.compile()
