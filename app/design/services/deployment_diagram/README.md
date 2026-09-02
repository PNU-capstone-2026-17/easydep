# 배포 WorkloadGraph 생성과 수정

이 패키지는 수락된 요구사항·설계 산출물에서 배포 가능한 workload와 그 연결을
`WorkloadGraph`로 제안한다. LLM의 결정 범위는 workload, external dependency, interface,
storage, configuration, connection, constraint까지다. VM 배치, provider resource와 IaC는
typed graph가 수락된 뒤 기존 결정론 단계가 만든다.

## 처리 경계

```text
구조화된 요구사항·class·sequence·API·ERD·deployment fact
  → graph adapter의 외부 JSON 검증
  → generate_workload_graph
  → WorkloadGraph
  → 저장 deployment_diagram_model JSON
  → normalize_workload_graph
  → deployment plan
  → provider ResourcePlan
  → deployment_diagram_bundle
       ├─ runtime PlantUML
       ├─ provisioning PlantUML
       └─ implementation의 OpenTofu renderer
```

사용자 피드백은 저장 JSON을 `WorkloadGraph`로 검증한 뒤
`revise_workload_graph`에 전달한다. 수정 결과 이후의 normalize·placement·provider projection은
항상 처음부터 결정론적으로 다시 만든다.

## 공개 입력과 출력

생성 서비스의 canonical 경계는 다음과 같다.

```python
generate_workload_graph(
    scenario_text: str,
    api_spec: dict[str, Any],
    *,
    refined_requirements: Any = None,
    capability_contract: dict[str, Any] | None = None,
    resource_intake: dict[str, Any] | None = None,
    class_model: Any = None,
    sequence_model: Any = None,
    erd_model: Any = None,
    deployment_planning_facts: list[dict[str, Any]] | None = None,
    proposal_call: WorkloadGraphProposalCall | None = None,
) -> WorkloadGraph
```

- structured class·sequence·ERD 모델을 기본 입력으로 사용한다.
- 동일 모델의 PlantUML 중복은 canonical 서비스 입력에 포함하지 않는다.
- 빈 scenario는 외부 호출 없이 빈 typed graph를 반환한다.
- `proposal_call`은 공통 structured adapter와 테스트 대역이 따르는 공개 protocol이다.

피드백 수정 경계는 다음과 같다.

```python
revise_workload_graph(
    current_model: WorkloadGraph,
    feedback: str,
    context_text: str = "",
    targets: set[str] | None = None,
    *,
    proposal_call: WorkloadGraphProposalCall | None = None,
) -> WorkloadGraph
```

빈 feedback은 외부 호출 없이 같은 객체를 반환한다. 유효한 수정은 전체 graph 제안을 한 번
받고 `WorkloadGraph`로 검증한다. 서비스 안에 별도 retry나 semantic repair loop를 추가하지
않는다.

## 저장·checkpoint 계약

`deployment_diagram_model`과 `deployment_workload_graph`의 schema version은
`easydep-workload-graph`다. 최상위 키는 다음과 같이 유지한다.

```json
{
  "schemaVersion": "easydep-workload-graph",
  "workloads": [],
  "externalDependencies": [],
  "connections": [],
  "constraints": [],
  "derivations": []
}
```

graph adapter만 raw checkpoint JSON을 `WorkloadGraph.model_validate`로 읽고
`model_dump()`로 저장한다. bundle은 기존 `easydep-deployment-diagram` shape와
`planningFacts`, `workloadGraph`, `resourceSpec`, `projections` 순서를 유지한다. hydration 결과의
`deployment_diagram_model`, `deployment_workload_graph`, `deployment_plan`,
`deployment_resource_plan` 키도 바뀌지 않는다.

단일 projection은 `selectedTarget`으로 자동 선택한다. 여러 projection은 선택 전에는
`needsInput`이며, 명시 선택 뒤 해당 projection만 다시 계산한다. 같은 graph 안의 workload
endpoint와 same-process 호출은 정규화가 결정하지만, 실제 주소를 알 수 없는 external dependency는
입력으로 남긴다. ERD의 영속 요구는 database engine을 추측하지 않으며, 단일 VM과 명시된
persistent-storage derivation이 함께 있을 때만 workload-owned disk를 선택한다.

`sizing.py`는 기존 cloud catalog의 provider·region 일치 SKU만 읽어 compute-only 월 예상치를
만든다. scale-out은 WorkloadGraph의 `replicationSafety`를 다시 확인하고, 선택된 SKU와 replica는
ResourcePlan에 투영된다.

## LLM 호출과 repair 범위

- scenario가 있는 생성 한 번은 structured proposal 한 번이다.
- feedback이 있는 수정 한 번도 structured proposal 한 번이다.
- schema class의 실제 이름 `WorkloadGraphProposal`을 유지하므로 기존 operation·timing 분류가
  바뀌지 않는다.
- native structured response와 JSON fallback·schema repair는 공통 structured adapter가
  소유한다. 이 서비스는 추가 repair, 병렬 호출, 후보 투표를 만들지 않는다.
- Pydantic 검증을 통과하지 않은 proposal은 저장하거나 planner로 넘기지 않는다.

## 결정론적 하류 호환

수락된 `WorkloadGraph` 이후의 normalization, planning, provider ResourcePlan, runtime binding,
bundle hydration과 PlantUML rendering은 LLM을 호출하지 않는다. 기존 dict candidate와 typed
model의 JSON dump가 같으면 다음 결과도 같다.

- `deployment_diagram_bundle` 외부 JSON
- runtime·provisioning PlantUML 문자열
- ResourcePlan ID, sourceRef, digest와 배열 순서
- implementation이 ResourcePlan에서 렌더한 OpenTofu 파일 이름과 내용

planner 내부 책임은 다음 공개 module로 나뉜다.

| module | 입력 | 출력과 소유 결정 |
|---|---|---|
| `planning_facts.py` | 상류 artifact와 version | fact·provenance·input digest, stale 비교 |
| `normalization.py` | WorkloadGraph candidate와 planning fact | 정규화 graph, issue와 승인 constraint |
| `placement.py` | normalized graph와 planning context | compute placement, storage/network/runtime binding |
| `runtime_binding.py` | graph·DeploymentPlan·구현 관측값 | 비구조 값 binding 또는 재생성 issue |
| `digest.py` | graph·DeploymentPlan·ResourcePlan | 각 structure digest |
| `planner.py` | 기존 공개 import | 위 함수 재노출과 provider ResourcePlan 연결 |

`planner.py`는 외부 호출자가 세부 모듈을 모두 알지 않아도 되게 위 흐름의 공개 진입점을
모은다. `DeploymentPlan`에는 배치와 실행 연결처럼 다음 단계가 읽는 값만 저장한다. 진단용
설명 목록과 별도의 late-binding 복사본은 저장하지 않는다. 구조를 바꾸지 않는 실행 시점 값과
검사 결과는 `structureDigest`에서 제외한다.

## Provider projection과 renderer 경계

provider 단계는 이미 수락된 DeploymentPlan과 WorkloadGraph만 소비한다. 새 workload,
placement, provider 선택을 추론하지 않는다.

| module | 입력 | 출력과 소유 책임 |
|---|---|---|
| `provider_template_generation.py` | DeploymentPlan·WorkloadGraph·provider·region | ResourcePlan node·reference·binding과 structure digest |
| `provider_template_validation.py` | ResourcePlan | schema, ID, reference, network, compute, storage 완결성 검사 |
| `runtime_renderer.py` | 단일 projection bundle | workload placement·traffic·mount 중심 runtime PlantUML |
| `provisioning_renderer.py` | 단일 projection bundle | IaC prerequisite·association 중심 provisioning PlantUML |
| `renderer_support.py` | bundle·ResourcePlan | 두 renderer가 공유하는 읽기 전용 context와 표기 도우미 |

`provider_template.py`와 `provider_plantuml.py`는 호출 위치에서 generation·validation·renderer의
세부 파일을 알 필요가 없도록 공개 진입점만 모은다. 자체 변환 규칙은 없다. implementation의
OpenTofu renderer는 완결된 ResourcePlan만 소비하고 provider resource나 dependency를 새로
선택하지 않는다. 이전 저장 형식의 byte 단위 비교 fixture는 현재 계약을 설명하지 못하고 실제
테스트에서도 사용되지 않아 제거했다. 대표 토폴로지 테스트는 현재 타입, reference 완결성,
PlantUML 생성과 파싱 가능한 OpenTofu 결과를 직접 확인한다.

`extractor.extract_deployment_model`과 `reviser.revise_deployment_model`은 이전 내부 호출자를 위한
dict compatibility facade다. facade는 canonical typed 서비스에 위임하고 자체 prompt나 repair
규칙을 소유하지 않는다.

## 부작용

- generate/revise만 주입된 structured LLM adapter를 호출할 수 있다.
- typed model 검증과 이후 planning·rendering은 순수 계산이며 저장소나 checkpoint를 직접
  수정하지 않는다.
- graph가 실행 순서, feedback 대상, bundle 저장과 checkpoint persistence를 소유한다.
- implementation은 수락된 ResourcePlan만 소비하며 WorkloadGraph를 다시 제안하지 않는다.

## 사용하면 안 되는 import

- deployment service는 `app.design.graphs`, `ArchitectureState`, artifact repository를 import하지
  않는다.
- deployment service는 requirements 내부 state나 implementation service를 import하지 않는다.
- typed generation/revision은 planner, provider template, IaC renderer를 호출하지 않는다.
- planning fact 이후 module은 structured adapter, prompt, LLM service를 import하거나 호출하지
  않는다.
- provider generation·validation과 runtime/provisioning renderer도 LLM, prompt, graph state,
  repository, requirements 내부 state, implementation service를 역참조하지 않는다.
- planner와 renderer는 graph state, repository, requirements 내부 state, implementation service를
  역참조하지 않는다.
- 테스트는 prompt literal, private helper, 내부 문자열 조립 방식에 결합하지 않는다.

## 실패 조건

- 중복 workload/external dependency ID 등 `WorkloadGraph` schema 위반은 Pydantic 경계에서
  실패한다.
- 외부 proposal 실패나 공통 schema repair 실패는 빈 placeholder로 바꾸지 않고 예외를
  전달한다.
- 근거가 부족한 exposure, storage, replication safety, provider 선택은 임의로 발명하지 않는다.
- accepted planning fact와 graph가 충돌하거나 placement 정보가 부족하면 bundle status를
  `needsInput`으로 남기고 불완전한 ResourcePlan을 완료 산출물로 노출하지 않는다.
- provider ResourcePlan 검증 실패 시 IaC renderer까지 진행하지 않는다.
