# 설계 서비스 구조

이 디렉터리는 요구사항에서 확정된 입력을 설계 산출물로 바꾸는 도메인 서비스다. 각
산출물 디렉터리는 자신이 저장하는 구조화 모델과 그 모델의 결정론적 표현을 소유한다.
상위 graph는 실행 순서와 상태 직렬화만 조율하며, 산출물 내부 규칙을 대신 구현하지 않는다.

## 전체 데이터 흐름

```text
동결된 유스케이스 명세
  └─ class_diagram: BCE 구조·연산·호출 협업을 확정
       ├─ sequence_diagram: 협업을 호출/반환 메시지로 투영
       ├─ api_spec: Boundary·Control 계약에서 API 모델 생성
       └─ erd: Entity·구조 타입에서 논리 데이터 모델 투영
            └─ deployment_diagram: 앞선 산출물과 리소스 입력을 배포 모델로 변환
```

구조화 모델이 수정과 저장의 기준이다. PlantUML과 OpenAPI 문서는 저장 모델에서 다시 만들 수 있는
표현이며, 렌더링 결과를 역으로 파싱해 모델을 수정하지 않는다.

## 디렉터리 책임

| 디렉터리 | 소유하는 결정 | LLM 사용 | 결정론적 출력 |
|---|---|---:|---|
| `class_diagram` | BCE 클래스, 타입, 연산, 호출 순서와 값 출처 | 사용 | `BCEModel`, 클래스 PlantUML |
| `sequence_diagram` | 수락된 협업의 메시지·fragment 표현 | 사용하지 않음 | `SequenceCollection`, 시퀀스 PlantUML |
| `api_spec` | 엔드포인트·스키마와 클래스 연산 binding | 사용 | API 모델, OpenAPI |
| `erd` | Entity를 테이블·키·관계로 매핑 | 피드백 수정에 사용 | 논리 데이터 모델, ERD PlantUML |
| `deployment_diagram` | 워크로드, 연결, CSP 리소스 계획 | 사용 | 배포 bundle과 PlantUML |
| `common` | 필드·다중성·PlantUML·구조화 응답 공통 기술 | 직접 사용하지 않음 | 다른 서비스가 쓰는 순수 도우미 |

`common`은 독립된 설계 단계가 아니다. 특정 산출물의 업무 규칙이나 저장 모델을 이곳으로
옮기지 않는다. 두 산출물 이상이 같은 형식 처리 규칙을 실제로 공유할 때만 기술 도우미를
둔다.

## 경계 규칙

- graph adapter만 원시 `ArchitectureState`를 읽고 저장 JSON으로 직렬화한다.
- 서비스 공개 함수는 Pydantic 모델이나 frozen dataclass를 주고받는다.
- 문서에 명시한 호환 facade는 이전 checkpoint와 저장소 내부 호출을 위해 legacy dict나
  PlantUML을 받을 수 있다. facade는 canonical typed 서비스에 위임하며 자체 규칙이나
  prompt를 소유하지 않는다.
- LLM 응답은 호출 위치에서 Pydantic schema로 검증한 뒤에만 다음 단계로 전달한다.
- 검증 함수는 모델을 수정하거나 LLM을 호출하지 않는다. repair 여부와 예산은 소유
  서비스가 결정한다.
- renderer는 생성 서비스를 역참조하지 않는다. 저장 모델 또는 전용 projection만 읽는다.
- 식별자, prompt, 런타임 오류 메시지는 영어로 유지하고 사람용 문서와 설명은 한국어로 쓴다.

## 상세 문서

- [클래스 설계 서비스](class_diagram/README.md)
- [클래스 설계 검증](class_diagram/validation/README.md)
- [시퀀스 투영과 검증](sequence_diagram/README.md)
- [API 명세 서비스](api_spec/README.md)
- [ERD 수정과 논리 모델 투영](erd/README.md)
- [배포 WorkloadGraph 생성과 수정](deployment_diagram/README.md)
- [클래스·시퀀스 생성 로직](../../../docs/class-design-pipeline.md)
- [클래스 설계 코드 규칙](../../../docs/class-design-code-conventions.md)

## 변경할 때 확인할 것

1. 저장 JSON alias와 체크포인트가 이전 모델을 그대로 읽는가.
2. LLM operation 이름, 호출 횟수, 병렬도와 repair 범위가 의도대로 유지되는가.
3. 새 검사가 자신의 `rule_id`만 발생시키고 입력을 변경하지 않는가.
4. projection이 동일 입력에 동일한 결과를 내는가.
5. 이 README와 해당 산출물 README의 입력·출력 예제가 실제 schema와 일치하는가.
