# 클라우드 리소스 의존성 분석 — 여정 전체 기록 (2026-07-30 ~ 07-31)

> **불변 기록.** 현재 상태는 코드·테스트·산출물이 진실이다(`depkb/` 전체,
> 테스트 `tests/test_depkb*.py`). 시각화: `depkb/dependency-graph.html`
> (claims에서 재생성 — `python -m app.core.cloudkb.depkb.render_graph`).

## 1. 질문과 정의

출발 질문(사용자): *"리소스 의존성이란 무엇이고, 어떻게 분석할 수 있는가 —
논문 근거 수준으로."* 채택한 답:

- **의존성 = 검증 가능한 주장.** "B에 개입하면 A가 달라진다"의 반사실 정식화
  (Parnas uses 관계의 인프라 인스턴스). 참조가 있다는 구조적 사실은 증거의 한
  종류이지 정의가 아니다.
- **질문은 유형이 갈린다**: 존재(B 없이 A가 만들어지나) · 생명주기(B를 지우면
  어떻게 되나) · 기능(공백 — 대응 실험을 세울 수 없어 분류에 넣지 않음).
  관계 타입화는 TOSCA·k8s `ownerReferences`가 선례다.
- **주장은 CSP로 색인된다** — 같은 간선의 필연이 CSP마다 다를 수 있다(가설
  이었고, 실측으로 두 번 확인됐다).
- **오라클 서열**: 컨트롤 플레인(apply) > preflight > 원문 스키마 > 우리 가공물
  (오라클 금지). 서열의 정당성도 실측됐다 — 아래층이 침묵한 것을 위층이 잡는
  사례가 층마다 있다.

## 2. 연대기

| 때 | 일 | 기록 |
|---|---|---|
| 07-30 | 계획 P1~P5 수립 + 계획 전 재검증(기억 오류 2건 정정) | `dependency-analysis-plan-2026-07-30.md` |
| 〃 | 범위 진술 셋(사용자): CB 비실행 · KB는 가공물이지 원천 아님 · 계정은 azure뿐 | 계획 §0.1~0.3 |
| 〃 | P1: graphkb 간선에 questions/authorities 사영 | `edge_semantics.py` |
| 〃 | **방향 전환(사용자): 순수 제로베이스** — 기존 산출물을 재료로 안 씀 | `depkb-zero-base-2026-07-30.md` |
| 〃 | depkb: 어휘 9종(용도 역산) · azure 원문 핀 수집 · 후보 22 추출 | `fetch_azure.py` · `extract_azure.py` |
| 〃 | CB 중립화의 벤더별 적용 확인 → 지도 체계화(호출 111 · 판정 42셀) | `cb-neutralization-application-2026-07-30.md` · `neutralization_map.json` |
| 〃 | P5a azure preflight(자원 무생성) — 경계 2 실측 | `experiments/azure-preflight-2026-07-30/` |
| 〃 | P5b azure apply 1~3라운드 — azure 16주장 전 판정 | `experiments/azure-apply*-2026-07-30/` |
| 07-31 | aws·gcp 스키마 층(핀 수집·추출) — CSP 색인 가족 시작 | `fetch_vendors.py` · `extract_vendors.py` |
| 〃 | gcp 사다리 1~3라운드(REST 직접) · aws 사다리 1~2라운드(DryRun+실물) | `experiments/{gcp,aws}-apply*/` |
| 〃 | **41주장 전 판정 (unknown 0)** — 통합 산출물 | `claims.json` · `build_claims.py` |
| 〃 | 폐포 소비자 + 문 — 과제 문제 ②에 CSP별로 답함 | `closure.py` · `app/core/dependency.py` |
| 〃 | 근거 감사(우리 구성 전수) · 여정 정리 · 그래프 시각화 | 이 문서 · `render_graph.py` |

실험 8라운드 전부 잔여물 0으로 종료. 총비용: 세 클라우드 합쳐 몇 백 원 이내
(azure VM 수 분 · gcp e2-micro 수 분 · aws는 DryRun+무료 자원).

## 3. 결과물 지도

| 층 | 실물 |
|---|---|
| 결과 | `depkb/claims.json` — 41주장(간선×CSP×질문), required 10 · optional 20 · holds 11 |
| 증거 | `depkb/experiments/` 8라운드 — 재실행 스크립트 + 원자료(오류 원문 발췌) |
| 소비 | `depkb/closure.py` + `app/core/dependency.py` — 폐포·생성 순서·삭제 제약·사람 결정 |
| 원문 | `depkb/cache/azure/`(커밋) · `.cache/cloudkb/`의 CFN·gcp 디스커버리·spider(핀만 커밋) |
| CB 연구 | `depkb/neutralization_map.json` — 기제 판정 42셀 + 호출 색인 111 |
| 시각화 | `depkb/dependency-graph.html` — 3패널 동일 배치, 간선만 다름 |

## 4. 발견

1. **3사 공통핵**: `subnet→network` 필수 · 생명주기 제약(사용 중 삭제 금지)은
   3사 불변, 코드만 다르다(`DependencyViolation`/`InUse…`/`RESOURCE_IN_USE`).
2. **양상 반전 2**: `vm→disk`(gcp만 필수) · `vm→nic`(aws만 선택 — 서버 ENI
   암묵). 중립 필수 플래그 하나로는 표현 불가능한 지식.
3. **조건부 필연 2**: gcp `nic→subnet`(대상 네트워크의 모드) · gcp
   `lb→subnet`(자신의 스킴). 필연은 (간선×CSP×상태)의 함수다.
4. **서버 대체 실물 5**: aws 기본 VPC·default SG·AMI 루트 볼륨, gcp default
   네트워크·auto 서브넷 — 서버가 채운 값을 전부 기록.
5. **존재와 생명주기의 독립**: `nic→firewall`·`nic→publicIp`·`vm→disk` —
   생성엔 선택인데 붙어 있으면 삭제 금지. 질문을 한 필드에 눌렀으면 표현
   불가능했다.
6. **검증 도구의 비균일**: preflight 깊이가 RP별(azure)·API별(aws DryRun)로
   다르고 gcp엔 상당물이 없다. "통과는 증거가 아니다" 규율이 오판 3건을 막았다.
7. **잔존의 쌍**: azure OS 디스크·gcp 부트 디스크가 VM 삭제 후 남는다 — CB
   드라이버가 디스크를 직접 지우는 이유의 실측 검증.
8. **sshKey 3사 완결**: aws 선택(동적) · azure 자원 있되 무참조 · gcp 자원
   부재 — CB의 "필수"는 도구의 요구.
9. **중립화의 기제 5+1**: 드라이버 합성·절단·기제 치환·값 인라인·서버측 암묵
   (+교차 주입 후보). **절단(azure SG `[0]`)은 tumblebug까지 무보정** — 중립화가
   정보를 잃는 실증.
10. **교란 2와 격리 원칙**: SKU 가용성·IGW 부재가 의존 검사보다 먼저 온다 —
    실험 템플릿은 의존 축 외 전부가 유효해야 한다.

## 5. cloud-barista의 자리

기준 후보 → (사용자 결정) 비실행·강등 → **연구 대상**. 지금의 세 역할: 중립화
사례 연구(기제 지도) · 평가 대상(claims로 채점: 평탄 모델은 aws에서만 네이티브,
sshKey 필수는 어느 클라우드의 요구도 아님) · 어휘 참조와 방증 코퍼스(graphkb
83관측). *"벤더 중립은 공짜가 아니라 변환이고, 그 변환이 어디서 정보를 잃는지
측정했다"* — CB에 대한 한 문장.

## 6. 근거 관리 — 우리 구성 전수와 약점

임의 설정은 전부 표시 + 기계 제약 + 위협 등재로 관리했다: 어휘 9종(Z1) ·
형태→질문 대응표(T1) · 이름/쌍 휴리스틱(Z3) · 기제 이름(지도 `_note`) ·
실험→판정 배정표(빌드가 실측 대조) · 술어 소비 분류(`PREDICATE_CLASSES`) ·
실험 보조 선택(교란 2건은 잡혀 기록됨). 분류 불가 입력에는 전부 죽는다.

**약한 지점 4 (논문 위협 절 원고)**: ① aws `nic→subnet` required는 클라이언트
층 거부(서버 미도달, note 명시) ② holds는 "그 상황에서 거부됐다"이지 "항상"이
아님(강제 삭제 미측정) ③ 단일 리전·단일 실행 ④ 기능 의존 공백(선언된 한계).

## 7. 재현

    python -m app.core.cloudkb.depkb.fetch_azure 2026-07-30   # 원문 (핀 검증)
    python -m app.core.cloudkb.depkb.extract_azure            # 후보 (사영)
    python -m app.core.cloudkb.depkb.extract_vendors
    python -m app.core.cloudkb.depkb.extract_spider
    # 실험: experiments/*/run.py <인자> — 계정 필요, 각 스크립트 머리에 비용 명시
    python -m app.core.cloudkb.depkb.build_claims             # 통합 (실측 대조)
    python -m app.core.cloudkb.depkb.render_graph             # 시각화
    pytest app/core/cloudkb/tests/test_depkb*.py              # 불변식 42건

## 8. 남은 갈래

상류 에이전트가 `app/core/dependency` 문을 실제로 부르게 하는 통합(진실 문서
§10-2 접점) · 어휘 확장 절단면(k8s·관리형 DB — 같은 방법 반복) · cross-injection
분류 확정 · 논문 정리(nic→subnet 4층 완주 worked example + 이 문서 §4·§6).
