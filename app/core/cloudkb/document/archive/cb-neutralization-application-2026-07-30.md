# cloud-barista 벤더 중립화의 벤더별 적용 — 첫 확인 (2026-07-30)

> **불변 기록.** 원천: `cb-spider v0.12.37`(캐시 태그 핀,
> `.cache/cloudkb/cb-spider-v0.12.37.tar.gz`). 인용 경로는 그 안의
> `cloud-control-manager/cloud-driver/drivers/` 기준이다.

## 1. 질문

depkb의 azure 원문 대조에서 CB 그래프와 클라우드 그래프가 같은 어휘 9종에서
**4/15쌍만 겹쳤다.** 그 차이(평탄화·방향 역전·합성·구체화·도구 합성물)는 스키마
층의 관측이었다 — 이 문서는 그 **기제**를 드라이버 코드에서 확인한다:
*중립 모델의 VM 생성 하나가 각 벤더에서 실제로 무엇이 되는가.*

## 2. 확인된 것 — 같은 중립 개념, 세 가지 다른 기제

| 중립 개념 | azure | aws | gcp |
|---|---|---|---|
| VM→subnet | **드라이버가 NIC를 합성**하고 NIC가 subnet 참조 — `azure/resources/VMHandler.go:1600 CreateVNic` | RunInstances의 `NetworkInterfaces` 스펙에 SubnetId 인라인(ENI는 서버측 암묵) — `aws/resources/VMHandler.go:358·365` | 인스턴스 insert에 NIC 인라인 — `gcp/resources/VMHandler.go:284` |
| VM→securityGroup (중립 모델은 **복수**) | NIC에 NSG 부착, **`SecurityGroupIIDs[0]`만 — 절단** — `azure/resources/VMHandler.go:1605` | `SecurityGroupIds` 목록 그대로 — `aws/resources/VMHandler.go:357` | **기제 치환**: SG→인스턴스 태그 + 방화벽 규칙 매칭 — `gcp/resources/VMHandler.go:306` · `gcp/resources/SecurityHandler.go:68` 주석 |
| VM→publicIp | **드라이버가 PIP 합성** — `azure/resources/VMHandler.go:1386 CreatePublicIP` | NetworkInterfaces 스펙에서 할당 — `aws/resources/VMHandler.go:365` 주석 | `AccessConfigs` 인라인(자원 없음) — `gcp/resources/VMHandler.go:286` |
| sshKey | 로컬 생성(`GenKeyPair`) 후 **azure `sshPublicKeys` 자원으로 등록**, VM에는 값 — `azure/resources/KeyPairHandler.go:79·220` | 네이티브 KeyPair(`KeyName`) — `aws/resources/VMHandler.go:349` | 로컬 생성 → **메타데이터 `ssh-keys` 값**(자원 아님) — `gcp/resources/KeyPairHandler.go:59` · `gcp/resources/VMHandler.go:264` |

## 3. 기제의 이름 다섯 (우리 구성 — 예시는 전부 인용)

1. **드라이버 합성** — 중립 모델에 없는 중간 자원을 드라이버가 만든다(azure NIC·PIP).
   depkb 대조의 "평탄화"가 성립하는 방법이 이것이다.
2. **절단** — 중립 모델의 표현력이 벤더 적용에서 줄어든다(azure SG 목록→1개).
   **중립화가 정보를 잃는 실증**이고, 사용자에게 보이지 않는 손실이다.
3. **기제 치환** — 자원 대응이 아예 없어 다른 기제로 구현한다(gcp SG→태그+방화벽).
   "SG에 의존한다"가 gcp에서는 자원 참조가 아니라 태그 문자열 일치다.
4. **값 인라인** — 자원↔값 경계가 벤더마다 갈린다(키: aws 자원 · azure 등록형
   자원+값 · gcp 순수 값).
5. **서버측 암묵** — 클라우드가 스스로 채운다(aws ENI, aws 기본 서브넷 —
   `aws/resources/VMHandler.go:358` 주석이 명시).

## 4. 의의

- **"CB 기준 의존성"은 벤더별로 다른 세 문장의 평균이다.** 같은 간선
  `vm→securityGroup`이 azure에선 "NIC 속성 하나", aws에선 "인스턴스 속성 목록",
  gcp에선 "태그 문자열"이다. 중립 그래프 하나로 셋을 대표하면 각 벤더에서
  잃는 것(절단·치환)이 보이지 않는다.
- depkb 반사실 실험(P5)의 대상은 **클라우드 층**이어야 한다는 것이 재확인된다 —
  드라이버 층의 요구(예: SG 정확히 1개 이상)는 CB의 사정이다.

## 5. 다음

1. **체계화**: 중립 타입 전수 × 3 CSP × 생성/삭제 경로 → depkb 산출물
   (중립화 적용 지도)로 굳히고 인용 실재를 테스트로 강제한다.
2. spider 소스 핀을 depkb manifest 규율로 편입.
3. 미확인 남김: azure NSG 절단이 상위(tumblebug)에서 보정되는지 — tumblebug이
   SG 여러 개를 azure로 보낼 때 무엇이 되는지는 **안 봤다.**
