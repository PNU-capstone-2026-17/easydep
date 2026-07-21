"""BASELINE vs 우리 파이프라인 비교 CLI.

같은 요구사항을 (1) 순진한 2콜 baseline, (2) 우리 4단계 파이프라인으로 돌리고, 동일한 결정론
검증기로 채점해 markdown+JSON 리포트를 만든다.

실행:
  python -m scripts.compare_baseline "I want to build a shopping mall service." "Users can pay online."
  python -m scripts.compare_baseline --file reqs.txt
  python -m scripts.compare_baseline --dataset <inputs/의 파일 stem>
출력: docs/research/baseline-comparison.md  (+ .json)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.agent.compare import compare
from app.runner import ARTIFACTS_DIR, persist_run

OUT_MD = "docs/research/baseline-comparison.md"
OUT_JSON = "docs/research/baseline-comparison.json"

# (라벨, score 키, 방향) — 방향: "up"=클수록 좋음, "down"=작을수록 좋음, None=중립(정보)
_METRICS = [
    ("유스케이스 수", "n_use_cases", None),
    ("명세 수", "n_specs", None),
    ("액터 수", "n_actors", None),
    ("FR 총계", "fr_total", None),
    ("FR 커버리지(결정론: 주장 기준)", "coverage_ratio", "up"),
    ("FR 커버리지(의미론: 검증된 것만)", "semantic_coverage_ratio", "up"),
    ("거짓 커버리지 주장(LLM 판정)", "false_coverage_claims", "down"),
    ("복합 FR(품질제약 융합)", "compound_fr_issues", "down"),
    ("고아 FR(어떤 UC에도 미포함)", "orphan_fr_ids", "down"),
    ("유령 요구 참조(환각)", "unknown_requirement_refs", "down"),
    ("명세 정적 검증 위반", "spec_validation_issues", "down"),
    ("precondition 없는 명세", "specs_missing_preconditions", "down"),
    ("success_guarantee 없는 명세", "specs_missing_success_guarantee", "down"),
    ("다이어그램 유령 참조", "dangling_diagram_refs", "down"),
    ("고아 액터", "orphan_actors", "down"),
    ("총 확장(예외·대안 흐름)", "total_extensions", "up"),
    ("평균 주시나리오 스텝", "avg_main_steps", None),
    ("관계 수", "n_relationships", None),
]


def _cell(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, list):
        return str(len(v)) if v else "0"
    if isinstance(v, float):
        return f"{v:.2f}" if v != int(v) else str(int(v))
    return str(v)


def _val(v):
    return len(v) if isinstance(v, list) else v


def _winner(direction, b, o) -> str:
    if direction is None or b is None or o is None:
        return "—"
    bv, ov = _val(b), _val(o)
    if bv == ov:
        return "동률"
    if direction == "up":
        return "ours" if ov > bv else "baseline"
    return "ours" if ov < bv else "baseline"  # down


def build_report(result: dict) -> str:
    reqs = result["requirements"]
    b = result["baseline"]["score"]
    o = result["ours"]["score"]
    L: list[str] = []
    L.append("# Baseline vs 우리 파이프라인 — 정량 비교")
    L.append("")
    L.append(f"- 입력 요구사항 {len(reqs)}개")
    L.append("- **baseline**: `intake → 명세 원샷(1콜) → 관계 원샷(1콜)` "
             "— **FR/NFR 분류 없이 요구사항 전체를 그대로** 주고, 커버리지 강제·명세 검증/반성·관계 참조가드 **없음**")
    L.append("- **ours**: 4단계 분해(액터→UC→명세→관계) + 각 단계 정적/의미 검증·반성·커버리지 강제·참조 가드")
    L.append("- 채점: 양쪽 산출물에 **동일한 결정론 검증기**(check_coverage·_validate_spec·관계 무결성)를 적용")
    L.append("")
    L.append("## 입력 요구사항")
    for r in reqs:
        L.append(f"- {r}")
    L.append("")
    L.append("## 지표 대비")
    L.append("")
    L.append("| 지표 | baseline | ours | 우위 |")
    L.append("|---|---|---|---|")
    for label, key, direction in _METRICS:
        bv, ov = b.get(key), o.get(key)
        L.append(f"| {label} | {_cell(bv)} | {_cell(ov)} | {_winner(direction, bv, ov)} |")
    L.append("")
    L.append("> **해석 유의**: baseline은 **분류 없이 요구사항 전체를 그대로** 주므로(번호 R1..는 추적용, "
             "커버리지=전체 요구 대비 참조 비율), 우리 파이프라인의 clarify 구체화+FR/NFR 분류를 거친 요구 집합과 "
             "FR 총계·개수 지표가 달라진다(교란요인). 따라서 개수 지표는 참고용이고, **정당성의 핵심 근거는 요구 수와 "
             "무관한 품질 지표** — 특히 `명세 정적 검증 위반`(양쪽에 동일 검증기 적용)·`유령 참조`·`고아 액터` — 다. "
             "baseline이 남긴 위반을 우리 시스템의 검증·반성 루프가 0으로 수렴시키는지가 대비의 요점이다.")
    L.append("")

    # 결함 상세(baseline이 남긴 것 = 우리 시스템의 검증이 잡아내는 것)
    L.append("## 결함 상세")
    for name, sc in (("baseline", b), ("ours", o)):
        L.append(f"### {name}")
        if sc["spec_issues_by_uc"]:
            L.append("- **명세 정적 위반**:")
            for uc, issues in sc["spec_issues_by_uc"].items():
                L.append(f"  - `{uc}`: {' · '.join(issues)}")
        if sc.get("compound_fr_detail"):
            L.append("- **복합 FR**(품질제약이 FR에 융합 — 원자성 위반):")
            for d in sc["compound_fr_detail"]:
                L.append(f"  - {d}")
        if sc["orphan_fr_ids"]:
            L.append(f"- **고아 FR**(누락): {', '.join(sc['orphan_fr_ids'])}")
        if sc.get("false_coverage_detail"):
            L.append("- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):")
            for d in sc["false_coverage_detail"]:
                L.append(f"  - {d}")
        if sc["dangling_refs_detail"]:
            L.append(f"- **다이어그램 유령 참조**: {' · '.join(sc['dangling_refs_detail'])}")
        if sc["orphan_actors"]:
            L.append(f"- **고아 액터**: {', '.join(sc['orphan_actors'])}")
        if not (sc["spec_issues_by_uc"] or sc["orphan_fr_ids"] or sc.get("false_coverage_detail")
                or sc.get("compound_fr_detail") or sc["dangling_refs_detail"] or sc["orphan_actors"]):
            L.append("- (검출된 결함 없음)")
        L.append("")

    # 다이어그램
    L.append("## 산출 다이어그램 (PlantUML)")
    for name, side in (("baseline", result["baseline"]), ("ours", result["ours"])):
        L.append(f"### {name}")
        L.append("```")
        L.append((side["state"].get("diagram") or "(없음)").strip())
        L.append("```")
        L.append("")
    return "\n".join(L) + "\n"


def _load_requirements(args) -> list[str]:
    if args.dataset:
        data = json.loads((Path("inputs") / f"{args.dataset}.json").read_text(encoding="utf-8"))
        return [c["text"] for c in data.get("classified", [])]
    if args.file:
        return [ln.strip() for ln in Path(args.file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in args.requirements if r.strip()]


def _persist_artifacts(reqs: list[str], result: dict, root: Path) -> dict[str, Path]:
    """baseline·ours 산출물을 각각 artifacts/run_*/ 에 저장한다(run_pipeline과 동일 포맷).

    각 side의 최종 state(actors/use_cases/coverage/specs/relationships/diagram)를 persist_run에
    그대로 넘긴다. input_obj의 name/classified가 side마다 달라 run_id(sha 기반)가 겹치지 않는다.
    """
    dirs: dict[str, Path] = {}
    for side in ("baseline", "ours"):
        state = result[side]["state"]
        # semantic 채점이 돌았으면 판정 원본을 RTM realized 컬럼에 넘긴다(없으면 None → 미판정).
        verdicts = result[side]["score"].get("coverage_verdicts")
        input_obj = {
            "name": f"compare-{side}",
            "raw_requirements": reqs,
            "classified": state.get("classified", []),
        }
        dirs[side] = persist_run(
            input_obj, state, dataset_name=f"compare-{side}", artifact_root=root,
            rtm_verdicts=verdicts,
        )
    return dirs


def _reconfigure_utf8() -> None:
    """윈도우 콘솔(cp949 등)에서 한글·em dash 출력이 깨지지 않도록 stdout/stderr를 UTF-8로."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    _reconfigure_utf8()
    parser = argparse.ArgumentParser(description="Baseline vs 우리 파이프라인 비교")
    parser.add_argument("requirements", nargs="*", help="요구사항 문장(각 인자 = 한 줄)")
    parser.add_argument("--file", help="요구사항을 한 줄에 하나씩 담은 텍스트 파일")
    parser.add_argument("--dataset", help="inputs/<stem>.json 의 classified 텍스트를 입력으로 사용")
    parser.add_argument("--no-semantic", action="store_true",
                        help="의미론적 커버리지 검증(LLM judge, UC당 콜 × baseline·ours 양쪽) 생략 — "
                             "결정론 지표만 채점해 반복 실행 시간을 대폭 단축")
    parser.add_argument("--no-artifacts", action="store_true",
                        help="artifacts/run_*/ 산출물 저장 생략(기본은 run_pipeline처럼 저장)")
    parser.add_argument("--artifacts-root", default=str(ARTIFACTS_DIR),
                        help="아티팩트 저장 루트 디렉토리(기본: artifacts/)")
    args = parser.parse_args()

    reqs = _load_requirements(args)
    if not reqs:
        parser.error("요구사항이 없습니다. 인자/--file/--dataset 중 하나로 제공하세요.")

    semantic = not args.no_semantic
    mode = "결정론만" if args.no_semantic else "결정론+의미검증(LLM)"
    print(f"[compare] 요구사항 {len(reqs)}개 — baseline과 우리 파이프라인 실행 중"
          f"(LLM 호출, 채점={mode})...")
    result = compare(reqs, semantic=semantic)

    Path(OUT_MD).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_MD).write_text(build_report(result), encoding="utf-8")
    # state는 크고 직렬화 어려운 객체(messages 등)가 있어 점수만 JSON으로 저장.
    Path(OUT_JSON).write_text(json.dumps(
        {"requirements": reqs,
         "baseline": result["baseline"]["score"],
         "ours": result["ours"]["score"]},
        ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.no_artifacts:
        dirs = _persist_artifacts(reqs, result, Path(args.artifacts_root))
        print(f"[compare] 아티팩트 저장: baseline → {dirs['baseline']}")
        print(f"[compare]              ours     → {dirs['ours']}")

    b, o = result["baseline"]["score"], result["ours"]["score"]
    print("\n=== 요약 ===")
    print(f"{'지표':30s} {'baseline':>10s} {'ours':>10s}")
    for label, key, _ in _METRICS:
        print(f"{label:30s} {_cell(b.get(key)):>10s} {_cell(o.get(key)):>10s}")
    print(f"\n리포트 저장: {OUT_MD}")


if __name__ == "__main__":
    main()
