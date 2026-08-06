"""k8s 합성 2라운드(gcp) — Ingress(내장 컨트롤러)·RWX PVC.

계획: `document/archive/k8s-synthesis2-plan-2026-07-31.md`. 1라운드 하네스
재사용(정적 토큰 kubectl·전용 네트워크). gcp HTTP LB는 **전역** 자원이라
열거 범위가 1라운드의 지역 성좌와 다르다(urlMaps·targetHttpProxies·전역
forwardingRules·backendServices).

국면: kickoff → continue → rwx → ing → finish.
실행: `python run.py <phase> <project> <region> <zone>`
"""

import base64
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent
GKE = "https://container.googleapis.com/v1"
KUBECTL = shutil.which("kubectl")
CA = HERE / "ca.crt"  # finish에서 지운다
NET, SUB, CLUSTER = "depkbs2-net", "depkbs2-sub", "depkbs2-gke"


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("합성 2라운드(gcp) — Ingress 내장 컨트롤러의 전역 HTTP LB "
                      "성좌·RWX 전제 부재 관측. 전용 네트워크 삭제가 잔여 0 증명."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ids": {}, "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    phase, project, region, zone = sys.argv[1:5]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    r = f"{BASE}/projects/{project}/regions/{region}"
    net, sub = f"{g}/networks/{NET}", f"{r}/subnetworks/{SUB}"
    cluster = f"{GKE}/projects/{project}/zones/{zone}/clusters/{CLUSTER}"
    doc = load()
    steps, ids = doc["steps"], doc["ids"]

    def step(name, result):
        steps[name] = result
        save(doc)
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    def gke(method, url, body=None):
        status, resp = call(method, url, body, tok)
        return {"ok": status < 400, "httpStatus": status,
                "errorCodes": [] if status < 400 else [str(status)],
                "excerpt": json.dumps(resp, ensure_ascii=False)[:400]}

    def kc(args: list[str], timeout: int = 120) -> dict:
        pre = ["--server", f"https://{ids['endpoint']}",
               "--certificate-authority", str(CA), "--token", token()]
        p = subprocess.run([KUBECTL, *pre, *args],
                           capture_output=True, text=True, timeout=timeout)
        text = (p.stderr or "") + (p.stdout or "")
        return {"ok": p.returncode == 0, "errorCodes": [],
                "excerpt": text.strip().replace("\r", "")[:600]}

    def http_lb_shape():
        """전역 HTTP LB 성좌 — Ingress 합성의 관측 범위."""
        shape = {}
        for key, url in (("urlMaps", f"{g}/urlMaps"),
                         ("targetHttpProxies", f"{g}/targetHttpProxies"),
                         ("globalForwardingRules", f"{g}/forwardingRules"),
                         ("backendServices", f"{g}/backendServices"),
                         ("healthChecks", f"{g}/healthChecks")):
            _, got = call("GET", url, None, tok)
            shape[key] = [i["name"] for i in got.get("items", [])]
        return {"ok": True, "errorCodes": [],
                "excerpt": json.dumps(shape, ensure_ascii=False)[:600]}

    if phase == "kickoff":
        step("G.create-network", mutate("POST", f"{g}/networks", {
            "name": NET, "autoCreateSubnetworks": False}, tok))
        step("G.create-subnet", mutate("POST", f"{r}/subnetworks", {
            "name": SUB, "ipCidrRange": "10.96.3.0/24", "network": net}, tok))
        step("K1.create-cluster", gke(
            "POST", f"{GKE}/projects/{project}/zones/{zone}/clusters",
            {"cluster": {"name": CLUSTER, "initialNodeCount": 1,
                         "network": NET, "subnetwork": SUB,
                         "nodeConfig": {"machineType": "e2-small",
                                        "diskSizeGb": 50}}}))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 600
        while time.time() < deadline:
            status, c = call("GET", cluster, None, tok)
            state = c.get("status", f"http{status}")
            print(f"status: {state}", flush=True)
            if state in ("RUNNING", "ERROR") or status >= 400:
                break
            time.sleep(30)
        step("K2.cluster-running", {"ok": state == "RUNNING",
                                    "errorCodes": [] if state == "RUNNING" else [state],
                                    "excerpt": state})
        _, c = call("GET", cluster, None, tok)
        ids["endpoint"] = c.get("endpoint", "")
        CA.write_bytes(base64.b64decode(
            c.get("masterAuth", {}).get("clusterCaCertificate", "")))
        save(doc)
        step("K3.ingressclasses", kc(["get", "ingressclass", "-o", "name"]))
        step("K4.storageclasses", kc(
            ["get", "sc", "-o", "jsonpath={range .items[*]}{.metadata.name} "
             "prov={.provisioner}\\n{end}"]))
        step("K5.http-lb-baseline", http_lb_shape())
        return

    if phase == "rwx":
        step("P1.apply-rwx-pvc", kc(["apply", "-f", str(HERE / "rwx-pvc.yaml")]))
        time.sleep(90)
        step("P2.rwx-status", kc(
            ["get", "pvc", "depkb-synth2-rwx", "-o",
             "jsonpath=phase={.status.phase}"]))
        step("P3.rwx-events", kc(
            ["get", "events", "--field-selector",
             "involvedObject.name=depkb-synth2-rwx",
             "-o", "jsonpath={range .items[*]}{.reason}: {.message}\\n{end}"]))
        step("P4.delete-rwx-pvc", kc(["delete", "pvc", "depkb-synth2-rwx",
                                      "--wait=false"]))
        return

    if phase == "ing":
        step("I1.apply-np-svc", kc(["apply", "-f", str(HERE / "svc-np.yaml")]))
        step("I2.apply-ingress", kc(["apply", "-f", str(HERE / "ingress.yaml")]))
        addr = ""
        deadline = time.time() + 600
        while time.time() < deadline:
            resp = kc(["get", "ingress", "depkb-synth2-ing", "-o",
                       "jsonpath={.status.loadBalancer.ingress[0].ip}"])
            addr = resp["excerpt"].strip()
            print(f"ingress addr: {addr or '(pending)'}", flush=True)
            if addr:
                break
            time.sleep(30)
        step("I3.ingress-address-hint", {"ok": bool(addr), "errorCodes": [],
                                         "excerpt": addr or "pending-timeout"})
        step("I4.http-lb-after-ingress", http_lb_shape())
        step("I5.ingress-events-hint", kc(
            ["get", "events", "--field-selector",
             "involvedObject.name=depkb-synth2-ing",
             "-o", "jsonpath={range .items[*]}{.reason}: {.message}\\n{end}"]))
        step("I6.delete-ingress", kc(["delete", "ingress", "depkb-synth2-ing"]))
        gone = False
        deadline = time.time() + 600
        while time.time() < deadline:
            shape = json.loads(http_lb_shape()["excerpt"])
            print(f"fr: {shape['globalForwardingRules']}", flush=True)
            if not any(shape.values()):
                gone = True
                break
            time.sleep(30)
        step("I7.http-lb-after-delete", {"ok": gone, "errorCodes": [],
                                         "excerpt": "cleaned" if gone else
                                         json.dumps(shape, ensure_ascii=False)[:300]})
        step("I8.delete-np-svc", kc(["delete", "svc", "depkb-synth2-np"]))
        return

    if phase == "finish":
        step("F1.delete-cluster", gke("DELETE", cluster))
        gone = False
        deadline = time.time() + 720
        while time.time() < deadline:
            status, _ = call("GET", cluster, None, tok)
            if status == 404:
                gone = True
                break
            print("deleting…", flush=True)
            time.sleep(30)
        step("F2.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "404" if gone else "timeout"})
        step("F3.delete-subnet", mutate("DELETE", sub, None, tok))
        step("F4.delete-network", mutate("DELETE", net, None, tok))
        step("F5.residual-global", http_lb_shape())
        if CA.exists():
            CA.unlink()
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
