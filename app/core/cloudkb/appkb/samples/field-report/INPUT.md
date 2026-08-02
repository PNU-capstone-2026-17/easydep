# 입력 요구사항 — 현장 점검 보고 서비스 (비교실험용)

비교실험(`document/archive/comparison-experiment-plan-2026-08-02.md`)의 **공통
입력**이 되는 씨앗입니다. 같은 문장을 세 계열(본 시스템 · 단순 LLM ·
MetaGPT류 프레임워크)에 주고 배포 산출물을 비교합니다.

**요구 문장은 영어입니다** — 시스템의 입출력 언어가 영어이고, 비교 대상
프레임워크들도 영어 중심이라 언어가 교란이 되지 않게 합니다. 설명 면은
한국어입니다.

## 작성 원칙 넷

**① 관심사를 미리 답해 놓지 않는다.** 관심사 축(실측 8건)의 결정 일부를
**일부러 침묵**시킵니다. 침묵 지점이 이 실험의 관측점입니다 — 본 시스템은
질문·인계로 드러내야 하고, 베이스라인이 그 자리를 어떻게 다루는지(지어내는지 ·
무시하는지)가 비교의 알맹이입니다. 아래 관측점 표가 그 설계입니다.

**② 클라우드 제약은 요구 산문에 섞지 않는다.** 실측상 provider·region·예산은
요구 문장에 0건입니다 — `constraints.txt`가 별도 입력입니다.

**③ 배포 축이 실제로 갈리게 만든다.** 외부 노출 API(모바일) · 내부 처리
서비스(썸네일·PDF — service-discovery가 걸릴 내부 호출) · 영속 저장(사진·
보고서) · 야간 백업(구현이 클라우드 API를 부를 자리 — 권한 축)이 있어야
관심사 8건이 전부 걸립니다.

**④ 세 계열에 공정하게.** 어느 시스템의 어휘·형식 힌트도 문장에 넣지
않습니다. 실제 사용자가 쓸 법한 문장만 씁니다. 세 계열이 받는 파일이
바이트 단위로 같아야 합니다(프롬프트 포장은 실험 기록에 따로 남깁니다).

**신규 개발 전제입니다** — 기존 인프라 인계는 범위 밖이고(계약 결정과 정합),
문장에도 기존 시스템 이야기가 없습니다.

---

## Functional requirements

1. A field engineer can sign in and see the list of sites assigned to them.
2. An engineer can create an inspection report for a site by filling in a
   checklist and attaching photos taken on site.
3. A report saved as a draft on site can be completed and submitted later.
4. After a report is submitted, its photos are processed into thumbnails and a
   PDF summary of the report is generated.
5. A site manager can browse reports by site and date, view the photos, and
   download the PDF summary.
6. A manager can mark a report as requiring follow-up, and the engineer sees
   the follow-up request the next time they sign in.
7. An administrator manages engineer accounts and site assignments.
8. Every night the day's reports and photos are backed up.

## Non-functional requirements

9. Inspection records must be kept for five years, and must survive any
   redeployment or replacement of the service.
10. Most reports are submitted during working hours on weekdays; almost none
    arrive at night or on weekends.
11. Engineers work at outdoor sites and reach the service over the mobile
    network.
12. Browsing the report list of a site must respond within 3 seconds.
13. A failure in photo processing must not prevent engineers from submitting
    new reports.

## Cloud constraints (separate input — `constraints.txt`)

> Deploy on AWS in the Seoul region, on managed Kubernetes. Everything must
> run inside the cluster; do not use managed data services, so that the
> deployment stays portable. The monthly budget is at most 400 USD. Around
> 150 engineers use the service, and during busy hours about 40 of them are
> submitting at the same time.

**정정 기록(2026-08-02, 실행 전)**: "클러스터 안에서, 관리형 데이터 서비스
불사용" 문장은 첫 판에 없었고 기준 설계에서 추가했다
(`document/archive/comparison-criteria-2026-08-02.md` §4 — S3·RDS가 관용적
답인데 우리 측정 어휘 밖이라, 이 제약이 없으면 세 계열이 서로 다른 흙 위에서
비교된다). 실행 전 정정이라 사후 조정이 아니며, 세 계열에 같은 규칙으로
걸린다.

---

## 관측점 표 — 관심사 8건 기준, 무엇을 명시하고 무엇을 침묵시켰나

| 관심사 | 명시/침묵 | 어디에 · 왜 |
|---|---|---|
| 부하 모양 (`cn.load-shape`) | **명시** | 문장 10 — 근무시간 집중(spiky 신호). 명시 지점의 대조군: 셋 다 이건 읽어야 정상 |
| 데이터 운명 (`cn.data-fate-on-removal`) | **반만** | 문장 9 — 5년 보존·재배포 생존은 명시. **PVC/디스크 삭제 방향은 침묵** — 기본 reclaim Delete가 9번과 충돌하는 것을 드러내는가 |
| 도달성 (`cn.reachability`) | **반만** | 문장 11 — 모바일망 접근은 명시. **관리자·내부 서비스의 접근 범위는 침묵** |
| 노출 경로 (`cn.exposure-path`) | 침묵 | Service type·Ingress·컨트롤러 선택을 아무도 안 정했다 — aws에서 Ingress는 조용한 무동작이 되는 자리 |
| 저장 프로비저닝 (`cn.storage-provisioning`) | 침묵 | 저장 필요는 함의되나 SC·접근 모드(썸네일 서비스와 API가 같은 볼륨을 공유하면 RWX!)는 침묵 — aws 기본 SC 미지정 Pending이 걸리는 자리 |
| 네트워크 격리 (`cn.network-isolation`) | 침묵 | 전용/기본 망 이야기 없음 |
| 주소 안정성 (`cn.address-stability`) | 침묵 | 모바일 앱이 있으니 **실제로는 필요한 값**인데 사용자는 안 쓴다 — 가장 자연스러운 침묵 관측점 |
| 클라우드 API 접근 (`cn.cloud-api-access`) | 침묵 | 문장 8은 백업이라는 **기능**만 요구한다. 구현이 디스크 스냅샷을 쓰면 권한이 필요해지는데 그 결정은 아무도 안 했다 |

제약 산문 쪽 침묵도 관측점이다: **스펙 하한 없음**(스펙을 고르지 않고 규모로
되묻는가, 아니면 지어내는가) · **multiZone 없음** · **dataResidency 없음**.

침묵이 함정이 되는 이유는 **평범한 필요가 그 CSP의 실제 행동과 만나기 때문**
이다 — 요구 문장에 함정 이야기는 하나도 없다. 사진을 API가 쓰고 워커가 읽으니
공유 볼륨(RWX)이 필요하고, 5년 보존(문장 9)이 기본 reclaim Delete와 부딪히며,
야간 백업(문장 8)은 구현이 스냅샷을 부르면 권한이 필요해진다. 심판은 우리
KB가 아니라 **실제 apply와 시간**이다(기준 설계 §1·C1 — 지연 실패).

---

## 실행 기록

여기서부터는 실제로 돌린 결과를 남깁니다. `PROVENANCE.md`가 그 자리입니다.
