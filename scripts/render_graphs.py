"""두 에이전트의 메인 그래프 + 스테이지 서브그래프를 mermaid PNG로 docs/graph/에 렌더한다.

    python -m scripts.render_graphs

각 그래프의 mermaid 소스(.mmd)도 함께 저장한다(PNG 렌더 실패 시 대체·재현용).
"""
from __future__ import annotations

import sys
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from app.design.graphs.design_graph import build_design_graph
from app.design.graphs.subgraphs import DESIGN_SUBGRAPHS
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
        # 요구사항 분석 에이전트
        (build_graph(feedback_gates=True), "requirements_main_gated"),
        (build_graph(feedback_gates=False), "requirements_main_plain"),
        (sg.build_refine_requirements(), "requirements_step1_refine_requirements"),
        (sg.build_model_use_cases(), "requirements_step2_model_use_cases"),
        (sg.build_write_specifications(), "requirements_step3_write_specifications"),
        (sg.build_draw_diagram(), "requirements_step4_draw_diagram"),
        # 시스템 설계 에이전트. MemorySaver를 넘겨 DB 없이 렌더한다 — 그림을 그리는 데
        # 체크포인터의 종류는 상관없고, 기본값(MySQL)이면 DB 설정이 있어야 한다.
        (build_design_graph(MemorySaver()), "design_main"),
    ]
    for stage, subs in DESIGN_SUBGRAPHS.items():
        targets.append((subs["generate"], f"design_{stage}"))
        targets.append((subs["feedback"], f"design_{stage}_feedback"))
    print(f"[render_graphs] {len(targets)}개 → {OUT}/")
    for compiled, name in targets:
        render(compiled, name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
