"""요구사항 capability의 자동 수락·질문·보류 결과를 집계한다."""

from __future__ import annotations

from typing import Any

QUESTION_DECISIONS = frozenset({"needsQuestion", "abstained"})


def score_selective_decisions(records: list[dict[str, Any]]) -> dict[str, Any]:
    """독립 검토 정답과 시스템 결정을 혼동행렬 및 위험도로 요약한다."""
    matrix = {
        "correctAccept": 0,
        "correctAbstention": 0,
        "falseAbstention": 0,
        "unsafeAccept": 0,
    }
    for index, record in enumerate(records):
        expected = record.get("expectedDecision")
        actual = record.get("systemDecision")
        if expected not in {"accept", "question", "abstain"}:
            raise ValueError(f"record {index}: invalid expectedDecision")
        if actual not in {"accepted", *QUESTION_DECISIONS}:
            raise ValueError(f"record {index}: invalid systemDecision")
        asks = actual in QUESTION_DECISIONS
        if expected == "accept":
            key = "falseAbstention" if asks else "correctAccept"
        else:
            key = "correctAbstention" if asks else "unsafeAccept"
        matrix[key] += 1

    total = len(records)
    accepted = matrix["correctAccept"] + matrix["unsafeAccept"]
    expected_questions = sum(record.get("expectedDecision") == "question" for record in records)
    expected_abstentions = sum(record.get("expectedDecision") == "abstain" for record in records)
    correct_questions = sum(
        record.get("expectedDecision") == "question"
        and record.get("systemDecision") == "needsQuestion"
        for record in records
    )
    correct_hard_abstentions = sum(
        record.get("expectedDecision") == "abstain"
        and record.get("systemDecision") == "abstained"
        for record in records
    )
    exact = sum(
        (record.get("expectedDecision"), record.get("systemDecision"))
        in {("accept", "accepted"), ("question", "needsQuestion"), ("abstain", "abstained")}
        for record in records
    )
    return {
        "schemaVersion": "easydep-selective-metrics/v1",
        "count": total,
        "confusionMatrix": matrix,
        "coverage": accepted / total if total else None,
        "selectiveRisk": matrix["unsafeAccept"] / accepted if accepted else None,
        "abstentionRecall": (
            matrix["correctAbstention"]
            / (matrix["correctAbstention"] + matrix["unsafeAccept"])
            if matrix["correctAbstention"] + matrix["unsafeAccept"] else None
        ),
        "questionRecall": correct_questions / expected_questions if expected_questions else None,
        "hardAbstentionRecall": (
            correct_hard_abstentions / expected_abstentions if expected_abstentions else None
        ),
        "dispositionAccuracy": exact / total if total else None,
    }
