# 조사: 인스턴스 특성 채움 + 자연어 KB 2호 (2026-07-24)

> **이력이다. 참조하지 않는다.**
>
> 현재 진실은 [`docs/cloud-native-extension.md`](../../../../docs/cloud-native-extension.md). 이 문서는 작성 시점의
> 스냅샷이고 전제가 바뀐 자리가 있다. **여기 적힌 결정·계획을 근거로 새 작업을
> 시작하지 말 것.** 안의 **실측치는 유효하다** — 다시 재지 말고 인용한다.

"버스터블만 있는 건 아니잖아"에서 출발한 조사입니다. 전부 HTTP·로컬 실측이고,
결론은 채택 3 · 기각 2 · 갭 없음 판정 2입니다.

## 전제가 뒤집힌 것부터 — 차원은 이미 34개다

perfkb 레코드에는 특성 필드가 34개 있고(클럭·CPU 제조사/모델·메모리 속도·네트워크
성능·GPU 모델/수·세대·베어메탈·EBS·디스크 IOPS·ACU…), 도구 표면도 fields 모듈
공유로 25+축이 profile/compare에 나가며 GPU 필터(require_accelerator)도 노출돼
있습니다. **문제는 차원 부재가 아니라 프로바이더별 채움의 비대칭입니다:**

    aws   18,564건: 거의 전 필드   azure 34,846건: 4필드   gcp 11,622건: 1필드(sustainedCpu)

- **A-4(도구 표면) 판정: 갭 없음.** "값은 있는데 안 나감"을 의심했으나 실측으로
  반증 — 기대가 또 틀렸다(프로브 기대를 먼저 의심하라).
- **A-3(기존 소스 미사용 필드):** Cyclenerd pricing.yml의 instance 항목에
  gcp GPU 수(a2-highgpu의 a100 등)가 있음 — 소폭. IBM은 추가분 없음.

## 채택

| 소스 | 라이선스·핀 | 실측 | 채움 |
|---|---|---|---|
| **MicrosoftDocs/azure-compute-docs** | CC-BY-4.0 · 태그 없음→커밋 SHA | `articles/virtual-machines/sizes/` 계열별 크기 문서(general-purpose만 63파일)에 vCPU·메모리·**디스크 IOPS/처리량(버스트 포함)·NIC 수·네트워크 대역폭 Mbps** 마크다운 표 | azure 4→10+필드 (azure-limits-doc 선례 그대로 — 문서 저장소의 **표** 파싱) |
| **Cyclenerd/google-cloud-compute-machine-types** | Apache-2.0 · 커밋 SHA | per-series SQL(UPDATE 문)에 cpuPlatform·family·localSsd·spot·**크기별 bandwidth Gbps·tier1**. 릴리스 산출물 없음 → SQL 파싱(문법 단순·결정론) | gcp 1→6+필드. **큐레이션 등급**(mingrammer 계열) — 근거 라벨을 갈라 담는다 |
| **MicrosoftDocs/well-architected** | CC-BY-4.0 · 커밋 SHA | `well-architected/` 하위 md **199편**(pillars·ai·워크로드별 지침) | patternkb 섹션 추가(FTS5·advisory·최소 편수 불변식 — 방법 재사용, 도구 수 불변) |

## 기각 (사유와 함께 — 지우면 다음 사람이 다시 조사한다)

- **AWS Well-Architected**: 공식 문서는 재배포 불명(awsdocs 아카이브·비움 전례)이고,
  awslabs/aws-well-architected-labs(Apache-2.0)는 내용이 **실습 절차**(대시보드·툴
  사용법)라 지침 산문 코퍼스로 성격 불일치.
- **GCP Architecture Framework**: 핀 가능한 원본 저장소가 없다(사이트 렌더링뿐) —
  사람이 읽는 문서를 긁지 않는 원칙(핀·재현 불가).

## 착수 순서 (구현은 별도 라운드)

1. azure 크기 표 파서 → perfkb azure 보강 (함정 예상: 파일별 표 구성 상이·각주
   sup 태그·'Not Supported' 행 — 최소 계열 수 불변식으로 재편 감지)
2. gcp 시리즈 SQL 파서 → perfkb gcp 보강 + pricing.yml GPU 수 병합
   (미러 specName과 조인율 실측이 게이트)
3. Azure WAF 199편 → patternkb 코퍼스 확장

**하지 않은 것(재확인):** 벤치마크·SLA(재조사 금지 목록 — vantage coremark도 기존
결정대로 미수록), AWS Price List 파생 속성(재배포 금지), 인증 필요 카탈로그 API.
