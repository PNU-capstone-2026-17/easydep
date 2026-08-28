from app.requirements.modeling.refinement_prompts import (
    extract_examples_from_xlsx,
    refine_requirements_prompt,
)


def test_no_corpus_mode_does_not_read_optional_spreadsheet(tmp_path):
    missing = tmp_path / "missing.xlsx"

    assert extract_examples_from_xlsx(str(missing), method="none") == ""


def test_prompt_states_when_reference_examples_are_not_used(tmp_path):
    prompt = refine_requirements_prompt(
        "Deploy a containerized service.",
        dataset_path=str(tmp_path / "missing.xlsx"),
        method="none",
    )

    assert "[Reference Examples]\nNone." in prompt
