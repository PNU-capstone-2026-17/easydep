"""BASELINE vs 우리 파이프라인 정량 비교.

두 그래프를 같은 요구사항으로 돌린 뒤, **우리 파이프라인이 내부에서 쓰는 그 결정론 검증기**를
양쪽 산출물에 똑같이 적용해 점수를 낸다. 핵심은 baseline이 남기는 결함(커버리지 누락·명세 정적
위반·다이어그램의 유령 참조·고아 액터)을 우리 시스템은 검증/반성으로 0에 수렴시킨다는 대비다.

주의(교란요인): 우리 파이프라인은 앞단에 clarify(요구사항 few-shot 구체화)가 있어 baseline보다
요구사항 집합이 달라질 수 있다. coverage_ratio(비율)는 각 시스템 기준으로 유효하지만 절대 FR 수는
다를 수 있어 리포트에 함께 표기한다. 품질 지표(명세 위반·유령 참조·고아 액터·누락 계약)는 요구
수와 무관하게 산출물 품질을 재므로 정당성의 핵심 근거가 된다.
"""
from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent.baseline import build_baseline_graph
from app.requirements.agent.graph import build_graph
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.steps.step2_usecases import check_coverage
from app.requirements.agent.steps.step3_specifications import _validate_spec
from app.requirements.common import telemetry
from app.requirements.config import settings
from app.requirements.schemas import CoverageJudgment

# --- D. 복합 FR 가드 ---------------------------------------------------------
# FR 텍스트에 정량 품질(NFR성) 제약이 융합돼 있으면 원자성 위반으로 플래그한다(결정론 감시).
# clarify가 성능/보안/원자성 제약을 FR 문장에 녹여 넣으면(예: "...within 1 second"),
# black-box 시나리오가 그 절반을 실현 못 해 semantic judge가 거짓 커버리지로 잡는다. 이 가드는
# 그 융합이 남아있는지를 수치로 드러내, 분해(clarify 원자화)의 효과를 인과적으로 귀속하게 한다.
_QUALITY_CONSTRAINT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"within\s+\d+\s*(?:milliseconds?|seconds?|minutes?|hours?|hrs?|mins?|secs?|ms|s)\b",
        r"at\s+least\s+\d+",
        r"under\s+(?:a\s+)?(?:concurrent\s+)?load",
        r"\bconcurrent\b[^.]*\bsessions?\b",
        r"\d+\s*%",
        r"\bencrypt(?:ed|ion)?\b",
        r"\batomically\b",
        r"\bAES-?\d+\b",
        r"\b24/7\b",
        r"\b(?:availability|uptime|throughput)\b",
    )
]


# 모델이 숫자·단위 사이에 끼우는 특수공백(narrow/no-break 등)을 정규화한다. 이걸 안 하면
# "within 1 second"의 \s 매칭이 실패해 가드가 복합 FR을 놓친다(step3 _REPLACEMENTS와 같은 이유).
_WS_NORMALIZE = re.compile("[     ⁠]")


def compound_fr_issues(classified: list) -> list[str]:
    """FR로 분류됐지만 정량 품질제약(NFR성)이 문장에 융합된 요구를 플래그한다.

    type=="FR"인 항목만 검사하므로, 제약이 별도 NFR로 분리되면(원자화 성공) 자동으로 사라진다.
    """
    issues: list[str] = []
    for r in classified:
        if r.get("type") != "FR":
            continue
        text = _WS_NORMALIZE.sub(" ", r.get("text", ""))
        hits = sorted({m.group(0).strip()
                       for pat in _QUALITY_CONSTRAINT_PATTERNS
                       if (m := pat.search(text))})
        if hits:
            issues.append(f"{r['id']}: 융합된 품질제약 {hits}")
    return issues


def score_relationships(rel: dict, actors: list, use_cases: list) -> dict:
    """관계의 구조적 무결성을 read-only로 채점(step4 참조 가드의 비파괴 재구현).

    - dangling_refs: 존재하지 않는 UC/액터를 참조하는 관계(우리 파이프라인은 드롭·표면화함)
    - orphan_actors: 어떤 association에도 안 걸린 액터
    """
    known_uc = {uc["name"] for uc in use_cases} | {d["name"] for d in rel.get("derived_use_cases", [])}
    known_actor = {a["name"] for a in actors}
    dangling: list[str] = []
    for a in rel.get("associations", []):
        if a["actor"] not in known_actor or a["use_case"] not in known_uc:
            dangling.append(f"association {a['actor']} -> {a['use_case']}")
    for r in rel.get("includes", []):
        if r["base_use_case"] not in known_uc or r["included_use_case"] not in known_uc:
            dangling.append(f"include {r['base_use_case']} / {r['included_use_case']}")
    for r in rel.get("extends", []):
        if r["base_use_case"] not in known_uc or r["extending_use_case"] not in known_uc:
            dangling.append(f"extend {r['base_use_case']} / {r['extending_use_case']}")
    for g in rel.get("generalizations", []):
        pool = known_actor if g.get("kind") == "actor" else known_uc
        if g["parent"] not in pool or g["child"] not in pool:
            dangling.append(f"generalization {g['parent']} / {g['child']}")
    associated = {a["actor"] for a in rel.get("associations", [])}
    orphan_actors = sorted(a["name"] for a in actors if a["name"] not in associated)
    return {"dangling_refs": dangling, "orphan_actors": orphan_actors}


def _judge_uc_coverage(uc: dict, specs_by_id: dict, by_id: dict) -> list[tuple]:
    """한 유스케이스가 주장한 requirement_ids가 시나리오로 실제 뒷받침되는지 LLM으로 판정."""
    claimed = [rid for rid in uc.get("requirement_ids", []) if rid in by_id]
    if not claimed:
        return []
    spec = specs_by_id.get(uc.get("id"))
    steps = "\n".join(f"{s['step_number']}. {s['sentence']}"
                      for s in (spec or {}).get("main_scenario", []))
    reqs_txt = "\n".join(f"- {rid}: {by_id[rid]['text']}" for rid in claimed)
    human = (f"[USE CASE] {uc.get('name', '?')}\nGoal: {uc.get('goal', '')}\n"
             f"Main scenario:\n{steps or '(none)'}\n\n[CLAIMED REQUIREMENTS]\n{reqs_txt}")
    try:
        res: CoverageJudgment = invoke_structured(
            CoverageJudgment,
            [SystemMessage(content=prompts.COVERAGE_JUDGE_SYSTEM), HumanMessage(content=human)],
        )
    except Exception as exc:  # noqa: BLE001 - 판정 실패는 치명적이지 않음(해당 UC 스킵)
        # 이 UC가 판정에서 빠진다. 점수를 비교할 때 표본이 줄었다는 사실을 모르면
        # 대조 결과를 잘못 읽게 된다.
        telemetry.record_degradation(
            "compare.coverage_judge", f"{type(exc).__name__}: {exc}", subject=uc.get("id")
        )
        return []
    return [(uc.get("id"), v.requirement_id, v.realized, v.reason) for v in res.verdicts]


def semantic_coverage(state: dict) -> dict:
    """의미론적 커버리지 검증(LLM). 결정론 집합검사가 못 잡는 '거짓 커버리지 주장'을 잡는다.

    각 유스케이스가 requirement_ids로 주장한 FR을, 그 유스케이스의 goal+주 시나리오가 정말
    실현하는지 판정한다. 주장했지만 realized=False면 false_coverage_claim(추적성 결함).
    """
    classified = state.get("classified") or []
    by_id = {r["id"]: r for r in classified}
    fr_ids = {r["id"] for r in classified if r.get("type") == "FR"}
    use_cases = state.get("use_cases") or []
    specs_by_id = {s["use_case_id"]: s for s in (state.get("use_case_specs") or [])}

    verdicts: list[tuple] = []
    workers = max(1, min(settings.spec_concurrency, len(use_cases) or 1))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(lambda uc: _judge_uc_coverage(uc, specs_by_id, by_id), use_cases):
            verdicts.extend(res)

    false = [(u, r, reason) for (u, r, real, reason) in verdicts if not real]
    verified_frs = {r for (u, r, real, _) in verdicts if real and r in fr_ids}
    return {
        "claimed_coverage_pairs": len(verdicts),
        "false_coverage_claims": len(false),
        "false_coverage_detail": [f"{u} claims {r}: {reason}" for (u, r, reason) in false],
        "semantic_coverage_ratio": round(len(verified_frs) / len(fr_ids), 4) if fr_ids else 1.0,
        # Phase 3(RTM): 판정 원본(uc,req,realized)을 RTM realized 컬럼용으로 노출.
        "coverage_verdicts": [
            {"use_case_id": u, "requirement_id": r, "realized": bool(real)}
            for (u, r, real, _reason) in verdicts
        ],
    }


def score_run(state: dict, semantic: bool = False) -> dict:
    """상태 dict(classified/actors/use_cases/use_case_specs/relationships)를 지표로 채점.

    semantic=True면 의미론적 커버리지 검증(LLM)을 추가로 실행해 지표를 병합한다.
    """
    actors = state.get("actors") or []
    use_cases = state.get("use_cases") or []
    specs = state.get("use_case_specs") or []
    rel = state.get("relationships") or {}

    coverage = check_coverage(state)["coverage"]
    # 명세 정적 검증을 양쪽에 fresh 적용(우리 파이프라인이 저장한 issues에 의존하지 않음 → 공정).
    issues_by_uc = {s.get("use_case_id", f"UC{i}"): _validate_spec(s) for i, s in enumerate(specs, 1)}
    steps = [len(s.get("main_scenario", [])) for s in specs]
    rel_score = score_relationships(rel, actors, use_cases)
    compound = compound_fr_issues(state.get("classified") or [])

    result = {
        "compound_fr_issues": len(compound),
        "compound_fr_detail": compound,
        "n_actors": len(actors),
        "n_use_cases": len(use_cases),
        "n_specs": len(specs),
        "fr_total": coverage["fr_total"],
        "coverage_ratio": coverage["coverage_ratio"],
        "orphan_fr_ids": coverage["orphan_fr_ids"],
        "unknown_requirement_refs": coverage["unknown_requirement_refs"],
        "unattached_nfr_ids": coverage["unattached_nfr_ids"],
        "spec_validation_issues": sum(len(v) for v in issues_by_uc.values()),
        "spec_issues_by_uc": {k: v for k, v in issues_by_uc.items() if v},
        "specs_missing_preconditions": sum(1 for s in specs if not s.get("preconditions")),
        "specs_missing_success_guarantee": sum(1 for s in specs if not s.get("success_guarantee")),
        "total_extensions": sum(len(s.get("extensions", [])) for s in specs),
        "avg_main_steps": round(sum(steps) / len(steps), 2) if steps else 0,
        "n_relationships": sum(len(rel.get(k, []))
                               for k in ("associations", "includes", "extends", "generalizations")),
        "dangling_diagram_refs": len(rel_score["dangling_refs"]),
        "dangling_refs_detail": rel_score["dangling_refs"],
        "orphan_actors": rel_score["orphan_actors"],
    }
    if semantic:
        result.update(semantic_coverage(state))
    return result


def run_baseline(requirements: list[str]) -> dict:
    """순진한 baseline 그래프를 끝까지 실행해 최종 상태를 반환."""
    return build_baseline_graph().invoke({"raw_requirements": requirements})


def run_ours(requirements: list[str], thread_id: str | None = None) -> dict:
    """우리 파이프라인(gates off, step1~4)을 끝까지 실행해 최종 상태를 반환."""
    graph = build_graph(feedback_gates=False)
    tid = thread_id or f"cmp-{uuid.uuid4()}"
    return graph.invoke({"raw_requirements": requirements}, {"configurable": {"thread_id": tid}})


def compare(requirements: list[str], semantic: bool = True) -> dict:
    """baseline과 우리 파이프라인을 같은 입력으로 돌려 점수·산출물을 함께 반환.

    semantic=True(기본)면 의미론적 커버리지 검증(LLM)까지 포함해 채점한다.

    baseline 계열과 ours 계열은 서로 완전히 독립이라 각 '실행→채점'을 스레드로 동시에 돌린다.
    작업이 대부분 원격 LLM 왕복(I/O 대기)이라 GIL이 병목이 아니고, invoke_structured는
    호출마다 결과를 독립 생성해 스레드 안전하다. 각 계열은 자체 그래프/thread_id를 쓰므로
    체크포인트 충돌도 없다. (semantic 채점은 계열 내부에서 다시 UC별 병렬이라, 두 계열이
    겹치면 순간 동시 호출이 최대 2×spec_concurrency까지 오를 수 있으나 max_retries가 흡수한다.)
    """
    def _run_and_score(runner) -> dict:
        state = runner(requirements)
        return {"score": score_run(state, semantic=semantic), "state": state}

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_baseline = ex.submit(_run_and_score, run_baseline)
        fut_ours = ex.submit(_run_and_score, run_ours)
        baseline_out = fut_baseline.result()
        ours_out = fut_ours.result()

    return {
        "requirements": requirements,
        "baseline": baseline_out,
        "ours": ours_out,
    }
