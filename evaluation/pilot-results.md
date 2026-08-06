# 종단 파일럿 기록

본실험 결과가 아니라 harness 안정화 기록이다.

| 날짜 | 실행 | 결과 | 판정과 후속 조치 |
|---|---|---|---|
| 2026-08-06 | modular EasyDep, P1-GCP, 1회 | 구현 골격 단계의 `compileJava + bootJar`가 멤버 코드 내부 300초 제한에서 timeout | harness 실패. 골격 단계의 중복 컴파일을 끄고 testing 단계에서 한 번만 실행하도록 수정 |
| 2026-08-06 | modular EasyDep, P1-GCP, 수정 후 1회 | 네 단계 생성 완료, OpenTofu 검증 통과, `experimentEligible=false` | 계약 실패. 테스트가 0개였고 무상태 앱에 별도 데이터 디스크가 생성됨. 구현 전 수용 테스트 provider와 명시적 영속성 금지 입력 추가 |
| 2026-08-06 | modular EasyDep, P1-GCP, 계약 수정 후 최종 1회 | 요구사항·설계 완료 후 멤버 구현 골격의 `puml2code-bce`가 Handlebars 예외로 종료 | 멤버 구현 경계 실패. 수용 테스트와 VM 전달 단계에는 도달하지 못했으며 자동 repair·재실행 없이 중단 |
| 2026-08-07 | LLM scaffold EasyDep, P1-GCP | 두 출력 계약 불일치를 수정한 뒤 네 단계와 공통 평가 완료, `experimentEligible=true` | Gradle 테스트, Docker health, 업무 API 2건, OpenTofu, IaC 의미 검증 12/12 통과. 무상태 별도 데이터 디스크 없음 |

첫 실패는 애플리케이션 품질 점수에 포함하지 않는다. 두 번째 결과는 실제 산출물 실패로
보존한다. 계약 수정 후 최종 실행은 멤버 구현 골격에서 실패했다. 따라서 본실험은 아직
멤버 구현기를 대신하는 명시적 LLM scaffold provider로 종단 gate를 통과했다. 이는 임시
실험 경로이며 멤버 provider의 기본값이나 실패 시 자동 fallback으로 사용하지 않는다.
