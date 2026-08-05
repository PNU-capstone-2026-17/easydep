"""GKE 생성 라운드 — 국면형(kickoff/continue/pools/finish), container API 직접.

측정 대상:
- **network 생략 하 생성**(kickoff→continue) → 스키마 서술의 default 대체가
  클러스터에서도 실제로 일어나는가(생성 후 cluster.network 실물 확인).
- **노드풀 add/delete**(pools) → k8sNodeGroup 독립 CRUD.
- **클러스터 삭제**(pools→finish) — 노드풀 캐스케이드.

실행: `python run.py {kickoff|continue|pools|finish} <project> <zone>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import call, codes_of, token  # noqa: E402

HERE = Path(__file__).resolve().parent
BASE = "https://container.googleapis.com/v1"


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("GKE 생성 라운드 — network 생략의 default 대체 실측·노드풀 "
                      "CRUD·삭제 캐스케이드."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def req(method, url, body, tok):
    status, resp = call(method, url, body, tok)
    return {"ok": status < 400, "httpStatus": status,
            "errorCodes": codes_of(resp) or ([] if status < 400 else [str(status)]),
            "excerpt": json.dumps(resp, ensure_ascii=False)[:400]}


def main() -> None:
    phase, project, zone = sys.argv[1], sys.argv[2], sys.argv[3]
    tok = token()
    root = f"{BASE}/projects/{project}/zones/{zone}"
    cluster_url = f"{root}/clusters/depkb-gke"
    doc = load()
    steps = doc["steps"]

    def step(name, result):
        steps[name] = result
        save(doc)
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    if phase == "kickoff":
        step("G1.create-omit-network", req("POST", f"{root}/clusters", {
            "cluster": {"name": "depkb-gke", "initialNodeCount": 1,
                        "nodeConfig": {"machineType": "e2-small",
                                       "diskSizeGb": 50}}}, tok))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 480
        while time.time() < deadline:
            status, c = call("GET", cluster_url, None, tok)
            state = c.get("status", f"http{status}")
            print(f"status: {state}", flush=True)
            if state in ("RUNNING", "ERROR", "DEGRADED") or status >= 400:
                break
            time.sleep(30)
        step("G2.status-final", {"ok": state == "RUNNING",
                                 "errorCodes": [] if state == "RUNNING" else [state],
                                 "excerpt": state})
        if state == "RUNNING":
            _, c = call("GET", cluster_url, None, tok)
            step("G3.server-filled-network", {
                "ok": c.get("network") == "default", "errorCodes": [],
                "excerpt": json.dumps({"network": c.get("network"),
                                       "subnetwork": c.get("subnetwork"),
                                       "nodePools": [p["name"] for p in
                                                     c.get("nodePools", [])]},
                                      ensure_ascii=False)})
        return

    if phase == "pools":
        step("P1.nodepool-add", req("POST", f"{cluster_url}/nodePools", {
            "nodePool": {"name": "np2", "initialNodeCount": 1,
                         "config": {"machineType": "e2-small",
                                    "diskSizeGb": 50}}}, tok))
        deadline = time.time() + 420
        while time.time() < deadline:
            status, p = call("GET", f"{cluster_url}/nodePools/np2", None, tok)
            st = p.get("status", f"http{status}")
            print(f"np2: {st}", flush=True)
            if st in ("RUNNING", "ERROR") or status >= 400:
                break
            time.sleep(20)
        step("P2.nodepool-delete", req(
            "DELETE", f"{cluster_url}/nodePools/np2", None, tok))
        return

    if phase == "poolsfix":
        # P2가 FAILED_PRECONDITION이었다 — 추가 연산이 아직 돌고 있었다
        # (클러스터는 한 번에 한 연산 — 그 자체가 동시성 관측). np2가 서면 지운다.
        deadline = time.time() + 420
        while time.time() < deadline:
            status, p = call("GET", f"{cluster_url}/nodePools/np2", None, tok)
            st = p.get("status", f"http{status}")
            print(f"np2: {st}", flush=True)
            if st == "RUNNING":
                step("P3.nodepool-delete-retry", req(
                    "DELETE", f"{cluster_url}/nodePools/np2", None, tok))
                break
            if status == 404:
                step("P3.nodepool-already-gone", {"ok": True, "errorCodes": [],
                                                  "excerpt": "404"})
                break
            time.sleep(20)
        return

    if phase == "finish":
        step("F1.cluster-delete", req("DELETE", cluster_url, None, tok))
        deadline = time.time() + 480
        gone = False
        while time.time() < deadline:
            status, _ = call("GET", cluster_url, None, tok)
            if status == 404:
                gone = True
                break
            print("deleting…", flush=True)
            time.sleep(30)
        step("F2.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "404" if gone else "timeout"})
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
