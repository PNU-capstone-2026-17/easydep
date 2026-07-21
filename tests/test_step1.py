"""STEP 1 노드 단위 테스트 (BERT 목킹)."""
from app.requirements.agent.steps import step1_requirements as s1


def test_classify_defaults_to_fr_when_bert_unavailable(monkeypatch):
    # BERT 비활성 → 전부 FR로 강등, id는 유형+순번(FR1, FR2), 필드는 id/text/type만.
    monkeypatch.setattr(s1, "bert_available", lambda: False)

    out = s1.classify({"refined_requirements": ["Log in", "Be fast"]})
    items = out["classified"]

    assert [it["id"] for it in items] == ["FR1", "FR2"]
    assert all(it["type"] == "FR" for it in items)
    assert set(items[0].keys()) == {"id", "text", "type"}  # 축소된 필드만


def test_classify_uses_bert_labels_and_numbers_per_type(monkeypatch):
    # BERT 단독 분류: 라벨에 따라 유형별로 번호를 매긴다(FR1, NFR1, FR2 ...).
    monkeypatch.setattr(s1, "bert_available", lambda: True)
    monkeypatch.setattr(
        s1, "classify_bert",
        lambda text: ("NFR", 0.9) if "fast" in text else ("FR", 0.95),
    )

    items = s1.classify(
        {"refined_requirements": ["log in", "be fast", "place order", "be reliable and fast"]}
    )["classified"]

    assert [it["id"] for it in items] == ["FR1", "NFR1", "FR2", "NFR2"]
    assert [it["type"] for it in items] == ["FR", "NFR", "FR", "NFR"]


def test_intake_creates_message():
    out = s1.intake({"raw_requirements": ["a", "b"]})
    assert out["phase"] == "intake"
    assert "a" in out["messages"][0].content and "b" in out["messages"][0].content
