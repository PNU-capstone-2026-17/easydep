# 요구사항-구현 오케스트레이션

이 패키지는 다른 팀원이 소유한 요구사항·설계·구현 에이전트의 내부 코드를
수정하지 않고 호출한다. 테스팅 에이전트는 아직 연결하지 않았다.

## 단계 구성

1. 요구사항 분석에서 추적 가능한 유스케이스와 `resource_spec`을 생성한다.
2. 설계에서 클래스·시퀀스·OpenAPI·ERD·논리 배포 산출물을 생성한다.
3. `depkb`로 Docker-on-VM 배포 다이어그램을 보완한다.
4. 구현 시작 전에 사용자 승인을 기다린다.
5. 승인 후 LLM으로 임시 인프라 용량·비용 권고를 생성한다. 이 값은 실측값이
   아니며 향후 성능·비용 추천기로 교체한다.
6. 구현 워크플로가 코드를 생성하고 컴파일·테스트·수정하며 체크포인트를 남긴다.

주요 재개 함수는 `graph.py`에 있다.

- `complete_design(run_id)`: 구현 시작 경계까지 설계를 진행한다.
- `start_implementation_from_completed_design(design_run_id)`: 저장된 설계 결과로
  구현 실행을 시작한다.
- `complete_implementation(run_id)`: 구현 전송을 승인하고 완료될 때까지 재개한다.

구현 어댑터는 저장소 루트의 `.env`에 있는 모델 설정을 작업자에게 전달하고,
외부 LLM 전송 승인 절차를 유지한다. 현재 BCE 생성기와 설계 결과 사이의 계약을
맞추기 위해 타입이 없는 설계 속성과 매개변수는 임시로 `String`으로 변환한다.
이는 설계 결정이 아니라 호환을 위한 가정이다.

## 산출물

일반 실행의 산출물은 다음 구조로 저장한다.

```text
artifacts/orchestration/runs/<run-id>/
├── manifest.json
├── 01-requirements/
├── 02-design/
├── 03-infrastructure/
└── 04-implementation/
```

설계 디렉터리에는 원본 결과와 클래스·시퀀스·ERD·OpenAPI·논리/클라우드 배포
다이어그램을 저장한다. 구현 디렉터리에는 결과 상태, 생성 소스, 테스트,
설정 파일과 실행 보고서를 저장한다. Gradle 캐시와 `build` 같은 재생성 가능한
임시 파일, JVM 크래시 덤프는 제외한다.

## 다중 애플리케이션 설계 평가

성공한 실행을 유지하면서 전체 샘플을 다시 실행하려면 다음 명령을 사용한다.

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m app.core.orchestration.sample_evaluation --resume
```

평가 결과는 `artifacts/orchestration/sample-evaluation/<sample>/`에 저장된다.
각 디렉터리에는 원본 응답, 요구사항·설계 산출물, 배포 다이어그램 두 종류,
제약조건 출처와 구조 검증 결과가 포함된다.

2026-08-05 실제 실행 결과는 다음과 같다.

| 샘플 | 결과 | 요구사항 / 액터 / 유스케이스 |
|---|---|---:|
| shopping_mall | 구조 검사 통과 | 9 / 3 / 4 |
| toystore | 구조 검사 통과 | 30 / 4 / 10 |
| cloud_native_voucher_medium | 구조 검사 통과 | 18 / 5 / 13 |
| note_taking | API 시간 초과, 재실행 필요 | - |
| bank_of_anthos | 호스트 `MemoryError`, 재실행 필요 | - |

오케스트레이션 실행에서는 JVM 메모리 사용을 피하기 위해 PlantUML 구문 검증을
건너뛴다. 산출물에는 이 상태를 `skipped`로 기록하며 구문 검증 통과로 간주하지
않는다. OpenAPI 검증과 다이어그램의 결정론적 변환은 유지한다. 기존 Voucher
샘플에서는 `depkb` 미해결 질문의 문자 인코딩 문제도 발견했다.

## 구현 단계 실제 실행 결과

Shopping Mall 설계를 구현 단계로 재개하여 18개 태스크 중 16개를 완료했다.
남은 wiring 태스크는 이 호스트에서 반복된 JVM 네이티브 메모리 크래시로
중단됐으며, 성공한 태스크의 체크포인트는 재사용할 수 있다. 구현 완료로는
기록하지 않았다.

Gradle을 단일 워커, Serial GC, 128 MiB 힙, 256 KiB 스레드 스택으로 제한한
후에도 문제가 반복됐다. 워크플로는 컴파일·테스트를 우회하지 않았으며,
JVM이 만든 `hs_err_pid`·`replay_pid` 파일을 작업 범위 밖 변경으로 감지해
실행을 실패 처리했다.
