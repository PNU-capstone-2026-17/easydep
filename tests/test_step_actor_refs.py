from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.design.services.class_diagram.scenario import (
    Step,
    UseCase as ScenarioUseCase,
    _actor_steps,
)
from app.requirements.modeling.specifications import validate_specification
from app.requirements.schemas import MainScenarioStep


def _scenario_use_case(primary_actor: str, sentences: list[str]) -> ScenarioUseCase:
    steps = tuple(
        Step(
            id=f"UC1:main:{index}",
            use_case_id="UC1",
            subject="Member" if index == 1 else "System",
            subject_ref="ACT1" if index == 1 else "system",
            sentence=sentence,
            order=index - 1,
            branch="main",
        )
        for index, sentence in enumerate(sentences, start=1)
    )
    return ScenarioUseCase(
        id="UC1",
        name="Submit request",
        primary_actor=primary_actor,
        primary_actor_ref="ACT1",
        specification={},
        steps=steps,
        precondition_refs=(),
    )


def test_actor_step_projection_is_invariant_to_display_name_rephrasing() -> None:
    original = _scenario_use_case("Member", ["Member submits a request.", "System records it."])
    rephrased = _scenario_use_case("Account holder", ["The account holder sends a request.", "System records it."])

    assert _actor_steps(original) == _actor_steps(rephrased) == {"UC1:main:1"}


def test_actor_step_projection_does_not_infer_subject_from_sentence() -> None:
    use_case = _scenario_use_case("Member", ["Member submits a request.", "System records it."])
    use_case = replace(
        use_case,
        steps=(replace(use_case.steps[0], subject_ref="system"), use_case.steps[1]),
    )

    assert _actor_steps(use_case) == set()


def test_main_step_requires_subject_ref() -> None:
    with pytest.raises(ValidationError):
        MainScenarioStep(step_number=1, sentence="Member submits a request.")


@pytest.mark.parametrize("subject_ref", ["ACT9", None])
def test_spec_validation_rejects_invalid_or_missing_subject_ref(subject_ref: str | None) -> None:
    step = {"step_number": 1, "sentence": "Member submits a request."}
    if subject_ref is not None:
        step["subject_ref"] = subject_ref
    spec = {"main_scenario": [step], "extensions": []}

    findings = validate_specification(spec, {"ACT1"})

    assert any("[step-subject-ref]" in finding for finding in findings)
