"""배포 계획 → **하류(구현 에이전트의 deployment intent)** 전수 대조.

## 하류는 이미 있다

`docs/deployment-file-generation.md`가 사슬을 이미 정의해 뒀다.

    배포 다이어그램 + cloud resource spec
        → 시스템 구현 에이전트가 `easydep-deployment-intent/v1alpha1`을 추론
        → **결정적 renderer**가 Dockerfile·k8s YAML을 만든다 (LLM이 YAML을 쓰지 않는다)
        → `sourceConformance`가 "입력 설계가 보존됐는가"를 검사한다

그래서 "IaC를 어디로 낼까"는 우리가 새로 정할 문제가 아니라 **이미 있는 소비자가 우리
산출물에 무엇을 요구하는가**의 문제다(계획서 결정 D3: 타깃을 고르지 않고 **줄 수 있는
정보**를 먼저 정의한다).

## 이 표가 하는 일

intent 필드마다 판정 하나. **못 주는 것에도 사유가 붙는다** — 침묵하면 "안 준 것"과
"줄 수 없는 것"이 구별되지 않고, 하류는 왜 빈칸인지 영영 모른다.

    have        계획·계약이 이미 답한다
    partial     신호는 있으나 하류가 요구하는 모양이 아니다 (사유에 무엇이 모자란지)
    missing     우리 산출물에 없다. 상류에서 정해야 한다
    undecided   **우리가 일부러 정하지 않는다.** 근거 없는 값을 만드는 자리라서다

`undecided`가 이 표의 핵심이다. 하류는 `replicas.min/max`를 요구하지만 계획은 대수를
`None`으로 두고 `×?`로 그린다 — 근거 없는 사이징을 하지 않기로 한 결정이 그림까지
내려와 있다. 이걸 `missing`으로 적으면 "채우면 되는 빈칸"으로 읽히는데, 그게 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass

HAVE = "have"
PARTIAL = "partial"
MISSING = "missing"
UNDECIDED = "undecided"
STATUSES = (HAVE, PARTIAL, MISSING, UNDECIDED)


@dataclass(frozen=True)
class Mapping:
    """intent 필드 하나에 대한 판정."""

    field: str
    status: str
    why: str
    source: str = ""
    """`have`·`partial`일 때 우리 쪽 사실의 자리."""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status: {self.status!r}")
        if not self.why.strip():
            raise ValueError("사유 없는 판정은 판정이 아니다")
        if self.status in (HAVE, PARTIAL) and not self.source:
            raise ValueError(f"{self.field}: 준다고 했으면 어디서 오는지 적어야 한다")


#: `easydep-deployment-intent/v1alpha1`의 필드 전수 (2026-07-29 기준
#: `docs/deployment-file-generation.md`). 하류 문서가 바뀌면 이 표가 먼저 낡는다 —
#: 그래서 테스트가 "사유 없는 항목"과 "중복"을 막고, 수는 커버리지로 보고한다.
INTENT_FIELDS: tuple[Mapping, ...] = (
    Mapping("namespace", PARTIAL,
            "계획에 이름(`plan.name`)은 있지만 DNS-1123 네임스페이스로 쓰기로 한 규약이 "
            "없다. 이름 규칙 검증은 capacitykb가 이미 할 수 있다",
            source="DeploymentPlan.name"),
    Mapping("workloads[].name", HAVE,
            "계획 노드 id가 그대로 워크로드 이름이 된다. 그림의 별칭도 같은 문자열이라 "
            "`diagramAlias`가 바로 붙는다",
            source="PlanNode.id"),
    Mapping("workloads[].diagramAlias", HAVE,
            "**우리가 되파싱 가능한 그림을 낸 덕에 공짜로 성립한다.** 하류는 alias를 못 "
            "이으면 외부 노출 검증을 통째로 건너뛰고 경고만 남긴다",
            source="diagram.render → parse_back (별칭 = 계획 id)"),
    Mapping("workloads[].kind", PARTIAL,
            "컴퓨트/관리형/서버리스 구분과 아키타입은 있고, `stateless`가 거짓이면 "
            "StatefulSet 쪽이라는 신호도 있다. 그러나 Job·CronJob을 가를 입력(일정·"
            "완료성)은 어디에도 없다",
            source="PlanNode.role · archetype · RESOURCE_SPEC.stateless"),
    Mapping("workloads[].image", MISSING,
            "이미지는 빌드 산출물이라 우리 축이 아니다. envkb가 **기본 이미지**는 알지만 "
            "그건 OS 이미지이지 앱 컨테이너가 아니다",),
    Mapping("workloads[].ports", MISSING,
            "OpenAPI 산출물은 경로를 말하지 포트를 말하지 않는다. 계약에도 칸이 없다",),
    Mapping("workloads[].replicas.min/max", UNDECIDED,
            "**일부러 정하지 않는다.** 근거 없는 대수는 이 저장소가 막아 온 것이고, "
            "그림에도 `×?`로 새겨 둔다. 설계자가 정하면 그 수가 들어온다",
            source="PlanNode.replicas (None = 미정)"),
    Mapping("workloads[].resources.cpu/memory", PARTIAL,
            "**축이 다르다.** 계획은 인스턴스 스펙(t3a.medium)을 말하고 하류는 컨테이너 "
            "request/limit을 말한다. 사용자가 준 워크로드 하한(`minVCpu`·`minMemoryGiB`)은 "
            "컨테이너 축에 그대로 쓸 수 있는 유일한 값이다",
            source="RESOURCE_SPEC.minVCpu · minMemoryGiB (사용자 진술)"),
    Mapping("workloads[].health(readiness/liveness)", MISSING,
            "헬스 경로는 설계 산출물에도 계약에도 없다. OpenAPI에 `/health`가 있으면 "
            "추측할 수 있지만 그건 추측이다",),
    Mapping("capabilities.service", HAVE,
            "무엇이 호출되는지가 엣지로 있다. 들어오는 엣지가 있으면 Service가 필요하다",
            source="PlanEdge (to_id)"),
    Mapping("capabilities.ingress", HAVE,
            "외부 행위자에서 오는 엣지와 진입점(ingress 역할) 노드가 계획에 있다",
            source="PlanNode.role == 'ingress' · actor→component 엣지"),
    Mapping("capabilities.networkPolicy", HAVE,
            "**엣지 목록이 곧 허용 흐름이다.** 하류가 요구하는 것 중 우리가 가장 잘 "
            "답하는 칸이고, 지금 아무도 안 쓰고 있다",
            source="DeploymentPlan.edges"),
    Mapping("capabilities.hpa", UNDECIDED,
            "HPA는 replica 범위를 전제한다. 그 범위를 우리가 정하지 않기로 했으므로 "
            "이 칸도 우리가 정할 수 없다 — 빠진 것이 아니라 상류 결정이 없는 것이다",),
    Mapping("capabilities.pdb", UNDECIDED,
            "PDB도 최소 가용 대수를 전제한다. 같은 이유",),
    Mapping("capabilities.serviceAccount", PARTIAL,
            "**필요 여부는 실측이 답한다** — aws EKS는 역할 없이 안 서고(k8sCluster→"
            "iamRole required), azure AKS는 서버가 identity를 합성하며(안 줘도 선다), "
            "VM은 3사 모두 없이 서지만 aws는 붙였다 떼면 게스트가 자격증명을 잃는다"
            "(vm→iamRole function holds). 다만 **이 앱이 어떤 권한을 원하는가**는 "
            "여전히 우리 축이 아니라 정책 목록은 못 준다",
            source="depkb claims: k8sCluster→iamRole(aws existence required · azure "
                   "서버 합성) · vm→iamRole(aws function holds)"),
    Mapping("configMap", MISSING,
            "설정 항목 목록이 설계 산출물에도 계약에도 없다. 환경별 값은 우리가 아는 "
            "사실이 아니라 운영 결정이다",),
    Mapping("pvc", PARTIAL,
            "영속 저장이 필요한지는 아키타입이 답한다(`relationalDatabase` 등). 다만 "
            "관리형 서비스로 매핑된 것은 PVC가 아니라 벤더 리소스다 — 둘을 가르는 것은 "
            "우리가 이미 하는 일이다",
            source="PlanNode.archetype · type_id"),
    Mapping("externalSecret", PARTIAL,
            "`app::secretStore` 노드가 있으면 시크릿을 쓴다는 사실은 안다. 하류는 "
            "`ClusterSecretStore` 이름과 `remoteKey`를 요구하는데 그건 클러스터 사실이라 "
            "우리 축이 아니다 — 하류 문서도 '전부 명시된 경우에만 만든다'고 적어 뒀다",
            source="PlanNode.archetype == 'app::secretStore'"),
    Mapping("serviceMonitor", MISSING,
            "관측 축이 없다. 메트릭 엔드포인트를 아는 산출물이 없다",),
)


def coverage() -> dict[str, int]:
    """판정별 개수. **커버리지를 수치로 말한다** — 표만 있으면 읽는 사람이 센다."""
    counts = dict.fromkeys(STATUSES, 0)
    for mapping in INTENT_FIELDS:
        counts[mapping.status] += 1
    return counts


def blocking_decisions(plan) -> list[str]:
    """**아래로 내려갈 수 없는 것들** — 매니페스트가 성립하려면 먼저 정해야 하는 결정.

    빈칸으로 채우지 않는다는 규율의 IaC 판이다. `type_id`가 없는 노드를 임의의 타입으로,
    `replicas=None`을 1로 적어 내려보내면 **그 순간 근거 없는 값이 매니페스트가 되고**,
    검증기는 문법만 보므로 통과한다. 조용히 통과하는 것이 가장 나쁜 결과다.
    """
    out: list[str] = []
    for node in plan.nodes:
        if node.role in ("actor", "external"):
            continue
        if not node.type_id and node.candidates:
            out.append(
                f"{node.id}: 타입이 후보 {len(node.candidates)}개로 남아 있다 — "
                "하나를 고르기 전에는 리소스로 못 내려간다"
            )
        if node.role in ("compute", "shared") and node.replicas is None:
            out.append(
                f"{node.id}: 대수가 미정이다(`×?`) — replicas·HPA·PDB가 이 결정을 "
                "전제한다. 우리가 근거 없이 정하지 않는다"
            )
    if any(n.role == "compute" and not n.hourly_usd for n in plan.nodes):
        out.append(
            "값이 안 붙은 컴퓨트가 있다 — 스펙 하한이 없으면 스펙을 고르지 않는다"
            "(minVCpu·minMemoryGiB)"
        )
    return out
