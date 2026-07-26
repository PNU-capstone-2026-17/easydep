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
효과를 주장하려면 N을 올리고 같은 N으로 전후를 비교해야 한다. 비용 계산의 근거는 실측이다 —
이 구조화 호출은 **평균 10초**대다(배치 계측의 `llm_seconds/llm_calls`). 그래서 측정도
병렬로 돈다(`_review_many`).

    RUN_LIVE_TESTS=1 python -m app.requirements.evaluation semantic --repeats 3
    python -m app.requirements.evaluation stability <run_dir> --repeats 5

## 두 계기가 재는 것이 다르다

  - `measure()` — **심어 둔 결함**을 잡는지(눈금이 살아 있나). fixture라 깨끗한 대조군이 있다.
  - `measure_stability()` — **실제 명세**에서 같은 판정이 반복되는지. 2026-07-26 측정에서
    흔들림 90%가 나왔고, 그게 C2가 값을 못 낸 이유였다. fixture로는 안 보이던 잡음이다.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from app.requirements.agent import validator
from app.requirements.common import telemetry
from app.requirements.config import settings
from app.requirements.evaluation import seeded
from app.requirements.evaluation.scorecard import rule_of
from app.requirements.knowledge import rules

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


def _review_many(jobs: list[tuple[str, dict]]) -> list[tuple[set[str], str, tuple[str, ...]]]:
    """판정을 **동시에** 여러 건 돌린다. 순서는 입력 순서를 유지한다.

    왜 병렬인가: 이 구조화 호출은 실측 **평균 10초**대다(배치 계측 `llm_seconds/llm_calls`).
    안정성 측정은 명세 × 반복 × 표 수만큼 호출하므로 순차로는 수십 분이 된다 — 측정이
    느려서 못 하는 일이 되면 눈금이 있어도 안 쓴다. 각 판정은 서로 독립이다.

    `telemetry.bind_context`로 감싸야 워커가 같은 실행에 계측을 모은다(step3와 같은 이유).
    """
    workers = max(1, min(len(jobs), settings.spec_concurrency))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(telemetry.bind_context(_review_once), stage, artifact)
            for stage, artifact in jobs
        ]
        return [f.result() for f in futures]


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


# ---------------------------------------------------------------------------
# 판정 안정성 — 같은 명세를 여러 번 물어 **흔들리는 판정**을 센다
# ---------------------------------------------------------------------------
def payloads_from_run(run_dir: str) -> list[tuple[str, dict]]:
    """실행 아티팩트에서 (UC id, 검증자 payload) 목록을 만든다.

    payload는 파이프라인과 **같은 함수**로 조립한다(`step3.spec_review_payload`) — 눈금이
    다른 것을 보여 주면 그 수치는 파이프라인에 대한 말이 아니다.
    """
    from app.requirements.agent.steps.step3_specifications import (
        requirement_view,
        spec_review_payload,
    )
    from app.requirements.runner import load_state

    state = load_state(run_dir)
    by_id = {r["id"]: r for r in (state.get("classified") or [])}
    ucs = {uc["id"]: uc for uc in (state.get("use_cases") or [])}
    out = []
    for spec in state.get("use_case_specs") or []:
        uc = ucs.get(spec.get("use_case_id"), {})
        out.append((spec.get("use_case_id", "?"),
                    spec_review_payload(spec, requirement_view(uc, by_id))))
    return out


def measure_stability(payloads: list[tuple[str, dict]], repeats: int = 3) -> dict:
    """같은 명세를 N번 판정해 **규칙별로 판정이 흔들리는 정도**를 잰다.

    왜 이 계기가 필요했나: 심어 둔 결함(`seeded.py`)은 대조군 오탐률 0%를 냈는데, 실제
    실행에서는 두 규칙(`no-scope-creep`·`remerge-re-establishes-state`)이 결함의 절반을
    낸다. fixture로는 안 보이는 잡음이라는 뜻이다.

    `always`(N/N)는 안정된 판정이고 실제 결함일 가능성이 높다. `sometimes`(0<k<N)는
    **같은 입력에 답이 갈린 것**이라 그 판정 위에 쌓은 수는 믿을 수 없다.
    """
    if not settings.enable_semantic_validator:
        raise ValidatorDisabled("enable_semantic_validator=False 다.")

    jobs = [
        (rules.WRITE_SPECIFICATIONS, payload)
        for _uc_id, payload in payloads
        for _ in range(repeats)
    ]
    results = _review_many(jobs)

    fires: dict[str, dict[str, int]] = {}
    for index in range(len(payloads)):
        counts: Counter[str] = Counter()
        for flagged, _status, _skipped in results[index * repeats:(index + 1) * repeats]:
            counts.update(flagged)
        for rule_id, hit in counts.items():
            bucket = fires.setdefault(rule_id, {"always": 0, "sometimes": 0})
            bucket["always" if hit == repeats else "sometimes"] += 1

    per_rule = {
        rule_id: {
            **bucket,
            "specs_flagged": bucket["always"] + bucket["sometimes"],
            # 흔들린 비율. 1.0에 가까우면 그 규칙의 판정은 잡음이다.
            "unstable_share": round(
                bucket["sometimes"] / (bucket["always"] + bucket["sometimes"]), 3
            ),
        }
        for rule_id, bucket in sorted(fires.items())
    }
    total_always = sum(b["always"] for b in fires.values())
    total_sometimes = sum(b["sometimes"] for b in fires.values())
    return {
        "repeats": repeats,
        "n_specs": len(payloads),
        "per_rule": per_rule,
        "unstable_share": round(
            total_sometimes / (total_always + total_sometimes), 3
        ) if (total_always + total_sometimes) else 0.0,
        "model": settings.model,
    }
