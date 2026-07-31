"""의존성 그래프 뷰어 — claims.json을 인터랙티브 HTML로 사영한다.

**소비 전용 뷰다** — 판정·증거는 claims.json이 진실이고, 여기서는 그것을
읽기 좋게 그릴 뿐이다. 배치(층)는 우리 구성(가독 목적)이고 판정에 영향이 없다.

- 시각화는 cytoscape.js(CDN)로 그린다 — **보는 데 인터넷이 필요하다.**
  오프라인이면 안내 문구가 뜬다(데이터 자체는 HTML에 내장돼 유실은 없다).
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
    ["image", "customImage", "disk", "nic", "publicIp", "sshKey", "iamRole"],
    ["subnet", "firewall", "internetGateway"],
    ["network"],
]
X_GAP, Y_GAP = 190, 150

CSPS = ("aws", "azure", "gcp")
KO = {"required": "필수", "optional": "선택", "holds": "생명주기 결속",
      "unknown": "미판정"}

#: 문외한용 자원 설명 — **우리 작성**(판정 아님). 실측에서 온 문장은 그렇다고
#: 적는다. 뷰어 노드 상세의 "이게 뭔가요" 칸에 실린다.
DESC: dict[str, str] = {
    "network": "클라우드 안에 만드는 나만의 사설 네트워크 공간입니다. 회사 "
               "건물의 전체 전산망에 해당하고, 쓸 IP 주소 대역을 정해 그 안에 "
               "서버들을 놓습니다. 거의 모든 자원이 결국 이 위에 섭니다.",
    "subnet": "네트워크를 잘게 나눈 구역입니다 — 건물로 치면 층이나 부서망. "
              "서버(NIC)는 반드시 어느 서브넷 안에 놓입니다(3사 공통으로 "
              "실측된 몇 안 되는 규칙입니다).",
    "firewall": "어떤 통신을 허용하고 차단할지 정하는 규칙 목록입니다 — 건물 "
                "출입 규칙표. 자원에 붙이는 건 선택이지만, 붙어서 쓰이는 동안엔 "
                "지울 수 없습니다(실측).",
    "nic": "가상 랜카드입니다 — 서버를 네트워크에 꽂는 플러그. azure에선 "
           "독립된 자원이고, gcp에선 VM 안에 내장돼 따로 만들 수 없습니다"
           "(실측된 3사 차이).",
    "publicIp": "인터넷 어디서나 접근할 수 있는 공인 주소입니다 — 건물의 대표 "
                "전화번호. 없으면 내부 통신만 됩니다.",
    "loadBalancer": "들어오는 요청을 여러 서버에 나눠 주는 장치입니다 — 은행의 "
                    "창구 안내원. gcp에선 부품 하나가 아니라 여러 자원의 "
                    "묶음(성좌)으로 만들어집니다(실측).",
    "vm": "가상 서버, 즉 클라우드에서 빌리는 컴퓨터 한 대입니다. 프로그램이 "
          "실제로 도는 곳이고, 이 그래프 대부분의 화살표가 여기서 나갑니다.",
    "disk": "서버에 붙이는 저장 장치입니다 — 외장하드. 서버를 지워도 디스크가 "
            "살아남는 경우가 있습니다(azure OS 디스크·gcp 부트 디스크, 실측).",
    "image": "서버를 부팅할 원판입니다 — OS가 미리 설치된 템플릿, 컴퓨터 설치 "
             "USB에 해당. aws에선 서버를 만들 때 사람이 정해야 하는 유일한 "
             "필수 입력이고, azure·gcp는 기존 디스크로 대신할 수 있습니다"
             "(실측된 3사 차이).",
    "sshKey": "서버에 원격 접속할 때 비밀번호 대신 쓰는 열쇠 파일입니다. "
              "다루는 방식이 3사 3색입니다(aws 선택 등록·azure 무관·gcp에는 "
              "자원 자체가 없음 — 실측).",
    "vpn": "회사망과 클라우드망을 안전하게 잇는 전용 터널입니다. azure에선 "
           "정확히 GatewaySubnet이라는 이름의 서브넷을 요구하는 특이한 "
           "규칙이 실측됐습니다.",
    "customImage": "우리가 직접 만든 부팅 원판입니다 — 프로그램까지 설치해 "
                   "둔 '골든 이미지'. 만들 때는 원본이 반드시 있어야 하지만"
                   "(실측), 한 번 만들고 나면 **원본을 지워도 이미지는 "
                   "살아남습니다**(3사 공통 실측 — 복사본이기 때문). 원본이 "
                   "무엇이냐는 갈립니다: azure·gcp는 디스크, aws는 서버 "
                   "자체입니다.",
    "iamRole": "서버나 서비스가 다른 클라우드 자원에 접근할 때 쓰는 "
               "신분증(권한 묶음)입니다 — 사원증. VM엔 3사 모두 없어도 "
               "되지만(실측), aws EKS 클러스터는 없으면 만들 수조차 "
               "없습니다(실측). gcp는 API로 만들면 서버가 기본 신분증을 "
               "붙여 주지 않는다는 것도 실측됐습니다.",
    "internetGateway": "사설 네트워크를 인터넷에 잇는 관문입니다 — 건물의 "
                       "정문. 이게 없거나 경로(라우트)가 빠지면 공인 IP가 "
                       "있어도 밖에서 못 들어옵니다(실측). aws에선 실제 "
                       "자원이고, gcp에선 라우트가 가리키는 개념적 목적지, "
                       "azure에선 자원 자체가 없습니다(시스템이 제공) — "
                       "3사 3색.",
    "k8sCluster": "쿠버네티스 클러스터 — 컨테이너(앱을 규격 상자처럼 포장한 "
                  "것)들을 자동으로 배치·복구·확장해 주는 관리 시스템 전체"
                  "입니다. 클라우드가 관리 서버 부분을 대신 운영해 줍니다.",
    "k8sNodeGroup": "클러스터에서 실제로 일하는 서버들의 묶음입니다 — 관리자"
                    "(클러스터)가 지휘하고 노드들이 컨테이너를 돌립니다.",
    "k8sService": "클러스터 안의 앱을 외부에 노출하겠다는 **선언문**입니다 — "
                  "클라우드 자원이 아니라 쿠버네티스에 내는 요청서. "
                  "type=LoadBalancer로 선언하면 클라우드 LB가 저절로 생기고 "
                  "선언을 지우면 함께 사라집니다(3사 실측). 그래서 LB를 따로 "
                  "만들면 이중 생성입니다.",
    "k8sPvc": "앱이 쓸 저장 공간의 **요청서**입니다(PersistentVolumeClaim). "
              "요청서를 내면 클라우드 디스크가 저절로 만들어지고(단, 실제로 "
              "쓰는 앱이 뜰 때 — 실측), 요청서를 지우면 디스크도 함께 "
              "지워집니다(azure·gcp 실측). 디스크를 따로 만들면 이중 생성입니다.",
    "k8sIngress": "웹 주소·경로 기준으로 트래픽을 나누겠다는 **선언문**입니다. "
                  "같은 선언이 gcp에선 LB 묶음을 저절로 만들고, azure·aws 기본 "
                  "상태에선 아무 일도 일어나지 않습니다(실측) — 처리기"
                  "(컨트롤러)를 깔지는 사람이 정합니다.",
}

#: CSP별 실제 명칭 — 어휘 결속(vocabulary.py)과 실측 관찰에서 온 대응.
CSP_NAMES: dict[str, str] = {
    "network": "aws VPC · azure Virtual Network · gcp VPC Network",
    "subnet": "aws Subnet · azure Subnet · gcp Subnetwork",
    "firewall": "aws Security Group · azure NSG · gcp Firewall rules",
    "nic": "aws ENI · azure Network Interface · gcp (Instance 내장)",
    "publicIp": "aws Elastic IP · azure Public IP · gcp Address",
    "loadBalancer": "aws ELB/ALB/NLB · azure Load Balancer · gcp Forwarding Rule 성좌",
    "vm": "aws EC2 Instance · azure Virtual Machine · gcp Compute Instance",
    "disk": "aws EBS Volume · azure Managed Disk · gcp Persistent Disk",
    "image": "aws AMI · azure Image/Marketplace · gcp Image",
    "sshKey": "aws Key Pair · azure SSH Public Key · gcp (자원 없음 — 메타데이터)",
    "vpn": "aws VPN Gateway · azure Virtual Network Gateway · gcp VPN Gateway",
    "internetGateway": "aws Internet Gateway · azure (자원 없음 — 시스템 "
                       "라우트) · gcp (라우트의 next-hop 개념)",
    "iamRole": "aws IAM Role/Instance Profile · azure Managed Identity · "
               "gcp Service Account",
    "customImage": "aws AMI(+스냅샷) · azure Managed Image · gcp Custom Image",
    "k8sCluster": "aws EKS · azure AKS · gcp GKE",
    "k8sNodeGroup": "aws Node Group · azure Node Pool · gcp Node Pool",
    "k8sService": "쿠버네티스 공통 오브젝트 (Service)",
    "k8sPvc": "쿠버네티스 공통 오브젝트 (PersistentVolumeClaim)",
    "k8sIngress": "쿠버네티스 공통 오브젝트 (Ingress)",
}


def _positions() -> dict[str, tuple[int, int]]:
    pos: dict[str, tuple[int, int]] = {}
    width = max(len(row) for row in LAYERS) * X_GAP
    for yi, row in enumerate(LAYERS):
        offset = (width - (len(row) - 1) * X_GAP) / 2
        for xi, name in enumerate(row):
            pos[name] = (int(offset + xi * X_GAP), yi * Y_GAP)
    return pos


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
    pos = _positions()
    out: dict[str, dict] = {"positions": {k: {"x": x, "y": y}
                                          for k, (x, y) in pos.items()},
                            "csps": {}}
    for csp in CSPS:
        rows = [c for c in doc["claims"] if c["csp"] == csp]
        exist = [c for c in rows if c["question"] == "existence"]
        life = {(c["subject"], c["object"]): c for c in rows
                if c["question"] == "lifecycle"}
        funcs = {(c["subject"], c["object"]): c for c in rows
                 if c["question"] == "function"}
        edges, disjunctions = [], []
        touched: set[str] = set()

        def func_block(fc: dict | None) -> dict | None:
            if fc is None:
                return None
            return {"verdict": fc["verdict"], "predicate": fc.get("predicate"),
                    "note": fc.get("note"), "oracle": fc["oracle"],
                    "evidence": fc["evidence"]}
        for c in exist:
            if "|" in c["object"]:
                disjunctions.append(c)  # 노드 상세로 — 선으로 그리면 겹친다
                touched.add(c["subject"])
                continue
            lc = life.get((c["subject"], c["object"]))
            # 증거는 **전부** 싣는다 — 스키마 인용(cite·form·requiredInSchema)
            # 포함. 상세 패널이 요약이 아니라 claims의 전체 기록이어야 한다.
            edges.append({
                "s": c["subject"], "o": c["object"],
                "cls": _edge_class(c), "verdict": c["verdict"],
                "predicate": c.get("predicate"), "note": c.get("note"),
                "oracle": c["oracle"],
                "evidence": c["evidence"],
                "function": func_block(
                    funcs.pop((c["subject"], c["object"]), None)),
                "lifecycle": None if lc is None else {
                    "verdict": lc["verdict"],
                    "cascade": (lc.get("predicate") or "").startswith("동반 정리:"),
                    "predicate": lc.get("predicate"), "note": lc.get("note"),
                    "oracle": lc["oracle"],
                    "evidence": lc["evidence"],
                },
            })
            touched.update((c["subject"], c["object"]))
        # 기능 질문만 실측된 쌍(gcp·aws vm→publicIp) — 존재 간선이 없어도
        # 그린다. 없으면 실측이 뷰에서 사라진다.
        for (s, o), fc in sorted(funcs.items()):
            edges.append({"s": s, "o": o, "cls": "func", "verdict": None,
                          "predicate": None, "note": None, "oracle": fc["oracle"],
                          "evidence": [], "function": func_block(fc),
                          "lifecycle": None})
            touched.update((s, o))
        nodes = [{"id": n, "ghost": n not in touched,
                  "desc": DESC.get(n, ""), "names": CSP_NAMES.get(n, ""),
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
          --req:#0a7d52; --opt:#8a94a2; --auto:#2563c4; --cond:#c26a12;
          --func:#8b3fc6; }
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
  .sw.func { border-top-style:dashed; border-color:var(--func) }
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
  <label class="f"><input type="checkbox" data-cls="func" checked><span class="sw func"></span>기능 결속만</label>
  <label class="f"><input type="checkbox" id="lifeToggle" checked>생명주기 배지 🔒/♻</label>
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
  const COLORS = {req:'#0a7d52', opt:'#8a94a2', auto:'#2563c4', cond:'#c26a12',
                  func:'#8b3fc6'};
  let cy = null, current = 'azure';

  function elements(csp) {
    const d = DATA.csps[csp], els = [];
    for (const n of d.nodes) els.push({group:'nodes',
      data:{id:n.id, ghost:n.ghost?1:0, disj:n.disjunctions,
            desc:n.desc, names:n.names},
      position:{...DATA.positions[n.id]}});
    d.edges.forEach((e,i) => els.push({group:'edges',
      data:{id:'e'+i, source:e.s, target:e.o, cls:e.cls,
            badge: (e.lifecycle ? (e.lifecycle.cascade?'♻':'🔒') : '')
                   + (e.function ? 'ƒ' : ''), ...e}}));
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
    {selector:'edge:selected', style:{width:4}},
    {selector:'.dim', style:{opacity:0.12}},
    {selector:'.hideCls', style:{display:'none'}},
    {selector:'.noBadge', style:{label:''}},
  ]; }

  function render(csp) {
    current = csp;
    document.querySelectorAll('#tabs button').forEach(b =>
      b.classList.toggle('on', b.textContent===csp));
    if (cy) cy.destroy();
    cy = cytoscape({container:document.getElementById('cy'),
      elements:elements(csp), style:style(), wheelSensitivity:0.25});
    cy.fit(undefined, 40);
    applyFilters();
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
  }

  const esc = s => String(s??'').replace(/[&<>]/g,
      m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
  function kv(title, body) {
    return body ? `<div class="kv"><b>${esc(title)}</b>${body}</div>` : '';
  }
  function evList(evidence) {
    // 층별로 나눠 전부 보여준다 — 스키마 인용까지가 그 판정의 전체 기록이다.
    const schema = (evidence||[]).filter(e => e.layer === 'schema');
    const dyn = (evidence||[]).filter(e => e.layer !== 'schema');
    let out = '';
    if (schema.length) out += '<div class="ev"><u>스키마 층</u></div>' +
      schema.map(e => `<div class="ev">${esc(e.cite)}<br>&nbsp;&nbsp;형태 ${esc(e.form)}` +
        ` · 스키마 required=${e.requiredInSchema}</div>`).join('');
    if (dyn.length) out += '<div class="ev"><u>동적 층</u></div>' +
      dyn.map(e => `<div class="ev">[${esc(e.layer)}] ${esc(e.experiment)} / ` +
        `${esc(e.step)} → ${esc(e.code)}</div>`).join('');
    return out;
  }
  const VK = {required:'필수', optional:'선택', holds:'생명주기 결속',
              unknown:'미판정'};

  function edgeDetail(d) {
    const lc = d.lifecycle, fc = d.function;
    const head = d.cls === 'func'
      ? `<span class="chip" style="color:#8b3fc6;border-color:#8b3fc6">기능 결속만 실측</span>`
      : `<span class="chip ${d.cls}">${{req:'필수',opt:'선택',auto:'서버가 채움',cond:'조건부'}[d.cls]}</span>
         <span class="chip">존재 판정: ${VK[d.verdict]||esc(d.verdict)}</span>`;
    return `<h2>${esc(d.s)} → ${esc(d.o)}</h2>
      ${head}
      <span class="chip">도달 오라클: ${esc(d.oracle)} 층</span>
      ${d.cls==='func' ? kv('존재 질문',
          '이 쌍에는 존재 주장이 없다 — 기능 질문만 실측됐다') : ''}
      ${kv('술어 (분류는 우리 구성)', esc(d.predicate))}
      ${kv('노트', esc(d.note))}
      ${d.evidence && d.evidence.length ? kv('증거 — 존재 질문', evList(d.evidence)) : ''}
      ${lc ? kv('생명주기 질문 ' + (lc.cascade?'♻ 동반 정리':'🔒 삭제 보호')
                + ` — 판정 ${VK[lc.verdict]||esc(lc.verdict)}`
                + ` (${esc(lc.oracle)} 층)`,
                (lc.predicate?`${esc(lc.predicate)}<br>`:'')
                + (lc.note?`${esc(lc.note)}<br>`:'')
                + evList(lc.evidence))
          : (d.cls==='func' ? '' : kv('생명주기 질문',
               '이 간선에는 생명주기 주장이 없다 — '
               + '빈칸이 아니라 그 질문을 판정할 실측이 없다는 기록이다'))}
      ${fc ? kv('기능 질문 ƒ — 판정 ' + (VK[fc.verdict]||esc(fc.verdict))
                + ` (${esc(fc.oracle)} 층)`,
                (fc.predicate?`${esc(fc.predicate)}<br>`:'')
                + (fc.note?`${esc(fc.note)}<br>`:'')
                + evList(fc.evidence)) : ''}`;
  }

  function showDetail(t) {
    const p = document.getElementById('panel');
    if (t.isEdge()) { p.innerHTML = edgeDetail(t.data()); return; }
    const d = t.data();
    const disj = (d.disj||[]).map(x =>
      kv(`선언 술어 → ${esc(x.object)} [${VK[x.verdict]||esc(x.verdict)}]`,
         esc(x.predicate||'') + (x.note?`<br>${esc(x.note)}`:''))).join('');
    // 이 노드에 걸린 주장 전부 — 나가는(요구/합성) 것과 들어오는 것.
    const rows = DATA.csps[current].edges;
    const line = e => `<div class="ev">${esc(e.s)} → ${esc(e.o)} · `
      + `${VK[e.verdict]||esc(e.verdict)}`
      + (e.lifecycle ? ` · ${e.lifecycle.cascade?'♻':'🔒'}` : '')
      + (e.predicate ? `<br>&nbsp;&nbsp;${esc(e.predicate)}` : '') + '</div>';
    const outs = rows.filter(e => e.s === d.id).map(line).join('');
    const ins = rows.filter(e => e.o === d.id).map(line).join('');
    p.innerHTML = `<h2>${esc(d.id)}</h2>
      ${d.ghost?'<span class="chip">이 CSP엔 간선 없음</span>':''}
      <span class="chip">${current}</span>
      ${kv('이게 뭔가요 (설명 — 우리 작성, 판정 아님)', esc(d.desc))}
      ${kv('CSP별 이름', esc(d.names))}
      ${kv('이 자원이 요구·합성하는 것', outs)}
      ${kv('이 자원을 요구·합성하는 것', ins)}
      ${disj || ''}
      <div class="empty" style="font-size:12px">간선을 클릭하면 그 주장의
      전체 기록(스키마 인용·동적 증거·생명주기)이 나옵니다.</div>`;
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
  document.querySelectorAll('input[data-cls], #lifeToggle')
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
