# 평가

이 디렉터리는 EasyDep의 효과를 검증하는 입력, 채점 코드와 비교 대상을 모아 둔다.
제품 실행 코드는 `app/`에, 모든 실행 결과는 `artifacts/runs/`에 저장한다.

- `easydep/`: EasyDep 자체를 단계별로 평가하는 입력, 정답과 채점기
- `baselines/`: 동일 입력과 LLM 설정으로 실행하는 LLM CoT 및 MetaGPT 비교군
- `class_design_evaluation.py`: 동결된 수강신청 요구사항·명세를 재사용해 클래스 후보를
  구조·참조·호출·입력 출처 및 선택적 순차 산출물과 비교하는 오프라인 도구
- `class_design_optimization.py`: 이미 생성된 9-cell artifact를 네트워크 없이 판정하는 도구
- `class_design_optimization_run.py`: frozen E1을 실제 provider로 최대 9회 생성하고 단계별
  설정·token/cap gate와 accepted-unit cold/warm 동작을 기록하는 실행기

새 하위 디렉터리는 독립적인 평가 목적이나 비교 방법이 생길 때만 추가한다. 실행 결과나
캐시는 이곳에 커밋하지 않는다.

실행 ID와 manifest 규칙은 `artifacts/README.md`를 따른다. EasyDep 기능 비교 시 variant는
`full`, `no-cloud-kb`처럼 기능 차이를 명시하고, CoT·MetaGPT는 `standard`를 사용한다.

클래스 설계 비교의 범위와 정성 검토 기준은
[`docs/class-design-pipeline.md`](../docs/class-design-pipeline.md)에 있다. 이 비교는 기존
클래스 다이어그램의 정확한 클래스 이름·개수·해시를 정답으로 사용하지 않는다.
