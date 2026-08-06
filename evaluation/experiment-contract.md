# 종단 비교실험 계약

본실험 전에 입력·프롬프트·평가기·oracle을 동결한다.

## 판정

- 생성 상태: `completed`, `failed`, `timeout`
- 평가 상태: `eligible`, `ineligible`, `unavailable`
- 요구 충족도: 리소스·의존성 점수와 Docker/IaC/업무 API 결과

생성 실패와 평가기 실패를 섞지 않는다. 실패·timeout도 표본과 원출력을 보존하며,
재평가는 최초 생성 상태를 바꾸지 않는다.

## 공통 산출물과 평가

필수 산출물은 소스, 빌드 설정, 테스트, Dockerfile, Terraform이다. 매니페스트와 설계
다이어그램은 선택 사항이다. 세 비교군은 동일한 Docker, OpenTofu, Trivy, Lizard, JaCoCo,
고정 HTTP oracle로 평가한다. `/health`와 업무 API를 모두 통과해야
`experimentEligible=true`이다.

## 변경 규칙

1. 실패를 subject, harness, evaluator, environment로 먼저 분류한다.
2. harness/evaluator 결함은 재현 테스트를 작성한 뒤 수정한다.
3. 선택한 provider 실패를 다른 provider로 자동 보완하지 않는다.
4. 개발군에서만 수정하고 동결 후 홀드아웃 결과로 시스템을 변경하지 않는다.
5. 한 run에는 하나의 run ID와 원시 산출물·평가 이력을 유지한다.

개발군은 P1~P3와 AWS·Azure·GCP의 9개 사례다. 홀드아웃은 원격진료-Azure,
물류-GCP, 파트너보고-AWS 세 도메인 사례다. 각 방식은 사례별 3회 반복한다.
