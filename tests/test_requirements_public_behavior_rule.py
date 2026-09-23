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
            "Do not invent authentication",
            "preconditions",
        )
    )


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
