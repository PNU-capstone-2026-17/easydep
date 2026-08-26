import copy
import dataclasses
import hashlib
from unittest.mock import patch

import pytest

from app.design.services.common.structured import StructuredLlmError

import app.design.graphs.subgraphs as sequence_subgraphs
from app.design.graphs.subgraphs import SEQUENCE_DIAGRAM_SPEC, _sequence_revision_context
from app.design.knowledge.detectors import (
    Finding,
    sequence_causal_call_chain,
    sequence_diagram_findings,
    sequence_initial_entry,
    sequence_message_methods,
    sequence_no_lifecycle_events,
    sequence_usecase_coverage,
)
from app.design.services.sequence_diagram.extractor import (
    SequenceModel,
    extract_sequence_model,
    extract_sequence_diagrams,
    normalize_sequence_contracts,
    normalize_sequence_entry_order,
    normalize_sequence_participants,
    normalize_sequence_usecase_spec,
    parse_sequence_structured,
    reassemble_sequence_diagrams,
    normalize_sequence_message_order,
    _assemble_deterministic_diagrams,
    _only_callable_class,
    _recover_explicit_actor_retries,
    _supplementary_actor_selection_routes,
)
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.services.sequence_diagram.reconcile import (
    _scope_proposal_evidence,
    reconcile_class_methods,
)
from app.design.services.sequence_diagram import reviser as sequence_reviser
from app.design.nodes.artifact import (
    CLEAN,
    NEEDS_INPUT,
    NO_IMPROVEMENT,
    _sequence_repair_score,
    check_node,
    merge_model,
    render_node,
)


def _participant(name: str, kind: str, source_class: str = "") -> dict:
    return {
        "name": name,
        "alias": name,
        "kind": kind,
        "description": "",
        "source_class": source_class,
    }


def _message(source: str, target: str, label: str, **overrides) -> dict:
    message = {
        "source": source,
        "target": target,
        "label": label,
        "type": "sync",
        "fragments": [],
        "use_case_ids": ["UC1"],
        "step_ids": ["UC1:main:1"],
    }
    message.update(overrides)
    return message


def test_normalize_sequence_message_order_places_extension_after_anchor():
    messages = [
        _message("boundary", "control", "complete()", step_ids=["UC1:main:3"], call_id="call-3"),
        _message("boundary", "control", "submit()", step_ids=["UC1:main:1"], call_id="call-1"),
        _message(
            "control", "boundary", "Error", type="return", step_ids=[], reply_to="call-1"
        ),
        _message(
            "control", "boundary", "displayError()",
            step_ids=["UC1:extension:1a:1a1"],
        ),
        _message("boundary", "control", "next()", step_ids=["UC1:main:2"], call_id="call-2"),
    ]

    ordered = normalize_sequence_message_order(messages)

    assert [message["label"] for message in ordered] == [
        "submit()", "Error", "displayError()", "next()", "complete()"
    ]


def test_normalize_sequence_message_order_never_places_reply_before_its_call():
    messages = [
        _message("control", "boundary", "Receipt", type="return", step_ids=[], reply_to="call-1"),
        _message("actor", "boundary", "submit()", call_id="call-1"),
    ]

    ordered = normalize_sequence_message_order(messages)

    assert [message["label"] for message in ordered] == ["submit()", "Receipt"]


def test_normalize_sequence_message_order_ignores_stale_reply_step_id():
    messages = [
        _message(
            "control", "boundary", "List<Course>", type="return",
            step_ids=["UC1:main:1"], reply_to="call-2",
        ),
        _message(
            "boundary", "control", "getCatalog()", call_id="call-2",
            step_ids=["UC1:main:2"],
        ),
    ]

    ordered = normalize_sequence_message_order(messages)

    assert [message["label"] for message in ordered] == ["getCatalog()", "List<Course>"]


def test_explicit_actor_retry_reuses_prior_boundary_input_in_loop():
    specification = {
        "use_case_id": "UC1",
        "primary_actor": "Student",
        "main_scenario": [
            {"step_number": 1, "sentence": "Student enters credentials."},
            {"step_number": 2, "sentence": "System validates credentials."},
            {"step_number": 3, "sentence": "System creates a session."},
        ],
        "extensions": [{
            "label": "2a",
            "branch_step": 2,
            "condition": "credentials are invalid",
            "handling_steps": [{
                "sub_step": "2a2",
                "sentence": "Student revises credentials.",
            }],
        }],
    }
    extracted = {
        "Participants": [
            _participant("Student", "actor"),
            _participant("SignInForm", "boundary", "SignInForm"),
            _participant("SignInController", "control", "SignInController"),
        ],
        "Messages": [
            _message(
                "Student", "SignInForm", "submitCredentials(username:String,password:String)",
                step_ids=["UC1:main:1"], call_id="UC1-call-1",
                arguments=[
                    {"parameter": "username", "type": "String", "source_kind": "input", "source_ref": "UC1:main:1"},
                    {"parameter": "password", "type": "String", "source_kind": "input", "source_ref": "UC1:main:1"},
                ],
            ),
            _message("SignInForm", "SignInController", "authenticate()", step_ids=["UC1:main:2"], call_id="UC1-call-2"),
        ],
    }

    _recover_explicit_actor_retries(extracted, specification, {})

    retry = extracted["Messages"][2]
    assert retry["label"] == "submitCredentials(username:String,password:String)"
    assert retry["step_ids"] == ["UC1:extension:2a:2a2"]
    assert retry["fragments"] == [{
        "id": "UC1_Actor_2a",
        "type": "loop",
        "branch": "main",
        "condition": "credentials are invalid",
    }]
    assert {argument["source_ref"] for argument in retry["arguments"]} == {"UC1:extension:2a:2a2"}


def test_selection_step_exposes_existing_selection_boundary_to_llm():
    registration_boundary = {
        "name": "RegistrationBoundary",
        "kind": "boundary",
        "methods": ["requestRegistration(courseId:String)", "confirmRegistration()"],
    }
    registration_control = {"name": "RegistrationControl", "kind": "control", "methods": []}
    catalog_boundary = {
        "name": "CourseCatalogBoundary",
        "kind": "boundary",
        "methods": ["selectCourse(courseId:String)"],
    }
    catalog_control = {"name": "CourseCatalogControl", "kind": "control", "methods": []}
    specification = {
        "use_case_id": "UC5",
        "primary_actor": "Student",
        "main_scenario": [
            {"step_number": 1, "sentence": "Student selects a desired course from the catalog."},
        ],
        "extensions": [],
    }

    routes = _supplementary_actor_selection_routes(
        specification,
        "Student",
        registration_boundary,
        registration_control,
        [(registration_boundary, registration_control), (catalog_boundary, catalog_control)],
    )

    assert [(boundary["name"], control["name"]) for boundary, control in routes] == [
        ("RegistrationBoundary", "RegistrationControl"),
        ("CourseCatalogBoundary", "CourseCatalogControl"),
    ]


def test_normalize_sequence_entry_order_restores_actor_before_front_loaded_control_call():
    messages = [
        _message("RegistrationBoundary", "RegistrationController", "registerForSection()"),
        _message("RegistrationController", "RegistrationBoundary", "Enrollment", type="return"),
        _message("Student", "RegistrationBoundary", "requestRegistration()"),
    ]
    kinds = {
        "Student": "actor",
        "RegistrationBoundary": "boundary",
        "RegistrationController": "control",
    }

    ordered = normalize_sequence_entry_order(messages, kinds)

    assert [message["label"] for message in ordered] == [
        "requestRegistration()", "registerForSection()", "Enrollment",
    ]
    model = {
        "Participants": [
            _participant("Student", "actor"),
            _participant("RegistrationBoundary", "boundary"),
            _participant("RegistrationController", "control"),
        ],
        "Messages": ordered,
    }
    assert sequence_initial_entry(model, {}) == []
    assert sequence_causal_call_chain(model, {}) == []


def test_sequence_repair_score_counts_every_message_in_duplicate_run():
    findings = [
        Finding(
            "sequence.duplicate-consecutive-messages",
            "동일한 메시지 'display()'가 4회 연달아 중복 기입되어 있음 (Control → Boundary)",
            "Control -> Boundary : display() (messages 1-4)",
        )
    ]

    score = _sequence_repair_score(findings)

    assert sum(score) == 4


def test_llm_contract_normalization_repairs_only_mechanical_links() -> None:
    class_diagram = """@startuml
class OrderBoundary <<Boundary>> { + submit(data:OrderData): Receipt }
class OrderControl <<Control>> { + persist(data:OrderData): Order }
OrderBoundary ..> OrderControl
@enduml"""
    model = {
        "Participants": [
            _participant("Buyer", "actor"),
            _participant("OrderBoundary", "boundary", "OrderBoundary"),
            _participant("OrderControl", "control", "OrderControl"),
        ],
        "Messages": [
            _message(
                "Buyer", "OrderBoundary", "submit(data:OrderData)",
                call_id="entry", reply_to="", arguments=[{
                    "parameter": "data", "type": "Wrong", "source_kind": "state", "source_ref": "stale",
                }],
            ),
            _message(
                "OrderBoundary", "OrderControl", "persist(data:OrderData)",
                call_id="persist", reply_to="", arguments=[{
                    "parameter": "data", "type": "OrderData", "source_kind": "call_result", "source_ref": "entry",
                }],
                fragments=[{"id": "failure", "type": "alt", "branch": "else", "condition": "invalid"}],
            ),
            {
                **_message("Buyer", "OrderBoundary", "Receipt", type="return"),
                "call_id": "", "reply_to": "entry", "arguments": [],
            },
        ],
    }

    normalized = normalize_sequence_contracts(model, class_diagram)
    calls = [message for message in normalized["Messages"] if message["type"] in {"sync", "self"}]
    returns = [message for message in normalized["Messages"] if message["type"] == "return"]

    assert [call["call_id"] for call in calls] == ["call-1", "call-2"]
    assert calls[0]["arguments"] == [{
        "parameter": "data", "type": "OrderData", "source_kind": "input", "source_ref": "UC1:main:1",
    }]
    assert calls[1]["arguments"] == [{
        "parameter": "data", "type": "OrderData", "source_kind": "input", "source_ref": "UC1:main:1",
    }]
    assert calls[1]["fragments"] == [{"id": "failure", "type": "opt", "branch": "main", "condition": "invalid"}]
    assert {(item["reply_to"], item["label"]) for item in returns} == {
        ("call-1", "Receipt"), ("call-2", "Order"),
    }


def test_normalizer_links_return_to_preceding_repeated_call() -> None:
    class_diagram = """@startuml
class CatalogBoundary <<Boundary>> { + search(filter:Filter): List<Course> }
class CatalogControl <<Control>> { + search(filter:Filter): List<Course> }
CatalogBoundary ..> CatalogControl
@enduml"""
    model = {
        "Participants": [
            _participant("CatalogBoundary", "boundary", "CatalogBoundary"),
            _participant("CatalogControl", "control", "CatalogControl"),
        ],
        "Messages": [
            _message("CatalogBoundary", "CatalogControl", "search(filter:Filter)", call_id="first", reply_to=""),
            {**_message("CatalogControl", "CatalogBoundary", "List<Course>", type="return"), "call_id": "", "reply_to": "first", "arguments": []},
            _message("CatalogBoundary", "CatalogControl", "search(filter:Filter)", call_id="later", reply_to=""),
        ],
    }

    normalized = normalize_sequence_contracts(model, class_diagram)

    replies = [item for item in normalized["Messages"] if item["type"] == "return"]
    assert replies[0]["reply_to"] == "call-1"


def test_normalizer_defers_boundary_return_until_control_returns() -> None:
    class_diagram = """@startuml
class AuthenticationBoundary <<Boundary>> {
  + submitCredentials(username : String, password : String): User
}
class AuthenticationControl <<Control>> {
  + authenticate(username : String, password : String): User
}
AuthenticationBoundary ..> AuthenticationControl
@enduml"""
    model = {
        "Participants": [
            _participant("User", "actor"),
            _participant("AuthenticationBoundary", "boundary", "AuthenticationBoundary"),
            _participant("AuthenticationControl", "control", "AuthenticationControl"),
        ],
        "Messages": [
            _message(
                "User", "AuthenticationBoundary",
                "submitCredentials(username:String,password:String)",
                call_id="submit", reply_to="", arguments=[],
            ),
            {
                **_message("AuthenticationBoundary", "User", "User", type="return"),
                "call_id": "", "reply_to": "submit", "arguments": [],
            },
            _message(
                "AuthenticationBoundary", "AuthenticationControl",
                "authenticate(username:String,password:String)",
                call_id="authenticate", reply_to="", arguments=[],
                step_ids=["UC1:main:2"],
            ),
            {
                **_message("AuthenticationControl", "AuthenticationBoundary", "User", type="return"),
                "call_id": "", "reply_to": "authenticate", "arguments": [],
                "step_ids": ["UC1:main:2"],
            },
        ],
    }

    normalized = normalize_sequence_contracts(model, class_diagram)

    assert [
        (message["source"], message["target"], message["label"])
        for message in normalized["Messages"]
    ] == [
        ("User", "AuthenticationBoundary", "submitCredentials(username:String,password:String)"),
        ("AuthenticationBoundary", "AuthenticationControl", "authenticate(username:String,password:String)"),
        ("AuthenticationControl", "AuthenticationBoundary", "User"),
        ("AuthenticationBoundary", "User", "User"),
    ]
    diagram = generate_sequence_from_model(normalized)
    assert diagram.index("AuthenticationBoundary -> AuthenticationControl") < diagram.index(
        "AuthenticationControl --> AuthenticationBoundary"
    ) < diagram.index("AuthenticationBoundary --> User")


def test_normalizer_restores_unambiguous_parameter_types_and_drops_placeholder_calls() -> None:
    class_diagram = """@startuml
class DropBoundary <<Boundary>> {
  + requestDrop(sectionId : String): void
}
class DropController <<Control>> {
  + dropSection(studentId : String, sectionId : String): void
}
@enduml"""
    model = {
        "Participants": [
            _participant("Student", "actor"),
            _participant("DropBoundary", "boundary", "DropBoundary"),
            _participant("DropController", "control", "DropController"),
        ],
        "Messages": [
            _message("Student", "DropBoundary", "requestDrop(sectionId)", call_id="x", reply_to=""),
            _message("DropBoundary", "DropController", "dropSection(studentId, sectionId)", call_id="y", reply_to=""),
            _message("DropBoundary", "DropBoundary", "self()", type="self", call_id="z", reply_to=""),
        ],
    }

    normalized = normalize_sequence_contracts(model, class_diagram)

    assert [message["label"] for message in normalized["Messages"]] == [
        "requestDrop(sectionId:String)",
        "dropSection(studentId:String,sectionId:String)",
    ]


def test_normalizer_drops_blank_return_for_void_receiver_method() -> None:
    class_diagram = """@startuml
class DepartmentBoundary <<Boundary>> { + deleteDepartment(departmentId : String): void }
class DepartmentController <<Control>> { + deleteDepartment(departmentId : String): void }
DepartmentBoundary ..> DepartmentController
@enduml"""
    model = {
        "Participants": [
            _participant("Administrator", "actor"),
            _participant("DepartmentBoundary", "boundary", "DepartmentBoundary"),
            _participant("DepartmentController", "control", "DepartmentController"),
        ],
        "Messages": [
            _message("Administrator", "DepartmentBoundary", "deleteDepartment(departmentId:String)", call_id="entry", reply_to="", arguments=[]),
            _message("DepartmentBoundary", "DepartmentController", "deleteDepartment(departmentId:String)", call_id="delete", reply_to="", arguments=[]),
            {**_message("DepartmentController", "DepartmentBoundary", "", type="return"), "call_id": "", "reply_to": "delete", "arguments": []},
        ],
    }

    normalized = normalize_sequence_contracts(model, class_diagram)

    assert not [message for message in normalized["Messages"] if message["type"] == "return"]


def test_llm_extraction_receives_the_allowed_interaction_routes() -> None:
    model = {"Participants": [], "Messages": []}
    with patch(
        "app.design.services.sequence_diagram.extractor.parse_structured",
        return_value=model,
    ) as parse:
        extract_sequence_model(
            '{"use_case_id": "UC1"}',
            "@startuml\n@enduml",
            [{"boundary_class": "OrderBoundary", "control_class": "OrderControl"}],
        )

    assert "[Candidate interaction routes]" in parse.call_args.args[0][1]["content"]
    assert "OrderBoundary" in parse.call_args.args[0][1]["content"]


def test_multiple_callable_boundaries_are_not_lexically_ranked() -> None:
    classes = {
        name: {
            "name": name,
            "kind": "boundary",
            "methods": ["manage(operation:String, data:String)"],
        }
        for name in (
            "AlphaManagementBoundary",
            "BetaManagementBoundary",
        )
    }

    assert _only_callable_class(classes, "boundary") is None


def test_targeted_sequence_revision_sends_only_the_affected_diagram(monkeypatch) -> None:
    current = {
        "Diagrams": [
            {"use_case_id": "UC1", "use_case_name": "First", "Participants": [], "Messages": []},
            {"use_case_id": "UC2", "use_case_name": "Second", "Participants": [], "Messages": []},
        ],
        "class_diagram_hash": "known-version",
        "MethodProposals": [{
            "id": "method:OrderControl:reserveOrder()",
            "class_name": "OrderControl",
            "method": "reserveOrder()",
            "reason": "requires review",
        }],
    }
    received: dict[str, object] = {}

    def capture(messages, schema):
        received["messages"] = messages
        received["schema"] = schema
        return {"Diagrams": [], "class_diagram_hash": "known-version", "MethodProposals": []}

    monkeypatch.setattr(sequence_reviser, "parse_sequence_structured", capture)

    sequence_reviser.revise_sequence_model(
        current, "repair UC2", "[context]", {"UC2"}
    )

    prompt = received["messages"][1]["content"]
    assert '"use_case_id": "UC2"' in prompt
    assert '"use_case_id": "UC1"' not in prompt
    assert "reserveOrder" not in prompt
    assert "Scoped automatic validation repair" in prompt


def test_targeted_sequence_revision_context_excludes_other_use_cases() -> None:
    state = {
        "usecase_spec": {
            "actors": [{"name": "Student"}],
            "use_cases": [
                {"id": "UC1", "name": "First"},
                {"id": "UC2", "name": "Second"},
            ],
            "use_case_specs": [
                {"use_case_id": "UC1", "main_scenario": [{"sentence": "first only"}]},
                {"use_case_id": "UC2", "main_scenario": [{"sentence": "second only"}]},
            ],
        },
        "class_diagram_puml": "@startuml\nclass Order\n@enduml",
    }

    context = _sequence_revision_context(state, {"UC2"})

    assert '"id": "UC2"' in context
    assert "second only" in context
    assert '"id": "UC1"' not in context
    assert "first only" not in context
    assert "class Order" in context


def test_targeted_reassembly_replaces_only_affected_use_case_card() -> None:
    usecase_spec = {
        "use_cases": [
            {"id": "UC1", "name": "Submit order", "primary_actor": "User"},
            {"id": "UC2", "name": "Keep card", "primary_actor": "User"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "name": "Submit order",
                "primary_actor": "User",
                "main_scenario": [{"step_number": 1, "sentence": "The user submits an order."}],
                "extensions": [],
            },
            {
                "use_case_id": "UC2",
                "name": "Keep card",
                "primary_actor": "User",
                "main_scenario": [{"step_number": 1, "sentence": "The user views an order."}],
                "extensions": [],
            },
        ],
    }
    current = {
        "Diagrams": [
            {"use_case_id": "UC1", "use_case_name": "Submit order", "Participants": [], "Messages": [], "UnresolvedSteps": []},
            {"use_case_id": "UC2", "use_case_name": "Keep card", "Participants": [], "Messages": [{"label": "keep()"}], "UnresolvedSteps": []},
        ],
        "class_diagram_hash": "old",
    }
    class_puml = """@startuml
class OrderBoundary <<Boundary>> {
  + submitOrder(): void
}
@enduml"""

    rebuilt = reassemble_sequence_diagrams(current, usecase_spec, class_puml, {"UC1"})

    assert rebuilt["Diagrams"][0]["use_case_id"] == "UC1"
    assert rebuilt["Diagrams"][0]["Messages"][0]["label"] == "submitOrder()"
    assert rebuilt["Diagrams"][1] is current["Diagrams"][1]
    assert rebuilt["MethodProposals"] == []


def test_system_receives_actor_request_uses_trigger_as_boundary_entry() -> None:
    specification = {
        "use_cases": [{"id": "UC1", "name": "Drop course", "primary_actor": "Student"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Drop course",
            "primary_actor": "Student",
            "trigger": "Student requests to drop a specific course",
            "main_scenario": [{
                "step_number": 1,
                "sentence": "System receives the drop request for the course",
            }],
            "extensions": [],
        }],
    }
    class_puml = """@startuml
class EnrollmentBoundary <<Boundary>> {
  + dropCourse(studentId : String, courseId : String): void
}
@enduml"""

    generated = extract_sequence_diagrams(specification, class_puml)
    message = generated["Diagrams"][0]["Messages"][0]

    assert message["source"] == "Student"
    assert message["target"] == "EnrollmentBoundary"
    assert message["label"] == "dropCourse(studentId:String,courseId:String)"


def test_shortened_leading_role_name_enters_the_declared_boundary() -> None:
    specification = {
        "use_cases": [{"id": "UC1", "name": "Maintain section", "primary_actor": "Registrar Staff"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Maintain section",
            "primary_actor": "Registrar Staff",
            "main_scenario": [
                {"step_number": 1, "sentence": "Registrar enters the section details"},
                {"step_number": 2, "sentence": "System saves the section"},
            ],
            "extensions": [],
        }],
    }
    class_puml = """@startuml
class SectionBoundary <<Boundary>> { + maintainSection(): Section }
class SectionControl <<Control>> { + saveSection(): Section }
SectionBoundary ..> SectionControl
@enduml"""

    result = extract_sequence_diagrams(specification, class_puml)

    assert result["Diagrams"][0]["UnresolvedSteps"] == []
    assert result["Diagrams"][0]["Messages"][0]["source"] == "Registrar_Staff"
    assert result["Diagrams"][0]["Messages"][0]["target"] == "SectionBoundary"


def test_repeated_control_operation_is_not_rejected_as_distinct_actor_inputs() -> None:
    specification = {
        "use_cases": [{"id": "UC1", "name": "Maintain order", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Maintain order",
            "primary_actor": "Buyer",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer submits the order"},
                {"step_number": 2, "sentence": "System validates the order"},
                {"step_number": 3, "sentence": "System persists the order"},
            ],
            "extensions": [],
        }],
    }
    class_puml = """@startuml
class OrderBoundary <<Boundary>> { + submitOrder(): Order }
class OrderControl <<Control>> { + saveOrder(): Order }
OrderBoundary ..> OrderControl
@enduml"""

    result = extract_sequence_diagrams(specification, class_puml)
    findings = sequence_diagram_findings(
        result,
        {"usecase_spec": specification, "class_diagram_puml": class_puml},
    )

    assert not [
        finding for finding in findings
        if finding.rule_id == "sequence.step-operation-distinctness"
    ]


def test_extracts_one_sequence_diagram_for_each_use_case():
    specification = {
        "use_cases": [
            {"id": "UC1", "name": "Create order"},
            {"id": "UC2", "name": "Cancel order"},
        ],
        "use_case_specs": [
            {"use_case_id": "UC1", "main_scenario": []},
            {"use_case_id": "UC2", "main_scenario": []},
        ],
    }

    def extracted(scenario_text, class_diagram_puml):
        use_case_id = "UC1" if '"UC1"' in scenario_text else "UC2"
        return {
            "Participants": [],
            "Messages": [],
        }

    with patch(
        "app.design.services.sequence_diagram.extractor.extract_sequence_model",
        side_effect=extracted,
    ) as extract:
        result = extract_sequence_diagrams(specification, "class Order")

    assert extract.call_count == 2
    assert [item["use_case_id"] for item in result["Diagrams"]] == ["UC1", "UC2"]
    assert [item["use_case_name"] for item in result["Diagrams"]] == [
        "Create order",
        "Cancel order",
    ]
    assert result["class_diagram_hash"] == hashlib.sha256(b"class Order").hexdigest()


def test_normalizes_missing_message_participants_from_class_diagram():
    model = {
        "Participants": [_participant("CourseDetailsBoundary", "boundary")],
        "Messages": [
            _message(
                "CourseDetailsBoundary",
                "ScheduleController",
                "getSchedule()",
            )
        ],
    }
    class_diagram = """@startuml
class CourseDetailsBoundary <<Boundary>> {
  + request()
}
class ScheduleController <<Control>> {
  + getSchedule()
}
@enduml"""

    normalized = normalize_sequence_participants(model, class_diagram)

    assert [item["alias"] for item in normalized["Participants"]] == [
        "CourseDetailsBoundary",
        "ScheduleController",
    ]


def test_boundary_forwards_to_single_control_method_when_names_differ():
    boundary = {
        "name": "BrowseBoundary",
        "kind": "boundary",
        "methods": ["browseCatalog(department:String)"],
        "method_returns": {"browseCatalog(department:String)": "List<Course>"},
    }
    control = {
        "name": "BrowseControl",
        "kind": "control",
        "methods": ["getCourses(department:String)"],
        "method_returns": {"getCourses(department:String)": "List<Course>"},
    }
    plan = {
        "use_case_id": "UC1",
        "use_case_name": "Browse courses",
        "actor": "Student",
        "step_id": "UC1:main:1",
        "sentence": "Student starts browsing courses",
        "fragment": {},
        "actor_led": True,
        "boundary": boundary,
        "control": control,
        "selected_class": boundary,
        "selected_method": "browseCatalog(department:String)",
        "candidates": [],
    }

    result = _assemble_deterministic_diagrams(
        [plan],
        {},
        {item["name"]: item for item in (boundary, control)},
    )
    calls = [
        (message["source"], message["target"], message["label"])
        for message in result[0]["Messages"]
        if message.get("type") != "return"
    ]

    assert ("BrowseBoundary", "BrowseControl", "getCourses(department:String)") in calls
    assert result[0]["UnresolvedSteps"] == []


def test_normalization_removes_inactive_llm_participants():
    model = {
        "Participants": [
            _participant("CourseApiBoundary", "boundary", "CourseApiBoundary"),
            _participant("CourseController", "control", "CourseController"),
            _participant("Course", "entity", "Course"),
        ],
        "Messages": [
            _message(
                "CourseApiBoundary",
                "CourseController",
                "createCourse()",
            )
        ],
    }
    class_diagram = """@startuml
class CourseApiBoundary <<Boundary>> {
  + createCourse()
}
class CourseController <<Control>> {
  + createCourse()
}
class Course <<Entity>> {
}
@enduml"""

    normalized = normalize_sequence_participants(model, class_diagram)

    assert [item["alias"] for item in normalized["Participants"]] == [
        "CourseApiBoundary",
        "CourseController",
    ]


def test_raw_cockburn_example_is_normalized_to_a_sequence_collection():
    raw = {
        "UseCase": {
            "UseCaseName": "Place order",
            "PrimaryActor": "Buyer",
            "MainSuccessScenario": [
                {"step": 1, "description": "Buyer places an order."}
            ],
            "Extensions": [],
        }
    }
    with patch(
        "app.design.services.sequence_diagram.extractor.extract_sequence_model",
        return_value={"Participants": [], "Messages": []},
    ) as extract:
        result = extract_sequence_diagrams(raw, "class Order")

    assert [item["use_case_id"] for item in result["Diagrams"]] == ["UC1"]
    scenario = extract.call_args.args[0]
    assert '"use_case_id": "UC1"' in scenario
    assert '"step_number": 1' in scenario


def test_unique_grounded_methods_are_assembled_without_semantic_selector():
    specification = {
        "use_cases": [
            {
                "id": "UC1",
                "name": "Create order",
                "primary_actor": "Buyer",
            }
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "name": "Create order",
                "primary_actor": "Buyer",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer creates an order"},
                    {"step_number": 2, "sentence": "System processes the order"},
                ],
                "extensions": [],
            }
        ],
    }
    class_diagram = """@startuml
class OrderApi <<Boundary>> {
  + createOrder()
}
class OrderControl <<Control>> {
  + processOrder()
}
OrderApi ..> OrderControl
@enduml"""

    result = extract_sequence_diagrams(specification, class_diagram)
    diagram = result["Diagrams"][0]
    assert [message["label"] for message in diagram["Messages"]] == [
        "createOrder()",
        "processOrder()",
    ]
    assert [message["step_ids"] for message in diagram["Messages"]] == [
        ["UC1:main:1"],
        ["UC1:main:2"],
    ]


def test_ambiguous_control_methods_delegate_full_interaction_to_llm():
    specification = {
        "use_cases": [{"id": "UC5", "name": "Enroll in course", "primary_actor": "Student"}],
        "use_case_specs": [{
            "use_case_id": "UC5",
            "name": "Enroll in course",
            "primary_actor": "Student",
            "main_scenario": [
                {"step_number": 1, "sentence": "Student submits an enrollment request"},
                {"step_number": 2, "sentence": "System checks seat availability"},
                {"step_number": 3, "sentence": "System records the enrollment"},
            ],
            "extensions": [],
        }],
    }
    class_diagram = """@startuml
class CourseApi <<Boundary>> { createCourse() }
class CourseController <<Control>> { createCourse() }
class EnrollmentApi <<Boundary>> { enrollInCourse() }
class EnrollmentController <<Control>> {
  enrollInCourse()
  checkSeatAvailability()
  recordEnrollment()
}
CourseApi ..> CourseController
EnrollmentApi ..> EnrollmentController
@enduml"""

    with (
        patch(
            "app.design.services.sequence_diagram.extractor._select_use_case_route",
            return_value=(
                {"name": "EnrollmentApi", "kind": "boundary", "methods": ["enrollInCourse()"]},
                {"name": "EnrollmentController", "kind": "control", "methods": ["enrollInCourse()"]},
            ),
        ),
        patch(
            "app.design.services.sequence_diagram.extractor.extract_sequence_model",
            return_value={"Participants": [], "Messages": []},
        ) as extract,
    ):
        result = extract_sequence_diagrams(specification, class_diagram)

    extract.assert_called_once()
    assert result["Diagrams"][0]["Messages"] == []
    assert result["Diagrams"][0]["UnresolvedSteps"]


def test_unique_element_selection_is_not_sent_to_llm():
    specification = {
        "use_cases": [
            {"id": "UC1", "name": "Order", "primary_actor": "Buyer"}
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "name": "Order",
                "primary_actor": "Buyer",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer initiates a workflow"},
                    {"step_number": 2, "sentence": "System completes the workflow"},
                ],
                "extensions": [],
            }
        ],
    }
    class_diagram = """@startuml
class OrderApi <<Boundary>> {
  + submitOrder()
}
class OrderControl <<Control>> {
  + persistOrder()
}
OrderApi ..> OrderControl
@enduml"""

    with patch(
        "app.design.services.sequence_diagram.extractor.parse_structured"
    ) as llm:
        result = extract_sequence_diagrams(specification, class_diagram)

    llm.assert_not_called()
    assert result["Diagrams"][0]["Messages"][-1]["label"] == "persistOrder()"


def test_rule_based_generation_emits_return_for_non_void_method():
    specification = {
        "use_cases": [{"id": "UC1", "name": "Order", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Order",
            "primary_actor": "Buyer",
            "main_scenario": [{"step_number": 1, "sentence": "Buyer creates an order"}],
            "extensions": [],
        }],
    }
    class_diagram = """@startuml
class OrderApi <<Boundary>> {
  + createOrder(): Order
}
@enduml"""

    result = extract_sequence_diagrams(specification, class_diagram)
    messages = result["Diagrams"][0]["Messages"]

    assert [(message["type"], message["label"]) for message in messages] == [
        ("sync", "createOrder()"),
        ("return", "Order"),
    ]
    assert messages[1]["reply_to"] == messages[0]["call_id"]


def test_repeated_control_candidate_uses_llm_semantics_and_leaves_unmapped_branch_visible():
    specification = {
        "use_cases": [{"id": "UC1", "name": "Register", "primary_actor": "Student"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Register",
            "primary_actor": "Student",
            "main_scenario": [
                {"step_number": 1, "sentence": "Student requests registration"},
                {"step_number": 2, "sentence": "System registers the student"},
            ],
            "extensions": [{
                "label": "2a",
                "branch_step": 2,
                "condition": "section capacity is reached",
                "handling_steps": [{
                    "sub_step": "2a1",
                    "sentence": "System informs the student that registration failed",
                }],
            }],
        }],
    }
    class_diagram = """@startuml
class RegistrationScreen <<Boundary>> {
  + requestRegistration(): void
}
class RegistrationControl <<Control>> {
  + registerSection(): Enrollment
}
RegistrationScreen ..> RegistrationControl
@enduml"""
    llm_model = {
        "Participants": [
            _participant("Student", "actor"),
            _participant("RegistrationScreen", "boundary", "RegistrationScreen"),
            _participant("RegistrationControl", "control", "RegistrationControl"),
        ],
        "Messages": [
            {
                **_message(
                    "Student", "RegistrationScreen", "requestRegistration()",
                    call_id="call-1", reply_to="", arguments=[],
                ),
                "step_ids": ["UC1:main:1"],
            },
            {
                **_message(
                    "RegistrationScreen", "RegistrationControl", "registerSection()",
                    call_id="call-2", reply_to="", arguments=[],
                ),
                "step_ids": ["UC1:main:2"],
            },
            {
                "source": "RegistrationControl",
                "target": "RegistrationScreen",
                "label": "Enrollment",
                "type": "return",
                "fragments": [],
                "use_case_ids": ["UC1"],
                "step_ids": ["UC1:main:2"],
                "call_id": "",
                "reply_to": "call-2",
                "arguments": [],
            },
        ],
    }

    with patch(
        "app.design.services.sequence_diagram.extractor.extract_sequence_model",
        return_value=llm_model,
    ) as extract:
        result = extract_sequence_diagrams(specification, class_diagram)

    extract.assert_called_once()
    messages = result["Diagrams"][0]["Messages"]
    assert [message["label"] for message in messages].count("registerSection()") == 1
    assert result["Diagrams"][0]["UnresolvedSteps"] == []
    assert result["Diagrams"][0]["NarrativeSteps"] == [{
        "step_id": "UC1:extension:2a:2a1",
        "sentence": "System informs the student that registration failed",
        "reason": "Narrative outcome or condition represented by the surrounding interaction.",
        "candidates": [],
    }]


def test_ambiguous_flow_keeps_steps_visible_when_llm_generation_fails():
    specification = {
        "use_cases": [{"id": "UC1", "name": "Order", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Order",
            "primary_actor": "Buyer",
            "main_scenario": [{"step_number": 1, "sentence": "Buyer initiates a workflow"}],
            "extensions": [],
        }],
    }
    class_diagram = """@startuml
class OrderApi <<Boundary>> {
  + submitOrder()
  + cancelOrder()
}
@enduml"""

    with patch(
        "app.design.services.sequence_diagram.extractor.extract_sequence_model",
        side_effect=StructuredLlmError("generation unavailable"),
    ):
        result = extract_sequence_diagrams(specification, class_diagram)

    assert result["Diagrams"][0]["Messages"] == []
    assert result["Diagrams"][0]["UnresolvedSteps"] == [
        {
            "step_id": "UC1:main:1",
            "sentence": "Buyer initiates a workflow",
            "reason": "Sequence planning failed before a grounded interaction could be assembled.",
            "candidates": [
                "OrderApi.submitOrder()",
                "OrderApi.cancelOrder()",
            ],
        }
    ]
    assert "Needs review" in generate_sequence_from_model(result["Diagrams"][0])


def test_llm_failure_keeps_the_selected_use_case_route_in_review_diagram():
    specification = {
        "use_cases": [{"id": "UC1", "name": "Maintain section", "primary_actor": "Administrator"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Maintain section",
            "primary_actor": "Administrator",
            "main_scenario": [{"step_number": 1, "sentence": "Administrator creates a section"}],
            "extensions": [],
        }],
    }
    class_diagram = """@startuml
class AcademicTermBoundary <<Boundary>> { + createTerm(): void }
class AcademicTermController <<Control>> { + createTerm(): void }
class SectionBoundary <<Boundary>> { + createSection(): void }
class SectionController <<Control>> { + createSection(): void }
AcademicTermBoundary ..> AcademicTermController
SectionBoundary ..> SectionController
@enduml"""

    with (
        patch(
            "app.design.services.sequence_diagram.extractor._select_use_case_route",
            return_value=(
                {"name": "SectionBoundary", "kind": "boundary", "methods": ["createSection()"]},
                {"name": "SectionController", "kind": "control", "methods": ["createSection()"]},
            ),
        ),
        patch(
            "app.design.services.sequence_diagram.extractor.extract_sequence_model",
            side_effect=StructuredLlmError("malformed return label"),
        ),
    ):
        result = extract_sequence_diagrams(specification, class_diagram)

    participants = result["Diagrams"][0]["Participants"]
    assert [participant["source_class"] for participant in participants] == [
        "", "SectionBoundary", "SectionController",
    ]


def test_intent_and_attribute_entry_can_share_one_boundary_submission():
    specification = {
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Administrator initiates creation of a department"},
                {"step_number": 2, "sentence": "Administrator supplies the department attributes"},
            ],
        }],
    }
    model = {
        "use_case_id": "UC1",
        "Participants": [
            _participant("Administrator", "actor"),
            _participant("DepartmentBoundary", "boundary", "DepartmentBoundary"),
        ],
        "Messages": [
            _message(
                "Administrator", "DepartmentBoundary", "createDepartment()",
                step_ids=["UC1:main:1", "UC1:main:2"], call_id="entry", reply_to="", arguments=[],
            ),
        ],
    }

    findings = sequence_diagram_findings(model, {"usecase_spec": specification})

    assert not [
        finding for finding in findings
        if finding.rule_id == "sequence.step-operation-distinctness"
    ]


def test_each_use_case_stays_renderable_when_semantic_selection_fails():
    specification = {
        "use_cases": [
            {"id": "UC1", "name": "Create order", "primary_actor": "Buyer"},
            {"id": "UC2", "name": "Cancel order", "primary_actor": "Buyer"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "name": "Create order",
                "primary_actor": "Buyer",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer creates an order"}
                ],
                "extensions": [],
            },
            {
                "use_case_id": "UC2",
                "name": "Cancel order",
                "primary_actor": "Buyer",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer cancels an order"}
                ],
                "extensions": [],
            },
        ],
    }
    class_diagram = """@startuml
class OrderApi <<Boundary>> {
  + createOrder()
  + cancelOrder()
}
@enduml"""

    with patch(
        "app.design.services.sequence_diagram.extractor.parse_structured",
        side_effect=StructuredLlmError("selection unavailable"),
    ):
        result = extract_sequence_diagrams(specification, class_diagram)

    assert [diagram["use_case_id"] for diagram in result["Diagrams"]] == ["UC1", "UC2"]
    assert all(diagram["UnresolvedSteps"] for diagram in result["Diagrams"])
    rendered = generate_sequence_from_model(result)
    assert "@startuml UC1" in rendered
    assert "@startuml UC2" in rendered
    assert rendered.count("Needs review") == 2

    state = {"usecase_spec": specification}
    assert sequence_usecase_coverage(result["Diagrams"][0], state) == []


def test_multiple_boundaries_delegate_the_semantic_choice_to_the_llm():
    specification = {
        "use_cases": [{"id": "UC3", "name": "Register for a course section", "primary_actor": "Student"}],
        "use_case_specs": [{
            "use_case_id": "UC3",
            "name": "Register for a course section",
            "primary_actor": "Student",
            "main_scenario": [
                {"step_number": 1, "sentence": "Student indicates the desired course section"},
            ],
            "extensions": [],
        }],
    }
    class_diagram = """@startuml
class CourseCatalogScreen <<Boundary>> { requestCatalog() }
class RegistrationScreen <<Boundary>> { submitRegistration(studentId:String, sectionId:String) }
class CourseSearchController <<Control>> { browseCatalog() }
class RegistrationController <<Control>> { registerSection(studentId:String, sectionId:String) }
CourseCatalogScreen ..> CourseSearchController
RegistrationScreen ..> RegistrationController
@enduml"""

    with (
        patch(
            "app.design.services.sequence_diagram.extractor._select_use_case_route",
            return_value=(
                {"name": "RegistrationScreen", "kind": "boundary", "methods": []},
                {"name": "RegistrationController", "kind": "control", "methods": []},
            ),
        ),
        patch(
            "app.design.services.sequence_diagram.extractor.extract_sequence_model",
            return_value={
            "Participants": [
                _participant("Student", "actor"),
                _participant("RegistrationScreen", "boundary", "RegistrationScreen"),
                _participant("RegistrationController", "control", "RegistrationController"),
            ],
            "Messages": [{
                **_message(
                    "Student", "RegistrationScreen",
                    "submitRegistration(studentId:String,sectionId:String)",
                    call_id="call-1", reply_to="", arguments=[],
                ),
                "use_case_ids": ["UC3"],
                "step_ids": ["UC3:main:1"],
            }],
            },
        ) as extract,
    ):
        result = extract_sequence_diagrams(specification, class_diagram)

    extract.assert_called_once()
    assert [
        (message["source"], message["target"], message["label"])
        for message in result["Diagrams"][0]["Messages"]
    ] == [
        ("Student", "RegistrationScreen", "submitRegistration(studentId:String,sectionId:String)")
    ]
    assert result["Diagrams"][0]["UnresolvedSteps"] == []


def test_multiple_boundaries_never_fall_back_to_input_order():
    specification = {
        "use_cases": [{"id": "UC4", "name": "Drop a course section", "primary_actor": "Student"}],
        "use_case_specs": [{
            "use_case_id": "UC4",
            "name": "Drop a course section",
            "primary_actor": "Student",
            "main_scenario": [{"step_number": 1, "sentence": "Student requests to drop the target course section"}],
            "extensions": [],
        }],
    }
    class_diagram = """@startuml
class CourseSearchScreen <<Boundary>> { selectCourse(courseId:String) }
class DropScreen <<Boundary>> { submitDrop(studentId:String, sectionId:String) }
class CourseSearchController <<Control>> { viewCourseDetails(courseId:String) }
class DropController <<Control>> { dropSection(studentId:String, sectionId:String) }
CourseSearchScreen ..> CourseSearchController
DropScreen ..> DropController
@enduml"""

    with (
        patch(
            "app.design.services.sequence_diagram.extractor._select_use_case_route",
            return_value=(
                {"name": "DropScreen", "kind": "boundary", "methods": []},
                {"name": "DropController", "kind": "control", "methods": []},
            ),
        ),
        patch(
            "app.design.services.sequence_diagram.extractor.extract_sequence_model",
            return_value={
            "Participants": [
                _participant("Student", "actor"),
                _participant("DropScreen", "boundary", "DropScreen"),
                _participant("DropController", "control", "DropController"),
            ],
            "Messages": [{
                **_message(
                    "Student", "DropScreen",
                    "submitDrop(studentId:String,sectionId:String)",
                    call_id="call-1", reply_to="", arguments=[],
                ),
                "use_case_ids": ["UC4"],
                "step_ids": ["UC4:main:1"],
            }],
            },
        ) as extract,
    ):
        result = extract_sequence_diagrams(specification, class_diagram)

    extract.assert_called_once()
    assert result["Diagrams"][0]["Messages"][0]["target"] == "DropScreen"
    assert result["Diagrams"][0]["UnresolvedSteps"] == []


def test_semantic_selector_cannot_escape_the_selected_boundary_control_route():
    specification = {
        "use_cases": [{"id": "UC1", "name": "Maintain course", "primary_actor": "Registrar"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Maintain course",
            "primary_actor": "Registrar",
            "main_scenario": [
                {"step_number": 1, "sentence": "Registrar submits course data"},
                {"step_number": 2, "sentence": "System maintains the course record"},
            ],
            "extensions": [],
        }],
    }
    class_diagram = """@startuml
class CourseForm <<Boundary>> { submitCourse(courseData:CourseData) }
class CatalogController <<Control>> { listCatalog() }
class CourseController <<Control>> { maintainCourse(courseData:CourseData) }
CourseForm ..> CatalogController
@enduml"""

    result = extract_sequence_diagrams(specification, class_diagram)

    assert [
        (message["target"], message["label"])
        for message in result["Diagrams"][0]["Messages"]
    ] == [
        ("CourseForm", "submitCourse(courseData:CourseData)"),
        ("CatalogController", "listCatalog()"),
    ]
    assert result["Diagrams"][0]["UnresolvedSteps"] == []


def test_duplicate_branch_operation_delegates_semantic_assembly_to_llm():
    specification = {
        "use_cases": [{"id": "UC1", "name": "Authenticate student", "primary_actor": "Student"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Authenticate student",
            "primary_actor": "Student",
            "main_scenario": [
                {"step_number": 1, "sentence": "Student provides credentials"},
                {"step_number": 2, "sentence": "System authenticates the student"},
            ],
            "extensions": [{
                "label": "2a", "branch_step": 2, "condition": "credentials are invalid",
                "handling_steps": [{"sub_step": "2a1", "sentence": "System reports authentication failure"}],
            }],
        }],
    }
    class_diagram = """@startuml
class LoginScreen <<Boundary>> { submitCredentials(credentials:String) }
class AuthenticationController <<Control>> { authenticate(credentials:String) }
LoginScreen ..> AuthenticationController
@enduml"""

    with patch(
        "app.design.services.sequence_diagram.extractor.extract_sequence_model",
        return_value={"Participants": [], "Messages": []},
    ) as extract:
        result = extract_sequence_diagrams(specification, class_diagram)

    extract.assert_called_once()
    assert result["Diagrams"][0]["Messages"] == []
    assert result["Diagrams"][0]["UnresolvedSteps"]


def test_extension_reusing_control_operation_delegates_semantic_assembly_to_llm():
    specification = {
        "use_cases": [{"id": "UC1", "name": "Authenticate student", "primary_actor": "Student"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Authenticate student",
            "primary_actor": "Student",
            "main_scenario": [
                {"step_number": 1, "sentence": "Student submits credentials"},
                {"step_number": 2, "sentence": "System validates credentials"},
            ],
            "extensions": [{
                "label": "1a", "branch_step": 1, "condition": "credentials are malformed",
                "handling_steps": [{"sub_step": "1a1", "sentence": "System validates credentials"}],
            }],
        }],
    }
    class_diagram = """@startuml
class LoginScreen <<Boundary>> { submitCredentials(credentials:String) }
class AuthenticationController <<Control>> { validateCredentials(credentials:String) }
LoginScreen ..> AuthenticationController
@enduml"""

    with patch(
        "app.design.services.sequence_diagram.extractor.extract_sequence_model",
        return_value={"Participants": [], "Messages": []},
    ) as extract:
        result = extract_sequence_diagrams(specification, class_diagram)

    extract.assert_called_once()
    assert result["Diagrams"][0]["Messages"] == []
    assert result["Diagrams"][0]["UnresolvedSteps"]


def test_multiple_boundary_candidates_delegate_semantic_assembly_to_llm():
    specification = {
        "use_cases": [{"id": "UC1", "name": "Action", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Action",
            "primary_actor": "Buyer",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer initiates an action"},
                {"step_number": 2, "sentence": "Buyer confirms the action"},
            ],
            "extensions": [],
        }],
    }
    class_diagram = """@startuml
class GatewayApi <<Boundary>> {
  + startFlow()
  + confirmFlow()
}
class GatewayControl <<Control>> {
  + processFlow()
}
GatewayApi ..> GatewayControl
@enduml"""

    with patch(
        "app.design.services.sequence_diagram.extractor.extract_sequence_model",
        return_value={"Participants": [], "Messages": []},
    ) as extract:
        result = extract_sequence_diagrams(specification, class_diagram)

    extract.assert_called_once()
    assert result["Diagrams"][0]["UnresolvedSteps"]


def test_use_case_summaries_without_specs_are_rejected_instead_of_collapsed():
    with pytest.raises(ValueError, match="requires use_case_specs"):
        normalize_sequence_usecase_spec(
            {"use_cases": [{"id": "UC1"}, {"id": "UC2"}]}
        )


def test_invalid_sequence_is_retained_but_not_rendered():
    model = {"Diagrams": [{"use_case_id": "UC1", "Participants": [], "Messages": []}]}
    result = render_node(SEQUENCE_DIAGRAM_SPEC)(
        {
            "sequence_diagram_model": model,
            "sequence_diagram_renderable": False,
        }
    )

    assert result["sequence_diagram_puml"] == ""
    assert result["sequence_diagram_syntax_valid"] is None
    assert result["sequence_diagram_syntax_errors"] == []


def test_sequence_collection_detects_stale_class_diagram_hash():
    findings = sequence_diagram_findings(
        {"Diagrams": [], "class_diagram_hash": "stale"},
        {"class_diagram_puml": "class Current"},
    )

    assert "sequence.class-diagram-version" in {
        finding.rule_id for finding in findings
    }


def test_sequence_structured_output_retries_with_schema_error(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "design_max_repair_iters", 1)
    valid = {"Participants": [], "Messages": []}
    with patch(
        "app.design.services.sequence_diagram.extractor.parse_structured",
        side_effect=[StructuredLlmError("return messages require a result label"), valid],
    ) as parse:
        result = parse_sequence_structured(
            [{"role": "system", "content": "rules"}],
            SequenceModel,
        )

    assert result == valid
    assert parse.call_count == 2
    retry_messages = parse.call_args_list[1].args[0]
    assert "STRUCTURED OUTPUT FAILED SCHEMA VALIDATION" in retry_messages[-1]["content"]
    assert "return messages require a result label" in retry_messages[-1]["content"]


def test_targeted_sequence_revision_preserves_other_use_case_diagrams():
    original = {
        "Diagrams": [
            {"use_case_id": "UC1", "Messages": [{"label": "before()"}]},
            {"use_case_id": "UC2", "Messages": [{"label": "keep()"}]},
        ]
    }
    revised = {
        "Diagrams": [
            {"use_case_id": "UC1", "Messages": [{"label": "after()"}]},
            {"use_case_id": "UC2", "Messages": [{"label": "changedByLlm()"}]},
        ]
    }

    merged = merge_model(SEQUENCE_DIAGRAM_SPEC, original, revised, {"UC1"})

    assert set(merged) == {"Diagrams"}
    assert merged["Diagrams"][0]["Messages"][0]["label"] == "after()"
    assert merged["Diagrams"][1]["Messages"][0]["label"] == "keep()"


def test_sequence_stage_asks_user_before_adding_receiver_method():
    assert SEQUENCE_DIAGRAM_SPEC.reconcile is None
    state = {
        "app_id": "test-app-id",
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]
        },
        "sequence_diagram_model": {
            "Participants": [_participant("OrderControl", "control", "OrderControl")],
            "Messages": [_message("OrderControl", "OrderControl", "reserveOrder()")],
        },
    }
    revised_bce = {
        "Classes": [
            {
                "className": "OrderControl",
                "methods": ["createOrder()", "reserveOrder()"],
            }
        ]
    }
    with (
        patch(
            "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
            return_value=revised_bce,
        ) as revise,
        patch("app.repositories.artifact_repository.save_stage") as save_stage,
    ):
        result = reconcile_class_methods(state)
    revise.assert_called_once()
    save_stage.assert_not_called()
    assert result["sequence_diagram_model"]["MethodProposals"][0]["method"] == "reserveOrder()"
    assert state["extracted_bce_classes"]["Classes"][0]["methods"] == ["createOrder()"]


def test_method_proposal_outside_affected_sequence_route_is_discarded():
    bce = {
        "Classes": [
            {"className": "CatalogBoundary", "stereotype": "Boundary"},
            {"className": "CatalogControl", "stereotype": "Control"},
            {"className": "SearchBoundary", "stereotype": "Boundary"},
            {"className": "SearchControl", "stereotype": "Control"},
        ],
        "Relationships": [
            {"source": "CatalogBoundary", "target": "CatalogControl"},
            {"source": "SearchBoundary", "target": "SearchControl"},
        ],
    }
    diagram = {
        "use_case_id": "UC1",
        "Participants": [
            _participant("CatalogBoundary", "boundary", "CatalogBoundary"),
            _participant("CatalogControl", "control", "CatalogControl"),
        ],
        "Messages": [],
    }
    proposals = [
        {
            "id": "method:CatalogBoundary:submitSearch()",
            "class_name": "CatalogBoundary",
            "method": "submitSearch(): void",
        },
        {
            "id": "method:SearchBoundary:submitSearch()",
            "class_name": "SearchBoundary",
            "method": "submitSearch(): void",
        },
    ]

    scoped = _scope_proposal_evidence(proposals, [diagram], {}, bce)

    assert [item["class_name"] for item in scoped] == ["CatalogBoundary"]


def test_sequence_stage_does_not_add_method_when_llm_declines():
    bce = {"Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]}
    state = {
        "extracted_bce_classes": bce,
        "sequence_diagram_model": {
            "Participants": [_participant("OrderControl", "control", "OrderControl")],
            "Messages": [_message("OrderControl", "OrderControl", "reserveOrder()")],
        },
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=bce,
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_called_once()
    assert result == {}
    assert bce["Classes"][0]["methods"] == ["createOrder()"]


def test_sequence_check_repairs_a_return_attached_to_an_async_call(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "2")
    participants = [
        _participant("User", "actor"),
        _participant("OrderBoundary", "boundary", "OrderBoundary"),
    ]
    async_call = {
        "source": "User",
        "target": "OrderBoundary",
        "label": "requestOrder()",
        "type": "async",
        "fragments": [],
        "use_case_ids": [],
        "step_ids": [],
    }
    returned = {
        "source": "OrderBoundary",
        "target": "User",
        "label": "Order",
        "type": "return",
        "fragments": [],
        "use_case_ids": [],
        "step_ids": [],
    }
    dirty = {"Participants": participants, "Messages": [async_call, returned]}
    repaired = {
        "Participants": participants,
        "Messages": [{**async_call, "type": "sync"}, returned],
    }
    state = {
        "class_diagram_puml": "class OrderBoundary <<Boundary>>",
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["requestOrder(): Order"]}
            ]
        },
        "sequence_diagram_model": dirty,
    }
    feedback_seen: list[str] = []

    def repair(current, feedback, current_state, targets):
        feedback_seen.append(feedback)
        return repaired

    spec = dataclasses.replace(SEQUENCE_DIAGRAM_SPEC, revise=repair)
    result = check_node(spec)(state)

    assert "sequence.async-call-has-no-return" in feedback_seen[0]
    assert result["sequence_diagram_model"] == repaired
    assert result["sequence_diagram_check"] == {
        "findings": [],
        "repair_iters": 1,
        "stopped": CLEAN,
    }


def test_sequence_check_rejects_repair_that_drops_existing_step_trace(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "1")
    participants = [
        _participant("User", "actor"),
        _participant("OrderBoundary", "boundary", "OrderBoundary"),
    ]
    async_call = _message(
        "User",
        "OrderBoundary",
        "requestOrder()",
        type="async",
    )
    returned = _message(
        "OrderBoundary",
        "User",
        "Order",
        type="return",
    )
    dirty = {"Participants": participants, "Messages": [async_call, returned]}
    lossy_repair = {
        "Participants": participants,
        "Messages": [{**async_call, "step_ids": []}],
    }
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["requestOrder(): Order"]}
            ]
        },
        "sequence_diagram_model": dirty,
    }
    spec = dataclasses.replace(
        SEQUENCE_DIAGRAM_SPEC,
        revise=lambda current, feedback, current_state, targets: lossy_repair,
    )

    result = check_node(spec)(state)

    assert result["sequence_diagram_model"] == dirty
    assert result["sequence_diagram_check"]["stopped"] == NO_IMPROVEMENT
    assert result["sequence_diagram_check"]["findings"]


def test_sequence_check_rejects_repair_that_replaces_a_finding_with_a_new_one(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "1")
    original = {
        "phase": "original",
        "Participants": [_participant("User", "actor")],
        "Messages": [_message("User", "User", "requestOrder()")],
    }
    candidate = {**original, "phase": "candidate"}

    def check(model, state):
        if model.get("phase") == "candidate":
            return [Finding("replacement.finding", "new defect", "candidate")]
        return [Finding("original.finding", "old defect", "original")]

    spec = dataclasses.replace(
        SEQUENCE_DIAGRAM_SPEC,
        check=check,
        revise=lambda current, feedback, current_state, targets: candidate,
    )

    result = check_node(spec)({"sequence_diagram_model": original})

    assert result["sequence_diagram_model"] == original
    assert result["sequence_diagram_check"]["stopped"] == NO_IMPROVEMENT
    assert "old defect" in result["sequence_diagram_check"]["findings"][0]


def test_sequence_check_tries_structure_batch_after_contract_batch_stalls(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "2")
    original = {"phase": "original", "Participants": [], "Messages": []}
    stalled = {"phase": "stalled", "Participants": [], "Messages": []}
    repaired = {"phase": "repaired", "Participants": [], "Messages": []}

    def check(model, state):
        if model.get("phase") == "repaired":
            return []
        return [
            Finding("sequence.flow-order", "late extension", "UC1:extension:1a"),
            Finding("sequence.message-labels-match-methods", "missing method", "A -> B"),
        ]

    feedback_seen = []

    def revise(current, feedback, state, targets):
        feedback_seen.append(feedback)
        return stalled if len(feedback_seen) == 1 else repaired

    spec = dataclasses.replace(SEQUENCE_DIAGRAM_SPEC, check=check, revise=revise)
    result = check_node(spec)({"sequence_diagram_model": original})

    assert len(feedback_seen) == 2
    assert "sequence.message-labels-match-methods" in feedback_seen[0]
    assert "sequence.flow-order" not in feedback_seen[0]
    assert "sequence.flow-order" in feedback_seen[1]
    assert result["sequence_diagram_model"] == repaired
    assert result["sequence_diagram_check"]["stopped"] == CLEAN


def test_sequence_check_retries_only_batch_with_remaining_budget(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "2")
    original = {"phase": "original", "Participants": [], "Messages": []}
    stalled = {"phase": "stalled", "Participants": [], "Messages": []}
    repaired = {"phase": "repaired", "Participants": [], "Messages": []}

    def check(model, state):
        if model.get("phase") == "repaired":
            return []
        return [Finding("sequence.flow-order", "late extension", "UC1:extension:1a")]

    attempts = []

    def revise(current, feedback, state, targets):
        attempts.append(feedback)
        return stalled if len(attempts) == 1 else repaired

    spec = dataclasses.replace(SEQUENCE_DIAGRAM_SPEC, check=check, revise=revise)
    result = check_node(spec)({"sequence_diagram_model": original})

    assert len(attempts) == 2
    assert all("sequence.flow-order" in feedback for feedback in attempts)
    assert result["sequence_diagram_model"] == repaired
    assert result["sequence_diagram_check"]["stopped"] == CLEAN


def test_sequence_collection_repairs_each_use_case_with_its_own_budget(monkeypatch):
    """최초 생성 컬렉션은 한 UC의 실패가 다른 UC의 수리 기회를 빼앗지 않는다."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "design_max_repair_iters", 2)
    original = {
        "class_diagram_hash": "same",
        "Diagrams": [
            {
                "use_case_id": "UC1",
                "phase": "dirty",
                "Participants": [],
                "Messages": [],
            },
            {
                "use_case_id": "UC2",
                "phase": "dirty",
                "Participants": [],
                "Messages": [],
            },
        ],
    }

    def check(model, state):
        diagrams = model.get("Diagrams")
        if isinstance(diagrams, list):
            return [finding for diagram in diagrams for finding in check(diagram, state)]
        if model.get("phase") == "dirty":
            use_case_id = str(model.get("use_case_id"))
            return [
                Finding(
                    "sequence.flow-order",
                    "flow is still out of order",
                    f"{use_case_id}:main:1",
                )
            ]
        return []

    attempts = {"UC1": 0, "UC2": 0}
    targets_seen: list[set[str]] = []

    def revise(current, feedback, state, targets):
        targets_seen.append(set(targets))
        target = next(iter(targets))
        attempts[target] += 1
        revised = copy.deepcopy(current)
        for diagram in revised["Diagrams"]:
            if diagram["use_case_id"] == target and attempts[target] >= 2:
                diagram["phase"] = "clean"
            elif diagram["use_case_id"] != target:
                # LLM이 비대상 UC를 건드려도 merge_model이 버려야 한다.
                diagram["phase"] = "corrupted-by-llm"
        return revised

    spec = dataclasses.replace(SEQUENCE_DIAGRAM_SPEC, check=check, revise=revise)
    result = check_node(spec)({"sequence_diagram_model": original})

    assert result["sequence_diagram_check"] == {
        "findings": [],
        "repair_iters": 4,
        "stopped": CLEAN,
    }
    assert attempts == {"UC1": 2, "UC2": 2}
    assert all(len(targets) == 1 for targets in targets_seen)
    assert [
        diagram["phase"]
        for diagram in result["sequence_diagram_model"]["Diagrams"]
    ] == ["clean", "clean"]


def test_sequence_check_keeps_progress_when_a_later_contract_finding_is_revealed(
    monkeypatch,
):
    """메서드 오류 수정 후 반환 오류가 보이는 정상적인 단계 진행을 롤백하지 않는다."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "design_max_repair_iters", 2)
    original = {"phase": "method", "Participants": [], "Messages": []}

    def check(model, state):
        if model.get("phase") == "method":
            return [
                Finding(
                    "sequence.message-labels-match-methods",
                    "receiver method is missing",
                    "A -> B",
                )
            ]
        if model.get("phase") == "return":
            return [
                Finding(
                    "sequence.nonvoid-call-requires-return",
                    "return is missing",
                    "A -> B",
                )
            ]
        return []

    def revise(current, feedback, state, targets):
        return {
            **current,
            "phase": "return" if current["phase"] == "method" else "clean",
        }

    spec = dataclasses.replace(SEQUENCE_DIAGRAM_SPEC, check=check, revise=revise)
    result = check_node(spec)({"sequence_diagram_model": original})

    assert result["sequence_diagram_model"]["phase"] == "clean"
    assert result["sequence_diagram_check"] == {
        "findings": [],
        "repair_iters": 2,
        "stopped": CLEAN,
    }


def test_sequence_check_allows_removing_hallucinated_trace_references(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "1")
    participants = [
        _participant("User", "actor"),
        _participant("OrderBoundary", "boundary", "OrderBoundary"),
    ]
    async_call = _message(
        "User",
        "OrderBoundary",
        "requestOrder()",
        type="async",
        use_case_ids=["UC404"],
        step_ids=["UC404:main:1"],
    )
    returned = _message(
        "OrderBoundary",
        "User",
        "Order",
        type="return",
        use_case_ids=["UC404"],
        step_ids=["UC404:main:1"],
    )
    repaired_call = {
        **async_call,
        "use_case_ids": ["UC1"],
        "step_ids": ["UC1:main:1"],
    }
    dirty = {"Participants": participants, "Messages": [async_call, returned]}
    repaired = {"Participants": participants, "Messages": [repaired_call]}
    state = {
        "class_diagram_puml": "class OrderBoundary <<Boundary>>",
        "usecase_spec": {
            "use_cases": [{"id": "UC1"}],
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{"step_number": 1, "description": "request"}],
            }],
        },
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["requestOrder(): Order"]}
            ]
        },
        "sequence_diagram_model": dirty,
    }
    spec = dataclasses.replace(
        SEQUENCE_DIAGRAM_SPEC,
        revise=lambda current, feedback, current_state, targets: repaired,
    )

    result = check_node(spec)(state)

    assert result["sequence_diagram_model"] == repaired
    assert result["sequence_diagram_check"]["stopped"] == CLEAN


def test_sequence_check_does_not_ask_llm_to_invent_an_unresolved_requirement(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "2")
    state = {
        "usecase_spec": {
            "use_cases": [{"id": "UC1"}],
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [],
                "extensions": [{
                    "label": "4a",
                    "branch_step": 4,
                    "handling_steps": [{
                        "sub_step": "4a1",
                        "sentence": "What do we do here?",
                    }],
                }],
            }],
        },
        "sequence_diagram_model": {
            "use_case_id": "UC1",
            "Participants": [],
            "Messages": [],
        },
    }
    revisions: list[str] = []
    spec = dataclasses.replace(
        SEQUENCE_DIAGRAM_SPEC,
        revise=lambda current, feedback, current_state, targets: revisions.append(feedback),
    )

    result = check_node(spec)(state)

    assert revisions == []
    assert result["sequence_diagram_check"]["repair_iters"] == 0
    assert result["sequence_diagram_check"]["stopped"] == NEEDS_INPUT
    assert "sequence.unresolved-usecase-step" in result["sequence_diagram_check"]["findings"][0]


def test_receiver_must_already_own_the_called_method():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["submitOrder()"]},
                {"className": "OrderControl", "methods": ["createOrder()"]},
            ]
        }
    }
    model = {
        "Participants": [
            _participant("OrderBoundary", "boundary", "OrderBoundary"),
            _participant("OrderControl", "control", "OrderControl"),
        ],
        "Messages": [_message("OrderBoundary", "OrderControl", "inventedMethod()")],
    }
    findings = sequence_message_methods(model, state)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.message-labels-match-methods"


def test_flow_coverage_checks_each_main_and_extension_step():
    state = {
        "usecase_spec": {
            "use_case_specs": [
                {
                    "use_case_id": "UC1",
                    "main_scenario": [
                        {"step_number": 1, "sentence": "submit"},
                        {"step_number": 2, "sentence": "save"},
                    ],
                    "extensions": [
                        {
                            "label": "2a",
                            "handling_steps": [{"sub_step": "2a1", "sentence": "reject"}],
                        }
                    ],
                }
            ]
        }
    }
    model = {"Messages": [_message("A", "B", "submitOrder()") ]}
    findings = sequence_usecase_coverage(model, state)
    missing = {finding.location for finding in findings}
    assert missing == {"UC1:main:2", "UC1:extension:2a:2a1"}


def test_renderer_preserves_fragments_but_excludes_lifecycle_rectangles():
    outer_main = {"id": "payment", "type": "alt", "branch": "main", "condition": "approved"}
    outer_else = {"id": "payment", "type": "alt", "branch": "else", "condition": "declined"}
    inner = {"id": "items", "type": "loop", "branch": "main", "condition": "for each item"}
    model = {
        "Participants": [
            _participant("OrderBoundary", "boundary", "OrderBoundary"),
            _participant("OrderControl", "control", "OrderControl"),
        ],
        "Messages": [
            _message("OrderBoundary", "OrderControl", "createOrder()", fragments=[outer_main]),
            _message("OrderControl", "OrderControl", "reserveItem()", type="self", fragments=[outer_main, inner]),
            _message("OrderControl", "OrderControl", "", type="activate", fragments=[outer_main]),
            _message("OrderControl", "OrderControl", "", type="deactivate", fragments=[outer_main]),
            _message("OrderControl", "OrderBoundary", "showFailure()", fragments=[outer_else]),
        ],
    }
    rendered = generate_sequence_from_model(model)
    assert "autonumber" not in rendered
    assert "alt approved" in rendered
    assert "loop for each item" in rendered
    assert "else declined" in rendered
    assert rendered.count("alt ") == 1
    assert "activate OrderControl" not in rendered
    assert "deactivate OrderControl" not in rendered
    assert sequence_no_lifecycle_events(model, {})


def test_renderer_uses_fixed_bce_lifeline_order():
    model = {
        "Participants": [
            _participant("EnrollmentStore", "database", "EnrollmentStore"),
            _participant("Enrollment", "entity", "Enrollment"),
            _participant("EnrollmentController", "control", "EnrollmentController"),
            _participant("EnrollmentApi", "boundary", "EnrollmentApi"),
            _participant("Student", "actor"),
        ],
        "Messages": [
            _message("Student", "EnrollmentApi", "enrollInCourse()"),
        ],
    }

    rendered = generate_sequence_from_model(model)

    assert rendered.index('actor "Student" as Student') < rendered.index(
        'boundary "EnrollmentApi" as EnrollmentApi'
    ) < rendered.index('control "EnrollmentController" as EnrollmentController')
    assert rendered.index('control "EnrollmentController" as EnrollmentController') < rendered.index(
        'entity "Enrollment" as Enrollment'
    ) < rendered.index('database "EnrollmentStore" as EnrollmentStore')


def test_renderer_emits_an_independent_plantuml_document_per_use_case():
    participants = [
        _participant("User", "actor"),
        _participant("OrderBoundary", "boundary", "OrderBoundary"),
    ]
    model = {
        "Diagrams": [
            {
                "use_case_id": "UC1",
                "use_case_name": "Create order",
                "Participants": participants,
                "Messages": [_message("User", "OrderBoundary", "createOrder()")],
            },
            {
                "use_case_id": "UC2",
                "use_case_name": "Cancel order",
                "Participants": participants,
                "Messages": [_message("User", "OrderBoundary", "cancelOrder()")],
            },
        ]
    }

    rendered = generate_sequence_from_model(model)

    assert rendered.count("@startuml") == 2
    assert "@startuml UC1" in rendered
    assert "@startuml UC2" in rendered
    assert "title UC1 - Create order" in rendered
    assert "title UC2 - Cancel order" in rendered
    assert "createOrder()" in rendered
    assert "cancelOrder()" in rendered


def test_sequence_spec_hands_the_typed_class_model_to_its_extractor(monkeypatch):
    received = {}

    def capture(usecase_spec, class_diagram_puml, *, class_model=None):
        received.update(
            usecase_spec=usecase_spec,
            class_diagram_puml=class_diagram_puml,
            class_model=class_model,
        )
        return {}

    monkeypatch.setattr(sequence_subgraphs, "extract_sequence_model", capture)
    class_model = {"Classes": [{"className": "OrderBoundary"}], "Relationships": []}

    assert SEQUENCE_DIAGRAM_SPEC.extract({
        "usecase_spec": {"use_cases": []},
        "class_diagram_puml": "@startuml\n@enduml",
        "extracted_bce_classes": class_model,
    }) == {}
    assert received["class_model"] is class_model
    assert received["class_diagram_puml"] == "@startuml\n@enduml"


def test_typed_class_model_bypasses_the_plantuml_parser(monkeypatch):
    specification = {
        "use_cases": [{"id": "UC1", "name": "Drop course", "primary_actor": "Student"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Drop course",
            "primary_actor": "Student",
            "trigger": "Student requests to drop a course",
            "main_scenario": [{
                "step_number": 1,
                "sentence": "System receives the drop request for the course",
            }],
            "extensions": [],
        }],
    }
    class_model = {
        "Classes": [{
            "className": "EnrollmentBoundary",
            "stereotype": "Boundary",
            "operations": [{
                "operationId": "EnrollmentBoundary::dropCourse(courseId:String)",
                "name": "dropCourse",
                "parameters": [{"name": "courseId", "type": "String"}],
                "returnType": "void",
            }],
        }],
        "Relationships": [],
    }

    def parser_must_not_run(_puml):
        raise AssertionError("typed class-model handoff must not parse PlantUML")

    monkeypatch.setattr(
        "app.design.services.sequence_diagram.extractor._parse_class_catalog",
        parser_must_not_run,
    )
    generated = extract_sequence_diagrams(
        specification,
        "class WrongBoundary <<Boundary>> { + wrong(): void }",
        class_model=class_model,
    )

    message = generated["Diagrams"][0]["Messages"][0]
    assert message["target"] == "EnrollmentBoundary"
    assert message["label"] == "dropCourse(courseId:String)"


def test_typed_multi_parameter_contract_restores_arguments_and_return():
    class_model = {
        "Classes": [{
            "className": "OrderBoundary",
            "stereotype": "Boundary",
            "operations": [{
                "operationId": "OrderBoundary::submit(orderId:String,actorId:String)",
                "name": "submit",
                "parameters": [
                    {"name": "orderId", "type": "String"},
                    {"name": "actorId", "type": "String"},
                ],
                "returnType": "Receipt",
            }],
        }],
        "Relationships": [],
    }
    model = {
        "Participants": [
            _participant("Buyer", "actor"),
            _participant("OrderBoundary", "boundary", "OrderBoundary"),
        ],
        "Messages": [
            _message(
                "Buyer", "OrderBoundary", "submit(orderId:String, actorId:String)",
                call_id="entry", reply_to="", arguments=[],
            ),
        ],
    }

    normalized = normalize_sequence_contracts(model, "", class_model)

    call = next(item for item in normalized["Messages"] if item["type"] == "sync")
    assert call["label"] == "submit(orderId:String,actorId:String)"
    assert [item["parameter"] for item in call["arguments"]] == ["orderId", "actorId"]
    reply = next(item for item in normalized["Messages"] if item["type"] == "return")
    assert reply["reply_to"] == call["call_id"]
    assert reply["label"] == "Receipt"


def test_parameter_origin_propagates_across_the_grounded_call_chain():
    class_model = {
        "Classes": [
            {
                "className": "OrderBoundary", "stereotype": "Boundary",
                "operations": [{
                    "name": "submit", "parameters": [{"name": "orderId", "type": "String"}],
                    "returnType": "void",
                }],
            },
            {
                "className": "OrderControl", "stereotype": "Control",
                "operations": [{
                    "name": "handle", "parameters": [{"name": "orderId", "type": "String"}],
                    "returnType": "void",
                }],
            },
        ],
        "Relationships": [{
            "source": "OrderBoundary", "target": "OrderControl", "type": "Dependency",
        }],
    }
    model = {
        "Participants": [
            _participant("Buyer", "actor"),
            _participant("OrderBoundary", "boundary", "OrderBoundary"),
            _participant("OrderControl", "control", "OrderControl"),
        ],
        "Messages": [
            _message(
                "Buyer", "OrderBoundary", "submit(orderId:String)",
                call_id="entry", reply_to="", arguments=[],
                step_ids=["UC1:main:1"],
            ),
            _message(
                "OrderBoundary", "OrderControl", "handle(orderId:String)",
                call_id="handle", reply_to="", arguments=[],
                step_ids=["UC1:main:2"],
            ),
        ],
    }

    normalized = normalize_sequence_contracts(model, "", class_model)
    calls = [item for item in normalized["Messages"] if item["type"] == "sync"]

    assert calls[0]["arguments"][0]["source_ref"] == "UC1:main:1"
    assert calls[1]["arguments"][0]["source_ref"] == "UC1:main:1"
