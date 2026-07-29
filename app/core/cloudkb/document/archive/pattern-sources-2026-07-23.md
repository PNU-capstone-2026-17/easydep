# 패턴 소스 조사 — 축을 이을 수 있나 (2026-07-23)

> **이력이다. 참조하지 않는다.**
>
> 현재 진실은 [`docs/cloud-native-extension.md`](../../../../docs/cloud-native-extension.md). 이 문서는 작성 시점의
> 스냅샷이고 전제가 바뀐 자리가 있다. **여기 적힌 결정·계획을 근거로 새 작업을
> 시작하지 말 것.** 안의 **실측치는 유효하다** — 다시 재지 말고 인용한다.

주어진 표(클라우드 네이티브 애플리케이션 패턴 6종)를 이 저장소의 판정 기준으로
걸렀습니다. **추측하지 않고 HTTP로 확인**했습니다 — 이 저장소는 "200이 온다고 살아
있는 게 아니다"를 여러 번 겪었고, 블로그가 추천하는 저장소가 404인 적도 있습니다.

> 판정 기준(7): 버전 고정 · 재배포 허가 · 무인증 · 기계 판독 · 조인 키 ·
> 기보유와 비중복 · **답할 질문이 있는가**

---

## 먼저 잰 것 — 표의 여섯 칸 중 우리가 닿는 것은 **둘**

| 표의 구분 | 우리가 가진 것 | 판정 |
|---|---|---|
| CSP 공식 모듈 & 패턴 | avm(207) · kcc(296) · aws-pattern(52) | 닿음 |
| 마이크로서비스 패턴 | — | 안 닿음 |
| 엔터프라이즈 통합(EIP) | — | 안 닿음 |
| 앱 개발 표준(12/15-Factor) | — | 안 닿음 |
| 컨테이너 패턴(K8s) | container-presets(28) · k8s 최소사양(2) | 거의 안 닿음 |
| 멀티 클라우드 IaC | tpaws·tpg·tpcsp 제약 · mapping-graph(82) | 닿음 |

**우리 여덟 축이 전부 인프라 계층**이라는 것이 이 표를 대 보고 드러납니다.
`research.md`가 말하는 것은 "클라우드 네이티브 **애플리케이션**"인데, 표의 네 칸이
애플리케이션 계층이고 우리는 거기에 사실상 아무것도 없습니다.

---

## 안 닿는 네 칸 중 **셋은 소스 문제가 아니라 범주 차이**

microservices.io · EIP · 12-Factor는 **산문**입니다. 기계 판독 형태가 없습니다.

```
microservices-patterns/microservices.io   GitHub 404 (저장소 자체가 없다)
heroku/12factor                           MIT · 태그 없음 · HTML 원칙문
EIP                                       책. 저장소 없음
```

이건 "좋은 소스를 못 찾았다"가 아닙니다. **Saga·CQRS·Outbox는 리소스가 아니라 설계
결정**이고, 우리 조인 키(타입 id·스펙명·리전)에 붙을 것이 없습니다. 억지로 담으려면
"Saga를 쓰면 메시지 브로커가 필요하다" 같은 **우리가 지어낸 매핑**을 만들어야 하는데,
그건 이 저장소가 가장 경계하는 종류입니다.

> 이 넷을 담고 싶다면 데이터가 아니라 **지시문·체크리스트**로 담는 것이 정직합니다.
> 그건 지식베이스가 아니라 프롬프트의 일이고, 그렇게 부르는 편이 낫습니다.

---

## 나머지 하나(컨테이너)만 데이터다 — 그리고 **그게 축을 잇는 고리**다

질문이 "여러 축을 연결지을 수 있을까"였는데, 답은 **컨테이너 계층에서 이미 반쯤
이어져 있다**입니다.

```
컨테이너 requests.cpu / memory      k8s core/v1 ResourceRequirements (스키마 있음)
   ↓
노드 하나에 몇 개가 들어가나          ← ★ 여기가 빈다
   ↓
필요한 노드 수
   ↓
core::k8sNodeGroup --references via specId--> core::spec     ← **이미 있다**(조사 1)
   ↓
costkb 단가 (+ Azure 예약·스팟, IBM 성능 신호)
```

뒷부분은 이번 라운드에 이미 붙였습니다 — `concepts_with_spec()`이 `core::k8sNodeGroup`을
**자동으로** 집어냈고(손코딩했으면 빠졌을 것), `resource_guideline`이 실제로 노드 그룹에
값을 붙입니다.

**빠진 고리는 하나뿐입니다 — 노드에서 실제로 쓸 수 있는 양.** vCPU·메모리 전량이
아니라 kubelet·시스템이 떼어 간 나머지입니다. **서브넷 예약 IP와 정확히 같은
모양**입니다(256이 아니라 251이었던 그것).

---

## 그 고리를 메울 수 있나 — 절반만

`awslabs/amazon-eks-ami`를 실측했습니다.

| | |
|---|---|
| 라이선스 | **MIT-0** — 가장 관대. 이 저장소가 `cfn-lint`로 이미 쓰는 것과 같다 |
| 핀 | 태그 `v20260714` · 2026-07-22 갱신 |
| 무인증 | ✓ (저장소 파일) |

**예약 공식은 코드에 있습니다.**

```go
// nodeadm/internal/kubelet/config.go
func getMemoryMebibytesToReserve(maxPods int32) int32 {
    return 11*maxPods + 255
}
```

**그런데 `maxPods` 자체를 못 얻습니다.** 예전에 있던 정적 표(`eni-max-pods.txt`)가
사라졌고, 지금은 **EC2 API(`DescribeInstanceTypes`)로 ENI 수를 받아 CEL로 계산**합니다.

```
nodeadm/internal/kubelet/eni_max_pods.go
   defaultENIsVar = "default_enis"      ← EC2 API에서 온다
   ipsPerENIVar   = "ips_per_eni"       ← EC2 API에서 온다
   CalcMaxPods(instanceInfo, customExpression)
```

**판정 기준 3(무인증) 실패**입니다. 공식은 담을 수 있고 인스턴스별 입력값은 못 받습니다.

> 담는다면 정직한 형태는 *"maxPods를 주면 예약 메모리를 계산해 준다"*이지
> *"m5.large의 allocatable은 얼마다"*가 아닙니다. 후자를 답하려면 ENI 수를
> 어딘가에서 짐작해야 하고, 그건 `freqency`를 2000으로 담는 것과 같은 실패입니다.

---

## 기각 — Crossplane (기계 판독 실패)

표의 "멀티 클라우드 IaC"에서 가장 기대했던 것입니다. 노린 것은 우리 `mapping-graph`
(**82엣지 전부 짐작·단일 출처 `cb-spider-driver`**)의 독립 대조였습니다.

```
upbound/platform-ref-aws @ v2.0.0    composition.yaml  491 bytes
  kind: Composition
  pipeline:
  - functionRef: { name: upbound-platform-ref-awscluster }   ← 리소스 목록이 없다
```

**v2에서 구성이 함수 패키지 안으로 들어갔습니다.** YAML에 벤더 리소스 kind가
`Composition`·`Cluster` 둘뿐이라 뽑을 것이 없습니다. aws·azure·gcp 셋 다 같습니다.
v1 계열 경로는 404였고, 설령 있어도 **버려진 버전을 핀 박는 셈**입니다.

---

## 부분적으로 유효 — Cluster API

| 클라우드 | 저장소 | 라이선스 | 최신 태그 | 갱신 |
|---|---|---|---|---|
| aws | cluster-api-provider-aws | Apache-2.0 | v2.12.1 | 2026-07-23 |
| azure | cluster-api-provider-azure | Apache-2.0 | v1.26.0 | 2026-07-22 |
| gcp | cluster-api-provider-gcp | Apache-2.0 | v1.12.0 | 2026-07-20 |
| openstack | cluster-api-provider-openstack | Apache-2.0 | v0.15.0-alpha.0 | 2026-07-20 |
| oracle | cluster-api-provider-oci | Apache-2.0 | v0.24.1 | 2026-07-10 |
| **ibm** | IBM/cluster-api-provider-ibmcloud | — | — | **404** |
| **alibaba** | AliyunContainerService/… | — | — | **404** |
| **tencent** | tencentcloud/cluster-api-provider-tencent | Apache-2.0 | v0.5 | **2022-10-24 (멈춤)** |

**"엣지 0인 7곳"을 메우는 데는 약합니다** — 7곳 중 openstack·oracle 둘만 덮고, 그 둘도
클러스터 주변 리소스에 한정됩니다. 다만 CAPI는 `Cluster` 하나를 여러 벤더가 구현한다고
**선언**하므로, `core::k8sCluster` 대응의 독립 대조로는 쓸 수 있습니다.

---

## 유효 — Kubernetes OpenAPI (앱 계층의 유일한 기계 판독 소스)

```
apps/v1   스키마 157개 (834 KB)   Deployment · DaemonSet · StatefulSet …
core/v1   스키마 239개            ResourceRequirements(claims/limits/requests) 포함
Apache-2.0 · 태그 v1.31.0 · 무인증
```

우리 것과 **안 겹칩니다** — 지금 k8s 지식은 tumblebug의 `core::k8sCluster`·
`core::k8sNodeGroup` 둘과 최소 사양 2건이 전부입니다.

답할 질문도 명확합니다 — *"Deployment에 뭘 넣어야 하나"*, *"requests와 limits는 뭐가
다른가"*, *"HPA를 붙이려면 무엇이 필요한가"*. 지금은 하나도 못 답합니다.

---

## 결론 — 무엇을 하면 되나

| 순위 | 무엇 | 왜 | 판정 |
|---|---|---|---|
| 1 | **k8s OpenAPI → 앱 계층 축** | 표의 네 칸 중 유일하게 데이터인 것. 우리 것과 안 겹침 | 채택 가능 |
| 2 | **EKS 예약 공식** | 축을 잇는 빠진 고리. MIT-0 | **절반만** — 공식만, 입력값은 자격증명 필요 |
| 3 | Cluster API | `core::k8sCluster` 대응의 독립 대조 | 부분 |
| — | Crossplane | 구성이 함수 안으로 들어감 | **기각(기계 판독)** |
| — | microservices.io · EIP · 12-Factor | **산문이다.** 조인 키가 없다 | **기각(범주)** |

### 하지 말아야 할 것

표를 보고 *"패턴 지식베이스"*를 만들고 싶어지는데, **넷은 데이터가 아닙니다.**
담으려면 우리가 매핑을 지어내야 하고, 그러면 이 저장소가 세 라운드에 걸쳐 막아 온
것(짐작을 사실처럼 말하기)을 정면으로 어기게 됩니다.

정직한 처리는 둘로 갈리는 것입니다.

- **데이터인 것**(k8s 스키마·EKS 공식·CAPI) → 지식베이스로
- **원칙인 것**(12-Factor·Saga·EIP) → 지시문·체크리스트로. 그리고 **그렇게 부르기**
