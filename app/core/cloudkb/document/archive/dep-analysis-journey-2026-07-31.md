# 클라우드 리소스 의존성 분석 — 여정과 실험 전수 (2026-07-30 ~ 07-31)

> **불변 기록.** 현재 상태는 코드·테스트·산출물이 진실이다(`depkb/` 전체,
> `tests/test_depkb*.py`). 시각화 `depkb/dependency-graph.html`은 claims에서
> 재생성한다(`python -m app.core.cloudkb.depkb.render_graph`).
>
> **2026-07-31 재작성.** 첫 판은 첫 사이클(41주장)까지만 담았다. 이후 실험이
> 8라운드 더 돌아 58주장이 됐고, 이 판은 **실험 전수**를 중심으로 다시 썼다.

## 1. 질문과 정의

출발(사용자): *"리소스 의존성이란 무엇이고 어떻게 분석하는가 — 논문 근거 수준으로."*

- **의존성 = 검증 가능한 주장.** "B에 개입하면 A가 달라진다"의 반사실 정식화
  (Parnas uses 관계의 인프라 인스턴스). 참조 유무는 증거의 한 종류이지 정의가 아니다.
- **질문 유형**: 존재(B 없이 A가 만들어지나) · 생명주기(B를 지우면 어떻게 되나) ·
  기능(공백 — 대응 실험을 세울 수 없어 분류에 넣지 않음). 관계 타입화의 선례는
  TOSCA·k8s `ownerReferences`.
- **주장은 CSP로 색인된다** — 가설이었고, 실측으로 다섯 번 확인됐다(§5).
- **오라클 서열**: 컨트롤 플레인(apply) > preflight > 원문 스키마 > 우리 가공물
  (오라클 금지). 서열도 실측으로 정당화됐다 — 아래층이 침묵한 것을 위층이 잡는
  사례가 층마다 있다.
- **판정 어휘**: required / optional / holds / unknown. **거부는 증거, 통과·침묵은
  증거가 아니다**(테스트가 강제). 이 규율이 오판 3건을 막았다.

## 2. 실험 전수 — 23라운드 · 262스텝

전부 잔여물 0으로 종료했고, 매 라운드 끝에 세 클라우드 전수 점검을 돌린다
(사용자 지시, 2026-07-31). 총비용은 세 클라우드 합쳐 수천 원 이내.

| 라운드 | 스텝 | 무엇을 쟀나 |
|---|---:|---|
| `azure-preflight-2026-07-30` | 13 | ARM validate/what-if — 자원 무생성. preflight 경계 2 발견 |
| `azure-apply-2026-07-30` | 15 | 존재·허상 5 + 무료 사슬 생명주기 3 + 역순 정리 |
| `azure-apply2-2026-07-30` | 11 | VNet-무서브넷 · LB 선언 술어 분해 · PIP 생명주기 |
| `azure-apply3-2026-07-30` | 12 | 실제 VM — vm→disk 존재·생명주기, OS 디스크 합성·잔존 |
| `azure-apply4-2026-07-31` | 2 | Basic PIP SKU 축 — 신규 구독 한도 0(축 소멸 실측) |
| `azure-k8s-vpn-2026-07-31` | 9 | AKS 노드풀 생략(az rest) · VNG 이름 조건 거부 |
| `azure-aks2-2026-07-31` | 9 | AKS 실생성 — 서비스 vnet 합성 · 노드풀 CRUD(2차 재측정) |
| `azure-aks3-2026-07-31` | 11 | 사용자 서브넷 AKS — k8s 생명주기 |
| `azure-vpn2-2026-07-31` | 13 | VNG 실생성 — 이름 조건 **대우** · 생명주기 · 조건 2 발견 |
| `aws-apply-2026-07-30` | 18 | DryRun 5 + 무료 사슬 생명주기 3 + 정리 |
| `aws-apply2-2026-07-31` | 22 | 서버 대체 2(기본 VPC·default SG) · LB 술어 · internal 보정 |
| `aws-paircompat-2026-07-31` | 3 | 아키텍처 쌍 호환(arm64 AMI × x86 타입) + 대조군 |
| `aws-eks-2026-07-31` | 5 | EKS 거부 — 역할·서브넷·허상, 검사 순서 관측 |
| `aws-eks2-2026-07-31` | 11 | 실역할·실서브넷으로 카디널리티 격리 |
| `aws-eks3-2026-07-31` | 19 | EKS 실생성 — 양성 대조 · k8s 생명주기 · SG 합성 · IAM 관측 |
| `gcp-apply-2026-07-31` | 22 | REST 직접 — 존재·허상·생명주기·부트 디스크 잔존 |
| `gcp-apply2-2026-07-31` | 15 | NIC 모드 조건부(custom/auto) · EXTERNAL LB |
| `gcp-apply3-2026-07-31` | 12 | INTERNAL FR의 subnetwork 필수 |
| `gcp-apply4-2026-07-31` | 12 | INTERNAL FR의 network 생략 → 서버 역산 |
| `gcp-paircompat-2026-07-31` | 6 | 존 쌍 호환 + 대조군 |
| `gcp-gke-2026-07-31` | 3 | GKE 거부 — 허상 network/subnetwork |
| `gcp-gke2-2026-07-31` | 8 | GKE 실생성 — default 대체 3종 · 노드풀 CRUD |
| `gcp-gke3-2026-07-31` | 11 | 전용 네트워크 GKE — k8s 생명주기 |

**실험 설계의 규율 넷** (전부 실패에서 얻었다):

1. **CLI를 믿지 않는다** — gcloud·az CLI는 생략 필드에 기본값을 주입해 반사실
   실험을 오염시킨다. gcp는 REST 직접, AKS 생략은 `az rest`.
2. **한 번에 한 축만 흔든다** — 교란 3건(SKU 불가·IGW 부재·허상이 카디널리티를
   가림)이 전부 축 혼입이었다.
3. **거부만으로는 부족하다 — 대우를 확인한다.** 양성 대조가 없으면 "무엇을 해도
   안 된다"와 구별되지 않는다(EKS·VPN에서 실제로 필요했다).
4. **측정 가능한 조건을 먼저 만든다** — gcp 생명주기는 전용 네트워크가, azure는
   사용자 서브넷이 있어야 잴 수 있었다(공용 자원·서비스 합성물은 대상이 아니다).

## 3. 산출물 지도

| 층 | 실물 |
|---|---|
| 결과 | `depkb/claims.json` — **58주장**(존재 39·생명주기 19 / required 14·optional 25·holds 19), 술어 22 |
| 증거 | `depkb/experiments/` 23라운드 262스텝 — 재실행 스크립트 + 원자료(오류 원문 발췌) |
| 소비 | `depkb/closure.py` + `app/core/dependency.py` — 폐포·생성 순서·삭제 제약·사람 결정 |
| 원문 | `depkb/cache/azure/`(커밋) · `.cache/cloudkb/`의 CFN·gcp 디스커버리·spider(핀만 커밋) |
| CB 연구 | `depkb/neutralization_map.json` — 기제 판정 42셀 + 호출 색인 111 |
| 시각화 | `depkb/dependency-graph.html` — 3패널 동일 배치, 간선만 다름 |

어휘 13종이 주장에 등장한다: network·subnet·firewall·nic·publicIp·loadBalancer·
vm·disk·sshKey·k8sCluster·k8sNodeGroup·vpn(+ LB 선언 술어의 합성 대상).

## 4. 조건부 의존의 네 차원 (+ 술어 부류 8)

조건이 **어디에** 걸리느냐로 갈린다. 전부 실측 표본이 있다.

| 차원 | 표본 |
|---|---|
| 자기 속성 | gcp LB 스킴(INTERNAL/EXTERNAL) · aws ALB/NLB 카디널리티 |
| 대상 상태 | gcp 네트워크 모드(custom 필수 / auto 서버 대체) |
| 쌍 호환 | aws 아키텍처(AMI×타입) · gcp 존(디스크×인스턴스) · **azure AZ 게이트웨이×zone PIP** |
| CSP | 양상 반전 다섯(§5) |

소비 분류(`closure.PREDICATE_CLASSES`, 우리 구성): `server-default`·
`server-implicit`(→auto) · `disjunctive`(→choice) · 모드/스킴 조건부(→conditional) ·
카디널리티·`쌍 호환`·`이름 조건`·`배치 조건`·`수명 조건`(→detail). **분류 없는
술어가 들어오면 죽는다** — 이번 세션에서 실제로 세 번 죽었다.

## 5. 발견

1. **3사 공통핵**: `subnet→network` 필수 · **생명주기 제약 19건 전부**가 3사
   동형(코드만 다르다: `DependencyViolation`/`InUse…`/`RESOURCE_IN_USE`).
   IaaS뿐 아니라 CNA 층(k8s)에서도 같다.
2. **양상 반전 5**: `vm→disk`(gcp만 필수) · `vm→nic`(aws만 선택) ·
   `k8sCluster→subnet`(azure 선택·aws 필수) · `k8sCluster→k8sNodeGroup`(azure 필수·
   gcp 선택) · `k8sCluster→network`(gcp 대체). **중립 필수 플래그 하나로는 이
   지식을 표현할 수 없다**는 논거의 기둥.
3. **서버 합성·대체 8종**: aws 기본 VPC·default SG·ENI·AMI 루트 볼륨·클러스터 SG ·
   gcp default 네트워크/서브넷·default-pool · azure 노드 RG의 vnet. **CB 드라이버가
   IaaS 층에서 하던 합성을 관리형 서비스가 CNA 층에서 한다.**
4. **존재와 생명주기의 독립**: `nic→firewall`·`nic→publicIp`·`vm→disk` — 생성엔
   선택인데 붙어 있으면 삭제 금지. 한 필드에 눌렀으면 표현 불가능했다.
5. **잔존의 쌍**: azure OS 디스크·gcp 부트 디스크가 VM 삭제 후 남는다 — CB
   드라이버가 디스크를 직접 지우던 이유의 실측 검증.
6. **sshKey 3사 완결**: aws 선택(동적) · azure 자원 있되 무참조 · gcp 자원 부재 —
   CB의 "필수"는 도구의 요구.
7. **검증 도구의 비균일**: preflight 깊이가 RP별(azure)·API별(aws DryRun)로 다르고
   gcp엔 상당물이 없다.
8. **참조 무결성이 자원 종류마다 다르다**: EKS 클러스터가 ACTIVE인데 IAM 역할의
   정책 분리가 **성공한다**. 네트워크 자원은 삭제 보호가 걸리는데 IAM은 안 걸린다 —
   기능 의존의 경계 사례라 어느 판정에도 싣지 않고 기록만 했다.
9. **쌍 호환은 사라지지 않고 옮겨간다**: azure Basic PIP 축은 신규 구독에서
   소멸했는데(한도 0), 그 자리에 AZ 게이트웨이↔zone PIP 축이 있었다.
10. **중립화 기제 5+1**: 드라이버 합성·절단·기제 치환·값 인라인·서버측 암묵
    (+교차 주입). **절단(azure SG `[0]`)은 tumblebug까지 무보정** — 중립화가
    정보를 잃는 실증.

## 6. cloud-barista의 자리

기준 후보 → (사용자 결정) 비실행·강등 → **연구 대상**. 셋: 중립화 사례 연구
(기제 지도 42셀) · 평가 대상(claims로 채점) · 어휘 참조와 방증 코퍼스.
*"벤더 중립은 공짜가 아니라 변환이고, 그 변환이 어디서 정보를 잃는지 측정했다."*

## 7. 근거 관리 — 우리 구성과 약점

임의 설정은 전부 표시 + 기계 제약 + 위협 등재로 관리한다: 어휘 선정(Z1) ·
형태→질문 대응표(T1) · 이름/쌍 휴리스틱(Z3) · 기제 이름 · 실험→판정 배정표
(빌드가 실측 대조) · 술어 소비 분류 · 실험 보조 선택. 분류 불가 입력에는 죽는다.

**약점 5 (논문 위협 절 원고)**: ① aws `nic→subnet` required는 클라이언트 층 거부
(서버 미도달) ② holds는 "그 상황에서 거부됐다"이지 "항상"이 아님(강제 삭제 미측정)
③ 단일 리전 · 대부분 단일 실행(AKS vnet 합성만 2회 재현) ④ 기능 의존 공백
⑤ 하네스 결함 3건이 측정을 가렸다가 잡혔다(발췌 파싱 2회 · SKU 중복 상수 1회) —
잡히지 않은 것이 없다고 말할 수는 없다.

## 8. 재현

    python -m app.core.cloudkb.depkb.fetch_azure 2026-07-30   # 원문(핀 검증)
    python -m app.core.cloudkb.depkb.extract_azure            # 후보(사영)
    python -m app.core.cloudkb.depkb.extract_vendors
    python -m app.core.cloudkb.depkb.extract_spider
    # 실험: experiments/*/run.py <인자> — 계정 필요, 각 스크립트 머리에 비용·국면 명시
    python -m app.core.cloudkb.depkb.build_claims             # 통합(실측 대조)
    python -m app.core.cloudkb.depkb.render_graph             # 시각화
    pytest app/core/cloudkb/tests/test_depkb*.py              # 불변식

## 9. 남은 갈래

- **상류 배선** — 요구사항·설계 에이전트가 `app/core/dependency` 문을 실제로
  부르게 한다(진실 문서 §10-2 접점). 분석이 시스템 가치가 되는 지점.
- **기능 의존 첫 실험** — 데이터 플레인 층. 표본 둘이 이미 있다: 백엔드 없는 LB
  (생성은 되는데 서빙하나) · IAM 정책 뗀 클러스터(§5-8).
- **어휘 밖 대기열** — image · internetGateway · iamRole. 셋 다 실험 중에 의존
  대상으로 등장했으나 어휘에 없어 기록만 했다.
- **논문 정리** — 방법 + `nic→subnet` 4층 완주 worked example + §4·§5 표.
