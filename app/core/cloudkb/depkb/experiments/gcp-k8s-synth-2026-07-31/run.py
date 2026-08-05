"""k8s 층 합성 라운드(gcp) — Service→LB 성좌 · PVC→디스크.

계획: `document/archive/k8s-synthesis-plan-2026-07-31.md`. azure 판과 같은 셀,
gcp 특이점 둘을 반영한다:

- **kubectl 인증**: gke-gcloud-auth-plugin 미설치 — 설치하는 대신 클러스터
  GET에서 얻은 엔드포인트·CA + `gcloud auth print-access-token`(1h 유효)으로
  플래그 직결(`--server/--certificate-authority/--token`). kubeconfig 파일 없음.
- **전용 네트워크** 위에 세운다(gke3 방식) — 라운드 끝의 네트워크 삭제 성공이
  곧 "k8s가 합성한 방화벽 규칙까지 정리됐다"는 잔여 0 증명이 된다.

오라클은 컨트롤 플레인 열거(forwardingRules·targetPools·addresses·firewalls·
disks). kubectl 상태는 힌트. 시한 내 미소멸은 잔존이 아니라 미판정.

국면: kickoff → continue → pvc → svc → life → finish.
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
CA = HERE / "ca.crt"  # 공개 인증서 — finish에서 지운다
NET, SUB, CLUSTER = "depkbs-net", "depkbs-sub", "depkbs-gke"


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("k8s 층 합성(gcp) — Service→LB 성좌·PVC→디스크. 전용 "
                      "네트워크 위라 마지막 네트워크 삭제 성공이 잔여 0 증명."),
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
    z = f"{BASE}/projects/{project}/zones/{zone}"
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

    def lb_shape(label):
        """LB 성좌 열거 — 판정용 실물. 이름은 k8s가 정한다(a<uuid> 꼴)."""
        _, frs = call("GET", f"{r}/forwardingRules", None, tok)
        _, tps = call("GET", f"{r}/targetPools", None, tok)
        _, fws = call("GET", f"{g}/firewalls", None, tok)
        shape = {
            "forwardingRules": [f["name"] for f in frs.get("items", [])],
            "targetPools": [t["name"] for t in tps.get("items", [])],
            "k8sFirewalls": [f["name"] for f in fws.get("items", [])
                             if f["name"].startswith("k8s")],
        }
        return {"ok": True, "errorCodes": [],
                "excerpt": json.dumps(shape, ensure_ascii=False)[:600]}

    def pvc_disks():
        _, ds = call("GET", f"{z}/disks", None, tok)
        names = [d["name"] for d in ds.get("items", [])]
        return {"ok": True, "errorCodes": [],
                "excerpt": json.dumps({"all": names,
                                       "pvc": [n for n in names
                                               if n.startswith("pvc-")]},
                                      ensure_ascii=False)[:600]}

    if phase == "kickoff":
        step("G.create-network", mutate("POST", f"{g}/networks", {
            "name": NET, "autoCreateSubnetworks": False}, tok))
        step("G.create-subnet", mutate("POST", f"{r}/subnetworks", {
            "name": SUB, "ipCidrRange": "10.96.2.0/24", "network": net}, tok))
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
        step("K3.endpoint", {"ok": bool(ids["endpoint"]), "errorCodes": [],
                             "excerpt": ids["endpoint"]})
        step("K4.default-storageclass", kc(
            ["get", "sc", "-o", "jsonpath={range .items[*]}{.metadata.name} "
             "default={.metadata.annotations.storageclass\\.kubernetes\\.io/is-default-class} "
             "bind={.volumeBindingMode} reclaim={.reclaimPolicy}\\n{end}"]))
        step("K5.disks-baseline", pvc_disks())
        step("K6.lb-baseline", lb_shape("baseline"))
        return

    if phase == "pvc":
        step("P1.apply-pvc", kc(["apply", "-f", str(HERE / "pvc.yaml")]))
        time.sleep(60)
        step("P2.pvc-status-alone", kc(
            ["get", "pvc", "depkb-synth-pvc", "-o",
             "jsonpath=phase={.status.phase}"]))
        step("P3.disks-after-pvc-alone", pvc_disks())
        step("P4.apply-pod-trigger", kc(["apply", "-f", str(HERE / "pod.yaml")]))
        bound = ""
        deadline = time.time() + 360
        while time.time() < deadline:
            resp = kc(["get", "pvc", "depkb-synth-pvc", "-o",
                       "jsonpath={.status.phase}"])
            bound = resp["excerpt"].strip()
            print(f"pvc phase: {bound}", flush=True)
            if bound == "Bound":
                break
            time.sleep(20)
        step("P5.pvc-bound", {"ok": bound == "Bound", "errorCodes": [],
                              "excerpt": bound})
        step("P6.disks-after-pod", pvc_disks())
        step("P7.pv-volumehandle-hint", kc(
            ["get", "pv", "-o", "jsonpath={range .items[*]}{.spec.csi.volumeHandle}\\n{end}"]))
        return

    if phase == "svc":
        step("S1.apply-svc", kc(["apply", "-f", str(HERE / "svc.yaml")]))
        ip = ""
        deadline = time.time() + 360
        while time.time() < deadline:
            resp = kc(["get", "svc", "depkb-synth-svc", "-o",
                       "jsonpath={.status.loadBalancer.ingress[0].ip}"])
            ip = resp["excerpt"].strip()
            print(f"svc ingress: {ip or '(pending)'}", flush=True)
            if ip:
                break
            time.sleep(20)
        step("S2.svc-ingress-hint", {"ok": bool(ip), "errorCodes": [],
                                     "excerpt": ip or "pending-timeout"})
        step("S3.lb-after-svc", lb_shape("after-svc"))
        return

    if phase == "life":
        step("L1.delete-svc", kc(["delete", "svc", "depkb-synth-svc"]))
        gone = False
        deadline = time.time() + 360
        while time.time() < deadline:
            shape = json.loads(lb_shape("poll")["excerpt"])
            print(f"frs: {shape['forwardingRules']}", flush=True)
            if not shape["forwardingRules"] and not shape["targetPools"]:
                gone = True
                break
            time.sleep(20)
        step("L2.lb-after-delete", {"ok": gone, "errorCodes": [],
                                    "excerpt": "cleaned" if gone else "timeout-미판정"})
        step("L3.fw-after-delete", lb_shape("after-delete"))
        step("L4.delete-pod", kc(["delete", "pod", "depkb-synth-pod",
                                  "--wait=false"]))
        time.sleep(30)
        step("L5.delete-pvc", kc(["delete", "pvc", "depkb-synth-pvc"],
                                 timeout=180))
        gone = False
        deadline = time.time() + 360
        while time.time() < deadline:
            body = json.loads(pvc_disks()["excerpt"])
            print(f"pvc-disks: {body['pvc']}", flush=True)
            if not body["pvc"]:
                gone = True
                break
            time.sleep(20)
        step("L6.pvc-disks-after-delete", {"ok": gone, "errorCodes": [],
                                           "excerpt": "cleaned" if gone else "timeout-미판정"})
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
        # 전용 네트워크 삭제 성공 = k8s 합성물(방화벽 규칙 등)까지 잔여 0
        step("F3.delete-subnet", mutate("DELETE", sub, None, tok))
        step("F4.delete-network", mutate("DELETE", net, None, tok))
        step("F5.residual", lb_shape("residual"))
        if CA.exists():
            CA.unlink()
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
