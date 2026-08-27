# 선행 연구 대조 — 의존 분석 축 (2026-08-01)

`document/dependency-model.md` §3에 이미 선행 대조가 있다. **그것은 이 축이 생기기
전에 쓴 것**이고(graphkb의 스키마 유래 의존 그래프 대조), 그 뒤 새로 생긴 주장들은
대조가 안 돼 있었다. 이 문서가 그 부분만 잇는다.

## 0. 무엇을 대조하는가 — 새로 생긴 주장 다섯

| # | 주장 | 규모 |
|---|---|---|
| ① | **질문 축을 셋으로 가른다** — 존재(만들 때) · 생명주기(지울 때) · 기능(떼었을 때) | 118주장 = 72 + 32 + 14 |
| ② | **실측으로 얻는다** — 스키마·문서가 아니라 실제 컨트롤 플레인에 걸어 거부 코드를 오라클로 쓴다 | 라운드 52 · 스텝 873 |
| ③ | **기능 축은 오라클이 다르다** — 컨트롤 플레인이 **막지 않는** 지대(무방비)라 거부 코드가 없다. 기능 신호로 잰다 | 신호 7종 |
| ④ | **양상 반전** — 같은 간선이 CSP마다 다른 판정 | 8건 + 대상 종류 반전 2건 |
| ⑤ | **계획 ↔ 실측 대조** — 설계에서 나온 배포 계획을 실측에 걸어 틈을 센다 | 표본 3 × 3사 |

## 1. 검색 절차 (재현 가능하게)

2026-08-01, 영문 웹 검색 6질의. **질의를 적어 두는 이유**: *"선행 연구가 없다"*는
찾아서 확인했을 때만 쓴다는 규율(`cloudkb/CLAUDE.md` §5) 때문이고, 무엇을 찾아봤는지가
없으면 그 문장이 검증 불가능해진다.

1. `empirical measurement cloud provider API resource dependency deletion order constraints multi-cloud differential`
2. `"chaos engineering" OR "fault injection" cloud infrastructure resource detach silent failure control plane does not prevent empirical study`
3. `Terraform infrastructure as code misconfiguration detection empirical provider behavior mining "resource dependency" static analysis 2024 2025 survey`
4. `cloud provider API black-box probing measure actual behavior rejection codes derive constraints "grey-box" OR "differential testing" AWS Azure GCP resource creation research paper`
5. `distinguishing creation-time versus deletion-time versus runtime dependencies cloud resources taxonomy "dependency" model research`
6. `"deletion dependency" OR "teardown order" cloud resources DependencyViolation empirical study cascade delete protection research`

**한계를 먼저 적는다.** 웹 검색 6질의는 체계적 문헌 조사가 아니다. 디지털 라이브러리
(ACM DL·IEEE Xplore) 전수 질의도, 후방·전방 인용 추적도 안 했다. 아래 "못 찾았다"는
**이 절차 안에서 못 찾았다**는 뜻이고, 그 이상을 주장하지 않는다.

## 2. 가장 가까운 선행 — 축 분리는 이미 있다

**Yang, Li, Shen, Su, Yang, Lyu. "Managing Service Dependency for Cloud Reliability:
The Industrial Practice." ISSREW'22 (arXiv:2210.06249).**

의존을 **배포(deployment) · 런타임(runtime) · 운영(operational)** 셋으로 가른다.
가르는 기준이 *"실패의 영향이 언제 나타나는가"* — 배포 중에 나타나면 배포 의존,
런타임 기능에 영향을 주면 런타임 의존이다.

**우리 ①과 정면으로 겹친다.** 존재 축 ≈ 배포 의존, 기능 축 ≈ 런타임 의존.
즉 **축을 가른다는 착상은 우리 것이 아니다.** 그렇게 적는다.

갈리는 자리 셋:

| | Yang 외 (ISSREW'22) | 우리 |
|---|---|---|
| **입도** | **서비스 수준** (ECS가 API 관리 서비스에 의존) | **자원 수준** (`vm→publicIp`, `subnet→internetGateway`) |
| **삭제 축** | 없다 — 배포/런타임/운영 셋 | **생명주기 축이 따로 있다**(32주장). 그리고 그 안에서 삭제 보호와 **동반 정리**가 기제가 반대인 것을 갈라 잰다 |
| **얻는 법** | 사내 운영 플랫폼(DMS)에 **선언·축적** | **실측** — 3사에 실제로 걸어 거부 코드를 오라클로 |
| **범위** | 단일 프로바이더 프로덕션 | 3사 대조가 목적(양상 반전 ④) |

### 2.1 강도(intensity) — 그쪽이 더 정교한 축, 그러나 출처가 다르다

Yang 외의 초록은 *"refined characterization of the **intensity** of dependencies"*를
기여로 든다. 본문을 열어 보면 그 강도가 어떻게 정해지는지가 이렇게 적혀 있다:

> *"reliability engineers will categorize the intensity into different levels by
> referring to the output of AID and their domain knowledge."*

**등급 자체는 사람이 도메인 지식으로 매긴다**(도구 출력은 참고 자료다). 논문에
고정된 척도는 제시되지 않는다.

우리는 강도 축이 **없다** — `required`/`optional`/`holds` 이진에 가깝다. 그쪽이
표현력에서 앞선다. 다만 우리 판정은 **오라클에서 나온다**: 존재·생명주기는 컨트롤
플레인의 거부 코드, 기능은 게스트에서 잰 신호다. 사람의 등급 매김이 없다.

    표현력   Yang 외 > 우리 (강도 등급이 있다)
    출처     우리 > Yang 외 (판정이 사람 판단이 아니라 오라클이다)

**한계로 적을 것**: 우리는 "얼마나 세게 의존하는가"를 말하지 못한다. 예컨대
`vm→publicIp`(기능 결속)와 `nic→subnet`(존재 필수)이 같은 무게로 실린다.
강도를 넣으려면 그 등급의 오라클부터 정해야 하고, 그건 이 축에 없다.

## 3. 주장별 대조

### ② 실측으로 얻는다 — 방법론에 선행이 있다

**Atlidakis, Godefroid, Polishchuk. RESTler (ICSE 2019)** — REST API를 상태 기반으로
퍼징하면서 **요청 타입 사이의 생산자-소비자 의존을 스펙과 응답 피드백에서 추론**한다.
*"analyzed specifications, inferred dependencies among request types, and dynamically
generated tests guided by feedback from service responses."*

**방법이 같다**(찔러 보고 응답으로 의존을 배운다). 목적이 갈린다 — RESTler는 **버그를
찾으려고** 의존을 추론하고, 우리는 **계획에 쓸 지식으로** 추론한다. 그래서 우리는
거부 코드를 커버리지 신호가 아니라 **판정의 오라클**로 쓰고, 주장마다 실험/스텝
좌표를 남긴다.

**Martin-Lopez, Segura, Ruiz-Cortés. RESTest (ICSOC 2020)** — 입력 파라미터 사이의
**inter-parameter dependency**를 IDL로 형식화한다(*"constraints that restrict not only
input values, but also the way in which input values can be combined"*).

**우리 선언 술어와 같은 부류다** — `azure loadBalancer→subnet|publicIp|publicIPPrefix`
("셋 중 하나")가 정확히 그 형태다. **우리가 그 형식주의를 안 쓰고 있다는 것이 이
대조의 소득이다**(지금은 `|` 문자열).

### ③ 무방비 지대 — 인접하지만 목적이 다르다

카오스 엔지니어링·결함 주입이 가장 가깝다. 특히 **Kubernetes 컨트롤 플레인 결함
주입**(Mutiny 계열)은 *"pod 수준 교란만 보는 기존 접근이 코어의 취약점을 놓친다"*고
지적한다 — 우리가 "컨트롤 플레인이 안 막는다"를 축으로 세운 것과 문제의식이 닿는다.

**갈리는 자리**: 카오스는 **돌고 있는 시스템의 회복력**을 시험하고, 우리는 **자원
모델에 대한 지식 주장**을 세운다. 그래서 우리 실험은 *confirm → mutate → loss →
restore → recovery*로 **인과를 닫고**(회복까지 관측해야 주장이 선다), 카오스는 그럴
필요가 없다.

### ⑤ 계획 ↔ 실측 대조 — 정적 검사 도구가 인접하다

Checkov·tfsec 같은 IaC 정적 분석과, 그 채택을 잰 실증 연구
(**Verdet 외, Empirical Software Engineering 2024** — 812개 오픈소스 프로젝트의
Terraform을 AWS·Azure·GCP에 걸쳐 스캔)가 있다.

**갈리는 자리**: 그쪽은 **손으로 쓴 정책 규칙**에 대조하고, 우리는 **실측한 컨트롤
플레인 거동**에 대조한다. 그리고 그쪽 규칙은 대부분 보안이고, 우리 대조는 순서·
서버 합성·무방비 결속이다.

### ①의 삭제 축 — 학술 선행을 못 찾았다

삭제 순서는 **실무 지식과 도구**로만 나온다: Terraform·CloudFormation이 **선언된 생성
의존 그래프를 뒤집어** 삭제 순서를 만든다. 검색 6번은 학술 실증 연구가 아니라
문제 해결 문서(DependencyViolation 대처법 등)로 채워졌다.

**여기서 우리 관측이 실무 전제와 어긋난다.** "생성 의존을 뒤집으면 삭제 순서"라는
전제는 우리 실측에서 **두 곳에서 깨진다**:

- **동반 정리**(`cleanupCascades`, 8건) — 주체를 지우면 합성물이 **함께 지워진다.**
  삭제 보호와 방향이 **반대**다. 뒤집기로는 안 나온다.
- **생성 optional인데 삭제는 막힌다** — `azure nic→publicIp`는 존재 optional인데
  붙어 있으면 삭제가 거부된다. 생성 그래프에 없는 간선이라 뒤집어도 안 나온다.

### ④ 양상 반전 — 부분적으로 대응이 있다

`dependency-model.md` §3.1.2가 이미 확인했다: 커뮤니티 TOSCA 프로파일에서 Azure가
NSG를 VNet이 아니라 Region 밑에 둔다. **모양 차이는 프로파일에 나타나지만
`required` 판정은 안 나타난다**(2.0의 `count_range` 기본값이 선택이라서).
CSP별 판정 차이를 **실측으로 8건 확정**한 것에 대응하는 선행은 이 절차로는 못 찾았다.

## 4. 정직하게 적는 결론

- **축을 가른다는 착상은 선행이 있다**(Yang 외). 우리 것은 입도(자원 수준)·
  **삭제 축의 추가**·획득 방법(실측)·3사 대조다.
- **찔러서 의존을 배우는 방법도 선행이 있다**(RESTler·RESTest). 우리 것은 그것을
  **계획 지식**으로 쓰고 주장마다 좌표를 남기는 규율이다.
- **선언 술어를 형식화할 언어가 이미 있다**(IDL) — 우리가 안 쓰고 있다. **개선 지점.**
- 삭제 축과 양상 반전은 이 절차 안에서 학술 선행을 **못 찾았다.** 없다고는 안 쓴다.
- **강도 축은 그쪽에 있고 우리에겐 없다**(§2.1). 표현력에서 뒤지는 자리이고,
  그대로 한계로 적는다 — 다만 그쪽 등급은 사람이 매기고 우리 판정은 오라클에서
  나온다는 차이도 함께 적는다.

## 5. 이 대조가 바꾼 것 (같은 날 이행)

1. `dependency-model.md` §3.2의 *"우리가 하려던 것은 대부분 선행 연구가 있다"*는
   이 축에도 그대로 적용된다 — **다시 확인됐다.** 연쇄 의존 자원 계산 자체도 그렇다
   (§3.2: Hirmer 외, Informatik 2014 — 불완전 토폴로지 자동 완성, Winery).
   **우리 것은 연쇄 의존 자원 계산이 아니라 그 위의 조건 분류였고, 거기가 약한 자리였다.**

2. **분류 어휘를 IDL에 정박했다**(`closure.IDL_FORM`). 부류 13 중 **다섯이
   IDL로 표현되고 여덟은 안 된다**. `None`이 곧 결과다 —

   | IDL로 표현됨 | 표현 안 됨 (이유) |
   |---|---|
   | `disjunctive:` → `Or(...)` | `배치 조건:` — 집합 카디널리티·상이성은 IDL 밖 |
   | `network 모드 조건부:` · `스킴 조건부:` → `Requires: IF..THEN` | `수명 조건:` · `동반 정리:` — **시간 축**이 IDL 밖 |
   | `쌍 호환:` · `이름 조건:` → `Relational` | `무방비:` — 입력 제약이 아니라 컨트롤 플레인이 안 막는 지대 |
   | | `server-*` — 의존이 아니라 서버의 기본값·합성 행위 |

   **안 매달리는 여덟이 이 축의 기여와 겹친다** — 시간 축(삭제·수명)과 무방비.

3. **정박이 새 실측 질문을 냈다.** IDL은 `Or`(적어도 하나)와 `OnlyOne`(정확히
   하나)을 가르는데 우리 `disjunctive:` 3건은 그 구별을 **안 쟀다**
   (`azure loadBalancer`에 subnet과 publicIp를 둘 다 주면 되는가). 지금은 더 약한
   `Or`로 적어 두었고, 테스트가 그 미결을 지운 채로 두지 못하게 한다.

4. **분류 어휘의 오염 둘을 걷어냈다.** `("ALB는", "detail")`은 부류가 아니라 한
   사례의 원문 조각이었고, `("EXTERNAL 스킴 실측", "detail")`은 **0건을 분류하는
   빈 범주**였다(저장소가 다른 축에서 이미 금지하던 것). 앞의 것 때문에 118주장
   중 **유일하게 부류 접두사 없는 술어**가 있었고, 지금은 `배치 조건:`을 단다.
   불변식 넷을 `test_depkb_closure.py`가 지킨다.

5. **"판정 안 한다"의 절반이 "못 한다"였다.** 실측 규칙 여덟 중 **일곱**이 계획에
   없는 정보(가용영역·존·시점)를 요구한다. 원칙(규칙을 코드로 다시 적지 않는다)
   뒤에 **칸을 열면 닫히는 일곱 건**이 숨어 있었다. 이제 규칙마다 *"안 한다"*와
   *"못 한다 — 계획에 AZ가 없다"*를 갈라 내고, 그것이 곧 계획 형식에 대한 요구
   목록이 된다.

6. 논문에서 기여를 적을 때 *"축을 나눈 것"*·*"연쇄 의존 자원을 계산한 것"*을 새것으로 쓰면
   안 된다. 새것은 **삭제/수명 축 · 실측 획득 · 3사 반전 · 계획 대조**다.

## 6. 범위 선언 (2026-08-01 결정)

같은 날 사용자 결정으로 경계를 확정했다 — `depkb/vocabulary.py`의 `OUT_OF_SCOPE`:

| 안 하는 것 | 사유 |
|---|---|
| 관리형 상품 서비스 | 상품이 CSP마다 수십 종이고 계속 는다. 우리 축은 (자원 × CSP × 질문)이고 한 칸이 클라우드 왕복 수 분이라 **축이 발산한다** — 24종·118주장에 라운드 52개가 들었다 |
| 외부 시스템 | 우리가 만들지도 지우지도 않으므로 생성·삭제 질문이 성립하지 않는다 |
| 서버리스 런타임 | 배치를 프로바이더가 정해 우리가 놓을 것이 없다 — 연쇄 의존 계산의 시작 리소스가 아니다 |

**"미룬다"를 "안 한다"로 바꾼 것이 요점이다.** `vocabulary.py`는 그전까지
*"다음 수직 절단면으로 미룬다 — 배제가 아니라 순서다"*라고 적고 있었고, 그 문장이
남아 있는 한 범위가 영영 안 닫힌다. 대조기도 이제 **경계(`out-of-scope`)와
공백(`out-of-vocabulary`)을 갈라 센다** — 섞으면 무엇을 더 재야 하는지가 안 보인다.

## 출처

- Yang T., Li B., Shen J., Su Y., Yang Y., Lyu M.R. *Managing Service Dependency for
  Cloud Reliability: The Industrial Practice.* ISSREW 2022. arXiv:2210.06249
- Atlidakis V., Godefroid P., Polishchuk M. *RESTler: Stateful REST API Fuzzing.*
  ICSE 2019
- Martin-Lopez A., Segura S., Ruiz-Cortés A. *RESTest: Black-Box Constraint-Based
  Testing of RESTful Web APIs.* ICSOC 2020. doi:10.1007/978-3-030-65310-1_33
- Verdet A. 외. *Assessing the adoption of security policies by developers in
  terraform across different cloud providers.* Empirical Software Engineering 2024.
  doi:10.1007/s10664-024-10610-0
- (이미 §3에 있음) Bellendorf & Mann, Computing 2019 · Nekrasov 외, TOSEM
  doi:10.1145/3817608 · OASIS TOSCA 1.3 / 2.0
