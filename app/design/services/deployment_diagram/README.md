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

`planner.py`의 기존 공개 import는 compatibility facade로 유지한다. facade와 소유 module은 같은
함수 객체를 노출하며, `bundle.py`의 입력·출력 shape와 호출 순서는 바뀌지 않는다. fact,
constraint, issue, derivation, compute, placement, network, runtime binding, ResourcePlan node와
reference 배열은 입력 순서와 기존 정책 순서를 그대로 유지한다. 세 `structureDigest`는
issue·관측값처럼 구조를 바꾸지 않는 필드를 제외하는 기존 규칙을 사용한다.

### 분리 전 baseline provenance

[planner baseline fixture](../../../../tests/fixtures/deployment_planner_baseline.json)는 분리 후
구현에서 만든 기대값이 아니다. 부모 기준점
`190f78fc43cc804d3a4b28d428a53ade5b1ec3f4`의 기존 `planner.py`와 `bundle.py` blob을 체크아웃
없이 직접 실행해 캡처했다. 고정 입력의 canonical SHA-256은
`65e464fe581f7d4079c6f05dcf89b8e5fe3d5ab74f727ba9271c4f5d7bb86e37`이다. fixture는 planning
facts, normalized graph, DeploymentPlan, runtime binding, ResourcePlan, invalid graph와 bundle의
전체 canonical hash뿐 아니라 배열 순서와 모든 structure digest를 함께 보존한다.

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

`provider_template.py`와 `provider_plantuml.py`는 기존 public import와 wrapper validation을
보존하는 compatibility facade다. facade는 새 generation·validation·renderer의 같은 공개 함수에
위임하며 자체 projection 규칙을 갖지 않는다. implementation의 OpenTofu renderer는 완결된
ResourcePlan만 소비하고 provider resource나 dependency를 새로 선택하지 않는다.

### Projection baseline provenance

[projection baseline fixture](../../../../tests/fixtures/deployment_projection_baseline.json)는
부모 기준점 `bb6ecab8385dc56868c597e6c6a6876321e3f420`의 기존 provider template,
runtime/provisioning PlantUML, OpenTofu renderer를 AWS·Azure·GCP 대표 입력에 직접 실행해
캡처했다. 입력 SHA-256은
`bfe0340ce4fd3142722e71b73b756664ad5c5fbe686a979165c03d3e3138eecd`다.

fixture는 provider별 bundle·ResourcePlan canonical hash, PlantUML UTF-8 byte hash와 byte 길이,
OpenTofu 전체·파일별 hash와 파일 순서를 고정한다. ResourcePlan key·node·runtime binding·binding
slot·late binding·derivation 순서, reference edge ID 순서 hash, node/reference/binding sourceRef
순서 hash와 structure digest도 함께 비교한다. 따라서 이름이나 내용뿐 아니라 배열과 문자열의
byte-level 계약까지 분리 전과 같아야 한다.

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
