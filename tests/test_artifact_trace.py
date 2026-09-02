"""Public contracts for the artifact trace projection."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import artifacts_api
from app.artifact_trace import (
    ArtifactTrace,
    TraceNode,
    TraceRef,
    format_ref,
    parse_ref,
)
from app.artifact_trace_projection import project_artifact_trace


@pytest.fixture
def trace_client() -> Iterator[TestClient]:
    application = FastAPI()
    application.include_router(artifacts_api.router)
    with TestClient(application) as client:
        yield client


def test_refs_preserve_ids_after_the_first_colon():
    file_ref = parse_ref("file:src/api.py:18")
    operation_ref = parse_ref("operation:GET:/users/{id}:detail")

    assert file_ref == TraceRef("file", "src/api.py:18")
    assert operation_ref == TraceRef("operation", "GET:/users/{id}:detail")
    assert format_ref(operation_ref) == "operation:GET:/users/{id}:detail"


def test_duplicate_nodes_merge_their_direct_sources():
    target = TraceRef("file", "src/api.py:18")
    requirement = TraceRef("requirement", "R1")
    operation = TraceRef("operation", "GET:/items:detail")

    trace = ArtifactTrace(
        [
            TraceNode(target, (requirement,)),
            TraceNode(target, (operation, requirement)),
        ]
    )

    assert trace.refs == (target,)
    assert trace.sources(target) == tuple(sorted((requirement, operation)))


def test_sources_consumers_and_transitive_queries_are_cycle_safe():
    a = TraceRef("node", "a")
    b = TraceRef("node", "b")
    c = TraceRef("node", "c")
    trace = ArtifactTrace(
        [
            TraceNode(a, (c,)),
            TraceNode(b, (a,)),
            TraceNode(c, (b,)),
        ]
    )

    assert trace.sources(b) == (a,)
    assert trace.consumers(a) == (b,)
    assert trace.upstream(a) == (b, c)
    assert trace.downstream(a) == (b, c)


def test_unknown_source_refs_are_retained():
    requirement = TraceRef("requirement", "R1")
    unknown = TraceRef("file", "missing.py:1")
    trace = ArtifactTrace([TraceNode(requirement, (unknown,))])

    assert trace.unknown_source_refs == (unknown,)
    assert trace.sources(requirement) == (unknown,)
    assert trace.upstream(requirement) == (unknown,)


def test_files_and_evidence_are_found_across_a_requirement_chain():
    requirement = TraceRef("requirement", "R1")
    use_case = TraceRef("use_case", "UC1")
    operation = TraceRef("operation", "GET:/items:detail")
    file_ref = TraceRef("file", "src/api.py:18")
    test = TraceRef("test", "tests/test_api.py::test_items")
    evidence = TraceRef("evidence", "run:42")
    trace = ArtifactTrace(
        [
            TraceNode(use_case, (requirement,)),
            TraceNode(operation, (use_case,)),
            TraceNode(file_ref, (operation,)),
            TraceNode(test, (file_ref,)),
            TraceNode(evidence, (test,)),
        ]
    )

    assert trace.files(requirement) == (file_ref,)
    assert trace.files(test) == (file_ref,)
    assert trace.evidence(requirement) == tuple(sorted((test, evidence)))


def test_projection_links_requirement_to_spec_step_bce_operation_and_exact_api_binding():
    trace = project_artifact_trace(
        {
            "refined_requirements": {"requirements": [{"id": "REQ-1"}]},
            "usecase_spec": {
                "use_cases": [{"id": "UC-1", "requirement_ids": ["REQ-1"]}],
                "use_case_specs": [
                    {
                        "use_case_id": "UC-1",
                        "main_scenario": [{"step_number": 1, "covered_req_ids": ["REQ-1"]}],
                    }
                ],
            },
            "extracted_bce_classes": {
                "Classes": [
                    {
                        "className": "OrderControl",
                        "use_case_ids": ["UC-1"],
                        "operations": [
                            {
                                "operationId": "OP-1",
                                "name": "createOrder",
                                "stepRefs": ["UC-1:main:1"],
                            },
                            {"operationId": "OP-2", "name": "listOrders"},
                        ],
                    }
                ]
            },
            "api_spec_model": {
                "Endpoints": [
                    {
                        "method": "post",
                        "path": "/orders",
                        "operation_id": "createOrder",
                        "control_binding": {
                            "control": "OrderControl",
                            "method": "createOrder",
                        },
                    }
                ]
            },
        }
    )
    requirement = TraceRef("requirement", "REQ-1")
    use_case = TraceRef("use_case", "UC-1")
    spec = TraceRef("use_case_spec", "UC-1")
    step = TraceRef("step", "UC-1:main:1")
    operation = TraceRef("operation", "OP-1")
    api = TraceRef("api", "createOrder")

    assert set(trace.downstream(requirement)) == {
        use_case,
        spec,
        step,
        operation,
        api,
        TraceRef("class", "OrderControl"),
        TraceRef("operation", "OP-2"),
    }
    assert trace.sources(api) == (operation,)
    assert {requirement} <= set(trace.sources(use_case))
    assert {spec, requirement} <= set(trace.sources(step))
    assert {step} <= set(trace.sources(operation))


def test_projection_connects_requirement_source_refs_to_deployment_implementation_and_testing():
    requirement = TraceRef("requirement", "REQ-1")
    workload = TraceRef("workload", "orders-worker")
    resource = TraceRef("resource", "aws:us-east-1:nodes:orders")
    task = TraceRef("task", "TASK-1")
    file_ref = TraceRef("file", "src/orders.py")
    test = TraceRef("test", "digest-1:UC-1")
    finding = TraceRef("finding", "testing.unit-tests")
    trace = project_artifact_trace(
        {
            "refined_requirements": {
                "requirements": [{"id": "REQ-1", "sourceRefs": ["raw:RAW-1"]}]
            },
            "deployment_diagram_bundle": {
                "workloadGraph": {
                    "workloads": [
                        {
                            "id": "orders-worker",
                            "sourceRefs": ["requirement:REQ-1"],
                        }
                    ]
                },
                "projections": [
                    {
                        "provider": "aws",
                        "region": "us-east-1",
                        "target": {"id": "prod"},
                        "resourcePlan": {
                            "nodes": [
                                {
                                    "id": "orders",
                                    "sourceRefs": ["requirement:REQ-1"],
                                }
                            ]
                        },
                    }
                ],
            },
        },
        implementation_rtm={
            "mappings": [
                {
                    "taskId": "TASK-1",
                    "target_file": "src/orders.py",
                    "requirementIds": ["REQ-1"],
                    "sourceRefs": ["workload:orders-worker"],
                }
            ]
        },
        testing_result={
            "dynamic_functional_report": {
                "candidateDigest": "digest-1",
                "candidatePlan": {
                    "cases": [
                        {
                            "case_id": "UC-1",
                            "requirement_ids": ["REQ-1"],
                            "use_case_id": "UC-1",
                            "steps": [
                                {
                                    "step_id": "run",
                                    "operation_id": "createOrder",
                                }
                            ],
                        }
                    ]
                },
            },
            "blocking_findings": [{"code": "testing.unit-tests"}],
        },
    )

    assert set(trace.downstream(requirement)) == {
        workload,
        resource,
        task,
        file_ref,
        test,
        finding,
    }
    assert trace.sources(workload) == (requirement,)
    assert {requirement, workload} <= set(trace.sources(resource))
    assert {requirement, workload, resource} <= set(trace.sources(task))
    assert trace.sources(file_ref) == (task,)
    assert set(trace.sources(test)) == {
        requirement,
        TraceRef("use_case", "UC-1"),
        TraceRef("api", "createOrder"),
    }
    assert trace.sources(finding) == (test,)


def test_trace_endpoint_uses_source_snapshot_rtm_and_latest_testing_result(
    trace_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    app_id = "11111111-1111-4111-8111-111111111111"
    state = {
        "refined_requirements": {"requirements": [{"id": "REQ-1"}]},
        "usecase_spec": {"use_cases": [{"id": "UC-1", "requirement_ids": ["REQ-1"]}]},
        "api_spec_model": {
            "Endpoints": [{"operation_id": "createOrder", "use_case_ids": ["UC-1"]}]
        },
    }
    snapshot = {
        "version_id": 8,
        "version_no": 2,
        "snapshot_digest": "source-digest",
        "created_at": "2026-09-02T00:00:00+00:00",
        "metadata": {
            "implementation_traceability": {
                "mappings": [
                    {
                        "taskId": "implement-orders",
                        "target_file": "application/src/orders/OrderService.java",
                        "sourceRefs": ["api:createOrder"],
                    }
                ]
            }
        },
    }
    testing_result = {
        "passed": False,
        "gateStatus": "FAIL",
        "verification": {
            "reports": {
                "dynamicFunctional": {
                    "candidateDigest": "plan-digest",
                    "candidatePlan": {
                        "cases": [
                            {
                                "case_id": "UC-1",
                                "requirement_ids": ["REQ-1"],
                                "use_case_id": "UC-1",
                                "steps": [{"step_id": "create", "operation_id": "createOrder"}],
                            }
                        ]
                    },
                    "cases": [
                        {
                            "caseId": "UC-1",
                            "result": {"finding": {"code": "HTTP_STATUS_NOT_SUCCESS"}},
                        }
                    ],
                }
            }
        },
    }
    monkeypatch.setattr(
        "app.artifact_trace_service.artifact_repository.load_state",
        lambda received_app_id: state if received_app_id == app_id else None,
    )
    monkeypatch.setattr(
        "app.artifact_trace_service.artifact_repository.load_file_snapshot",
        lambda received_app_id, _kind: snapshot if received_app_id == app_id else None,
    )
    monkeypatch.setattr(
        "app.artifact_trace_service.workspace_repository.latest_command",
        lambda received_app_id, *, stage: {
            "command_id": "testing-command",
            "stage": "testing",
            "status": "COMPLETED",
            "result": {"job": {"result": testing_result}},
        }
        if received_app_id == app_id and stage == "testing"
        else None,
    )

    response = trace_client.get(f"/api/apps/{app_id}/trace?ref=api:createOrder")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ref"] == "api:createOrder"
    assert payload["files"] == ["file:application/src/orders/OrderService.java"]
    assert "test:plan-digest:UC-1" in payload["evidence"]
    assert payload["source_snapshot"]["snapshot_digest"] == "source-digest"
    assert payload["testing"] == {
        "command_id": "testing-command",
        "status": "COMPLETED",
        "passed": False,
        "gate_status": "FAIL",
    }
