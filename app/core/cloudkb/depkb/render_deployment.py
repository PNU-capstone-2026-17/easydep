"""배포 다이어그램 — `design_view`를 사람이 보는 그림으로.

`render_graph.py`(claims 전체 그래프)와 다른 것이다: 저쪽은 **지식의 지도**이고
이쪽은 **특정 앱의 배포 형상**이다. 설계 에이전트가 낼 산출물의 시안이기도 하다.

읽는 법이 곧 논지다 — 같은 요구를 3사에 나란히 놓는다. 노드 수부터 다르고,
누가 만드는지(사용자/서버)가 다르고, 물어야 할 것이 다르다.

인코딩(색+선형 이중, 팔레트는 검증기 통과):
- **선택한 것**(앵커) 진한 테두리 · **반드시 필요** 실선 · **선택 사항** 파선 ·
  **서버가 만듦** 점선 + "자동" 배지
- 간선은 `A→B = A가 B를 요구한다`(그림 위에도 적는다)

실행: `python -m app.core.cloudkb.depkb.render_deployment` → `deployment-diagram.html`
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .examples import EXAMPLES, Example
from .translate import translate

_OUT = Path(__file__).resolve().parent / "deployment-diagram.html"
CSPS = ("aws", "azure", "gcp")

#: 그룹 → 밴드 순서(위에서 아래로). **우리 구성** — 가독 목적뿐이다.
_BANDS = ("컨테이너", "컴퓨트", "연결", "네트워크", "기타")
_BAND_H = 92
_NODE_W, _NODE_H = 104, 34
_PANEL_W = 380


def _layout(nodes: list[dict]) -> dict[str, tuple[float, float]]:
    """그룹 밴드에 가로로 늘어놓는다 — 결정적이도록 이름순."""
    by_band: dict[str, list[dict]] = {}
    for n in nodes:
        by_band.setdefault(n["group"], []).append(n)
    pos: dict[str, tuple[float, float]] = {}
    row = 0
    for band in _BANDS:
        items = sorted(by_band.get(band, []), key=lambda n: n["id"])
        if not items:
            continue
        y = 34 + row * _BAND_H
        step = _PANEL_W / (len(items) + 1)
        for i, n in enumerate(items, start=1):
            pos[n["id"]] = (step * i, y)
        row += 1
    return pos


def _node_class(node: dict) -> str:
    if node["role"] == "anchor":
        return "anchor"
    if node["role"] == "required":
        return "required"
    return "auto" if node["autoFilledNotice"] else "attachable"


def _panel(csp: str, view: dict, questions: list[str]) -> str:
    nodes = view["nodes"]
    pos = _layout(nodes)
    height = 34 + max(1, len({n["group"] for n in nodes})) * _BAND_H + 10
    svg: list[str] = []

    bands_drawn = set()
    for n in nodes:
        if n["group"] in bands_drawn or n["id"] not in pos:
            continue
        bands_drawn.add(n["group"])
        y = pos[n["id"]][1]
        svg.append(
            f'<text class="band" x="6" y="{y - _NODE_H / 2 - 7:.0f}">'
            f'{html.escape(n["group"])}</text>')

    for e in view["edges"]:
        if e["from"] not in pos or e["to"] not in pos:
            continue
        x1, y1 = pos[e["from"]]
        x2, y2 = pos[e["to"]]
        dy = 1 if y2 > y1 else -1
        y1e = y1 + dy * _NODE_H / 2
        y2e = y2 - dy * _NODE_H / 2
        mx = (x1 + x2) / 2 + (28 if abs(x1 - x2) > 40 else 0)
        svg.append(
            f'<path class="edge" d="M{x1:.0f},{y1e:.0f} Q{mx:.0f},'
            f'{(y1e + y2e) / 2:.0f} {x2:.0f},{y2e:.0f}" marker-end="url(#dep-arrow)">'
            f'<title>{html.escape(e["from"])}가 {html.escape(e["to"])}를 요구</title></path>')

    for n in nodes:
        if n["id"] not in pos:
            continue
        x, y = pos[n["id"]]
        cls = _node_class(n)
        tip = n["autoFilledNotice"] or (
            "왜: " + ", ".join(n["because"]) if n["because"] else n["label"])
        svg.append(
            f'<g class="node {cls}">'
            f'<rect x="{x - _NODE_W / 2:.0f}" y="{y - _NODE_H / 2:.0f}" '
            f'width="{_NODE_W}" height="{_NODE_H}" rx="8">'
            f'<title>{html.escape(tip)}</title></rect>'
            f'<text x="{x:.0f}" y="{y + 4:.0f}">{html.escape(n["id"])}</text>'
            + (f'<text class="badge" x="{x + _NODE_W / 2 - 6:.0f}" '
               f'y="{y - _NODE_H / 2 + 12:.0f}">자동</text>'
               if cls == "auto" else "")
            + "</g>")

    counts = {
        "만들 것": sum(1 for n in nodes if n["role"] in ("anchor", "required")),
        "서버가 채움": sum(1 for n in nodes if n["autoFilledNotice"]),
        "물어볼 것": len(questions),
    }
    stat = " · ".join(f"{k} {v}" for k, v in counts.items())
    qs = "".join(f"<li>{html.escape(q)}</li>" for q in questions)
    cs = "".join(f"<li>{html.escape(c)}</li>" for c in view["constraints"])
    return (
        f'<section class="panel"><h3>{csp}</h3><p class="stat">{stat}</p>'
        f'<svg viewBox="0 0 {_PANEL_W} {height:.0f}" role="img" '
        f'aria-label="{csp} 배포 다이어그램">{"".join(svg)}</svg>'
        + (f'<div class="ask"><b>물어볼 것</b><ul>{qs}</ul></div>' if qs else "")
        + (f'<div class="rule"><b>지켜야 할 규칙</b><ul>{cs}</ul></div>' if cs else "")
        + "</section>")


def _example_block(ex: Example) -> str:
    from app.core.infra_planning import plan_from_deployment_intent

    from app.core.infra_planning import plan_for_anchors

    panels, extras = [], []
    for csp in CSPS:
        plan = plan_from_deployment_intent(ex.deployment_intent, csp, "-")
        if ex.given_anchors:
            merged = sorted(set(plan.intent.anchors) | set(ex.given_anchors))
            try:
                plan = plan_for_anchors(merged, csp, "-")
            except KeyError as e:
                # 재지 않은 자원은 계획을 내지 않는다 — 빈 패널로 그 사실을 보인다.
                panels.append(
                    f'<section class="panel none"><h3>{csp}</h3>'
                    f'<p class="stat">계획 없음</p>'
                    f'<p class="nonebody">요구된 자원을 이 클라우드에서 '
                    f'재지 않았습니다 — 추측 대신 비웁니다.<br><small>'
                    f'{html.escape(str(e)[:140])}</small></p></section>')
                continue
        panels.append(_panel(csp, plan.design, list(plan.questions)))
        if plan.unmeasured:
            extras.extend(plan.unmeasured)
        concrete = ex.concrete_plans.get(csp)
        if concrete:
            from app.core.infra_planning import plan_for_anchors

            checked = (
                plan_for_anchors(list(ex.check_anchors), csp, "-",
                                 concrete_plan=concrete)
                if ex.check_anchors else
                plan_from_deployment_intent(
                    ex.deployment_intent, csp, "-", concrete_plan=concrete))
            if checked.report and not checked.report.ok:
                for v in checked.report.violations:
                    extras.append(f"[{csp} 계획 검사] {v.detail} — 규칙: {v.rule}")
                for miss in checked.report.missing_required:
                    extras.append(f"[{csp} 계획 검사] 필수 자원이 계획에 없다: {miss}")

    hi = "".join(f"<li>{html.escape(h)}</li>" for h in ex.highlights)
    hard = "".join(f"<li>{html.escape(h)}</li>" for h in ex.hard_for)
    ex_extra = "".join(f"<li>{html.escape(x)}</li>" for x in dict.fromkeys(extras))
    return (
        f'<article class="example"><h2>{html.escape(ex.title)}</h2>'
        f'<p class="req">“{html.escape(ex.requirement)}”</p>'
        f'<div class="panels">{"".join(panels)}</div>'
        f'<div class="cols">'
        f'<div><b>이 예제가 보여주는 것</b><ul>{hi}</ul></div>'
        f'<div><b>문서만 읽어서는 갈리지 않는 자리</b><ul>{hard}</ul></div>'
        + (f'<div class="warn"><b>경고·검사 결과</b><ul>{ex_extra}</ul></div>'
           if ex_extra else "")
        + "</div></article>")


def render() -> str:
    blocks = "".join(_example_block(ex) for ex in EXAMPLES)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>배포 다이어그램 — 같은 요구, 세 클라우드</title>
<style>
  :root {{ color-scheme: light;
    --surface:#fcfcfb; --panel:#f4f4f2; --border:#d8d7d2; --ink:#0b0b0b;
    --ink2:#52514e; --anchor:#2a78d6; --req:#1baf7a; --opt:#8a8a86; --warn:#eb6834; }}
  @media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark;
    --surface:#1a1a19; --panel:#232322; --border:#3a3936; --ink:#fff;
    --ink2:#c3c2b7; --anchor:#3987e5; --req:#199e70; --opt:#9a9a94; --warn:#d95926; }} }}
  body {{ margin:24px; background:var(--surface); color:var(--ink);
         font:14px/1.55 system-ui, sans-serif; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  h2 {{ font-size:16px; margin:26px 0 2px; }}
  h3 {{ font-size:14px; margin:0 0 2px; }}
  .lead, .req, .stat {{ color:var(--ink2); }}
  .req {{ margin:2px 0 10px; font-style:italic; }}
  .stat {{ font-size:12px; margin:0 0 4px; }}
  .panels {{ display:flex; flex-wrap:wrap; gap:14px; }}
  .panel {{ background:var(--panel); border:1px solid var(--border);
            border-radius:10px; padding:10px 12px; width:404px; }}
  .panel svg {{ width:100%; height:auto; }}
  .panel.none {{ border-style:dashed; }}
  .nonebody {{ color:var(--ink2); font-size:12.5px; margin:6px 0 0; }}
  .node rect {{ fill:var(--surface); stroke:var(--opt); stroke-width:1.4; }}
  .node text {{ fill:var(--ink); font-size:12.5px; text-anchor:middle; }}
  .node.anchor rect {{ stroke:var(--anchor); stroke-width:3; }}
  .node.required rect {{ stroke:var(--req); stroke-width:2; }}
  .node.attachable rect {{ stroke-dasharray:6 4; }}
  .node.auto rect {{ stroke-dasharray:2 4; }}
  .node .badge {{ font-size:9px; fill:var(--ink2); text-anchor:end; }}
  .edge {{ fill:none; stroke:var(--ink2); stroke-width:1.6; opacity:.75; }}
  .edge:hover {{ stroke-width:3; opacity:1; }}
  .band {{ fill:var(--ink2); font-size:10px; }}
  #dep-arrow path {{ fill:var(--ink2); }}
  .ask ul, .rule ul {{ margin:3px 0 0; padding-left:18px; }}
  .ask, .rule {{ font-size:12px; margin-top:8px; color:var(--ink2); }}
  .cols {{ display:flex; flex-wrap:wrap; gap:18px; margin-top:10px; font-size:13px; }}
  .cols > div {{ flex:1; min-width:280px; }}
  .cols ul {{ margin:4px 0 0; padding-left:18px; color:var(--ink2); }}
  .warn b {{ color:var(--warn); }}
  .legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12.5px;
             color:var(--ink2); margin:10px 0 4px; }}
  article {{ border-top:1px solid var(--border); padding-top:6px; }}
</style></head><body>
<h1>배포 다이어그램 — 같은 요구, 세 클라우드</h1>
<p class="lead">요구사항 → 배포 의도(k8s) → <b>인프라 계획</b> → 이 그림.
판정은 세 클라우드 컨트롤 플레인에 직접 물어 얻은 58주장에서 왔다
(<code>depkb/claims.json</code>). 화살표 <b>A→B는 “A가 B를 요구한다”</b>이고,
노드에 마우스를 올리면 왜 그것이 거기 있는지 나온다.</p>
<div class="legend">
  <span>■ 진한 테두리 = 선택한 것(앵커)</span>
  <span>■ 실선 = 반드시 필요</span>
  <span>■ 파선 = 선택 사항</span>
  <span>■ 점선+자동 = 서버가 만든다(우리가 만들면 중복)</span>
</div>
<svg width="0" height="0"><defs>
  <marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
    markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>
</defs></svg>
{blocks}
</body></html>"""


if __name__ == "__main__":
    _OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {_OUT}")
