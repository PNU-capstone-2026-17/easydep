# 인프라 계획 API — 설계·구현 에이전트가 부르는 문

> **살아 있는 문서.** 계약이 바뀌면 여기를 고친다.
> 근거는 `depkb/claims.json`(3사 실측 주장 — 개수는 파일이 진실이다), 여정은
> `document/archive/dep-analysis-journey-2026-07-31.md`.

## 이게 무엇인가

지금 사슬(요구사항 → 설계 → 배포 의도 → manifest)에는 **클라우드 자원 층이
없다** — 클러스터·네트워크·디스크가 어디서 오는지 아무도 정하지 않는다. 이 API가
그 자리를 채운다: 배포 의도(k8s 층)를 받아 **어떤 클라우드 자원을 어떤 순서로
만들고, 무엇을 만들지 말아야 하며, 사람이 무엇을 정해야 하는지**를 낸다.

판정은 전부 세 클라우드 컨트롤 플레인에 직접 물어 얻은 것이다(생성 거부·삭제
거부·양성 대조). 스키마 문서를 읽어 추측한 것이 아니다.

## 부르는 법

```python
from app.core.infra_planning import plan_from_deployment_intent

plan = plan_from_deployment_intent(
    deployment_intent,          # easydep-deployment-intent/v1alpha1 (dict)
    csp="aws",                  # RESOURCE_SPEC.provider
    region="ap-northeast-2",    # RESOURCE_SPEC.region
    concrete_plan=None,         # 이미 채운 계획이 있으면 규칙 위반도 함께 본다
)
```

앵커를 이미 아는 경우엔 `plan_for_anchors(["k8sCluster"], csp, region)`.

**이 모듈은 다른 영역을 import하지 않는다**(테스트로 고정). dict를 받고 dict를
주므로, 각 에이전트가 자기 파이프라인에서 호출해 산출물에 실으면 된다.

## 설계 에이전트가 쓰는 것 — `plan.design`

배포 다이어그램을 그리는 데 필요한 것만 담는다. **순서는 없다**(그림에 시간축이
없다).

**PlantUML로 바로 받으려면**:

```python
from app.core.cloudkb.depkb.plantuml import deployment_puml, deployment_puml_set

puml = deployment_puml(plan.intent, title="주문 API — aws")
# CSP별을 한 파일에 나란히:
puml = deployment_puml_set({"aws": a.intent, "azure": b.intent}, title="주문 API")
```

역할은 스테레오타입으로 나른다(`<<선택한 것>>`·`<<필수>>`·`<<선택>>`·`<<자동>>`) —
색만으로 구분하지 않는다. 근거는 `note`, 물어볼 것과 규칙은 `legend`에 실린다.

| 키 | 뜻 |
|---|---|
| `nodes[]` | `id`·`group`(네트워크/컴퓨트/컨테이너/연결)·`role`(anchor/required/attachable)·`label`(사람이 읽는 말)·`because`(왜 이게 여기 있나)·`autoFilledNotice` |
| `edges[]` | `from`→`to`, `kind: requires` |
| `edgeSemantics` | **화살표 방향의 뜻** — "A→B는 A가 B를 요구한다, 포함이 아니다" |
| `openDecisions[]` | 사람이 정해야 하는 것 + **질문 문장** |
| `constraints[]` | 그림에 주석으로 달 규칙(예: "서로 다른 AZ의 서브넷 ≥2") |

## 구현 에이전트가 쓰는 것 — `plan.provision`

| 키 | 뜻 |
|---|---|
| `layer` / `notForLayer` | **`"cloud"` / `["kubernetes"]`** — 우리 주장은 클라우드 자원뿐이다. manifest에 대해서는 아무 말도 하지 않으니 **침묵을 "제약 없음"으로 읽지 말 것** |
| `createOrder[]` | 만드는 순서. `required`·`skipIfOmitted`·`comment` |
| `deleteBefore[]` | `[먼저 지울 것, 그 다음]` — 실측된 삭제 제약만 |
| `doNotCreate[]` | **서버가 알아서 만드는 것.** 우리가 또 만들면 계획이 실제와 어긋난다 |
| `cleanupCascades[]` | **동반 정리(실측).** `owner` 삭제가 `synthesized`를 함께 지운다 — 그 자원의 생성·삭제 단계를 내지 말 것(생성은 이중, 삭제는 이미 없어 실패). `deleteBefore`와 기제가 반대라 섞지 않는다 |
| `checks[]` | 검사 규칙(`kind`·`subject`·`object`·`rule`) |
| `blockedBy[]` | 사람이 정하기 전엔 프로비저닝하면 안 되는 것 |

## 함께 오는 것

| 키 | 왜 있나 |
|---|---|
| `plan.questions` | 물어야 할 것 전부(하류에서 못 읽은 것 + 우리가 대신 정하지 않는 것) |
| `plan.unmeasured` | **재지 않아 말할 수 없는 것.** 예: aws에서 `k8sPvc→disk`는 간선이 없어(전제 부재로 미측정 — 범위 표시) 그 앵커 계획을 내지 않고 여기로 강등한다. Service→LB는 2026-07-31 실측으로 여기서 빠져 `cleanupCascades`가 됐다 |
| `plan.notes` | 우리 축이 아니라 안 쓴 신호와 그 사유(`replicas`·`hpa`·`pdb`·`networkPolicy`) |
| `plan.report` | `concrete_plan`을 준 경우의 위반·미검사·필수 누락 |

## 규율 — 이 API가 하지 않는 것

- **모르면 계획을 내지 않는다.** 판정 없는 간선을 만나면 죽고, 앵커를 못 읽으면
  예외다. 추측한 값으로 계획을 채우지 않는다.
- **대신 정하지 않는다.** 선택(LB 프론트엔드)·조건(네트워크 모드)은 질문으로
  올린다. 근거 없이 고르면 그건 발명이다.
- **침묵하지 않는다.** 서버가 채우는 것은 고지하고, 안 쓴 신호는 사유를 적고,
  못 잰 것은 `unmeasured`로 낸다.
- **대수·스펙·비용은 다루지 않는다.** 이 분석의 축이 아니다.

## 명시적 미해결

- **사설 연결(private endpoint/link)**: 배포 의도(`easydep-deployment-intent/
  v1alpha1`)에 사설 연결을 뜻하는 칸이 없다 — **하류 스키마의 공백**이고 그
  스키마는 우리 소관이 아니라 여기서 고칠 수 없다(P5 데모가 드러낸 것).
  신호가 생기기 전까지 이 API는 사설 연결 계획을 내지 않는다. 신호가 생겨도
  vpn 간선은 azure만 실측이라 aws·gcp는 unmeasured로 강등된다.
- ~~Ingress → 클라우드 LB~~ **실측으로 닫힘(2026-07-31 합성 2라운드)**:
  `ingress` 신호는 `k8sIngress` 앵커다. gcp는 내장 컨트롤러가 **전역** HTTP LB
  성좌를 합성(동반 정리까지 실측 — 직접 만들면 이중 생성), azure·aws **기본
  구성**은 합성 없음(IngressClass 0·Ingress 방치 실측) — 노출 방법(컨트롤러
  애드온 등)은 사용자 결정이고 이 API가 대신 정하지 않는다.
- **RWX PVC(파일 스토리지)**: 3사 전부 기본 구성에서 완주 불가 실측 —
  azure는 CSI가 스토리지 계정 합성을 **시도**하나 구독 정책 교란으로 실패
  (합성 기제는 확인), gcp는 드라이버가 명시 거부("multi writer with mount
  access type"), aws는 전제 부재(EFS CSI·노드 0). RWX 신호의 계획은 내지
  않는다 — fileSystem 어휘 편입과 애드온 변형 실측이 선행이다.

## 같은 배포 의도, 세 가지 답

`kind: Deployment` + `ingress: true` 하나로:

| | 만들 것 | 서버가 채움 | 물어야 할 것 |
|---|---|---|---|
| **aws** | network → subnet → k8sCluster → loadBalancer | firewall | — |
| **azure** | k8sNodeGroup → k8sCluster → loadBalancer | subnet | LB 프론트엔드 선택 |
| **gcp** | k8sCluster → loadBalancer | k8sNodeGroup·network·subnet | LB 서브넷 조건 |

이 차이가 이 API의 존재 이유다 — 중립 그래프 하나로는 셋 중 어느 것도 맞지 않는다.
