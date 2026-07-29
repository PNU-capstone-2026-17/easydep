# 관리형 서비스 가격 조사 (⑥-B, 2026-07-24)

> **이력이다. 참조하지 않는다.**
>
> 현재 진실은 [`docs/cloud-native-extension.md`](../../../../docs/cloud-native-extension.md). 이 문서는 작성 시점의
> 스냅샷이고 전제가 바뀐 자리가 있다. **여기 적힌 결정·계획을 근거로 새 작업을
> 시작하지 말 것.** 안의 **실측치는 유효하다** — 다시 재지 말고 인용한다.

배포 계획의 managed 노드가 전부 "값 없음"인 구멍을 메울 수 있는지 — 재편 계획의
"실측 전 착수 금지" 게이트를 통과하기 위한 조사입니다. **결론: 조건부 GO** —
단, "시간당 단가를 붙인다"는 문제 설정 자체가 틀렸다는 것이 실측 결과입니다.

## 실측 — Azure Retail API, koreacentral, Consumption, 6개 서비스

아키타입 9종 중 Azure 대응이 있는 6종을 조사했습니다. 전부 **한 페이지에 다
들어옵니다**(리전당 13~135건) — 폭은 문제가 아닙니다.

| 아키타입 | serviceName | 건수 | 과금 축 (실측) |
|---|---|---|---|
| keyValueCache | Redis Cache | 89 | **전부 1 Hour** — C0/P1/B0… 인스턴스 시간. VM과 같은 모양 |
| relationalDatabase | Azure Database for PostgreSQL | 135 | vCore-시간(115) + 스토리지 GB/월(15) + 백업 — **단가가 vCore 수에 비례** |
| nosqlDatabase | Azure Cosmos DB | 109 | 100 RU/s 시간 + GB/월 — **사용량 축** |
| messageQueue | Service Bus | 18 | 1M 오퍼레이션 + 시간 + GB — 사용량 축 |
| secretStore | Key Vault | 16 | 10K 오퍼레이션 — 사용량 축 |
| serverlessFunction | Functions | 13 | GB-초 + 실행 횟수 — 사용량 축 (**"서버리스는 호출당 과금이라 단가 없음" 고지가 원본으로 확인됨**) |

### 발견 1 — "관리형 서비스 가격"은 세 종류다

1. **인스턴스-시간형** (Redis 전부, PostgreSQL의 compute 부분): VM과 같은 모양.
   시간당 단가를 붙일 수 있고, 월 환산도 같은 기준(730h)이 성립한다.
2. **용량-비례형** (PostgreSQL vCore, Cosmos RU/s): 단가는 있는데 **곱할 수량을
   우리가 모른다** — vCore 몇 개, RU/s 얼마는 사이징 결과다. "1 vCore당 $X/h"
   같은 **비율 줄**로만 낼 수 있다.
3. **사용량형** (Service Bus, Key Vault, Functions): 요청·오퍼레이션·GB-초.
   트래픽을 알아야 비용이 나온다. 시간당 단가는 **존재하지 않는다** — 여기에
   숫자 하나를 붙이면 그게 바로 "모르는 것을 채우는" 실패다.

따라서 산출물은 "단가 한 칸"이 아니라 **과금 축 목록**이어야 합니다:
아키타입마다 (제품, 미터, 단위, 단가) 줄들 + "이 축은 사용량을 알아야 한다"는
구분. 배포 답변의 managed 노드에는 *"값 없음"* 대신 *"과금 축 N개 — 인스턴스
시간형 $X/h · 사용량형(단위당 $Y)"*이 붙게 됩니다. 합계 금지 원칙은 그대로이고,
오히려 이 데이터가 왜 합계가 불가능한지를 원본으로 보여줍니다.

### 발견 2 — serviceName은 아키타입 경계를 지키지 않는다

- `Azure Database for PostgreSQL` 안에 **`Azure Cosmos DB for PostgreSQL`**
  (Citus 분산)이 섞여 있고, 반대로 Cosmos 쪽에도 PostgreSQL 이름이 나온다.
- `Redis Cache` 안에 구형(Azure Redis Cache)과 신형(Azure Managed Redis)이
  같이 있다.
- VM 가격에서 배운 그 교훈 그대로 — **판별자는 serviceName이 아니라
  productName**이고, (serviceName, productName 접두) 큐레이션 표가 필요하다.
  표는 svcmap과 같은 등급(짐작·검수됨)으로 붙는다.

### 발견 3 — 조인 키는 두 홉 다 손 검수다

Retail API에는 ARM 타입 칸이 없다. `app::아키타입 → serviceName/productName`
대응은 손으로 맞추고 검수한다 — svcmap이 이미 그 방식이고, 소스 핀 원칙과도
맞는다(빌드 때 받고, 재배포 문구가 없으므로 azure-discount와 같은 NOTICE 처리).

## 착수 설계 (다음 세션)

- costkb에 `azure-managed-pricing` 산출물 추가 (빌드 시 fetch, 커밋 여부는
  azure-discount 전례: not-stated → 명시 조건으로 커밋 가능).
- 큐레이션 표: 아키타입 6종 × (serviceName, productName 판별 접두, 축 분류
  instance-hour/capacity-rate/usage). **표가 스키마와 1:1임을 테스트로 고정**
  (소비자 대응표와 같은 방식).
- 도구: 새 도구를 늘리지 않고 `design_to_deployment`의 managed 노드 노트와
  `resource_guideline`에 과금 축 줄을 붙인다 (도구 수 39 유지).
- 검증: 리전 39곳 전수에서 (a) 페이지당 전부 수렴 확인 (b) 아키타입 경계 오염
  0건 (c) 단위 함정 재확인(예약가 기간총액 전례).
- **Azure 한 곳만이다** — AWS/GCP 관리형 가격은 여전히 소스가 없다. 답에는
  "azure만 수록"이 붙어야 한다.

## 하지 않는 것

- 사용량형 축에 가정 사용량을 곱해 "예상 비용"을 만드는 것 — 사용량은 계약에
  없는 입력이고, 그 곱은 어느 기준도 아닌 숫자가 된다.
- Dev/Test·Low Priority 미터 (azure-discount에서 이미 제외한 것과 같은 이유).

## 추기 — AWS·GCP 재조사 (보강 3, 2026-07-24)

"AWS·GCP 소스는 없다"를 재실측해 **반은 뒤집혔다**:

- **GCP: 이미 핀 박은 Cyclenerd pricing.yml의 안 쓰던 `storage` 섹션**에 Cloud
  Storage 버킷의 저장(GB/월)·검색(GB당) 단가가 리전 전수로 있다 — objectStorage
  한 종을 담았다(`gcp-managed-pricing.json`, Apache-2.0이라 커밋 가능). Cloud
  SQL·Memorystore·Pub/Sub는 이 파일에 없고, GCP Billing Catalog API는 API 키
  인증이 필요해 무인증 재현 빌드 규약에 안 맞는다 — 그래서 한 종이 전부다.
- **AWS: 미수록 확정.** Price List API는 소스가 실재하지만 재배포가 **명시적으로
  금지**다(소스 표의 `denied` — EBS 한도 때 이미 판정). 과금 축 산출물은 단가
  그 자체라, "빌드 때 받고 값은 안 남긴다"는 EBS식 우회도 성립하지 않는다.
- 소비자도 함께 열었다: OpenAPI의 파일 업로드 본문(multipart/octet-stream) →
  objectStorage 노드(inferred). 신호 없는 데이터는 계약 원칙(소비자 없는 칸
  금지) 위반이라, 신호부터 만들었다.

## 추기 2 — 1층 보강 실측 (2026-07-24)

- **Blob(azure objectStorage)**: Storage serviceName은 잡탕이다 — koreacentral
  1,412건·제품 35종에 Managed Disk·Files·Tables·Data Lake 혼재. Blob만
  productName 큐레이션(include: Blob Storage·General Block Blob / exclude:
  Hierarchical Namespace = Data Lake Gen2). 남는 축은 GCS와 같은 이야기 구조다
  (저장 GB/월 + 검색·오퍼레이션 사용량).
- **Load Balancer**: **일반 리전 행이 없다** — armRegionName='Global'로만 공표
  (실측: koreacentral 0건, Networking 계열에도 없음). 리전별 값을 지어내지 않고
  Global 그대로 담는다. Standard 미터만 취한다(Gateway LB·크로스리전은 다른
  제품층). 진입점(ingress) 노드가 소비한다 — svcmap 아키타입이 아니라 의사
  아키타입 `loadBalancer`다.
- **새 아키타입 4종**: AKS·Cognitive Search(제품명은 AI Search — serviceName은
  옛 이름이라 'Azure AI Search'로 조회하면 0건)·Event Hubs·API Management 전부
  제품 1~2종으로 깨끗. **Event Hubs의 Throughput/Processing/Capacity Unit은
  vCore 함정의 재판**이다 — 단위는 '1 Hour'지만 단위 수가 사이징 결과라
  capacityRate로 분류(axis_of 확장).
- **GCP 이그레스**: Cyclenerd `compute.network.traffic.egress.internet`에 목적지
  (전 세계 기본·중국·호주) × 월간 구간(0~1TB·1~10TB·10TB 초과) × 리전 단가가
  전수로 있다 — networkEgress 의사 아키타입으로 담고, 노출이 있는 계획의
  전역 노트가 소비한다(전부 사용량형 — 곱하지 않는다). 같은 섹션의 azure
  'Bandwidth' serviceName(koreacentral 17건)도 실측에서 보였다 — **다음 후보로
  기록만** 하고 이번엔 안 담는다(gcp와 달리 소비자 검증을 따로 해야 한다).
