# 배포 WorkloadGraph 생성과 수정

이 패키지는 수락된 요구사항·설계 산출물을 미리 정한 Docker-on-VM 구조에 넣어
`WorkloadGraph`를 만든다. workload, interface, storage, connection, 배치 조건은 코드와
명시적으로 승인된 배포 계약이 정한다. LLM은 이미 만들어진 컴포넌트의 영어 표시 이름만
제안한다. VM 배치, provider resource, 다이어그램과 IaC도 모두 코드가 만든다.

## 처리 경계

```text
구조화된 요구사항·class·sequence·API·ERD·deployment fact
  → template_topology의 구조 선택
  → LLM의 컴포넌트 이름 제안
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

사용자 피드백은 저장 JSON을 `WorkloadGraph`로 검증한 뒤 `revise_workload_graph`에 전달한다.
이 함수도 기존 컴포넌트의 `name`만 바꿀 수 있다. 네트워크, storage, replica, workload 수처럼
구조에 관한 피드백은 요구사항의 배포 입력에서 승인한 뒤 코드가 다시 선택해야 한다.

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
    resource_spec: dict[str, Any] | None = None,
    class_model: Any = None,
    sequence_model: Any = None,
    erd_model: Any = None,
    deployment_planning_facts: list[dict[str, Any]] | None = None,
    proposal_call: DeploymentLabelProposalCall | None = None,
) -> WorkloadGraph
```

- 일반 HTTP 프로젝트는 하나의 `generatedApplication` 템플릿으로 시작한다.
- 승인된 `workloadContract`, `connectionContract`, `constraintContract`만 구조를 확장한다.
- API가 있으면 기본 HTTP 공개 진입점이 생기며, 별도 계약이 있으면 그 값을 우선한다.
- 승인 capability의 `persistent-block-storage`와 `load-balanced-ingress`는 각각 기존 block
  storage와 managed VM group 템플릿을 선택한다.
- structured class 모델은 이름을 짓는 작은 문맥으로만 사용한다.
- 빈 scenario는 외부 호출 없이 빈 typed graph를 반환한다.

피드백 수정 경계는 다음과 같다.

```python
revise_workload_graph(
    current_model: WorkloadGraph,
    feedback: str,
    context_text: str = "",
    targets: set[str] | None = None,
    *,
    proposal_call: DeploymentLabelProposalCall | None = None,
) -> WorkloadGraph
```

빈 feedback은 외부 호출 없이 같은 객체를 반환한다. 그 밖에는 `id`, `name`만 있는
`DeploymentComponentLabels`를 한 번 받고, 기존 ID와 일치하는 이름만 복사한다. LLM이 알지
못하는 ID를 반환해도 새 컴포넌트가 생기지 않으며 graph의 다른 필드는 바뀌지 않는다.

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

- scenario가 있는 생성은 코드가 구조를 만든 뒤 `DeploymentComponentLabels`를 한 번 받는다.
- feedback이 있는 수정도 같은 이름 전용 schema를 한 번 받는다.
- LLM 입력에는 컴포넌트 ID·현재 이름, 유스케이스 문맥, 클래스 이름만 포함한다.
- LLM 출력에는 workload·connection·storage·constraint·CSP 리소스 필드가 없다.
- native structured response와 JSON fallback·schema repair는 공통 structured adapter가
  소유한다. 이 서비스는 추가 repair, 병렬 호출, 후보 투표를 만들지 않는다.
- 이름 응답이 schema 검증을 통과하지 않으면 구조를 임의로 바꾸지 않고 오류를 전달한다.

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
| `template_topology.py` | API·RESOURCE_SPEC·승인 배포 계약 | LLM이 수정할 수 없는 workload 구조 |
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

- generate/revise만 이름 전용 structured LLM adapter를 호출할 수 있다.
- typed model 검증과 이후 planning·rendering은 순수 계산이며 저장소나 checkpoint를 직접
  수정하지 않는다.
- graph가 실행 순서, feedback 대상, bundle 저장과 checkpoint persistence를 소유한다.
- implementation은 수락된 ResourcePlan만 소비하며 WorkloadGraph를 다시 제안하지 않는다.

## 사용하면 안 되는 import

- deployment service는 `app.design.graphs`, `ArchitectureState`, artifact repository를 import하지
  않는다.
- deployment service는 requirements 내부 state나 implementation service를 import하지 않는다.
- 이름 generation/revision은 planner, provider template, IaC renderer를 호출하지 않는다.
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
- 외부 이름 proposal 실패나 공통 schema repair 실패는 빈 placeholder로 바꾸지 않고 예외를
  전달한다.
- 구조 입력이 부족하면 LLM에게 추측시키지 않고 기본 단일 애플리케이션 템플릿 또는 명시
  입력 질문으로 처리한다.
- accepted planning fact와 graph가 충돌하거나 placement 정보가 부족하면 bundle status를
  `needsInput`으로 남기고 불완전한 ResourcePlan을 완료 산출물로 노출하지 않는다.
- provider ResourcePlan 검증 실패 시 IaC renderer까지 진행하지 않는다.
