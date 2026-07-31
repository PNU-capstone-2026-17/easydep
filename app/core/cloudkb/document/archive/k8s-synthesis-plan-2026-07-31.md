# k8s 층 합성 실험 계획 — Service→LB · PVC→디스크 (2026-07-31, 실행 전 기록)

## 왜 이것인가

`archive/infra-intent-plan-2026-07-31.md`가 **명시적 미해결**로 남긴 층 경계(I2):
k8s Service(type=LoadBalancer)가 클라우드 LB를 자동 생성하면 우리 IaC가 LB를 또
만들 때 **이중 생성**이 된다. 같은 부류로 P5 데모가 드러낸 공백: **k8s PVC →
클라우드 디스크 경로 미측정**. 둘 다 "k8s 컨트롤 플레인이 클라우드 자원을
합성한다"는 같은 기제의 후보이고, 기존 실측(azure AKS의 vnet 합성 · gcp GKE의
default-pool 합성 · aws EKS의 SG 합성)이 이 기제가 k8s 관리형 서비스에 실재함을
이미 보였다 — 이번 라운드는 그 기제가 **클러스터 생성 시점이 아니라 k8s 오브젝트
생성 시점에도** 작동하는지를 잰다.

## 셀 정의 (간선 × CSP × 질문)

새 간선 둘. 주체가 클라우드 자원이 아니라 **k8s 오브젝트**인 첫 간선이다
(k8sCluster는 클라우드 컨트롤 플레인에 CRUD가 있지만 Service·PVC는 k8s API의
것이다 — 어휘 표시는 claims 반영 때 정한다, 아래 "기록 방식").

| 간선 | 질문 | 관측 |
| --- | --- | --- |
| k8sService → loadBalancer | 합성(존재) | Service 생성 후 클라우드 층에 LB 실물이 생기는가, 어느 스코프에, 어떤 성좌로 |
| k8sService → loadBalancer | 정리(생명주기) | Service 삭제 후 그 실물이 사라지는가 |
| k8sPvc → disk | 합성(존재) | PVC(+Pod) 후 클라우드 디스크 실물이 생기는가 |
| k8sPvc → disk | 정리(생명주기) | PVC 삭제 후 디스크가 사라지는가 (기본 reclaimPolicy에서) |

덤 관측(각 CSP): 합성물이 놓이는 스코프(우리 RG인가 노드 RG인가) ·
기본 StorageClass의 volumeBindingMode·reclaimPolicy 실물 · LB 성좌 구성.

**이번 라운드에서 재지 않는 것(명시)**: 클러스터 삭제 시 남은 Service/PVC의
합성물이 잔존하는가(잔여 자원 위험이 커서 오브젝트 선삭제 경로만 잰다) ·
reclaimPolicy=Retain 변형 · INTERNAL LB 변형.

## 오라클과 순서

- **진실은 클라우드 컨트롤 플레인 열거다** (`az resource list` · gcp REST ·
  `aws elb/ec2 describe-*`). kubectl이 보여주는 상태(`status.loadBalancer.ingress`,
  PV 바인딩)는 k8s 층의 주장일 뿐 — 힌트로 쓰되 실물 판정은 클라우드 API로만.
- 순서(클러스터 하나에서): 생성 → PVC 단독(트리거 없이 디스크가 생기는지 먼저) →
  Pod로 트리거 → 디스크 실물 → Service LB → 실물 → Service 삭제 → 소멸 관측 →
  Pod·PVC 삭제 → 소멸 관측 → 클러스터 삭제 → **잔여 0 확인**.
- 소멸 관측은 시한부 폴링. 시한 내 미소멸이면 "잔존"이 아니라 **미판정**으로
  적는다(비동기 정리가 시한보다 느릴 수 있다).

## 함정 (사전 인지 — 실측 전 지식임을 표시)

1. **volumeBindingMode=WaitForFirstConsumer**(기본일 것으로 예상 — 실측 대상):
   PVC만으론 디스크가 안 생길 수 있다. Pod 없이 "합성 없음"으로 판정하면 오판.
   그래서 PVC 단독 관측을 셀에 넣고 Pod 트리거를 뒤에 둔다.
2. **LB 프로비저닝 지연**(수 분): pending을 실패로 읽지 않는다.
3. **GKE kubectl 인증**: `gke-gcloud-auth-plugin` 미설치. 설치 대신 정적 토큰
   kubeconfig(엔드포인트·CA는 REST에서, 토큰은 `gcloud auth print-access-token`,
   1h 유효)로 우회 — 기존 "CLI 기본값 주입 배제" 결정과 정합.
4. **EKS 전제 사슬**: PVC 셀은 노드그룹(Pod 스케줄) + EBS CSI 애드온 + IAM
   정책이 전제다 — IAM은 어휘 밖 대기열(iamRole)과 겹친다. Service→CLB가 노드
   없이 생성되는지는 미지 — aws는 Service 셀만 시도하고 PVC 셀은 미측정으로
   남길 수 있다(azure·gcp 결과 후 결정).
5. **kubeconfig는 자격증명이다** — 실험 디렉터리에 두되 gitignore에 추가,
   절대 커밋하지 않는다.

## 위협

- **T-단일실행**: 각 셀 1회 실측 — 합성/정리의 존재 증명으로는 충분하나
  시간·구성 민감성은 못 잰다(기존 라운드와 같은 지위).
- **T-기본값 의존**: 기본 StorageClass·기본 LB 어노테이션 경로만 잰다.
  판정에 "기본 구성에서"를 명시한다.
- **T-버전**: kubectl v1.32(Docker Desktop) ↔ 서버 버전 skew는 기록만 한다.
  관리형 기본 버전은 CSP가 정하므로 결과에 버전을 함께 적는다.

## 비용·시간 (예상)

azure: AKS 1노드 B2s_v2 ~1h 내 · LB/PIP 분 단위 — 수백 원.
gcp: GKE zonal e2-small 1노드 · forwardingRule 분 단위 — 무료 크레딧 내.
aws: EKS 컨트롤 플레인 $0.10/h × ~40분(Service 셀만일 때, 노드그룹 없이).

리전은 기존 라운드 계승: koreacentral · asia-northeast3(-a) · ap-northeast-2.
라운드 끝에 세 클라우드 전수 점검(잔여 0)은 규율이다.
