"""기능 의존 2라운드(gcp) — vm→firewall(규칙)·network→기본 라우트.

계획: `document/archive/functional-dependency2-plan-2026-07-31.md`.
전용 네트워크 위에서 두 셀: (1) allow-22 방화벽 규칙 삭제/재생성 —
gcp 방화벽은 자원 간 부착이 아니라 **네트워크 스코프 규칙**이라 관계
변이가 없고 규칙 변이가 유일한 경로다(그 자체가 3사 차이의 기록).
(2) 네트워크의 default-route(0.0.0.0/0 → default-internet-gateway)
삭제/재생성. 재생성 라우트는 우리 이름 — 기준은 기능 회복이지 이름이 아니다.

실행: `python run.py <project> <region> <zone>`
"""

import json
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent
DEBIAN = "projects/debian-cloud/global/images/family/debian-12"
NET, SUB = "depkbf2-net", "depkbf2-sub"


def find_default_route(items: list[dict], network_name: str) -> dict | None:
    """API endpoint host와 무관하게 network의 IPv4 default route를 찾는다."""

    suffix = f"/networks/{network_name}"
    return next(
        (
            item
            for item in items
            if item.get("destRange") == "0.0.0.0/0"
            and item.get("network", "").rstrip("/").endswith(suffix)
        ),
        None,
    )


def tcp_ok(ip: str) -> bool:
    try:
        with socket.create_connection((ip, 22), timeout=5):
            return True
    except OSError:
        return False


def probe(ip: str, want: bool, budget: int, confirm: int = 1) -> dict:
    deadline = time.time() + budget
    tries = streak = 0
    while time.time() < deadline:
        tries += 1
        got = tcp_ok(ip)
        streak = streak + 1 if got == want else 0
        print(f"tcp {ip}:22 -> {got} (want {want}, streak {streak})", flush=True)
        if streak >= confirm:
            return {"ok": True, "errorCodes": [],
                    "excerpt": f"tcp22={got} (시도 {tries}, 연속 {streak})"}
        time.sleep(10)
    return {"ok": False, "errorCodes": ["PROBE_TIMEOUT"],
            "excerpt": f"tcp22가 {budget}초 내 {want}×{confirm}에 도달 못 함"}


def main() -> None:
    project, region, zone = sys.argv[1], sys.argv[2], sys.argv[3]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    r = f"{BASE}/projects/{project}/regions/{region}"
    z = f"{BASE}/projects/{project}/zones/{zone}"
    net, sub = f"{g}/networks/{NET}", f"{r}/subnetworks/{SUB}"
    inst = f"{z}/instances/depkbf2-vm"
    doc = {"_note": ("기능 의존 2라운드(gcp) — 방화벽 규칙·기본 라우트 "
                     "삭제/복원. 기능 신호 = 로컬 TCP 22."),
           "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
           "ids": {}, "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = result
        save()
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    fw_body = {"name": "depkbf2-allow22", "network": net,
               "direction": "INGRESS", "sourceRanges": ["0.0.0.0/0"],
               "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}]}

    step("R1.create-network", mutate("POST", f"{g}/networks", {
        "name": NET, "autoCreateSubnetworks": False}, tok))
    step("R2.create-subnet", mutate("POST", f"{r}/subnetworks", {
        "name": SUB, "ipCidrRange": "10.96.4.0/24", "network": net}, tok))
    step("R3.create-fw-allow22", mutate("POST", f"{g}/firewalls", fw_body, tok))
    step("R4.create-vm", mutate(
        "POST", f"{z}/instances",
        {"name": "depkbf2-vm", "machineType": f"{z}/machineTypes/e2-small",
         "disks": [{"boot": True, "autoDelete": True,
                    "initializeParams": {"sourceImage": DEBIAN,
                                         "diskSizeGb": "10"}}],
         "networkInterfaces": [{"network": net, "subnetwork": sub,
                                "accessConfigs": [{"type": "ONE_TO_ONE_NAT",
                                                   "name": "External NAT"}]}]},
        tok))
    state = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        _, got = call("GET", inst, None, tok)
        state = got.get("status", "")
        if state == "RUNNING":
            break
        time.sleep(10)
    step("R5.running", {"ok": state == "RUNNING", "errorCodes": [],
                        "excerpt": state})
    _, got = call("GET", inst, None, tok)
    ip = (got.get("networkInterfaces") or [{}])[0].get(
        "accessConfigs", [{}])[0].get("natIP", "")
    doc["ids"]["ip"] = ip
    save()

    step("F1.reachable-baseline", probe(ip, True, 300))
    # ── 셀 1: 방화벽 규칙 삭제(변이 성공 = 무방비) → 복원
    step("M1.delete-fw-rule", mutate(
        "DELETE", f"{g}/firewalls/depkbf2-allow22", None, tok))
    step("F2.unreachable-no-rule", probe(ip, False, 180, confirm=2))
    step("M2.recreate-fw-rule", mutate("POST", f"{g}/firewalls", fw_body, tok))
    step("F3.reachable-again", probe(ip, True, 300))
    # ── 셀 2: 기본 라우트 삭제 → 복원(우리 이름)
    # Compute API filter 문자열의 URL quoting 차이로 빈 목록이 된 적이 있어,
    # 전체 route를 받은 뒤 정확한 network selfLink와 목적 CIDR을 함께 비교한다.
    _, routes = call("GET", f"{g}/routes", None, tok)
    # API 응답은 www.googleapis.com, 요청은 compute.googleapis.com을 쓸 수
    # 있어 host까지 비교하지 않는다.
    default = find_default_route(routes.get("items", []), NET)
    step("M3a.find-default-route", {
        "ok": default is not None, "errorCodes": [],
        "excerpt": json.dumps({"name": default and default["name"],
                               "nextHop": default and default.get(
                                   "nextHopGateway", "")[-40:]},
                              ensure_ascii=False)})
    if default is None:
        raise RuntimeError("default route for the experiment network was not found")
    step("M3b.delete-default-route", mutate(
        "DELETE", f"{g}/routes/{default['name']}", None, tok))
    step("F4.unreachable-no-route", probe(ip, False, 180, confirm=2))
    step("M4.recreate-route", mutate("POST", f"{g}/routes", {
        "name": "depkbf2-default", "network": net, "destRange": "0.0.0.0/0",
        "nextHopGateway": f"{g}/gateways/default-internet-gateway"}, tok))
    step("F5.reachable-final", probe(ip, True, 300))

    # 정리
    step("T1.delete-vm", mutate("DELETE", inst, None, tok))
    gone = False
    deadline = time.time() + 300
    while time.time() < deadline:
        status, _ = call("GET", inst, None, tok)
        if status == 404:
            gone = True
            break
        time.sleep(15)
    step("T2.vm-gone", {"ok": gone, "errorCodes": [],
                        "excerpt": "404" if gone else "timeout"})
    step("T3.delete-fw", mutate(
        "DELETE", f"{g}/firewalls/depkbf2-allow22", None, tok))
    step("T4.delete-route", mutate(
        "DELETE", f"{g}/routes/depkbf2-default", None, tok))
    step("T5.delete-subnet", mutate("DELETE", sub, None, tok))
    step("T6.delete-network", mutate("DELETE", net, None, tok))
    _, nets = call("GET", f"{g}/networks", None, tok)
    residual = [n["name"] for n in nets.get("items", [])
                if n["name"].startswith("depkbf2")]
    step("T7.residual", {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)})
    doc["finishedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
