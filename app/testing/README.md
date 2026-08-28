# 테스팅 단계

`app.testing`은 EasyDep 저장소 자체를 테스트하는 디렉터리가 아니다. 구현 단계가 만든
애플리케이션을 별도의 입력으로 받아 정적 검사, IaC 검사와 실제 컨테이너 동작을 확인하는
파이프라인이다. 저장소 자체의 회귀 테스트는 루트 `tests/`에 있다.

## 실행 흐름

```text
구현 job 산출물
  → 입력 산출물과 실행 조건 확인
  → 정적 소스·구성 검사
  → Terraform을 포함한 IaC 검사
  → 애플리케이션 컨테이너 빌드·기동
  → 동적 기능 검사
  → 단계별 증거와 최종 testing state 저장
```

## 디렉터리 지도

| 위치 | 역할 |
|---|---|
| `api.py` | testing job을 생성·조회하는 HTTP 경계 |
| `graphs/testing_graph.py` | 검사 순서와 상태 전이를 정의 |
| `nodes/` | 정적·IaC·동적 검사를 한 단계씩 수행하는 graph node |
| `runtime/` | subprocess, Docker와 컨테이너를 실행하는 adapter |
| `schemas/testing_state.py` | 저장하고 이어서 실행할 testing state |
| `utils/` | artifact 선택, 요구사항 근거, 정적 분석과 보안 검사 도우미 |

## 계약

- **입력:** `COMPLETED` 상태인 구현 job ID와 그 산출물 경로.
- **출력:** 검사별 상태, 명령 증거, 로그와 최종 성공/실패 판정.
- **실행하면서 바꾸는 것:** subprocess와 Docker container를 실행하고 보고서를 저장한다.
- **이 단계에서 수정하지 않는 것:** 설계나 구현 LLM의 내부 state와 이미 생성된 소스의 성공 상태.
- **주요 실패 원인:** 입력 누락, build 실패, timeout, 컨테이너 비정상 종료, 요구사항과 동작 불일치.

동적 검사 실패와 테스트 인프라 실패는 구분한다. 예를 들어 포트가 열리지 않은 것은 앱 실패일
수 있지만 Docker daemon에 접근하지 못한 것은 실행 환경 실패다. 보고서에는 재시도 여부를
결정할 수 있도록 원래 명령과 종료 원인을 남긴다.
