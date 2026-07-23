"""설계 산출물(JSON) → 배포 계획 → PlantUML. 앱 계층 P3.

## 왜 도구 계층인가

조인이 여기서 일어난다 — appkb(계약) × graphkb/svcmap(서비스 대응) × costkb/perfkb(값).
KB끼리는 import하지 않는 규약이라 엮는 일은 이 계층에서만 할 수 있고,
`guideline_tools`(bundlekb×graphkb×costkb×perfkb)가 이미 그 자리다.

## 추론 규칙 — 전부 `inferred`다

설계 신호에서 배포 아키타입으로 가는 걸음은 **우리 추론**이다. 사실로 만들 방법이
없으므로 hedge를 답에 싣는 것까지가 우리가 할 수 있는 일이다.

    OpenAPI 산출물이 있다        → HTTP 서비스 (인바운드 노출)
    비동기 메시지의 수신자다      → 워커 (인바운드 없음)
    ER 엔티티를 소유한다          → 영속 저장소 필요
    engineHint가 있다             → 그 엔진의 flavor로 좁힌다
    async 메시지가 하나라도 있다  → 큐 필요
    actor가 컴포넌트를 부른다     → 공개 노출
    securitySchemes가 있다        → 비밀 저장소 후보

**deployHint만 예외**로 `designer` 근거가 되는데, 그것도 유보 대상이다(설계자의
주장이지 검증된 사실이 아니다).

## 값은 붙이되 합계는 없다

컴퓨트 노드에만 단가가 붙는다. 관리형 서비스 가격은 이 데이터셋에 **0건**이라
`resource_guideline`과 같은 원칙이 그대로 적용된다 — 값 없는 것을 0으로 두고
더하면 실제보다 낮은 숫자가 나온다.
"""

from __future__ import annotations

import json

from agents import function_tool

from appkb.contract import validate_design
from appkb.diagram import render
from appkb.plan import (
    ORIGIN_DESIGN,
    ORIGIN_DESIGNER,
    ORIGIN_INFERRED,
    ORIGIN_KB,
    DeploymentPlan,
    Note,
    PlanEdge,
    PlanNode,
    needs_hedge,
)
from appkb.verify import unhedged_claims, verify_diagram, verify_plan

#: engineHint → app:: 개념. 모르는 힌트는 관계형으로 몰지 않고 **미결로 올린다.**
_ENGINE_CONCEPT = {
    "postgresql": "relationalDatabase", "postgres": "relationalDatabase",
    "mysql": "relationalDatabase", "mariadb": "relationalDatabase",
    "sqlserver": "relationalDatabase", "oracle": "relationalDatabase",
    "redis": "keyValueCache", "memcached": "keyValueCache",
    "mongodb": "nosqlDatabase", "dynamodb": "nosqlDatabase",
    "cassandra": "nosqlDatabase", "firestore": "nosqlDatabase",
}

#: engineHint가 벤더 flavor까지 좁히는 경우. svcmap이 한 개념에 여러 타입을 줄 때
#: 그중 어느 것인지 고르는 유일한 근거다 — 없으면 후보를 다 보여준다.
_ENGINE_FLAVOR = {
    "postgresql": ("postgre",), "postgres": ("postgre",),
    "mysql": ("mysql",), "mariadb": ("mysql",),
}


def _artifacts(design: dict, kind: str) -> list[dict]:
    return [a for a in design["artifacts"] if a["kind"] == kind]


def _svcmap_types(concept: str, provider: str | None) -> list[str]:
    """app:: 개념의 벤더 타입 후보. provider를 주면 그 프로바이더만."""
    from graphkb.agent_api import load_merged

    graph = load_merged()
    if graph is None:
        return []
    app_id = f"app::{concept}"
    out = []
    for edge in graph.edges:
        if edge.from_id != app_id or edge.type != "equivalent_to":
            continue
        node = graph.nodes.get(edge.to_id)
        if node is None:
            continue
        if provider and node.provider != provider:
            continue
        out.append(edge.to_id)
    return sorted(out)


def _pick_flavor(types: list[str], engine: str | None) -> list[str]:
    """engineHint로 후보를 좁힌다. **못 좁히면 전부 돌려준다** — 하나를 임의로
    고르는 것이 이 저장소가 막아 온 실패다."""
    if not engine:
        return types
    keys = _ENGINE_FLAVOR.get(engine.lower())
    if not keys:
        return types
    narrowed = [t for t in types if any(k in t.lower() for k in keys)]
    return narrowed or types


def _compute_note(spec: dict) -> str:
    hourly = spec.get("hourlyUSD")
    body = (
        f"{spec.get('provider')} {spec.get('region')} {spec.get('specName')} "
        f"{spec.get('vCPU')} vCPU / {spec.get('memGiB')} GiB"
    )
    return body + (f" · ${hourly:.4f}/h" if hourly else " · 단가 미상")


def compose(design: dict) -> DeploymentPlan:
    """설계 JSON → 배포 계획. **계약 검증을 통과한 입력만 들어온다고 가정하지 않는다.**"""
    problems = validate_design(design)
    if problems:
        plan = DeploymentPlan(name=design.get("name") or "(이름 없음)")
        plan.unresolved = [f"입력 계약 위반: {p}" for p in problems]
        return plan

    requirements = design.get("requirements") or {}
    provider = (requirements.get("provider") or "").strip().lower() or None
    region = (requirements.get("region") or "").strip() or None

    plan = DeploymentPlan(name=design["name"])
    components = {c["id"]: c for c in design["components"]}

    # --- 1. 신호 모으기 ---------------------------------------------------
    has_api = {a["componentId"] for a in _artifacts(design, "openapi")}
    needs_secret = {
        a["componentId"] for a in _artifacts(design, "openapi")
        if (a["openapi"].get("components") or {}).get("securitySchemes")
    }
    owners: dict[str, list[str]] = {}
    engine_of: dict[str, str] = {}
    for artifact in _artifacts(design, "er"):
        for entity in artifact["entities"]:
            owners.setdefault(entity["ownerComponentId"], []).append(entity["name"])
            if artifact.get("engineHint"):
                engine_of[entity["ownerComponentId"]] = artifact["engineHint"]

    exposed: set[str] = set()
    async_targets: set[str] = set()
    sync_calls: list[tuple[str, str, str]] = []
    any_async = False
    for artifact in _artifacts(design, "sequence"):
        by_pid = {p["id"]: p for p in artifact["participants"]}
        for message in artifact["messages"]:
            src, dst = by_pid[message["from"]], by_pid[message["to"]]
            if message["async"]:
                any_async = True
                if dst.get("componentId"):
                    async_targets.add(dst["componentId"])
            if src.get("actor") and dst.get("componentId"):
                exposed.add(dst["componentId"])
            src_id = src.get("componentId") or src.get("externalId") or src["id"]
            dst_id = dst.get("componentId") or dst.get("externalId") or dst["id"]
            sync_calls.append((src_id, dst_id, message.get("label") or ""))

    # --- 2. 컴퓨트 노드 ---------------------------------------------------
    for cid, component in components.items():
        hint = component.get("deployHint")
        notes: list[Note] = []
        if hint:
            origin = ORIGIN_DESIGNER
            notes.append(Note(
                f"설계자가 {hint['compute']}로 지정" + (f" — {hint['reason']}" if hint.get("reason") else ""),
                ORIGIN_DESIGNER, "deployHint",
            ))
        else:
            origin = ORIGIN_INFERRED
            if cid in has_api:
                notes.append(Note("OpenAPI 산출물이 있어 HTTP 서비스로 봄",
                                  ORIGIN_INFERRED, "openapi"))
            elif cid in async_targets:
                notes.append(Note("비동기 메시지의 수신자라 워커로 봄",
                                  ORIGIN_INFERRED, "sequence"))
            else:
                notes.append(Note("배포 형태를 정할 신호가 설계에 없음",
                                  ORIGIN_INFERRED, ""))
                plan.unresolved.append(
                    f"{cid}: OpenAPI도 비동기 수신도 없어 배포 형태를 정하지 못했습니다"
                )
        if cid in exposed:
            notes.append(Note("시퀀스에서 actor가 직접 호출 — 공개 노출",
                              ORIGIN_DESIGN, "sequence"))
        plan.nodes.append(PlanNode(
            id=cid, label=component["name"], role="compute",
            origin=origin, notes=tuple(notes),
        ))

    for external in design.get("externals") or []:
        plan.nodes.append(PlanNode(
            id=external["id"], label=external["name"], role="external",
            origin=ORIGIN_DESIGN,
            notes=(Note("설계가 외부 시스템으로 선언", ORIGIN_DESIGN, "externals"),),
        ))

    # --- 3. 관리형 서비스 -------------------------------------------------
    def add_managed(node_id: str, label: str, concept: str, why: Note) -> None:
        types = _pick_flavor(_svcmap_types(concept, provider), engine_of.get(node_id))
        notes = [why]
        if not provider:
            notes.append(Note("프로바이더 미지정이라 특정 클라우드로 좁히지 못함",
                              ORIGIN_INFERRED, "requirements"))
        chosen, candidates = "", ()
        if len(types) == 1:
            chosen = types[0]
            notes.append(Note(f"svcmap: app::{concept} → {chosen}", ORIGIN_KB, "svcmap"))
        elif types:
            candidates = tuple(types)
            notes.append(Note(
                f"svcmap: app::{concept}에 후보 {len(types)}개 — 하나를 고르지 않음",
                ORIGIN_KB, "svcmap",
            ))
        else:
            plan.unresolved.append(
                f"{node_id}: app::{concept}에 대응하는 타입을 찾지 못했습니다"
                + (f" (provider={provider})" if provider else "")
            )
        plan.nodes.append(PlanNode(
            id=node_id, label=label, role="managed", origin=ORIGIN_INFERRED,
            archetype=f"app::{concept}", type_id=chosen, candidates=candidates,
            notes=tuple(notes),
        ))

    for cid, entities in sorted(owners.items()):
        engine = engine_of.get(cid)
        concept = _ENGINE_CONCEPT.get((engine or "").lower(), "relationalDatabase")
        if engine and (engine or "").lower() not in _ENGINE_CONCEPT:
            plan.unresolved.append(
                f"{cid}: engineHint '{engine}'를 아는 개념으로 옮기지 못해 "
                "관계형으로 가정했습니다"
            )
        why = Note(
            f"엔티티 {len(entities)}개를 소유({', '.join(entities[:3])}) → 영속 저장소 필요",
            ORIGIN_INFERRED, "er",
        )
        add_managed(f"{cid}-db", f"{components[cid]['name']} 저장소", concept, why)
        plan.edges.append(PlanEdge(cid, f"{cid}-db", "읽기/쓰기", ORIGIN_INFERRED))

    if any_async:
        add_managed(
            "message-queue", "메시지 큐", "messageQueue",
            Note("시퀀스에 비동기 메시지가 있어 큐가 필요하다고 봄",
                 ORIGIN_INFERRED, "sequence"),
        )
    for cid in sorted(needs_secret):
        plan.edges.append(PlanEdge(cid, "secret-store", "자격 증명 조회", ORIGIN_INFERRED))
    if needs_secret:
        add_managed(
            "secret-store", "비밀 저장소", "secretStore",
            Note("OpenAPI에 securitySchemes가 있어 자격 증명 보관이 필요하다고 봄",
                 ORIGIN_INFERRED, "openapi"),
        )

    # --- 4. 통신 선 ------------------------------------------------------
    known = {n.id for n in plan.nodes}
    for src, dst, label in sync_calls:
        if src in known and dst in known:
            is_async = dst in async_targets and src in components
            plan.edges.append(PlanEdge(
                src, dst, label, ORIGIN_DESIGN,
                async_=bool(is_async and any_async),
            ))
    # actor는 컴포넌트가 아니라 사람이다 — 노출된 컴포넌트마다 하나 세운다.
    for cid in sorted(exposed):
        if "end-user" not in known:
            plan.nodes.append(PlanNode(
                id="end-user", label="사용자", role="actor", origin=ORIGIN_DESIGN,
                notes=(Note("시퀀스의 actor", ORIGIN_DESIGN, "sequence"),),
            ))
            known.add("end-user")
        plan.edges.append(PlanEdge("end-user", cid, "요청", ORIGIN_DESIGN))

    # --- 5. 값 ------------------------------------------------------------
    if provider:
        _attach_values(plan, provider, region, requirements)
    else:
        plan.notes.append(Note(
            "프로바이더가 없어 단가·리전 조인을 하지 않았습니다 — 임의로 고르지 않습니다",
            ORIGIN_KB, "requirements",
        ))

    plan.notes.append(Note(
        "관리형 서비스 가격은 이 데이터셋에 없어 값이 붙지 않습니다. "
        "**합계를 내지 않습니다** — 값 없는 것을 0으로 두면 실제보다 낮아집니다.",
        ORIGIN_KB, "costkb",
    ))
    return plan


def _attach_values(plan: DeploymentPlan, provider: str, region: str | None,
                   requirements: dict) -> None:
    """컴퓨트 노드에 스펙·단가·성능 소견을 붙인다. 관리형에는 붙지 않는다(가격 축 없음)."""
    from costkb import dataset as cost_dataset

    from .cost_tools import _perf_note

    users = requirements.get("expectedConcurrentUsers")
    # 규모→스펙은 지식베이스 근거가 없는 추정이다. 최소치만 올리고 그 사실을 적는다.
    vcpu, mem = (2, 4) if not users or users <= 500 else (4, 8)
    specs = cost_dataset.filter_specs(
        vcpu_min=vcpu, mem_min_gib=mem, provider=provider, region=region,
        sort_by="cost", limit=1,
    )
    if not specs:
        plan.unresolved.append(
            f"{provider}{f'/{region}' if region else ''}에서 vCPU {vcpu}·메모리 "
            f"{mem}GiB 이상인 스펙을 찾지 못해 컴퓨트 값을 붙이지 못했습니다"
        )
        return
    spec = specs[0]
    note_text = _compute_note(spec)
    perf = _perf_note(spec)
    updated = []
    for node in plan.nodes:
        if node.role != "compute":
            updated.append(node)
            continue
        notes = list(node.notes)
        notes.append(Note(note_text, ORIGIN_KB, "costkb"))
        if users:
            notes.append(Note(
                f"동시 사용자 {users}명 기준 vCPU {vcpu}·메모리 {mem}GiB 이상으로 잡음 "
                "(지식베이스 근거가 없는 추정)", ORIGIN_INFERRED, "requirements",
            ))
        if perf.text:
            notes.append(Note(perf.text, ORIGIN_KB, "perfkb"))
        updated.append(PlanNode(
            id=node.id, label=node.label, role=node.role, origin=node.origin,
            archetype=node.archetype, type_id=node.type_id,
            candidates=node.candidates, notes=tuple(notes),
        ))
    plan.nodes[:] = updated


def _render_plan_text(plan: DeploymentPlan) -> str:
    """계획을 사람이 읽는 텍스트로. **근거를 줄마다 붙인다.**"""
    from appkb.plan import ORIGIN_LABEL

    lines = [f"{plan.name} — 배포 구성"]
    for role, title in (("actor", "사용자"), ("compute", "직접 배포"),
                        ("managed", "관리형 서비스"), ("external", "외부 시스템")):
        nodes = [n for n in plan.nodes if n.role == role]
        if not nodes:
            continue
        lines.append(f"\n[{title}] {len(nodes)}개")
        for node in nodes:
            mark = " ⚠" if needs_hedge(node.origin) else ""
            head = f"  - {node.label} ({node.id}){mark}"
            if node.type_id:
                head += f" → {node.type_id}"
            elif node.candidates:
                head += f" → 후보 {len(node.candidates)}개: " + ", ".join(node.candidates[:3])
            lines.append(head)
            for note in node.notes:
                lines.append(f"      · [{ORIGIN_LABEL[note.origin]}] {note.text}")
    if plan.edges:
        lines.append(f"\n[연결] {len(plan.edges)}개")
        for edge in plan.edges:
            arrow = "⇢" if edge.async_ else "→"
            lines.append(
                f"  {edge.from_id} {arrow} {edge.to_id}"
                f"{f' : {edge.label}' if edge.label else ''}"
                f"  [{ORIGIN_LABEL[edge.origin]}]"
            )
    if plan.unresolved:
        lines.append(f"\n[답하지 못한 것] {len(plan.unresolved)}건")
        lines.extend(f"  - {item}" for item in plan.unresolved)
    for note in plan.notes:
        lines.append(f"\n※ {note.text}")
    if plan.hedged_count:
        lines.append(
            f"\n⚠ 위 {plan.hedged_count}건(⚠ 표시)은 **설계 신호에서 우리가 추론한 것**"
            "이거나 설계자가 지정한 것이며, 검증된 사실이 아닙니다."
        )
    return "\n".join(lines)


@function_tool
def design_to_deployment(design_json: str, diagram: bool = True) -> str:
    """앱 설계 산출물(JSON)에서 **배포 구성**을 만든다 — 구성요소·관리형 서비스·연결.

    입력은 `appkb/schema.json` 계약을 따르는 JSON이다(클래스·시퀀스·ER·OpenAPI를
    한 문서에 담은 것). 계약을 어기면 **무엇이 어긋났는지 목록으로** 답한다.

    답에는 근거가 줄마다 붙는다 — 설계 산출물이 말한 것 / 설계자가 지정한 것 /
    지식베이스가 답한 것 / **우리가 추론한 것**. ⚠ 표시는 추론이므로 사용자에게
    그대로 전하세요. 관리형 서비스는 가격 축이 없어 값이 안 붙고 **합계도 내지
    않습니다** — 그 고지도 그대로 전하세요.

    Args:
        design_json: 설계 산출물 JSON 문자열.
        diagram: True면 PlantUML 다이어그램도 함께 낸다.
    """
    try:
        design = json.loads(design_json)
    except json.JSONDecodeError as exc:
        return f"설계 JSON을 읽지 못했습니다: {exc}"
    print(f"\n[설계질의] 배포 구성: {design.get('name')!r}")

    plan = compose(design)
    if plan.unresolved and not plan.nodes:
        return "입력 계약을 통과하지 못했습니다:\n" + "\n".join(
            f"  - {item}" for item in plan.unresolved
        )

    text = _render_plan_text(plan)
    problems = verify_plan(plan)
    if diagram:
        uml = render(plan)
        problems += verify_diagram(plan, uml)
        text += "\n\n```plantuml\n" + uml + "\n```"
    naked = unhedged_claims(plan)
    if naked:
        problems.append(f"[계획] 근거 줄 없는 추론 노드: {naked}")
    if problems:
        # **우리가 만든 그림을 우리가 검사한 결과**다. 숨기면 검사가 무의미해진다.
        text += "\n\n⚠ 자체 검증에서 걸린 것:\n" + "\n".join(f"  - {p}" for p in problems)
    return text


DESIGN_TOOLS = [design_to_deployment]
