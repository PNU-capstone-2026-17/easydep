"""표본 한 벌을 **계획 ↔ 실측 대조**에 걸어 본다 — 하네스.

판정 규칙은 `app/core/plan_crosscheck.py`에 있고 여기서는 표본을 읽어 태우기만
한다. 갈라 둔 이유는 층 규율이다: **라이브러리는 하네스를 부르지 않는다**
(`tests/test_core_layer.py`가 지킨다). 표본 디렉터리를 읽는 일은 하네스의 몫이다.

    python -m app.core.cloudkb.tools.crosscheck_sample <표본 디렉터리>

`crosscheck.json`을 표본 옆에 남긴다 — 나중에 배선하면 이 숫자가 줄어야 하고,
`tests/test_plan_crosscheck.py`가 그 기록을 읽는다.

**LLM도 클라우드 호출도 없다.** 설계 산출물에서 계획을 다시 만들고 폐포를 계산할
뿐이라 몇 번이든 돌려도 된다.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from app.core.plan_crosscheck import crosscheck, render


def main(argv: list[str] | None = None) -> int:
    # **cp949 함정.** 이 저장소는 여기서 이미 물렸다 — `—` 하나가 3시간짜리
    # 캠페인을 죽였다(2026-07-27). 보고가 전부 한국어라 다시 물릴 자리다.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("사용법: python -m app.core.cloudkb.tools.crosscheck_sample <표본 디렉터리>")
        return 2
    root = Path(args[0])

    from app.core.cloudkb.nim_agent.design_tools import compose
    from app.core.cloudkb.tools.intake_report import _design_from, _read

    spec, _ = _read(root, "requirements/resource_spec.json")
    spec = spec if isinstance(spec, dict) else None
    design, problems = _design_from(root, spec)
    if design is None:
        print(f"설계 계약을 못 읽었다: {problems}")
        return 1
    csp = (spec or {}).get("provider") or (design.get("requirements") or {}).get("provider")
    if not csp:
        print("provider가 없다 — 주장이 CSP로 색인돼 있어 대조할 수 없다")
        return 1

    result = crosscheck(compose(design), csp, (spec or {}).get("region") or "-")
    print(render(result))
    out = root / "crosscheck.json"
    out.write_text(json.dumps(
        {"csp": result.csp, "anchors": list(result.anchors),
         "mapped": result.mapped, "unmapped": result.unmapped,
         "weak": result.weak, "counts": result.counts(),
         "findings": [f.__dict__ for f in result.findings]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"(기록: {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
