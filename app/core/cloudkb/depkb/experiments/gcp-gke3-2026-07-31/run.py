"""GKE 생명주기 라운드 — 클러스터가 쓰는 네트워크·서브넷을 지울 수 있는가.

앞 라운드는 default 네트워크를 썼기에 생명주기를 잴 수 없었다(지우면 안 되는
공용 자원). 여기서는 전용 네트워크·서브넷 위에 클러스터를 세우고 그것들의
삭제를 시도한다. aws에서 확인된 DependencyViolation 패턴이 gcp에도 있는가.

국면: kickoff(전용 net/sub + 클러스터) → continue(RUNNING 폴링) →
life(사용 중 삭제 시도 → 클러스터 삭제) → finish(정리·잔여 확인).

실행: `python run.py {kickoff|continue|life|finish} <project> <region> <zone>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, codes_of, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent
GKE = "https://container.googleapis.com/v1"


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("GKE 생명주기 — 전용 네트워크 위 클러스터로 사용 중 삭제를 "
                      "잰다(앞 라운드는 default를 써 잴 수 없었다)."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    phase, project, region, zone = sys.argv[1:5]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    r = f"{BASE}/projects/{project}/regions/{region}"
    net, sub = f"{g}/networks/depkbg6-net", f"{r}/subnetworks/depkbg6-sub"
    cluster = f"{GKE}/projects/{project}/zones/{zone}/clusters/depkbg6-gke"
    doc = load()
    steps = doc["steps"]

    def step(name, result):
        steps[name] = result
        save(doc)
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    def gke(method, url, body=None):
        status, resp = call(method, url, body, tok)
        return {"ok": status < 400, "httpStatus": status,
                "errorCodes": codes_of(resp) or ([] if status < 400 else [str(status)]),
                "excerpt": json.dumps(resp, ensure_ascii=False)[:400]}

    if phase == "kickoff":
        step("G.create-network", mutate("POST", f"{g}/networks", {
            "name": "depkbg6-net", "autoCreateSubnetworks": False}, tok))
        step("G.create-subnet", mutate("POST", f"{r}/subnetworks", {
            "name": "depkbg6-sub", "ipCidrRange": "10.96.1.0/24",
            "network": net}, tok))
        step("G.create-cluster", gke(
            "POST", f"{GKE}/projects/{project}/zones/{zone}/clusters",
            {"cluster": {"name": "depkbg6-gke", "initialNodeCount": 1,
                         "network": "depkbg6-net", "subnetwork": "depkbg6-sub",
                         "nodeConfig": {"machineType": "e2-small",
                                        "diskSizeGb": 50}}}))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 480
        while time.time() < deadline:
            status, c = call("GET", cluster, None, tok)
            state = c.get("status", f"http{status}")
            print(f"status: {state}", flush=True)
            if state in ("RUNNING", "ERROR") or status >= 400:
                break
            time.sleep(30)
        step("G2.cluster-running", {"ok": state == "RUNNING",
                                    "errorCodes": [] if state == "RUNNING" else [state],
                                    "excerpt": state})
        return

    if phase == "life":
        step("L1.delete-subnet-in-use", mutate("DELETE", sub, None, tok))
        step("L2.delete-network-in-use", mutate("DELETE", net, None, tok))
        step("L3.delete-cluster", gke("DELETE", cluster))
        return

    if phase == "finish":
        gone = False
        deadline = time.time() + 540
        while time.time() < deadline:
            status, _ = call("GET", cluster, None, tok)
            if status == 404:
                gone = True
                break
            print("deleting…", flush=True)
            time.sleep(30)
        step("F1.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "404" if gone else "timeout"})
        step("F2.delete-subnet", mutate("DELETE", sub, None, tok))
        step("F3.delete-network", mutate("DELETE", net, None, tok))
        _, nets = call("GET", f"{g}/networks", None, tok)
        residual = [n["name"] for n in nets.get("items", [])
                    if n["name"].startswith("depkbg6")]
        step("F4.residual", {"ok": not residual, "errorCodes": [],
                             "excerpt": json.dumps(residual)})
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
