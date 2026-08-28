# 설계 단계

`app.design`은 확정된 요구사항을 구현 전에 확인할 수 있는 구체적인 약속으로 바꾼다. 여기서
말하는 “설계”는 그림만 뜻하지 않는다. 클래스, 호출 순서, API, 데이터와 배포 구조를
Pydantic schema로 검증한 모델로 보존한다. PlantUML 그림과 OpenAPI 문서는 이 데이터를
사람이나 다른 도구가 읽기 쉬운 모습으로 바꾼 결과다.

## 생성 순서

```text
요구사항과 유스케이스 명세
  → 클래스: BCE 클래스, 메서드와 서로의 호출 관계
  → 시퀀스: 검사를 통과한 호출 관계를 시간순 메시지로 변환
  → API: Boundary·Control 메서드를 HTTP 요청·응답 규칙으로 변환
  → ERD: Entity와 값 객체를 테이블·컬럼·관계로 변환
  → 배포: 애플리케이션과 필요한 자원을 workload와 AWS·Azure·GCP 자원 계획으로 변환
```

앞 단계가 뒤 단계의 입력이므로 사용자 의견으로 클래스 약속이 바뀌면 영향받는 호출 순서,
통신 명세와 데이터베이스 설계도
다시 계산한다. `cascade.py`가 이 범위를 계산하고 graph가 실제 실행 순서를 조율한다.

## 디렉터리 지도

| 위치 | 역할 |
|---|---|
| `api.py` | 설계 생성·수정·readiness HTTP 경계 |
| `graphs/` | 단계 순서, 중단·재개와 state 갱신 |
| `nodes/` | graph에서 설계 함수를 호출하고 결과를 state에 넣는 작은 adapter |
| `schemas/` | 저장 가능한 설계 state와 Pydantic 모델 |
| `services/` | 각 산출물의 생성, normalize, validate, repair와 문서 변환 |
| `contracts/` | 다른 패키지가 읽어도 되는 공개 설계 모델 |
| `validation.py` | 구현 진입 전에 전체 산출물의 준비 상태를 모음 |
| `session_store.py` | 설계 세션의 저장·복원 |
| `progress.py` | 클래스 생성 중간 결과를 UI로 전달하는 callback 경계 |

서비스별 자세한 책임은 [설계 서비스 구조](services/README.md)를 참고한다.

## LLM과 일반 코드가 나누어 맡는 일

- 후보를 제안하거나 자연어 feedback을 해석하는 일은 LLM이 맡을 수 있다.
- 이름과 데이터 형식 정리, 참조 확인, 순서 검사와 문서 변환은 일반 코드가 맡는다.
- LLM이 만든 후보는 validator를 통과하기 전까지 정식 산출물이 아니다.
- repair 때에는 앞서 거절된 후보와 오류 이유를 함께 보여 준다. 같은 실패 후보가 반복되면
  횟수를 더 늘리는 대신 더 진행할 수 없는 상태임을 알려야 한다.

## 계약

- **입력:** 요구사항 state, resource contract, 선택적 기존 설계와 사용자 피드백.
- **출력:** 버전이 붙은 설계 모델, PlantUML/OpenAPI 문서, 구현 readiness 보고서.
- **실행하면서 바꾸는 것:** LLM 호출, 진행 preview 발행, 설계 session과 산출물 저장.
- **서비스에서 직접 사용하지 않는 것:** workspace 명령, repository와 구현 agent 내부 코드.
- **주요 실패 원인:** schema 오류, 존재하지 않는 타입·메서드 참조, 호출 순서나 값의 출처 불일치, 같은 실패 후보 반복.

## 처음 디버깅할 때

1. 설계 session의 `stage`와 `ArchitectureState`의 해당 산출물 필드를 확인한다.
2. renderer가 만든 그림보다 Pydantic 검사를 통과한 JSON 모델을 먼저 확인한다.
3. finding의 `rule_id`와 `location`으로 소유 validator를 찾는다.
4. LLM 원문보다 normalize 이후 후보가 어떤 validator에서 거절됐는지 확인한다.
5. 피드백 cascade가 관련 없는 산출물까지 다시 만들지 않았는지 확인한다.
