# 요구사항 에이전트 평가 모음

EasyDep의 Docker-on-VM 범위에서 요구사항 에이전트를 평가한다. 애플리케이션
입력에는 요구사항과 클라우드 제약조건만 포함한다. 기대 사실은 `oracle.json`에
두며 에이전트 프롬프트에는 절대 포함하지 않는다.

## 오버피팅 방지 절차

1. 프롬프트·스키마·수정 로직을 변경할 때는 `development` 분할만 사용한다.
2. 애플리케이션 이름·액터·요구사항 문장을 프롬프트나 규칙에 복사하지 않는다.
3. 최적화 전에 `holdout` 입력과 SHA-256 해시를 고정한다.
4. 개발 결과로 변경안을 선정한 후에만 홀드아웃을 실행한다.
5. 애플리케이션 전체 매크로 평균과 개별 결과를 모두 보고한다. 한 앱만
   개선됐다는 이유로 변경안을 선택하지 않는다.
6. 새로운 실패 유형은 개발 사례로 추가한다. 결과를 본 뒤 홀드아웃 사례를
   수정하지 않는다.

오라클에는 요구사항에 명시된 사실만 기록한다. 완전한 정답 유스케이스 모델을
강제하기 위한 것이 아니라 평가를 위한 자료다.

## 실행 방법

저장소 루트에서 실행한다.

```powershell
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split development
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split holdout
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split domainExpansion
```

홀드아웃 명령은 LLM 호출 전에 고정된 해시를 검증한다. 도메인 확장 입력은
`suite.json`에 등록하며 새 사례는 `templates/`의 파일을 복사해 작성한다.
완료된 개선 과정과 결과는 `improvement-report.ko.md`에 정리되어 있다.
