"""STEP 1 요구사항 확장·provenance·BERT 분류 공개 계약을 검증한다."""
import pytest

from app.requirements.modeling import refinement as s1
from app.requirements.schemas import ClarifyOnlyResult, ExpandedRequirementsResult


def test_expand_requirements_keeps_the_user_raw_input_and_maps_to_raw1(monkeypatch):
    monkeypatch.setattr(
        s1,
        "invoke_structured",
        lambda _schema, _messages: ExpandedRequirementsResult(
            requirements=[
                "Customers shall browse products.",
                "Customers shall place orders.",
            ]
        ),
    )
    state = {"raw_requirements": ["I want to build a shopping mall service."]}

    result = s1.expand_requirements(state)

    assert state["raw_requirements"] == ["I want to build a shopping mall service."]
    assert result["expanded_requirements"] == [
        "Customers shall browse products.",
        "Customers shall place orders.",
    ]
    assert result["expanded_source_refs"] == [["RAW1"], ["RAW1"]]


def test_expand_requirements_preserves_an_existing_requirement_set(monkeypatch):
    def forbidden_call(*_args, **_kwargs):
        raise AssertionError("multi-item requirement sets must not be expanded")

    monkeypatch.setattr(s1, "invoke_structured", forbidden_call)
    source = [
        "Students shall browse published courses.",
        "Registrations shall survive application restarts.",
    ]

    result = s1.expand_requirements({"raw_requirements": source})

    assert result["expanded_requirements"] == source
    assert result["expanded_source_refs"] == [["RAW1"], ["RAW2"]]


def test_clarify_maps_expanded_items_back_to_the_immutable_raw_source(monkeypatch):
    calls = 0

    def fake_structured(_schema, _messages):
        nonlocal calls
        calls += 1
        return ClarifyOnlyResult.model_validate(
            {
                "requirementDrafts": [
                    {"text": "Customers shall browse products.", "sourceRefs": ["RAW1"]},
                    {"text": "Customers shall place orders.", "sourceRefs": ["RAW1"]},
                ]
            }
        )

    monkeypatch.setattr(s1, "invoke_structured", fake_structured)
    raw = ["I want to build a shopping mall service."]
    result = s1.clarify(
        {
            "raw_requirements": raw,
            "expanded_requirements": [
                "Customers shall browse products.",
                "Customers shall place orders.",
            ],
            "expanded_source_refs": [["RAW1"], ["RAW1"]],
            "messages": [],
        }
    )

    assert raw == ["I want to build a shopping mall service."]
    assert calls == 1
    assert [item["sourceRefs"] for item in result["requirement_drafts"]] == [
        ["RAW1"],
        ["RAW1"],
    ]


def test_normalize_refinement_assigns_stable_refs_and_reports_missing_sources():
    result = ClarifyOnlyResult.model_validate(
        {
            "requirementDrafts": [
                {"text": "Second requirement.", "sourceRefs": ["RAW2"]},
                {"text": "First requirement.", "sourceRefs": ["RAW1"]},
                {"text": "Invalid requirement.", "sourceRefs": ["RAW9"]},
            ]
        }
    )

    drafts, issues, links = s1.normalize_refinement(
        result, ["first", "second", "third"]
    )

    assert [(item["ref"], item["sourceRefs"]) for item in drafts] == [
        ("RR1", ["RAW1"]),
        ("RR2", ["RAW2"]),
        ("RR3", []),
    ]
    assert any("RAW9" in issue for issue in issues)
    assert any("RAW3" in issue for issue in issues)
    assert links == []


def test_classify_rejects_unclassified_input_when_bert_unavailable(monkeypatch):
    # Raw requirements cannot receive guessed fallback labels.
    monkeypatch.setattr(s1, "bert_available", lambda: False)

    with pytest.raises(RuntimeError, match="requires the BERT classifier"):
        s1.classify({"refined_requirements": ["Log in", "Be fast"]})


def test_classify_uses_bert_labels_and_numbers_per_type(monkeypatch):
    # BERT labels are independent of stable RR identities.
    monkeypatch.setattr(s1, "bert_available", lambda: True)
    monkeypatch.setattr(
        s1, "classify_bert",
        lambda text: ("NFR", 0.9) if "fast" in text else ("FR", 0.95),
    )

    items = s1.classify(
        {"refined_requirements": ["log in", "be fast", "place order", "be reliable and fast"]}
    )["classified"]

    assert [it["id"] for it in items] == ["RR1", "RR2", "RR3", "RR4"]
    assert [it["type"] for it in items] == ["FR", "NFR", "FR", "NFR"]


def test_classify_reuses_current_labels_without_bert_inference(monkeypatch):
    def unexpected():
        raise AssertionError("BERT availability should not be checked for a current checkpoint")

    monkeypatch.setattr(s1, "bert_available", unexpected)
    monkeypatch.setattr(
        s1,
        "classify_bert",
        lambda _text: (_ for _ in ()).throw(AssertionError("BERT inference was repeated")),
    )

    result = s1.classify({
        "requirement_drafts": [
            {"ref": "RR1", "text": "Users shall submit requests.", "sourceRefs": ["RAW1"]},
            {"ref": "RR2", "text": "Responses shall be timely.", "sourceRefs": ["RAW1"]},
        ],
        "classified": [
            {"id": "RR1", "text": "Users shall submit requests.", "type": "FR"},
            {"id": "RR2", "text": "Responses shall be timely.", "type": "NFR"},
        ],
        "constraint_links": [{
            "constraint": "Responses shall be timely.",
            "qualifies": "Users shall submit requests.",
        }],
    })

    assert [item["type"] for item in result["classified"]] == ["FR", "NFR"]
    assert result["classified"][1]["qualifies"] == ["RR1"]


def test_classified_requirements_keep_draft_sources_and_constraint_links(monkeypatch):
    monkeypatch.setattr(s1, "bert_available", lambda: True)
    monkeypatch.setattr(
        s1,
        "classify_bert",
        lambda text: ("NFR", 0.95) if "within" in text else ("FR", 0.95),
    )

    items = s1.classify(
        {
            "requirement_drafts": [
                {"ref": "RR1", "text": "Users shall submit requests.", "sourceRefs": ["RAW1"]},
                {
                    "ref": "RR2",
                    "text": "Request responses shall arrive within two seconds.",
                    "sourceRefs": ["RAW1"],
                },
            ],
            "constraint_links": [
                {
                    "constraint": "Request responses shall arrive within two seconds.",
                    "qualifies": "Users shall submit requests.",
                }
            ],
        }
    )["classified"]

    assert items[0]["id"] == items[0]["draft_ref"] == "RR1"
    assert items[0]["source_refs"] == ["RAW1"]
    assert items[1]["qualifies"] == ["RR1"]


def test_constraint_link_cannot_join_requirements_from_different_raw_sources(monkeypatch):
    monkeypatch.setattr(s1, "bert_available", lambda: True)
    monkeypatch.setattr(
        s1,
        "classify_bert",
        lambda text: ("NFR", 0.95) if "survive" in text else ("FR", 0.95),
    )

    items = s1.classify({
        "requirement_drafts": [
            {"ref": "RR1", "text": "Operators shall manage records.", "sourceRefs": ["RAW1"]},
            {"ref": "RR2", "text": "Records shall survive restarts.", "sourceRefs": ["RAW2"]},
        ],
        "constraint_links": [{
            "constraint": "Records shall survive restarts.",
            "qualifies": "Operators shall manage records.",
        }],
    })["classified"]

    assert "qualifies" not in items[1]


def test_intake_creates_message():
    out = s1.intake({"raw_requirements": ["a", "b"]})
    assert out["phase"] == "intake"
    assert "a" in out["messages"][0].content and "b" in out["messages"][0].content
