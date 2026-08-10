"""gcp 4라운드 — INTERNAL FR의 network 생략(3라운드가 안 잰 반쪽).

3라운드는 subnetwork 생략만 쟀고 network는 늘 채워져 있었다. 여기서는
INTERNAL FR에 subnetwork만 주고 network를 생략한다 — 서버가 서브넷에서
네트워크를 역산하는가(nic→network처럼), 아니면 필수인가. 결과가 어느 쪽이든
gcp lb→network의 술어가 완성된다.

실행: `python run.py <project> <region>`
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> None:
    project, region = sys.argv[1], sys.argv[2]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    r = f"{BASE}/projects/{project}/regions/{region}"
    net = f"{g}/networks/depkbg4-net"
    sub = f"{r}/subnetworks/depkbg4-sub"
    steps: dict[str, dict] = {}

    def step(name, result):
        steps[name] = result
        print(f"{name:30} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    step("G.create-network", mutate("POST", f"{g}/networks", {
        "name": "depkbg4-net", "autoCreateSubnetworks": False}, tok))
    step("G.create-subnet", mutate("POST", f"{r}/subnetworks", {
        "name": "depkbg4-sub", "ipCidrRange": "10.86.1.0/24",
        "network": net}, tok))
    step("G.create-healthcheck", mutate("POST", f"{g}/healthChecks", {
        "name": "depkbg4-hc", "type": "TCP",
        "tcpHealthCheck": {"port": 80}}, tok))
    step("G.create-backendservice", mutate("POST", f"{r}/backendServices", {
        "name": "depkbg4-bs", "loadBalancingScheme": "INTERNAL",
        "protocol": "TCP", "healthChecks": [f"{g}/healthChecks/depkbg4-hc"],
        "network": net}, tok))

    step("I3.internal-fr-omit-network", mutate("POST", f"{r}/forwardingRules", {
        "name": "depkbg4-fr", "loadBalancingScheme": "INTERNAL",
        "IPProtocol": "TCP", "ports": ["80"],
        "backendService": f"{r}/backendServices/depkbg4-bs",
        "subnetwork": sub}, tok))
    if steps["I3.internal-fr-omit-network"]["ok"]:
        _, fr = call("GET", f"{r}/forwardingRules/depkbg4-fr", None, tok)
        filled = fr.get("network", "")
        steps["I3.server-filled-network"] = {
            "ok": bool(filled), "errorCodes": [], "excerpt": filled[-120:]}
        print(f"{'I3.server-filled-network':30} {filled.rsplit('/', 1)[-1]}")
        step("D.delete-fr", mutate("DELETE", f"{r}/forwardingRules/depkbg4-fr",
                                   None, tok))

    for name, url in [
        ("D.delete-backendservice", f"{r}/backendServices/depkbg4-bs"),
        ("D.delete-healthcheck", f"{g}/healthChecks/depkbg4-hc"),
        ("D.delete-subnet", sub),
        ("D.delete-network", net),
    ]:
        step(name, mutate("DELETE", url, None, tok))
    _, nets = call("GET", f"{g}/networks", None, tok)
    residual = [n["name"] for n in nets.get("items", [])
                if n["name"].startswith("depkbg4")]
    steps["residual"] = {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)}
    print(f"{'residual':30} {residual}")

    (HERE / "results.json").write_text(json.dumps({
        "_note": "gcp 4라운드 — INTERNAL FR의 network 생략(스킴 술어의 반쪽 완성).",
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": project, "region": region, "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
