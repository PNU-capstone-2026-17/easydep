# 공식 근거 기반 자동 판정 절차

## 전환 이유

두 비전문 검토자가 수백 개 CSP 스키마를 독립 분류하는 방식은 검토자 수만 늘릴 뿐
전문성이나 정답성을 보장하지 않는다. 리소스 모델의 핵심 판정은 벤더가 발행한 수명주기
스키마, 참조 스키마, 매뉴얼과 재현 실험으로 옮긴다. 사람은 서로 충돌하는 고영향 근거만
확인한다.

## 증거 사다리

| 주장 | 자동 승인 근거 | 승인하지 않는 근거 |
|---|---|---|
| 리소스 경계 | 공식 스키마의 독립 식별자와 create/read 연산 | 이름 유사성, LLM 분류 |
| 의존성 존재 | 공식 참조 필드·공식 매뉴얼·제어면 관측 | 출처 없는 공통 이름만 존재 |
| 필수 의존성 | 공식 규범적 선행조건 또는 3회 제거–실패–복구 | 스키마 참조만 존재 |

AWS는 Cloud Control API/CloudFormation resource type schema의 identifier와 CRUD-L handler를,
Azure는 Resource Provider REST/OpenAPI의 resource ID·경로·연산을, GCP는 Config Connector
CRD의 리소스 종류와 `*Ref` 필드를 우선 사용한다. 현재 Botocore와 Compute Discovery
관측은 공식 보조 근거지만 독립 리소스 경계를 단독 확정하지 않는다.

## 사람과 LLM의 역할

LLM은 공식 문서에서 후보 문장과 source locator를 추출할 수 있지만 판정 규칙을 바꾸지
못한다. 해시가 고정된 원문에 실제 구절이 있는지 기계적으로 확인한 뒤에만 증거 카드에
들어간다. 서로 지지·반박하는 고정 근거가 동시에 있을 때만
`humanReviewRequired=true`가 된다. 검토자는 CSP 지식을 기억으로 답하지 않고 카드에
포함된 두 근거가 같은 조건·API 버전·리소스를 말하는지만 확인한다.

기존 Native v2 빈 검토 양식은 감사 자료로 보존하지만 더 이상 모델 동결의 필수 입력이
아니다. 새 동결 모델은 규칙 버전, 모든 증거 카드, 공식 출처 해시, 미해결 예외를 기록한다.

## 재현 명령과 현재 범위

다음 명령은 Docker-on-VM 범위에서 선택한 CSP의 공식 모델이 제공하는 독립 식별자와
create/read 연산을 대조하고 동결 모델을 다시 만든다.

```powershell
python -m evaluation.research_protocol.commands.build_evidence_models
```

현재 자동 확정된 경계는 AWS 8개, Azure 7개, GCP 12개다. Azure의 로드 밸런서 하위
구성처럼 별도 create/read를 모두 갖추지 않은 요소는 독립 경계로 억지로 분리하지
않고 `embedded` 실현으로 보존한다. 반대로 GCP 글로벌 외부 HTTPS 로드밸런서는
forwarding rule, target HTTPS proxy, URL map, backend service, instance group, health check,
certificate가 함께 기능을 실현하는 `composite-member`로 기록한다. 참조 관계의 필수성은
공식 규범 문장이나 앱 기능을 포함한 제거–복구 실험 없이는 확정하지 않는다.

## 생성 가능성과 기능 유효성의 분리

제어면의 생성 성공은 앱이 작동한다는 증거가 아니다. 문서로 필수성이 확정되지 않은
의존성의 개입 실험은 `dependency-experiment-plan.json`의 고정 순서를 따른다. 기준 구성과
개입 구성 모두에서 제어면 안정화, VM·컨테이너 기동, `/readyz`, 대표 업무 요청을 서로
다른 관측층으로 기록한다. 따라서 결과는 생성 차단, 런타임 차단, 기능 차단, 영향 없음으로
구분된다. 의존성을 복원한 뒤 같은 기능 오라클이 회복되어야 인과 근거로 인정한다.

시간 예산 초과와 CSP 스케줄러 지연은 각각 `budgetCensored`, `schedulerDelayed`로 남기고
의존성 실패로 세지 않는다. 세 번의 독립 반복에서 같은 제거–실패–복구 결과가 나올 때만
실험으로 필수성을 확정한다.
