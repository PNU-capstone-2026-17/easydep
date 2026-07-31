"""인프라 의도의 두 사영 — 소비자가 둘이고 필요가 다르다.

계획 P3(`archive/infra-intent-plan-2026-07-31.md` §4). 같은 `InfraIntent`에서
나오는 두 뷰이고, **지식을 복사하지 않는다** — 사영이라 원본이 바뀌면 함께 바뀐다.

| 뷰 | 소비자 | 산출물 | 담는 것 |
|---|---|---|---|
| `design_view` | 설계 에이전트 | **배포 다이어그램** | 노드·간선·그룹·라벨·근거·빈칸. 순서는 그림에 없다 |
| `provision_view` | 구현 에이전트 | **manifest + IaC** | 생성/삭제 순서·참조 방향·검사 규칙 |

## 층 경계를 뷰가 말한다

구현 에이전트는 manifest(k8s 오브젝트)와 IaC(클라우드 자원)를 **둘 다** 낸다.
우리 주장은 전부 후자다. 그래서 `provision_view`는 `layer: "cloud"`를 달고,
k8s 층에 대해서는 아무 말도 하지 않는다는 것을 `notForLayer`로 밝힌다 —
밝히지 않으면 생성기가 우리 침묵을 "제약 없음"으로 읽는다.

## 다이어그램의 간선 방향

의존 화살표(A→B = A가 B를 요구)를 그대로 쓴다. 그림에서 흔히 쓰는 "포함"
방향과 반대일 수 있으므로 `edgeSemantics`로 명시한다 — 방향을 말하지 않은
다이어그램은 읽는 사람이 각자 해석한다.
"""

from __future__ import annotations

from dataclasses import asdict

from .infra_intent import InfraIntent

#: 자원 → 다이어그램 그룹. **우리 구성**(가독 목적) — 판정에 영향이 없다.
_GROUP: dict[str, str] = {
    "network": "네트워크", "subnet": "네트워크", "firewall": "네트워크",
    "publicIp": "네트워크", "nic": "네트워크", "loadBalancer": "네트워크",
    "vm": "컴퓨트", "disk": "컴퓨트", "sshKey": "컴퓨트", "image": "컴퓨트",
    "k8sCluster": "컨테이너", "k8sNodeGroup": "컨테이너",
    "k8sService": "컨테이너", "k8sPvc": "컨테이너", "k8sIngress": "컨테이너",
    "vpn": "연결",
}

#: 역할 → 다이어그램 라벨. 사람이 읽는 말이다.
_ROLE_LABEL: dict[str, str] = {
    "anchor": "선택한 것",
    "required": "반드시 필요",
    "attachable": "선택 사항",
}


def design_view(intent: InfraIntent) -> dict:
    """배포 다이어그램이 그릴 수 있는 형태 — 노드·간선·그룹·빈칸·근거.

    순서를 담지 않는다(그림에 시간축이 없다). 대신 **왜 이 노드가 여기 있는지**를
    노드마다 싣는다 — 사용자가 정하지 않았는데 나타난 자원은 근거가 있어야
    그림이 설득력을 갖는다.
    """
    auto = {a.id: a.notice for a in intent.autoFilled}
    nodes = []
    for r in intent.resources:
        nodes.append({
            "id": r.id,
            "group": _GROUP.get(r.id, "기타"),
            "role": r.role,
            "label": _ROLE_LABEL[r.role],
            "because": list(r.because),
            "note": r.detail or "",
            "autoFilledNotice": auto.get(r.id, ""),
        })
    edges = [{"from": s, "to": o, "kind": "requires"}
             for r in intent.resources for s, _, o in
             (b.partition("→") for b in r.because) if o]
    return {
        "schemaVersion": intent.schemaVersion,
        "view": "design",
        "csp": intent.csp,
        "region": intent.region,
        "edgeSemantics": "A→B는 'A가 B를 요구한다' — 포함 관계가 아니다",
        "nodes": nodes,
        "edges": edges,
        "openDecisions": [{"about": d.about, "question": d.question}
                          for d in intent.decisions],
        "constraints": [f"{c.subject}→{c.object}: {c.rule}"
                        for c in intent.constraints],
        "provenance": intent.provenance,
    }


def provision_view(intent: InfraIntent) -> dict:
    """IaC 생성기가 그대로 쓰는 형태 — 순서·참조·검사 규칙.

    근거는 주석 수준으로만 남긴다(기계가 소비하는 뷰다). 대신 **무엇을 만들지
    말아야 하는지**를 분명히 한다: 서버가 채우는 것을 우리가 또 만들면 계획이
    실제와 어긋난다.
    """
    auto = {a.id: a.notice for a in intent.autoFilled}
    create = [{"id": rid,
               "required": any(r.id == rid and r.role in ("required", "anchor")
                               for r in intent.resources),
               "skipIfOmitted": rid in auto,
               "comment": auto.get(rid, "")}
              for rid in intent.createOrder]
    return {
        "schemaVersion": intent.schemaVersion,
        "view": "provision",
        "layer": "cloud",
        "notForLayer": ["kubernetes"],
        "notForLayerNote": (
            "이 뷰는 클라우드 자원만 말한다 — manifest(k8s 오브젝트)에 대해서는 "
            "아무 주장도 하지 않는다. 침묵을 '제약 없음'으로 읽지 말 것"),
        "csp": intent.csp,
        "region": intent.region,
        "createOrder": create,
        "deleteBefore": [list(p) for p in intent.deleteBefore],
        "doNotCreate": [{"id": k, "why": v} for k, v in sorted(auto.items())],
        # 동반 정리(실측) — 합성물은 주체 삭제가 함께 지운다. IaC가 이 자원의
        # 생성·삭제 단계를 내면 안 된다(생성은 이중, 삭제는 이미 없어 실패).
        "cleanupCascades": [
            {"owner": s, "synthesized": o,
             "note": f"{s} 삭제가 {o}를 함께 지웁니다 — 삭제 단계를 내지 마세요"}
            for s, o in intent.cleanupCascades],
        # 기능 결속(실측) — 컨트롤 플레인이 막지 않으므로 검사로는 안 잡힌다.
        # apply는 성공하는데 서비스가 죽는 자리라 **운영 경고**로 낸다.
        "operationalWarnings": [
            {"subject": s, "object": o,
             "warning": f"{o}를 떼어도 {s}는 남지만 기능이 깨집니다 "
                        f"(컨트롤 플레인이 막지 않습니다 — 실측)",
             "evidence": why}
            for s, o, why in intent.functionalDeps],
        "checks": [asdict(c) for c in intent.constraints],
        "blockedBy": [d.about for d in intent.decisions],
        "provenance": intent.provenance,
    }
