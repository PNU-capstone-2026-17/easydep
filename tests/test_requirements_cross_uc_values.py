from app.requirements.modeling.cross_uc_values import (
    CandidatePair,
    CandidateReview,
    ConsumerCandidates,
    PairReview,
    _candidate_pairs,
    reconcile_cross_use_case_values,
)


def _spec(
    uc_id: str, *, source: str, requirement_id: str, value_ref: str,
    value_name: str = "item_id",
) -> dict:
    return {
        "use_case_id": uc_id,
        "name": uc_id,
        "requirement_ids": [requirement_id],
        "main_scenario": [{
            "step_number": 2,
            "sentence": "The system records a durable item.",
            "covered_req_ids": [requirement_id],
        }],
        "extensions": [{
            "branch_step": "2",
            "label": "Retry",
            "condition": "The initial attempt is retried.",
            "handling_steps": [{
                "sub_step": "2a1",
                "sentence": "The system records the durable item.",
            }],
            "outcome": "resume",
            "resume_at_step": 3,
        }] if uc_id == "UC2" else [],
        "public_contract": {
            "schema_version": "PublicBehaviorContract/v1",
            "identity_obligations": [],
            "required_values": [{
                "value_ref": value_ref,
                "name": value_name,
                "source": source,
                "value_type": "identifier",
                "usage": "control" if source == "caller_input" else "result",
                "requirement_ids": [requirement_id],
            }],
        },
    }


def test_pairwise_review_adds_stable_result_value_with_exact_evidence():
    producer = _spec("UC2", source="caller_input", requirement_id="RR2", value_ref="val_in")
    producer["requirement_ids"].append("RR5")
    consumer = _spec("UC3", source="caller_input", requirement_id="RR3",
                     value_ref="val_use", value_name="current_registration_id")
    calls = []

    def model_call(schema, messages):
        calls.append((schema, str(messages[-1].content)))
        if schema is CandidateReview:
            return CandidateReview(consumers=[
                ConsumerCandidates(consumer_use_case_id="UC3",
                                   consumer_value_ref="val_use",
                                   candidate_producer_ids=["UC2"])
            ])
        return PairReview(
            add_output=True,
            consumer_value_ref="val_use",
            output_name="created_item_id",
            evidence_refs=["UC2:main:2"],
            requirement_ids=["RR2", "RR5"],
        )

    result = reconcile_cross_use_case_values(
        [producer, consumer],
        [{"id": "UC2", "name": "Create", "goal": "Create item"},
         {"id": "UC3", "name": "Use", "goal": "Use item"}],
        [{"id": "RR2", "text": "Create item"}, {"id": "RR3", "text": "Use item"}],
        model_call=model_call,
    )
    output = result[0]["public_contract"]["required_values"][-1]
    assert len(calls) == 2
    assert "current_registration_id" in calls[0][1]
    assert "Producer spec:" in calls[1][1] and "Consumer spec:" in calls[1][1]
    assert output == {
        "value_ref": "val_7a844cd66492e8d2ad6f",
        "name": "created_item_id",
        "source": "system_result",
        "value_type": "identifier",
        "usage": "result",
        "requirement_ids": ["RR2"],
    }
    assert len(producer["public_contract"]["required_values"]) == 1


def test_pairwise_review_discards_unlinked_requirement_and_unknown_step():
    producer = _spec("UC2", source="caller_input", requirement_id="RR2", value_ref="val_in")
    consumer = _spec("UC3", source="caller_input", requirement_id="RR3", value_ref="val_use")

    def model_call(schema, _messages):
        if schema is CandidateReview:
            return CandidateReview(consumers=[
                ConsumerCandidates(consumer_use_case_id="UC3", consumer_value_ref="val_use",
                                   candidate_producer_ids=["UC2"])
            ])
        return PairReview(
            add_output=True,
            consumer_value_ref="val_use",
            output_name="created_item_id",
            evidence_refs=["UC2:main:99"],
            requirement_ids=["RR3"],
        )

    result = reconcile_cross_use_case_values(
        [producer, consumer], [], [{"id": "RR2", "text": "Create"}],
        model_call=model_call,
    )
    assert len(result[0]["public_contract"]["required_values"]) == 1


def test_pairwise_review_rejects_cited_requirements_with_zero_supported_overlap():
    producer = _spec("UC2", source="caller_input", requirement_id="RR2", value_ref="val_in")
    consumer = _spec("UC3", source="caller_input", requirement_id="RR3", value_ref="val_use")

    def model_call(schema, _messages):
        if schema is CandidateReview:
            return CandidateReview(consumers=[
                ConsumerCandidates(consumer_use_case_id="UC3", consumer_value_ref="val_use",
                                   candidate_producer_ids=["UC2"])
            ])
        return PairReview(
            add_output=True,
            consumer_value_ref="val_use",
            output_name="created_item_id",
            evidence_refs=["UC2:main:2"],
            requirement_ids=["RR3"],
        )

    result = reconcile_cross_use_case_values(
        [producer, consumer], [], [], model_call=model_call,
    )

    assert len(result[0]["public_contract"]["required_values"]) == 1


def test_partial_regeneration_only_reconciles_allowed_producer():
    producer = _spec("UC2", source="caller_input", requirement_id="RR2", value_ref="val_in")
    consumer = _spec("UC3", source="caller_input", requirement_id="RR3", value_ref="val_use")
    calls = []

    def model_call(schema, _messages):
        calls.append(schema)
        return CandidateReview(consumers=[
            ConsumerCandidates(consumer_use_case_id="UC3", consumer_value_ref="val_use",
                               candidate_producer_ids=["UC2"])
        ])

    result = reconcile_cross_use_case_values(
        [producer, consumer], [], [], model_call=model_call,
        allowed_producer_ids={"UC3"},
    )
    assert calls == [CandidateReview]
    assert len(result[0]["public_contract"]["required_values"]) == 1


def test_candidate_lists_expand_to_consumer_producer_pairs():
    review = CandidateReview(consumers=[
        ConsumerCandidates(consumer_use_case_id="UC3", consumer_value_ref="val_a",
                           candidate_producer_ids=["UC2", "UC5"]),
        ConsumerCandidates(consumer_use_case_id="UC8", consumer_value_ref="val_b",
                           candidate_producer_ids=["UC7"]),
    ])

    pairs = _candidate_pairs(review)

    assert [(p.producer_use_case_id, p.consumer_use_case_id, p.consumer_value_ref)
            for p in pairs] == [
        ("UC2", "UC3", "val_a"),
        ("UC5", "UC3", "val_a"),
        ("UC7", "UC8", "val_b"),
    ]


def test_check_specs_persists_reconciled_contract(monkeypatch):
    from app.requirements.modeling import specifications

    producer = _spec("UC2", source="caller_input", requirement_id="RR2", value_ref="val_in")
    producer["semantic_status"] = "ok"
    consumer = _spec("UC3", source="caller_input", requirement_id="RR3", value_ref="val_use")
    state = {
        "use_case_specs": [producer, consumer],
        "use_cases": [{"id": "UC2", "name": "Create", "goal": "Create item"},
                      {"id": "UC3", "name": "Use", "goal": "Use item"}],
        "classified": [{"id": "RR2", "text": "Create item"},
                       {"id": "RR3", "text": "Use item"}],
    }
    def reconcile(specs, _use_cases, _requirements, *, allowed_producer_ids=None):
        assert allowed_producer_ids is None
        updated = [dict(item) for item in specs]
        updated[0] = {**updated[0], "public_contract": {
            **updated[0]["public_contract"],
            "required_values": [*updated[0]["public_contract"]["required_values"], {
                "value_ref": "val_new", "name": "created_item_id",
                "source": "system_result", "value_type": "identifier",
                "usage": "result", "requirement_ids": ["RR2"],
            }],
        }}
        return updated
    monkeypatch.setattr(specifications, "reconcile_cross_use_case_values", reconcile)
    checked = []

    def validate(spec, allowed_subject_refs=None):
        checked.append((spec["use_case_id"], spec["public_contract"]["required_values"]))
        return ["[public-contract-integrity] checked after reconciliation"]

    monkeypatch.setattr(specifications, "validate_specification", validate)
    monkeypatch.setattr(
        specifications, "find_source_grounded_semantic_ambiguity", lambda _state: None
    )

    patch = specifications.check_specs(state)

    assert patch["use_case_specs"][0]["public_contract"]["required_values"][-1]["value_ref"] == "val_new"
    assert checked == [("UC2", patch["use_case_specs"][0]["public_contract"]["required_values"])]
    assert patch["use_case_specs"][0]["issues"] == [
        "[public-contract-integrity] checked after reconciliation"
    ]
    assert patch["use_case_specs"][0]["semantic_status"] == "ok"
    assert patch["spec_report"]["n_specs"] == 2
    assert len(producer["public_contract"]["required_values"]) == 1
