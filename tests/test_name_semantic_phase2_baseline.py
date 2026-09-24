"""Characterize phase-2 DTO type handling without name-based correction."""
from __future__ import annotations

from app.design.services.class_diagram.models import AcceptedInventory
from app.design.services.class_diagram.operations import normalize_operation_fragment
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.validation import OperationContext, validate_operations
from tests.class_design_fixtures import single_use_case


def _candidate(*, target_shared_field: str = "shared", include_second_source: bool = False):
    source_fields = [
        "shared : String",
        "sourceOnly : String",
    ]
    data_types = [
        {"name": "SourceDto", "kind": "valueObject", "fields": source_fields, "values": []},
        {
            "name": "TargetDto",
            "kind": "valueObject",
            "fields": [
                f"{target_shared_field} : String",
                "requiredProfile : Profile",
            ],
            "values": [],
        },
        {"name": "Profile", "kind": "valueObject", "fields": ["code : String"], "values": []},
    ]
    boundary_parameters = [{"name": "request", "type": "SourceDto"}]
    if include_second_source:
        data_types.append({"name": "OtherSourceDto", "kind": "valueObject", "fields": [
            "shared : String", "otherOnly : String",
        ], "values": []})
        boundary_parameters.append({"name": "other", "type": "OtherSourceDto"})

    return {
        "Classes": [
            {
                "className": "RequestBoundary",
                "operations": [{"name": "submit", "parameters": boundary_parameters,
                                "returnType": "TargetDto", "stepRefs": ["UC1:main:1"]}],
            },
            {
                "className": "RequestControl",
                "operations": [{
                    "name": "process",
                    "parameters": [{"name": "payload", "type": "TargetDto"}],
                    "returnType": "TargetDto",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": data_types,
    }


def _inventory() -> AcceptedInventory:
    return AcceptedInventory.from_payload({
        "Classes": [
            {"className": "RequestBoundary", "stereotype": "Boundary"},
            {"className": "RequestControl", "stereotype": "Control"},
        ],
        "DataTypes": [],
        "Relationships": [],
    })


def _target_parameter(candidate):
    return candidate["Classes"][1]["operations"][0]["parameters"][0]


def _normalize(candidate):
    index = build_scenario_index(single_use_case())
    inventory = _inventory()
    return normalize_operation_fragment(candidate, index, inventory, index.use_case("UC1")).as_payload()


def test_field_overlap_leaves_authored_downstream_parameter_type():
    candidate = _candidate()

    result = _normalize(candidate)

    assert _target_parameter(result)["type"] == "TargetDto"


def test_renaming_overlapping_target_field_leaves_authored_downstream_parameter_type():
    candidate = _candidate(target_shared_field="renamedShared")

    result = _normalize(candidate)

    assert _target_parameter(result)["type"] == "TargetDto"


def test_tied_field_overlap_leaves_authored_downstream_type_unchanged():
    candidate = _candidate(include_second_source=True)

    result = _normalize(candidate)

    assert _target_parameter(result)["type"] == "TargetDto"


def test_invalid_explicit_type_is_reported_by_existing_validator():
    candidate = _candidate()
    _target_parameter(candidate)["type"] = "MissingDto"
    index = build_scenario_index(single_use_case())
    inventory = _inventory()

    normalized = normalize_operation_fragment(
        candidate, index, inventory, index.use_case("UC1")
    ).as_payload()
    report = validate_operations(
        normalized,
        OperationContext(index, inventory.as_payload(), index.use_case("UC1")),
    )

    assert _target_parameter(normalized)["type"] == "MissingDto"
    assert any(finding.rule_id == "class.operation.references" for finding in report.findings)
