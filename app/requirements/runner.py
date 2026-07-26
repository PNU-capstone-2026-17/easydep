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

from app.requirements.agent.rtm import build_rtm, render_rtm_md
from app.requirements.agent.state import AgentState
from app.requirements.agent.steps.step2_usecases import (
    check_coverage,
    identify_actors,
    identify_use_cases,
    review_model,
)
from app.requirements.agent.steps.step3_specifications import check_specs, generate_specs
from app.requirements.agent.steps.step4_diagram import (
    check_relationships,
    identify_relationships,
    render_diagram,
)
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
        "use_case_specs": _j("use_case_specs.json", []),
        "relationships": _j("relationships.json", {}),
        "diagram": diagram_path.read_text(encoding="utf-8") if diagram_path.exists() else "",
    }


def run_pipeline(classified: list[dict]) -> dict:
    """step2~4를 순서대로 실행해 전체 상태(actors~diagram)를 반환한다."""
    state: dict = {"classified": classified}
    st = cast(AgentState, state)  # 노드 함수는 AgentState를 받는다(런타임엔 동일 dict)
    state.update(identify_actors(st))
    state.update(identify_use_cases(st))
    state.update(review_model(st))
    state.update(check_coverage(st))
    state.update(generate_specs(st))
    state.update(check_specs(st))
    state.update(identify_relationships(st))
    state.update(check_relationships(st))
    state.update(render_diagram(st))
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
