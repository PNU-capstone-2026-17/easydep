"""의존성 그래프 뷰어 — claims.json을 인터랙티브 HTML로 사영한다.

**소비 전용 뷰다** — 판정·증거는 claims.json이 진실이고, 여기서는 그것을
읽기 좋게 그릴 뿐이다. 배치(층)는 우리 구성(가독 목적)이고 판정에 영향이 없다.

## 왜 한 그림이 아니라 세 그림인가

의존은 한 관계가 아니라 **질문 셋**이다(존재·생명주기·기능). 한 화면에 겹쳐
그리던 이전 판은 존재 간선 위에 🔒/♻/ƒ 배지를 얹었는데, 그러면 이 분석의
요점 — *같은 쌍인데 질문마다 답이 다르다* — 가 배지 하나로 뭉개진다.

그래서 **같은 배치의 판 셋을 나란히** 놓는다. 좌표가 같으므로 같은 자리를
가로로 훑으면 축별 차이가 바로 보인다. 화면(pan/zoom)과 선택은 세 판이
공유한다.

세 축이 모두 실측된 쌍은 82쌍 중 둘뿐이다(`azure nic→publicIp`,
`azure vm→disk`). 이 희소함 자체가 결과이므로, 간선을 고르면 상세 패널이
**축별 대조표**를 내고 빈 칸을 "실측 없음"으로 명시한다 — 빈칸을 "의존
없음"으로 읽지 않게 하려는 것이다.

## 연산 성질

`operations.json`의 사영으로 ⏳ 배지를 **노드**에 단다. 간선이 아닌 이유는
비동기성이 (간선×CSP×질문)이 아니라 **자원 하나의 성질**이기 때문이다.
미표시는 "동기"가 아니라 "안 재봤다"이고, 그 규율을 배지 클릭 시 밝힌다.

- 시각화는 cytoscape.js(CDN)로 그린다 — **보는 데 인터넷이 필요하다.**
  오프라인이면 안내 문구가 뜬다(데이터 자체는 HTML에 내장돼 유실은 없다).
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
    ["k8sCluster", "k8sNodeGroup", "vpn", "globalDnsRecord"],
    ["vm", "loadBalancer", "globalDns", "fileSystem", "storageAccount"],
    ["image", "customImage", "disk", "nic", "publicIp", "sshKey", "iamRole"],
    ["subnet", "firewall", "internetGateway"],
    ["network"],
]
X_GAP, Y_GAP = 190, 150

CSPS = ("aws", "azure", "gcp")
#: 질문 축 — 판 셋의 순서이자 상세 대조표의 행 순서.
AXES = ("existence", "lifecycle", "function")
AXIS_LABEL = {"existence": "존재", "lifecycle": "생명주기", "function": "기능"}
AXIS_ASK = {
    "existence": "A를 만들려면 B가 먼저 있어야 하는가",
    "lifecycle": "B를 지우려 할 때 A가 무엇을 하는가",
    "function": "B를 떼면 A가 계속 동작하는가",
}

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
    "globalDns": "이름(도메인)을 관리하는 영역입니다 — 전화번호부 한 권. "
                 "레코드를 담는 그릇이고, aws에선 사설 영역을 만들 때 "
                 "네트워크를 반드시 붙여야 합니다(실측).",
    "globalDnsRecord": "'이 이름은 이 주소'라는 항목 하나입니다 — 전화번호부의 "
                       "한 줄. 영역 없이는 못 만듭니다(3사 실측). 지울 때가 "
                       "갈립니다: gcp·aws는 레코드가 남아 있으면 영역 삭제를 "
                       "거부하는데, azure는 그냥 지워집니다(양상 반전 실측).",
    "fileSystem": "여러 서버가 **동시에** 함께 쓰는 저장소입니다 — 공용 "
                  "문서함(디스크가 1인용 외장하드라면 이건 공유 폴더). "
                  "네트워크와 엮이는 방식이 3사 3색입니다: aws는 저장소 "
                  "자체는 네트워크가 필요 없고 접속점만 서브넷을 요구하며, "
                  "gcp는 저장소가 네트워크를 요구하고, azure는 네트워크가 "
                  "아니라 스토리지 계정 밑에 놓입니다(실측).",
    "storageAccount": "azure에서 저장 자원들을 담는 상위 그릇입니다 — 계좌 "
                      "하나에 여러 통장. 파일 공유는 이 계정 밑에서만 "
                      "만들어집니다(실측). 다른 두 CSP엔 대응 개념이 "
                      "이 자리에 없습니다.",
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
    "globalDns": "aws Route53 Hosted Zone · azure Private DNS Zone · "
                 "gcp Cloud DNS Managed Zone",
    "globalDnsRecord": "aws ResourceRecordSet · azure DNS record-set · "
                       "gcp ResourceRecordSet",
    "fileSystem": "aws EFS(+Mount Target) · azure File Share · gcp Filestore",
    "storageAccount": "azure Storage Account (aws·gcp엔 이 자리의 대응 "
                      "개념이 없다)",
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


def _existence_class(claim: dict) -> str:
    """존재 판 간선의 색·선 — 판정과 술어 부류를 섞은 **우리 분류**."""
    pred = claim.get("predicate") or ""
    if pred.split(":")[0].endswith(("조건부", "조건")) and not pred.startswith(
            ("이름 조건", "배치 조건", "수명 조건")):
        return "cond"
    if claim["verdict"] == "required":
        return "req"
    if pred.startswith(("server-default:", "server-implicit:")):
        return "auto"
    return "opt"


def _axis_class(claim: dict) -> str:
    """축별 간선 부류. 생명주기는 두 기제가 **반대**라 반드시 갈라 그린다.

    `deleteBefore`(쓰는 동안 대상 삭제 거부)와 `동반 정리:`(주체 삭제가 합성물을
    함께 지움)는 방향이 반대인 사실이다 — 한 색으로 그리면 IaC가 뒤집어 읽는다.
    """
    if claim["question"] == "existence":
        return _existence_class(claim)
    if claim["question"] == "function":
        return "func"
    return "casc" if (claim.get("predicate") or "").startswith("동반 정리:") \
        else "life"


def _claim_block(c: dict) -> dict:
    """상세 패널이 쓰는 주장 한 건의 **전체 기록** — 요약하지 않는다."""
    return {"verdict": c["verdict"], "predicate": c.get("predicate"),
            "note": c.get("note"), "oracle": c["oracle"],
            "evidence": c["evidence"], "cls": _axis_class(c)}


def _operations() -> dict[str, dict[str, list[dict]]]:
    """`operations.json` → {csp: {resource: [연산…]}}. 없으면 빈 사영."""
    path = _HERE / "operations.json"
    if not path.exists():
        return {c: {} for c in CSPS}
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, list[dict]]] = {c: {} for c in CSPS}
    for o in doc["operations"]:
        out.setdefault(o["csp"], {}).setdefault(o["resource"], []).append(
            {"op": o["op"], "status": o["status"],
             "doneSignal": o["doneSignal"],
             "intermediate": o.get("intermediateObserved"),
             "evidence": o["evidence"]})
    return out


def _build_data() -> dict:
    doc = json.loads((_HERE / "claims.json").read_text(encoding="utf-8"))
    pos = _positions()
    ops = _operations()
    out: dict = {"positions": {k: {"x": x, "y": y} for k, (x, y) in pos.items()},
                 "axisLabel": AXIS_LABEL, "axisAsk": AXIS_ASK, "csps": {}}
    #: 세 축이 모두 실측된 쌍 — 희소함 자체가 결과다(상세 패널이 표시한다).
    triples: list[str] = []
    for csp in CSPS:
        rows = [c for c in doc["claims"] if c["csp"] == csp]
        axes: dict[str, list[dict]] = {a: [] for a in AXES}
        #: 쌍 → 축 → 주장. 상세의 축별 대조표가 여기서 나온다.
        pairs: dict[str, dict[str, dict]] = {}
        disjunctions = [c for c in rows
                        if c["question"] == "existence" and "|" in c["object"]]
        for c in rows:
            if "|" in c["object"]:
                continue  # 선언 술어는 선으로 그리지 않는다 — 노드 상세로
            key = f'{c["subject"]}→{c["object"]}'
            block = _claim_block(c)
            pairs.setdefault(key, {})[c["question"]] = block
            axes[c["question"]].append(
                {"s": c["subject"], "o": c["object"], "key": key, **block})
        for key, byq in pairs.items():
            if len(byq) == 3:
                triples.append(f"{csp} {key}")
        touched = {a: {e["s"] for e in axes[a]} | {e["o"] for e in axes[a]}
                   for a in AXES}
        nodes = [{"id": n, "desc": DESC.get(n, ""), "names": CSP_NAMES.get(n, ""),
                  "ops": ops.get(csp, {}).get(n, []),
                  "inAxis": {a: n in touched[a] for a in AXES},
                  "disjunctions": [
                      {"object": d["object"], "verdict": d["verdict"],
                       "predicate": d.get("predicate"), "note": d.get("note")}
                      for d in disjunctions if d["subject"] == n]}
                 for n in pos]
        out["csps"][csp] = {"nodes": nodes, "axes": axes, "pairs": pairs,
                            "counts": {a: len(axes[a]) for a in AXES}}
    out["triples"] = sorted(triples)
    return out


_PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>depkb 의존성 그래프 — 질문 축 3판</title>
<script src="https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js"></script>
<style>
  :root { --ink:#1c1e21; --sub:#5b6572; --line:#d7dce3; --bg:#f6f7f9;
          --req:#0a7d52; --opt:#8a94a2; --auto:#2563c4; --cond:#c26a12;
          --life:#b3261e; --casc:#0f7f8c; --func:#8b3fc6; }
  * { box-sizing:border-box }
  body { margin:0; font:14px/1.5 "Segoe UI",system-ui,sans-serif;
         color:var(--ink); background:var(--bg); height:100vh;
         display:flex; flex-direction:column }
  header { padding:8px 16px; background:#fff; border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:14px; flex-wrap:wrap }
  h1 { font-size:15px; margin:0 8px 0 0 }
  .tabs button { border:1px solid var(--line); background:#fff; padding:5px 14px;
                 cursor:pointer; font:inherit }
  .tabs button:first-child { border-radius:6px 0 0 6px }
  .tabs button:last-child { border-radius:0 6px 6px 0 }
  .tabs button.on { background:var(--ink); color:#fff; border-color:var(--ink) }
  label.f { display:inline-flex; align-items:center; gap:4px; color:var(--sub);
            cursor:pointer; user-select:none; font-size:12px }
  .sw { display:inline-block; width:20px; height:0; border-top:3px solid }
  .sw.req { border-color:var(--req) }
  .sw.opt { border-top-style:dashed; border-color:var(--opt) }
  .sw.auto { border-top-style:dotted; border-color:var(--auto) }
  .sw.cond { border-top-style:dashed; border-color:var(--cond) }
  .sw.life { border-color:var(--life) }
  .sw.casc { border-top-style:dashed; border-color:var(--casc) }
  .sw.func { border-top-style:dashed; border-color:var(--func) }
  #reset { margin-left:auto; border:1px solid var(--line); background:#fff;
           padding:5px 12px; border-radius:6px; cursor:pointer; font:inherit }
  main { flex:1; display:flex; min-height:0 }
  #panes { flex:1; display:flex; min-width:0 }
  .pane { flex:1; min-width:0; display:flex; flex-direction:column;
          border-right:1px solid var(--line); background:#fff }
  .pane > .cyBox { flex:1; min-height:0 }
  .ph { padding:6px 10px; border-bottom:1px solid var(--line);
        display:flex; align-items:baseline; gap:8px; flex-wrap:wrap;
        background:#fbfcfd }
  .ph b { font-size:13px }
  .ph .ask { color:var(--sub); font-size:11px }
  .ph .n { margin-left:auto; color:var(--sub); font-size:12px }
  aside { width:370px; border-left:1px solid var(--line); background:#fff;
          padding:14px 16px; overflow-y:auto }
  aside h2 { font-size:14px; margin:0 0 6px }
  aside .empty { color:var(--sub) }
  .chip { display:inline-block; padding:1px 8px; border-radius:10px;
          font-size:12px; margin:0 4px 4px 0; border:1px solid var(--line) }
  .chip.req { color:var(--req); border-color:var(--req) }
  .chip.opt { color:var(--sub) }
  .chip.auto { color:var(--auto); border-color:var(--auto) }
  .chip.cond { color:var(--cond); border-color:var(--cond) }
  .chip.life { color:var(--life); border-color:var(--life) }
  .chip.casc { color:var(--casc); border-color:var(--casc) }
  .chip.func { color:var(--func); border-color:var(--func) }
  .kv { margin:8px 0; padding:8px 10px; background:var(--bg); border-radius:8px;
        font-size:13px; overflow-wrap:anywhere }
  .kv b { display:block; font-size:12px; color:var(--sub); margin-bottom:2px }
  .ev { font-family:Consolas,monospace; font-size:12px; color:var(--sub) }
  table.ax { width:100%; border-collapse:collapse; font-size:12px; margin:6px 0 }
  table.ax th, table.ax td { border:1px solid var(--line); padding:4px 6px;
                             text-align:left; vertical-align:top }
  table.ax th { width:66px; background:var(--bg); font-weight:600 }
  table.ax td.none { color:#9aa4b1 }
  footer { padding:6px 16px; color:var(--sub); font-size:12px; background:#fff;
           border-top:1px solid var(--line) }
  #offline { display:none; padding:40px; color:var(--sub) }
</style></head><body>
<header>
  <h1>depkb 의존성 그래프</h1>
  <span class="tabs" id="tabs"></span>
  <label class="f"><input type="checkbox" id="opToggle" checked>⏳ 연산 배지</label>
  <label class="f"><input type="checkbox" id="ghostToggle" checked>간선 없는 노드</label>
  <label class="f"><input type="checkbox" id="syncToggle" checked>세 판 화면 동기화</label>
  <button id="reset">배치 초기화</button>
</header>
<main>
  <div id="panes"></div>
  <div id="offline">cytoscape.js(CDN)를 불러오지 못했습니다 — 보려면 인터넷이
    필요합니다. 데이터는 이 파일에 내장돼 있습니다(&lt;script id="data"&gt;).</div>
  <aside id="panel"></aside>
</main>
<footer>판정·증거의 진실은 claims.json — 이 페이지는 사영이다. 배치는 가독
목적의 우리 구성이고 세 판이 좌표를 공유한다(가로로 훑으면 축별 차이가 보인다).</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const AXES = ['existence','lifecycle','function'];
if (typeof cytoscape === 'undefined') {
  document.getElementById('panes').style.display='none';
  document.getElementById('offline').style.display='block';
} else {
  const COLORS = {req:'#0a7d52', opt:'#8a94a2', auto:'#2563c4', cond:'#c26a12',
                  life:'#b3261e', casc:'#0f7f8c', func:'#8b3fc6'};
  const CLS_KO = {req:'필수', opt:'선택', auto:'서버가 채움', cond:'조건부',
                  life:'🔒 삭제 보호', casc:'♻ 동반 정리', func:'ƒ 기능 결속'};
  // 판마다 다른 범례 — 존재 판만 넷이고, 나머지는 기제가 갈리는 축이다.
  const LEGEND = {existence:['req','opt','auto','cond'],
                  lifecycle:['life','casc'], function:['func']};
  const VK = {required:'필수', optional:'선택', holds:'결속', unknown:'미판정'};

  let cys = {}, current = 'azure', syncing = false;

  const esc = s => String(s??'').replace(/[&<>]/g,
      m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));

  function opBadge(node) {
    // ⏳는 자원의 성질이지 간선의 성질이 아니다 — 그래서 노드에 단다.
    return (node.ops && node.ops.length) ? ' ⏳' : '';
  }

  function elements(csp, axis) {
    const d = DATA.csps[csp], els = [];
    for (const n of d.nodes) els.push({group:'nodes',
      data:{id:n.id, ghost:n.inAxis[axis]?0:1, label:n.id,
            labelOp:n.id + opBadge(n), hasOp:(n.ops||[]).length?1:0},
      position:{...DATA.positions[n.id]}});
    d.axes[axis].forEach((e,i) => els.push({group:'edges',
      data:{id:axis+'-e'+i, source:e.s, target:e.o, cls:e.cls, key:e.key,
            axis:axis}}));
    return els;
  }

  function style() { return [
    {selector:'node', style:{
      shape:'round-rectangle', width:100, height:34, 'background-color':'#fff',
      'border-width':1.5, 'border-color':'#9aa4b1', label:'data(labelOp)',
      'font-size':13, 'text-valign':'center', 'text-halign':'center',
      color:'#1c1e21'}},
    {selector:'node[ghost=1]', style:{opacity:0.28, 'border-style':'dashed'}},
    {selector:'node.pick', style:{'border-color':'#111', 'border-width':3,
      'background-color':'#fff7d6'}},
    {selector:'edge', style:{
      'curve-style':'bezier', 'control-point-step-size':55,
      'target-arrow-shape':'triangle', 'arrow-scale':1.1, width:2}},
    ...Object.entries(COLORS).map(([k,c]) => ({selector:`edge[cls="${k}"]`,
      style:{'line-color':c, 'target-arrow-color':c,
             width:k==='req'?3:2,
             'line-style':(k==='opt'||k==='cond'||k==='casc'||k==='func')
                          ? 'dashed' : k==='auto' ? 'dotted' : 'solid'}})),
    {selector:'edge.pick', style:{width:5, 'z-index':9}},
    {selector:'.dim', style:{opacity:0.1}},
    {selector:'.hideCls', style:{display:'none'}},
    {selector:'.hideGhost', style:{display:'none'}},
    {selector:'node.noOp', style:{label:'data(label)'}},
  ]; }

  function buildPanes(csp) {
    const host = document.getElementById('panes');
    host.innerHTML = '';
    cys = {};
    for (const axis of AXES) {
      const pane = document.createElement('div');
      pane.className = 'pane';
      const legend = LEGEND[axis].map(k =>
        `<label class="f"><input type="checkbox" data-axis="${axis}" data-cls="${k}" checked>` +
        `<span class="sw ${k}"></span>${CLS_KO[k]}</label>`).join('');
      pane.innerHTML =
        `<div class="ph"><b>${DATA.axisLabel[axis]}</b>` +
        `<span class="ask">${esc(DATA.axisAsk[axis])}</span>` +
        `<span class="n">${DATA.csps[csp].counts[axis]}건</span></div>` +
        `<div class="ph">${legend}</div>` +
        `<div class="cyBox" id="cy-${axis}"></div>`;
      host.appendChild(pane);
    }
    for (const axis of AXES) {
      const cy = cytoscape({container:document.getElementById('cy-'+axis),
        elements:elements(csp, axis), style:style(), wheelSensitivity:0.25});
      cys[axis] = cy;
      cy.on('tap', 'node', ev => pickNode(ev.target.id()));
      cy.on('tap', 'edge', ev => pickEdge(ev.target.data('key')));
      cy.on('tap', ev => { if (ev.target === cy) clearPick(); });
      cy.on('viewport', () => syncView(axis));
    }
    // 세 판이 같은 좌표계를 쓰므로 첫 판의 화면을 그대로 복사한다 —
    // 판마다 fit하면 배율이 달라져 "가로로 훑기"가 깨진다.
    cys.existence.fit(undefined, 30);
    syncView('existence');
    applyFilters();
  }

  function syncView(from) {
    if (syncing || !document.getElementById('syncToggle').checked) return;
    syncing = true;
    const src = cys[from];
    for (const axis of AXES) if (axis !== from) {
      cys[axis].viewport({zoom:src.zoom(), pan:{...src.pan()}});
    }
    syncing = false;
  }

  function applyFilters() {
    const showOp = document.getElementById('opToggle').checked;
    const showGhost = document.getElementById('ghostToggle').checked;
    document.querySelectorAll('input[data-cls]').forEach(cb => {
      const cy = cys[cb.dataset.axis];
      if (cy) cy.edges(`[cls="${cb.dataset.cls}"]`)
                .toggleClass('hideCls', !cb.checked);
    });
    for (const axis of AXES) {
      cys[axis].nodes().toggleClass('noOp', !showOp);
      cys[axis].nodes('[ghost=1]').toggleClass('hideGhost', !showGhost);
    }
  }

  function clearMarks() {
    for (const axis of AXES) cys[axis].elements().removeClass('pick dim');
  }

  function pickNode(id) {
    clearMarks();
    for (const axis of AXES) {
      const cy = cys[axis], n = cy.$id(id);
      n.addClass('pick');
      cy.elements().not(n.closedNeighborhood()).addClass('dim');
    }
    showNode(id);
  }

  function pickEdge(key) {
    clearMarks();
    // 같은 쌍을 **세 판 모두**에서 짚는다 — 없는 판은 비어 있는 것이 답이다.
    // 키에 '→'가 들어가므로 선택자 문자열이 아니라 filter로 고른다.
    for (const axis of AXES) {
      const cy = cys[axis], es = cy.edges().filter(e => e.data('key') === key);
      es.addClass('pick');
      es.connectedNodes().addClass('pick');
      if (es.length) cy.elements().not(es.union(es.connectedNodes())).addClass('dim');
    }
    showPair(key);
  }

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

  function axisRow(axis, b) {
    if (!b) return `<tr><th>${DATA.axisLabel[axis]}</th>` +
      `<td class="none" colspan="2">실측 없음 — <i>의존이 없다는 뜻이 아니다</i></td></tr>`;
    return `<tr><th>${DATA.axisLabel[axis]}</th>` +
      `<td><span class="chip ${b.cls}">${CLS_KO[b.cls]}</span><br>` +
      `${VK[b.verdict]||esc(b.verdict)} · ${esc(b.oracle)} 층</td>` +
      `<td>${esc(b.predicate||'')}${b.note?'<br>'+esc(b.note):''}</td></tr>`;
  }

  function showPair(key) {
    const byq = DATA.csps[current].pairs[key] || {};
    const [s,o] = key.split('→');
    const all3 = AXES.every(a => byq[a]);
    const p = document.getElementById('panel');
    p.innerHTML = `<h2>${esc(s)} → ${esc(o)}</h2>
      <span class="chip">${current}</span>
      ${all3 ? '<span class="chip" style="color:#8b3fc6;border-color:#8b3fc6">'
             + '세 축 모두 실측 — 전체 '+DATA.triples.length+'쌍 중 하나</span>' : ''}
      <table class="ax"><tr><th>축</th><th>판정</th><th>술어·노트</th></tr>
      ${AXES.map(a => axisRow(a, byq[a])).join('')}</table>
      ${AXES.filter(a => byq[a]).map(a => kv(
          DATA.axisLabel[a] + ' 질문의 증거', evList(byq[a].evidence))).join('')}
      <div class="empty" style="font-size:12px">화살표 A→B는 “A가 B를
      요구/합성한다”입니다(포함 아님). 빈 축은 <b>빈칸이 아니라 기록</b>입니다 —
      그 질문을 판정할 실측이 아직 없다는 뜻입니다.</div>`;
  }

  function showNode(id) {
    const d = DATA.csps[current].nodes.find(n => n.id === id);
    const pairs = DATA.csps[current].pairs;
    const line = (key) => {
      const byq = pairs[key], marks = AXES.filter(a => byq[a])
        .map(a => `<span class="chip ${byq[a].cls}">${CLS_KO[byq[a].cls]}</span>`).join('');
      return `<div class="ev">${esc(key)} ${marks}</div>`;
    };
    const outs = Object.keys(pairs).filter(k => k.split('→')[0] === id).map(line).join('');
    const ins = Object.keys(pairs).filter(k => k.split('→')[1] === id).map(line).join('');
    const ops = (d.ops||[]).map(o =>
      `<div class="ev">${esc(o.op)} → 완료 신호 <b>${esc(o.doneSignal)}</b><br>` +
      `&nbsp;&nbsp;${o.status === 'async-confirmed'
        ? '중간 상태 <b>'+esc(o.intermediate)+'</b>가 실측됐다 (비동기 확인)'
        : '우리가 기다렸다 — <i>기다려야 한다는 증명은 아니다</i>'}<br>` +
      `&nbsp;&nbsp;${esc(o.evidence.experiment)} / ${esc(o.evidence.step)}</div>`
    ).join('');
    const disj = (d.disjunctions||[]).map(x =>
      kv(`선언 술어 → ${esc(x.object)} [${VK[x.verdict]||esc(x.verdict)}]`,
         esc(x.predicate||'') + (x.note?`<br>${esc(x.note)}`:''))).join('');
    document.getElementById('panel').innerHTML = `<h2>${esc(id)}</h2>
      <span class="chip">${current}</span>
      ${AXES.filter(a => !d.inAxis[a]).map(a =>
        `<span class="chip">${DATA.axisLabel[a]} 판에 간선 없음</span>`).join('')}
      ${kv('이게 뭔가요 (설명 — 우리 작성, 판정 아님)', esc(d.desc))}
      ${kv('CSP별 이름', esc(d.names))}
      ${ops ? kv('⏳ 연산 성질 (실측) — 만들고 기다려야 하는가', ops)
            : kv('⏳ 연산 성질', '<i>이 자원의 연산은 재지 않았다 — '
                 + "'동기'라는 뜻이 아니다</i>")}
      ${kv('이 자원이 요구·합성하는 것', outs || '<div class="ev">없음</div>')}
      ${kv('이 자원을 요구·합성하는 것', ins || '<div class="ev">없음</div>')}
      ${disj}
      <div class="empty" style="font-size:12px">간선을 클릭하면 그 쌍의
      <b>축별 대조표</b>와 증거 좌표가 나옵니다.</div>`;
  }

  function clearPick() {
    clearMarks();
    document.getElementById('panel').innerHTML =
      `<h2>세 판을 가로로 읽으세요</h2>
       <div class="empty">같은 자리·같은 좌표의 판 셋입니다. 왼쪽부터
       <b>존재</b>(만들 때) · <b>생명주기</b>(지울 때) · <b>기능</b>(떼었을 때).
       <br><br>노드나 간선을 클릭하면 그 쌍이 <b>세 판 모두에서</b> 짚어지고,
       상세에 축별 대조표가 나옵니다 — 어느 판에서 사라지는지가 곧 결과입니다.
       <br><br>세 축이 모두 실측된 쌍은 3사 통틀어
       <b>${DATA.triples.length}쌍</b>뿐입니다:
       <div class="ev">${DATA.triples.map(t => esc(t)).join('<br>')}</div>
       <br>⏳는 <b>자원</b>의 성질입니다(간선이 아님) — 만들고 완료를
       기다려야 하는 자원입니다.</div>`;
  }

  const tabs = document.getElementById('tabs');
  for (const csp of ['aws','azure','gcp']) {
    const b = document.createElement('button');
    b.textContent = csp;
    b.onclick = () => {
      current = csp;
      document.querySelectorAll('#tabs button').forEach(x =>
        x.classList.toggle('on', x.textContent === csp));
      buildPanes(csp); clearPick();
    };
    tabs.appendChild(b);
  }
  document.getElementById('panes').addEventListener('change', applyFilters);
  ['opToggle','ghostToggle'].forEach(id =>
    document.getElementById(id).addEventListener('change', applyFilters));
  document.getElementById('reset').onclick = () => {
    buildPanes(current); clearPick();
  };
  document.querySelector('#tabs button:nth-child(2)').click();
}
</script></body></html>
"""


def main() -> None:
    data = _build_data()
    html = _PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    _OUT.write_text(html, encoding="utf-8")
    counts = {c: data["csps"][c]["counts"] for c in CSPS}
    print(f"wrote {_OUT}")
    print(f"  축별 주장 {counts}")
    print(f"  세 축 모두 실측된 쌍 {len(data['triples'])}: {data['triples']}")


if __name__ == "__main__":
    main()
