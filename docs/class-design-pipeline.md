# 클래스 설계 파이프라인과 평가

클래스 설계는 동결된 요구사항과 명세를 비교 입력으로 사용한다. 평가가 상류 단계를 다시
실행하거나 LLM을 호출하지 않으며, 현재 유지하는 goldset은 수강신청 `e1-aws` 하나다.
이는 정답 클래스 목록·개수·해시가 아니라 입력 및 상류 체크포인트의 재현성 기준이다.
현재 이 입력에는 UC4 확장 분기의 명세 검증 이슈 하나가 남아 있으므로 정상 E2E의 상류
승인을 뜻하지 않는다. 클래스 단계 평가는 해당 입력을 고정한 비교 실험이다.

```text
동결 요구사항·명세 → 전역 BCE 구조 → 유스케이스 그룹별 협업 → 계약 검증 → 순차 설계
```

- 전역 구조는 `gpt-oss-120b`의 중간 추론 노력으로 BCE 후보와 관계의 큰 틀만 정한다.
  높은 추론 노력은 completion budget을 JSON 없이 reasoning에 모두 쓸 수 있으므로,
  구조 품질은 엄격한 transient 계약과 한 번의 schema repair로 지킨다.
- 유스케이스 그룹별 협업은 중간 노력으로 최대 4개까지 병렬 처리한다. 메시지 우선으로
  호출·반환을 먼저 드러내고, 연산·입력 바인딩으로 투영한다.
- 시나리오가 지속 상태를 읽거나 바꾸면 해당 상태와 동작은 Entity가 소유하고 Control이
  이를 호출한다. 응답·요약·결과 같은 일시 데이터는 Boundary나 Entity가 아니라 참조되는
  DataType으로 둔다.
- 입력의 출처가 여러 개로 모호할 때만 낮은 노력의 유한 후보 선택을 쓴다. 후보 밖의 값을
  새로 만들지 않는다.
- 중간 노력의 의미 검토는 발견 사항만 기록한다. CRC는 책임 응집도를 읽는 보조 렌즈이며
  클래스 생성기나 정답 판정기가 아니다.
- 호출 선택·입력 출처 문제는 해당 그룹만 계약 위반·호출 telemetry와 함께 재개·수리한다.
  필요한 단계에 대응하는 연산 자체가 없으면 협업에서 억지로 만들지 않고 전역 구조를 다시
  설계한다. 성공한 그룹은 구조가 유지되는 동안 재사용한다.

## 비교 평가

`python -m evaluation.class_design_evaluation`은 저장된 클래스 모델과 선택적 순차 모델을
비교한다. 요구사항·명세 체크포인트의 digest를 먼저 검증하고, 다음 기계 게이트만 센다.

- BCE 스키마와 관계·유스케이스 참조 무결성
- 연산 ID, 매개변수 타입, 입력 출처와 유한 호출 순서
- 제공된 경우 순차 모델의 클래스 호출·반환 및 단계 정합성

클래스 이름·개수·관계 모양·프롬프트 문자열·산출물 해시는 비교 통과 조건이 아니다. 결과의
finding 수 차이도 승자를 자동 선택하지 않는다. 사람 검토는 요구사항 충실성, 책임 응집도,
거대·중복 클래스, 자연스러운 Entity/DataType 선택, 연산·매개변수·반환의 명료성, 관계 절약,
호출·반환 가독성을 기록한다.

```powershell
python -m evaluation.class_design_evaluation `
  --baseline-class <baseline-class-model.json> `
  --candidate-class <candidate-class-model.json> `
  --baseline-sequence <baseline-sequence-model.json> `
  --candidate-sequence <candidate-sequence-model.json> `
  --output artifacts/runs/<run-id>/class-design-comparison.json
```

네트워크 호출은 없고, 재개 시에는 기존 산출물의 체크포인트 digest와 그룹 telemetry를 확인한 뒤
실패한 그룹만 다시 평가한다.
