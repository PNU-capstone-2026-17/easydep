"""Startup contract for the required STEP 1 BERT classifier."""

import pytest

from app.requirements import classifier


def test_enabled_classifier_fails_startup_when_warmup_fails(monkeypatch):
    monkeypatch.setattr(classifier.settings, "enable_bert_verify", True)
    monkeypatch.setattr(classifier, "warmup", lambda: False)

    with pytest.raises(RuntimeError, match="enabled but failed to load"):
        classifier.warmup_or_raise()


def test_explicit_lightweight_mode_may_skip_warmup(monkeypatch):
    monkeypatch.setattr(classifier.settings, "enable_bert_verify", False)
    monkeypatch.setattr(classifier, "warmup", lambda: False)

    assert classifier.warmup_or_raise() is False
