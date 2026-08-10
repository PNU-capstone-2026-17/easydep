"""gcp 3라운드 — INTERNAL LB 측의 미측정 술어를 닫는다.

2라운드가 EXTERNAL 측(network·subnetwork 불참)만 쟀다. 여기서는 INTERNAL
포워딩 규칙 사슬(healthCheck → 지역 backendService → FR)을 세워:

- **I1** INTERNAL FR에서 subnetwork 생략 → 거부 예상. lb→subnet의
  "INTERNAL에선 필수" 술어 반쪽을 실측으로 채운다.
- **I2** 같은 FR을 subnetwork까지 채워 생성 → 성공(양성 대조) → 삭제.

전부 무료(또는 분 단위 소액 — FR 시간당 소액). 실행: `python run.py <project> <region>`
"""

import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
GCLOUD = shutil.which("gcloud") or (
    Path.home() / "AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")
BASE = "https://compute.googleapis.com/compute/v1"


def token() -> str:
    return subprocess.run([str(GCLOUD), "auth", "print-access-token"],
                          capture_output=True, text=True, timeout=60, check=False).stdout.strip()


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
    project, region = sys.argv[1], sys.argv[2]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    r = f"{BASE}/projects/{project}/regions/{region}"
    net = f"{g}/networks/depkbg3-net"
    sub = f"{r}/subnetworks/depkbg3-sub"
    steps: dict[str, dict] = {}

    def step(name, result):
        steps[name] = result
        print(f"{name:30} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    step("G.create-network", mutate("POST", f"{g}/networks", {
        "name": "depkbg3-net", "autoCreateSubnetworks": False}, tok))
    step("G.create-subnet", mutate("POST", f"{r}/subnetworks", {
        "name": "depkbg3-sub", "ipCidrRange": "10.85.1.0/24",
        "network": net}, tok))
    step("G.create-healthcheck", mutate("POST", f"{g}/healthChecks", {
        "name": "depkbg3-hc", "type": "TCP",
        "tcpHealthCheck": {"port": 80}}, tok))
    step("G.create-backendservice", mutate("POST", f"{r}/backendServices", {
        "name": "depkbg3-bs", "loadBalancingScheme": "INTERNAL",
        "protocol": "TCP", "healthChecks": [f"{g}/healthChecks/depkbg3-hc"],
        "network": net}, tok))

    fr = {"name": "depkbg3-fr", "loadBalancingScheme": "INTERNAL",
          "IPProtocol": "TCP", "ports": ["80"],
          "backendService": f"{r}/backendServices/depkbg3-bs",
          "network": net}
    step("I1.internal-fr-omit-subnet", mutate(
        "POST", f"{r}/forwardingRules", fr, tok))
    step("I2.internal-fr-full", mutate(
        "POST", f"{r}/forwardingRules", {**fr, "subnetwork": sub}, tok))

    for name, url in [
        ("D.delete-fr", f"{r}/forwardingRules/depkbg3-fr"),
        ("D.delete-backendservice", f"{r}/backendServices/depkbg3-bs"),
        ("D.delete-healthcheck", f"{g}/healthChecks/depkbg3-hc"),
        ("D.delete-subnet", sub),
        ("D.delete-network", net),
    ]:
        step(name, mutate("DELETE", url, None, tok))
    _, nets = call("GET", f"{g}/networks", None, tok)
    residual = [n["name"] for n in nets.get("items", [])
                if n["name"].startswith("depkbg3")]
    steps["residual"] = {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)}
    print(f"{'residual':30} {residual}")

    (HERE / "results.json").write_text(json.dumps({
        "_note": "gcp 3라운드 — INTERNAL 포워딩 규칙의 subnetwork 필수성 측정.",
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": project, "region": region,
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
