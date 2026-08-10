import pytest

from evaluation.research_protocol.commands.evaluate_ambiguity import evaluate
from evaluation.research_protocol.core.selective_metrics import score_selective_decisions


def test_selective_metrics_separate_safe_questions_from_unsafe_acceptance():
    result = score_selective_decisions([
        {"expectedDecision": "accept", "systemDecision": "accepted"},
        {"expectedDecision": "accept", "systemDecision": "needsQuestion"},
        {"expectedDecision": "question", "systemDecision": "abstained"},
        {"expectedDecision": "question", "systemDecision": "accepted"},
    ])

    assert result["confusionMatrix"] == {
        "correctAccept": 1,
        "correctAbstention": 1,
        "falseAbstention": 1,
        "unsafeAccept": 1,
    }
    assert result["coverage"] == 0.5
    assert result["selectiveRisk"] == 0.5
    assert result["abstentionRecall"] == 0.5


def test_selective_metrics_reject_unreviewed_labels():
    with pytest.raises(ValueError, match="expectedDecision"):
        score_selective_decisions([{
            "expectedDecision": None, "systemDecision": "needsQuestion"
        }])


def test_ambiguity_policy_separates_questions_from_hard_abstentions():
    result = evaluate()

    assert result["allPassed"] is True
    assert result["policy"]["autoAcceptEnabled"] is False
    assert result["policy"]["acceptThreshold"] is None
    assert result["metrics"]["selectiveRisk"] == 0.0
    assert result["metrics"]["questionRecall"] == 1.0
    assert result["metrics"]["hardAbstentionRecall"] == 1.0
    assert result["metrics"]["dispositionAccuracy"] == 1.0
