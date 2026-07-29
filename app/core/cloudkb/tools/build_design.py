"""요구사항 산출물 → **설계 산출물**을 파일로 남긴다(설계 에이전트를 직접 돌려서).

    python -m app.core.cloudkb.tools.build_design app/core/cloudkb/appkb/samples/<이름>

`build_sample`이 남긴 `requirements/`를 읽어 설계 에이전트의 상태를 조립하고,
클래스 → 시퀀스 → API → ERD를 차례로 돌려 `design/`에 저장한다.

## 왜 이게 필요한가

배포 계획·다이어그램의 진입 계약은 **설계 산출물**을 요구한다(`appkb/easydep.py`의
`design_from_easydep`). 요구사항만으로는 읽을 산출물이 하나도 없어 계약에서 막히고,
실제로 그랬다(2026-07-29): `RESOURCE_SPEC`은 통과하는데
`artifacts: [] should be non-empty`로 배포가 서지 않았다. 사슬이 끊긴 자리가 여기다.

## 남의 코드를 부르기만 한다

`app/design/`은 **팀원의 코드이고 고치지 않는다.** 여기서 하는 일은 셋뿐이다.

  1. 저장된 요구사항 산출물을 `ArchitectureState`의 키로 옮긴다(그 매핑의 진실은
     `app/requirements/api.py`의 `persist_analysis`다 — 여기서 새로 정하지 않고 따른다).
  2. 설계 노드를 선행조건 순서대로 부른다(그 순서의 진실은 `app/design/api.py`의
     `PREREQUISITES`다).
  3. 나온 것을 파일로 남긴다.

DB는 거치지 않는다. 서빙 경로(`POST /api/apps/{id}/stages/{stage}`)는 앱 행과 저장소를
요구하는데, 표본을 만드는 데에 그것까지 세울 이유가 없고 노드 자체는 순수 함수다.

## 남기는 것

    design/class_diagram.puml
    design/sequence_diagram.puml
    design/api_spec.json
    design/erd.puml
    design/RUN.json          실행 메타(모델·시각·단계별 성패·문법 검증 결과)

**한 단계가 실패하면 거기서 멈춘다.** 뒤 단계가 앞 산출물을 입력으로 받기 때문에,
빈 것을 물려 돌리면 나오는 것은 산출물이 아니라 그럴듯한 지어냄이다. 어디서 멈췄는지는
`RUN.json`에 남는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from ..kbcommon.console import use_utf8
from .provenance import git_head

#: 단계 이름 → (상태 키, 저장할 파일). 순서가 곧 선행조건 순서다
#: (`app/design/api.py`의 `PREREQUISITES`).
_STAGES = (
    ("class_diagram", "class_diagram_puml", "design/class_diagram.puml"),
    ("sequence_diagram", "sequence_diagram_puml", "design/sequence_diagram.puml"),
    ("api_spec", "api_spec", "design/api_spec.json"),
    ("erd", "erd_puml", "design/erd.puml"),
)


def _load(root: Path, rel: str):
    path = root / "requirements" / rel
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _build_state(root: Path) -> tuple[dict, list[str]]:
    """저장된 요구사항 산출물 → 설계 에이전트의 초기 상태. (상태, 없는 것).

    키 이름을 여기서 정하지 않는다 — `app/requirements/api.py`의 `persist_analysis`가
    실제 서빙 경로에서 쓰는 그 이름을 그대로 쓴다. 다르게 적으면 이 표본은 서빙에서
    나오는 것과 다른 물건이 되고, 그러면 표본으로서 값이 없다.
    """
    missing: list[str] = []
    state: dict = {"app_id": root.name}

    reqs_file = root / "requirements.txt"
    if reqs_file.exists():
        state["requirements_text"] = reqs_file.read_text(encoding="utf-8").strip()
    constraints_file = root / "constraints.txt"
    if constraints_file.exists():
        state["resource_constraints_text"] = constraints_file.read_text(
            encoding="utf-8").strip()

    classified = _load(root, "classified.json")
    if classified:
        state["refined_requirements"] = classified
    else:
        missing.append("refined_requirements (requirements/classified.json)")

    specs = _load(root, "use_case_specs.json")
    if specs:
        # 설계 에이전트는 `usecase_spec` 하나를 통째로 프롬프트에 넣는다
        # (`usecase_spec_text`). 액터·유스케이스가 있어야 명세가 읽히므로 함께 담는다.
        state["usecase_spec"] = {
            "actors": _load(root, "actors.json") or [],
            "use_cases": _load(root, "use_cases.json") or [],
            "use_case_specs": specs,
        }
    else:
        missing.append("usecase_spec (requirements/use_case_specs.json)")

    diagram = root / "requirements" / "usecase_diagram.puml"
    if diagram.exists():
        state["usecase_diagram_puml"] = diagram.read_text(encoding="utf-8")

    spec = _load(root, "resource_spec.json")
    if spec:
        state["resource_spec"] = spec
    else:
        # 배포 계획에는 필요하지만 **설계 단계의 선행조건은 아니다.** 없다고 멈추지
        # 않고, 없다는 사실만 남긴다.
        missing.append("resource_spec (계약 미충족 — 배포 단계에서 제약이 빈다)")

    return state, missing


def _run_stage(stage: str, state: dict) -> dict:
    """설계 노드 하나를 부른다. 상태 변경분(delta)을 돌려준다."""
    from app.design.api import (
        auto_fix_api_spec,
        auto_fix_puml_stage,
        generate_class_diagram_once,
        merge_state,
    )
    from app.design.nodes.artifact_generation import (
        generate_api_spec,
        generate_erd,
        generate_sequence_diagram,
    )

    if stage == "class_diagram":
        return generate_class_diagram_once(state)
    if stage == "sequence_diagram":
        return auto_fix_puml_stage(
            stage, merge_state(state, generate_sequence_diagram(state)), "")
    if stage == "api_spec":
        return auto_fix_api_spec(merge_state(state, generate_api_spec(state)), "")
    return auto_fix_puml_stage(stage, merge_state(state, generate_erd(state)), "")


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(
        prog="build_design",
        description="요구사항 산출물 → 설계 산출물 (설계 에이전트를 직접 실행)")
    parser.add_argument("sample_dir")
    parser.add_argument("--only", nargs="*", choices=[s for s, _, _ in _STAGES],
                        help="이 단계들만 돌린다(앞 단계 산출물은 파일에서 읽는다)")
    # **무인 실행에는 상한이 있어야 한다.** 설계 쪽 수정 루프는 `MAX_REVISION_ATTEMPTS`가
    # 없으면 **검증을 통과할 때까지 무제한**으로 LLM을 다시 부른다(`app/design/api.py`의
    # `revision_attempt_limit`, 기본 0 = 무제한). 서빙에서는 사람이 보고 있으니 맞는
    # 기본값이지만, 표본 생성은 사람이 없는 자리다 — 실제로 시퀀스 단계가 40분을 돌았고
    # (2026-07-29) 남은 것은 출력 없는 프로세스 하나였다.
    #
    # 남의 코드를 고치지 않고 **그쪽이 열어 둔 환경변수로** 묶는다. 통과 못 한 산출물은
    # 문법 오류를 달고 저장되고, 그 사실이 `RUN.json`에 남는다 — 무한히 다시 묻느니
    # 못 통과했다고 적는 편이 낫다.
    parser.add_argument("--max-revisions", type=int, default=3,
                        help="문법 검증 실패 시 LLM에 다시 물을 최대 횟수 (0=무제한)")
    args = parser.parse_args(argv)

    import os
    os.environ["MAX_REVISION_ATTEMPTS"] = str(args.max_revisions)

    root = Path(args.sample_dir)
    state, missing = _build_state(root)
    if any(m.startswith(("refined_requirements", "usecase_spec")) for m in missing):
        print("설계의 선행조건이 없습니다:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        print("\nbuild_sample을 먼저 돌리십시오.", file=sys.stderr)
        return 2

    # 이미 있는 설계 산출물은 읽어서 상태에 넣는다 — `--only`로 뒷단계만 다시 돌릴 때
    # 앞 산출물을 LLM으로 다시 만들지 않기 위해서다.
    for _stage, key, rel in _STAGES:
        path = root / rel
        if path.exists():
            body = path.read_text(encoding="utf-8")
            state[key] = json.loads(body) if rel.endswith(".json") else body

    print(f"표본: {root.name}")
    for item in missing:
        print(f"  ⚠ 없음 — {item}")
    from app.requirements.config import settings
    print(f"모델: {settings.model}\n")

    (root / "design").mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    log: list[dict] = []
    wanted = set(args.only or [s for s, _, _ in _STAGES])

    for stage, key, rel in _STAGES:
        if stage not in wanted:
            print(f"  · {stage} 건너뜀")
            continue
        print(f"  → {stage} …", end=" ", flush=True)
        try:
            state = {**state, **_run_stage(stage, state)}
        except Exception as exc:  # noqa: BLE001 — 어디서 멈췄는지가 결과다
            print(f"실패: {type(exc).__name__}: {exc}")
            log.append({"stage": stage, "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"})
            # **뒤 단계는 앞 산출물을 입력으로 받는다.** 빈 것을 물려 돌리면 나오는
            # 것은 산출물이 아니라 지어냄이라, 여기서 멈춘다.
            break

        value = state.get(key)
        if not value:
            print("빈 산출물")
            log.append({"stage": stage, "ok": False, "error": "빈 산출물"})
            break

        body = (json.dumps(value, ensure_ascii=False, indent=2)
                if rel.endswith(".json") else str(value))
        (root / rel).write_text(body, encoding="utf-8")
        valid = state.get(f"{key.removesuffix('_puml')}_syntax_valid")
        if key == "api_spec":
            valid = state.get("api_spec_syntax_valid")
        errors = state.get(f"{key.removesuffix('_puml')}_syntax_errors") or []
        print(f"저장 {rel} ({len(body):,}B)"
              + ("" if valid is not False else f"  ⚠ 문법 오류 {len(errors)}건"))
        log.append({"stage": stage, "ok": True, "file": rel,
                    "bytes": len(body), "syntaxValid": valid,
                    "syntaxErrors": errors[:5]})

    # **이번에 안 돌린 단계의 기록을 지우지 않는다.** `--only`로 뒷단계만 다시 돌리면
    # 앞 단계 산출물은 파일로 남아 있는데 그 기록만 덮여 사라진다 — 실제로 그랬고
    # (2026-07-29: 클래스·시퀀스가 기록에서 증발), 그러면 이 파일은 `design/`에 있는
    # 것의 출처를 더 이상 설명하지 못한다. 이어받은 것은 **어느 실행의 것인지**를
    # 달아서 이번 실행과 구별한다.
    run_path = root / "design" / "RUN.json"
    carried: list[dict] = []
    if run_path.exists():
        try:
            prior = json.loads(run_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prior = {}
        prior_at = prior.get("startedAt")
        for entry in prior.get("stages") or []:
            if entry.get("stage") not in wanted:
                carried.append({**entry, "fromRun": entry.get("fromRun") or prior_at})

    run_path.write_text(json.dumps({
        "startedAt": started,
        "finishedAt": datetime.now(UTC).isoformat(),
        "model": settings.model,
        "code": git_head(),
        "sample": root.name,
        "maxRevisionAttempts": args.max_revisions,
        "ranStages": sorted(wanted),
        # 무엇이 없어서 무엇이 빈 채로 갔는지. 부재도 사실이라 남긴다.
        "missingUpstream": missing,
        "stages": log + carried,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    done = [entry["stage"] for entry in log if entry["ok"]]
    failed = [entry["stage"] for entry in log if not entry["ok"]]
    print(f"\n나온 것 {len(done)}건: {', '.join(done) or '(없음)'}")
    if failed:
        print(f"멈춘 자리: {failed[0]} — design/RUN.json에 사유가 있습니다.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
