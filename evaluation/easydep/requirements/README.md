# 요구사항 분석 평가

EasyDep의 Docker-on-VM 요구사항 분석 결과를 평가한다. 합성 입력에는 소프트웨어 요구사항과
클라우드 제약만 포함하며, 정답인 `oracle.json`은 에이전트 입력으로 전달하지 않는다.

- `inputs/`: 개발·홀드아웃·도메인 확장용 입력
- `oracle.json`: 입력별 기대 요소
- `suite.json`: 평가 분할과 고정 해시
- `evaluate.py`: 결정론적 채점기
- `run_suite.py`: 분석 실행과 채점을 연결하는 진입점

저장소 루트에서 실행한다.

```powershell
.\.venv\Scripts\python.exe -m evaluation.easydep.requirements.run_suite --split development
.\.venv\Scripts\python.exe -m evaluation.easydep.requirements.run_suite --split holdout
.\.venv\Scripts\python.exe -m evaluation.easydep.requirements.run_suite --split domainExpansion
```

개선 중에는 `development`만 사용하고, 변경안을 고른 후 `holdout`을 실행한다. 결과는
`artifacts/runs/<run-id>/`에 저장되며 manifest의 `purpose`가 `evaluation`으로 기록된다.
