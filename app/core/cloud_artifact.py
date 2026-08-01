"""배포 계획 → 구현 단계가 먹는 **`cloud` 산출물** — 사슬의 마지막 이음매.

## 왜 있나

구현 단계는 `cloud` 산출물을 **기다리고 있었다.**
`implementation/engine/orchestrator.py`의 `OPTIONAL_DESIGN_INPUTS`에 그 이름이
있고, `deployment_renderer.infer_intent(name, cloud, deployment)`가 그것에서
워크로드를 읽어 k8s 매니페스트와 Dockerfile을 낸다.

**그런데 아무도 그것을 내지 않았다**(실측 2026-08-01: 실물 표본
`samples/lecture-platform/design/`에 `api_spec·class_diagram·erd·sequence`만
있다). 그래서 렌더러는 빈 dict로 돌아 워크로드가 이름 하나짜리 기본값으로
떨어진다 — 요구사항에서 YAML까지의 사슬이 **이 한 자리에서 끊겨 있었다.**

여기서 그 자리를 채운다. 재료는 이미 다 있다: 설계 신호에서 나온 배포 계획
(`nim_agent.design_tools.compose`)과 거기 붙은 3사 실측(`plan.measured`).

## 형식은 우리 것이 아니다

`cloud`의 모양은 **하류가 정한다**(`deployment_renderer`가 읽는 키들). ARM
자원 타입 문자열을 쓰고 `Microsoft.ContainerService/managedClusters`를 찾는다.
우리가 고를 수 있는 형식이 아니라 **맞춰 주는 것**이고, 그래서 이 모듈은
투영(projection)이지 우리 모델이 아니다.

**한계를 그대로 적는다**: 하류가 azure ARM 타입만 알아본다. aws·gcp 계획도
같은 자리에 넣을 수 있어야 하지만 그건 하류 스키마의 변경이라 우리 몫이 아니다
(`unsupported`에 적어 낸다 — 침묵하지 않는다).

## 모르는 칸은 **안 채운다**

렌더러가 읽는 칸 중 우리가 아는 것은 절반이다. 나머지(프로브 경로·컨테이너
포트·메트릭 경로 …)는 **우리 축이 아니다.** 렌더러에 기본값이 있으므로 비워
두면 그쪽 기본값이 서고, 우리가 채우면 **그 값이 우리 주장이 된다** — 예컨대
`readinessPath: "/healthz"`를 우리가 쓰면 "이 앱에 그 엔드포인트가 있다"는
말이 되는데 우리는 그걸 모른다.

무엇을 왜 안 채웠는지는 `_omitted`에 남긴다. 빈 칸이 "해당 없음"으로 읽히는
것을 막는 것이 이 저장소의 규율이다.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.cloudkb.appkb.plan import DeploymentPlan

#: 우리 어휘 → 하류가 읽는 ARM 타입. **하류가 정한 이름이라 여기 옮겨 적는다.**
#: `deployment_renderer.infer_intent`가 이 문자열로 자원을 찾는다.
_ARM_TYPE: dict[str, str] = {
    "k8sCluster": "Microsoft.ContainerService/managedClusters",
    "containerRegistry": "Microsoft.ContainerRegistry/registries",
}

#: 채우지 않는 칸과 그 이유. **우리 축이 아닌 것들이다.**
_OMITTED: dict[str, str] = {
    "networking.containerPort": "앱이 어느 포트를 듣는지는 설계·구현이 정한다 — "
                                "렌더러 기본값(8000)이 선다",
    "workloads[].probes": "헬스 엔드포인트 경로는 앱의 것이다. 우리가 쓰면 "
                          "'이 앱에 /healthz가 있다'는 주장이 된다",
    "workloads[].monitoring.metricsPath": "같은 이유 — 앱의 것이다",
    "networking.ingressClassName": "인그레스 컨트롤러 선택은 운영 결정이고, "
                                   "우리 실측은 '컨트롤러를 깔지는 사람이 정한다'"
                                   "까지만 말한다(k8sIngress 라운드)",
}


def _spec_of(node) -> dict[str, Any]:
    """계획이 고른 스펙에서 컨테이너 자원 요구를 만든다 — **고른 것이 있을 때만.**

    스펙 하한이 없으면 계획도 스펙을 안 고른다(`no spec floor was stated`).
    그때는 빈 dict를 돌려주고, 렌더러가 자기 기본값을 쓴다.
    """
    host = node.host or ""
    if " · " not in host:
        return {}
    # `"Kubernetes node · Standard_D2s_v3"` — 뒤가 스펙 이름이다.
    return {"_specName": host.split(" · ", 1)[1]}


def build(plan: DeploymentPlan, design: dict, *, name: str = "") -> dict:
    """계획 + 설계 계약 → `cloud` 산출물.

    Args:
        plan: `design_tools.compose`가 낸 계획(실측이 붙어 있으면 함께 실린다).
        design: 설계 계약 — 노출 신호를 읽는 데만 쓴다.
        name: 산출물 이름. 비우면 계획의 이름.
    """
    measured = plan.measured
    csp = measured.csp if measured else ""
    computes = [n for n in plan.nodes if n.role == "compute"]
    ingress = [n for n in plan.nodes if n.role == "ingress"]
    managed = [n for n in plan.nodes if n.role == "managed"]

    exposed = {e.to_id for e in plan.edges
               if (plan.node(e.from_id) or e).__dict__.get("role") == "actor"}

    workloads = []
    for node in computes:
        item: dict[str, Any] = {
            "name": node.id,
            # 다이어그램 별칭 — 하류가 노출 검증에 쓴다. 계획의 노드 id가 곧
            # 컴포넌트 id라 그대로 준다(없으면 하류가 "검증할 수 없다"고 낸다).
            "diagramAlias": node.id,
        }
        # 노출: **계획이 아는 만큼만.** LB가 이 노드로 들어오면 공개다.
        if ingress or node.id in exposed:
            item["exposure"] = "public"
        # 영속 저장소: 계획에 관리형 저장소가 있으면 그 사실을 나른다.
        if any(m.archetype in ("app::relationalDatabase", "app::nosqlDatabase")
               for m in managed):
            item["persistentVolume"] = False  # 저장은 관리형이 받는다 — PVC 아님
        spec = _spec_of(node)
        if spec:
            item["_chosenSpec"] = spec["_specName"]
        workloads.append(item)

    cluster_nodes = [n for n in computes
                     if (n.host or "").startswith("Kubernetes node")]
    resources: list[dict[str, Any]] = []
    unsupported: list[str] = []

    if cluster_nodes:
        networking: dict[str, Any] = {}
        # 인그레스 프로토콜은 설계가 말한다 — OpenAPI `servers`가 https면 HTTPS.
        for artifact in design.get("artifacts", []):
            if artifact.get("kind") != "openapi":
                continue
            urls = [s.get("url", "") for s in
                    (artifact.get("openapi") or {}).get("servers", [])]
            if any(u.startswith("https://") for u in urls):
                networking["ingressProtocol"] = "HTTPS"
        # 레지스트리 — 사용자가 준 이름이 있을 때만. 없으면 하류가 자리표시자를
        # 내고, 그 자리표시자가 "이 값을 못 받았다"는 사실을 그대로 말한다.
        registry = (design.get("requirements") or {}).get("containerRegistry")
        if registry:
            resources.append({"type": _ARM_TYPE["containerRegistry"],
                              "name": registry})
        resources.append({
            "type": _ARM_TYPE["k8sCluster"],
            "name": name or plan.name,
            "networking": networking,
            "workloads": workloads,
        })
    else:
        # **하류가 관리형 k8s만 읽는다.** VM 계획은 여기 담을 자리가 없다 —
        # 우리 한계가 아니라 하류 스키마의 범위이고, 그렇게 적는다.
        unsupported.append(
            "이 계획은 관리형 쿠버네티스 위에 서지 않는다"
            f"({', '.join(sorted({(n.host or '?').split(' · ')[0] for n in computes})) or '컴퓨트 없음'})"
            " — 하류 렌더러는 Microsoft.ContainerService/managedClusters만 "
            "읽으므로 이 산출물로는 매니페스트가 나오지 않는다")
    if csp and csp != "azure":
        unsupported.append(
            f"계획은 {csp}인데 하류 렌더러는 azure ARM 타입만 알아본다 — "
            "3사 실측이 여기서 막힌다(하류 스키마의 변경이 필요하다)")

    doc: dict[str, Any] = {
        "schemaVersion": "easydep-cloud-resource/v1alpha1",
        "provider": csp,
        "resources": resources,
        # **우리가 안 채운 칸과 그 이유.** 빈 칸이 "해당 없음"으로 읽히면 안 된다.
        "_omitted": _OMITTED,
        "_unsupported": unsupported,
        "_provenance": ("app/core/cloud_artifact.build — 설계 신호에서 나온 배포 "
                        "계획의 투영이다. 형식은 하류(deployment_renderer)가 정했다"),
    }
    # **배포 후 검증** — 기능 결속은 컨트롤 플레인이 막지 않아 apply 전 검사로는
    # 영영 안 잡힌다(`deploy_checks` 모듈 문서). 계획이 놓는 자원에 걸리는
    # 것만 낸다: 그린 것 + 실측이 "만들어야 한다"고 한 것.
    if csp:
        from app.core.deploy_checks import build as build_checks
        from app.core.plan_crosscheck import read_plan

        mapped, _unmapped, _weak, _roles = read_plan(plan, csp)
        drawn = set(mapped.values())
        if measured is not None:
            drawn |= set(measured.create_order)
        doc["_deployChecks"] = build_checks(csp, drawn)

    if measured is not None:
        # **실측을 함께 나른다.** 하류가 지금 읽지 않더라도 산출물에는 있어야
        # 한다 — 순서·대기·무방비 경고는 사람이 apply할 때 필요한 것이고,
        # 없으면 그 지식이 사슬의 이 지점에서 사라진다.
        doc["_measured"] = {
            "csp": measured.csp,
            "anchors": list(measured.anchors),
            "createOrder": list(measured.create_order),
            "deleteBefore": [list(p) for p in measured.delete_before],
            "waitFor": [{"id": i, "op": o, "doneSignal": s, "confidence": c}
                        for i, o, s, c in measured.wait_for],
            "doNotCreate": [{"id": i, "why": w, "kind": k}
                            for i, w, k in measured.do_not_create],
            "operationalWarnings": [{"subject": s, "object": o, "breaks": b}
                                    for s, o, b in measured.operational_warnings],
            "unmeasured": list(measured.unmeasured),
        }
    return doc


def write(plan: DeploymentPlan, design: dict, path, *, name: str = "") -> dict:
    """산출물을 파일로 남긴다. 돌려주는 것은 그 내용이다."""
    doc = build(plan, design, name=name)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc
