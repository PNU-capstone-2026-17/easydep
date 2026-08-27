"""gcp P5b 실행기 — REST 직접 호출로 후보를 판정한다.

설계 결정 둘, 둘 다 측정의 순수성 때문이다:

- **gcloud CLI를 쓰지 않는다.** CLI는 생략된 필드에 기본값(기본 네트워크·부트
  디스크)을 몰래 채워 넣는다 — 반사실 실험(B 없이 A)의 'B 없음'이 CLI 층에서
  소거된다. 토큰만 gcloud에서 받고 호출은 compute REST v1 원형으로 한다.
- **gcp에는 preflight 상당이 없다**(compute insert에 validateOnly 없음) —
  사다리가 스키마 → apply 두 층이다. 그 부재 자체가 CSP 색인 관측이다.

국면: A(존재·허상 — 실패 예상, 자원 무생성) → B(무료 사슬: 커스텀 네트워크·
서브넷·방화벽) → F0(e2-micro 인스턴스 — 유일한 유료 자원, 분 단위) →
C(생명주기 변이) → D(역순 정리 + 잔존 관측). azure 3라운드의 OS 디스크 잔존
관측과 짝을 맞춰, 인스턴스 삭제 후 부트 디스크가 남는지도 본다(autoDelete를
명시하지 않은 API 기본값의 실측).

실행: `python run.py <project> <region> <zone>`
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


def call(method: str, url: str, body: dict | None, tok: str) -> tuple[int, dict]:
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


def codes_of(doc: dict) -> list[str]:
    out = []
    err = doc.get("error", {})
    for e in err.get("errors", []):
        out.append(e.get("reason") or e.get("code") or "")
    if err.get("status"):
        out.append(err["status"])
    for e in (doc.get("error", {}).get("errors", [])
              if "kind" not in doc else []):
        pass
    return [c for c in dict.fromkeys(out) if c]


def wait_op(op: dict, tok: str, timeout: int = 240) -> dict:
    """비동기 연산을 끝까지 따라간다 — 거부가 연산 안에서 나기도 한다."""
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


def mutate(method: str, url: str, body: dict | None, tok: str) -> dict:
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
    net_url = f"{g}/networks/depkbg-net"
    sub_url = f"{r}/subnetworks/depkbg-subnet"
    steps: dict[str, dict] = {}

    def step(name: str, result: dict) -> None:
        steps[name] = result
        print(f"{name:30} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    fw_allow = [{"IPProtocol": "tcp", "ports": ["22"]}]

    # A — 존재·허상 (전부 거부 예상)
    step("A.subnet-omit-network", mutate("POST", f"{r}/subnetworks", {
        "name": "depkbg-x1", "ipCidrRange": "10.70.9.0/24"}, tok))
    step("A.subnet-dangling-network", mutate("POST", f"{r}/subnetworks", {
        "name": "depkbg-x2", "ipCidrRange": "10.70.9.0/24",
        "network": f"{g}/networks/depkbg-absent"}, tok))
    step("A.firewall-dangling-network", mutate("POST", f"{g}/firewalls", {
        "name": "depkbg-x3", "allowed": fw_allow,
        "network": f"{g}/networks/depkbg-absent"}, tok))
    # 방화벽에서 network 생략 → 스키마 서술대로 default 대체가 실제로 일어나나
    step("A.firewall-omit-network", mutate("POST", f"{g}/firewalls", {
        "name": "depkbg-x4", "allowed": fw_allow}, tok))
    if steps["A.firewall-omit-network"]["ok"]:
        step("A.cleanup-x4", mutate("DELETE", f"{g}/firewalls/depkbg-x4",
                                    None, tok))

    # B — 무료 사슬
    step("B.create-network", mutate("POST", f"{g}/networks", {
        "name": "depkbg-net", "autoCreateSubnetworks": False}, tok))
    step("B.create-subnet", mutate("POST", f"{r}/subnetworks", {
        "name": "depkbg-subnet", "ipCidrRange": "10.70.1.0/24",
        "network": net_url}, tok))
    step("B.create-firewall", mutate("POST", f"{g}/firewalls", {
        "name": "depkbg-fw", "allowed": fw_allow, "network": net_url}, tok))

    # A(계속) — 인스턴스 음성: 유효한 사슬을 참조 재료로 쓴다
    vm_base = {
        "name": "depkbg-vm",
        "machineType": f"{z}/machineTypes/e2-micro",
        "disks": [{"boot": True, "initializeParams": {
            "sourceImage": "projects/debian-cloud/global/images/family/debian-12"}}],
        "networkInterfaces": [{"subnetwork": sub_url}],
    }
    step("A.instance-omit-nic", mutate("POST", f"{z}/instances",
                                       {k: v for k, v in vm_base.items()
                                        if k != "networkInterfaces"}, tok))
    step("A.instance-omit-disks", mutate("POST", f"{z}/instances",
                                         {k: v for k, v in vm_base.items()
                                          if k != "disks"}, tok))
    step("A.instance-dangling-subnet", mutate("POST", f"{z}/instances", {
        **vm_base, "name": "depkbg-x5",
        "networkInterfaces": [{"subnetwork": f"{r}/subnetworks/depkbg-absent"}]},
        tok))

    # F0 — 실제 인스턴스 (autoDelete 명시 안 함 — 기본값 실측)
    step("F0.create-instance", mutate("POST", f"{z}/instances", vm_base, tok))

    # C — 생명주기 변이
    step("C.delete-subnet-in-use", mutate("DELETE", sub_url, None, tok))
    step("C.delete-network-in-use", mutate("DELETE", net_url, None, tok))
    step("C.delete-bootdisk-attached", mutate(
        "DELETE", f"{z}/disks/depkbg-vm", None, tok))

    # D — 역순 정리 + 잔존 관측
    step("D.delete-instance", mutate(
        "DELETE", f"{z}/instances/depkbg-vm", None, tok))
    status, disks = call("GET", f"{z}/disks", None, tok)
    left = [d["name"] for d in disks.get("items", [])
            if d["name"].startswith("depkbg")]
    steps["D.disks-after-delete"] = {"ok": True, "errorCodes": [],
                                     "excerpt": json.dumps(left)}
    print(f"{'D.disks-after-delete':30} {left}")
    for name in left:
        step(f"D.delete-disk.{name}", mutate(
            "DELETE", f"{z}/disks/{name}", None, tok))
    step("D.delete-firewall", mutate("DELETE", f"{g}/firewalls/depkbg-fw",
                                     None, tok))
    step("D.delete-subnet", mutate("DELETE", sub_url, None, tok))
    step("D.delete-network", mutate("DELETE", net_url, None, tok))
    _, nets = call("GET", f"{g}/networks", None, tok)
    residual = [n["name"] for n in nets.get("items", [])
                if n["name"].startswith("depkbg")]
    steps["residual"] = {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)}
    print(f"{'residual':30} {residual}")

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("gcp apply 측정 기록 — REST 직접 호출(gcloud CLI의 기본값 "
                  "주입을 배제). preflight 층 부재 자체가 CSP 색인 관측이다."),
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": project, "region": region, "zone": zone,
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
