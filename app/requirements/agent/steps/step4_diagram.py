"""STEP 4 — 액터/유스케이스 관계 식별 + 유스케이스 다이어그램 생성.

두 노드로 분리한다(의미 판단은 LLM, 구조 렌더는 결정론):
  - identify_relationships: LLM이 association/include/extend/generalization/파생 UC를 도출.
    이후 각 UC의 primary_actor association이 빠지지 않도록 결정론적으로 보강한다.
  - render_diagram: 관계 모델을 PlantUML 유스케이스 다이어그램 텍스트로 렌더링(순수 함수).
"""
from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent import supervisor, validator
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState
from app.requirements.common import telemetry
from app.requirements.common.state_contract import contract
from app.requirements.config import settings
from app.requirements.knowledge import rules
from app.requirements.schemas import RelationshipModel


def _rel_findings(rel: dict) -> tuple[list[str], str]:
    """관계에 대한 의미 검증을 독립 검증자에게 맡긴다.

    검증자에게 주는 것은 **제안된 관계뿐이다** — 어떤 힌트로 그걸 뽑았는지, 사용자가 무슨
    피드백을 줬는지는 주지 않는다. 물어야 하는 것은 결과가 규칙을 지켰는지다.

    `(결함 목록, 검증 상태)`를 돌려준다 — step3의 `_semantic_findings`와 같은 이유로,
    "결함 없음"과 "확인하지 못함"이 같은 값이 되면 안 된다.
    """
    payload = {k: rel.get(k, []) for k in ("includes", "extends", "generalizations", "derived_use_cases")}
    result = validator.review(
        rules.DRAW_DIAGRAM, payload, prefix="rel", source="relationships.semantic_validator"
    )
    return result.findings, result.status


# include 후보 힌트를 관계 에이전트에 몇 개까지 노출할지.
# ⚠ 순수 엔지니어링 가드(프롬프트 크기 제한)일 뿐 규칙이 아니다. 출력(관계) 개수를 제한하지
# 않는다 — 의미 필터는 프롬프트가 담당한다. 이 사실은 지식베이스에도 규칙이 **아닌 것**으로
# 적혀 있다(`knowledge/rules.py`의 `rel.include-hint-cap`, severity=NON_RULE).
_MAX_INCLUDE_HINTS = 6


def _norm_step(sentence: str) -> str:
    """MSS 스텝 문장을 정규화(소문자·문장부호 제거·공백 축약)해 비교 키로 만든다."""
    return " ".join(re.sub(r"[^\w\s]", " ", sentence.lower()).split())


def _mine_include_candidates(use_cases: list, specs_by_name: dict) -> dict[str, list[str]]:
    """여러 UC가 공유하는 정규화 MSS 스텝을 include 후보로 마이닝한다(결정론 힌트).

    LLM이 공유행위를 놓치지 않도록 하는 '제안'일 뿐 — 확정은 관계 에이전트가 한다.
    """
    step_to_ucs: dict[str, set[str]] = {}
    for uc in use_cases:
        spec = specs_by_name.get(uc["name"])
        if not spec:
            continue
        for st in spec.get("main_scenario", []):
            key = _norm_step(st["sentence"])
            if len(key.split()) >= 3:  # 너무 짧은 스텝은 노이즈로 제외
                step_to_ucs.setdefault(key, set()).add(uc["name"])
    return {k: sorted(v) for k, v in step_to_ucs.items() if len(v) >= 2}


@contract("identify_relationships", requires=("use_cases", "actors"),
          produces=("relationships",))
def identify_relationships(state: AgentState, feedback: str = "") -> dict:
    """액터/유스케이스/명세로부터 다이어그램 관계를 도출한다. feedback 시 재생성 지시.

    관계 에이전트에 주 시나리오와 결정론 후보 힌트(공유 스텝→include, parent_actor→일반화)를
    함께 주고, parent_actor 일반화는 결정론적으로 보강한다. 주액터 association도 보강한다.
    """
    feedback = supervisor.feedback_for(state, "relationships", feedback)
    use_cases = state.get("use_cases") or []
    empty = {
        "associations": [], "includes": [], "extends": [], "generalizations": [],
        "derived_use_cases": [], "orphan_actors": [], "dropped_refs": [], "relationship_issues": [],
    }
    if not use_cases:
        return {"relationships": empty, "phase": "relationships"}

    actors = state.get("actors") or []
    specs_by_name = {s["name"]: s for s in (state.get("use_case_specs") or [])}

    actor_lines = "\n".join(
        f"- {a['name']} ({a['kind']})"
        + (f" specializes {a['parent_actor']}" if a.get("parent_actor") else "")
        + f": {a['description']}"
        for a in actors
    )
    # 유스케이스 + 주 시나리오(공유행위 판단 근거를 에이전트에 제공).
    uc_blocks = []
    for uc in use_cases:
        spec = specs_by_name.get(uc["name"]) or {}
        steps = "\n".join(
            f"    {st['step_number']}. {st['sentence']}" for st in spec.get("main_scenario", [])
        )
        block = f"- {uc['name']} [primary actor: {uc.get('primary_actor', '?')}]: {uc.get('goal', '')}"
        uc_blocks.append(f"{block}\n{steps}" if steps else block)
    uc_lines = "\n".join(uc_blocks)

    # 결정론 후보 힌트: 공유 스텝(include), parent_actor(일반화).
    # include 힌트는 공유 UC 수가 많은 순 top-N만(과다 팩토링 유발 방지). 제네릭 스텝 걸러내기는
    # 프롬프트의 "meaningful nameable capability" 판단에 맡긴다(임의 사전 금지).
    gen_cand = [(a["name"], a["parent_actor"]) for a in actors if a.get("parent_actor")]
    inc_cand = _mine_include_candidates(use_cases, specs_by_name)
    top_inc = sorted(inc_cand.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:_MAX_INCLUDE_HINTS]
    hint_lines = [
        f'- shared step "{step}" appears in: {", ".join(ucs)} (possible include)'
        for step, ucs in top_inc
    ] + [f"- actor {child} specializes {parent} (possible generalization)" for child, parent in gen_cand]
    hints = "\n".join(hint_lines) or "- (none)"

    human = (
        f"Actors:\n{actor_lines or '- (none)'}\n\n"
        f"Use cases (with main success scenarios):\n{uc_lines}\n\n"
        f"Candidate hints (suggestions to confirm or reject, not commands):\n{hints}"
    )
    human = prompts.apply_user_feedback(human, feedback)

    def _generate(user: str) -> dict:
        result: RelationshipModel = invoke_structured(
            RelationshipModel,
            [
                SystemMessage(content=prompts.generation_system_for(rules.DRAW_DIAGRAM)),
                HumanMessage(content=user),
            ],
        )
        return {
            "associations": [{"actor": a.actor, "use_case": a.use_case} for a in result.associations],
            "includes": [
                {"base_use_case": i.base_use_case, "included_use_case": i.included_use_case, "rationale": i.rationale}
                for i in result.includes
            ],
            "extends": [
                {"base_use_case": e.base_use_case, "extending_use_case": e.extending_use_case,
                 "extension_point": e.extension_point, "rationale": e.rationale}
                for e in result.extends
            ],
            "generalizations": [
                {"parent": g.parent, "child": g.child, "kind": g.kind, "rationale": g.rationale}
                for g in result.generalizations
            ],
            "derived_use_cases": [
                {"name": d.name, "origin": d.origin, "rationale": d.rationale}
                for d in result.derived_use_cases
            ],
        }

    # 생성 → 의미검증(안티패턴) → 실패 시 지시로 재생성하는 반성 루프(더 나빠지면 직전본 유지).
    # 채택 규칙과 멈춘 이유의 이름은 step3의 반성 루프와 같게 둔다 — 두 루프가 같은
    # 규율을 따른다는 걸 리포트에서 바로 읽을 수 있어야 한다.
    rel = _generate(human)
    findings, semantic_status = _rel_findings(rel)
    attempts = 0
    stopped = "budget"
    for _ in range(settings.max_repair_iters):
        if not findings:
            stopped = "clean"
            break
        repair = human + "\n\n[YOUR PREVIOUS RELATIONSHIPS FAILED THESE CHECKS — fix every one, " \
                 "keeping the correct ones]\n" + "\n".join(f"- {d}" for d in findings)
        attempts += 1
        try:
            candidate = _generate(repair)
        except Exception as exc:  # noqa: BLE001 - 재생성 실패 시 직전본 유지
            # 수리를 못 했으므로 검증이 지적한 안티패턴이 그대로 남는다.
            telemetry.record_degradation("relationships.repair", f"{type(exc).__name__}: {exc}")
            stopped = "error"
            break
        cand_findings, cand_status = _rel_findings(candidate)
        if len(cand_findings) >= len(findings):
            stopped = "no_improvement"
            break
        rel, findings, semantic_status = candidate, cand_findings, cand_status
    else:
        stopped = "clean" if not findings else "budget"
    rel["relationship_issues"] = findings
    rel["semantic_status"] = semantic_status
    rel["repair_iters"] = attempts       # 채택 수가 아니라 시도 수(비용)
    rel["repair_stopped"] = stopped

    # 결정론 보강: parent_actor로부터 액터 일반화를 추가(LLM이 놓쳐도 부모-자식은 확정 사실).
    have_gen = {(g["parent"], g["child"]) for g in rel["generalizations"]}
    for child, parent in gen_cand:
        if (parent, child) not in have_gen:
            rel["generalizations"].append(
                {"parent": parent, "child": child, "kind": "actor", "rationale": "parent_actor"}
            )

    # 결정론 참조 검증: 존재하지 않는 UC/액터를 참조하는 관계를 제거(구조적 무결성).
    # (LLM이 환각한 이름을 조용히 다이어그램에 그리지 않도록. 실패-승격 억제는 프롬프트 몫.)
    known_uc = {uc["name"] for uc in use_cases} | {d["name"] for d in rel["derived_use_cases"]}
    known_actor = {a["name"] for a in actors}
    dropped: list[str] = []

    def _keep_assoc(a):
        ok = a["actor"] in known_actor and a["use_case"] in known_uc
        if not ok:
            dropped.append(f"association {a['actor']} -> {a['use_case']}")
        return ok

    def _keep_uc_pair(r, x, y, kind):
        ok = r[x] in known_uc and r[y] in known_uc
        if not ok:
            dropped.append(f"{kind} {r[x]} / {r[y]}")
        return ok

    def _keep_general(g):
        pool = known_actor if g.get("kind") == "actor" else known_uc
        ok = g["parent"] in pool and g["child"] in pool
        if not ok:
            dropped.append(f"generalization {g['parent']} / {g['child']}")
        return ok

    rel["associations"] = [a for a in rel["associations"] if _keep_assoc(a)]
    rel["includes"] = [r for r in rel["includes"] if _keep_uc_pair(r, "base_use_case", "included_use_case", "include")]
    rel["extends"] = [r for r in rel["extends"] if _keep_uc_pair(r, "base_use_case", "extending_use_case", "extend")]
    rel["generalizations"] = [g for g in rel["generalizations"] if _keep_general(g)]
    rel["dropped_refs"] = dropped

    # 결정론 보강: 각 UC의 primary_actor association이 빠졌으면 추가(다이어그램 누락 방지).
    have = {(a["actor"], a["use_case"]) for a in rel["associations"]}
    for uc in use_cases:
        key = (uc.get("primary_actor"), uc["name"])
        if key[0] and key not in have:
            rel["associations"].append({"actor": key[0], "use_case": key[1]})

    # 결정론 점검: 어떤 association에도 안 걸린 액터 → orphan_actors (UC와 무관한 액터 표면화).
    associated = {a["actor"] for a in rel["associations"]}
    rel["orphan_actors"] = sorted(
        a["name"] for a in actors if a["name"] not in associated
    )

    return {"relationships": rel, "phase": "relationships"}


@contract("check_relationships", requires=("relationships",),
          produces=("relationship_report",))
def check_relationships(state: AgentState) -> dict:
    """관계 검증 결과를 집계한다(결정론 요약 노드).

    identify_relationships가 의미검증·반성·참조가드를 이미 수행했고, 이 노드는 그 결과(잔여
    안티패턴 issues·환각 drop·orphan 액터·관계 카운트)를 그래프에서 보이는 별도 단계로 집계한다.
    """
    rel = state.get("relationships") or {}
    report = {
        "counts": {
            k: len(rel.get(k, []))
            for k in ("associations", "includes", "extends", "generalizations", "derived_use_cases")
        },
        "orphan_actors": rel.get("orphan_actors", []),
        "dropped_refs": rel.get("dropped_refs", []),
        "relationship_issues": rel.get("relationship_issues", []),
        # 의미 검증을 실제로 거쳤는지. "failed"면 relationship_issues가 비어 있어도
        # 그건 "안티패턴이 없다"가 아니라 "확인하지 못했다"는 뜻이다.
        "semantic_status": rel.get("semantic_status", "unknown"),
        # 반성 루프의 비용과 멈춘 이유(step3의 spec_report와 같은 이름).
        "repair_iters": rel.get("repair_iters", 0),
        "repair_stopped": rel.get("repair_stopped", "unknown"),
    }
    return {"relationship_report": report, "phase": "check_relationships"}


def _san(name: str) -> str:
    """이름을 PlantUML alias로 안전화(영숫자만, 숫자 시작 방지)."""
    alias = re.sub(r"\W+", "_", name).strip("_") or "n"
    return alias if alias[0].isalpha() else f"n_{alias}"


@contract("render_diagram", requires=("relationships", "use_cases", "actors"),
          produces=("diagram",))
def render_diagram(state: AgentState) -> dict:
    """관계 모델을 PlantUML 유스케이스 다이어그램으로 렌더링한다(결정론적 순수 함수)."""
    actors = state.get("actors") or []
    use_cases = state.get("use_cases") or []
    rel = state.get("relationships") or {}
    if not use_cases:
        return {"diagram": "@startuml\n@enduml", "phase": "diagram"}

    # 이름 → alias 매핑 (액터 A*, 원본 UC는 id, 파생 UC D*).
    actor_alias = {a["name"]: _san(a["name"]) for a in actors}
    uc_alias = {uc["name"]: uc["id"] for uc in use_cases}
    derived = rel.get("derived_use_cases") or []
    for i, d in enumerate(derived, 1):
        uc_alias.setdefault(d["name"], f"D{i}")

    def uc_ref(name: str) -> str:
        return uc_alias.get(name) or _san(name)

    def actor_ref(name: str) -> str:
        return actor_alias.get(name) or _san(name)

    # UML 관례: primary 액터는 시스템 왼쪽, supporting 액터는 오른쪽에 둔다. PlantUML은
    # rectangle 앞에 선언하면 왼쪽, 뒤에 선언하면 오른쪽으로 배치되는 경향을 이용한다.
    actor_kind = {a["name"]: a.get("kind", "primary") for a in actors}
    primary = [a for a in actors if a.get("kind") != "supporting"]
    supporting = [a for a in actors if a.get("kind") == "supporting"]

    lines = ["@startuml", "left to right direction"]
    for a in primary:
        lines.append(f'actor "{a["name"]}" as {actor_alias[a["name"]]}')
    lines.append("rectangle System {")
    for uc in use_cases:
        lines.append(f'  usecase "{uc["name"]}" as {uc["id"]}')
    for d in derived:
        lines.append(f'  usecase "{d["name"]}" as {uc_alias[d["name"]]}')
    lines.append("}")
    for a in supporting:
        lines.append(f'actor "{a["name"]}" as {actor_alias[a["name"]]}')

    # 연결선은 무방향(---, UML 표준). 렌더 순서로 primary/supporting을 구분한다:
    #  - primary(발의자):     actor --- use case
    #  - supporting(피호출): use case --- actor
    for a in rel.get("associations", []):
        if actor_kind.get(a["actor"]) == "supporting":
            lines.append(f'{uc_ref(a["use_case"])} --- {actor_ref(a["actor"])}')
        else:
            lines.append(f'{actor_ref(a["actor"])} --- {uc_ref(a["use_case"])}')
    for inc in rel.get("includes", []):
        lines.append(f'{uc_ref(inc["base_use_case"])} ..> {uc_ref(inc["included_use_case"])} : <<include>>')
    for ext in rel.get("extends", []):
        # UML: 확장 UC가 기반 UC를 향해 <<extend>> 의존.
        lines.append(f'{uc_ref(ext["extending_use_case"])} ..> {uc_ref(ext["base_use_case"])} : <<extend>>')
    for g in rel.get("generalizations", []):
        ref = actor_ref if g.get("kind") == "actor" else uc_ref
        lines.append(f'{ref(g["parent"])} <|-- {ref(g["child"])}')
    lines.append("@enduml")

    return {"diagram": "\n".join(lines), "phase": "diagram"}
