# 비교 기준선

EasyDep과 비교할 LLM CoT 및 MetaGPT 실행 코드를 둔다. 모든 비교군은 같은 케이스 JSON과
저장소 루트 `.env`의 `MODEL`, `BASE_URL`, `API_KEY`, `TEMPERATURE`, `SEED`를 사용한다.
실행 결과는 `artifacts/runs/<run-id>/`에 저장하며 Git에는 포함하지 않는다.

## 구성

- `cot.py`: 단일 LLM이 요구사항부터 테스트 계획까지 순차적으로 생성하는 기준선
- `metagpt.py`: MetaGPT 0.8.2 Software Company 기준선
- `cases/`: 비교군이 공통으로 사용하는 입력
- `verify.py`: 공통 최종 구현물 평가기의 호환 CLI

## 평가 산출물

기준선에 EasyDep 전용 중간 형식을 강제하지 않는다. 공통 필수 평가는 생성 저장소의
애플리케이션 소스코드, Dockerfile, Terraform IaC를 대상으로 하며 배포 매니페스트는 선택 사항이다.
배포 다이어그램과 `cloud-plan.json`은 필수 산출물이 아니다.
MetaGPT의 고유 workspace는 원본으로 보존하고, 생성 프로젝트는 공통 평가를 위해 같은
실행 디렉터리의 `repo/`로 복사한다. Terraform이 누락된 출력도 보정하지 않고 그대로
평가 실패로 기록한다.

## 실행

MetaGPT는 Python 3.11 가상환경을 한 번 준비한다.

```powershell
evaluation/baselines/setup_metagpt.ps1
```

API 호출 없는 설정 확인:

```powershell
python -m evaluation.baselines.cot evaluation/baselines/cases/p1-stateless-aws.json --dry-run
python -m evaluation.baselines.metagpt evaluation/baselines/cases/p1-stateless-aws.json --dry-run
```

실제 실행은 `--dry-run`만 제거한다. 케이스에는 `caseId`, `requirements`,
`cloudConstraints`, `scope`가 필요하다.

## 자원 제한 확인 파일럿

확인적 본 실험 전에 같은 사례·같은 반복의 세 실험군을 직렬로 한 번씩 실행한다. 이
파일럿은 실행 환경, 시간 제한, 프로세스 트리 정리, Docker 평가 가능성과 디스크 증가량을
검증하기 위한 것이며 본 실험의 효과 크기나 통계적 결론으로 사용하지 않는다.

Docker 데몬을 사용자가 시작한 뒤 다음 읽기 전용 검사에서 `ready=true`인지 먼저 확인한다.

```powershell
python -m evaluation.experiment --check-environment
```

실행 전에 선택된 세 작업만 출력되는지 확인한다.

```powershell
python -m evaluation.experiment --split development --confirmatory `
  --case P1-gcp --repetition 1 --limit 3 --print-schedule
```

같은 인자로 실제 파일럿을 실행한다. 작업은 한 번에 하나만 실행되며 각 작업의 시간 제한은
30분이다. 파일럿 결과는 개발 자료로만 표시한다.

```powershell
python -m evaluation.experiment --split development --confirmatory `
  --case P1-gcp --repetition 1 --limit 3 --timeout-seconds 1800
```

파일럿 종료 후 실행 인덱스에서 세 작업 모두 종료 상태인지, 하위 프로세스가 남지 않았는지,
Docker 평가가 `unavailable`이 아닌지, 실행 전후 여유 디스크가 5 GiB 이상인지 확인한다.
확인되지 않은 상태에서는 다음 묶음이나 81회 개발 본 실험을 시작하지 않는다.

`cases/suite.json`은 P1~P3과 세 CSP를 교차한 9개 입력, 개발·홀드아웃 분리, 반복 횟수와
정규화 JSON의 SHA-256을 고정한다. 줄바꿈 형식은 해시에 영향을 주지 않는다. P4 예산 충돌은 만족 가능한 최종 구현물이 없으므로 별도 계획 평가에서
다룬다. H1~H3은 업무 도메인 홀드아웃이며 새로운 의존성 유형 홀드아웃으로 해석하지 않는다.

## EasyDep 내부 절제실험

종단 시스템 비교와 별도로 `cases/ablation-suite.json`은 `full`, `no-depkb`,
`no-verification` 세 조건을 고정한다. `no-verification`에서도 검사는 측정용으로 실행하지만
그 결과를 API 또는 IaC 생성 모델에 수정 피드백으로 보내지 않는다.

```powershell
python -m evaluation.experiment --study ablation --split development `
  --case P2-azure --repetition 1 --print-schedule
```

출력에는 같은 사례의 세 EasyDep 조건만 있어야 한다. 처치 충실도 단위시험과 P1~P3 개발
게이트가 통과한 뒤에만 `--print-schedule`을 제거한다. 절제실험 결과는 CoT·MetaGPT 시스템
비교와 같은 실험군으로 합산하지 않는다.
