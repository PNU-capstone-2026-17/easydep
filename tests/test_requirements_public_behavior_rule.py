from app.requirements.knowledge import rules
from app.requirements.modeling import specifications


def test_public_behavior_rule_is_part_of_spec_semantic_review() -> None:
    rule = next(rule for rule in rules.RULES if rule.id == "spec.public-behavior-completeness")

    assert rule is not None
    assert rule.stage == rules.WRITE_SPECIFICATIONS
    assert rule.judged_by == rules.JUDGED_VALIDATOR
    assert rule.owner == "specs"
    assert all(
        term in rule.statement
        for term in (
            "actor",
            "authenticated subject",
            "delegation",
            "required information",
            "observable outcome",
            "`RequiredValue.usage`",
            "control means the system consumes the value",
            "result means the system produces the value",
            "Source is provenance, not direction",
            "cite the specific flow or outcome",
            "Do not invent authentication",
            "preconditions",
        )
    )


def test_spec_generation_distinguishes_usage_direction_from_value_source() -> None:
    prompt = specifications._spec_human(
        {
            "id": "UC1",
            "name": "Review a record",
            "goal": "Review a record using the current actor context.",
            "primary_actor_ref": "ACT1",
            "requirement_ids": ["R1"],
            "nfr_ids": [],
        },
        {"R1": {"id": "R1", "text": "A user reviews an assigned record."}},
        [{"actor_ref": "ACT1", "name": "User", "description": "An authenticated user."}],
    )

    assert "Use describes direction relative to this use case's behavior, not origin" in prompt
    assert "control means the system consumes the value" in prompt
    assert "result means the system produces the value as an observable outcome" in prompt
    assert "Source is provenance only" in prompt
    assert "does not become a result merely because it is server-provided" in prompt


def test_omitted_identify_repair_requires_a_complete_source_linked_unit() -> None:
    statement = rules.rule("spec.public-behavior-completeness").statement

    assert "OMITTED identify relation" in statement
    assert "linked identifier `RequiredValue`" in statement
    assert "`identity_obligation_index` points to it" in statement
    assert "`source` is the evidenced origin" in statement
    assert "when authenticated_actor_context is evidenced" in statement
    assert "authenticate obligation in the same proposal if it is absent" in statement
    assert "`source_authenticate_obligation_index`" in statement
    assert "orphan identify obligation alone" in statement
    assert "explicitly applicable constraint establishes authenticated identity" in statement
    assert "requester's own subject or record" in statement
    assert "public or published-data browsing alone" in statement
    assert "do not belong in an entry's requirement_ids unless themselves linked" in statement
    assert "student" not in statement.lower()
    assert "course" not in statement.lower()


def test_semantic_review_payload_contains_source_and_public_behavior_fields() -> None:
    item = {
        "use_case_id": "UC1",
        "requirement_ids": ["R1"],
        "preconditions": [],
        "trigger": "A clerk submits a request for a named customer.",
        "main_scenario": ["System records the request."],
        "extensions": [],
        "success_guarantee": ["The request is recorded for that customer."],
        "minimal_guarantee": [],
    }
    payload = specifications.spec_review_payload(
        item,
        requirements=[{"id": "R1", "text": "A clerk may submit a request for a customer."}],
        goal_context={"use_case_goal": "Submit a customer request."},
    )

    assert payload["requirements_it_must_cover"] == [
        {"id": "R1", "text": "A clerk may submit a request for a customer."}
    ]
    assert payload["use_case_goal"] == "Submit a customer request."
    assert payload["preconditions"] == []
    assert payload["trigger"] == item["trigger"]
    assert payload["main_scenario"] == item["main_scenario"]
    assert payload["success_guarantee"] == item["success_guarantee"]
