"""gcp 2라운드 — nic·lb 잔여 쌍의 조건부 양상을 잰다.

가설 셋, 전부 조건부 술어가 걸려 있다:

- **A1** custom 모드 네트워크에서 NIC가 network만 지정(서브넷 생략) → 거부 예상.
- **A2** auto 모드 네트워크에서 같은 생략 → 성공 예상(서버가 리전 서브넷 대체).
  성공하면 인스턴스의 NIC에 서버가 채운 subnetwork를 읽어 대체의 실물을 남긴다.
- **B** EXTERNAL 포워딩 규칙(+빈 targetPool)은 network·subnetwork 없이 선다 →
  lb→network·lb→subnet의 EXTERNAL 측 optional. INTERNAL 측은 이번에 안 잰다
  (연쇄 자원이 길어 교란 위험 — 미측정으로 명시).

비용: e2-micro 수 분 + 포워딩 규칙 수 분 — 무시 규모. 이번 부트 디스크는
autoDelete=true를 명시한다(기본값 실측은 1라운드에서 끝났다 — 정리 단순화).

실행: `python run.py <project> <region> <zone>`
"""

import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GCLOUD = shutil.which("gcloud") or (
    Path.home() / "AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")
BASE = "https://compute.googleapis.com/compute/v1"


def token() -> str:
    return subprocess.run([str(GCLOUD), "auth", "print-access-token"],
                          capture_output=True, text=True, timeout=60).stdout.strip()


def call(method, url, body, tok):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def codes_of(doc):
    err = doc.get("error", {})
    out = [e.get("reason") or e.get("code") or "" for e in err.get("errors", [])]
    if err.get("status"):
        out.append(err["status"])
    return [c for c in dict.fromkeys(out) if c]


def wait_op(op, tok, timeout=300):
    link = op.get("selfLink")
    end = time.time() + timeout
    while link and time.time() < end:
        _, cur = call("GET", link, None, tok)
        if cur.get("status") == "DONE":
            errs = [e.get("code", "") for e in
                    cur.get("error", {}).get("errors", [])]
            return {"ok": not errs, "errorCodes": errs,
                    "excerpt": json.dumps(cur.get("error", cur.get("status")),
                                          ensure_ascii=False)[:600]}
        time.sleep(3)
    return {"ok": False, "errorCodes": ["OPERATION_TIMEOUT"], "excerpt": ""}


def mutate(method, url, body, tok):
    status, doc = call(method, url, body, tok)
    if status >= 400:
        return {"ok": False, "errorCodes": codes_of(doc) or [str(status)],
                "httpStatus": status,
                "excerpt": json.dumps(doc, ensure_ascii=False)[:600]}
    if doc.get("kind", "").endswith("operation"):
        out = wait_op(doc, tok)
        out["httpStatus"] = status
        return out
    return {"ok": True, "errorCodes": [], "httpStatus": status,
            "excerpt": json.dumps(doc, ensure_ascii=False)[:300]}


def main() -> None:
    project, region, zone = sys.argv[1], sys.argv[2], sys.argv[3]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    r = f"{BASE}/projects/{project}/regions/{region}"
    z = f"{BASE}/projects/{project}/zones/{zone}"
    steps: dict[str, dict] = {}

    def step(name, result):
        steps[name] = result
        print(f"{name:30} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    def vm(name, nic):
        return {"name": name,
                "machineType": f"{z}/machineTypes/e2-micro",
                "disks": [{"boot": True, "autoDelete": True,
                           "initializeParams": {"sourceImage":
                               "projects/debian-cloud/global/images/family/debian-12"}}],
                "networkInterfaces": [nic]}

    step("G0.create-auto-network", mutate("POST", f"{g}/networks", {
        "name": "depkbg2-auto", "autoCreateSubnetworks": True}, tok))
    step("G1.create-custom-network", mutate("POST", f"{g}/networks", {
        "name": "depkbg2-cust", "autoCreateSubnetworks": False}, tok))
    step("G1.create-custom-subnet", mutate("POST", f"{r}/subnetworks", {
        "name": "depkbg2-sub", "ipCidrRange": "10.80.1.0/24",
        "network": f"{g}/networks/depkbg2-cust"}, tok))

    # A1 — custom 모드에서 서브넷 생략
    step("A1.nic-network-only-custom", mutate(
        "POST", f"{z}/instances",
        vm("depkbg2-x1", {"network": f"{g}/networks/depkbg2-cust"}), tok))
    # A2 — auto 모드에서 서브넷 생략
    step("A2.nic-network-only-auto", mutate(
        "POST", f"{z}/instances",
        vm("depkbg2-vm", {"network": f"{g}/networks/depkbg2-auto"}), tok))
    if steps["A2.nic-network-only-auto"]["ok"]:
        _, inst = call("GET", f"{z}/instances/depkbg2-vm", None, tok)
        filled = inst.get("networkInterfaces", [{}])[0].get("subnetwork", "")
        steps["A2.server-filled-subnetwork"] = {
            "ok": bool(filled), "errorCodes": [],
            "excerpt": filled[-120:]}
        print(f"{'A2.server-filled-subnetwork':30} {filled.rsplit('/', 1)[-1]}")
        step("A2.cleanup-vm", mutate(
            "DELETE", f"{z}/instances/depkbg2-vm", None, tok))

    # B — EXTERNAL 포워딩 규칙: network·subnetwork 없이
    step("B.create-targetpool", mutate("POST", f"{r}/targetPools", {
        "name": "depkbg2-tp"}, tok))
    step("B.create-ext-forwardingrule", mutate("POST", f"{r}/forwardingRules", {
        "name": "depkbg2-fr", "IPProtocol": "TCP", "portRange": "80",
        "target": f"{r}/targetPools/depkbg2-tp"}, tok))
    step("D.delete-forwardingrule", mutate(
        "DELETE", f"{r}/forwardingRules/depkbg2-fr", None, tok))
    step("D.delete-targetpool", mutate(
        "DELETE", f"{r}/targetPools/depkbg2-tp", None, tok))

    step("D.delete-custom-subnet", mutate(
        "DELETE", f"{r}/subnetworks/depkbg2-sub", None, tok))
    step("D.delete-custom-network", mutate(
        "DELETE", f"{g}/networks/depkbg2-cust", None, tok))
    step("D.delete-auto-network", mutate(
        "DELETE", f"{g}/networks/depkbg2-auto", None, tok))
    _, nets = call("GET", f"{g}/networks", None, tok)
    residual = [n["name"] for n in nets.get("items", [])
                if n["name"].startswith("depkbg2")]
    steps["residual"] = {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)}
    print(f"{'residual':30} {residual}")

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("gcp 2라운드 측정 기록 — NIC의 네트워크 모드 조건부 양상과 "
                  "EXTERNAL LB의 network/subnet 불참. INTERNAL LB 측은 "
                  "미측정으로 남긴다."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project, "region": region, "zone": zone,
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
