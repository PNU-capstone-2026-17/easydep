from unittest.mock import patch

from app.design.graphs.subgraphs import SEQUENCE_DIAGRAM_SPEC
import pytest

from app.design.services.sequence_diagram.reconcile import (
    ensure_sequence_class_methods,
    finalize_sequence_class_methods,
    reconcile_class_methods,
)


def test_sequence_reconcile_proposes_grounded_receiver_method_for_user_approval():
    state = {
        "app_id": "test-app-id",
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                }
            ],
            "Messages": [
                {
                    "source": "Control",
                    "target": "Control",
                    "label": "reserveOrder()",
                    "type": "self",
                }
            ],
        },
    }

    assert SEQUENCE_DIAGRAM_SPEC.reconcile is None
    assert SEQUENCE_DIAGRAM_SPEC.finalize is finalize_sequence_class_methods
    revised_bce = {
        "Classes": [
            {
                "className": "OrderControl",
                # LLM이 기존 메서드를 누락해도 병합 단계가 보존해야 한다.
                "methods": ["reserveOrder()"],
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
    assert "decide whether the use case genuinely requires it" in revise.call_args.kwargs["feedback"]
    save_stage.assert_not_called()
    assert state["extracted_bce_classes"]["Classes"][0]["methods"] == ["createOrder()"]
    assert result["sequence_diagram_model"]["MethodProposals"] == [{
        "id": "method:OrderControl:reserveOrder()",
        "class_name": "OrderControl",
        "method": "reserveOrder()",
        "reason": "현재 시퀀스 검증이 이 동작에 대응하는 클래스 메서드를 찾지 못했습니다.",
        "use_case_ids": [],
        "step_ids": [],
    }]


def test_sequence_reconcile_applies_only_approved_method_and_reassembles_its_uc():
    proposal = {
        "id": "method:OrderControl:reserveOrder()",
        "class_name": "OrderControl",
        "method": "reserveOrder(): void",
        "reason": "UC1 flow requires a distinct reservation action.",
        "use_case_ids": ["UC1"],
        "step_ids": ["UC1:main:2"],
    }
    state = {
        "app_id": "test-app-id",
        "sequence_diagram_feedback": "제안 메서드 모두 승인",
        "class_diagram_puml": "@startuml\n@enduml",
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder(): void"]}]
        },
        "sequence_diagram_model": {
            "Diagrams": [
                {"use_case_id": "UC1", "use_case_name": "Create", "Participants": [], "Messages": [], "UnresolvedSteps": []},
                {"use_case_id": "UC2", "use_case_name": "Keep", "Participants": [], "Messages": [], "UnresolvedSteps": []},
            ],
            "class_diagram_hash": "",
            "MethodProposals": [proposal],
        },
    }
    regenerated = {
        "Diagrams": [
            {"use_case_id": "UC1", "use_case_name": "Create", "Participants": [], "Messages": [], "UnresolvedSteps": []},
            state["sequence_diagram_model"]["Diagrams"][1],
        ],
        "class_diagram_hash": "new-hash",
        "MethodProposals": [],
    }
    with (
        patch(
            "app.design.services.sequence_diagram.reconcile.reassemble_sequence_diagrams",
            return_value=regenerated,
        ) as reassemble,
        patch("app.repositories.artifact_repository.save_stage") as save_stage,
    ):
        result = reconcile_class_methods(state)

    assert result["extracted_bce_classes"]["Classes"][0]["methods"] == [
        "createOrder(): void", "reserveOrder(): void"
    ]
    assert result["sequence_diagram_model"] == regenerated
    # UC2 is deliberately empty in this compact fixture, so its own coverage
    # finding joins UC1 in the fixed-template pass.  The approved method still
    # guarantees that UC1 is included in the reassembly target set.
    assert "UC1" in reassemble.call_args.args[3], reassemble.call_args
    save_stage.assert_called_once()


def test_graph_finalizer_keeps_invalid_model_for_repair_without_rendering():
    state = {
        "sequence_diagram_model": {"Participants": [], "Messages": []},
        "sequence_diagram_check": {
            "findings": ["receiver method is missing"],
            "repair_iters": 2,
            "stopped": "no_improvement",
        },
    }
    with patch(
        "app.design.services.sequence_diagram.reconcile.ensure_sequence_class_methods",
        side_effect=ValueError("invalid sequence contract"),
    ):
        result = finalize_sequence_class_methods(state)

    assert result["sequence_diagram_renderable"] is False
    assert result["sequence_diagram_check"] == state["sequence_diagram_check"]


def test_graph_finalizer_blocks_legacy_model_when_current_checker_has_findings():
    state = {
        "sequence_diagram_model": {"Participants": [], "Messages": []},
        "sequence_diagram_check": {
            "findings": ["current semantic finding"],
            "repair_iters": 0,
            "stopped": "checked_only",
        },
    }

    result = finalize_sequence_class_methods(state)

    assert result["sequence_diagram_renderable"] is False


def test_sequence_reconcile_does_not_duplicate_method_owned_by_another_class():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "SelectionScreen", "methods": ["onSelected(site: string)"]},
                {"className": "PurchaseControl", "methods": ["startPurchase()"]},
            ]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "SelectionScreen",
                    "alias": "screen",
                    "kind": "boundary",
                    "source_class": "SelectionScreen",
                },
                {
                    "name": "PurchaseControl",
                    "alias": "control",
                    "kind": "control",
                    "source_class": "PurchaseControl",
                },
            ],
            "Messages": [{
                "source": "screen",
                "target": "control",
                "label": "onSelected(site: string)",
                "type": "sync",
            }],
        },
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_not_called()
    assert result == {}


def test_sequence_reconcile_ignores_unrequested_methods_from_class_llm():
    state = {
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]
        },
        "sequence_diagram_model": {
            "Participants": [{
                "name": "OrderControl",
                "alias": "control",
                "kind": "control",
                "source_class": "OrderControl",
            }],
            "Messages": [{
                "source": "control",
                "target": "control",
                "label": "reserveOrder()",
                "type": "self",
            }],
        },
    }
    proposed = {
        "Classes": [{
            "className": "OrderControl",
            "methods": ["createOrder()", "reserveOrder()", "hallucinatedMethod()"],
        }]
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=proposed,
    ):
        result = reconcile_class_methods(state)

    assert result["sequence_diagram_model"]["MethodProposals"][0]["method"] == "reserveOrder()"
    assert state["extracted_bce_classes"]["Classes"][0]["methods"] == ["createOrder()"]


def test_sequence_reconcile_scopes_proposal_evidence_to_its_receiver_route():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "FirstBoundary", "stereotype": "Boundary", "methods": []},
                {"className": "FirstControl", "stereotype": "Control", "methods": []},
                {"className": "SecondBoundary", "stereotype": "Boundary", "methods": []},
                {"className": "SecondControl", "stereotype": "Control", "methods": []},
            ],
            "Relationships": [
                {"source": "FirstBoundary", "target": "FirstControl"},
                {"source": "SecondBoundary", "target": "SecondControl"},
            ],
        },
        "sequence_diagram_model": {
            "Diagrams": [
                {
                    "use_case_id": "UC1", "Participants": [
                        {"alias": "first", "kind": "boundary", "source_class": "FirstBoundary"},
                        {"alias": "firstControl", "kind": "control", "source_class": "FirstControl"},
                    ],
                    "Messages": [],
                    "UnresolvedSteps": [{"step_id": "UC1:main:1", "reason": "missing action"}],
                },
                {
                    "use_case_id": "UC2", "Participants": [
                        {"alias": "second", "kind": "boundary", "source_class": "SecondBoundary"},
                        {"alias": "secondControl", "kind": "control", "source_class": "SecondControl"},
                    ],
                    "Messages": [],
                    "UnresolvedSteps": [{"step_id": "UC2:main:1", "reason": "missing action"}],
                },
            ]
        },
    }
    proposed = {"Classes": [
        {"className": "FirstBoundary", "methods": ["submitFirst(): void"]},
        {"className": "FirstControl", "methods": []},
        {"className": "SecondBoundary", "methods": ["submitSecond(): void"]},
        {"className": "SecondControl", "methods": []},
    ]}
    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=proposed,
    ), patch(
        "app.design.services.sequence_diagram.reconcile.reassemble_sequence_diagrams",
        side_effect=lambda sequence, *_: sequence,
    ):
        result = reconcile_class_methods(state)

    proposals = result["sequence_diagram_model"]["MethodProposals"]
    assert next(item for item in proposals if item["class_name"] == "FirstBoundary")["use_case_ids"] == ["UC1"]
    assert next(item for item in proposals if item["class_name"] == "SecondBoundary")["use_case_ids"] == ["UC2"]


def test_sequence_finalizer_rejects_call_without_a_receiver_class():
    state = {
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "User",
                    "alias": "User",
                    "kind": "actor",
                    "source_class": "",
                }
            ],
            "Messages": [
                {
                    "source": "User",
                    "target": "User",
                    "label": "createOrder()",
                    "type": "self",
                }
            ],
        },
    }

    with pytest.raises(ValueError, match="must target a class-diagram class"):
        ensure_sequence_class_methods(state)


def test_sequence_finalizer_requires_one_diagram_per_use_case():
    state = {
        "usecase_spec": {
            "use_cases": [{"id": "UC1"}, {"id": "UC2"}],
        },
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": []}]
        },
        "sequence_diagram_model": {
            "Diagrams": [
                {
                    "use_case_id": "UC1",
                    "use_case_name": "Create order",
                    "Participants": [],
                    "Messages": [],
                }
            ]
        },
    }

    with pytest.raises(ValueError, match="exactly one diagram per use case"):
        ensure_sequence_class_methods(state)


def test_reconcile_declares_return_type_for_a_required_result():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "OrderControl",
                    "methods": ["findOrder()", "cancelOrder()"],
                }
            ]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                },
                {
                    "name": "OrderBoundary",
                    "alias": "Boundary",
                    "kind": "boundary",
                    "source_class": "OrderBoundary",
                },
            ],
            "Messages": [
                {"source": "Boundary", "target": "Control", "label": "findOrder()", "type": "sync"},
                {"source": "Control", "target": "Boundary", "label": "Order", "type": "return"},
            ],
        },
    }

    revised_bce = {
        "Classes": [{"className": "OrderControl", "methods": ["findOrder(): Order"]}]
    }
    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=revised_bce,
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_called_once()
    assert result["extracted_bce_classes"]["Classes"][0]["methods"] == [
        "findOrder(): Order",
        "cancelOrder()",
    ]


def test_reconcile_does_not_change_receiver_return_when_caller_owns_contract():
    bce = {
        "Classes": [
            {"className": "SelectionScreen", "methods": ["getSiteName(): string"]},
            {"className": "PurchaseControl", "methods": ["getSiteName()"]},
        ]
    }
    state = {
        "extracted_bce_classes": bce,
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "SelectionScreen", "alias": "screen", "kind": "boundary",
                    "source_class": "SelectionScreen",
                },
                {
                    "name": "PurchaseControl", "alias": "control", "kind": "control",
                    "source_class": "PurchaseControl",
                },
            ],
            "Messages": [
                {
                    "source": "screen", "target": "control", "label": "getSiteName()",
                    "type": "sync", "call_id": "c1",
                },
                {
                    "source": "control", "target": "screen", "label": "string",
                    "type": "return", "reply_to": "c1",
                },
            ],
        },
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_not_called()
    assert result == {}


def test_finalizer_rejects_return_label_different_from_declared_type():
    state = {
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["findOrder(): Order"]}]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                },
                {
                    "name": "OrderBoundary",
                    "alias": "Boundary",
                    "kind": "boundary",
                    "source_class": "OrderBoundary",
                },
            ],
            "Messages": [
                {"source": "Boundary", "target": "Control", "label": "findOrder()", "type": "sync"},
                {"source": "Control", "target": "Boundary", "label": "Customer", "type": "return"},
            ],
        },
    }

    with pytest.raises(ValueError, match="sequence interaction contracts remain invalid"):
        ensure_sequence_class_methods(state)


def test_finalizer_rejects_multiple_returns_for_one_call():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderControl", "methods": ["findOrder(): Order"]}
            ]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                },
                {
                    "name": "OrderBoundary",
                    "alias": "Boundary",
                    "kind": "boundary",
                    "source_class": "OrderBoundary",
                },
            ],
            "Messages": [
                {
                    "source": "Boundary",
                    "target": "Control",
                    "label": "findOrder()",
                    "type": "sync",
                },
                {
                    "source": "Control",
                    "target": "Boundary",
                    "label": "Order",
                    "type": "return",
                },
                {
                    "source": "Control",
                    "target": "Boundary",
                    "label": "Customer",
                    "type": "return",
                },
            ],
        },
    }

    with pytest.raises(ValueError, match="고립된 return"):
        ensure_sequence_class_methods(state)


def test_uncovered_flow_asks_class_llm_whether_a_method_is_missing():
    state = {
        "usecase_spec": {
            "use_case_specs": [
                {
                    "use_case_id": "UC1",
                    "main_scenario": [
                        {"step_number": 1, "sentence": "submit order"},
                        {"step_number": 2, "sentence": "reserve order"},
                    ],
                    "extensions": [],
                }
            ]
        },
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}],
            "Relationships": [],
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                }
            ],
            "Messages": [
                {
                    "source": "Control",
                    "target": "Control",
                    "label": "createOrder()",
                    "type": "self",
                    "step_ids": ["UC1:main:1"],
                }
            ],
        },
    }
    current_bce = state["extracted_bce_classes"]
    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=current_bce,
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_called_once()
    assert "uncovered use-case step" in revise.call_args.kwargs["feedback"]
    assert result == {}


def test_uncovered_flow_without_a_visible_route_does_not_revise_every_class():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{"step_number": 1, "sentence": "The user submits an order."}],
                "extensions": [],
            }]
        },
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderScreen", "methods": ["display()"]},
                {"className": "OrderControl", "methods": ["createOrder()"]},
            ]
        },
        "sequence_diagram_model": {
            "use_case_id": "UC1",
            "Participants": [],
            "Messages": [],
        },
    }
    proposed = {
        "Classes": [
            {"className": "OrderScreen", "methods": ["display()", "submitOrder()"]},
            {"className": "OrderControl", "methods": ["createOrder()"]},
        ]
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=proposed,
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_not_called()
    assert result == {}
    assert state["extracted_bce_classes"]["Classes"][0]["methods"] == ["display()"]


def test_unresolved_sequence_step_without_a_visible_route_stays_unresolved():
    state = {
        "usecase_spec": {"use_case_specs": []},
        "extracted_bce_classes": {
            "Classes": [
                {"className": "SignInBoundary", "methods": ["submitCredentials()"]},
                {"className": "SignInControl", "methods": ["authenticate(): Student"]},
            ]
        },
        "sequence_diagram_model": {
            "use_case_id": "UC1",
            "Participants": [],
            "Messages": [],
            "UnresolvedSteps": [{
                "step_id": "UC1:extension:2a:2a1",
                "sentence": "System informs the student that credentials are invalid.",
                "reason": "No grounded receiver method was selected from the class diagram.",
                "candidates": ["SignInControl.authenticate()"],
            }],
        },
    }
    proposed = {
        "Classes": [
            {
                "className": "SignInBoundary",
                "methods": ["submitCredentials()", "showSignInFailure(message : String): void"],
            },
            {"className": "SignInControl", "methods": ["authenticate(): Student"]},
        ]
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=proposed,
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_not_called()
    assert result == {}
    assert state["extracted_bce_classes"]["Classes"][0]["methods"] == ["submitCredentials()"]


def test_unresolved_step_revision_is_scoped_to_its_visible_bce_route():
    state = {
        "usecase_spec": {"use_case_specs": []},
        "extracted_bce_classes": {
            "Classes": [
                {"className": "SignInBoundary", "stereotype": "Boundary", "methods": ["submitCredentials()"]},
                {"className": "SignInControl", "stereotype": "Control", "methods": ["authenticate(): Session"]},
                {"className": "ScheduleBoundary", "stereotype": "Boundary", "methods": ["showSchedule()"]},
                {"className": "ScheduleControl", "stereotype": "Control", "methods": ["getSchedule(): Schedule"]},
            ],
            "Relationships": [
                {"source": "SignInBoundary", "target": "SignInControl"},
                {"source": "ScheduleBoundary", "target": "ScheduleControl"},
            ],
        },
        "sequence_diagram_model": {
            "use_case_id": "UC1",
            "Participants": [
                {"name": "Student", "alias": "student", "kind": "actor"},
                {"name": "SignInBoundary", "alias": "signIn", "kind": "boundary", "source_class": "SignInBoundary"},
            ],
            "Messages": [],
            "UnresolvedSteps": [{
                "step_id": "UC1:main:2",
                "sentence": "System creates an authenticated session.",
                "reason": "No grounded receiver method was selected from the class diagram.",
                "candidates": [],
            }],
        },
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=state["extracted_bce_classes"],
    ) as revise:
        reconcile_class_methods(state)

    assert revise.call_args.kwargs["targets"] == {"SignInBoundary", "SignInControl"}


def test_distinct_actor_inputs_propose_a_separate_boundary_method_for_approval():
    state = {
        "usecase_spec": {"use_case_specs": []},
        "extracted_bce_classes": {
            "Classes": [
                {"className": "CatalogBoundary", "methods": ["requestCatalog()"]},
            ]
        },
        "sequence_diagram_model": {
            "use_case_id": "UC1",
            "Participants": [
                {"name": "Student", "alias": "student", "kind": "actor"},
                {
                    "name": "CatalogBoundary",
                    "alias": "catalog",
                    "kind": "boundary",
                    "source_class": "CatalogBoundary",
                },
            ],
            "Messages": [
                {
                    "source": "student", "target": "catalog", "label": "requestCatalog()",
                    "type": "sync", "step_ids": ["UC1:main:1"],
                },
                {
                    "source": "student", "target": "catalog", "label": "requestCatalog()",
                    "type": "sync", "step_ids": ["UC1:main:2"],
                },
            ],
        },
    }
    proposed = {
        "Classes": [{
            "className": "CatalogBoundary",
            "methods": ["requestCatalog()", "submitCatalogFilters(): void"],
        }]
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=proposed,
    ) as revise:
        result = reconcile_class_methods(state)

    assert "Different actor input steps reported as sharing one Boundary call" in revise.call_args.kwargs["feedback"]
    proposal = result["sequence_diagram_model"]["MethodProposals"][0]
    assert proposal["class_name"] == "CatalogBoundary"
    assert proposal["method"] == "submitCatalogFilters(): void"


def test_boundary_output_violation_proposes_an_input_method_for_approval():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{
                    "step_number": 1,
                    "sentence": "The user buys stock.",
                }],
                "extensions": [],
            }]
        },
        "extracted_bce_classes": {
            "Classes": [{"className": "BuyScreen", "methods": ["display()"]}]
        },
        "sequence_diagram_model": {
            "use_case_id": "UC1",
            "Participants": [
                {"name": "User", "alias": "user", "kind": "actor"},
                {
                    "name": "BuyScreen", "alias": "screen", "kind": "boundary",
                    "source_class": "BuyScreen",
                },
            ],
            "Messages": [{
                "source": "user", "target": "screen", "label": "display()", "type": "sync",
                "step_ids": ["UC1:main:1"],
            }],
        },
    }
    proposed = {
        "Classes": [{"className": "BuyScreen", "methods": ["display()", "buyStock()"]}]
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=proposed,
    ) as revise:
        result = reconcile_class_methods(state)

    assert "you MUST add the minimum grounded input method" in revise.call_args.kwargs["feedback"]
    assert result["sequence_diagram_model"]["MethodProposals"][0]["method"] == "buyStock()"
    assert state["extracted_bce_classes"]["Classes"][0]["methods"] == ["display()"]


def _finalizer_contract_state(messages: list[dict]) -> dict:
    return {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["start(): View"]},
                {"className": "OrderControl", "methods": ["process(): Result", "validate(): void"]},
            ]
        },
        "sequence_diagram_model": {
            "Participants": [
                {"name": "User", "alias": "User", "kind": "actor"},
                {"name": "OrderBoundary", "alias": "Boundary", "kind": "boundary", "source_class": "OrderBoundary"},
                {"name": "OrderControl", "alias": "Control", "kind": "control", "source_class": "OrderControl"},
            ],
            "Messages": messages,
        },
    }


def test_sequence_finalizer_rejects_missing_nonvoid_return():
    state = _finalizer_contract_state([
        {"source": "User", "target": "Boundary", "label": "start()", "type": "sync"},
        {"source": "Boundary", "target": "User", "label": "View", "type": "return"},
        {"source": "Boundary", "target": "Control", "label": "process()", "type": "sync"},
    ])

    with pytest.raises(ValueError, match="return 메시지가 없음"):
        ensure_sequence_class_methods(state)


def test_sequence_finalizer_rejects_disconnected_call_source():
    state = _finalizer_contract_state([
        {"source": "User", "target": "Boundary", "label": "start()", "type": "sync"},
        {"source": "Boundary", "target": "User", "label": "View", "type": "return"},
        {"source": "Control", "target": "Control", "label": "validate()", "type": "self"},
    ])

    with pytest.raises(ValueError, match="활성화되기 전에"):
        ensure_sequence_class_methods(state)


def test_sequence_finalizer_rejects_one_sided_alt():
    state = _finalizer_contract_state([
        {"source": "User", "target": "Boundary", "label": "start()", "type": "sync"},
        {"source": "Boundary", "target": "User", "label": "View", "type": "return"},
        {
            "source": "Boundary",
            "target": "Control",
            "label": "validate()",
            "type": "sync",
            "fragments": [{"id": "choice", "type": "alt", "branch": "main", "condition": "valid"}],
        },
    ])

    with pytest.raises(ValueError, match="main과 else"):
        ensure_sequence_class_methods(state)


def test_new_sequence_contract_finalizer_runs_all_registered_detectors():
    state = _finalizer_contract_state([
        {
            "source": "User", "target": "Boundary", "label": "start()", "type": "sync",
            "call_id": "call-1", "reply_to": "", "arguments": [],
        },
        {
            "source": "Boundary", "target": "User", "label": "View", "type": "return",
            "call_id": "", "reply_to": "call-1", "arguments": [],
        },
        {
            "source": "Boundary", "target": "Control", "label": "validate()", "type": "sync",
            "call_id": "call-2", "reply_to": "", "arguments": [],
        },
        {
            "source": "Boundary", "target": "Control", "label": "validate()", "type": "sync",
            "call_id": "call-3", "reply_to": "", "arguments": [],
        },
    ])

    with pytest.raises(ValueError, match="연달아 중복"):
        ensure_sequence_class_methods(state)


def test_sequence_finalizer_rejects_stale_class_diagram_version():
    state = {
        "class_diagram_puml": "class Current",
        "extracted_bce_classes": {"Classes": []},
        "sequence_diagram_model": {
            "class_diagram_hash": "stale",
            "Diagrams": [],
        },
    }

    with pytest.raises(ValueError, match="different class diagram version"):
        ensure_sequence_class_methods(state)
