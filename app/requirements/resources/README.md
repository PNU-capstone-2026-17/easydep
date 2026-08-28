# Requirements resource 경계

`app.requirements.resources`는 요구사항에서 배포 capability와 사용자의
클라우드 제약을 읽고, 근거·질문을 보존한 `RESOURCE_SPEC`으로 전환하는
단계를 소유한다. 설계 리소스를 선택하거나 구현 용량을 계산하지 않는다.

## 모듈 책임

- `capability_contract.py`: calibration, 안전 gate, 안정 capability ID 링크와
  accepted projection을 결정론적으로 판정한다.
- `capability_extraction.py`: classified requirement에서 capability proposal을 설정된
  표본 수만큼 호출하고 근거·표본 합의·policy로 accepted result를 만든다.
- `extraction.py`: 자유문장 클라우드 제약을 구조화 proposal로 한 번 읽는다.
- `cloud_inputs.py`: capability 분석과 제약 extraction을 최대 2개 worker에서
  겹친 뒤 고정 순서로 병합한다.
- `service.py`: proposal 근거 대조, provider·region·환율 정규화, 질문,
  `resource_intake` 생성과 `RESOURCE_SPEC` 수여를 담당한다.
- `tools.py`: provider/workload/region/환율/검색 도구를 제공한다.
- `cloud_contract.py`, `input_registry.py`, `resource_contract.py`,
  `resource_spec.schema.json`: 필드·질문·근거·consumer·검증의 단일 원천이다.
- `application_cloud.py`: application/runtime 사실과 cloud capability/binding의
  결정론적 일관성 계약을 소유한다.

## 입력

- `AnalyzeRequest`/`AgentState`의 `classified`, `resource_constraints_text`,
  `initial_cloud_constraints`, `resource_answers`, 선택적
  `resource_constraint_extraction`
- `DeploymentNeedsResult`/`CloudConstraintExtraction` proposal과 선택적 공개
  proposal-call injection
- ResourceSpec JSON Schema, application/runtime 사실, cloud capability/binding,
  region catalog와 환율 도구 결과

## 출력

- `deployment_needs` 및 `CapabilityContract/v1`
- `resource_constraint_extraction` 중간 결과
- 초안·질문·근거·거절값·degradation을 담은 `resource_intake`
- 계약이 유효할 때만 수여하는 `resource_spec`
- 스키마 필드·타입·enum, 질문(`Ask`), gap 및 결정론적 검증 오류

## 부수효과와 호출 범위

- capability extraction은 기본 `settings.capability_samples`번, 명시한
  `sample_count`가 있으면 그 횟수만 구조화 LLM을 호출한다.
- 자유문장 resource extraction은 활성화 시 1번, 비활성화 시 0번
  호출한다. native structured→JSON fallback·retry는 runtime adapter 설정을 따른다.
- cloud input의 두 분기는 2-thread에서 ContextVar 계측을 전파하므로
  네트워크 대기를 겹칠 수 있다. 병합 순서와 출력 순서는 고정된다.
- schema/catalog 조회는 process-local cache를 쓰며 checkpoint·디스크에 캐시를
  저장하지 않는다. 외부 검색·환율 도구는 실제로 선택된 경우에만
  네트워크를 쓴다.

## 보존 중인 tool-agent 경로

`service.py`의 tool binding/agent loop와 `tools.py`는 현재 기본 stage가 호출하지
않는 경로를 포함한다. 호출되지 않는다는 사실만으로 삭제하지 않는다.
실제 dead path라는 별도 근거, 도구 관찰 대체, 호환 영향 검증이 확보될 때까지
생산 경계의 일부로 보존한다.

## 사용하면 안 되는 import

- requirements graph·runner·supervisor·feedback cascade·HTTP API·session store
- artifact repository, design service, implementation service, workspace
- 구체 CSP 리소스 선택·구현 용량 계산

contracts, runtime structured adapter/telemetry, config, schemas, requirements knowledge,
`app.cloudkb`의 catalog primitive만 필요한 방향으로 참조한다.

## 실패 조건

- capability 호출이 전부 실패하면 가공 need를 만들지 않고 빈 contract와
  degradation을 반환한다.
- resource proposal 호출 실패·비활성화는 `failed`/`disabled` 중간 결과와
  `resource_intake` 질문으로 강등하며 반쪽 `resource_spec`을 수여하지 않는다.
- 알 수 없는 필드/타입, 필수값 누락, 잘못된 enum·object, 근거 불일치,
  consumer 없는 질문, schema/registry 불일치는 호출자가 고칠 수 있는
  오류·질문으로 노출한다.

`input_registry`는 무엇을 물을지와 왜 필요한지를, `cloud_contract`는 값의 기계적
모양을 담당한다. 둘을 합치거나 단계에 별도 계약을 만들지 않는다.

`input_registry`의 일부 `Basis(CODE, "app/core/...")` 값은 기존 저장 JSON과 checkpoint의
감사 식별자를 보존하기 위한 레거시 provenance ID다. 실행 가능한 import나 현재 파일 링크가
아니며, canonical Python 경로를 이 문자열로부터 유도하지 않는다.
