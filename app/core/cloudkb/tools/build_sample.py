"""요구사항 문장 한 벌 → **상류 산출물 전부를 파일로** 남긴다.

    python -m app.core.cloudkb.tools.build_sample app/core/cloudkb/appkb/samples/<이름>

`<이름>/requirements.txt`와 `<이름>/constraints.txt`를 읽어 요구사항 에이전트를
돌리고, 나온 것을 그 디렉터리에 저장한다.

## 왜 CLI로 안 하나

CLI는 화면에 찍고 버린다. 실제로 2026-07-29에 한 번 돌리고 나서 `RESOURCE_SPEC`이
나왔는지 확인하지 못해 다시 돌려야 했다 — **LLM을 여러 번 부르는 실행을 기록 없이
날리는 것**은 이 저장소의 측정 규율에도 어긋난다(`easydep-unattended-runs`).

여기서는 산출물을 전부 남긴다. 그래야:

  - 배포 단계가 **같은 입력**으로 재현된다(LLM을 다시 부르지 않고)
  - "이 결함이 입력 탓인가 우리 파서 탓인가"를 `intake_report`가 가를 수 있다
  - 무엇이 안 나왔는지(`RESOURCE_SPEC` 미충족 등)가 파일의 **부재**로 남는다

## 남기는 것

    requirements/classified.json       FR/NFR 분류
    requirements/use_cases.json        유스케이스
    requirements/use_case_specs.json   유스케이스 명세
    requirements/usecase_diagram.puml  유스케이스 다이어그램
    requirements/resource_spec.json    RESOURCE_SPEC (계약을 만족할 때만 존재)
    requirements/resource_intake.json  왜 못 채웠는지 (계약 미충족이어도 늘 존재)
    requirements/cloud_concerns.json   관심사 커버리지
    RUN.json                           실행 메타(모델·시각·상태·단계)

**계약을 만족하지 못하면 `resource_spec.json`이 없다.** 그 부재가 사실이므로 빈
파일을 만들지 않는다 — 반쯤 채운 사양을 내보내면 뒤 단계가 그것을 사양으로 알고
조인을 돌린다(`agent/state.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..kbcommon.console import use_utf8
from .provenance import git_head

#: **페이로드** 키 → 저장할 파일. 없는 키는 파일을 만들지 않는다(부재가 사실이다).
#:
#: 상태(`AgentState`) 키가 아니라 **페이로드 키**다 — 둘이 다르다. 첫 판에서 상태
#: 이름(`classified`)을 적었다가 "안 나옴"으로 찍혔는데 실제로는 나와 있었다
#: (페이로드에서는 `requirements`다). `graph.py`의 `_result_payload`가 진실이다.
_ARTIFACTS = {
    "requirements": "requirements/classified.json",
    "actors": "requirements/actors.json",
    "use_cases": "requirements/use_cases.json",
    "use_case_specs": "requirements/use_case_specs.json",
    "resource_spec": "requirements/resource_spec.json",
    "resource_intake": "requirements/resource_intake.json",
    "cloud_concerns": "requirements/cloud_concerns.json",
    "coverage": "requirements/coverage.json",
    "spec_report": "requirements/spec_report.json",
    "relationships": "requirements/relationships.json",
    "relationship_report": "requirements/relationship_report.json",
    "model_review": "requirements/model_review.json",
    "telemetry": "requirements/telemetry.json",
}


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(
        prog="build_sample",
        description="요구사항 문장 → 상류 산출물 전부를 파일로")
    parser.add_argument("sample_dir")
    parser.add_argument("--no-bert", action="store_true", default=True)
    args = parser.parse_args(argv)

    root = Path(args.sample_dir)
    reqs_file = root / "requirements.txt"
    if not reqs_file.exists():
        print(f"{reqs_file}가 없습니다.", file=sys.stderr)
        return 2
    requirements = [ln.strip() for ln in reqs_file.read_text(encoding="utf-8").splitlines()
                    if ln.strip()]
    constraints_file = root / "constraints.txt"
    constraints = (constraints_file.read_text(encoding="utf-8").strip()
                   if constraints_file.exists() else "")

    from app.requirements.agent import start_analysis
    from app.requirements.config import settings

    if args.no_bert:
        settings.enable_bert_verify = False

    print(f"요구사항 {len(requirements)}문장 · 제약 {'있음' if constraints else '**없음**'}")
    print(f"모델: {settings.model}\n")

    started = datetime.now(UTC).isoformat()
    thread_id = str(uuid.uuid4())
    payload = start_analysis(requirements, thread_id, constraints_text=constraints)

    (root / "requirements").mkdir(parents=True, exist_ok=True)
    written, absent = [], []
    for key, rel in _ARTIFACTS.items():
        value = payload.get(key)
        if value in (None, [], {}):
            absent.append(key)
            continue
        (root / rel).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        written.append(rel)

    # 다이어그램은 텍스트라 따로. 페이로드 키는 `diagram` 하나다 — 요구사항
    # 에이전트가 내는 것은 **유스케이스 다이어그램**이고, 클래스·시퀀스·ER은
    # 설계 에이전트의 몫이라 여기서 안 나온다(그 사실을 `absent`가 기록한다).
    text = payload.get("diagram")
    if isinstance(text, str) and text.strip():
        rel = "requirements/usecase_diagram.puml"
        (root / rel).write_text(text, encoding="utf-8")
        written.append(rel)
    else:
        absent.append("diagram")

    (root / "RUN.json").write_text(json.dumps({
        "startedAt": started,
        "finishedAt": datetime.now(UTC).isoformat(),
        "model": settings.model,
        # 어느 커밋에서 돌았나 — 시각만으로는 재현이 안 된다(프롬프트가 그 사이에 바뀐다).
        "code": git_head(),
        "threadId": thread_id,
        "status": payload.get("status"),
        "phase": payload.get("phase"),
        "requirementCount": len(requirements),
        "hasConstraints": bool(constraints),
        # **무엇이 안 나왔는지도 기록한다.** 부재가 사실이므로 남긴다.
        "absentArtifacts": sorted(absent),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"저장 {len(written)}건:")
    for rel in written:
        print(f"  O {rel}")
    print(f"\n안 나온 것 {len(absent)}건: {', '.join(sorted(absent)) or '(없음)'}")
    if "resource_spec" in absent:
        print("  ⚠ **RESOURCE_SPEC이 안 나왔습니다** — 계약을 만족하지 못했다는 뜻이고,")
        print("    배포 단계는 provider·region 없이 조인이 전부 닫힌 채로 돕니다.")
        print("    이유는 requirements/resource_intake.json에 있습니다.")
    print(f"\n상태: {payload.get('status')} · 단계: {payload.get('phase')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
