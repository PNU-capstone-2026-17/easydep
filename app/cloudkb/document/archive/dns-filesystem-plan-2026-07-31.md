# globalDns · fileSystem 라운드 계획 (2026-07-31, 실행 전 기록)

어휘 밖 대기열의 마지막 둘. 자원이 겹치지 않아 병렬로 돈다.

## globalDns — 서비스 노출의 이름 층

tumblebug v0.12.25 신설 자원이고, CNA에서 "앱을 무슨 주소로 노출하나"의
자리다. 지금 우리 사슬은 IP까지만 말한다.

셀 (3사 공통 사다리):

| 셀 | 관측 |
| --- | --- |
| A1 zone 없이 record 생성 | 존재 의존의 음성 — 영역 없이 레코드가 서는가 |
| A2 zone 생성 → record 생성 | 양성 대조 |
| L1 record 존재 중 zone 삭제 | 생명주기 — 비어 있지 않은 영역을 지울 수 있는가 |
| L2 record 삭제 → zone 삭제 | 양성 대조(막혔다면) |

**공인 도메인은 쓰지 않는다** — 소유하지 않은 이름을 등록하면 안 되므로
사설 영역(azure private-dns · gcp private managed zone · aws private
hosted zone)으로 잰다. 사설/공인의 차이가 판정에 영향을 준다면 그것도
기록한다(사설로만 쟀다는 한계를 note에 명시).

aws는 private hosted zone이 VPC를 요구할 수 있다 — 그러면 그 자체가
관측(`globalDns→network`)이고, 전제로 VPC를 만들어 진행한다.

## fileSystem — RWX의 자원 층

k8s 합성 2라운드에서 RWX PVC가 3사 전부 완주 불가였고(전제 부재·정책
교란·드라이버 거부), 그때 "fileSystem 어휘 편입이 선행"이라고 적었다.
여기서 **k8s를 거치지 않고 클라우드 자원 층에서 직접** 잰다.

| CSP | 자원 | 셀 |
| --- | --- | --- |
| aws | EFS + mount target | A1 파일시스템 생성(네트워크 인자 없이 되는가) · A2 mount target에 subnet 생략 → 거부 예상 · A3 subnet 주고 생성 → 양성 · L1 mount target 존재 중 subnet 삭제 |
| gcp | Filestore | A1 network 생략 → 거부 예상 · A2 network 주고 생성 → 양성 (**최소 티어가 1TiB급이라 비용이 크다 — 거부 셀까지만 하고 실생성은 안 한다**) |
| azure | Storage Account + File Share | 2라운드에서 CSI가 계정 합성을 시도하다 구독 정책(TLS)으로 실패했다 — 자원 층에서 직접 만들어 그 정책이 사용자 생성에도 걸리는지 본다 |

**비용 규율**: gcp Filestore는 실생성하지 않는다(거부 관측만). aws EFS는
프로비저닝 없는 탄력 모드라 분 단위 무료에 가깝고, azure 스토리지 계정도
빈 계정은 무료다. 이 비대칭은 판정 범위에 그대로 적는다 — gcp는 존재
의존의 음성만, 나머지는 양성까지.

## 위협

- **T-사설 한정**(dns): 공인 영역 미측정.
- **T-비대칭 깊이**(fs): gcp는 거부 층까지만 — "gcp는 안 된다"가 아니라
  "우리가 거기까지만 쟀다"로 적는다.
- T-단일실행 · 리전 계승 · 라운드 끝 3사 전수 점검.
