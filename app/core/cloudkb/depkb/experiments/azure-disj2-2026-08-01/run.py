"""선언 술어의 배타성 2차(azure) — **컨트롤 플레인에 직접 건다.**

1차(`azure-disj-2026-08-01`)는 **무효**였다. `az network lb create`에 `--subnet`과
`--public-ip-address`를 함께 주니 거부됐는데, 그 거부가

    ERROR: incorrect usage: --subnet NAME --vnet-name NAME | --subnet ID | ...

즉 **CLI 클라이언트층**이었다. 이 저장소의 오라클 서열은 컨트롤 플레인 > preflight >
스키마이고 클라이언트 거부는 그 안에 없다 — `aws k8sCluster→iamRole`에서 이미 같은
자리에 물렸고 그 노트에 적어 두었다.

그래서 여기서는 **ARM 템플릿을 배포**해 컨트롤 플레인이 직접 답하게 한다.
`frontendIPConfigurations[0].properties`에 `subnet`과 `publicIPAddress`를 **함께**
넣는다.

    거부되면   `OnlyOne` — 정확히 하나다 (그리고 거부 코드가 증거다)
    수락되면   `Or`      — 겹쳐도 된다. 그때는 실물이 둘 다 들고 있는지 본다

대조군은 1차가 이미 세웠다(`D2`·`D3` — 하나씩만 주면 선다). 여기서도 같은
템플릿으로 한쪽씩 배포해 이 경로에서도 대조가 서는지 확인한다.

실행: `python run.py`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
RG = "depkb-disj2"
LOC = "koreacentral"
VNET, SUBNET, PIP = "d2-vnet", "d2-subnet", "d2-pip"

_SUBNET_ID = ("[resourceId('Microsoft.Network/virtualNetworks/subnets',"
              f"'{VNET}','{SUBNET}')]")
_PIP_ID = f"[resourceId('Microsoft.Network/publicIPAddresses','{PIP}')]"


def lb_template(name: str, *, subnet: bool, pip: bool) -> dict:
    """LB 하나짜리 템플릿. 프런트엔드 속성에 무엇을 넣을지가 이 실험의 변수다."""
    props: dict = {}
    if subnet:
        props["subnet"] = {"id": _SUBNET_ID}
    if pip:
        props["publicIPAddress"] = {"id": _PIP_ID}
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/"
                   "deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": [{
            "type": "Microsoft.Network/loadBalancers",
            "apiVersion": "2023-09-01",
            "name": name,
            "location": LOC,
            "sku": {"name": "Standard"},
            "properties": {
                "frontendIPConfigurations": [
                    {"name": "fe", "properties": props}],
            },
        }],
    }


def main() -> None:
    doc = {"_note": ("선언 술어의 배타성 2차 — ARM 템플릿으로 컨트롤 플레인에 "
                     "직접 건다. 1차는 CLI 클라이언트 거부라 무효였다."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        save()
        codes = "/".join(result["errorCodes"]) or ("OK" if result["ok"] else "FAIL")
        print(f"{name:32} {codes}", flush=True)
        return result

    def deploy(step_name: str, lb: str, *, subnet: bool, pip: bool):
        path = HERE / f"{lb}.json"
        path.write_text(json.dumps(lb_template(lb, subnet=subnet, pip=pip)),
                        encoding="utf-8")
        return step(step_name, az(
            ["deployment", "group", "create", "-g", RG, "-n", lb,
             "--template-file", str(path), "-o", "json"], timeout=600))

    step("S1.create-rg", az(["group", "create", "-n", RG, "-l", LOC]))
    step("S2.create-vnet", az(
        ["network", "vnet", "create", "-g", RG, "-n", VNET,
         "--address-prefix", "10.91.0.0/16",
         "--subnet-name", SUBNET, "--subnet-prefix", "10.91.1.0/24"]))
    step("S3.create-pip", az(
        ["network", "public-ip", "create", "-g", RG, "-n", PIP,
         "--sku", "Standard", "--allocation-method", "Static"]))

    # ── 본 판정 ────────────────────────────────────────────────────────────
    both = deploy("E1.arm-both-subnet-and-pip", "lb-both", subnet=True, pip=True)
    # ── 대조군: 이 경로에서도 하나씩은 서는가 ────────────────────────────────
    deploy("E2.arm-subnet-only", "lb-sub", subnet=True, pip=False)
    deploy("E3.arm-pip-only", "lb-pip", subnet=False, pip=True)

    if both["ok"]:
        # 수락됐다면 **실물이 둘 다 들고 있는지** 본다 — 하나를 조용히 버렸다면
        # "겹쳐도 된다"가 아니라 "하나가 무시된다"이고, 그건 다른 사실이다.
        step("E4.both-shape", az(
            ["network", "lb", "frontend-ip", "show", "-g", RG,
             "--lb-name", "lb-both", "-n", "fe",
             "--query", "{subnet:subnet.id, pip:publicIPAddress.id}", "-o", "json"]))

    step("T1.delete-rg", az(["group", "delete", "-n", RG, "--yes", "--no-wait"]))
    step("T2.residual-groups", az(
        ["group", "list", "--query", "[?starts_with(name,'depkb')].name", "-o", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
