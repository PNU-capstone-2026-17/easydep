from app.requirements.modeling.refinement_prompts import (
    extract_examples_from_xlsx,
)


def test_no_corpus_mode_does_not_read_optional_spreadsheet(tmp_path):
    missing = tmp_path / "missing.xlsx"

    assert extract_examples_from_xlsx(str(missing), method="none") == ""
