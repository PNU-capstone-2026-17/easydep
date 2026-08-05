"""배타성 6차 — azure `vm→image`를 **apply로** 닫는다.

4차에서 azure는 셋 다 preflight 검증을 통과했다. 그런데 이 저장소의 규율은
분명하다: **preflight/스키마의 통과·침묵은 어떤 판정의 증거도 아니다**
(`build_claims` docstring). 거부만이 충분 증거이고, 통과는 apply로 확인해야 한다.

그래서 여기서는 **실제로 배포한다.**

    B1  imageReference + 기존 OS 디스크 attach를 **함께**
    B2  이미지만 (대조군)
    B3  기존 디스크만 (대조군)

    B1이 거부되면   `OnlyOne`
    B1이 성공하면   실물의 `storageProfile`을 보고 **어느 쪽으로 떴는지** 확인한다

## 준비물

붙일 **기존 OS 디스크**가 있어야 한다. VM을 하나 만들어 OS 디스크를 확보하고
(그 VM은 지운다 — azure는 VM을 지워도 OS 디스크가 남는 것이 실측돼 있다),
그 디스크를 attach 후보로 쓴다. 그 실측(`vm→disk` 생명주기)이 이 라운드의
준비 절차를 성립시킨다는 점이 재미있다.

## 비용

B2·B3가 성공하면 VM이 뜬다. 즉시 지운다(분 단위). B1이 목적이고 거부되면
그쪽은 무과금이다.

실행: `python run.py`
"""

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RG, LOC, SIZE = "depkb-disj6", "koreacentral", "Standard_B2s_v2"
SEED_VM = "depkb-disj6-seed"
_CODE = re.compile(r'"code":\s*"([^"]+)"|\(([A-Za-z]+[A-Za-z0-9]*)\)')


def az(args, timeout=900):
    path = shutil.which("az")
    r = subprocess.run([path, *args, "--only-show-errors"],
                       capture_output=True, text=True, timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    return {"ok": r.returncode == 0, "errorCodes": list(dict.fromkeys(codes)),
            "excerpt": text.strip().replace("\r", "")[:600], "_out": r.stdout}


def template(disk_id: str, *, image: bool, disk: bool, name: str) -> dict:
    storage: dict = {"osDisk": {}}
    if image and disk:
        # **둘 다**: 이미지에서 만들라고 하면서 기존 디스크를 가리킨다.
        storage["imageReference"] = {"publisher": "Canonical",
                                     "offer": "ubuntu-24_04-lts",
                                     "sku": "server", "version": "latest"}
        storage["osDisk"] = {"createOption": "FromImage", "osType": "Linux",
                             "managedDisk": {"id": disk_id}}
    elif image:
        storage["imageReference"] = {"publisher": "Canonical",
                                     "offer": "ubuntu-24_04-lts",
                                     "sku": "server", "version": "latest"}
        storage["osDisk"] = {"createOption": "FromImage"}
    else:
        storage["osDisk"] = {"createOption": "Attach", "osType": "Linux",
                             "managedDisk": {"id": disk_id}}
    props: dict = {"hardwareProfile": {"vmSize": SIZE},
                   "storageProfile": storage,
                   "networkProfile": {"networkInterfaces": [
                       {"id": f"[resourceId('Microsoft.Network/networkInterfaces',"
                              f"'{name}-nic')]"}]}}
    if image:
        # 이미지에서 만들 때만 OS 프로필이 필요하다(attach는 이미 설치돼 있다).
        props["osProfile"] = {"computerName": "d6", "adminUsername": "depkbadmin",
                              "adminPassword": "Depkb!Passw0rd#2026"}
    return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/"
                       "deploymentTemplate.json#",
            "contentVersion": "1.0.0.0",
            "resources": [
                {"type": "Microsoft.Network/networkInterfaces",
                 "apiVersion": "2023-09-01", "name": f"{name}-nic",
                 "location": LOC,
                 "properties": {"ipConfigurations": [{"name": "ipcfg", "properties": {
                     "subnet": {"id": "[resourceId('Microsoft.Network/"
                                      "virtualNetworks/subnets','d6-vnet','d6-sub')]"},
                     "privateIPAllocationMethod": "Dynamic"}}]}},
                {"type": "Microsoft.Compute/virtualMachines",
                 "apiVersion": "2024-07-01", "name": name, "location": LOC,
                 "dependsOn": [f"[resourceId('Microsoft.Network/networkInterfaces',"
                               f"'{name}-nic')]"],
                 "properties": props}]}


def main() -> None:
    doc = {"_note": ("배타성 6차 — azure vm→image를 **apply로** 닫는다. 4차는 "
                     "preflight 통과였고 통과는 이 저장소에서 증거가 아니다."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save():
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if not k.startswith("_")}
        save()
        print(f"{name:30} "
              f"{'/'.join(result['errorCodes']) or ('OK' if result['ok'] else 'FAIL')}",
              flush=True)
        return result

    step("S1.create-rg", az(["group", "create", "-n", RG, "-l", LOC]))
    step("S2.create-vnet", az(
        ["network", "vnet", "create", "-g", RG, "-n", "d6-vnet",
         "--address-prefix", "10.92.0.0/16", "--subnet-name", "d6-sub",
         "--subnet-prefix", "10.92.1.0/24"]))
    # 씨앗 VM — OS 디스크를 얻으려고 만든다.
    step("S3.seed-vm", az(
        ["vm", "create", "-g", RG, "-n", SEED_VM, "--image",
         "Canonical:ubuntu-24_04-lts:server:latest", "--size", SIZE,
         "--vnet-name", "d6-vnet", "--subnet", "d6-sub",
         "--admin-username", "depkbadmin", "--generate-ssh-keys",
         "--public-ip-address", "", "--nsg", "", "-o", "json"]))
    disk_id = ""
    got = step("S4.seed-disk-id", az(
        ["vm", "show", "-g", RG, "-n", SEED_VM,
         "--query", "storageProfile.osDisk.managedDisk.id", "-o", "tsv"]))
    disk_id = (got.get("_out") or "").strip()
    # VM만 지운다 — OS 디스크는 남는다(vm→disk 생명주기 실측).
    step("S5.delete-seed-vm", az(["vm", "delete", "-g", RG, "-n", SEED_VM, "--yes"]))

    for label, kw in (("B1.apply-both", dict(image=True, disk=True)),
                      ("B2.apply-image-only", dict(image=True, disk=False)),
                      ("B3.apply-disk-only", dict(image=False, disk=True))):
        name = f"d6-{label.split('.')[0].lower()}"
        path = HERE / f"{name}.json"
        path.write_text(json.dumps(template(disk_id, name=name, **kw)),
                        encoding="utf-8")
        r = step(label, az(["deployment", "group", "create", "-g", RG, "-n", name,
                            "--template-file", str(path), "-o", "json"]))
        if r["ok"]:
            step(f"{label}.shape", az(
                ["vm", "show", "-g", RG, "-n", name, "--query",
                 "storageProfile.{img:imageReference.sku, osdisk:osDisk.managedDisk.id,"
                 "create:osDisk.createOption}", "-o", "json"]))
            step(f"{label}.cleanup", az(["vm", "delete", "-g", RG, "-n", name, "--yes"]))
            time.sleep(5)

    step("T1.delete-rg", az(["group", "delete", "-n", RG, "--yes", "--no-wait"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
