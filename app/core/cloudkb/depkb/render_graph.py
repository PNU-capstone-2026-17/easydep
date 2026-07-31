"""의존성 그래프 뷰어 — claims.json을 인터랙티브 HTML로 사영한다.

**소비 전용 뷰다** — 판정·증거는 claims.json이 진실이고, 여기서는 그것을
읽기 좋게 그릴 뿐이다. 배치(층)는 우리 구성(가독 목적)이고 판정에 영향이 없다.

- 시각화는 cytoscape.js(CDN)로 그린다 — **보는 데 인터넷이 필요하다.**
  오프라인이면 안내 문구가 뜬다(데이터 자체는 HTML에 내장돼 유실은 없다).
- **배치는 생성 시점에 여기서 한 번 계산하고 세 탭이 공유한다** — CSP마다
  배치가 다르면 비교가 안 된다(사용자 지적). 층은 LAYERS(우리 구성), 층 안
  순서는 3사 **합집합** 간선의 무게중심(barycenter) 반복으로 교차를 줄인다.
- **층을 건너뛰는 간선은 바깥으로 호를 그린다**(랭크 폭에 비례한 곡률을
  간선마다 미리 계산) — 직선이면 중간 층 노드를 관통한다(사용자 지적).
- CSP 탭 · 판정별 간선 필터 · 생명주기 배지(🔒 삭제 보호 / ♻ 동반 정리) ·
  노드 드래그 · 클릭 상세(술어·note·증거의 실험/스텝 좌표).
- 선언 술어(`a|b|c` 합집합 간선)는 선으로 그리면 겹침만 늘어 **주체 노드
  상세에 싣는다.**

실행: `python -m app.core.cloudkb.depkb.render_graph` → `dependency-graph.html`
"""

from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_OUT = _HERE / "dependency-graph.html"

#: 층 배치 — 위가 k8s API 오브젝트, 아래로 갈수록 기반 자원. **우리 구성.**
LAYERS: list[list[str]] = [
    ["k8sPvc", "k8sService", "k8sIngress"],
    ["k8sCluster", "k8sNodeGroup", "vpn"],
    ["vm", "loadBalancer"],
    ["image", "disk", "nic", "publicIp", "sshKey"],
    ["subnet", "firewall"],
    ["network"],
]
X_GAP, Y_GAP = 210, 170

CSPS = ("aws", "azure", "gcp")

RANK: dict[str, int] = {n: i for i, row in enumerate(LAYERS) for n in row}


def _positions(union_pairs: set[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """합집합 간선으로 층 안 순서를 정한다(barycenter 반복) — 세 탭 공통 배치.

    이웃의 평균 x로 정렬을 몇 번 반복하면 이 크기(노드 16)에서는 교차가
    수렴한다. 결정적이도록 동률은 이름순.
    """
    order: dict[int, list[str]] = {i: list(row) for i, row in enumerate(LAYERS)}
    neigh: dict[str, list[str]] = {}
    for s, o in union_pairs:
        neigh.setdefault(s, []).append(o)
        neigh.setdefault(o, []).append(s)

    def xs() -> dict[str, float]:
        out: dict[str, float] = {}
        for _, row in order.items():
            for idx, n in enumerate(row):
                out[n] = (idx - (len(row) - 1) / 2) * X_GAP
        return out

    for _ in range(8):
        cur = xs()
        for li, row in order.items():
            def key(n: str) -> tuple[float, str]:
                ns = neigh.get(n, [])
                mean = (sum(cur[m] for m in ns) / len(ns)) if ns else cur[n]
                return (mean, n)
            order[li] = sorted(row, key=key)

    final = xs()
    width = max(len(r) for r in LAYERS) * X_GAP
    return {n: (int(final[n] + width / 2), RANK[n] * Y_GAP) for n in final}


def _edge_class(claim: dict) -> str:
    pred = claim.get("predicate") or ""
    if pred.split(":")[0].endswith(("조건부", "조건")) and not pred.startswith(
            ("이름 조건", "배치 조건", "수명 조건")):
        return "cond"
    if claim["verdict"] == "required":
        return "req"
    if pred.startswith(("server-default:", "server-implicit:")):
        return "auto"
    return "opt"


def _build_data() -> dict:
    doc = json.loads((_HERE / "claims.json").read_text(encoding="utf-8"))
    union = {(c["subject"], c["object"]) for c in doc["claims"]
             if c["question"] == "existence" and "|" not in c["object"]}
    pos = _positions(union)
    center_x = sum(x for x, _ in pos.values()) / len(pos)

    def curvature(s: str, o: str) -> int:
        """직선이 노드를 관통할 간선에 호를 준다.

        층을 건너뛰면(폭≥2) 중간 층을, 같은 층이면 사이 노드를 지나므로
        둘 다 호로 비킨다. 곡률은 폭에 비례, 방향은 바깥쪽.
        """
        span = abs(RANK[o] - RANK[s])
        if span == 0:
            return 70  # 같은 층 — 행 위로 비키는 호
        if span < 2:
            return 0
        mid = (pos[s][0] + pos[o][0]) / 2
        direction = 1 if mid >= center_x else -1
        return direction * (60 + 50 * (span - 1))

    out: dict[str, dict] = {"positions": {k: {"x": x, "y": y}
                                          for k, (x, y) in pos.items()},
                            "csps": {}}
    for csp in CSPS:
        rows = [c for c in doc["claims"] if c["csp"] == csp]
        exist = [c for c in rows if c["question"] == "existence"]
        life = {(c["subject"], c["object"]): c for c in rows
                if c["question"] == "lifecycle"}
        edges, disjunctions = [], []
        touched: set[str] = set()
        for c in exist:
            if "|" in c["object"]:
                disjunctions.append(c)  # 노드 상세로 — 선으로 그리면 겹친다
                touched.add(c["subject"])
                continue
            lc = life.get((c["subject"], c["object"]))
            edges.append({
                "s": c["subject"], "o": c["object"],
                "cpd": curvature(c["subject"], c["object"]),
                "cls": _edge_class(c), "verdict": c["verdict"],
                "predicate": c.get("predicate"), "note": c.get("note"),
                "oracle": c["oracle"],
                "evidence": [e for e in c["evidence"] if e["layer"] != "schema"],
                "lifecycle": None if lc is None else {
                    "verdict": lc["verdict"],
                    "cascade": (lc.get("predicate") or "").startswith("동반 정리:"),
                    "predicate": lc.get("predicate"), "note": lc.get("note"),
                    "evidence": [e for e in lc["evidence"]
                                 if e["layer"] != "schema"],
                },
            })
            touched.update((c["subject"], c["object"]))
        nodes = [{"id": n, "ghost": n not in touched,
                  "disjunctions": [
                      {"object": d["object"], "verdict": d["verdict"],
                       "predicate": d.get("predicate"), "note": d.get("note")}
                      for d in disjunctions if d["subject"] == n]}
                 for n in pos]
        out["csps"][csp] = {"nodes": nodes, "edges": edges}
    return out


_PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>depkb 의존성 그래프 — 3사 실측</title>
<script src="https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js"></script>
<style>
  :root { --ink:#1c1e21; --sub:#5b6572; --line:#d7dce3; --bg:#f6f7f9;
          --req:#0a7d52; --opt:#8a94a2; --auto:#2563c4; --cond:#c26a12; }
  * { box-sizing:border-box }
  body { margin:0; font:14px/1.5 "Segoe UI",system-ui,sans-serif;
         color:var(--ink); background:var(--bg); height:100vh;
         display:flex; flex-direction:column }
  header { padding:10px 16px; background:#fff; border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:16px; flex-wrap:wrap }
  h1 { font-size:15px; margin:0 12px 0 0 }
  .tabs button { border:1px solid var(--line); background:#fff; padding:5px 14px;
                 cursor:pointer; font:inherit }
  .tabs button:first-child { border-radius:6px 0 0 6px }
  .tabs button:last-child { border-radius:0 6px 6px 0 }
  .tabs button.on { background:var(--ink); color:#fff; border-color:var(--ink) }
  label.f { display:inline-flex; align-items:center; gap:4px; color:var(--sub);
            cursor:pointer; user-select:none }
  .sw { display:inline-block; width:22px; height:0; border-top:3px solid }
  .sw.req { border-color:var(--req) }
  .sw.opt { border-top-style:dashed; border-color:var(--opt) }
  .sw.auto { border-top-style:dotted; border-color:var(--auto) }
  .sw.cond { border-top-style:dashed; border-color:var(--cond) }
  #reset { margin-left:auto; border:1px solid var(--line); background:#fff;
           padding:5px 12px; border-radius:6px; cursor:pointer; font:inherit }
  main { flex:1; display:flex; min-height:0 }
  #cy { flex:1; background:#fff }
  aside { width:340px; border-left:1px solid var(--line); background:#fff;
          padding:14px 16px; overflow-y:auto }
  aside h2 { font-size:14px; margin:0 0 6px }
  aside .empty { color:var(--sub) }
  .chip { display:inline-block; padding:1px 8px; border-radius:10px;
          font-size:12px; margin:0 4px 4px 0; border:1px solid var(--line) }
  .chip.req { color:var(--req); border-color:var(--req) }
  .chip.opt { color:var(--sub) }
  .chip.auto { color:var(--auto); border-color:var(--auto) }
  .chip.cond { color:var(--cond); border-color:var(--cond) }
  .kv { margin:8px 0; padding:8px 10px; background:var(--bg); border-radius:8px;
        font-size:13px; overflow-wrap:anywhere }
  .kv b { display:block; font-size:12px; color:var(--sub); margin-bottom:2px }
  .ev { font-family:Consolas,monospace; font-size:12px; color:var(--sub) }
  footer { padding:6px 16px; color:var(--sub); font-size:12px; background:#fff;
           border-top:1px solid var(--line) }
  #offline { display:none; padding:40px; color:var(--sub) }
</style></head><body>
<header>
  <h1>depkb 의존성 그래프</h1>
  <span class="tabs" id="tabs"></span>
  <label class="f"><input type="checkbox" data-cls="req" checked><span class="sw req"></span>필수</label>
  <label class="f"><input type="checkbox" data-cls="opt" checked><span class="sw opt"></span>선택</label>
  <label class="f"><input type="checkbox" data-cls="auto" checked><span class="sw auto"></span>서버가 채움</label>
  <label class="f"><input type="checkbox" data-cls="cond" checked><span class="sw cond"></span>조건부</label>
  <label class="f"><input type="checkbox" id="lifeToggle" checked>생명주기 배지 🔒/♻</label>
  <label class="f"><input type="checkbox" id="ghostToggle">간선 없는 자원 표시</label>
  <button id="reset">배치 초기화</button>
</header>
<main>
  <div id="cy"></div>
  <div id="offline">cytoscape.js(CDN)를 불러오지 못했습니다 — 보려면 인터넷이
    필요합니다. 데이터는 이 파일에 내장돼 있습니다(&lt;script id="data"&gt;).</div>
  <aside id="panel"><h2>상세</h2><div class="empty">노드나 간선을 클릭하세요.
    화살표 A→B는 “A가 B를 요구/합성한다”입니다(포함 아님).<br><br>
    🔒 = 삭제 보호(쓰는 동안 대상 삭제 거부) · ♻ = 동반 정리(주체 삭제가
    합성물을 함께 지움 — 직접 만들지도 지우지도 말 것)</div></aside>
</main>
<footer>판정·증거의 진실은 claims.json — 이 페이지는 사영이다. 배치는 가독
목적의 우리 구성. 노드는 드래그로 옮길 수 있다.</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
if (typeof cytoscape === 'undefined') {
  document.getElementById('cy').style.display='none';
  document.getElementById('offline').style.display='block';
} else {
  const COLORS = {req:'#0a7d52', opt:'#8a94a2', auto:'#2563c4', cond:'#c26a12'};
  let cy = null, current = 'azure';

  function elements(csp) {
    const d = DATA.csps[csp], els = [];
    // 좌표는 3사 공통(합집합 배치) — 탭을 바꿔도 노드가 제자리에 있어야
    // 비교가 된다.
    for (const n of d.nodes) els.push({group:'nodes',
      data:{id:n.id, ghost:n.ghost?1:0, disj:n.disjunctions},
      position:{...DATA.positions[n.id]}});
    d.edges.forEach((e,i) => els.push({group:'edges',
      data:{id:'e'+i, source:e.s, target:e.o, cls:e.cls, cpd:e.cpd||0,
            badge: e.lifecycle ? (e.lifecycle.cascade?'♻':'🔒') : '', ...e}}));
    return els;
  }

  function style() { return [
    {selector:'node', style:{
      shape:'round-rectangle', width:96, height:34, 'background-color':'#fff',
      'border-width':1.5, 'border-color':'#9aa4b1', label:'data(id)',
      'font-size':13, 'text-valign':'center', 'text-halign':'center',
      color:'#1c1e21'}},
    {selector:'node[ghost=1]', style:{opacity:0.35, 'border-style':'dashed'}},
    {selector:'node:selected', style:{'border-color':'#111', 'border-width':3}},
    {selector:'edge', style:{
      'curve-style':'bezier', 'control-point-step-size':55,
      'target-arrow-shape':'triangle', 'arrow-scale':1.1,
      width:2, 'font-size':13, 'text-rotation':'none',
      label:'data(badge)', 'text-background-color':'#fff',
      'text-background-opacity':0.9, 'text-background-padding':2}},
    ...Object.entries(COLORS).map(([k,c]) => ({selector:`edge[cls="${k}"]`,
      style:{'line-color':c, 'target-arrow-color':c,
             width:k==='req'?3:2,
             'line-style':k==='opt'?'dashed':k==='auto'?'dotted':
                          k==='cond'?'dashed':'solid'}})),
    // 층을 건너뛰는 간선 — 미리 계산된 곡률로 바깥쪽 호(중간 노드 관통 방지)
    {selector:'edge[cpd != 0]', style:{
      'curve-style':'unbundled-bezier',
      'control-point-distances':'data(cpd)',
      'control-point-weights':0.5}},
    {selector:'edge:selected', style:{width:4}},
    {selector:'.dim', style:{opacity:0.12}},
    {selector:'.hideCls', style:{display:'none'}},
    {selector:'.hideGhost', style:{display:'none'}},
    {selector:'.noBadge', style:{label:''}},
  ]; }

  function render(csp) {
    current = csp;
    document.querySelectorAll('#tabs button').forEach(b =>
      b.classList.toggle('on', b.textContent===csp));
    if (cy) cy.destroy();
    cy = cytoscape({container:document.getElementById('cy'),
      elements:elements(csp), style:style(), wheelSensitivity:0.25});
    applyFilters();
    cy.fit(cy.elements(':visible'), 40);
    cy.on('tap', 'node,edge', ev => showDetail(ev.target));
    cy.on('tap', ev => { if (ev.target===cy) clearDetail(); });
    // 이웃 강조
    cy.on('select', 'node', ev => {
      const n = ev.target, keep = n.closedNeighborhood();
      cy.elements().not(keep).addClass('dim');
    });
    cy.on('unselect', 'node', () => cy.elements().removeClass('dim'));
  }

  function applyFilters() {
    document.querySelectorAll('input[data-cls]').forEach(cb => {
      cy.edges(`[cls="${cb.dataset.cls}"]`)
        .toggleClass('hideCls', !cb.checked);
    });
    cy.edges().toggleClass('noBadge',
      !document.getElementById('lifeToggle').checked);
    cy.nodes('[ghost=1]').toggleClass('hideGhost',
      !document.getElementById('ghostToggle').checked);
  }

  const esc = s => String(s??'').replace(/[&<>]/g,
      m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
  function kv(title, body) {
    return body ? `<div class="kv"><b>${esc(title)}</b>${body}</div>` : '';
  }
  function evList(evidence) {
    return (evidence||[]).map(e =>
      `<div class="ev">${esc(e.experiment)} / ${esc(e.step)} → ${esc(e.code)}</div>`
    ).join('');
  }

  function showDetail(t) {
    const p = document.getElementById('panel');
    if (t.isEdge()) {
      const d = t.data(), lc = d.lifecycle;
      p.innerHTML = `<h2>${esc(d.s)} → ${esc(d.o)}</h2>
        <span class="chip ${d.cls}">${{req:'필수',opt:'선택',auto:'서버가 채움',cond:'조건부'}[d.cls]}</span>
        <span class="chip">${esc(d.oracle)} 층</span>
        ${kv('술어', esc(d.predicate))}
        ${kv('노트', esc(d.note))}
        ${kv('동적 증거', evList(d.evidence))}
        ${lc ? kv('생명주기 ' + (lc.cascade?'♻ 동반 정리':'🔒 삭제 보호'),
                  esc(lc.predicate||'') + (lc.note?`<br>${esc(lc.note)}`:'')
                  + evList(lc.evidence)) : ''}`;
    } else {
      const d = t.data();
      const disj = (d.disj||[]).map(x =>
        kv(`선언 술어 → ${esc(x.object)} [${esc(x.verdict)}]`,
           esc(x.predicate||'') + (x.note?`<br>${esc(x.note)}`:''))).join('');
      const deg = t.connectedEdges(':visible').length;
      p.innerHTML = `<h2>${esc(d.id)}</h2>
        ${d.ghost?'<span class="chip">이 CSP엔 간선 없음</span>':''}
        <span class="chip">${current}</span><span class="chip">간선 ${deg}</span>
        ${disj || ''}`;
    }
  }
  function clearDetail() {
    document.getElementById('panel').innerHTML =
      '<h2>상세</h2><div class="empty">노드나 간선을 클릭하세요.</div>';
  }

  const tabs = document.getElementById('tabs');
  for (const csp of ['aws','azure','gcp']) {
    const b = document.createElement('button');
    b.textContent = csp; b.onclick = () => render(csp);
    tabs.appendChild(b);
  }
  document.querySelectorAll('input[data-cls], #lifeToggle, #ghostToggle')
    .forEach(cb => cb.addEventListener('change', applyFilters));
  document.getElementById('reset').onclick = () => render(current);
  render('azure');
}
</script></body></html>
"""


def main() -> None:
    data = _build_data()
    html = _PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    _OUT.write_text(html, encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
