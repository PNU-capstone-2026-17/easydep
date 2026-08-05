"""VPN 게이트웨이 실생성 라운드 — 이름 조건의 양성 대조와 생명주기.

앞 라운드는 거부만 봤다(GatewaySubnet이 없을 때). 여기서는 정확히 그 이름의
서브넷을 만들어 게이트웨이가 **실제로 서는지**(이름 조건의 대우)와, 선 뒤에
그 서브넷·PIP를 지울 수 있는지(생명주기)를 잰다.

비용·시간: VpnGw1 게이트웨이는 생성 20~45분, 시간당 소액. 국면형으로 나눈다.

실행: `python run.py {kickoff|continue|life|finish} <resource-group>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("VPN 게이트웨이 실생성 — 이름 조건(GatewaySubnet)의 양성 "
                      "대조와 생명주기. 거부만 보면 대우를 모른다."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    phase, rg = sys.argv[1], sys.argv[2]
    doc = load()
    steps = doc["steps"]

    def step(name, result):
        steps[name] = result
        save(doc)
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    if phase == "kickoff":
        step("R.create-vnet", az(
            ["network", "vnet", "create", "-g", rg, "-n", "depkb-vpn2-vnet",
             "--address-prefix", "10.98.0.0/16", "--subnet-name", "GatewaySubnet",
             "--subnet-prefix", "10.98.0.0/27", "-o", "json"]))
        # AZ SKU 게이트웨이는 **zone이 구성된** PIP를 요구한다
        # (VmssVpnGatewayPublicIpsMustHaveZonesConfigured) — 쌍 호환 조건이고,
        # zone 없는 Standard PIP로 거부당한 1차 기록이 K0b에 있다.
        step("R.create-pip", az(
            ["network", "public-ip", "create", "-g", rg, "-n", "depkb-vpn2-pip",
             "--sku", "Standard", "--zone", "1", "2", "3", "-o", "json"]))
        step("K1.create-vng-nowait", az(
            ["network", "vnet-gateway", "create", "-g", rg, "-n", "depkb-vng2",
             "--vnet", "depkb-vpn2-vnet", "--public-ip-address", "depkb-vpn2-pip",
             "--gateway-type", "Vpn", "--vpn-type", "RouteBased",
             # koreacentral은 비-AZ SKU를 거부한다
             # (NonAzSkusNotAllowedForVPNGateway) — 리전 능력이 SKU를 제한하는
             # 조건이고, 그 자체가 관측이다(1차 시도 기록 참조).
             "--sku", "VpnGw1AZ", "--no-wait"], timeout=420))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 540
        while time.time() < deadline:
            r = az(["network", "vnet-gateway", "show", "-g", rg,
                    "-n", "depkb-vng2", "--query", "provisioningState",
                    "-o", "tsv"])
            state = r["excerpt"].strip()
            print(f"provisioningState: {state}", flush=True)
            if state in ("Succeeded", "Failed"):
                break
            time.sleep(45)
        step("K2.vng-state", {"ok": state == "Succeeded",
                              "errorCodes": [] if state == "Succeeded" else [state],
                              "excerpt": state})
        return

    if phase == "life":
        step("L1.delete-gatewaysubnet-in-use", az(
            ["network", "vnet", "subnet", "delete", "-g", rg,
             "--vnet-name", "depkb-vpn2-vnet", "-n", "GatewaySubnet"]))
        step("L2.delete-pip-in-use", az(
            ["network", "public-ip", "delete", "-g", rg, "-n", "depkb-vpn2-pip"]))
        step("L3.delete-vng-nowait", az(
            ["network", "vnet-gateway", "delete", "-g", rg, "-n", "depkb-vng2",
             "--no-wait"]))
        return

    if phase == "finish":
        gone = False
        deadline = time.time() + 540
        while time.time() < deadline:
            r = az(["network", "vnet-gateway", "show", "-g", rg,
                    "-n", "depkb-vng2", "--query", "provisioningState",
                    "-o", "tsv"])
            if not r["ok"]:
                gone = True
                break
            print(f"deleting… {r['excerpt'].strip()[:30]}", flush=True)
            time.sleep(45)
        step("F1.vng-gone", {"ok": gone, "errorCodes": [],
                             "excerpt": "gone" if gone else "timeout"})
        step("F2.delete-pip", az(
            ["network", "public-ip", "delete", "-g", rg, "-n", "depkb-vpn2-pip"]))
        step("F3.delete-vnet", az(
            ["network", "vnet", "delete", "-g", rg, "-n", "depkb-vpn2-vnet"]))
        step("F4.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
