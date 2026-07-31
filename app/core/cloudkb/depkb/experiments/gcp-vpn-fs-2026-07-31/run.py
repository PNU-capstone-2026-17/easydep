"""VPN·Filestore 라운드(gcp) — 어휘 완결의 마지막 둘.

계획: `document/archive/remaining-rounds-plan-2026-07-31.md` §1·§2.

- VPN: network 없이 게이트웨이 거부 → 전용 network로 생성(양성) →
  게이트웨이 존재 중 network 삭제(생명주기). 터널은 범위 밖.
- Filestore: dns·fs 라운드가 거부 층까지만이었던 한계를 **양성 대조**로
  없앤다. BASIC_HDD 1TiB를 만들고 즉시 지운다(실비 $0.04 수준).

실행: `python run.py <project> <region> <zone>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent
FILE_API = "https://file.googleapis.com/v1"
NET = "depkbv-net"


def main() -> None:
    project, region, zone = sys.argv[1], sys.argv[2], sys.argv[3]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    r = f"{BASE}/projects/{project}/regions/{region}"
    net = f"{g}/networks/{NET}"
    gw = f"{r}/vpnGateways/depkbv-gw"
    fs_parent = f"{FILE_API}/projects/{project}/locations/{zone}/instances"
    doc = {"_note": ("vpn·fileSystem(gcp) — VPN 게이트웨이 존재·생명주기와 "
                     "Filestore 양성 대조(거부 층 한계 해소)."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def api(method, url, body=None):
        status, resp = call(method, url, body, tok)
        err = resp.get("error", {}) if isinstance(resp, dict) else {}
        codes = [e.get("reason", "") for e in err.get("errors", [])]
        if err.get("status"):
            codes.append(err["status"])
        if not codes and status >= 400:
            codes = [str(status)]
        return {"ok": status < 400, "httpStatus": status,
                "errorCodes": [c for c in codes if c],
                "excerpt": json.dumps(resp, ensure_ascii=False)[:400]}

    def step(name, result):
        steps[name] = result
        save()
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    # ── VPN ──
    step("V1.gateway-without-network", mutate(
        "POST", f"{r}/vpnGateways", {"name": "depkbv-bad"}, tok))
    step("R1.create-network", mutate("POST", f"{g}/networks", {
        "name": NET, "autoCreateSubnetworks": False}, tok))
    step("V2.create-gateway", mutate(
        "POST", f"{r}/vpnGateways",
        {"name": "depkbv-gw", "network": net}, tok))
    step("V3.gateway-shape", api("GET", gw))
    step("L1.delete-network-with-gateway", mutate("DELETE", net, None, tok))

    # ── Filestore 양성 대조 ──
    step("F1.create-filestore", api(
        "POST", f"{fs_parent}?instanceId=depkbv-fs",
        {"tier": "BASIC_HDD",
         "fileShares": [{"name": "depkbshare", "capacityGb": "1024"}],
         "networks": [{"network": NET, "modes": ["MODE_IPV4"]}]}))
    state = ""
    deadline = time.time() + 900
    while time.time() < deadline:
        status, got = call("GET", f"{fs_parent}/depkbv-fs", None, tok)
        state = got.get("state", f"http{status}")
        print(f"filestore: {state}", flush=True)
        if state in ("READY", "ERROR") or status >= 400:
            break
        time.sleep(30)
    step("F2.filestore-ready", {"ok": state == "READY", "errorCodes": [],
                                "excerpt": state})
    _, got = call("GET", f"{fs_parent}/depkbv-fs", None, tok)
    step("F3.filestore-shape", {
        "ok": True, "errorCodes": [],
        "excerpt": json.dumps({"networks": got.get("networks"),
                               "state": got.get("state")},
                              ensure_ascii=False)[:400]})
    step("F4.delete-network-with-filestore", mutate("DELETE", net, None, tok))
    step("F5.delete-filestore", api("DELETE", f"{fs_parent}/depkbv-fs"))
    gone = False
    deadline = time.time() + 900
    while time.time() < deadline:
        status, _ = call("GET", f"{fs_parent}/depkbv-fs", None, tok)
        if status == 404:
            gone = True
            break
        print("filestore deleting…", flush=True)
        time.sleep(30)
    step("F6.filestore-gone", {"ok": gone, "errorCodes": [],
                               "excerpt": "404" if gone else "timeout"})

    # ── 정리 ──
    step("T1.delete-gateway", mutate("DELETE", gw, None, tok))
    step("T2.delete-network", mutate("DELETE", net, None, tok))
    _, nets = call("GET", f"{g}/networks", None, tok)
    residual = [n["name"] for n in nets.get("items", [])
                if n["name"].startswith("depkbv")]
    step("T3.residual", {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)})
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
