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

`cases/suite.json`은 P1~P3과 세 CSP를 교차한 9개 입력, 개발·홀드아웃 분리, 반복 횟수와
정규화 JSON의 SHA-256을 고정한다. 줄바꿈 형식은 해시에 영향을 주지 않는다. P4 예산 충돌은 만족 가능한 최종 구현물이 없으므로 별도 계획 평가에서
다룬다. 구현·프롬프트를 개선할 때 홀드아웃 P3 산출물을 열어 보지 않는다.
