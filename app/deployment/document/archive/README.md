# 불변 기록 보관소

날짜 박힌 조사·계획·실측 문서입니다. **작성 시점의 스냅샷이며 갱신하지 않습니다**
— 완료·정정은 커밋 메시지가 기록하고, 현재 상태는 코드·테스트가 진실입니다.
여기 문서의 숫자(축·도구·테스트 개수)는 낡았을 수 있습니다.

| 문서 | 무엇 (작성 시점) |
|---|---|
| kb-roadmap.md | 초기 로드맵 — 목표 2 대비 갭 |
| cloud-dependency-graph-brief.md | 최초 프로젝트 브리프 (Plan Mode 입력용) |
| dependency-extraction.md | 의존성 추출 방법 기록 (07-20) |
| decisions.md | 세션 기록 8편의 1차 통폐합 (07-18~21 결정) |
| agent-probe-2026-07-21.md | 에이전트 실측·회귀 결과 |
| gap-map-2026-07-22.md | 소스 조사 전 기준선 (빈 곳 지도) |
| bundle-sizing-research-2026-07-22.md | bundlekb·sizingkb 소스 조사 |
| goal2-open-items.md | 목표 2 미해결 항목 실측 (07-23) |
| app-layer-plan-2026-07-23.md | 앱 계층 P1~P4 계획 |
| design-input-contract-2026-07-23.md | 설계 산출물 입력 계약의 근거 |
| pattern-sources-2026-07-23.md | 패턴 소스 조사 |
| pipeline-restructure-plan-2026-07-24.md | 재편 계획 (①~⑥, 완결) |
| managed-pricing-research-2026-07-24.md | 관리형 가격 조사 (⑥-B 게이트 + 추기) |
| easydep-agenda-2026-07-24.md | easydep 팀 합의 안건 4건 (제안 스냅샷 — 결과는 커밋이 기록) |
| instance-traits-nl-research-2026-07-24.md | 인스턴스 특성·자연어 KB 조사 — 채택 3(azure 크기 표·gcp 시리즈·Azure WAF)·기각 2 |
| verification-round-2026-07-24.md | 검증 라운드 — 라이브 회귀 0건(프로브 기대 결함 1 정정)·FTS 재현율 첫 실측·세대 보류 게이트 |
| end-to-end-example.md · runnable-examples.md | 실행 예제 스냅샷 (07-23 출력 기준) |
| kb-test-queries.md | 수동 검증 질의집 (도구 목록은 코드가 진실) |
| cloud-kb-guide.md · plain-language-overview.md | 구세대 해설서 (KB 3개 시절 — kb-book으로 대체) |
| kb-verification-2026-07-28.md | kb-book 외부 검증 — 수치 전수 재현(일치) · 논문 기준 공백 5 · 측정된 편향 6 · 드리프트 11건 |
| kb-sourcebook-2026-07-28.md | 문외한용 해설 — 축×클라우드 커버리지 격자 + **소스 47종 전수** 설명(URL 포함) |
| cloud-native-deployment-2026-07-29.md | **연구 관점 요약**(kb-book 축약) — research.md 목표 2를 지식 요구로 번역 · 조사 절차 · 에이전트 반영 4층 · 미결 10건 · 외부 표준 양방향 매핑 |
| perfkb-field-axis-plan-2026-07-29.md | perfkb 필드 축 재설계 **계획**(실행 전) — details 키 전수 분류 절차·포화·외부 매핑·타당성 위협 6. 실측: aws 24키중 7 · azure 59중 7 · gcp 17중 4 사용, azure maxNics 미러 100% vs 문서표 72.8%(불일치 832) |
| kb-source-atlas-2026-07-29.md | 재료 47종 전수 — **원본 발췌 → 처리 규칙 → 산출 레코드**를 실제 캐시·산출물에서 뽑아 나란히 놓은 것 (sourcebook의 "무엇"에 대한 "어떻게") |
| pipeline-big-picture-2026-07-28.md | 요구사항→설계→배포 사슬의 큰 그림 — 설계 산출물이 주는 신호 전수·부족분 3종·관심사 29건 중 23건 미연결·과제 목적 대조 |
