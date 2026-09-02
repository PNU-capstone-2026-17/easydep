"""저장된 산출물의 직접 근거를 ``ArtifactTrace``로 읽는 순수 어댑터.

DB·repository·stage service를 호출하지 않는다. 저장 JSON의 확정 ID와 ``sourceRefs``만
사용하며, 이름 유사성이나 부분 문자열로 산출물을 연결하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.artifact_trace import ArtifactTrace, TraceNode, TraceRef


def project_artifact_trace(
    state: Mapping[str, Any] | None,
    implementation_rtm: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    testing_result: Mapping[str, Any] | None = None,
) -> ArtifactTrace:
    """한 snapshot에서 requirement→설계→배포→파일/테스트 근거를 투영한다.

    접두사가 없는 ``sourceRefs``는 종류를 추측하지 않고 ``opaque``로 보존한다.
    """

    state = state if isinstance(state, Mapping) else {}
    nodes: list[TraceNode] = []
    _requirements(nodes, state.get("refined_requirements"))
    specification = _map(state.get("usecase_spec"))
    _use_cases(nodes, specification)
    operations = _bce(nodes, _map(state.get("extracted_bce_classes")))
    _sequence(nodes, _map(state.get("sequence_diagram_model")))
    _api(nodes, _map(state.get("api_spec_model")), operations)
    _erd(nodes, _map(state.get("erd_bce_classes")))
    resources_by_workload = _deployment(nodes, _map(state.get("deployment_diagram_bundle")))
    _implementation(
        nodes,
        implementation_rtm or state.get("implementation_rtm"),
        resources_by_workload,
    )
    _testing(nodes, testing_result or _map(state.get("testing_result")))
    return ArtifactTrace(nodes)


def _requirements(nodes: list[TraceNode], value: Any) -> None:
    """refined requirement의 ID와 원문 source_refs를 있는 그대로 보존한다."""

    for item in _records(value, "requirements"):
        requirement_id = _id(item, "id")
        if requirement_id:
            _add(nodes, TraceRef("requirement", requirement_id), _source_refs(item))


def _use_cases(nodes: list[TraceNode], specification: Mapping[str, Any]) -> None:
    """UC, UC 명세, requirements traceability의 명시 ID 관계만 읽는다."""

    for item in _records(specification.get("use_cases")):
        use_case_id = _id(item, "id")
        if use_case_id:
            _add(
                nodes,
                TraceRef("use_case", use_case_id),
                [
                    *_source_refs(item),
                    *_refs(item, "requirement", "requirement_ids", "nfr_ids"),
                ],
            )

    for item in _records(specification.get("use_case_specs")):
        use_case_id = _id(item, "use_case_id")
        if not use_case_id:
            continue
        spec_ref = TraceRef("use_case_spec", use_case_id)
        _add(
            nodes,
            spec_ref,
            [
                TraceRef("use_case", use_case_id),
                *_source_refs(item),
                *_refs(item, "requirement", "requirement_ids", "nfr_ids"),
            ],
        )
        for step in _records(item.get("main_scenario")):
            number = step.get("step_number")
            if isinstance(number, (int, str)) and str(number):
                _add(
                    nodes,
                    TraceRef("step", f"{use_case_id}:main:{number}"),
                    [spec_ref, *_refs(step, "requirement", "covered_req_ids")],
                )
        for extension in _records(item.get("extensions")):
            label = _id(extension, "label")
            if not label:
                continue
            for step in _records(extension.get("handling_steps")):
                sub_step = _id(step, "sub_step")
                if sub_step:
                    _add(
                        nodes,
                        TraceRef(
                            "step",
                            f"{use_case_id}:extension:{label}:{sub_step}",
                        ),
                        [spec_ref],
                    )

    traceability = _map(specification.get("traceability"))
    for requirement_id, item in _map(traceability.get("requirements")).items():
        if (
            not isinstance(requirement_id, str)
            or not requirement_id
            or not isinstance(item, Mapping)
        ):
            continue
        for use_case_id in _strings(
            item.get("use_cases"),
            item.get("realized_by_use_cases"),
            item.get("constrains_use_cases"),
        ):
            _add(
                nodes,
                TraceRef("use_case", use_case_id),
                [TraceRef("requirement", requirement_id)],
            )


def _bce(nodes: list[TraceNode], model: Mapping[str, Any]) -> dict[tuple[str, str], TraceRef]:
    """BCE Classes/operations/Collaborations의 직접 ID만 투영한다."""

    candidates: dict[tuple[str, str], list[TraceRef]] = {}
    for item in _records(model.get("Classes")):
        class_name = _id(item, "className")
        if not class_name:
            continue
        class_ref = TraceRef("class", class_name)
        use_cases = _refs(item, "use_case", "use_case_ids")
        _add(nodes, class_ref, [*_source_refs(item), *use_cases])
        for operation in _records(item.get("operations")):
            operation_id = _id(operation, "operationId")
            if not operation_id:
                continue
            operation_ref = TraceRef("operation", operation_id)
            _add(
                nodes,
                operation_ref,
                [
                    class_ref,
                    *use_cases,
                    *_source_refs(operation),
                    *_refs(operation, "step", "stepRefs"),
                ],
            )
            name = _id(operation, "name")
            if name:
                candidates.setdefault((class_name, name), []).append(operation_ref)

    for item in _records(model.get("DataTypes")):
        name = _id(item, "name")
        if name:
            _add(nodes, TraceRef("data_type", name), _source_refs(item))

    for item in _records(model.get("Collaborations")):
        collaboration_id = _id(item, "collaborationId")
        if not collaboration_id:
            continue
        collaboration_ref = TraceRef("collaboration", collaboration_id)
        use_cases = _refs(item, "use_case", "useCaseIds")
        _add(nodes, collaboration_ref, [*_source_refs(item), *use_cases])
        for call in _records(item.get("calls")):
            call_id = _id(call, "callId")
            if call_id:
                _add(
                    nodes,
                    TraceRef("call", call_id),
                    [
                        collaboration_ref,
                        *use_cases,
                        *_source_refs(call),
                        *_refs(call, "operation", "receiverOperationId"),
                        *_refs(call, "call", "parentCallId"),
                        *_refs(call, "step", "stepRefs"),
                    ],
                )

    return {key: values[0] for key, values in candidates.items() if len(values) == 1}


def _sequence(nodes: list[TraceNode], model: Mapping[str, Any]) -> None:
    """Diagrams/Messages의 UC·call/reply ID를 정확히 BCE call로 연결한다."""

    for diagram in _records(model.get("Diagrams")):
        use_case_id = _id(diagram, "use_case_id")
        if not use_case_id:
            continue
        sequence_ref = TraceRef("sequence", use_case_id)
        _add(nodes, sequence_ref, [TraceRef("use_case", use_case_id), *_source_refs(diagram)])
        for message in _records(diagram.get("Messages")):
            call_id = _id(message, "call_id") or _id(message, "reply_to")
            if not call_id:
                continue
            _add(
                nodes,
                TraceRef("message", call_id),
                [
                    sequence_ref,
                    TraceRef("call", call_id),
                    *_source_refs(message),
                    *_refs(message, "use_case", "use_case_ids"),
                    *_refs(message, "step", "step_ids"),
                ],
            )


def _api(
    nodes: list[TraceNode],
    model: Mapping[str, Any],
    operations: Mapping[tuple[str, str], TraceRef],
) -> None:
    """API endpoint/schema를 추적하고 확정 binding일 때만 BCE operation을 잇는다."""

    for item in _records(model.get("Endpoints")):
        method, path = _id(item, "method"), _id(item, "path")
        # UI feedback과 저장 모델이 사용하는 operationId를 우선한다. operationId가
        # 없는 불완전 draft만 HTTP method/path를 임시 주소로 사용한다.
        endpoint_id = _id(item, "operation_id")
        if not endpoint_id and method and path:
            endpoint_id = f"{method.upper()} {path}"
        if not endpoint_id:
            continue
        sources = [
            *_source_refs(item),
            *_refs(item, "class", "source_classes"),
            *_refs(item, "use_case", "use_case_ids"),
            *_refs(item, "schema", "request_schema"),
        ]
        for response in _records(item.get("responses")):
            sources.extend(_refs(response, "schema", "schema_name"))
        binding = _map(item.get("control_binding"))
        control, method_name = _id(binding, "control"), _id(binding, "method")
        operation = operations.get((control, method_name)) if control and method_name else None
        if operation:
            sources.append(operation)
        _add(nodes, TraceRef("api", endpoint_id), sources)

    for item in _records(model.get("Schemas")):
        name = _id(item, "name")
        if name:
            _add(
                nodes,
                TraceRef("schema", name),
                [*_source_refs(item), *_refs(item, "class", "source_class")],
            )


def _erd(nodes: list[TraceNode], model: Mapping[str, Any]) -> None:
    """ERD Entity는 동명 BCE Entity의 결정론적 투영으로만 연결한다."""

    for item in _records(model.get("Classes")):
        name = _id(item, "className")
        if name and item.get("stereotype") == "Entity":
            _add(
                nodes,
                TraceRef("entity", name),
                [
                    TraceRef("class", name),
                    *_refs(item, "use_case", "use_case_ids"),
                    *_source_refs(item),
                ],
            )


def _deployment(nodes: list[TraceNode], bundle: Mapping[str, Any]) -> dict[TraceRef, set[TraceRef]]:
    """WorkloadGraph와 각 projection ResourcePlan의 식별 가능한 sourceRefs를 읽는다."""

    resources_by_workload: dict[TraceRef, set[TraceRef]] = {}
    fact_refs: dict[str, TraceRef] = {}
    for fact in _records(_map(bundle.get("planningFacts")).get("facts")):
        identifier = _id(fact, "id")
        if identifier:
            fact_ref = TraceRef("planning_fact", identifier)
            fact_refs[identifier] = fact_ref
            _add(
                nodes,
                fact_ref,
                _source_refs(fact),
            )

    graph = _map(bundle.get("workloadGraph"))
    workload_refs: dict[str, TraceRef] = {}
    workload_tokens: dict[str, set[str]] = {}
    for item in _records(graph.get("workloads")):
        identifier = _id(item, "id")
        if not identifier:
            continue
        workload_ref = TraceRef("workload", identifier)
        workload_refs[identifier] = workload_ref
        workload_tokens[identifier] = {
            identifier,
            *_strings(item.get("sourceRefs"), item.get("source_refs")),
        }
        _add(nodes, workload_ref, _source_refs(item, fact_refs))

    for collection, kind in (
        ("externalDependencies", "external_dependency"),
        ("connections", "connection"),
        ("constraints", "constraint"),
    ):
        for item in _records(graph.get(collection)):
            identifier = _id(item, "id")
            if identifier:
                _add(
                    nodes,
                    TraceRef(kind, identifier),
                    _source_refs(item, fact_refs),
                )

    for projection in _records(bundle.get("projections")):
        provider = _id(projection, "provider")
        region = _id(projection, "region")
        if not provider or not region:
            continue
        target_id = f"{provider}:{region}"
        plan = _map(projection.get("resourcePlan"))
        # ResourcePlan의 top-level collection은 provider 기능이 늘면 함께 늘어난다.
        # 특정 collection 이름을 복제하지 않고 ID/sourceRefs가 있는 record만 읽는다.
        for collection, value in plan.items():
            for item in _records(value):
                identifier = _id(item, "id") or _id(item, "ruleId")
                if identifier:
                    source_tokens = set(_strings(item.get("sourceRefs"), item.get("source_refs")))
                    linked_workloads = {
                        workload_ref
                        for workload_id, workload_ref in workload_refs.items()
                        if workload_id == item.get("workloadRef")
                        or bool(source_tokens & workload_tokens[workload_id])
                    }
                    resource_ref = TraceRef("resource", f"{target_id}:{collection}:{identifier}")
                    _add(
                        nodes,
                        resource_ref,
                        [*_source_refs(item, fact_refs), *linked_workloads],
                    )
                    for workload_ref in linked_workloads:
                        resources_by_workload.setdefault(workload_ref, set()).add(resource_ref)
    return resources_by_workload


def _implementation(
    nodes: list[TraceNode],
    value: Any,
    resources_by_workload: Mapping[TraceRef, set[TraceRef]],
) -> None:
    """implementation RTM의 taskId→target_file 근거를 requirement/use case와 잇는다."""

    for item in _records(value, "mappings"):
        task_id, target_file = _id(item, "taskId"), _id(item, "target_file")
        sources = [
            *_refs(item, "requirement", "requirementIds"),
            *_refs(item, "use_case", "useCaseIds"),
            *_source_refs(item),
        ]
        sources.extend(
            resource_ref
            for source in tuple(sources)
            for resource_ref in resources_by_workload.get(source, set())
        )
        task_ref = TraceRef("task", task_id) if task_id else None
        if task_ref:
            _add(nodes, task_ref, sources)
        if target_file:
            _add(nodes, TraceRef("file", target_file), [task_ref] if task_ref else sources)


def _testing(nodes: list[TraceNode], result: Mapping[str, Any]) -> None:
    """작은 테스트 계획을 requirement/use case/API와 실행 evidence에 잇는다."""

    report = _map(result.get("dynamic_functional_report"))
    if not report:
        report = _map(_map(result.get("verification")).get("reports")).get("dynamicFunctional")
    report = _map(report) if report else result
    digest = _id(report, "candidateDigest") or "unversioned"
    for case in _records(_map(report.get("candidatePlan")).get("cases")):
        case_id = _id(case, "case_id")
        if not case_id:
            continue
        sources = [
            *_refs(case, "requirement", "requirement_ids"),
            *_refs(case, "use_case", "use_case_id"),
        ]
        sources.extend(
            TraceRef("api", operation_id)
            for step in _records(case.get("steps"))
            if (operation_id := _id(step, "operation_id"))
        )
        test_ref = TraceRef("test", f"{digest}:{case_id}")
        _add(nodes, test_ref, sources)

    for case_result in _records(report.get("cases")):
        case_id = _id(case_result, "caseId")
        finding = _map(_map(case_result.get("result")).get("finding"))
        finding_id = _id(finding, "code")
        if case_id and finding_id:
            source = TraceRef("test", f"{digest}:{case_id}")
            _add(nodes, TraceRef("finding", f"{case_id}:{finding_id}"), [source])

    for item in _records(result.get("blocking_findings")):
        finding_id = _id(item, "code") or _id(item, "id")
        if finding_id:
            exact_tests: list[TraceRef] = []
            for value in item.get("target_ids") or []:
                if not isinstance(value, str):
                    continue
                try:
                    ref = TraceRef.parse(value)
                except ValueError:
                    continue
                if ref.kind == "test":
                    exact_tests.append(ref)
            _add(nodes, TraceRef("finding", finding_id), exact_tests)


def _add(nodes: list[TraceNode], ref: TraceRef, sources: Iterable[TraceRef]) -> None:
    nodes.append(TraceNode(ref=ref, direct_sources=tuple(sources)))


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: Any, key: str | None = None) -> list[Mapping[str, Any]]:
    if key and isinstance(value, Mapping):
        value = value.get(key)
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _id(item: Mapping[str, Any], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) and value else None


def _strings(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value:
            result.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            result.extend(item for item in value if isinstance(item, str) and item)
    return result


def _refs(item: Mapping[str, Any], kind: str, *keys: str) -> list[TraceRef]:
    return [TraceRef(kind, value) for value in _strings(*(item.get(key) for key in keys))]


def _source_refs(
    item: Mapping[str, Any], exact_refs: Mapping[str, TraceRef] | None = None
) -> list[TraceRef]:
    refs: list[TraceRef] = []
    for value in _strings(item.get("sourceRefs"), item.get("source_refs")):
        kind, separator, identifier = value.partition(":")
        if separator and kind and identifier:
            refs.append(TraceRef(kind, identifier))
        elif exact_refs and value in exact_refs:
            refs.append(exact_refs[value])
        else:
            refs.append(TraceRef("opaque", value))
    return refs


__all__ = ["project_artifact_trace"]
