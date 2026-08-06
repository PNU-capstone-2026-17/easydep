"""선언 술어의 배타성 3차 — `vm→image`(azure·gcp).

`azure-disj2`가 `loadBalancer` 하나를 닫았고 여기서 **나머지 둘**을 닫는다.
질문은 같다: 부팅 원천을 **둘 다** 주면 어떻게 되는가.

    azure   `storageProfile`에 `imageReference`와 기존 OS 디스크 attach를 함께
    gcp     부트 디스크에 `initializeParams.sourceImage`와 기존 `source`를 함께

    거부되면   `OnlyOne` — 정확히 하나다
    수락되면   `Or`      — 겹쳐도 된다. 그때는 **실물이 어느 쪽으로 떴는지** 본다

## 오라클 규율

**CLI가 먼저 막으면 그건 판정이 아니다.** 이 축이 두 번 걸린 함정이라
(여정 문서 위협 ①), azure는 ARM 템플릿으로 컨트롤 플레인에 직접 건다.
gcp는 `gcloud`가 `--image`와 `--disk`를 함께 받으므로 그대로 쓰되, 거부가
나오면 **그 문구가 클라이언트인지 API인지 확인해서 적는다.**

## 비용

azure는 템플릿 **검증**(`deployment group validate`)이라 자원이 안 생긴다.
gcp는 dry-run이 없어 실제 생성 요청을 보낸다 — 거부되면 무과금이고, 수락되면
인스턴스가 뜨므로 즉시 지운다.

준비물이 필요하다: 붙일 **기존 디스크**. 그것만 만들고 지운다(분 단위).

실행: `python run.py`
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RG, LOC = "depkb-disj3", "koreacentral"
ZONE, REGION = "asia-northeast3-a", "asia-northeast3"
GDISK, GVM = "depkb-disj3-disk", "depkb-disj3-vm"
_CODE = re.compile(r'"code":\s*"([^"]+)"|\(([A-Za-z]+[A-Za-z0-9]*)\)'
                   r"|Invalid value for field '([^']+)'")


def _run(exe: str, args: list[str], timeout: int = 420) -> dict:
    path = shutil.which(exe)
    if not path:
        return {"ok": False, "errorCodes": ["NO_CLI"], "excerpt": f"{exe} 없음"}
    r = subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    return {"ok": r.returncode == 0, "errorCodes": list(dict.fromkeys(codes)),
            "excerpt": text.strip().replace("\r", "")[:600]}


def az(args, **kw):
    return _run("az", [*args, "--only-show-errors"], **kw)


def gcloud(args, **kw):
    return _run("gcloud", args, **kw)


def azure_vm_template(*, image: bool, disk: bool) -> dict:
    """VM 하나짜리 템플릿. `storageProfile`에 무엇을 넣을지가 변수다."""
    storage: dict = {"osDisk": {"createOption": "FromImage" if image else "Attach",
                                "managedDisk": {}}}
    if image:
        storage["imageReference"] = {
            "publisher": "Canonical", "offer": "ubuntu-24_04-lts",
            "sku": "server", "version": "latest"}
    if disk:
        storage["osDisk"]["managedDisk"] = {
            "id": "[resourceId('Microsoft.Compute/disks','depkb-disj3-osdisk')]"}
        storage["osDisk"]["osType"] = "Linux"
        if not image:
            storage["osDisk"]["createOption"] = "Attach"
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/"
                   "deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": [{
            "type": "Microsoft.Compute/virtualMachines",
            "apiVersion": "2024-07-01", "name": "d3-vm", "location": LOC,
            "properties": {
                "hardwareProfile": {"vmSize": "Standard_B1s"},
                "storageProfile": storage,
                "networkProfile": {"networkInterfaces": [{"id": "[resourceId("
                    "'Microsoft.Network/networkInterfaces','d3-nic')]"}]},
            },
        }],
    }


def main() -> None:
    doc = {"_note": ("선언 술어의 배타성 3차 — vm→image(azure·gcp). 부팅 원천을 "
                     "둘 다 주면 거부되는가. CLI 거부는 판정이 아니라는 규율을 "
                     "지킨다(위협 ①)."),
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
        print(f"{name:34} {codes}", flush=True)
        return result

    # ── azure: 템플릿 검증(자원 안 생김) ────────────────────────────────────
    step("A0.create-rg", az(["group", "create", "-n", RG, "-l", LOC]))
    for label, kw in (("A1.both-image-and-disk", dict(image=True, disk=True)),
                      ("A2.image-only", dict(image=True, disk=False)),
                      ("A3.disk-only", dict(image=False, disk=True))):
        path = HERE / f"{label.split('.')[1]}.json"
        path.write_text(json.dumps(azure_vm_template(**kw)), encoding="utf-8")
        step(label, az(["deployment", "group", "validate", "-g", RG,
                        "--template-file", str(path), "-o", "json"]))
    step("A9.delete-rg", az(["group", "delete", "-n", RG, "--yes", "--no-wait"]))

    # ── gcp: 실제 요청(거부 기대) ──────────────────────────────────────────
    step("G0.create-disk", gcloud(
        ["compute", "disks", "create", GDISK, "--zone", ZONE, "--size", "10GB",
         "--image-family", "ubuntu-2404-lts-amd64", "--image-project",
         "ubuntu-os-cloud", "--format", "json", "--quiet"]))
    step("G1.both-image-and-disk", gcloud(
        ["compute", "instances", "create", GVM, "--zone", ZONE,
         "--machine-type", "e2-micro",
         "--image-family", "ubuntu-2404-lts-amd64",
         "--image-project", "ubuntu-os-cloud",
         "--disk", f"name={GDISK},boot=yes,auto-delete=no",
         "--format", "json", "--quiet"]))
    if steps["G1.both-image-and-disk"]["ok"]:
        step("G2.instance-shape", gcloud(
            ["compute", "instances", "describe", GVM, "--zone", ZONE,
             "--format", "json(disks[].source,disks[].boot)", "--quiet"]))
        step("G3.delete-vm", gcloud(
            ["compute", "instances", "delete", GVM, "--zone", ZONE, "--quiet"]))
    step("G9.delete-disk", gcloud(
        ["compute", "disks", "delete", GDISK, "--zone", ZONE, "--quiet"]))
    step("T1.residual-gcp-vms", gcloud(
        ["compute", "instances", "list", "--format", "value(name)", "--quiet"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
