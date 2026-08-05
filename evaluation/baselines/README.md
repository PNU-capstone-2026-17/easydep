# 비교 기준선

EasyDep과 비교할 LLM CoT 및 MetaGPT 실행 코드를 둔다. 모든 비교군은 같은 케이스 JSON과
저장소 루트 `.env`의 `MODEL`, `BASE_URL`, `API_KEY`, `TEMPERATURE`, `SEED`를 사용한다.
실행 결과는 `artifacts/evaluations/baselines/`에 저장하며 Git에는 포함하지 않는다.

## 구성

- `cot.py`: 단일 LLM이 요구사항부터 테스트 계획까지 순차적으로 생성하는 기준선
- `metagpt.py`: MetaGPT 0.8.2 Software Company 기준선
- `cases/`: 비교군이 공통으로 사용하는 입력
- `verify.py`: 산출물에서 저장소·클라우드 관련 항목을 분리해 확인하는 도구

## 실행

MetaGPT는 Python 3.11 가상환경을 한 번 준비한다.

```powershell
evaluation/baselines/setup_metagpt.ps1
```

API 호출 없는 설정 확인:

```powershell
python -m evaluation.baselines.cot evaluation/baselines/cases/p1-stateless-detailed.json --dry-run
python -m evaluation.baselines.metagpt evaluation/baselines/cases/p1-stateless-detailed.json --dry-run
```

실제 실행은 `--dry-run`만 제거한다. 케이스에는 `caseId`, `requirements`,
`cloudConstraints`, `scope`가 필요하다.
