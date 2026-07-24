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
from dataclasses import replace

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
from appkb.verify import (
    unhedged_claims,
    verify_against_requirements,
    verify_diagram,
    verify_plan,
)

#: 컴퓨트 방식별 성격. **`deployHint`가 실제로 계획을 바꾸게 하는 표다.**
#:
#: 처음엔 힌트를 근거 라벨로만 기록하고 계획은 그대로 뒀는데, 그러면
#: `serverlessFunction`을 지정해도 **시간당 VM 단가**가 붙었다 — 서버리스는 호출당
#: 과금이라 그 값은 그냥 틀린 값이다. 실측으로 잡았다.
_COMPUTE_KIND = {
    # 값이 붙는가 · 어느 core 개념 위에 도는가 · svcmap 개념(서버리스 전용)
    "vm": {"priced": True, "hosts": ("core::vm",), "concept": None},
    "kubernetes": {"priced": True, "hosts": ("core::k8sCluster", "core::k8sNodeGroup"),
                   "concept": None},
    "serverlessFunction": {"priced": False, "hosts": (),
                           "concept": "serverlessFunction"},
}

#: 컴퓨트가 도는 데 **함께 있어야 하는 것**. bundlekb가 답하는 것을 그대로 쓴다.
#: 연결당 공유이므로 컴포넌트마다가 아니라 **계획에 한 벌만** 세운다 — 아니면
#: 컴포넌트 2개짜리 앱에 VPC가 2개 그려진다.
_SHARED_LABEL = {
    "core::vNet": "가상 네트워크",
    "core::subnet": "서브넷",
    "core::securityGroup": "보안 그룹",
    "core::sshKey": "SSH 키",
    "core::image": "OS 이미지",
    "core::k8sCluster": "쿠버네티스 클러스터",
    "core::k8sNodeGroup": "노드 그룹",
}

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


def _vendor_of(core_id: str, provider: str | None) -> tuple[str, bool]:
    """core 개념 → 그 프로바이더의 벤더 타입. `(타입, 짐작을 거쳤나)`.

    조사 1에서 찾은 다리를 그대로 쓴다. **대응은 cb-spider 드라이버를 읽어 사람이
    맞춘 것(짐작·검수됨)**이라 등급이 떨어지고, 그 사실이 노트에 실린다.
    """
    if not provider:
        return "", False
    from graphkb.agent_api import load_merged
    from graphkb.query import equivalents

    graph = load_merged()
    if graph is None or core_id not in graph.nodes:
        return "", False
    for peer in equivalents(graph, core_id):
        if peer.provider == provider:
            return peer.id, True
    return "", False


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
    #: 시간당 단가가 붙는 컴퓨트만. 서버리스는 여기 안 들어간다.
    _priced_computes: set[str] = set()

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
    #: 어느 컴퓨트 방식이 실제로 쓰였나 — 공유 인프라를 무엇으로 세울지 정한다.
    compute_kinds: set[str] = set()
    for cid, component in components.items():
        hint = component.get("deployHint")
        notes: list[Note] = []
        if hint:
            origin = ORIGIN_DESIGNER
            kind = hint["compute"]
            notes.append(Note(
                f"설계자가 {kind}로 지정"
                + (f" — {hint['reason']}" if hint.get("reason") else ""),
                ORIGIN_DESIGNER, "deployHint",
            ))
        else:
            origin = ORIGIN_INFERRED
            kind = "vm"
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
            notes.append(Note(
                "컴퓨트 방식은 VM으로 가정했습니다 — 설계가 지정하지 않았습니다"
                "(deployHint로 바꿀 수 있습니다)", ORIGIN_INFERRED, "",
            ))
        compute_kinds.add(kind)
        if cid in exposed:
            notes.append(Note("시퀀스에서 actor가 직접 호출 — 공개 노출",
                              ORIGIN_DESIGN, "sequence"))

        # **서버리스는 값이 다른 축이다.** 시간당 VM 단가를 붙이면 그냥 틀린 값이라
        # 관리형 서비스로 세우고 값을 붙이지 않는다(호출당 과금 데이터가 0건).
        spec = _COMPUTE_KIND[kind]
        if spec["concept"]:
            types = _svcmap_types(spec["concept"], provider)
            notes.append(Note(
                "서버리스는 호출당 과금이라 이 데이터셋에 단가가 없습니다 — "
                "값을 붙이지 않습니다", ORIGIN_KB, "costkb",
            ))
            if len(types) == 1:
                notes.append(Note(f"svcmap: app::{spec['concept']} → {types[0]}",
                                  ORIGIN_KB, "svcmap"))
            plan.nodes.append(PlanNode(
                id=cid, label=component["name"], role="managed", origin=origin,
                archetype=f"app::{spec['concept']}",
                type_id=types[0] if len(types) == 1 else "",
                candidates=tuple(types) if len(types) != 1 else (),
                notes=tuple(notes),
            ))
            if not types:
                plan.unresolved.append(
                    f"{cid}: 서버리스 함수에 대응하는 타입을 찾지 못했습니다"
                    + (f" (provider={provider})" if provider else "")
                )
            continue

        if kind == "kubernetes":
            # **파드가 도는 곳은 노드 그룹이다.** 컴포넌트마다 VM 단가를 붙이면
            # 같은 노드에 여러 파드가 올라가는 구조가 지워지고, 합치면 중복이 된다.
            notes.append(Note(
                "파드로 배포됩니다 — 값은 이 컴포넌트가 아니라 노드 그룹에 붙습니다",
                ORIGIN_INFERRED, "",
            ))
        plan.nodes.append(PlanNode(
            id=cid, label=component["name"], role="compute",
            origin=origin, notes=tuple(notes),
        ))
        if kind == "vm":
            _priced_computes.add(cid)

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

    # --- 3.5 공유 인프라 (bundlekb) --------------------------------------
    _add_shared_infra(plan, compute_kinds, provider, requirements, _priced_computes)

    # --- 4. 통신 선 ------------------------------------------------------
    known = {n.id for n in plan.nodes}
    for src, dst, label in sync_calls:
        # src == dst 는 한 배포 단위 안의 호출이다(easydep 단일 컴포넌트 어댑터가
        # 내부 비동기를 남긴다) — 배포 선이 아니므로 그리지 않는다. any_async
        # 신호는 이미 위에서 집계돼 큐 노드는 선다.
        if src in known and dst in known and src != dst:
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
        _attach_values(plan, provider, region, requirements, _priced_computes)
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


def _add_shared_infra(plan: DeploymentPlan, kinds: set[str], provider: str | None,
                      requirements: dict, priced: set[str]) -> None:
    """컴퓨트가 도는 데 **함께 있어야 하는 것**을 bundlekb에서 가져온다.

    붙이기 전까지 배포 다이어그램에 **네트워크 경계가 통째로 없었다** — VPC도
    서브넷도 보안 그룹도. bundlekb는 정확히 "무엇이 딸려 오나"에 답하려고 만든
    축인데 구성기가 부르질 않았다(실측으로 잡았다).

    **연결당 공유라 계획에 한 벌만 세운다.** 컴포넌트마다 세우면 컴포넌트 2개짜리
    앱에 VPC가 2개 그려진다 — tumblebug이 스스로 "연결당 공유라 이미 있으면
    재사용한다"고 밝힌 것과도 어긋난다.
    """
    from bundlekb.dataset import default_bundle_for

    anchors = []
    if "vm" in kinds:
        anchors.append("core::vm")
    if "kubernetes" in kinds:
        anchors.append("core::k8sCluster")
    if not anchors:
        # 전부 서버리스면 VM 네트워크가 필요 없다 — 없는 것을 그리지 않는다.
        return

    seen: set[str] = set()
    for anchor in anchors:
        bundle = default_bundle_for(anchor)
        if bundle is None:
            plan.unresolved.append(f"{anchor}: 함께 필요한 리소스 정보를 찾지 못했습니다")
            continue
        for member in bundle.members:
            core_id = member.type_id
            if core_id in seen or core_id == "core::vm":
                continue  # core::vm은 컴포넌트 노드가 이미 대표한다
            seen.add(core_id)
            if core_id == "core::image":
                # **이미지는 리소스가 아니라 값이다.** 벤더 타입이 없는 게 정상이라
                # 매핑 미결로 올리면 거짓 미결이 된다 — 대신 실제 이미지 id를 붙인다.
                _add_image_note(plan, provider, requirements, priced)
                continue
            node_id = core_id.split("::")[-1].lower()
            label = _SHARED_LABEL.get(core_id, node_id)
            notes = [Note(
                f"{bundle.name}: {member.tier}"
                + (f" — {member.note}" if member.note else ""),
                ORIGIN_KB, "bundlekb",
            )]
            if member.count != 1:
                # 이름 붙은 템플릿의 대수는 **그 템플릿의 것**이지 이 앱의 것이 아니다
                # (k8scluster-across는 멀티클라우드 데모라 클러스터가 8개다).
                notes.append(Note(
                    f"위 개수({member.count})는 '{bundle.name}' 템플릿의 값이며 "
                    "이 앱에 필요한 수가 아닙니다", ORIGIN_KB, "bundlekb",
                ))
            if bundle.caveat:
                notes.append(Note(bundle.caveat, ORIGIN_KB, "bundlekb"))
            type_id, hedged = _vendor_of(core_id, provider)
            if type_id:
                notes.append(Note(
                    f"{core_id} → {type_id}"
                    + (" (대응은 짐작·검수됨 — cb-spider 드라이버를 읽어 사람이 맞춘 것)"
                       if hedged else ""),
                    ORIGIN_KB, "mapping-graph",
                ))
            elif provider:
                plan.unresolved.append(
                    f"{node_id}: {provider}에서 {core_id}에 해당하는 타입을 찾지 못했습니다"
                )
            if core_id == "core::subnet":
                notes.extend(_subnet_notes(provider, requirements))
            if core_id == "core::k8sNodeGroup":
                notes.extend(_node_group_notes())
            # **선을 긋지 않는다.** 컴퓨트마다 공유 자원 4개로 선을 그으면 컴포넌트
            # 5개짜리 앱에 선이 20개 늘어 그림이 못 쓰게 된다(실측: 2개에 이미 15개).
            # 관계는 다이어그램의 **중첩**이 표현한다 — UML 배포 다이어그램의 정석이고,
            # tumblebug이 "연결당 공유"라 말한 것과도 맞는다.
            plan.nodes.append(PlanNode(
                id=node_id, label=label, role="shared",
                origin=ORIGIN_KB, type_id=type_id, notes=tuple(notes),
            ))


def _add_image_note(plan: DeploymentPlan, provider: str | None,
                    requirements: dict, priced: set[str]) -> None:
    """OS 이미지는 **값**이라 노드가 아니라 컴퓨트의 노트로 붙인다."""
    text = "요청에 이미지 ID를 줘야 합니다 — 설계 산출물에는 없는 정보입니다"
    origin, source = ORIGIN_KB, "bundlekb"
    if provider:
        from kbcommon.images import describe

        found = describe(provider, requirements.get("region"), "x86_64", limit=1)
        first = next(
            (ln.strip() for ln in found.splitlines()[1:] if ln.strip().startswith("-")),
            "",
        )
        if first:
            text = f"OS 이미지를 골라야 합니다. 이 리전의 기본 이미지 예: {first[1:].strip()}"
            source = "basic-images"
    plan.nodes[:] = [
        node if node.id not in priced
        else replace(node, notes=node.notes + (Note(text, origin, source),))
        for node in plan.nodes
    ]


def _node_group_notes() -> list[Note]:
    """노드 그룹의 최소 사양. **cb-tumblebug이 정한 값이지 쿠버네티스가 정한 게 아니다.**"""
    from sizingkb.dataset import rules_of
    from sizingkb.model import MINIMUM

    notes = []
    for rule in rules_of(MINIMUM, "k8s-node"):
        notes.append(Note(
            f"노드 최소 {rule.metric} {rule.value}{rule.unit or ''} "
            "(cb-tumblebug이 요구하는 값이며 쿠버네티스가 정한 값이 아닙니다)",
            ORIGIN_KB, "sizingkb",
        ))
    return notes


def _subnet_notes(provider: str | None, requirements: dict) -> list[Note]:
    """서브넷에 붙는 사이징 사실 — 개수와 용량. 둘 다 sizingkb가 답한다."""
    from sizingkb.dataset import rules_of
    from sizingkb.model import REQUIRED_COUNT

    notes: list[Note] = []
    if provider:
        for rule in rules_of(REQUIRED_COUNT, provider):
            if rule.metric == "requiredSubnetCount":
                # 원본 `unit`이 "서브넷"이라 그대로 붙이면 "2서브넷가"가 된다 — 실측.
                notes.append(Note(
                    f"이 프로바이더의 클러스터는 서브넷이 {rule.value}개 필요합니다",
                    ORIGIN_KB, "sizingkb",
                ))
    if requirements.get("multiZone"):
        # 계약이 받아 놓고 안 읽던 칸이다.
        notes.append(Note(
            "요구사항이 multiZone이라 서브넷을 여러 가용영역에 나눠 둬야 합니다",
            ORIGIN_DESIGN, "requirements",
        ))
    if provider:
        from sizingkb.agent_api import subnet_capacity

        first = subnet_capacity(24, provider).splitlines()[0]
        notes.append(Note(f"참고 — {first}", ORIGIN_KB, "sizingkb"))
    return notes


def _attach_values(plan: DeploymentPlan, provider: str, region: str | None,
                   requirements: dict, priced: set[str]) -> None:
    """값이 붙는 노드에 스펙·단가·성능 소견을 붙인다.

    **어디에 붙이느냐가 핵심이다.** 관리형 서비스는 가격 축이 없어 안 붙고,
    서버리스는 호출당 과금이라 안 붙고, 쿠버네티스 컴포넌트는 파드라 안 붙는다 —
    대신 파드가 도는 **노드 그룹**이 값을 받는다.
    """
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
        # **서버리스는 role이 managed라 여기 안 걸린다** — 시간당 단가를 붙이면
        # 틀린 값이 되는 그 자리다. 쿠버네티스 컴포넌트도 안 걸리고, 대신
        # 노드 그룹(shared)이 값을 받는다 — 파드가 도는 곳이 거기다.
        if node.id not in priced and node.id != "k8snodegroup":
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
        # 단가는 노트 문장과 **별도로 기계 값**으로도 싣는다 — 예산 대조가 읽는다.
        updated.append(replace(
            node, notes=tuple(notes), hourly_usd=spec.get("hourlyUSD") or None,
        ))
    plan.nodes[:] = updated


def _render_plan_text(plan: DeploymentPlan) -> str:
    """계획을 사람이 읽는 텍스트로. **근거를 줄마다 붙인다.**"""
    from appkb.plan import ORIGIN_LABEL

    lines = [f"{plan.name} — 배포 구성"]
    for role, title in (("actor", "사용자"), ("compute", "직접 배포"),
                        ("managed", "관리형 서비스"),
                        ("shared", "공유 인프라 (연결당 한 벌)"),
                        ("external", "외부 시스템")):
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


def deployment_answer(design: dict, diagram: bool = True) -> str:
    """설계 dict → 답변 텍스트 전부(계획·대조·다이어그램·자체 검증).

    도구 껍데기와 분리해 둔 이유: **easydep의 배포 다이어그램 노드가 이 함수를
    그대로 부른다**(재편 계획 P1c). LLM도 agents SDK도 필요 없는 결정론 경로다.
    """
    plan = compose(design)
    if plan.unresolved and not plan.nodes:
        return "입력 계약을 통과하지 못했습니다:\n" + "\n".join(
            f"  - {item}" for item in plan.unresolved
        )

    text = _render_plan_text(plan)

    # 요구사항 대조 — research.md 목표 1의 "부합 측정". 판정문이라 항상 싣는다:
    # "기준 없음"도 판정이다(침묵이면 부분 답이 완전한 답처럼 읽힌다).
    from costkb.agent_api import HOURS_PER_MONTH

    conformance = verify_against_requirements(
        plan, design.get("requirements"), HOURS_PER_MONTH
    )
    if conformance:
        text += "\n\n[요구사항 대조]\n" + "\n".join(f"  - {c}" for c in conformance)

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


def deployment_answer_from_easydep(
    name: str,
    *,
    api_spec: dict | None = None,
    class_puml: str = "",
    sequence_puml: str = "",
    erd_puml: str = "",
    resource_spec: dict | None = None,
    diagram: bool = True,
) -> str:
    """easydep 최종 산출물 → 배포 답변. **easydep 배포 다이어그램 노드의 진입점**(P1c).

    어댑터가 못 읽은 것·추정한 것과 제약 계약 위반을 답변 끝에 그대로 싣는다 —
    어댑터의 휴리스틱을 조용히 삼키면 부분 답이 완전한 답처럼 읽힌다.
    """
    from appkb.contract import validate_request
    from appkb.easydep import design_from_easydep

    design, skipped = design_from_easydep(
        name,
        api_spec=api_spec,
        class_puml=class_puml,
        sequence_puml=sequence_puml,
        erd_puml=erd_puml,
        resource_spec=resource_spec,
    )
    text = deployment_answer(design, diagram=diagram)
    if resource_spec is not None:
        problems = validate_request(resource_spec)
        if problems:
            text += "\n\n[제약(RESOURCE_SPEC) 검증]\n" + "\n".join(
                f"  - {p}" for p in problems
            )
    if skipped:
        text += "\n\n[어댑터가 읽지 못한 것·추정한 것]\n" + "\n".join(
            f"  - {s}" for s in skipped
        )
    return text


@function_tool
def design_to_deployment(design_json: str, diagram: bool = True) -> str:
    """앱 설계 산출물(JSON)에서 **배포 구성**을 만든다 — 구성요소·관리형 서비스·연결.

    입력은 `appkb/schema.json` 계약을 따르는 JSON이다(클래스·시퀀스·ER·OpenAPI를
    한 문서에 담은 것). 계약을 어기면 **무엇이 어긋났는지 목록으로** 답한다.

    답에는 근거가 줄마다 붙는다 — 설계 산출물이 말한 것 / 설계자가 지정한 것 /
    지식베이스가 답한 것 / **우리가 추론한 것**. ⚠ 표시는 추론이므로 사용자에게
    그대로 전하세요. 관리형 서비스는 가격 축이 없어 값이 안 붙고 **합계도 내지
    않습니다** — 그 고지도 그대로 전하세요.

    답 끝의 [요구사항 대조]는 예산·규모·multiZone 판정입니다. **"초과 확정"과
    "부합 단정 불가"는 다른 판정입니다** — 뭉개지 말고 그대로 전하세요.

    Args:
        design_json: 설계 산출물 JSON 문자열.
        diagram: True면 PlantUML 다이어그램도 함께 낸다.
    """
    try:
        design = json.loads(design_json)
    except json.JSONDecodeError as exc:
        return f"설계 JSON을 읽지 못했습니다: {exc}"
    print(f"\n[설계질의] 배포 구성: {design.get('name')!r}")
    return deployment_answer(design, diagram=diagram)


DESIGN_TOOLS = [design_to_deployment]
