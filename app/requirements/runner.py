"""파이프라인 러너 + 아티팩트 저장.

inputs/*.json(분류된 요구사항)을 step2~4에 태우고, 실행 결과를 artifacts/run_*/에
재현 가능한 형태로 남긴다:
  run_<UTC>_<input_sha10>/
    input.json          # 입력 재현용(그대로)
    manifest.json       # run_id / config 스냅샷 / input_sha256 / 스테이지 요약
    actors.json  use_cases.json  coverage.json  relationships.json
    diagram.puml
    use_cases/uc_NN_<slug>/{use_case.json, spec.json}

run_pipeline은 LLM을 호출하고, persist_run은 순수하게 파일만 쓴다(테스트 용이).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from app.requirements.agent import stages, supervisor
from app.requirements.agent.rtm import build_rtm, render_rtm_md
from app.requirements.agent.state import AgentState

# ⚠ 아래 단계 함수들은 **이 모듈의 이름으로 존재해야 한다.** `_run_stages`가
# `globals()[노드이름]`으로 찾기 때문이다(이유는 그 함수 docstring). 린터에게는 안 쓰는
# import로 보이지만 지우면 배치 실행이 죽고, 테스트의 monkeypatch도 여기에 건다.
# 자동 수정(`ruff --fix`)이 지우지 않도록 명시적으로 막는다.
from app.requirements.agent.steps.step2_usecases import (  # noqa: F401
    check_coverage,
    identify_actors,
    identify_use_cases,
    review_model,
)
from app.requirements.agent.steps.step3_specifications import (  # noqa: F401
    check_specs,
    generate_specs,
)
from app.requirements.agent.steps.step4_diagram import (  # noqa: F401
    check_relationships,
    identify_relationships,
    render_diagram,
)
from app.requirements.agent.steps.step_cloud import link_cloud_concerns  # noqa: F401
from app.requirements.config import settings

# app/requirements/runner.py 에서 저장소 루트까지는 세 단계 위다.
_ROOT = Path(__file__).parent.parent.parent
INPUTS_DIR = _ROOT / "inputs"
ARTIFACTS_DIR = _ROOT / "artifacts"


def load_input(name_or_path: str) -> dict:
    """이름(inputs/<name>.json) 또는 경로로 입력 데이터셋을 로드한다."""
    path = Path(name_or_path)
    if not path.exists():
        path = INPUTS_DIR / f"{name_or_path}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_state(run_dir: str | Path) -> dict:
    """artifacts/run_*/ 산출물을 파이프라인 state로 복원한다(피드백 재생성용)."""
    run_dir = Path(run_dir)

    def _j(name: str, default):
        p = run_dir / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    diagram_path = run_dir / "diagram.puml"
    return {
        "classified": _j("input.json", {}).get("classified", []),
        "actors": _j("actors.json", []),
        "use_cases": _j("use_cases.json", []),
        "coverage": _j("coverage.json", {}),
        "model_review": _j("model_review.json", {}),
        **_j("redo.json", {"redo_rounds": 0, "redo_history": []}),
        "use_case_specs": _j("use_case_specs.json", []),
        "relationships": _j("relationships.json", {}),
        "diagram": diagram_path.read_text(encoding="utf-8") if diagram_path.exists() else "",
    }


def _run_stages(state: dict, to_run: tuple[stages.Stage, ...]) -> list[str]:
    """단계들을 순서대로 돌린다. 실행한 노드 이름을 돌려준다.

    **정방향 패스와 되돌리기가 같은 함수를 쓴다.** 예전에는 정방향이 아홉 줄 손코딩이고
    되돌리기만 `stages.PIPELINE`에서 파생했다 — 한쪽만 파생된 것이 문제였다. 단계를 하나
    넣으면 되돌리기는 저절로 따라오는데 정방향은 안 따라오고, 그러면 정방향에 없는 단계를
    되돌리기가 실행하는 상태가 조용히 만들어진다.

    **함수는 이름으로 이 모듈에서 찾는다**(`stages.Stage.fn`을 직접 쓰지 않는다).
    import 시점에 묶인 참조를 쓰면 **한 단계를 가리키는 이름이 두 개**가 되어, 테스트의
    monkeypatch가 한쪽에만 걸린다 — 조용히 다른 코드를 재는 일이다(`feedback.py`가 같은
    이유로 `globals()` 조회를 쓴다). 이제 두 패스가 같은 조회를 쓰므로 갈릴 자리가 없다.
    """
    ran: list[str] = []
    for stage in to_run:
        fn = globals().get(stage.node, stage.fn)
        state.update(fn(cast(AgentState, state)))
        ran.append(stage.node)
    return ran


def _rerun_from(state: dict, owner: str) -> list[str]:
    """`owner` 단계부터 끝까지 다시 돌린다.

    집계 노드(`check_*`)까지 포함해야 리포트가 새 산출물을 반영한다. 되돌릴 단계는
    `stage_feedback`에서 지시를 읽는다.
    """
    order = list(stages.PIPELINE)
    start = next(i for i, s in enumerate(order) if s.key == owner)
    return _run_stages(state, tuple(order[start:]))


def run_pipeline(classified: list[dict]) -> dict:
    """step2~4를 순서대로 실행하고, 남은 결함은 **낸 단계로 되돌린다**.

    그래프(`agent/graph.py`)는 되돌아가기를 조건부 엣지로 표현하는데, 이 러너는 그래프를
    우회해 함수를 직접 부른다(그게 배치 경로의 규약이다). 그래서 같은 판단(`supervisor.decide`)을
    여기서도 돌려야 한다 — **평가 세트가 재는 실행이 이 배치**이고, 여기에 되돌아가기가
    없으면 C2의 효과가 측정에 잡히지 않는다.
    """
    state: dict = {"classified": classified}
    # 정방향 패스는 `stages.batch_order()`에서 파생한다 — 파이프라인 모양을 말하는 곳은
    # `agent/stages.py` 하나다. 여기 손으로 적으면 그 목록의 두 번째 사본이 된다.
    _run_stages(state, stages.batch_order())

    # 되돌아가기. 상한은 그래프와 같은 설정을 쓴다(`settings.max_redo_rounds`).
    while True:
        decision = supervisor.decide(state)
        if decision.action != supervisor.REDO or not decision.owner:
            break
        state["stage_feedback"] = {decision.owner: decision.instruction}
        state["redo_rounds"] = int(state.get("redo_rounds", 0) or 0) + 1
        history = list(state.get("redo_history") or [])
        ran = _rerun_from(state, decision.owner)
        history.append({
            "owner": decision.owner,
            "reason": decision.reason,
            "escalated": decision.escalated,
            "rule_ids": list(decision.rule_ids),
            "rerun": ran,
        })
        state["redo_history"] = history
        state["stage_feedback"] = {}   # 낡은 지시를 다음 라운드로 넘기지 않는다
    return state


def _sha256(obj) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"\W+", "_", text.lower()).strip("_")
    return (s[:maxlen].strip("_")) or "uc"


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _summarize(state: dict) -> dict:
    specs = state.get("use_case_specs", [])
    issues = {s["use_case_id"]: s["issues"] for s in specs if s.get("issues")}
    rel = state.get("relationships", {}) or {}
    return {
        "n_actors": len(state.get("actors", [])),
        "n_use_cases": len(state.get("use_cases", [])),
        "coverage": state.get("coverage", {}),
        "n_specs": len(specs),
        "spec_issues": issues,  # 위반 있는 UC만
        # 의미 검증이 실제로 돌았는지. 이게 없으면 결함 0건이 "깨끗하다"인지
        # "확인 못 했다"인지 매니페스트만 보고 알 수 없다.
        "model_review": state.get("model_review", {}),
        # 되돌아가기가 있었는지. 있었으면 이 실행의 비용은 한 바퀴 이상이다.
        "redo_rounds": state.get("redo_rounds", 0),
        "relationships": {
            k: len(rel.get(k, []))
            for k in ("associations", "includes", "extends", "generalizations", "derived_use_cases")
        },
    }


def persist_run(
    input_obj: dict,
    state: dict,
    dataset_name: str = "",
    artifact_root: Path | str = ARTIFACTS_DIR,
    rtm_verdicts: list[dict] | None = None,
) -> Path:
    """실행 결과를 artifacts/run_*/ 에 저장하고 그 디렉토리를 반환한다(순수 파일 IO)."""
    artifact_root = Path(artifact_root)
    sha = _sha256(input_obj)
    created = _now_utc()
    run_id = f"run_{created}_{sha[:10]}"
    run_dir = artifact_root / run_id
    (run_dir / "use_cases").mkdir(parents=True, exist_ok=True)

    _dump(run_dir / "input.json", input_obj)
    _dump(run_dir / "actors.json", state.get("actors", []))
    _dump(run_dir / "use_cases.json", state.get("use_cases", []))
    _dump(run_dir / "coverage.json", state.get("coverage", {}))
    # 2단계 의미 검증 결과. 커버리지와 따로 남긴다 — 하나는 "빠진 게 없나"(결정론),
    # 다른 하나는 "규칙을 지켰나"(의미)이고, 채점표가 둘을 따로 읽어야 한다.
    _dump(run_dir / "model_review.json", state.get("model_review", {}))
    # 되돌아가기 기록. 이게 없으면 산출물만 보고 **어느 단계가 왜 다시 돌았는지** 알 수 없다.
    # (2026-07-26 C2 첫 측정 때 실제로 없어서, 호출 수로 추정해야 했다.)
    _dump(run_dir / "redo.json", {
        "redo_rounds": state.get("redo_rounds", 0),
        "redo_history": state.get("redo_history", []),
    })
    _dump(run_dir / "use_case_specs.json", state.get("use_case_specs", []))
    _dump(run_dir / "relationships.json", state.get("relationships", {}))
    (run_dir / "diagram.puml").write_text(state.get("diagram", ""), encoding="utf-8")

    # RTM(요구사항 추적 매트릭스) 물질화 — state의 추적 정보를 매트릭스로 집계(순수, LLM 없음).
    # rtm_verdicts(semantic judge 판정)가 있으면 FR realized(검증) 컬럼도 채운다(compare 경로).
    rtm = build_rtm(state, verdicts=rtm_verdicts)
    _dump(run_dir / "rtm.json", rtm)
    (run_dir / "rtm.md").write_text(render_rtm_md(rtm, dataset_name), encoding="utf-8")

    specs_by_id = {s["use_case_id"]: s for s in state.get("use_case_specs", [])}
    for i, uc in enumerate(state.get("use_cases", []), start=1):
        uc_dir = run_dir / "use_cases" / f"uc_{i:02d}_{_slug(uc['name'])}"
        uc_dir.mkdir(parents=True, exist_ok=True)
        _dump(uc_dir / "use_case.json", uc)
        spec = specs_by_id.get(uc["id"])
        if spec is not None:
            _dump(uc_dir / "spec.json", spec)

    manifest = {
        "run_id": run_id,
        "created_utc": created,
        "dataset": dataset_name,
        "input_sha256": sha,
        "config": {
            "model": settings.model,
            "base_url": settings.base_url,
            "temperature": settings.temperature,
            "spec_concurrency": settings.spec_concurrency,
            "enable_bert_verify": settings.enable_bert_verify,
        },
        "summary": _summarize(state),
    }
    _dump(run_dir / "manifest.json", manifest)
    return run_dir
