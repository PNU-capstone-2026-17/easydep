"""메인 그래프 + 4개 스테이지 서브그래프를 mermaid PNG로 docs/graph/에 렌더한다.

    python -m scripts.render_graphs

각 그래프의 mermaid 소스(.mmd)도 함께 저장한다(PNG 렌더 실패 시 대체·재현용).
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.requirements.agent.graph import build_graph
from app.requirements.agent import subgraphs as sg

OUT = Path("docs/graph")


def render(compiled, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    g = compiled.get_graph()
    # mermaid 소스는 항상 저장(네트워크 불필요).
    (OUT / f"{name}.mmd").write_text(g.draw_mermaid(), encoding="utf-8")
    try:
        png = g.draw_mermaid_png()
        (OUT / f"{name}.png").write_bytes(png)
        print(f"  [ok] {name}.png ({len(png):,} bytes) + .mmd")
    except Exception as exc:  # noqa: BLE001 - PNG 렌더는 네트워크/크로미움 의존
        print(f"  [png실패] {name}: {type(exc).__name__}: {str(exc)[:120]} (.mmd만 저장)")


def main() -> int:
    targets = [
        (build_graph(feedback_gates=True), "main_gated"),
        (build_graph(feedback_gates=False), "main_plain"),
        (sg.build_refine_requirements(), "step1_refine_requirements"),
        (sg.build_model_use_cases(), "step2_model_use_cases"),
        (sg.build_write_specifications(), "step3_write_specifications"),
        (sg.build_draw_diagram(), "step4_draw_diagram"),
    ]
    print(f"[render_graphs] {len(targets)}개 → {OUT}/")
    for compiled, name in targets:
        render(compiled, name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
