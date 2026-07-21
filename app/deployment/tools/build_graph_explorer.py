"""의존성 그래프 탐색기(단일 HTML)를 만든다.

`output/*-graph.json`을 읽어 자기완결 HTML 한 장으로 굽는다. 외부 스크립트·폰트를
쓰지 않는다 — 아티팩트로 올리면 CSP가 외부 요청을 전부 막기 때문이기도 하고,
파일 하나만 열면 되는 편이 검수에 편해서이기도 하다.

    python tools/build_graph_explorer.py

시각 인코딩의 원칙: **한 변수에 한 채널.**
  - 색상 = 프로바이더 (aws/azure/gcp/core)
  - 선 모양 = basis (실선 = 원본이 명시 / 파선 = 우리 짐작)
둘을 같은 채널에 겹치면 "짐작인 aws 엣지"를 눈으로 못 가른다.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

OUTPUT = Path("output")
TARGET = OUTPUT / "graph-explorer.html"


def load_graph() -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    for path in sorted(OUTPUT.glob("*-graph.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes.extend(data.get("nodes", []))
        edges.extend(data.get("edges", []))
    return nodes, edges


def compact(nodes: list[dict], edges: list[dict]) -> dict:
    """id 문자열이 엣지마다 반복되지 않게 색인으로 접는다."""
    providers = sorted({n["provider"] for n in nodes})
    sources = sorted({n.get("source") or "" for n in nodes})
    etypes = sorted({e["type"] for e in edges})
    evidences = sorted({e.get("evidence") or "" for e in edges})
    cards = sorted({e.get("cardinality") or "" for e in edges})

    index = {n["id"]: i for i, n in enumerate(nodes)}
    node_rows = [
        [n["id"], providers.index(n["provider"]), sources.index(n.get("source") or "")]
        for n in nodes
    ]
    edge_rows = []
    for e in edges:
        a, b = index.get(e["from"]), index.get(e["to"])
        if a is None or b is None:
            continue  # 산출물이 서로 안 맞으면 조용히 버리지 말고 아래에서 센다
        edge_rows.append([
            a, b,
            etypes.index(e["type"]),
            e.get("via_property") or "",
            1 if e.get("required") else 0,
            cards.index(e.get("cardinality") or ""),
            evidences.index(e.get("evidence") or ""),
            1 if e.get("basis") == "stated" else 0,
        ])
    return {
        "providers": providers,
        "sources": sources,
        "etypes": etypes,
        "evidences": evidences,
        "cards": cards,
        "nodes": node_rows,
        "edges": edge_rows,
        "dropped": len(edges) - len(edge_rows),
    }


def facts(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """탐색기 안에 같이 띄울 '이 데이터의 성질' — 보다가 오해할 만한 것들."""
    deg: Counter[str] = Counter()
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    by_provider: dict[str, Counter] = defaultdict(Counter)
    isolated: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for n in nodes:
        total[n["provider"]] += 1
        if deg[n["id"]] == 0:
            isolated[n["provider"]] += 1
    for e in edges:
        prov = e["from"].split("::", 1)[0]
        by_provider[prov][e["type"]] += 1

    rows = []
    for prov in sorted(total, key=lambda p: -total[p]):
        rows.append({
            "provider": prov,
            "types": total[prov],
            "isolated": isolated[prov],
            "references": by_provider[prov].get("references", 0),
            "contained_in": by_provider[prov].get("contained_in", 0),
        })
    return rows


def main() -> None:
    nodes, edges = load_graph()
    payload = compact(nodes, edges)
    payload["facts"] = facts(nodes, edges)
    payload["basis"] = {
        "stated": sum(1 for e in edges if e.get("basis") == "stated"),
        "inferred": sum(1 for e in edges if e.get("basis") != "stated"),
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = TEMPLATE.replace("__DATA__", blob)
    TARGET.write_text(html, encoding="utf-8")
    size = TARGET.stat().st_size / 1024
    print(f"{TARGET} ({size:,.0f} KB) — 노드 {len(nodes):,} 엣지 {len(edges):,}")
    if payload["dropped"]:
        print(f"  ⚠ 양쪽 노드를 못 찾은 엣지 {payload['dropped']}건은 제외했습니다.")


TEMPLATE = r"""<title>의존성 그래프 탐색기</title>
<style>
:root{
  --ground:#f4f6f7; --panel:#ffffff; --panel-2:#fafbfb;
  --line:#dbe2e6; --line-soft:#e9eef0;
  --ink:#161c20; --ink-2:#4a5a64; --ink-3:#7d8f9a;
  --accent:#0e7c7b; --accent-soft:#d8ecec;
  --aws:#b3701f; --azure:#3577c0; --gcp:#2f8a63; --core:#7a6cb5;
  --warn:#a8571f;
  --r:7px;
  --mono:ui-monospace,"SFMono-Regular","Cascadia Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --sans:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,-apple-system,"Segoe UI",sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{
    --ground:#0d1114; --panel:#141a1e; --panel-2:#10161a;
    --line:#263138; --line-soft:#1c252b;
    --ink:#e0e7ea; --ink-2:#9aabb4; --ink-3:#6c7d87;
    --accent:#3fbfb4; --accent-soft:#14312f;
    --aws:#d99a4a; --azure:#5fa3e8; --gcp:#52b489; --core:#a294dd;
    --warn:#d2884a;
  }
}
:root[data-theme="dark"]{
  --ground:#0d1114; --panel:#141a1e; --panel-2:#10161a;
  --line:#263138; --line-soft:#1c252b;
  --ink:#e0e7ea; --ink-2:#9aabb4; --ink-3:#6c7d87;
  --accent:#3fbfb4; --accent-soft:#14312f;
  --aws:#d99a4a; --azure:#5fa3e8; --gcp:#52b489; --core:#a294dd;
  --warn:#d2884a;
}
:root[data-theme="light"]{
  --ground:#f4f6f7; --panel:#ffffff; --panel-2:#fafbfb;
  --line:#dbe2e6; --line-soft:#e9eef0;
  --ink:#161c20; --ink-2:#4a5a64; --ink-3:#7d8f9a;
  --accent:#0e7c7b; --accent-soft:#d8ecec;
  --aws:#b3701f; --azure:#3577c0; --gcp:#2f8a63; --core:#7a6cb5;
  --warn:#a8571f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:14px;line-height:1.55;-webkit-font-smoothing:antialiased}
h1,h2,h3{margin:0;text-wrap:balance}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}

header{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 22px;
  padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-size:15px;font-weight:650;letter-spacing:-.01em}
.sub{color:var(--ink-3);font-size:12px}
.stats{display:flex;gap:18px;margin-left:auto;flex-wrap:wrap}
.stat{display:flex;align-items:baseline;gap:6px}
.stat b{font-family:var(--mono);font-size:14px;font-variant-numeric:tabular-nums;font-weight:600}
.stat span{color:var(--ink-3);font-size:11px;text-transform:uppercase;letter-spacing:.07em}

.app{display:grid;grid-template-columns:270px minmax(0,1fr) 340px;
  height:calc(100dvh - 53px);min-height:520px}
@media (max-width:1100px){
  .app{grid-template-columns:1fr;height:auto}
  #stage{height:62vh}
}
aside{background:var(--panel);border-right:1px solid var(--line);
  overflow-y:auto;padding:16px 16px 28px;display:flex;flex-direction:column;gap:18px}
aside.right{border-right:0;border-left:1px solid var(--line)}
.lbl{font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;
  color:var(--ink-3);font-weight:650;margin-bottom:7px}

input[type=search]{width:100%;padding:8px 10px;border:1px solid var(--line);
  border-radius:var(--r);background:var(--panel-2);color:var(--ink);
  font-family:var(--mono);font-size:12.5px}
input[type=search]::placeholder{color:var(--ink-3);font-family:var(--sans)}

.results{list-style:none;margin:8px 0 0;padding:0;max-height:230px;overflow-y:auto;
  border:1px solid var(--line-soft);border-radius:var(--r)}
.results:empty{display:none}
.results li{border-bottom:1px solid var(--line-soft)}
.results li:last-child{border-bottom:0}
.results button{width:100%;text-align:left;background:none;border:0;cursor:pointer;
  padding:7px 9px;font-family:var(--mono);font-size:11.5px;color:var(--ink-2);
  display:flex;align-items:center;gap:7px;overflow-wrap:anywhere}
.results button:hover{background:var(--accent-soft);color:var(--ink)}
.dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto}

.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{border:1px solid var(--line);background:var(--panel-2);border-radius:99px;
  padding:4px 10px;font-size:11.5px;cursor:pointer;color:var(--ink-2);
  display:flex;align-items:center;gap:6px;transition:background .12s,border-color .12s}
.chip[aria-pressed="true"]{border-color:var(--accent);background:var(--accent-soft);color:var(--ink)}
.chip:hover{border-color:var(--accent)}

.depth{display:flex;gap:6px}
.depth button{flex:1;border:1px solid var(--line);background:var(--panel-2);
  border-radius:var(--r);padding:6px;cursor:pointer;font-family:var(--mono);
  font-size:12px;color:var(--ink-2)}
.depth button[aria-pressed="true"]{border-color:var(--accent);background:var(--accent-soft);color:var(--ink)}

.legend{display:flex;flex-direction:column;gap:7px;font-size:11.5px;color:var(--ink-2)}
.legend div{display:flex;align-items:center;gap:8px}
.legend svg{flex:0 0 auto}

#stage{position:relative;background:
  radial-gradient(circle at 50% 42%, var(--panel-2) 0%, var(--ground) 78%)}
canvas{display:block;width:100%;height:100%;cursor:grab}
canvas:active{cursor:grabbing}
.hint{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  text-align:center;color:var(--ink-3);font-size:13px;max-width:380px;pointer-events:none;
  line-height:1.7}
.hint b{color:var(--ink-2);font-weight:600}
#tip{position:absolute;pointer-events:none;background:var(--panel);
  border:1px solid var(--line);border-radius:var(--r);padding:7px 9px;font-size:11.5px;
  font-family:var(--mono);max-width:330px;opacity:0;transition:opacity .1s;
  box-shadow:0 5px 18px rgb(0 0 0 /.16);z-index:5;overflow-wrap:anywhere}

.title-id{font-family:var(--mono);font-size:13px;overflow-wrap:anywhere;line-height:1.45}
.meta{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}
.tag{font-size:10.5px;border:1px solid var(--line);border-radius:4px;padding:2px 6px;
  color:var(--ink-2);font-family:var(--mono)}
.tag.warn{border-color:var(--warn);color:var(--warn)}

.edges{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:1px}
.edges li button{width:100%;text-align:left;background:var(--panel-2);border:0;
  border-left:2px solid transparent;cursor:pointer;padding:8px 10px;display:block}
.edges li button:hover{background:var(--accent-soft)}
.edges .row1{font-family:var(--mono);font-size:11.5px;color:var(--ink);
  overflow-wrap:anywhere;display:flex;gap:6px;align-items:baseline}
.edges .row2{font-size:10.5px;color:var(--ink-3);margin-top:3px;overflow-wrap:anywhere}
.edges .dashed{border-left-color:var(--warn)}
.arrow{color:var(--ink-3);flex:0 0 auto}

table{border-collapse:collapse;width:100%;font-size:11.5px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:5px 6px;border-bottom:1px solid var(--line-soft)}
th:first-child,td:first-child{text-align:left;font-family:var(--mono)}
th{color:var(--ink-3);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.06em}
td b{font-family:var(--mono)}
.zero{color:var(--warn)}
.scroll{overflow-x:auto}
.note{font-size:11.5px;color:var(--ink-3);line-height:1.65}
.empty{color:var(--ink-3);font-size:12px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<header>
  <h1>의존성 그래프 탐색기</h1>
  <span class="sub">graphkb 산출물 · 타입을 고르면 이웃을 그립니다</span>
  <div class="stats">
    <div class="stat"><b id="s-nodes">–</b><span>타입</span></div>
    <div class="stat"><b id="s-edges">–</b><span>관계</span></div>
    <div class="stat"><b id="s-stated">–</b><span>명시</span></div>
    <div class="stat"><b id="s-inferred">–</b><span>짐작</span></div>
  </div>
</header>

<div class="app">
  <aside>
    <div>
      <div class="lbl">타입 검색</div>
      <input type="search" id="q" placeholder="예: Subnet, RDS, vNet" autocomplete="off" spellcheck="false">
      <ul class="results" id="results"></ul>
    </div>
    <div>
      <div class="lbl">프로바이더</div>
      <div class="chips" id="provs"></div>
    </div>
    <div>
      <div class="lbl">관계 종류</div>
      <div class="chips" id="etypes"></div>
    </div>
    <div>
      <div class="lbl">근거</div>
      <div class="chips" id="basis"></div>
    </div>
    <div>
      <div class="lbl">이웃 깊이</div>
      <div class="depth" id="depth"></div>
    </div>
    <div>
      <div class="lbl">읽는 법</div>
      <div class="legend">
        <div><svg width="30" height="8"><line x1="1" y1="4" x2="29" y2="4" stroke="currentColor" stroke-width="1.6"/></svg> 실선 — 원본이 명시</div>
        <div><svg width="30" height="8"><line x1="1" y1="4" x2="29" y2="4" stroke="currentColor" stroke-width="1.6" stroke-dasharray="3 3"/></svg> 파선 — 우리 짐작</div>
        <div><svg width="30" height="8"><circle cx="8" cy="4" r="4" fill="currentColor"/></svg> 원 크기 — 붙은 관계 수</div>
      </div>
    </div>
  </aside>

  <div id="stage">
    <canvas id="cv"></canvas>
    <div class="hint" id="hint">왼쪽에서 타입을 검색해 고르세요.<br>
      그래프의 <b>원을 누르면</b> 그 타입을 중심으로 다시 그립니다.</div>
    <div id="tip"></div>
  </div>

  <aside class="right" id="detail">
    <div class="empty">타입을 고르면 여기에 관계가 나옵니다.</div>
    <div id="factsbox">
      <div class="lbl">이 데이터의 성질</div>
      <div class="scroll"><table id="facts">
        <thead><tr><th>프로바이더</th><th>타입</th><th>고립</th><th>참조</th><th>포함</th></tr></thead>
        <tbody></tbody>
      </table></div>
      <p class="note" id="facts-note"></p>
    </div>
  </aside>
</div>

<script>
const DATA = __DATA__;
const PC = {aws:'--aws', azure:'--azure', gcp:'--gcp', common:'--core'};
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const provColor = i => css(PC[DATA.providers[i]] || '--core');

/* ---- 색인 만들기 ---- */
const N = DATA.nodes.map((r,i)=>({i, id:r[0], p:r[1], src:r[2], out:[], inn:[]}));
DATA.edges.forEach((e,k)=>{ N[e[0]].out.push(k); N[e[1]].inn.push(k); });
const deg = n => n.out.length + n.inn.length;
const label = id => id.slice(id.indexOf('::')+2);

document.getElementById('s-nodes').textContent = N.length.toLocaleString();
document.getElementById('s-edges').textContent = DATA.edges.length.toLocaleString();
document.getElementById('s-stated').textContent = DATA.basis.stated.toLocaleString();
document.getElementById('s-inferred').textContent = DATA.basis.inferred.toLocaleString();

/* ---- 필터 상태 ---- */
const state = {
  prov:new Set(DATA.providers), etype:new Set(DATA.etypes),
  basis:new Set(['stated','inferred']), depth:1, focus:null
};

function chips(host, items, key, colorize){
  host.innerHTML = '';
  items.forEach(it=>{
    const b = document.createElement('button');
    b.className = 'chip'; b.type = 'button';
    b.setAttribute('aria-pressed','true');
    if(colorize){
      const d = document.createElement('span'); d.className='dot';
      d.style.background = css(PC[it]||'--core'); b.appendChild(d);
    }
    b.appendChild(document.createTextNode(it));
    b.onclick = ()=>{
      const on = b.getAttribute('aria-pressed')==='true';
      b.setAttribute('aria-pressed', String(!on));
      on ? state[key].delete(it) : state[key].add(it);
      rebuild();
    };
    host.appendChild(b);
  });
}
chips(document.getElementById('provs'), DATA.providers, 'prov', true);
chips(document.getElementById('etypes'), DATA.etypes, 'etype', false);
chips(document.getElementById('basis'), ['stated','inferred'], 'basis', false);

const depthHost = document.getElementById('depth');
[1,2].forEach(d=>{
  const b=document.createElement('button'); b.type='button'; b.textContent='이웃 '+d+'단계';
  b.setAttribute('aria-pressed', String(d===1));
  b.onclick=()=>{ state.depth=d;
    [...depthHost.children].forEach(c=>c.setAttribute('aria-pressed', String(c===b)));
    rebuild(); };
  depthHost.appendChild(b);
});

const edgeOk = k => {
  const e = DATA.edges[k];
  return state.etype.has(DATA.etypes[e[2]])
      && state.basis.has(e[7] ? 'stated' : 'inferred')
      && state.prov.has(DATA.providers[N[e[0]].p])
      && state.prov.has(DATA.providers[N[e[1]].p]);
};

/* ---- 검색 ---- */
const q = document.getElementById('q'), results = document.getElementById('results');
q.addEventListener('input', ()=>{
  const t = q.value.trim().toLowerCase();
  results.innerHTML = '';
  if(t.length < 2) return;
  const hits = N.filter(n => state.prov.has(DATA.providers[n.p]) && n.id.toLowerCase().includes(t))
                .sort((a,b)=> deg(b)-deg(a)).slice(0,40);
  hits.forEach(n=>{
    const li=document.createElement('li'), b=document.createElement('button');
    b.type='button';
    const d=document.createElement('span'); d.className='dot';
    d.style.background = provColor(n.p); b.appendChild(d);
    b.appendChild(document.createTextNode(label(n.id)));
    b.onclick = ()=>{ focus(n.i); results.innerHTML=''; q.value=''; };
    li.appendChild(b); results.appendChild(li);
  });
});

/* ---- 캔버스 ---- */
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const hint = document.getElementById('hint'), tip = document.getElementById('tip');
let view = {nodes:[], edges:[]}, drag=null, hover=null, raf=null;

function size(){
  const r = cv.parentElement.getBoundingClientRect(), dpr = devicePixelRatio||1;
  cv.width = r.width*dpr; cv.height = r.height*dpr;
  ctx.setTransform(dpr,0,0,dpr,0,0);
  return r;
}
addEventListener('resize', ()=>{ size(); draw(); });

function focus(i){ state.focus = i; rebuild(); }

function rebuild(){
  if(state.focus===null) return;
  const seen = new Map([[state.focus,0]]);
  let frontier=[state.focus];
  for(let d=1; d<=state.depth; d++){
    const next=[];
    frontier.forEach(i=>{
      N[i].out.concat(N[i].inn).filter(edgeOk).forEach(k=>{
        const e=DATA.edges[k], o = e[0]===i ? e[1] : e[0];
        if(!seen.has(o)){ seen.set(o,d); next.push(o); }
      });
    });
    frontier=next;
  }
  // 2단계에서 이웃이 너무 많으면 관계 수 기준 상위만 — 자른 건 아래에 밝힌다
  let ids=[...seen.keys()], trimmed=0;
  if(ids.length>140){
    const c=state.focus;
    ids = ids.filter(i=>i!==c).sort((a,b)=>deg(N[b])-deg(N[a])).slice(0,139);
    trimmed = seen.size - ids.length - 1;
    ids.unshift(c);
  }
  const inView = new Set(ids);
  const r = size(), cx=r.width/2, cy=r.height/2;
  view.nodes = ids.map((i,k)=>{
    const a = k*2.399963, rad = k===0 ? 0 : 28+Math.sqrt(k)*26;
    return {i, x:cx+Math.cos(a)*rad, y:cy+Math.sin(a)*rad, vx:0, vy:0,
            r: k===0 ? 13 : Math.min(11, 4.5+Math.sqrt(deg(N[i]))*1.1)};
  });
  const pos = new Map(view.nodes.map(n=>[n.i,n]));
  const keys = new Set();
  view.edges = [];
  ids.forEach(i=> N[i].out.filter(edgeOk).forEach(k=>{
    const e=DATA.edges[k];
    if(inView.has(e[1]) && !keys.has(k)){ keys.add(k);
      view.edges.push({k, a:pos.get(e[0]), b:pos.get(e[1]), stated:!!e[7]}); }
  }));
  view.trimmed = trimmed;
  hint.style.display='none';
  detail(state.focus);
  run();
}

function run(){
  let t=0;
  cancelAnimationFrame(raf);
  const step = ()=>{
    const r = cv.getBoundingClientRect();
    for(let s=0;s<2;s++) tick(r.width, r.height);
    draw();
    if(++t<260) raf=requestAnimationFrame(step);
  };
  raf=requestAnimationFrame(step);
}

function tick(W,H){
  const ns=view.nodes;
  for(let a=0;a<ns.length;a++){
    for(let b=a+1;b<ns.length;b++){
      const p=ns[a],q2=ns[b];
      let dx=q2.x-p.x, dy=q2.y-p.y, d2=dx*dx+dy*dy;
      if(d2<1){ dx=Math.random()-.5; dy=Math.random()-.5; d2=1; }
      if(d2>90000) continue;
      const f=2600/d2, d=Math.sqrt(d2), fx=dx/d*f, fy=dy/d*f;
      p.vx-=fx; p.vy-=fy; q2.vx+=fx; q2.vy+=fy;
    }
  }
  view.edges.forEach(e=>{
    const dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.hypot(dx,dy)||1;
    const f=(d-115)*0.012, fx=dx/d*f, fy=dy/d*f;
    e.a.vx+=fx; e.a.vy+=fy; e.b.vx-=fx; e.b.vy-=fy;
  });
  const cx=W/2, cy=H/2;
  ns.forEach((n,k)=>{
    if(k===0 && !drag){ n.x+=(cx-n.x)*.12; n.y+=(cy-n.y)*.12; n.vx=n.vy=0; return; }
    if(drag && drag.n===n) return;
    n.vx+=(cx-n.x)*.0016; n.vy+=(cy-n.y)*.0016;
    n.vx*=.86; n.vy*=.86;
    n.x=Math.max(18,Math.min(W-18,n.x+n.vx));
    n.y=Math.max(18,Math.min(H-18,n.y+n.vy));
  });
}

function draw(){
  const r=cv.getBoundingClientRect();
  ctx.clearRect(0,0,r.width,r.height);
  const soft=css('--line'), ink3=css('--ink-3'), ink=css('--ink');
  view.edges.forEach(e=>{
    const hot = hover && (e.a===hover||e.b===hover);
    ctx.strokeStyle = hot ? css('--accent') : soft;
    ctx.lineWidth = hot ? 1.7 : 1;
    ctx.setLineDash(e.stated ? [] : [3,3]);
    ctx.beginPath(); ctx.moveTo(e.a.x,e.a.y); ctx.lineTo(e.b.x,e.b.y); ctx.stroke();
    // 방향 표시 — 도착점 원 바로 앞에 삼각형
    const dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.hypot(dx,dy)||1;
    const ux=dx/d, uy=dy/d, tx=e.b.x-ux*(e.b.r+3), ty=e.b.y-uy*(e.b.r+3);
    ctx.setLineDash([]); ctx.fillStyle = hot ? css('--accent') : soft;
    ctx.beginPath();
    ctx.moveTo(tx,ty);
    ctx.lineTo(tx-ux*7+uy*3.4, ty-uy*7-ux*3.4);
    ctx.lineTo(tx-ux*7-uy*3.4, ty-uy*7+ux*3.4);
    ctx.closePath(); ctx.fill();
  });
  ctx.setLineDash([]);
  view.nodes.forEach((n,k)=>{
    ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,6.284);
    ctx.fillStyle = provColor(N[n.i].p);
    ctx.globalAlpha = (k===0||n===hover) ? 1 : .82; ctx.fill(); ctx.globalAlpha=1;
    if(k===0||n===hover){ ctx.lineWidth=2; ctx.strokeStyle=css('--ground'); ctx.stroke();
      ctx.lineWidth=1.4; ctx.strokeStyle=css('--accent'); ctx.stroke(); }
  });
  ctx.font = '11px ' + css('--mono').split(',')[0];
  ctx.textAlign='center'; ctx.textBaseline='top';
  view.nodes.forEach((n,k)=>{
    if(k!==0 && n!==hover && n.r<8 && view.nodes.length>46) return;
    const t = label(N[n.i].id), s = t.length>26 ? t.slice(0,25)+'…' : t;
    ctx.fillStyle = (k===0||n===hover) ? ink : ink3;
    ctx.fillText(s, n.x, n.y+n.r+4);
  });
}

/* ---- 마우스 ---- */
const at = (mx,my) => view.nodes.find(n=>Math.hypot(n.x-mx,n.y-my) <= n.r+4);
cv.addEventListener('mousemove', ev=>{
  const r=cv.getBoundingClientRect(), mx=ev.clientX-r.left, my=ev.clientY-r.top;
  if(drag){ drag.n.x=mx; drag.n.y=my; drag.n.vx=drag.n.vy=0; drag.moved=true; draw(); return; }
  const h=at(mx,my);
  if(h!==hover){ hover=h; cv.style.cursor = h?'pointer':'grab'; draw(); }
  if(h){
    const n=N[h.i];
    tip.innerHTML = label(n.id) + '<br><span style="color:var(--ink-3)">'
      + DATA.providers[n.p] + ' · 나가는 ' + n.out.length + ' · 들어오는 ' + n.inn.length + '</span>';
    tip.style.opacity=1;
    tip.style.left = Math.min(mx+14, r.width-toW()) + 'px';
    tip.style.top = (my+14) + 'px';
  } else tip.style.opacity=0;
});
const toW = () => tip.getBoundingClientRect().width + 16;
cv.addEventListener('mouseleave', ()=>{ tip.style.opacity=0; hover=null; draw(); });
cv.addEventListener('mousedown', ev=>{
  const r=cv.getBoundingClientRect(), n=at(ev.clientX-r.left, ev.clientY-r.top);
  if(n) drag={n, moved:false};
});
addEventListener('mouseup', ()=>{
  if(drag && !drag.moved) focus(drag.n.i);
  drag=null;
});

/* ---- 오른쪽 상세 ---- */
function detail(i){
  const n=N[i], host=document.getElementById('detail');
  const rows = (keys, dir) => keys.filter(edgeOk).map(k=>{
    const e=DATA.edges[k], other = N[dir==='out' ? e[1] : e[0]];
    return {k, other, type:DATA.etypes[e[2]], via:e[3], req:e[4],
            card:DATA.cards[e[5]], ev:DATA.evidences[e[6]], stated:!!e[7], dir};
  });
  const out=rows(n.out,'out'), inn=rows(n.inn,'in');

  const list = (items, empty) => {
    if(!items.length) return '<div class="empty">'+empty+'</div>';
    return '<ul class="edges">' + items.map(r=>
      '<li><button type="button" data-go="'+r.other.i+'" class="'+(r.stated?'':'dashed')+'">'
      + '<div class="row1"><span class="arrow">'+(r.dir==='out'?'→':'←')+'</span>'
      + esc(label(r.other.id))+'</div>'
      + '<div class="row2">'+esc(r.type)+(r.via?' · '+esc(r.via):'')
      + ' · '+(r.req?'필수':'선택')+' · '+esc(r.card)
      + ' · '+esc(r.ev)+' · '+(r.stated?'원본에 명시':'짐작')+'</div>'
      + '</button></li>').join('') + '</ul>';
  };

  // id로 잡는다. 예전엔 'div:last-child'였는데, 두 번째 호출부터 box 안쪽의
  // 마지막 div가 먼저 걸려서 성질 표가 통째로 사라졌다.
  const facts = document.getElementById('factsbox');
  host.innerHTML = '';
  const box=document.createElement('div');
  box.innerHTML =
    '<div class="lbl">고른 타입</div>'
    + '<div class="title-id">'+esc(label(n.id))+'</div>'
    + '<div class="meta"><span class="tag">'+esc(DATA.providers[n.p])+'</span>'
    + '<span class="tag">'+esc(DATA.sources[n.src])+'</span>'
    + (deg(n)===0?'<span class="tag warn">고립 — 관계 0건</span>':'')
    + (view.trimmed?'<span class="tag warn">이웃 '+view.trimmed+'개는 안 그림</span>':'')
    + '</div>'
    + '<div style="margin-top:16px"><div class="lbl">나가는 관계 '+out.length+'건 — 먼저 있어야 하는 것</div>'
    + list(out,'없습니다.')+'</div>'
    + '<div style="margin-top:16px"><div class="lbl">들어오는 관계 '+inn.length+'건 — 이걸 쓰는 것</div>'
    + list(inn,'없습니다.')+'</div>';
  host.appendChild(box);
  host.appendChild(facts);
  box.querySelectorAll('[data-go]').forEach(b=> b.onclick=()=>focus(+b.dataset.go));
  host.scrollTop = 0;
}
const esc = s => String(s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* ---- 데이터 성질 표 ---- */
const tb = document.querySelector('#facts tbody');
tb.innerHTML = DATA.facts.map(f=>
  '<tr><td>'+esc(f.provider)+'</td><td>'+f.types.toLocaleString()+'</td>'
  + '<td'+(f.isolated?' class="zero"':'')+'>'+f.isolated.toLocaleString()+'</td>'
  + '<td>'+f.references.toLocaleString()+'</td>'
  + '<td'+(f.contained_in?'':' class="zero"')+'>'+f.contained_in.toLocaleString()+'</td></tr>').join('');
document.getElementById('facts-note').textContent =
  '“고립”은 관계가 하나도 안 붙은 타입입니다. 없어서가 아니라 아직 못 찾아서일 수 있습니다. '
  + '“포함”이 0인 프로바이더는 담김 관계를 아직 안 뽑았다는 뜻이라, 그 축으로는 답이 안 나옵니다.';

/* 시작점 — 관계가 가장 많은 타입 */
size();
focus(N.reduce((a,b)=> deg(b)>deg(a)?b:a).i);
</script>
"""


if __name__ == "__main__":
    main()
