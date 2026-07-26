"""실행 하나를 규칙 단위로 채점한다 — 변경 전후를 비교할 수 있는 형태로.

## 왜 필요한가

`agent/compare.py`가 이미 20여 개 지표를 결정론으로 뽑는다. 그런데 C5(에이전트 갱신)가
물어야 하는 질문에는 그 지표들이 답하지 못한다. 갱신이란 프롬프트·플레이북을 고치는
일이고, 물어야 하는 것은 **"무엇이 좋아지고 무엇이 나빠졌나"**다. `spec_validation_issues:
14 → 11`은 그 답이 아니다 — scope creep 4건이 줄고 hidden branching 1건이 늘었을 수도 있다.

그게 `_spec_for`의 `len(issues)` 비교와 같은 실수다(스칼라로 트레이스를 뭉갠다). ACE·GEPA가
공통으로 말하는 것도 그것이고, 그 문장을 개선 후보 문서에 적어 놓고 정작 채점은 스칼라로
하고 있었다.

## 두 가지 수를 따로 낸다

같은 실행에서 나오는 수인데 뜻이 다르다.

  - **`static_now`** — 오늘의 검출기로 산출물을 **다시** 검증한 결과. 코드가 바뀌어도
    같은 잣대라서, 예전 실행과 오늘 실행을 나란히 놓을 수 있다.
  - **`as_recorded`** — 그 실행이 스스로 기록한 결함(의미 검증 포함). 의미 판정은 재현하려면
    LLM을 다시 불러야 하므로 다시 재지 않는다. 대신 **검증 설정이 같았을 때만 비교할 수
    있다** — `enable_semantic_validator`가 꺼진 실행과 켜진 실행의 이 수를 비교하면 안 된다.
    그래서 상태 분포(`statuses`)를 함께 싣는다. `disabled`가 섞여 있으면 그 비교는 무효다.

## 세지 못한 것을 세지 않은 것처럼 두지 않는다

지적 문구에서 규칙 id를 못 찾으면 `(untagged)`로 센다. 조용히 버리면 규칙별 합이 전체와
어긋나는데 아무 표시가 없다.
"""
from __future__ import annotations

from collections import Counter

from app.requirements.knowledge import detectors, rules

# ⚠ `agent.compare`(→ `score_run`)는 **함수 안에서** import한다. 그 모듈은 그래프·LLM
# 스택 전체를 끌고 오고, 그러면 자격증명 없이는 이 파일을 열 수도 없다. 규칙별 채점과
# 심어 둔 결함 검사(`seeded.py`)는 CI에서 API 키 없이 돌아야 하는 것들이다.

#: 규칙 꼬리표를 찾지 못한 지적. 규칙별 합과 전체 합이 어긋나는 것을 드러낸다.
UNTAGGED = "(untagged)"


def rule_of(issue: str) -> str:
    """지적 문구가 인용한 규칙 id. 못 찾으면 `UNTAGGED`.

    되읽기 자체는 지식베이스가 한다(`rules.rule_of`) — 채점과 되돌리기 라우팅
    (`agent/supervisor.py`)이 같은 되읽기를 쓰므로 두 벌이면 갈라진다. 여기서는 못 찾은
    것을 **세기 위한 이름**으로만 바꾼다(조용히 버리면 규칙별 합이 전체와 어긋난다).
    """
    return rules.rule_of(issue) or UNTAGGED


def _issues_of(state: dict) -> list[str]:
    """실행이 기록한 지적 전부(명세 + 관계 + 2단계 모델 검증)."""
    issues: list[str] = []
    for spec in state.get("use_case_specs") or []:
        issues.extend(spec.get("issues") or [])
    issues.extend((state.get("relationships") or {}).get("relationship_issues") or [])
    issues.extend((state.get("model_review") or {}).get("issues") or [])
    return issues


def _statuses(state: dict) -> dict[str, dict[str, int]]:
    """의미 검증이 실제로 돌았는지의 분포. **이게 없으면 결함 수를 읽을 수 없다.**

    결함 0건이 "깨끗하다"인지 "확인 못 했다"인지는 여기서만 갈린다.
    """
    specs = state.get("use_case_specs") or []
    return {
        "specs": dict(Counter(s.get("semantic_status", "unknown") for s in specs)),
        "repair_stopped": dict(Counter(s.get("repair_stopped", "unknown") for s in specs)),
        "relationships": {
            (state.get("relationships") or {}).get("semantic_status", "unknown"): 1
        },
        "model_review": {
            (state.get("model_review") or {}).get("semantic_status", "unknown"): 1
        },
    }


def _unexamined(state: dict) -> list[str]:
    """검증자가 판정하지 않고 넘어간 규칙(early victory의 흔적). 있으면 결함 수는 하한이다."""
    return list((state.get("model_review") or {}).get("unexamined_rules") or [])


def scorecard(state: dict) -> dict:
    """실행 상태 → 비교 가능한 채점표(LLM 호출 없음).

    `totals`는 `agent/compare.py`의 채점을 그대로 쓴다 — 같은 지표를 두 번 구현하면 갈라진다.
    그 import가 여기 있는 이유는 모듈 상단 주석 참고.
    """
    from app.requirements.agent.compare import score_run

    static_findings = _static_rule_counts(state)
    recorded = Counter(rule_of(issue) for issue in _issues_of(state))
    return {
        "totals": score_run(state, semantic=False),
        "static_now": dict(sorted(static_findings.items())),
        "as_recorded": dict(sorted(recorded.items())),
        "statuses": _statuses(state),
        "unexamined_rules": _unexamined(state),
    }


def _static_rule_counts(state: dict) -> Counter[str]:
    """오늘의 검출기로 명세를 다시 검증해 규칙별로 센다."""
    counts: Counter[str] = Counter()
    for spec in state.get("use_case_specs") or []:
        for finding in detectors.spec_findings(spec):
            counts[finding.rule_id] += 1
    return counts


def diff(before: dict, after: dict) -> dict:
    """두 채점표의 **규칙별** 증감. 좋아진 것과 나빠진 것을 섞지 않는다.

    비교가 성립하지 않는 경우를 조용히 넘기지 않는다 — 한쪽에서 의미 검증이 꺼져 있었다면
    `as_recorded` 비교는 무효이고, 그 사실을 `warnings`에 적는다.
    """
    warnings: list[str] = []
    for side, card in (("before", before), ("after", after)):
        statuses = card.get("statuses", {})
        disabled = [
            where for where, dist in statuses.items()
            if "disabled" in dist or "unknown" in dist
        ]
        if disabled:
            warnings.append(
                f"{side}: 의미 검증이 돌지 않은 곳이 있다({', '.join(sorted(disabled))}) — "
                "as_recorded 비교는 이 실행에 대해 무효다."
            )

    def _deltas(key: str) -> dict[str, int]:
        old, new = before.get(key, {}), after.get(key, {})
        keys = set(old) | set(new)
        changed = {k: new.get(k, 0) - old.get(k, 0) for k in keys}
        # 안 바뀐 규칙은 싣지 않는다. 0으로 가득한 표에서는 변화가 안 보인다.
        return dict(sorted(
            ((k, v) for k, v in changed.items() if v),
            key=lambda kv: (kv[1], kv[0]),
        ))

    return {
        "static_now": _deltas("static_now"),
        "as_recorded": _deltas("as_recorded"),
        "totals": {
            k: after["totals"][k] - before["totals"][k]
            for k in ("spec_validation_issues", "dangling_diagram_refs", "n_use_cases")
            if isinstance(before.get("totals", {}).get(k), int)
            and isinstance(after.get("totals", {}).get(k), int)
            and after["totals"][k] != before["totals"][k]
        },
        "warnings": warnings,
    }
