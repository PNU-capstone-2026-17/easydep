# 종단 비교실험 계약

본실험 전에 입력·프롬프트·평가기·oracle을 동결한다.
`--confirmatory` 실행과 모든 holdout 실행은 연구 준비도 검사를 먼저 통과해야 하며,
프로토콜·결정 앵커·Native v2 모델·capability 정책의 해시를 실행 인덱스에 기록한다.
각 셀은 별도 프로세스와 고유 세션 ID에서 실행해 이전 에이전트 문맥을 재사용하지 않는다.

## 판정

- 기존 생성 호환 상태: `completed`, `failed`, `timeout`
- 대상 결과: `pass`, `fail`, `notObserved`
- 실행 상태: `completed`, `censored`, `infrastructureFailure`
- 검열 사유: 측정시간, 비용, CSP 작업 timeout, throttling, 스케줄 지연, 정리 기한
- 평가 상태: `eligible`, `ineligible`, `unavailable`
- 요구 충족도: 리소스·의존성 점수와 Docker/IaC/업무 API 결과

생성 실패와 평가기 실패를 섞지 않는다. 실패·timeout도 표본과 원출력을 보존하며,
재평가는 최초 생성 상태를 바꾸지 않는다.

각 격리 작업은 사례 경로뿐 아니라 자신이 속한 suite의 오라클 경로도 함께 전달받는다.
따라서 종단·절제·구성요소 연구가 서로 다른 오라클을 사용하더라도 전역 기본 오라클로
조용히 평가되지 않는다. `paired-components` suite는 각 정의된 쌍에 대해 control과
treatment가 AWS·Azure·GCP에 모두 존재하는지 로딩 시 검사하며, 업무 도메인 holdout과
재사용하거나 합산하지 않는다.

45분 측정 창에 도달하거나 CSP 비동기 작업이 끝나지 않은 관측은 대상 실패가 아니라
검열로 기록한다. 측정 종료 후에도 별도의 60분 안전 창에서 정리를 계속한다. 각 bundle의
예상 비용은 10달러, 전체 캠페인은 150달러로 제한하며 이 중 15달러는 정리 예비비로 남긴다.
잔존 리소스가 있으면 같은 provider의 다음 작업을 시작하지 않는다.

## 공통 산출물과 평가

필수 산출물은 소스, 빌드 설정, 테스트, Dockerfile, Terraform이다. 매니페스트와 설계
다이어그램은 선택 사항이다. 세 비교군은 동일한 Docker, OpenTofu, Trivy, Lizard, JaCoCo,
고정 HTTP oracle로 평가한다. `/health`와 업무 API를 모두 통과해야
`experimentEligible=true`이다.

영속성 요구가 있는 사례는 선언된 마운트 경로에 임시 Docker 명명 볼륨을 연결한다. 첫
컨테이너에서 데이터를 쓴 뒤 `SIGTERM`으로 정상 종료하고 컨테이너를 제거한 다음, 같은
볼륨을 연결한 새 컨테이너에서 데이터를 읽는다. 일반 업무 API 성공과 이 재시작 보존
성공을 서로 다른 필드로 기록하며 둘 중 하나라도 실패하면 기능 게이트를 통과하지 못한다.
평가기의 컨테이너·볼륨·이미지는 성공 여부와 무관하게 정리한다. 강제 종료 내구성은 이
재시작 계약의 주장 범위에 포함하지 않으며 필요하면 별도 장애 주입 과제로 측정한다.

## 변경 규칙

1. 실패를 subject, harness, evaluator, environment로 먼저 분류한다.
2. harness/evaluator 결함은 재현 테스트를 작성한 뒤 수정한다.
3. 선택한 provider 실패를 다른 provider로 자동 보완하지 않는다.
4. 개발군에서만 수정하고 동결 후 홀드아웃 결과로 시스템을 변경하지 않는다.
5. 한 run에는 하나의 run ID와 원시 산출물·평가 이력을 유지한다.
6. 429와 5xx만 15초, 30초 간격으로 최대 두 번 재시도한다.
7. Holdout의 동결 모델 결과와 이후 적응 결과를 별도 집계한다.

개발군은 P1~P3와 AWS·Azure·GCP의 9개 사례다. 홀드아웃은 원격진료-Azure,
물류-GCP, 파트너보고-AWS 세 도메인 사례다. 각 방식은 사례별 3회 반복한다.
