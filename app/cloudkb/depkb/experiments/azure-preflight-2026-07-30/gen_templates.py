"""P5a 실험 템플릿 생성기 — 후보 간선의 반사실 질문을 ARM 템플릿으로 번역한다.

세 종류다:
- **생략(omit)**: B 슬롯 없이 A를 낸다 → preflight가 거부하면 "B는 생성 필수"의
  실물 증거. 통과는 증거가 아니다(preflight 커버리지 편향 — 계획 T7).
- **허상(dangling)**: 존재하지 않는 B의 id로 A를 낸다 → 참조 해석 강제의 증거.
- **대조군(control)**: 유효 템플릿과 독립 자원 — 하네스가 멀쩡한지, 그리고
  "B 없이도 되는" 자원이 실제로 통과하는지.

apiVersion은 2026-07-30에 `az provider show`로 받은 실값이다.
"""

import json
from pathlib import Path

NET, CMP, DSK = "2026-01-01", "2026-04-01", "2026-03-02"
LOC = "[resourceGroup().location]"
OUT = Path(__file__).resolve().parent / "templates"


def tpl(*resources):
    return {
        "$schema": ("https://schema.management.azure.com/schemas/2019-04-01/"
                    "deploymentTemplate.json#"),
        "contentVersion": "1.0.0.0",
        "resources": list(resources),
    }


def vnet(name, subnets):
    p = {"addressSpace": {"addressPrefixes": ["10.10.0.0/16"]}}
    if subnets is not None:
        p["subnets"] = subnets
    return {"type": "Microsoft.Network/virtualNetworks", "apiVersion": NET,
            "name": name, "location": LOC, "properties": p}


def nic(name, ipconfigs):
    p = {} if ipconfigs is None else {"ipConfigurations": ipconfigs}
    return {"type": "Microsoft.Network/networkInterfaces", "apiVersion": NET,
            "name": name, "location": LOC, "properties": p}


def vm(name, network_profile):
    p = {
        # Standard_B1s는 이 구독·리전에서 SkuNotAvailable — 의존 검사에 못 닿았다.
        # 가용 SKU는 `az vm list-skus`로 확인(findings.md §3).
        "hardwareProfile": {"vmSize": "Standard_B2ats_v2"},
        "storageProfile": {
            "imageReference": {"publisher": "Canonical",
                               "offer": "ubuntu-24_04-lts",
                               "sku": "server", "version": "latest"},
            "osDisk": {"createOption": "FromImage"},
        },
        "osProfile": {"computerName": name, "adminUsername": "azureuser",
                      "adminPassword": "Depkb-P5a-2026!"},
    }
    if network_profile is not None:
        p["networkProfile"] = network_profile
    return {"type": "Microsoft.Compute/virtualMachines", "apiVersion": CMP,
            "name": name, "location": LOC, "properties": p}


ABSENT_SUBNET = ("[resourceId('Microsoft.Network/virtualNetworks/subnets', "
                 "'depkb-absent-vnet', 'absent-subnet')]")
ABSENT_NIC = ("[resourceId('Microsoft.Network/networkInterfaces', "
              "'depkb-absent-nic')]")
ABSENT_PIP = ("[resourceId('Microsoft.Network/publicIPAddresses', "
              "'depkb-absent-pip')]")

TEMPLATES = {
    # 대조군
    "control-vnet-valid": tpl(vnet("depkb-vnet", [
        {"name": "s1", "properties": {"addressPrefix": "10.10.1.0/24"}}])),
    "control-pip-alone": tpl({
        "type": "Microsoft.Network/publicIPAddresses", "apiVersion": NET,
        "name": "depkb-pip1", "location": LOC,
        "sku": {"name": "Standard"},
        "properties": {"publicIPAllocationMethod": "Static"}}),
    "control-nsg-alone": tpl({
        "type": "Microsoft.Network/networkSecurityGroups", "apiVersion": NET,
        "name": "depkb-nsg1", "location": LOC, "properties": {}}),
    "control-disk-alone": tpl({
        "type": "Microsoft.Compute/disks", "apiVersion": DSK,
        "name": "depkb-disk1", "location": LOC,
        "sku": {"name": "Standard_LRS"},
        "properties": {"creationData": {"createOption": "Empty"},
                       "diskSizeGB": 4}}),
    # 생략 — 필수성
    "omit-vnet-subnet": tpl(vnet("depkb-vnet-nosub", None)),
    "omit-nic-subnet": tpl(nic("depkb-nic1", [
        {"name": "ipc", "properties": {}}])),
    "omit-nic-ipconfig": tpl(nic("depkb-nic2", None)),
    "omit-vm-nic": tpl(vm("depkb-vm1", None)),
    "omit-lb-frontend-ref": tpl({
        "type": "Microsoft.Network/loadBalancers", "apiVersion": NET,
        "name": "depkb-lb1", "location": LOC, "sku": {"name": "Standard"},
        "properties": {"frontendIPConfigurations": [
            {"name": "fe", "properties": {}}]}}),
    # 허상 — 참조 해석
    "dangling-nic-subnet": tpl(nic("depkb-nic3", [
        {"name": "ipc", "properties": {"subnet": {"id": ABSENT_SUBNET}}}])),
    "dangling-subnet-parent": tpl({
        "type": "Microsoft.Network/virtualNetworks/subnets", "apiVersion": NET,
        "name": "depkb-absent-vnet/depkb-orphan",
        "properties": {"addressPrefix": "10.20.1.0/24"}}),
    "dangling-vm-nic": tpl(vm("depkb-vm2", {
        "networkInterfaces": [{"id": ABSENT_NIC}]})),
    "dangling-lb-pip": tpl({
        "type": "Microsoft.Network/loadBalancers", "apiVersion": NET,
        "name": "depkb-lb2", "location": LOC, "sku": {"name": "Standard"},
        "properties": {"frontendIPConfigurations": [
            {"name": "fe", "properties": {
                "publicIPAddress": {"id": ABSENT_PIP}}}]}}),
}

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, body in TEMPLATES.items():
        (OUT / f"{name}.json").write_text(
            json.dumps(body, indent=1), encoding="utf-8")
    print(f"templates: {len(TEMPLATES)}")
