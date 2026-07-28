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
import re
from dataclasses import dataclass, replace

from agents import function_tool

from app.deployment.appkb.contract import validate_design
from app.deployment.appkb.diagram import render
from app.deployment.appkb.plan import (
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
from app.deployment.appkb.verify import (
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
    "core::vNet": "virtual network",
    "core::subnet": "subnet",
    "core::securityGroup": "security group",
    "core::sshKey": "SSH key",
    "core::image": "OS image",
    "core::k8sCluster": "Kubernetes cluster",
    "core::k8sNodeGroup": "node group",
}

#: engineHint → app:: 개념. 모르는 힌트는 관계형으로 몰지 않고 **미결로 올린다.**
#:
#: **kafka는 일부러 안 담는다** — ER 저장소로서의 kafka는 큐·스트림·저장 어느
#: 축인지가 설계 의도에 달려 있어(이벤트 소싱? 버퍼?) 한 개념으로 몰면 조용히
#: 틀린 서비스가 나온다. 미결 + 패턴 자문(pattern-advisory)으로 남긴다.
_ENGINE_CONCEPT = {
    "postgresql": "relationalDatabase", "postgres": "relationalDatabase",
    "mysql": "relationalDatabase", "mariadb": "relationalDatabase",
    "sqlserver": "relationalDatabase", "oracle": "relationalDatabase",
    "redis": "keyValueCache", "memcached": "keyValueCache",
    "mongodb": "nosqlDatabase", "dynamodb": "nosqlDatabase",
    "cassandra": "nosqlDatabase", "firestore": "nosqlDatabase",
    "elasticsearch": "searchIndex", "opensearch": "searchIndex",
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
    from app.deployment.graphkb.agent_api import load_merged
    from app.deployment.graphkb.query import equivalents

    graph = load_merged()
    if graph is None or core_id not in graph.nodes:
        return "", False
    for peer in equivalents(graph, core_id):
        if peer.provider == provider:
            return peer.id, True
    return "", False


def _svcmap_types(concept: str, provider: str | None) -> list[str]:
    """app:: 개념의 벤더 타입 후보. provider를 주면 그 프로바이더만."""
    from app.deployment.graphkb.agent_api import load_merged

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


def _pattern_advisory(query: str) -> Note | None:
    """아키타입 분류가 애매할 때 patternkb 자문 한 줄 (앱 계층 P4).

    **분류를 바꾸지 않는다.** 산문 지침은 클라우드 사실이 아니라서, 참고 인용을
    노트로 달고 그 성격을 문장에 함께 싣는 것까지가 자문의 전부다 — 근거 라벨은
    `pattern-advisory`이고 basis는 영원히 inferred다.
    """
    from app.deployment.patternkb.query import search

    hits = search(query, limit=1)
    if not hits:
        return None
    hit = hits[0]
    return Note(
        f"Reference guidance '{hit.title}' ({hit.path}) — design guidance, not a "
        "cloud fact",
        ORIGIN_KB, "pattern-advisory",
    )


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


def _managed_axes_note(archetype: str, provider: str | None,
                       region: str | None) -> Note | None:
    """관리형 노드의 **과금 축** 노트 (수록: azure 6종 · gcp objectStorage — ⑥-B/보강 3).

    단가 한 칸이 아니다: 인스턴스-시간형만 시간당 단가가 성립하고, 용량-비례형은
    곱할 수량이 사이징 결과이며, 사용량형은 트래픽을 알아야 한다(조사 문서).
    미빌드(None)와 "봤는데 없음"([])은 뜻이 반대라 그대로 가른다.
    """
    if provider not in ("azure", "gcp", "aws") or not region:
        return None
    from app.deployment.costkb import dataset as cost_dataset

    if not cost_dataset.managed_built(provider):
        # 이 프로바이더의 산출물이 없다(aws는 로컬 빌드 전용이라 이게 기본) —
        # "수록 없음(봤는데 없다)"이라 말하면 거짓이다. 전역 고지에 맡긴다.
        return None
    axes = cost_dataset.managed_axes(archetype, region)
    if axes is None:
        return None  # 미빌드 — 전역 고지("가격 없음")가 그대로 참이다
    if not axes:
        return Note(
            f"Billing axes: not included — {provider}'s managed pricing has no entry "
            "for this archetype and region (that does not mean it is free)",
            ORIGIN_KB, "costkb",
        )
    by_axis: dict[str, list[dict]] = {}
    for record in axes:
        by_axis.setdefault(record["axis"], []).append(record)
    parts = []
    if instance := by_axis.get("instanceHour"):
        lo = min(r["unitPriceUSD"] for r in instance)
        hi = max(r["unitPriceUSD"] for r in instance)
        span = f"${lo:.4f}/h" if lo == hi else f"${lo:.4f}~${hi:.4f}/h"
        parts.append(f"instance-hour {len(instance)} kinds {span}")
    if capacity := by_axis.get("capacityRate"):
        parts.append(
            f"capacity-rate {len(capacity)} kinds (per vCore · RU · GB/month — the "
            "quantity to multiply by is a sizing result)"
        )
    if usage := by_axis.get("usage"):
        parts.append(
            f"usage {len(usage)} kinds (operations · searches · executions — you have "
            "to know the usage)"
        )
    return Note(
        f"Billing axes ({provider} {region}, not a single unit price): "
        + " · ".join(parts) + " — no total is produced", ORIGIN_KB, "costkb",
    )


def _compute_note(spec: dict) -> str:
    hourly = spec.get("hourlyUSD")
    body = (
        f"{spec.get('provider')} {spec.get('region')} {spec.get('specName')} "
        f"{spec.get('vCPU')} vCPU / {spec.get('memGiB')} GiB"
    )
    return body + (f" · ${hourly:.4f}/h" if hourly else " · unit price unknown")


@dataclass(frozen=True)
class _Signals:
    """설계 산출물에서 모은 결정론 신호 — compose 단계들이 공유하는 읽기 전용 값."""

    has_api: set[str]
    needs_secret: set[str]
    uploads: set[str]
    owners: dict[str, list[str]]
    engine_of: dict[str, str]
    archetype_hint_of: dict[str, str]
    exposed: set[str]
    async_targets: set[str]
    sync_calls: list[tuple[str, str, str]]
    any_async: bool


def _collect_signals(design: dict) -> _Signals:
    """1단계 — 신호 모으기. 산출물이 말한 사실만 모은다(결론은 뒤 단계의 몫)."""
    has_api = {a["componentId"] for a in _artifacts(design, "openapi")}
    needs_secret = {
        a["componentId"] for a in _artifacts(design, "openapi")
        if (a["openapi"].get("components") or {}).get("securitySchemes")
    }
    # 파일 업로드 본문 → 객체 스토리지 후보. securitySchemes→비밀 저장소와 같은
    # 계열의 결정론 신호다 — OpenAPI가 말한 사실에서 출발하되 결론은 inferred.
    uploads: set[str] = set()
    for openapi_artifact in _artifacts(design, "openapi"):
        for path_item in (openapi_artifact["openapi"].get("paths") or {}).values():
            if not isinstance(path_item, dict):
                continue
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                content = (operation.get("requestBody") or {}).get("content") or {}
                if any(
                    ct.startswith(("multipart/", "application/octet-stream"))
                    for ct in content
                ):
                    uploads.add(openapi_artifact["componentId"])
    owners: dict[str, list[str]] = {}
    engine_of: dict[str, str] = {}
    archetype_hint_of: dict[str, str] = {}
    for artifact in _artifacts(design, "er"):
        for entity in artifact["entities"]:
            owners.setdefault(entity["ownerComponentId"], []).append(entity["name"])
            if artifact.get("engineHint"):
                engine_of[entity["ownerComponentId"]] = artifact["engineHint"]
            if artifact.get("archetypeHint"):
                archetype_hint_of[entity["ownerComponentId"]] = artifact["archetypeHint"]

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
    return _Signals(
        has_api=has_api, needs_secret=needs_secret, uploads=uploads,
        owners=owners, engine_of=engine_of, archetype_hint_of=archetype_hint_of,
        exposed=exposed, async_targets=async_targets, sync_calls=sync_calls,
        any_async=any_async,
    )


def _add_computes(
    plan: DeploymentPlan, components: dict, s: _Signals,
    provider: str | None, region: str | None,
) -> tuple[dict[str, str], set[str]]:
    """2단계 — 컴퓨트 노드. `(컴포넌트별 방식, 값이 붙는 컴퓨트)`를 돌려준다."""
    #: 컴포넌트별 방식 — 진입점(LB)을 세울지 말지가 여기 달렸다(VM만 NLB 대상).
    kind_of: dict[str, str] = {}
    priced: set[str] = set()
    for cid, component in components.items():
        hint = component.get("deployHint")
        notes: list[Note] = []
        if hint:
            origin = ORIGIN_DESIGNER
            kind = hint["compute"]
            notes.append(Note(
                f"The designer specified {kind}"
                + (f" — {hint['reason']}" if hint.get("reason") else ""),
                ORIGIN_DESIGNER, "deployHint",
            ))
        else:
            origin = ORIGIN_INFERRED
            kind = "vm"
            if cid in s.has_api:
                notes.append(Note("An OpenAPI artifact exists, so we read it as an "
                                  "HTTP service", ORIGIN_INFERRED, "openapi"))
            elif cid in s.async_targets:
                notes.append(Note("It receives async messages, so we read it as a "
                                  "worker", ORIGIN_INFERRED, "sequence"))
            else:
                notes.append(Note("The design carries no signal for deciding the "
                                  "deployment form", ORIGIN_INFERRED, ""))
                plan.unresolved.append(
                    f"{cid}: no OpenAPI and no async reception, so we could not decide "
                    "the deployment form"
                )
            notes.append(Note(
                f"{_VM_ASSUMED} — the design did not specify one"
                " (deployHint can change it)", ORIGIN_INFERRED, "",
            ))
        kind_of[cid] = kind
        if cid in s.exposed:
            notes.append(Note("An actor calls it directly in the sequence — publicly "
                              "exposed", ORIGIN_DESIGN, "sequence"))
            if kind == "kubernetes":
                # k8s의 노출은 클러스터 안(Service/Ingress)에서 일어난다 — 그 층의
                # 대응 축이 없으므로 NLB를 억지로 세우지 않고 사실만 적는다.
                notes.append(Note(
                    "Public exposure is handled in the cluster's Service/Ingress "
                    "layer — this knowledge base has no mapping for that layer, so we "
                    "do not answer how it is laid out",
                    ORIGIN_INFERRED, "",
                ))
            if kind == "vm":
                # 스케일아웃은 스펙이 명시하는 구조다: NLB의 대상이 VM 서브그룹이고
                # (targetGroup.subGroupId), 동적 생성이 subGroupSize를 받는다.
                # **대수는 사이징이라 우리가 정하지 못한다** — 그 정직함이 이 노트의 절반이다.
                notes.append(Note(
                    "This is the horizontal scaling unit — the execution-path spec "
                    "references the NLB target as a VM subgroup "
                    "(targetGroup.subGroupId) and dynamic creation takes the subgroup "
                    "size (subGroupSize). **How many instances you need is not "
                    "something this knowledge base can decide** — settle it with a "
                    "load test or a sizing reference point. Where a value is attached, "
                    "the unit price and the monthly floor are for one instance.",
                    ORIGIN_KB, "graphkb",
                ))

        # **서버리스는 값이 다른 축이다.** 시간당 VM 단가를 붙이면 그냥 틀린 값이라
        # 관리형 서비스로 세우고 값을 붙이지 않는다(호출당 과금 데이터가 0건).
        spec = _COMPUTE_KIND[kind]
        if spec["concept"]:
            types = _svcmap_types(spec["concept"], provider)
            notes.append(Note(
                "Serverless is billed per invocation, so it has no hourly rate — "
                "we attach no value", ORIGIN_KB, "costkb",
            ))
            if axes_note := _managed_axes_note(spec["concept"], provider, region):
                notes.append(axes_note)
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
                    f"{cid}: found no type matching a serverless function"
                    + (f" (provider={provider})" if provider else "")
                )
            continue

        if kind == "kubernetes":
            # **파드가 도는 곳은 노드 그룹이다.** 컴포넌트마다 VM 단가를 붙이면
            # 같은 노드에 여러 파드가 올라가는 구조가 지워지고, 합치면 중복이 된다.
            notes.append(Note(
                "It deploys as pods — the value attaches to the node group, not to "
                "this component",
                ORIGIN_INFERRED, "",
            ))
        plan.nodes.append(PlanNode(
            id=cid, label=component["name"], role="compute",
            origin=origin, notes=tuple(notes),
        ))
        if kind == "vm":
            priced.add(cid)
    return kind_of, priced


def _add_managed_services(
    plan: DeploymentPlan, components: dict, s: _Signals,
    provider: str | None, region: str | None,
) -> None:
    """3단계 — 관리형 서비스 (저장소·큐·비밀·파일)."""

    def add_managed(node_id: str, label: str, concept: str, why: Note,
                    extra: tuple[Note, ...] = (),
                    origin: str = ORIGIN_INFERRED) -> None:
        types = _pick_flavor(_svcmap_types(concept, provider), s.engine_of.get(node_id))
        notes = [why, *extra]
        if not provider:
            notes.append(Note("No provider specified, so we could not narrow it to "
                              "one cloud", ORIGIN_INFERRED, "requirements"))
        if axes_note := _managed_axes_note(concept, provider, region):
            notes.append(axes_note)
        chosen, candidates = "", ()
        if len(types) == 1:
            chosen = types[0]
            notes.append(Note(f"svcmap: app::{concept} → {chosen}", ORIGIN_KB, "svcmap"))
        elif types:
            candidates = tuple(types)
            notes.append(Note(
                f"svcmap: app::{concept} has {len(types)} candidates — we do not "
                "pick one",
                ORIGIN_KB, "svcmap",
            ))
        else:
            plan.unresolved.append(
                f"{node_id}: found no type matching app::{concept}"
                + (f" (provider={provider})" if provider else "")
            )
        plan.nodes.append(PlanNode(
            id=node_id, label=label, role="managed", origin=origin,
            archetype=f"app::{concept}", type_id=chosen, candidates=candidates,
            notes=tuple(notes),
        ))

    for cid, entities in sorted(s.owners.items()):
        engine = s.engine_of.get(cid)
        hint_concept = s.archetype_hint_of.get(cid)
        node_origin = ORIGIN_INFERRED
        extra: tuple[Note, ...] = ()
        if hint_concept:
            # **설계자가 아키타입을 직접 갈랐다** — deployHint와 같은 등급이다.
            # kafka처럼 우리가 몰면 안 되는 모호 엔진의 해소 경로이고, 주장이라
            # origin=designer로 hedge된다. 미결·자문은 달지 않는다(해소됐다).
            concept = hint_concept
            node_origin = ORIGIN_DESIGNER
            extra = (Note(
                f"The designer specified {hint_concept} (archetypeHint)"
                + (f" — the designer settled which axis engineHint '{engine}' is on"
                   if engine else ""),
                ORIGIN_DESIGNER, "archetypeHint",
            ),)
        else:
            concept = _ENGINE_CONCEPT.get((engine or "").lower(), "relationalDatabase")
            if engine and (engine or "").lower() not in _ENGINE_CONCEPT:
                plan.unresolved.append(
                    f"{cid}: could not map engineHint '{engine}' to a concept we know, "
                    "so we assumed a relational database"
                )
                # 분류가 애매한 바로 그 자리에만 산문 자문을 단다 — 지침이지 사실이
                # 아니라서 분류·미결 판정은 그대로 둔다.
                if advisory := _pattern_advisory(engine):
                    extra = (advisory,)
        why = Note(
            f"Owns {len(entities)} entities ({', '.join(entities[:3])}) → needs "
            "persistent storage",
            ORIGIN_INFERRED, "er",
        )
        add_managed(f"{cid}-db", f"{components[cid]['name']} storage", concept, why,
                    extra, origin=node_origin)
        plan.edges.append(PlanEdge(cid, f"{cid}-db", "read/write", ORIGIN_INFERRED))

    if s.any_async:
        add_managed(
            "message-queue", "message queue", "messageQueue",
            Note("The sequence has async messages, so we read it as needing a queue",
                 ORIGIN_INFERRED, "sequence"),
        )
    for cid in sorted(s.needs_secret):
        plan.edges.append(
            PlanEdge(cid, "secret-store", "credential lookup", ORIGIN_INFERRED)
        )
    if s.needs_secret:
        add_managed(
            "secret-store", "secret store", "secretStore",
            Note("OpenAPI has securitySchemes, so we read it as needing credential "
                 "storage", ORIGIN_INFERRED, "openapi"),
        )
    for cid in sorted(s.uploads):
        add_managed(
            f"{cid}-files", f"{components[cid]['name']} file storage", "objectStorage",
            Note("OpenAPI has a file-upload body (multipart/octet-stream), so we read "
                 "it as needing object storage", ORIGIN_INFERRED, "openapi"),
        )
        plan.edges.append(
            PlanEdge(cid, f"{cid}-files", "store/fetch files", ORIGIN_INFERRED)
        )


def _wire_edges(
    plan: DeploymentPlan, components: dict, s: _Signals,
    kind_of: dict[str, str], provider: str | None, region: str | None,
) -> None:
    """4단계 — 통신 선 + 사용자·진입점(LB)."""
    known = {n.id for n in plan.nodes}
    for src, dst, label in s.sync_calls:
        # src == dst 는 한 배포 단위 안의 호출이다(easydep 단일 컴포넌트 어댑터가
        # 내부 비동기를 남긴다) — 배포 선이 아니므로 그리지 않는다. any_async
        # 신호는 이미 위에서 집계돼 큐 노드는 선다.
        if src in known and dst in known and src != dst:
            is_async = dst in s.async_targets and src in components
            plan.edges.append(PlanEdge(
                src, dst, label, ORIGIN_DESIGN,
                async_=bool(is_async and s.any_async),
            ))
    # actor는 컴포넌트가 아니라 사람이다 — 노출된 컴포넌트마다 하나 세운다.
    for cid in sorted(s.exposed):
        if "end-user" not in known:
            plan.nodes.append(PlanNode(
                id="end-user", label="End user", role="actor", origin=ORIGIN_DESIGN,
                notes=(Note("An actor in the sequence", ORIGIN_DESIGN, "sequence"),),
            ))
            known.add("end-user")
        # **공개 노출된 VM 앞에는 진입점(로드밸런서)을 둔다.** 설계가 그린 것은
        # actor→컴포넌트 호출이고 LB 삽입은 우리 권고라 전부 inferred다. 근거는
        # 실행 경로에 실재한다 — core::nlb가 스펙(swagger)에서 VM을 참조한다.
        # 노출 서비스마다 하나다: cb-tumblebug NLB는 VM 그룹 단위라 서로 다른
        # 서비스를 한 대로 합치면 실행 경로가 못 만드는 그림이 된다.
        if kind_of.get(cid) == "vm":
            lb_id = f"{cid}-lb"
            lb_notes = [Note(
                "An actor calls this public service directly, so we put an entry "
                "point (load balancer) in front — this is our recommendation, not "
                "something the design specified",
                ORIGIN_INFERRED, "sequence",
            ), Note(
                "The execution path's (cb-tumblebug) NLB references VMs as its target "
                "(stated in the spec)", ORIGIN_KB, "graphkb",
            )]
            if provider:
                # LB 과금 축 — azure는 원본이 리전 무관(Global)으로 공표한다
                # (실측: 일반 리전 행이 없다). 축이 없으면 "수록 없음"이,
                # 관리형 데이터 미빌드·미수록 프로바이더면 "가격 없음"이 붙는다 —
                # 프로바이더가 없으면 전역 고지("단가 조인 안 함")가 이미 참이다.
                axes_note = _managed_axes_note(
                    "loadBalancer", provider,
                    "Global" if provider == "azure" else region,
                )
                if axes_note is not None:
                    lb_notes.append(axes_note)
                    if provider == "azure":
                        lb_notes.append(Note(
                            "The LB unit price above is a value the source publishes "
                            "as region-independent (Global)",
                            ORIGIN_KB, "costkb",
                        ))
                else:
                    lb_notes.append(Note(
                        "Load balancer prices are not in this dataset, so no value is "
                        "attached", ORIGIN_KB, "costkb",
                    ))
            type_id, hedged = _vendor_of("core::nlb", provider)
            if type_id:
                lb_notes.append(Note(
                    f"core::nlb → {type_id}"
                    + (" (the mapping is a guess (reviewed) — a person matched it by "
                       "reading the cb-spider driver)"
                       if hedged else ""),
                    ORIGIN_KB, "mapping-graph",
                ))
            elif provider:
                plan.unresolved.append(
                    f"{lb_id}: found no type for core::nlb on {provider}"
                )
            plan.nodes.append(PlanNode(
                id=lb_id, label=f"{components[cid]['name']} load balancer",
                role="ingress", origin=ORIGIN_INFERRED, type_id=type_id,
                notes=tuple(lb_notes),
            ))
            plan.edges.append(PlanEdge("end-user", lb_id, "request", ORIGIN_INFERRED))
            plan.edges.append(PlanEdge(lb_id, cid, "forward request", ORIGIN_INFERRED))
        else:
            # k8s(Service/Ingress가 클러스터 안)·서버리스(플랫폼 엔드포인트)는
            # NLB 대상이 아니다 — 설계가 말한 직접 호출을 그대로 그린다.
            plan.edges.append(PlanEdge("end-user", cid, "request", ORIGIN_DESIGN))


def _method_comparison_notes(
    requirements: dict, provider: str | None, region: str | None,
    unhinted: int,
) -> tuple[list[Note], str | None]:
    """컴퓨트 방식 비교 판정 — **결정 대행이 아니라 근거 달린 권고다.**

    권고한 방식의 키도 함께 돌려준다(없으면 None). 계획 본문은 VM 가정 위에
    세워지므로, 권고가 VM이 아니면 **가정을 적은 자리에서** 그 사실을 밝혀야
    한다(`_flag_assumption_against`).

    deployHint 없는 컴포넌트가 있을 때, VM·k8s·서버리스 각각에 대해 우리가
    **잴 수 있는 판정**(비용 하한·버스트 상충·stateless 적합·도구 최소사양·
    대응 존재)을 나란히 놓는다. 권고는 **상충 없는 방식이 정확히 하나일 때만**
    낸다 — 여럿 남으면 하나를 고르지 않고(이 저장소가 막아 온 실패), 판별할
    사실이 없다고 말한다. 판정에 못 들어간 입력(팀 역량·지연 SLO·조직)이
    있다는 사실도 명시한다.
    """
    if not unhinted or not provider:
        return [], None
    from app.deployment.costkb import dataset as cost_dataset
    from app.deployment.sizingkb.dataset import rules_of
    from app.deployment.sizingkb.model import MINIMUM, REQUIRED_COUNT

    from .cost_tools import _perf_note

    users = requirements.get("expectedConcurrentUsers")
    vcpu, mem = (2, 4) if not users or users <= 500 else (4, 8)
    specs = cost_dataset.filter_specs(
        vcpu_min=vcpu, mem_min_gib=mem, provider=provider, region=region,
        sort_by="cost", limit=1,
    )
    spec = specs[0] if specs else None
    steady = requirements.get("trafficPattern") == "steady"
    stateless = requirements.get("stateless")

    conflicts: dict[str, list[str]] = {"vm": [], "kubernetes": [], "serverless": []}
    holds: dict[str, list[str]] = {"vm": [], "kubernetes": [], "serverless": []}

    # --- VM ---
    vm_parts: list[str] = []
    burst = False
    if spec:
        vm_parts.append(
            f"cheapest {spec['specName']} ${spec['hourlyUSD']:.4f}/h (one instance)"
            if spec.get("hourlyUSD")
            else f"cheapest {spec['specName']} (unit price unknown)"
        )
        # perfkb 노트 원문은 데이터셋에서 온다. 영어로 다시 빌드했으므로 한 표기만 본다.
        perf_text = _perf_note(spec).text or ""
        burst = "burst" in perf_text.lower()
        if steady and burst:
            conflicts["vm"].append(
                "sustained load but the cheapest spec is burstable — review with a "
                "fixed-performance spec"
            )
    else:
        holds["vm"].append("found no spec for these conditions, so no value verdict")

    # --- k8s (같은 스펙 경제 — 값은 노드 그룹에 붙는다) ---
    k8s_parts: list[str] = []
    for rule in rules_of(MINIMUM, "k8s-node"):
        k8s_parts.append(
            f"node minimum {rule.metric} {rule.value}{rule.unit or ''}"
            " (required by the tooling)"
        )
    for rule in rules_of(REQUIRED_COUNT, provider):
        if rule.metric == "requiredSubnetCount":
            k8s_parts.append(f"{rule.value} subnets required")
    k8s_parts.append("the value is per node group, not per component")
    if steady and burst:
        conflicts["kubernetes"].append(
            "nodes run on the same spec economy, so the burst conflict is identical"
        )

    # --- 서버리스 ---
    sls_parts: list[str] = ["no hourly rate (billed per invocation and usage)"]
    sls_types = _svcmap_types("serverlessFunction", provider)
    if not sls_types:
        conflicts["serverless"].append(
            f"no matching type is included for {provider}"
        )
    if stateless is False:
        conflicts["serverless"].append(
            "stateless=false — possible conflict with an app that keeps state on the "
            "instance (we inferred)"
        )
    elif stateless is None:
        holds["serverless"].append("stateless unconfirmed — fit verdict held")

    def _fmt(name: str, parts: list[str], key: str) -> str:
        line = f"  {name}: " + (" · ".join(parts) if parts else "no axis to judge on")
        for c in conflicts[key]:
            line += f" · ✗ {c}"
        for h in holds[key]:
            line += f" · ? {h}"
        return line

    body = "\n".join([
        f"Compute method comparison — {unhinted} components with no deployHint "
        "(currently assumed VM):",
        _fmt("VM", vm_parts, "vm"),
        _fmt("k8s", k8s_parts, "kubernetes"),
        _fmt("Serverless", sls_parts, "serverless"),
    ])
    notes = [Note(body, ORIGIN_KB, "costkb·perfkb·sizingkb·svcmap")]

    clean = [m for m in ("vm", "kubernetes", "serverless")
             if not conflicts[m] and not holds[m]]
    conflicted = [m for m in ("vm", "kubernetes", "serverless") if conflicts[m]]
    label = _METHOD_LABEL
    if len(clean) == 1 and len(conflicted) == 2:
        notes.append(Note(
            f"Recommendation: {label[clean[0]]} — on the axes we measured it is the "
            "only one with no conflict. **This is our recommendation, not a verified "
            "fact**, and inputs outside the measured axes (team skills, latency "
            "requirements, operating model) are not in this verdict. Fix it with "
            "deployHint.",
            ORIGIN_INFERRED, "method-comparison",
        ))
        return notes, clean[0]
    else:
        survivors = ", ".join(label[m] for m in ("vm", "kubernetes", "serverless")
                              if not conflicts[m]) or "none"
        notes.append(Note(
            f"No recommendation — methods with no conflict: {survivors}. The measured "
            "axes alone give no ground for picking one, so we do not pick (an "
            "arbitrary pick is the failure this repository guards against). The "
            "deciding inputs (team skills, latency requirements, and the like) are "
            "outside this verdict — specify one with deployHint and we follow it.",
            ORIGIN_INFERRED, "method-comparison",
        ))
    return notes, None


#: 컴퓨트 방식의 사람 이름. 비교 판정과 가정 뒤집기가 **같은 말을 써야** 한다.
_METHOD_LABEL = {"vm": "VM", "kubernetes": "k8s", "serverless": "serverless"}

#: 컴퓨트 방식 가정을 적는 노트의 머리말. 이 문장을 고치면 `_flag_assumption_against`가
#: 붙을 자리를 잃으므로 두 곳을 같이 고쳐야 한다(테스트가 고정한다).
_VM_ASSUMED = "We assumed VM as the compute method"


def _flag_assumption_against(plan: DeploymentPlan, recommended: str) -> None:
    """VM 가정 **바로 그 자리에** 권고가 다르다는 사실을 붙인다.

    **실측(X7, 5회 중 4회)**: 도구는 서버리스를 권고했는데 모델은 "VM으로
    가정했습니다"만 답에 옮기고 권고를 버렸다. 무리가 아니다 — 계획 본문도,
    유일한 구체 가격($0.0468/h)도, PlantUML 다이어그램도 전부 VM 위에 세워져
    있는데 권고는 27줄 뒤 각주 한 줄이었다. **문서에서 압도적인 쪽이 읽힌다.**

    그래서 각주를 늘리지 않고 가정을 적은 자리에서 뒤집는다 — 둘이 한 화면에
    있어야 같이 읽힌다. 계획 자체는 VM으로 남긴다(권고는 권고이지 결정이
    아니고, 임의로 계획을 바꾸는 것이 이 저장소가 막아 온 실패다).
    """
    for i, node in enumerate(plan.nodes):
        amended: list[Note] = []
        for note in node.notes:
            amended.append(note)
            if note.text.startswith(_VM_ASSUMED):
                amended.append(Note(
                    "**This plan is built on the VM assumption, but the comparison "
                    f"verdict below recommends {_METHOD_LABEL[recommended]}** — the "
                    "values and the diagram below are on a VM basis, so do not fix "
                    "them as they stand",
                    ORIGIN_INFERRED, "method-comparison",
                ))
        if len(amended) != len(node.notes):
            plan.nodes[i] = replace(node, notes=tuple(amended))


def _global_notices(
    plan: DeploymentPlan, requirements: dict,
    provider: str | None, region: str | None, exposed: set[str],
) -> None:
    """5단계 — 전역 고지 (레지던시 대조·이그레스·관리형 가격)."""
    # 레지던시 대조 자료 — 판정이 아니다. 리전의 원본 표시 이름을 그대로 싣고,
    # 국가 판정은 하지 않는다(부합 판정문이 "판정 불가"를 명시한다 — verify).
    if requirements.get("dataResidency") and provider and region:
        from app.deployment.envkb.regions import name_of

        display = name_of(region, provider=provider)
        if display:
            plan.notes.append(Note(
                f"Residency requirement ({requirements['dataResidency']}), material "
                f"to compare against — the original display name of {provider} "
                f"{region}: '{display}'. We do not judge the country (a display name "
                "is the source's wording, not a source for a verdict)",
                ORIGIN_KB, "envkb",
            ))
        else:
            plan.notes.append(Note(
                f"Residency requirement ({requirements['dataResidency']}) — the "
                f"display name for {provider} {region} is not in the region dataset, "
                "so we could not include material to compare against",
                ORIGIN_KB, "envkb",
            ))

    # 이그레스 — 노출(트래픽이 밖으로 나가는 신호)이 있을 때만 알린다. 전부
    # 사용량형이라 곱하지 않는다 — 대표로 기본(전 세계) 첫 구간 단가만 보이고
    # 목적지·구간별 축 개수를 함께 밝힌다.
    if provider and region and exposed:
        from app.deployment.costkb import dataset as cost_dataset

        egress_axes = cost_dataset.managed_axes("networkEgress", region)
        if egress_axes:
            # `meter`는 데이터셋 값이다. 영어화로 `월 0~1TB 구간` → `0-1TB/month
            # band`가 되면서 이 리터럴이 조용히 아무것도 안 맞게 됐다 — 대표 단가가
            # 빠진 채 축 개수만 남는다. 실패가 **조용하다**는 것이 위험한 지점이라
            # 두 표기를 다 받는 대신 새 표기 하나로 고정하고 테스트로 묶는다.
            base = next(
                (r for r in egress_axes
                 if r["sku"] == "worldwide" and "0-1TB" in r["meter"]),
                None,
            )
            head = (
                f"default (worldwide) {base['meter']} ${base['unitPriceUSD']}/GB · "
                if base else ""
            )
            plan.notes.append(Note(
                "Internet egress is billed per GB (usage-based — you have to know the "
                f"traffic, and we do not multiply it out). For {region}: {head}"
                f"{len(egress_axes)} axes by path, destination, and tier",
                ORIGIN_KB, "costkb",
            ))

    # 관리형 가격 고지 — azure 수록분이 붙었으면 "없다"는 고지가 거짓이 된다.
    if any(
        note.source == "costkb" and "Billing axes" in note.text
        for node in plan.nodes for note in node.notes
    ):
        plan.notes.append(Note(
            "Managed services carry not a single value but a **list of billing axes** "
            "(included: 6 azure kinds, gcp object storage — only the instance-hour "
            "kind yields an hourly rate). **No total is produced** — adding them up "
            "without knowing the quantities on the capacity and usage axes comes out "
            "lower than reality.",
            ORIGIN_KB, "costkb",
        ))
    else:
        text = (
            "Managed service prices are not in this dataset, so no value is attached. "
            "**No total is produced** — treating what has no value as zero comes out "
            "lower than reality."
        )
        if provider == "aws":
            # 없는 이유가 다르다 — 소스 부재가 아니라 재배포 금지다. 열 수 있는
            # 길(로컬 빌드)을 안내한다(azure-discount의 명령 안내 선례).
            text += (
                " aws managed prices come from a source that forbids redistribution, "
                "so they are not in the repository — build them locally with "
                "`python -m costkb build-aws-managed` and they attach."
            )
        plan.notes.append(Note(text, ORIGIN_KB, "costkb"))


def compose(design: dict) -> DeploymentPlan:
    """설계 JSON → 배포 계획. **계약 검증을 통과한 입력만 들어온다고 가정하지 않는다.**

    단계별 헬퍼로 나뉘어 있다(신호→컴퓨트→관리형→공유 인프라→선·진입점→값→고지) —
    각 단계의 본문과 순서는 427줄짜리 단일 함수 시절과 동일하고, 결정론 출력의
    전후 대조로 확인했다. 노드·선이 붙는 **순서가 곧 출력 순서**라 단계 순서를
    바꾸면 안 된다."""
    problems = validate_design(design)
    if problems:
        plan = DeploymentPlan(name=design.get("name") or "(no name)")
        plan.unresolved = [f"input contract violation: {p}" for p in problems]
        return plan

    requirements = design.get("requirements") or {}
    provider = (requirements.get("provider") or "").strip().lower() or None
    region = (requirements.get("region") or "").strip() or None

    plan = DeploymentPlan(name=design["name"])
    components = {c["id"]: c for c in design["components"]}

    s = _collect_signals(design)
    kind_of, priced = _add_computes(plan, components, s, provider, region)

    for external in design.get("externals") or []:
        plan.nodes.append(PlanNode(
            id=external["id"], label=external["name"], role="external",
            origin=ORIGIN_DESIGN,
            notes=(Note("The design declares it as an external system",
                        ORIGIN_DESIGN, "externals"),),
        ))

    _add_managed_services(plan, components, s, provider, region)
    _add_shared_infra(plan, set(kind_of.values()), provider, requirements, priced)
    _wire_edges(plan, components, s, kind_of, provider, region)

    if provider:
        _attach_values(plan, provider, region, requirements, priced)
    else:
        plan.notes.append(Note(
            "No provider, so we did not join unit prices or regions — we do not pick "
            "one arbitrarily",
            ORIGIN_KB, "requirements",
        ))

    unhinted = sum(1 for c in components.values() if not c.get("deployHint"))
    method_notes, recommended = _method_comparison_notes(
        requirements, provider, region, unhinted
    )
    plan.notes.extend(method_notes)
    if recommended and recommended != "vm":
        # 계획은 VM 가정 위에 세워져 있다 — 권고가 다르면 가정한 자리에서 밝힌다.
        _flag_assumption_against(plan, recommended)
    _global_notices(plan, requirements, provider, region, s.exposed)
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
    from app.deployment.bundlekb.dataset import default_bundle_for

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
            plan.unresolved.append(
                f"{anchor}: found no information on what has to come with it"
            )
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
                    f"The count above ({member.count}) is the value in the "
                    f"'{bundle.name}' template, not the number this app needs",
                    ORIGIN_KB, "bundlekb",
                ))
            if bundle.caveat:
                notes.append(Note(bundle.caveat, ORIGIN_KB, "bundlekb"))
            type_id, hedged = _vendor_of(core_id, provider)
            if type_id:
                notes.append(Note(
                    f"{core_id} → {type_id}"
                    + (" (the mapping is a guess (reviewed) — a person matched it by "
                       "reading the cb-spider driver)"
                       if hedged else ""),
                    ORIGIN_KB, "mapping-graph",
                ))
            elif provider:
                plan.unresolved.append(
                    f"{node_id}: found no type for {core_id} on {provider}"
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
    text = ("You have to give an image ID in the request — the design artifact does "
            "not carry that")
    origin, source = ORIGIN_KB, "bundlekb"
    if provider:
        from app.deployment.envkb.images import describe

        found = describe(provider, requirements.get("region"), "x86_64", limit=1)
        first = next(
            (ln.strip() for ln in found.splitlines()[1:] if ln.strip().startswith("-")),
            "",
        )
        if first:
            text = ("You have to pick an OS image. An example basic image in this "
                    f"region: {first[1:].strip()}")
            source = "basic-images"
    plan.nodes[:] = [
        node if node.id not in priced
        else replace(node, notes=node.notes + (Note(text, origin, source),))
        for node in plan.nodes
    ]


def _node_group_notes() -> list[Note]:
    """노드 그룹의 최소 사양. **cb-tumblebug이 정한 값이지 쿠버네티스가 정한 게 아니다.**"""
    from app.deployment.sizingkb.dataset import rules_of
    from app.deployment.sizingkb.model import MINIMUM

    notes = []
    for rule in rules_of(MINIMUM, "k8s-node"):
        notes.append(Note(
            f"node minimum {rule.metric} {rule.value}{rule.unit or ''} "
            "(a value cb-tumblebug requires, not one Kubernetes sets)",
            ORIGIN_KB, "sizingkb",
        ))
    return notes


def _subnet_notes(provider: str | None, requirements: dict) -> list[Note]:
    """서브넷에 붙는 사이징 사실 — 개수와 용량. 둘 다 sizingkb가 답한다."""
    from app.deployment.sizingkb.dataset import rules_of
    from app.deployment.sizingkb.model import REQUIRED_COUNT

    notes: list[Note] = []
    if provider:
        for rule in rules_of(REQUIRED_COUNT, provider):
            if rule.metric == "requiredSubnetCount":
                # 원본 `unit`이 "서브넷"이라 그대로 붙이면 "2서브넷가"가 된다 — 실측.
                notes.append(Note(
                    f"A cluster on this provider needs {rule.value} subnets",
                    ORIGIN_KB, "sizingkb",
                ))
    if requirements.get("multiZone"):
        # 계약이 받아 놓고 안 읽던 칸이다.
        notes.append(Note(
            "The requirement is multiZone, so the subnets have to be spread across "
            "several availability zones",
            ORIGIN_DESIGN, "requirements",
        ))
    if provider:
        from app.deployment.sizingkb.agent_api import subnet_capacity

        first = subnet_capacity(24, provider).splitlines()[0]
        notes.append(Note(f"For reference — {first}", ORIGIN_KB, "sizingkb"))
    return notes


def _attach_values(plan: DeploymentPlan, provider: str, region: str | None,
                   requirements: dict, priced: set[str]) -> None:
    """값이 붙는 노드에 스펙·단가·성능 소견을 붙인다.

    **어디에 붙이느냐가 핵심이다.** 관리형 서비스는 가격 축이 없어 안 붙고,
    서버리스는 호출당 과금이라 안 붙고, 쿠버네티스 컴포넌트는 파드라 안 붙는다 —
    대신 파드가 도는 **노드 그룹**이 값을 받는다.
    """
    from app.deployment.costkb import dataset as cost_dataset

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
            f"found no spec with at least {vcpu} vCPU and {mem} GiB memory on "
            f"{provider}{f'/{region}' if region else ''}, so no compute value was "
            "attached"
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
                f"Sized at {vcpu}+ vCPU and {mem}+ GiB memory for {users} concurrent "
                "users (an estimate with no knowledge-base backing)",
                ORIGIN_INFERRED, "requirements",
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
    from app.deployment.appkb.plan import ORIGIN_LABEL

    lines = [f"{plan.name} — deployment plan"]
    for role, title in (("actor", "End user"),
                        ("ingress", "Entry point (one per exposed service)"),
                        ("compute", "Deployed directly"),
                        ("managed", "Managed services"),
                        ("shared", "Shared infrastructure (one set per connection)"),
                        ("external", "External systems")):
        nodes = [n for n in plan.nodes if n.role == role]
        if not nodes:
            continue
        lines.append(f"\n[{title}] {len(nodes)}")
        for node in nodes:
            mark = " ⚠" if needs_hedge(node.origin) else ""
            head = f"  - {node.label} ({node.id}){mark}"
            if node.type_id:
                head += f" → {node.type_id}"
            elif node.candidates:
                head += (f" → {len(node.candidates)} candidates: "
                         + ", ".join(node.candidates[:3]))
            lines.append(head)
            for note in node.notes:
                lines.append(f"      · [{ORIGIN_LABEL[note.origin]}] {note.text}")
    if plan.edges:
        lines.append(f"\n[Connections] {len(plan.edges)}")
        for edge in plan.edges:
            arrow = "⇢" if edge.async_ else "→"
            lines.append(
                f"  {edge.from_id} {arrow} {edge.to_id}"
                f"{f' : {edge.label}' if edge.label else ''}"
                f"  [{ORIGIN_LABEL[edge.origin]}]"
            )
    if plan.unresolved:
        lines.append(f"\n[Could not answer] {len(plan.unresolved)}")
        lines.extend(f"  - {item}" for item in plan.unresolved)
    for note in plan.notes:
        lines.append(f"\n※ {note.text}")
    if plan.hedged_count:
        lines.append(
            f"\n⚠ The {plan.hedged_count} items above (marked ⚠) are **what we "
            "inferred from design signals** or what the designer specified, and are "
            "not verified facts."
        )
    return "\n".join(lines)


def deployment_answer(design: dict, diagram: bool = True) -> str:
    """설계 dict → 답변 텍스트 전부(계획·대조·다이어그램·자체 검증).

    도구 껍데기와 분리해 둔 이유: **easydep의 배포 다이어그램 노드가 이 함수를
    그대로 부른다**(재편 계획 P1c). LLM도 agents SDK도 필요 없는 결정론 경로다.
    """
    plan = compose(design)
    if plan.unresolved and not plan.nodes:
        return "The input did not pass the contract:\n" + "\n".join(
            f"  - {item}" for item in plan.unresolved
        )

    text = _render_plan_text(plan)

    # 요구사항 대조 — research.md 목표 1의 "부합 측정". 판정문이라 항상 싣는다:
    # "기준 없음"도 판정이다(침묵이면 부분 답이 완전한 답처럼 읽힌다).
    from app.deployment.costkb.agent_api import HOURS_PER_MONTH

    conformance = verify_against_requirements(
        plan, design.get("requirements"), HOURS_PER_MONTH
    )
    if conformance:
        text += ("\n\n[Requirements comparison]\n"
                 + "\n".join(f"  - {c}" for c in conformance))

    problems = verify_plan(plan)
    if diagram:
        uml = render(plan)
        problems += verify_diagram(plan, uml)
        text += "\n\n```plantuml\n" + uml + "\n```"
    naked = unhedged_claims(plan)
    if naked:
        problems.append(f"[plan] inferred nodes with no evidence line: {naked}")
    if problems:
        # **우리가 만든 그림을 우리가 검사한 결과**다. 숨기면 검사가 무의미해진다.
        text += ("\n\n⚠ Caught by our own verification:\n"
                 + "\n".join(f"  - {p}" for p in problems))
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
    from app.deployment.appkb.contract import validate_request
    from app.deployment.appkb.easydep import design_from_easydep

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
            text += "\n\n[Constraint (RESOURCE_SPEC) check]\n" + "\n".join(
                f"  - {p}" for p in problems
            )
    if skipped:
        text += "\n\n[What the adapter could not read · had to guess]\n" + "\n".join(
            f"  - {s}" for s in skipped
        )
    return text


def deployment_puml_from_easydep(
    name: str,
    *,
    api_spec: dict | None = None,
    class_puml: str = "",
    sequence_puml: str = "",
    erd_puml: str = "",
    resource_spec: dict | None = None,
) -> str:
    """easydep 배포 노드가 **저장할 한 문서** — PlantUML + 근거·판정을 주석으로.

    easydep 산출물 저장은 스테이지당 단일 문서(PUML 문자열)라, 근거 라벨·부합
    판정·어댑터 고지를 PlantUML 줄 주석(`'`)으로 같은 문서에 싣는다(팀 결정
    2026-07-24). 주석은 렌더링에 안 나오지만 산출물과 함께 저장·버전되고,
    문서만 떼어 봐도 어느 줄이 추론인지 남는다.

    `deployment_answer_from_easydep`의 답을 변환해 만든다 — 따로 조립하면 두
    표면이 드리프트한다.
    """
    text = deployment_answer_from_easydep(
        name,
        api_spec=api_spec,
        class_puml=class_puml,
        sequence_puml=sequence_puml,
        erd_puml=erd_puml,
        resource_spec=resource_spec,
        diagram=True,
    )
    match = re.search(r"```plantuml\n(.*?)\n```", text, re.DOTALL)
    if match is None:
        # 계약 실패 등 그림이 없는 답 — 빈 그림 대신 **실패를 그린 문서**를 남긴다.
        body = "\n".join(ln for ln in text.splitlines() if ln.strip())
        return "@startuml\nnote as contract_failure\n" + body + "\nend note\n@enduml\n"
    uml = match.group(1).rstrip()
    evidence = (text[: match.start()] + text[match.end():]).strip()
    comments = "\n".join(f"' {ln}".rstrip() for ln in evidence.splitlines())
    assert uml.endswith("@enduml"), "the output contract of render() has changed"
    return (
        uml[: -len("@enduml")]
        + "' ──── evidence and verdicts below"
          " (generated by agent-sdk · not shown in the rendering)\n"
        + comments
        + "\n@enduml\n"
    )


@function_tool
def design_to_deployment(design_json: str, diagram: bool = True) -> str:
    """Build a **deployment plan** from an app design artifact (JSON) —
    components, managed services, and connections.

    The input is JSON following the `appkb/schema.json` contract (class,
    sequence, ER, and OpenAPI held in one document). Input that violates the
    contract produces **a list of what is wrong**.

    Every line of the answer carries its evidence — what the design artifact
    said / what the designer specified / what the knowledge base answered /
    **what we inferred**. A ⚠ mark means inference, so pass it to the user
    as-is. Managed services have no price axis, so no value is attached and
    **no total is produced either** — pass that notice through as-is too.

    The requirements-comparison section at the end of the answer is the budget,
    scale, and multiZone verdict. **"over, confirmed" and "cannot be asserted
    to fit" are different verdicts** — do not blur them; pass them through
    as-is.

    Args:
        design_json: The design artifact JSON string.
        diagram: If True, emit a PlantUML diagram along with the answer.
    """
    try:
        design = json.loads(design_json)
    except json.JSONDecodeError as exc:
        return f"Could not read the design JSON: {exc}"
    print(f"\n[design query] deployment plan: {design.get('name')!r}")
    return deployment_answer(design, diagram=diagram)


DESIGN_TOOLS = [design_to_deployment]
