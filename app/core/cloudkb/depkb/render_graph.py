"""의존성 그래프 시각화 — claims.json의 사영을 자립형 HTML로 그린다.

읽는 법이 곧 논지다: **세 패널의 노드 배치는 같고 간선만 다르다** — 같은 어휘에
CSP가 다른 답을 얹는다는 것이 이 분석의 결과이므로, 배치를 고정해 차이만 보이게
한다.

인코딩(색은 검증기 통과 팔레트, 정체는 색+선형 이중):
- 필수(existence required) = 파랑 실선
- 선택(optional) = 청록 파선 · **서버 대체(auto)** = 청록 점선 + 속빈 원
- 조건부(conditional) = 주황 쇄선
- 삭제 제약(lifecycle holds, 실측 쌍) = 간선 중점의 자물쇠 표
간선에 닿는 주장의 판정·증거 층은 브라우저 기본 툴팁(<title>)으로 나온다.
표 뷰(41주장 전체)를 함께 싣는다 — 대비 경고의 구제이자 접근성 요건.

실행: `python -m app.core.cloudkb.depkb.render_graph` → `dependency-graph.html`
"""

from __future__ import annotations

import json
from pathlib import Path

from .closure import _classify

_HERE = Path(__file__).resolve().parent
_OUT = _HERE / "dependency-graph.html"

#: 고정 배치 — 세 패널 공통. 값은 노드 중심 좌표(우리 구성 — 가독 목적뿐).
POS: dict[str, tuple[int, int]] = {
    # 맨 윗줄은 k8s API 오브젝트 층 — 클라우드 자원이 아니라 합성의 주체다
    # (2026-07-31 합성 라운드). 세로로 합성 대상 위에 놓는다.
    "k8sPvc": (30, 25), "k8sService": (280, 25),
    "k8sCluster": (80, 95), "k8sNodeGroup": (215, 95), "vpn": (330, 95),
    "vm": (90, 165), "loadBalancer": (280, 165),
    "image": (30, 210),  # image 라운드(2026-07-31) — vm의 부팅 원천
    "disk": (30, 255), "nic": (120, 260), "publicIp": (230, 255),
    "sshKey": (330, 250),
    "subnet": (140, 355), "firewall": (290, 355),
    "network": (190, 445),
}
NODE_W, NODE_H = 78, 30
CSPS = ("aws", "azure", "gcp")

KO = {"required": "필수", "optional": "선택", "holds": "삭제 제약",
      "unknown": "미판정"}


def _edge_class(claim: dict) -> str:
    kind = _classify(claim.get("predicate"))
    if kind == "conditional":
        return "cond"
    if claim["verdict"] == "required":
        return "req"
    return "auto" if kind == "auto" else "opt"


def _anchor(a: tuple[int, int], b: tuple[int, int]) -> tuple[float, float]:
    """a 노드 사각형 가장자리에서 b 방향으로 나가는 점."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return float(ax), float(ay)
    sx = (NODE_W / 2) / abs(dx) if dx else 9e9
    sy = (NODE_H / 2) / abs(dy) if dy else 9e9
    s = min(sx, sy)
    return ax + dx * s, ay + dy * s


def _panel(csp: str, claims: list[dict]) -> str:
    rows = [c for c in claims if c["csp"] == csp]
    exist = [c for c in rows if c["question"] == "existence"
             and "|" not in c["object"]]
    holds = {(c["subject"], c["object"]): (c.get("predicate") or "")
             for c in rows
             if c["question"] == "lifecycle" and c["verdict"] == "holds"}
    pairs = {(c["subject"], c["object"]) for c in exist}
    touched = {n for p in pairs for n in p} | {n for p in holds for n in p}

    svg: list[str] = []
    for c in exist:
        s, o = c["subject"], c["object"]
        x1, y1 = _anchor(POS[s], POS[o])
        x2, y2 = _anchor(POS[o], POS[s])
        cls = _edge_class(c)
        # 제3 노드를 관통하면 비켜 굽힌다 (층 건너뛰는 간선이 그렇다)
        def _near_third() -> bool:
            for name, (px, py) in POS.items():
                if name in (s, o):
                    continue
                dx, dy = x2 - x1, y2 - y1
                L2 = dx * dx + dy * dy or 1
                t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
                qx, qy = x1 + t * dx, y1 + t * dy
                if abs(qx - px) < NODE_W / 2 + 6 and abs(qy - py) < NODE_H / 2 + 6:
                    return True
            return False

        # 역방향 간선이 함께 있으면 서로 비켜 굽힌다
        if (o, s) in pairs or _near_third():
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            nx, ny = -(y2 - y1), (x2 - x1)
            norm = (nx * nx + ny * ny) ** 0.5 or 1
            mx += 16 * nx / norm
            my += 16 * ny / norm
            d = f"M{x1:.0f},{y1:.0f} Q{mx:.0f},{my:.0f} {x2:.0f},{y2:.0f}"
        else:
            d = f"M{x1:.0f},{y1:.0f} L{x2:.0f},{y2:.0f}"
        tip = (f"{s}→{o} [{KO[c['verdict']]}] ({c['oracle']} 층)"
               + (f" — {c['predicate']}" if c.get("predicate") else ""))
        svg.append(
            f'<path class="edge {cls}" d="{d}" '
            f'marker-end="url(#mk-{cls})"><title>{tip}</title></path>')
        if cls == "auto":
            svg.append(f'<circle class="autodot" cx="{x1:.0f}" cy="{y1:.0f}" r="4"/>')
        if (s, o) in holds:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            if holds[(s, o)].startswith("동반 정리:"):
                # 삭제 보호의 반대 방향 — 주체 삭제가 합성물을 함께 지운다
                svg.append(
                    f'<text class="lock" x="{mx:.0f}" y="{my + 4:.0f}">♻'
                    f'<title>{s} 삭제가 {o}를 함께 지운다(동반 정리) — 실측'
                    f'</title></text>')
            else:
                svg.append(
                    f'<text class="lock" x="{mx:.0f}" y="{my + 4:.0f}">🔒'
                    f'<title>{s}가 쓰는 동안 {o} 삭제 거부 — 실측</title></text>')

    for name, (x, y) in POS.items():
        ghost = ' ghost' if name not in touched else ""
        svg.append(
            f'<g class="node{ghost}">'
            f'<rect x="{x - NODE_W // 2}" y="{y - NODE_H // 2}" '
            f'width="{NODE_W}" height="{NODE_H}" rx="7"/>'
            f'<text x="{x}" y="{y + 4}">{name}</text></g>')
    if csp == "gcp":
        x, y = POS["sshKey"]
        svg.append(f'<text class="note" x="{x}" y="{y + 30}">자원 없음</text>')
    if csp == "azure":
        x, y = POS["loadBalancer"]
        svg.append(f'<text class="note" x="{x}" y="{y - 24}">'
                   f'frontend: subnet ∨ publicIp 중 1 필수</text>')

    n_req = sum(1 for c in exist if c["verdict"] == "required")
    head = (f'<h2>{csp}</h2><p class="sub">필수 {n_req} · '
            f'선택 {len(exist) - n_req} · 삭제 제약 {len(holds)}</p>')
    return (f'<section class="panel">{head}'
            f'<svg viewBox="0 0 380 470" role="img" '
            f'aria-label="{csp} 의존성 그래프">{"".join(svg)}</svg></section>')


def _table(claims: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{c['csp']}</td><td>{c['subject']} → {c['object']}</td>"
        f"<td>{c['question']}</td><td>{KO[c['verdict']]}</td>"
        f"<td>{c['oracle']}</td><td>{c.get('predicate') or ''}</td></tr>"
        for c in claims)
    return ("<details><summary>표로 보기 — 41주장 전체</summary>"
            "<table><thead><tr><th>CSP</th><th>간선</th><th>질문</th>"
            "<th>판정</th><th>층</th><th>술어</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></details>")


def render() -> str:
    doc = json.loads((_HERE / "claims.json").read_text(encoding="utf-8"))
    claims = doc["claims"]
    panels = "".join(_panel(csp, claims) for csp in CSPS)
    legend = (
        '<div class="legend">'
        '<span><svg width="34" height="10"><line class="edge req" x1="0" y1="5" x2="34" y2="5"/></svg> 필수</span>'
        '<span><svg width="34" height="10"><line class="edge opt" x1="0" y1="5" x2="34" y2="5"/></svg> 선택</span>'
        '<span><svg width="34" height="10"><line class="edge auto" x1="0" y1="5" x2="34" y2="5"/>'
        '<circle class="autodot" cx="4" cy="5" r="3"/></svg> 선택 — 생략 시 서버가 채움</span>'
        '<span><svg width="34" height="10"><line class="edge cond" x1="0" y1="5" x2="34" y2="5"/></svg> 조건부</span>'
        '<span>🔒 삭제 제약(실측)</span></div>')
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>클라우드 리소스 의존성 — 3사 실측 (depkb claims)</title>
<style>
  :root {{ color-scheme: light;
    --surface: #fcfcfb; --panel: #f4f4f2; --border: #d8d7d2;
    --ink: #0b0b0b; --ink2: #52514e;
    --c-req: #2a78d6; --c-opt: #1baf7a; --c-cond: #eb6834; }}
  @media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark;
    --surface: #1a1a19; --panel: #232322; --border: #3a3936;
    --ink: #ffffff; --ink2: #c3c2b7;
    --c-req: #3987e5; --c-opt: #199e70; --c-cond: #d95926; }} }}
  body {{ margin: 24px; background: var(--surface); color: var(--ink);
         font: 14px/1.5 system-ui, sans-serif; }}
  h1 {{ font-size: 19px; margin: 0 0 2px; }}
  .sub, .meta {{ color: var(--ink2); font-size: 12.5px; margin: 2px 0 8px; }}
  .panels {{ display: flex; flex-wrap: wrap; gap: 18px; margin-top: 14px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border);
            border-radius: 10px; padding: 10px 14px; }}
  .panel h2 {{ font-size: 15px; margin: 2px 0 0; }}
  .panel svg {{ width: 380px; height: 400px; }}
  .node rect {{ fill: var(--surface); stroke: var(--border); stroke-width: 1.2; }}
  .node text {{ fill: var(--ink); font-size: 12.5px; text-anchor: middle; }}
  .node.ghost {{ opacity: .34; }}
  .edge {{ fill: none; stroke-width: 2; }}
  .edge.req  {{ stroke: var(--c-req); stroke-width: 2.6; }}
  .edge.opt  {{ stroke: var(--c-opt); stroke-dasharray: 7 4; }}
  .edge.auto {{ stroke: var(--c-opt); stroke-dasharray: 2 4; }}
  .edge.cond {{ stroke: var(--c-cond); stroke-dasharray: 9 3 2 3; }}
  path.edge:hover {{ stroke-width: 4.4; }}
  .autodot {{ fill: var(--panel); stroke: var(--c-opt); stroke-width: 1.6; }}
  .lock {{ font-size: 11px; text-anchor: middle; }}
  .note {{ fill: var(--ink2); font-size: 10.5px; text-anchor: middle; }}
  #mk-req path {{ fill: var(--c-req); }}
  #mk-opt path, #mk-auto path {{ fill: var(--c-opt); }}
  #mk-cond path {{ fill: var(--c-cond); }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
             margin-top: 10px; color: var(--ink2); font-size: 12.5px; }}
  .legend svg {{ vertical-align: middle; }}
  details {{ margin-top: 16px; }}
  summary {{ cursor: pointer; color: var(--ink2); }}
  table {{ border-collapse: collapse; margin-top: 8px; font-size: 12.5px; }}
  th, td {{ border: 1px solid var(--border); padding: 3px 9px; text-align: left; }}
  th {{ color: var(--ink2); font-weight: 600; }}
</style></head><body>
<h1>클라우드 리소스 의존성 — 3사 실측</h1>
<p class="meta">근거: depkb/claims.json ({doc['verdictCounts']}) — 화살표 A→B는
"A가 B를 참조/요구", 판정은 컨트롤 플레인 실험(preflight·apply)의 결과다.
세 패널의 배치는 같고 간선만 다르다 — 그 차이가 측정 결과다. 간선에 마우스를
올리면 판정·층·술어가 나온다.</p>
<svg width="0" height="0"><defs>
  <marker id="mk-req" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
    markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>
  <marker id="mk-opt" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
    markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>
  <marker id="mk-auto" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
    markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>
  <marker id="mk-cond" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
    markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>
</defs></svg>
{legend}
<div class="panels">{panels}</div>
{_table(claims)}
</body></html>"""


if __name__ == "__main__":
    _OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {_OUT}")
