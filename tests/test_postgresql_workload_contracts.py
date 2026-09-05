"""Regression coverage for the narrow explicit PostgreSQL topology producer."""

from __future__ import annotations

from app.design.services.deployment_diagram.placement import build_deployment_plan
from app.design.services.deployment_diagram.template_topology import (
    build_template_workload_graph,
)


def _inputs(**overrides: object) -> dict[str, object]:
    return {
        "useCaseSpecification": "A user manages course registrations.",
        "apiSpec": {"paths": {"/registrations": {"get": {}}}},
        "resourceSpec": {
            "workloads": ["vm"],
            "minVCpu": 2,
            "minMemoryGiB": 4,
        },
        **overrides,
    }


def test_app_only_input_keeps_the_existing_single_application_topology() -> None:
    graph = build_template_workload_graph(_inputs()).model_dump()

    assert [item["id"] for item in graph["workloads"]] == ["application"]
    assert graph["connections"] == []
    assert graph["workloads"][0]["resourceRequirements"] == {
        "minVCpu": 2.0,
        "minMemoryGiB": 4.0,
    }


def test_erd_only_input_keeps_the_single_vm_h2_fallback() -> None:
    graph = build_template_workload_graph(
        _inputs(erdModel={"tables": [{"name": "registrations"}]})
    ).model_dump()

    application = graph["workloads"][0]
    assert [item["id"] for item in graph["workloads"]] == ["application"]
    assert application["storage"][0]["mountPath"] == "/var/lib/easydep/data"
    assert {
        item["name"]: item.get("value") for item in application["configuration"]
    }["SPRING_DATASOURCE_URL"].startswith("jdbc:h2:file:")


def test_explicit_postgres_requirement_projects_existing_contract_topology() -> None:
    graph = build_template_workload_graph(
        _inputs(
            refinedRequirements=[
                {
                    "id": "NFR-DB",
                    "text": "Run PostgreSQL as a separate database runtime.",
                    "authority": "explicit",
                    "status": "accepted",
                }
            ]
        )
    ).model_dump()

    application, database = graph["workloads"]
    assert [application["id"], database["id"]] == ["application", "postgresql"]
    assert database["artifact"] == {
        "kind": "prebuiltImage",
        "image": "postgres:16",
        "engine": "postgresql",
        "deploymentMode": "container",
        "runtimeCatalogRef": "docker-on-vm/prebuilt-image",
    }
    assert database["storage"][0]["mountPath"] == "/var/lib/postgresql/data"
    assert graph["connections"] == [
        {
            "id": "application-to-postgresql",
            "sourceRef": "application",
            "targetRef": "postgresql",
            "protocol": "tcp",
            "sourceInterfaceRef": "",
            "targetInterfaceRef": "postgresql-tcp",
            "sourceRefs": ["requirement:NFR-DB"],
        }
    ]
    plan = build_deployment_plan(graph, _inputs()["resourceSpec"])
    assert plan["computeUnits"][0]["resourceRequirements"] == {
        "minVCpu": 4.0,
        "minMemoryGiB": 8.0,
    }


def test_persisted_string_requirement_can_select_korean_postgres_topology() -> None:
    graph = build_template_workload_graph(
        _inputs(refinedRequirements=["PostgreSQL 데이터베이스를 별도 컨테이너로 실행한다."])
    ).model_dump()

    assert [item["id"] for item in graph["workloads"]] == [
        "application",
        "postgresql",
    ]
    assert graph["connections"][0]["sourceRefs"] == ["refinedRequirements:1"]


def test_user_confirmed_capability_can_select_postgres_topology() -> None:
    graph = build_template_workload_graph(
        _inputs(
            capabilityContract={
                "capabilities": [
                    {
                        "id": "database-runtime",
                        "statement": "Run PostgreSQL as a separate database runtime.",
                        "requirementIds": ["NFR-DB"],
                        "evidenceSpans": ["PostgreSQL database"],
                        "origin": "inferred",
                        "decision": "accepted",
                        "confirmation": "userConfirmed",
                    }
                ]
            }
        )
    ).model_dump()

    assert [item["id"] for item in graph["workloads"]] == [
        "application",
        "postgresql",
    ]
    assert graph["connections"][0]["sourceRefs"] == ["requirement:NFR-DB"]


def test_postgres_topology_is_repeatable_and_does_not_duplicate_contracts() -> None:
    inputs = _inputs(
        refinedRequirements=[
            {
                "id": "NFR-DB",
                "text": "Use an external Postgres database service.",
                "authority": "explicit",
                "status": "accepted",
            }
        ]
    )

    first = build_template_workload_graph(inputs).model_dump()
    second = build_template_workload_graph(inputs).model_dump()

    assert first == second
    assert [item["id"] for item in first["workloads"]] == [
        "application",
        "postgresql",
    ]
    assert [item["id"] for item in first["connections"]] == [
        "application-to-postgresql"
    ]


def test_caller_supplied_topology_contracts_take_precedence() -> None:
    graph = build_template_workload_graph(
        _inputs(
            refinedRequirements=[
                {
                    "id": "NFR-DB",
                    "text": "Use an external PostgreSQL database runtime.",
                    "authority": "explicit",
                    "status": "accepted",
                }
            ],
            deploymentPlanningFacts=[
                {
                    "id": "caller-application",
                    "kind": "workloadContract",
                    "value": {
                        "workloadId": "caller-app",
                        "artifactKind": "generatedApplication",
                    },
                    "sourceRefs": ["requirement:CALLER"],
                    "authority": "explicit",
                    "status": "accepted",
                }
            ],
        )
    ).model_dump()

    assert [item["id"] for item in graph["workloads"]] == ["caller-app"]
