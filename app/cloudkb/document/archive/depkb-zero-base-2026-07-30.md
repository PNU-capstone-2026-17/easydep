# depkb — 제로베이스 의존 분석의 첫 수직 절단면 (2026-07-30)

> **불변 기록.** 현재 상태는 코드·테스트(`depkb/` · `tests/test_depkb.py`)가 진실이다.

## 1. 결정 (사용자, 2026-07-30)

**기존 산출물(graphkb·캐시·검수 파일)을 재료로 쓰지 않고 원천 수집부터 새로
시작한다.** 앞선 계획(`dependency-analysis-plan-2026-07-30.md`)의 P2~P4는 이
결정으로 **대체**됐다 — 살아남는 것은 규율(순서≠의존 · 도구≠클라우드 · 사영이지
등급 아님 · 오라클 서열)과 MBT 3층 설계(P5)다. graphkb는 도구 층의 관측 코퍼스로
남고 depkb의 근거로 쓰지 않는다.

## 2. 세운 것

- **주장 형식이 산출물보다 먼저다** — (주체, 대상, 질문, CSP, 술어) + 원천 인용 +
  도달한 오라클 층. `depkb/__init__.py`.
- **어휘 9종은 용도에서 역산했다**(선정 기준 2개 명시, `vocabulary.py`) — 도구
  swagger가 아니라 "계획기가 결정해야 하는 것". k8s·관리형 DB는 다음 절단면.
- **수집은 핀과 함께** — `Azure/azure-rest-api-specs` 커밋
  `478f542f`(2026-07-30 HEAD), 파일 5개(network 2025-07-01 · ComputeRP 2026-03-01 ·
  DiskRP 2026-03-02), SHA-256 manifest. `fetch_azure.py`.
- **추출은 후보까지만** — 입력 참조 / readOnly 백링크 / 경로 중첩 세 형태.
  `extract_azure.py` → `azure_candidates.json`(22후보 · 15쌍 · 미해결 외부 ref 36
  — 숨기지 않고 센다).

## 3. 원문이 말한 것 (전부 인용 있음)

- **골격이 azure 네이티브로 섰다**: `vm→nic→subnet(→network 경로 중첩)` ·
  `nic|subnet→firewall` · `nic→publicIp` · `loadBalancer→subnet|publicIp` ·
  `vm→disk`. **tumblebug 어휘에 없던 NIC 층이 1급이다** — 스파인을 바꿔 얻은 실물.
- **ARM 스키마는 필연을 거의 안 말한다** — NIC의 PropertiesFormat조차 `required`
  목록이 없다. TOSCA 2.0의 `count_range` 기본값과 같은 방향의 실측이고,
  **필연 판정이 반사실 실험(preflight·apply)의 몫**이라는 설계를 원문이 뒷받침한다.
- **azure VM은 `SshPublicKeyResource`를 참조하지 않는다** — 키는
  `osProfile.linuxConfiguration` 인라인 값이다. "sshKey 필수는 도구의 요구"라는
  이전 판정(커밋 `a490071`)이 그 산출물을 전혀 안 쓴 추출에서 독립 재현됐다.

## 4. 물린 것

**readOnly는 상위 속성에 붙고 참조는 그 안에 있다.** 전파를 빠뜨린 첫 판이
`publicIp→subnet`(`ipConfiguration` 경유)을 입력 참조로 오분류했다. 하강에
readOnly를 전파해 고쳤고 `test_backlinks_never_masquerade_as_inputs`가 지킨다.

## 5. 다음

1. **반사실 실험(azure)** — 후보의 필연 판정. P5a(preflight: ARM validate/What-If,
   계정만 필요·실행 전 API 능력 확인)부터, P5b(apply)는 비용 게이트.
2. 공통 타입 파일(types.json) 캐시 추가 → 미해결 ref 36 해소.
3. aws·gcp 스키마 층 확장(같은 어휘, CSP별 결속) — CSP 색인 주장의 시작.
4. k8s·관리형 DB 절단면.

## 6. 타당성 위협

| | 위협 | 대응 |
|---|---|---|
| Z1 | 어휘 9종 선정은 우리 구성 | 기준 2개를 명시했고, 하류 소비와의 대조는 다음 절단면에서 |
| Z2 | 스키마 층은 필연을 못 준다 | `requiredInSchema` 실측으로 명시. 필연은 반사실 층 전까지 미판정 |
| Z3 | 래퍼·경로 세그먼트 대응은 우리 구성 | 후보마다 원문 인용 — 틀리면 인용에서 드러난다 |
| Z4 | 판 갱신 시 이름 이동(실제로 `VirtualNetwork`→`Common.VirtualNetwork`) | 결속 실재를 테스트가 강제 |
| Z5 | 미해결 외부 ref 36 | 0으로 적지 않고 센다 — 커버리지 주장 금지 |
