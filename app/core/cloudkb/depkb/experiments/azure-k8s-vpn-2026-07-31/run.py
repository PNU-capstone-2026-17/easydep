"""azure k8s·vpn 거부 라운드 — 자원 무생성, 생략·허상·이름 조건만 잰다.

- AKS는 az CLI가 노드풀·아이덴티티 기본값을 주입하므로 **az rest(관리 API
  직접)**로 잰다. 교란 격리를 위해 identity는 모든 시도에 준다(안 주면
  아이덴티티 오류가 노드풀 검사를 가린다 — SKU 교훈).
  - K1: agentPoolProfiles 생략 → k8sCluster→노드풀 필수성
  - K2: 허상 vnetSubnetID → 참조 해석
- VNG(vpn): **서브넷 이름 조건**(GatewaySubnet) — graphkb 소스 관측의 컨트롤
  플레인 확인. 이름이 다른 서브넷을 가리키는 게이트웨이 생성 시도 → 거부
  예상(검증 단계라 빠르고 자원 무생성). 재료 vnet·subnet·PIP는 무료·즉시 삭제.

실행: `python run.py <resource-group>`
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
AZ = shutil.which("az")


def save(steps):
    (HERE / "results.json").write_text(json.dumps({
        "_note": ("azure k8s·vpn 거부 라운드 — 자원 무생성(재료 vnet·PIP는 "
                  "생성 후 즉시 삭제). 생성 기반 실험(양성 대조·노드풀·생명주기)"
                  "은 후속 라운드."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    rg = sys.argv[1]
    steps: dict[str, dict] = {}

    def step(name, result):
        steps[name] = result
        save(steps)
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)

    sub = json.loads(subprocess.run(
        [AZ, "account", "show", "-o", "json"], capture_output=True,
        text=True).stdout)["id"]
    aks_api = subprocess.run(
        [AZ, "provider", "show", "--namespace", "Microsoft.ContainerService",
         "--query", "resourceTypes[?resourceType=='managedClusters']"
         ".apiVersions[0] | [0]", "-o", "tsv", "--only-show-errors"],
        capture_output=True, text=True).stdout.strip()
    steps["0.aks-api-version"] = {"ok": bool(aks_api), "errorCodes": [],
                                  "excerpt": aks_api}
    print(f"{'0.aks-api-version':34} {aks_api}", flush=True)

    aks_url = (f"https://management.azure.com/subscriptions/{sub}"
               f"/resourceGroups/{rg}/providers/Microsoft.ContainerService"
               f"/managedClusters/depkb-aks?api-version={aks_api}")

    def aks_put(body: dict) -> dict:
        return az(["rest", "--method", "put", "--url", aks_url,
                   "--body", json.dumps(body)], timeout=180)

    base = {"location": "koreacentral",
            "identity": {"type": "SystemAssigned"},
            "properties": {"dnsPrefix": "depkb"}}
    step("K1.aks-omit-agentpools", aks_put(base))

    absent_subnet = (f"/subscriptions/{sub}/resourceGroups/{rg}/providers"
                     f"/Microsoft.Network/virtualNetworks/depkb-absent-vnet"
                     f"/subnets/absent")
    step("K2.aks-dangling-subnet", aks_put({
        **base, "properties": {
            "dnsPrefix": "depkb",
            "agentPoolProfiles": [{
                "name": "np1", "count": 1, "vmSize": "Standard_B2s",
                "mode": "System", "vnetSubnetID": absent_subnet}]}}))

    # VNG — GatewaySubnet 이름 조건
    step("V0.create-vnet", az(
        ["network", "vnet", "create", "-g", rg, "-n", "depkb-vpn-vnet",
         "--address-prefix", "10.95.0.0/16", "--subnet-name", "sub1",
         "--subnet-prefix", "10.95.0.0/27", "-o", "json"]))
    step("V0.create-pip", az(
        ["network", "public-ip", "create", "-g", rg, "-n", "depkb-vpn-pip",
         "--sku", "Standard", "-o", "json"]))
    step("V1.vng-wrong-subnet-name", az(
        ["network", "vnet-gateway", "create", "-g", rg, "-n", "depkb-vng",
         "--vnet", "depkb-vpn-vnet", "--public-ip-address", "depkb-vpn-pip",
         "--sku", "VpnGw1", "--no-wait"], timeout=180))
    step("D.delete-pip", az(["network", "public-ip", "delete", "-g", rg,
                             "-n", "depkb-vpn-pip"]))
    step("D.delete-vnet", az(["network", "vnet", "delete", "-g", rg,
                              "-n", "depkb-vpn-vnet"]))
    step("residual", az(["resource", "list", "-g", rg, "-o", "json"]))


if __name__ == "__main__":
    main()
