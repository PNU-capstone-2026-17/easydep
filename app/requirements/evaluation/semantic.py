"""의미 판정 규칙의 눈금 — 결정론이 아니므로 **여러 번 돌려 검출률로** 본다.

## CI에 못 넣는다 ≠ 못 잰다

LLM 판정은 같은 입력에 같은 답을 보장하지 않는다. 그래서 이 측정은 CI 게이트가 될 수 없다
(한 번 돌려 실패했다고 코드가 잘못됐다고 말할 수 없다). 그렇다고 안 재면 의미 규칙에 대한
모든 "결함 0건"은 근거 없는 0이 된다.

그래서 케이스마다 N회 돌려 **검출률**을 본다. 0/N인 규칙은 눈금이 죽은 것이고, 그 규칙에
대한 판정은 신뢰할 수 없다. 대조군(결함 없는 산출물)에서 나오는 지적은 오탐이다.

## 이 수를 어디에 쓰나

검증 프롬프트를 고칠 때(C5) 전후를 비교하는 근거다. `evaluation/scorecard.py`가 생성 쪽
변경을 비교하고, 이 모듈이 검증 쪽 변경을 비교한다.

**표본이 작다는 것을 잊지 말 것.** N=3의 2/3과 3/3 차이는 잡음일 수 있다. 프롬프트 변경의
효과를 주장하려면 N을 올리고(비용은 호출 1~2초 × 케이스 × N) 같은 N으로 전후를 비교해야 한다.

    RUN_LIVE_TESTS=1 python -m app.requirements.evaluation semantic --repeats 3
"""
from __future__ import annotations

from collections import Counter

from app.requirements.agent import validator
from app.requirements.config import settings
from app.requirements.evaluation import seeded
from app.requirements.evaluation.scorecard import rule_of

#: 이 측정에서 지적 문구에 붙이는 머리표(파이프라인 실행과 섞이지 않게).
_PREFIX = "eval"
_SOURCE = "evaluation.semantic"


class ValidatorDisabled(RuntimeError):
    """`enable_semantic_validator=False`인데 의미 눈금을 재려 했다.

    그대로 재면 모든 규칙이 0/N으로 나오고, 그건 "눈금이 죽었다"와 구별되지 않는다.
    """


def _review_once(stage: str, artifact: dict) -> tuple[set[str], str, tuple[str, ...]]:
    review = validator.review(stage, artifact, prefix=_PREFIX, source=_SOURCE)
    flagged = {rule_of(finding) for finding in review.findings}
    return flagged, review.status, review.unexamined


def measure(repeats: int = 3, stage: str | None = None) -> dict:
    """심어 둔 의미 결함을 N회씩 판정해 검출률을 낸다(실제 LLM 호출).

    `stage`를 주면 그 단계만 잰다. 대조군도 그 단계만 돌린다.
    """
    if not settings.enable_semantic_validator:
        raise ValidatorDisabled(
            "enable_semantic_validator=False 다. 이대로 재면 전부 0/N으로 나오고, "
            "그건 눈금이 죽은 것과 구별되지 않는다."
        )

    cases = [c for c in seeded.SEEDED_SEMANTIC if stage is None or c.stage == stage]
    results = []
    for case in cases:
        detected = 0
        extras: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        unexamined: Counter[str] = Counter()
        for _ in range(repeats):
            flagged, status, skipped = _review_once(case.stage, case.artifact)
            statuses[status] += 1
            for rule_id in skipped:
                unexamined[rule_id] += 1
            if case.rule_id in flagged:
                detected += 1
            extras.update(flagged - {case.rule_id})
        results.append({
            "rule_id": case.rule_id,
            "stage": case.stage,
            "seeded": case.seeded,
            "detected": detected,
            "repeats": repeats,
            "rate": round(detected / repeats, 3),
            # 심은 것 말고 함께 걸린 규칙. 많으면 판정이 넘친다는 뜻이다.
            "also_flagged": dict(extras),
            "statuses": dict(statuses),
            # 검증자가 판정하지 않고 넘어간 규칙(early victory).
            "unexamined": dict(unexamined),
        })

    controls = []
    for control_stage, artifact in seeded.clean_artifacts().items():
        if stage is not None and control_stage != stage:
            continue
        dirty = 0
        flagged_all: Counter[str] = Counter()
        for _ in range(repeats):
            flagged, _status, _skipped = _review_once(control_stage, artifact)
            if flagged:
                dirty += 1
            flagged_all.update(flagged)
        controls.append({
            "stage": control_stage,
            "runs_with_findings": dirty,
            "repeats": repeats,
            "false_positive_rate": round(dirty / repeats, 3),
            "flagged": dict(flagged_all),
        })

    return {
        "repeats": repeats,
        "cases": results,
        "controls": controls,
        # 한 번도 못 잡은 규칙 — 이 규칙에 대한 모든 "0건"은 근거가 없다.
        "dead_gauges": [c["rule_id"] for c in results if c["detected"] == 0],
        "model": settings.model,
    }
