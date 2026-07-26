"""판정 데이터셋의 규율 — 라벨이 의미를 갖기 위한 조건들.

`tests/test_evaluation.py`가 채점 도구를 지키고, 여기서는 **사람 라벨을 받는 파일**을 지킨다.
셋 다 깨지면 조용히 무의미해지는 성질이다:
  - **눈가림**: 모델 판정이 파일에 없어야 한다(있으면 라벨이 그 답의 함수가 된다).
  - **자기 완결**: 아티팩트 없이 채점돼야 한다(`artifacts/`는 커밋되지 않는다).
  - **같은 것을 본다**: 사람이 읽는 문장과 검증자가 받는 payload가 같은 명세여야 한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.requirements.evaluation import dataset
from app.requirements.knowledge import rules

#: 저장소에 커밋해 두는 라벨 파일. 사람이 만든 자산이라 재현하려면 저장소에 있어야 한다.
LABEL_FILE = (
    Path(__file__).parent.parent
    / "app/requirements/evaluation/data/labels-black-box.json"
)


@pytest.fixture(scope="module")
def labelled():
    if not LABEL_FILE.exists():
        pytest.skip(f"라벨 파일이 없다: {LABEL_FILE}")
    return json.loads(LABEL_FILE.read_text(encoding="utf-8"))


def test_the_rule_it_labels_exists(labelled):
    rule = rules.rule(labelled["rule_id"])
    assert labelled["rule_statement"] == rule.statement
    assert labelled["citation"] == rule.citation


def test_the_file_never_reveals_what_the_model_said(labelled):
    """모델 판정이 새면 라벨이 그 답에 끌린다(anchoring). 그러면 라벨의 값이 사라진다."""
    blob = json.dumps(labelled, ensure_ascii=False).lower()
    for leak in ("verdict", "flagged", "violated_by_model", "detected", "prediction"):
        # `label` 값으로서의 "violated"는 사람이 적는 것이라 예외다.
        assert leak not in blob, f"모델 판정이 새고 있다: {leak!r}"


def test_every_item_can_be_scored_without_the_artifacts(labelled):
    """`artifacts/`는 커밋되지 않는다 — payload가 파일 안에 있어야 채점이 재현된다."""
    for item in labelled["items"]:
        payload = item["payload"]
        assert payload["main_scenario"], item["id"]
        assert "requirements_it_must_cover" in payload


def test_the_reader_and_the_validator_see_the_same_spec(labelled):
    """사람이 읽는 문장과 검증자가 받는 payload가 어긋나면 라벨이 다른 것을 판정한 것이 된다."""
    for item in labelled["items"]:
        payload = item["payload"]
        rendered = " ".join(item["sentences"])
        assert payload["trigger"] in rendered, item["id"]
        for step in payload["main_scenario"]:
            assert step["sentence"] in rendered, item["id"]


def test_ids_are_stable_and_unique(labelled):
    ids = [i["id"] for i in labelled["items"]]
    assert len(ids) == len(set(ids))
    for item in labelled["items"]:
        assert item["id"] == dataset._item_id(
            item["domain"], item["use_case_id"], item["sentences"]
        )


def test_the_sample_spans_domains(labelled):
    """한 도메인에서 나온 수로 규칙을 바꾸지 않는다 — 그게 §9의 교훈이다."""
    domains = {i["domain"] for i in labelled["items"]}
    assert len(domains) >= 5, domains


def test_labels_are_empty_or_one_of_two_words(labelled):
    for item in labelled["items"]:
        assert item["label"] in ("", *dataset.LABELS), (item["id"], item["label"])


# ---------------------------------------------------------------------------
# 채점 — LLM 없이 산수만 확인한다
# ---------------------------------------------------------------------------
def _score_with(monkeypatch, labels_and_verdicts):
    """(라벨, 모델판정) 목록으로 채점한다. 프로브는 목킹한다."""
    items = [
        {"id": f"i{n}", "label": label, "payload": {"main_scenario": []}}
        for n, (label, _v) in enumerate(labels_and_verdicts)
    ]
    verdicts = {f"i{n}": v for n, (_l, v) in enumerate(labels_and_verdicts)}

    from app.requirements.evaluation import semantic

    monkeypatch.setattr(
        semantic, "probe_rule",
        lambda rule_id, payloads, repeats: {
            "always": 1 if verdicts[payloads[0][0]] else 0, "sometimes": 0,
        },
    )
    return dataset.score(
        {"rule_id": "spec.black-box-no-internal-components", "items": items}, repeats=3
    )


def test_precision_and_recall_are_computed_from_the_labels(monkeypatch):
    report = _score_with(monkeypatch, [
        (dataset.VIOLATED, True),    # tp
        (dataset.VIOLATED, True),    # tp
        (dataset.VIOLATED, False),   # fn
        (dataset.CLEAN, True),       # fp
        (dataset.CLEAN, False),      # tn
    ])
    assert (report["true_positive"], report["false_positive"]) == (2, 1)
    assert (report["false_negative"], report["true_negative"]) == (1, 1)
    assert report["precision"] == round(2 / 3, 3)
    assert report["recall"] == round(2 / 3, 3)
    assert report["labelled_violations"] == 3


def test_unlabelled_items_are_left_out_not_guessed(monkeypatch):
    """사람이 보류한 것을 0이나 1로 채우면 그 수는 사람의 판단이 아니다."""
    report = _score_with(monkeypatch, [(dataset.VIOLATED, True), ("", True)])
    assert report["labelled"] == 1
    assert report["skipped_unlabelled"] == 1
