"""여러 표본 × 여러 CSP를 한 번에 대조한다 — **17건이 이 앱의 문제인가**를 묻는 자.

표본 하나(lecture-platform/aws)에서 17건이 나왔을 때 남은 질문은 하나였다:
그 숫자가 **이 앱의 특성인가, 두 경로가 안 만난다는 구조적 사실인가**.
답하려면 표본을 늘려야 하고, 이 하네스가 그 일을 한다.

    python -m app.core.cloudkb.tools.crosscheck_sweep

**LLM도 클라우드 호출도 없다.** 이미 있는 설계 산출물에서 계획을 다시 만들고
폐포를 계산할 뿐이다. `provider`만 바꿔 끼우면 CSP 축도 공짜로 늘어난다 —
설계 산출물은 CSP에 매여 있지 않기 때문이다.

결과는 `document/archive/`가 아니라 표준 출력과 `--json`으로 낸다. 숫자가
변하는 것이 배선의 진척이라 **박제하지 않는다.**
"""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

from app.core import plan_crosscheck as pc
from app.core.plan_crosscheck import crosscheck

_APPKB = Path(pc.__file__).resolve().parent / "cloudkb" / "appkb"

#: 대조에 쓸 것 전부. 표본 디렉터리(설계 산출물 한 벌)와 손으로 쓴 설계 계약을
#: 함께 넣는다 — **출처가 다른 것이 목적이다**(합성 픽스처는 스스로 합성이라고
#: 밝히고, order-demo는 손으로 쓴 것이다).
SAMPLES: tuple[str, ...] = ("lecture-platform", "_synthetic-fixture")
CONTRACTS: tuple[str, ...] = ("examples/order-demo.json",)

CSPS: tuple[str, ...] = ("aws", "gcp", "azure")

_KINDS = (pc.MISSING_REQUIRED, pc.DOUBLE_CREATE, pc.REDUNDANT_NODE,
          pc.UNCHECKED_RULE, pc.ABSENT_ORDER, pc.ABSENT_WARNING,
          pc.ABSENT_WAIT, pc.WEAK_READING, pc.OUT_OF_VOCABULARY)


def _targets() -> list[tuple[str, dict, dict]]:
    """(이름, 설계 계약, 요구사항) 목록. 못 읽은 것은 건너뛰지 않고 죽는다."""
    from app.core.cloudkb.tools.intake_report import _design_from, _read

    out: list[tuple[str, dict, dict]] = []
    for name in SAMPLES:
        root = _APPKB / "samples" / name
        spec, _ = _read(root, "requirements/resource_spec.json")
        spec = spec if isinstance(spec, dict) else {}
        design, problems = _design_from(root, spec)
        if design is None:
            raise SystemExit(f"{name}: 설계 계약을 못 읽었다 — {problems}")
        out.append((name, design, spec))
    for rel in CONTRACTS:
        doc = json.loads((_APPKB / rel).read_text(encoding="utf-8"))
        out.append((Path(rel).stem, doc, dict(doc.get("requirements") or {})))
    return out


def sweep() -> list[dict]:
    from app.core.cloudkb.nim_agent.design_tools import compose

    rows: list[dict] = []
    for name, design, spec in _targets():
        for csp in CSPS:
            doc = copy.deepcopy(design)
            doc.setdefault("requirements", {})["provider"] = csp
            result = crosscheck(compose(doc), csp, spec.get("region") or "-")
            rows.append({
                "sample": name, "csp": csp,
                "anchors": list(result.anchors),
                "mapped": result.mapped,
                "unmapped": sorted(result.unmapped),
                "counts": result.counts(),
                "total": len(result.findings),
            })
    return rows


def render(rows: list[dict]) -> str:
    head = (f"{'표본':22}{'csp':7}"
            + "".join(f"{k.split('-')[0][:5]:>6}" for k in _KINDS) + "   합계")
    lines = [head, "-" * len(head)]
    for row in rows:
        lines.append(
            f"{row['sample']:22}{row['csp']:7}"
            + "".join(f"{row['counts'].get(k, 0):6}" for k in _KINDS)
            + f"{row['total']:8}")
    lines += ["", "범례: " + " · ".join(
        f"{k.split('-')[0][:5]}={k}" for k in _KINDS), ""]

    # **대조되는 부분이 표본마다 같은가.** 이것이 이 하네스가 답하려는 질문이다.
    per_csp: dict[str, set[frozenset]] = {}
    for row in rows:
        per_csp.setdefault(row["csp"], set()).add(
            frozenset(row["mapped"].values()))
    lines.append("## 어휘로 읽힌 자원 집합 (표본별)")
    for csp, shapes in sorted(per_csp.items()):
        same = "표본 전체가 같다" if len(shapes) == 1 else f"{len(shapes)}가지로 갈린다"
        lines.append(f"  {csp:6} {same}: "
                     + " / ".join(", ".join(sorted(s)) for s in shapes))
    lines.append("")
    lines.append("## 어휘 밖으로 빠진 것 (앱마다 다른 부분)")
    for row in rows:
        if row["csp"] == "aws":
            lines.append(f"  {row['sample']:22} {', '.join(row['unmapped'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "buffer"):  # cp949 함정 — 보고가 전부 한국어다
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    args = list(sys.argv[1:] if argv is None else argv)
    rows = sweep()
    print(render(rows))
    if "--json" in args:
        path = Path(args[args.index("--json") + 1])
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"\n(기록: {path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
