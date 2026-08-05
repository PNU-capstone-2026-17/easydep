"""예제 셋 — 무엇이 다른지 **한눈에 보이는** 클라우드 네이티브 앱.

예제를 고른 기준: *"스키마 문서를 읽어 추론하는 것으로는 답이 갈리지 않고,
컨트롤 플레인에 물어봐야만 갈리는 자리"*. 그래야 이 분석의 값이 드러난다.

세 예제가 서로 다른 자리를 조명한다.

| | 조명하는 것 | 근거가 된 실측 |
|---|---|---|
| 1. 멀티 CSP 이식 | **양상 반전·서버 대체** — 같은 요구가 3사에서 다른 계획이 된다 | vm→nic(aws만 선택) · k8sCluster→subnet(azure 선택·aws 필수) · 서버 합성 8종 |
| 2. 상태 있는 워크로드 | **쌍 호환 제약** — 만들어지긴 하는데 조합이 틀리면 거부된다 | gcp 존 일치 · aws 다른 AZ ≥2 |
| 3. 사설 연결 | **숨은 조건의 연쇄** — 이름·SKU·zone이 모두 맞아야 선다 | azure GatewaySubnet 이름 · AZ SKU · zone PIP |

## 비교군에 대해 정직하게

우리는 LLM CoT·MetaGPT 같은 비교군을 **측정하지 않았다.** 아래 `hard_for`는
"비교군이 틀린다"는 주장이 아니라 **우리 실측이 드러낸 함정의 위치**다 — 스키마
문서와 통상적 예제만으로는 그 자리에서 답이 갈리지 않는다는 뜻이다. 실제 비교는
같은 요구를 각 시스템에 주고 산출물을 이 저장소의 검사기(`depkb.check`)와
컨트롤 플레인에 대는 별도 실험이어야 한다.

관련 선행 연구는 있다: Nekrasov 외(ACM TOSEM, doi:10.1145/3817608)가 LLM의 IaC
생성에서 **의도 불일치 자원 37.0% · 필요 자원 누락 30.4%**를 보고했고, 기술적
정확성은 올라가도 의도 정렬은 정체된다("Correctness-Congruence Gap")고 결론했다.
우리 예제는 그 갭이 어디에 있는지를 자원 의존 축에서 짚는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Example:
    id: str
    title: str
    #: 사용자가 말할 법한 요구 — 자연어.
    requirement: str
    #: 하류(구현 에이전트)가 낼 배포 의도. 우리 입력이다.
    deployment_intent: dict
    #: 이 예제가 조명하는 것.
    highlights: tuple[str, ...]
    #: 스키마 문서만으로는 답이 갈리지 않는 자리 — 우리 실측이 드러낸 함정.
    hard_for: tuple[str, ...] = field(default_factory=tuple)
    #: 함께 검사할 구체 계획(일부러 틀린 것도 있다 — 검사기 시연용).
    concrete_plans: dict = field(default_factory=dict)
    #: 검사를 다른 앵커로 해야 하는 경우. **왜 필요한가**: 배포 의도에서 읽은
    #: 앵커와 제약이 걸린 자원이 어긋날 수 있다(k8s PVC ↔ 클라우드 디스크처럼
    #: 층 경계를 넘는 자리). 그때 무엇으로 검사했는지 밝히고 쓴다.
    check_anchors: tuple[str, ...] = ()
    check_anchors_why: str = ""
    #: 배포 의도에서 읽을 수 없어 **사람이 직접 준** 앵커. 왜 필요한지 함께.
    #: 하류 스키마에 자리가 없는 요구(사설 연결 등)가 여기 온다 — 번역이
    #: 지어내지 않고 비워 둔 자리를 사람이 채운 것이다.
    given_anchors: tuple[str, ...] = ()
    given_anchors_why: str = ""


def _workload(name: str, kind: str, **caps) -> dict:
    return {"name": name, "kind": kind, "capabilities": caps}


EXAMPLES: tuple[Example, ...] = (
    Example(
        id="portable-api",
        title="① 어디로든 갈 수 있는 주문 API",
        requirement=(
            "주문 API를 컨테이너로 배포한다. 외부에서 HTTP로 들어온다. "
            "아직 클라우드를 정하지 않았고, 세 곳 중 어디로 가도 되게 하고 싶다."),
        deployment_intent={
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "orders",
            "workloads": [
                _workload("orders-api", "Deployment",
                          service=True, ingress=True, hpa=True, networkPolicy=True),
            ],
        },
        highlights=(
            "같은 요구가 3사에서 **구조가 다른** 계획이 된다 — 노드 수부터 다르다",
            "aws는 사용자에게 network·subnet을 요구하고, gcp는 셋을 서버가 채우며, "
            "azure는 노드풀을 생성 시 필수로 든다",
            "무엇을 **만들지 말아야 하는지**(doNotCreate)가 CSP마다 다르다",
        ),
        hard_for=(
            "'클라우드 네이티브 = VPC+서브넷+클러스터'라는 통상 패턴을 3사에 "
            "동형으로 적용하면 azure·gcp에서 서버가 만들 자원을 중복 생성한다",
            "k8s Service가 클라우드 LB를 자동 생성하는 경로와 IaC의 LB가 겹치는 "
            "이중 생성 — 우리는 재지 않았다고 표시한다(unmeasured)",
        ),
    ),
    Example(
        id="stateful-store",
        title="② 영속 스토리지를 쓰는 주문 저장소",
        requirement=(
            "주문 데이터를 저장하는 워크로드가 필요하다. 재시작해도 데이터가 "
            "남아야 하고, 장애에 견디게 여러 영역에 나눠 두고 싶다."),
        deployment_intent={
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "orders",
            "workloads": [
                _workload("orders-store", "StatefulSet", service=True, pvc=True),
            ],
        },
        highlights=(
            "vm→disk의 필연이 CSP마다 뒤집힌다 — gcp는 필수, azure·aws는 선택",
            "**만들어지긴 하는데 조합이 틀리면 거부되는** 자리를 검사기가 잡는다",
        ),
        hard_for=(
            "gcp에서 디스크와 인스턴스의 존이 다르면 생성이 거부된다 — 스키마에는 "
            "'zone' 필드가 각각 있을 뿐 둘이 같아야 한다는 말이 없다(실측으로 확인)",
            "aws에서 다중 영역을 요구하는 자원은 서브넷 개수가 아니라 **AZ 분산**을 "
            "본다(같은 AZ 둘도 거부)",
            "**이 예제가 우리 공백도 드러낸다**: k8s PVC가 어떤 경로로 클라우드 "
            "디스크가 되는지(StorageClass 경유) 우리는 재지 않았다. 그래서 "
            "k8sCluster→disk 간선이 없고, 존 제약을 그 층에 걸 근거가 없다 — "
            "아래 검사는 우리가 실제로 잰 vm→disk 층에서 한 것이다",
        ),
        concrete_plans={
            "gcp": {  # 일부러 틀린 계획 — 존 불일치
                "resources": [
                    {"id": "vm", "instances": [{"name": "store-0", "zone": "asia-northeast3-a"}]},
                    {"id": "disk", "instances": [{"name": "data-0", "zone": "asia-northeast3-b"}]},
                    {"id": "nic", "instances": [{"name": "nic-0"}]},
                ]},
        },
        check_anchors=("vm",),
        check_anchors_why=(
            "존 제약은 우리가 vm→disk에서 쟀다. 배포 의도의 앵커는 k8sCluster지만 "
            "그 층의 디스크 경로는 미측정이라, 검사는 측정한 층에서 보여 준다"),
    ),
    Example(
        id="private-link",
        title="③ 사내망과 사설로 연결되는 내부 서비스",
        requirement=(
            "사내 네트워크에서만 접근하는 내부 서비스를 올린다. 온프레미스와 "
            "VPN으로 연결해야 한다."),
        deployment_intent={
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "internal",
            "workloads": [
                _workload("internal-svc", "Deployment", service=True),
            ],
        },
        highlights=(
            "VPN 게이트웨이는 **이름이 정확해야** 선다 — azure는 서브넷 이름이 "
            "`GatewaySubnet`이어야 한다(우리가 실측으로 확인했고 검사기가 잡는다)",
            "조건이 연쇄한다: 리전이 SKU를 제한하고, 그 SKU가 공인 IP의 zone 구성을 "
            "요구한다 — 셋이 다 맞아야 게이트웨이가 선다",
        ),
        hard_for=(
            "서브넷 이름 규칙은 문서 한 줄에 묻혀 있고, 틀리면 '참조를 찾을 수 없다'는 "
            "간접적인 오류로 나온다(우리 실측: InvalidResourceReference)",
            "리전별 SKU 제한과 zone 요구는 배포해 보기 전에는 드러나지 않는다 — "
            "우리는 두 번 거부당하고서야 조합을 찾았고, 그 경로를 기록해 뒀다",
            "**이 예제가 사슬의 공백도 드러낸다**: 사용자는 'VPN으로 연결'이라고 "
            "말했는데 배포 의도(k8s 층)에는 그 신호를 담을 자리가 없다. 번역은 "
            "지어내지 않고 비워 두고, 앵커는 사람이 준 것으로 처리한다",
        ),
        given_anchors=("vpn",),
        given_anchors_why=(
            "배포 의도에 사설 연결 신호가 없다 — 하류 스키마의 공백이라 "
            "번역이 읽을 수 없고, 사용자 요구에서 직접 받았다"),
        concrete_plans={
            "azure": {  # 일부러 틀린 계획 — 서브넷 이름
                "resources": [
                    {"id": "network", "instances": [{"name": "internal-vnet"}]},
                    {"id": "subnet", "instances": [{"name": "vpn-subnet"}]},
                    {"id": "publicIp", "instances": [{"name": "vpn-pip"}]},
                    {"id": "vpn", "instances": [{"name": "vpn-gw"}]},
                ]},
        },
        check_anchors=("vpn",),
        check_anchors_why="이름 조건은 vpn→subnet에 걸려 있다",
    ),
)


def by_id(example_id: str) -> Example:
    for e in EXAMPLES:
        if e.id == example_id:
            return e
    raise KeyError(f"모르는 예제다: {example_id}. 아는 것: "
                   f"{[e.id for e in EXAMPLES]}")
