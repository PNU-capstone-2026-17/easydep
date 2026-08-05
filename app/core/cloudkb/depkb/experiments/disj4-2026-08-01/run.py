"""선언 술어의 배타성 4차 — 3차가 남긴 두 자리를 닫는다.

3차(`disj3-2026-08-01`)의 결과와 남은 것:

- **azure 미판정.** 셋 다 `SkuNotAvailable`로 죽었다 — VM 크기가 이 구독에서
  안 잡히는 것이라 **배타성과 무관한 이유**다. 앞선 라운드에서 실제로 쓴 크기
  (`Standard_B2s_v2`)로 다시 건다.
- **gcp는 수락됐는데 실물이 기존 디스크 하나뿐이었다** — 이미지가 조용히
  무시됐다. 그런데 **누가 무시했는지 안 갈렸다**: `gcloud`가 `--disk boot=yes`를
  보고 이미지를 안 보냈을 수도 있고, API가 받아서 무시했을 수도 있다.
  `--log-http`로 **요청 본문을 직접 본다** — 거기 `initializeParams`가 있으면
  API가 받은 것이고, 없으면 클라이언트가 버린 것이다.

**"조용히 무시된다"는 `Or`가 아니다.** `Or`는 둘 다 유효하다는 뜻이고, 이쪽은
한쪽이 사라진다 — 계획층에 주는 뜻이 다르다(둘 다 적으면 하나는 헛일이다).

실행: `python run.py`
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RG, LOC = "depkb-disj4", "koreacentral"
SIZE = "Standard_B2s_v2"   # lb-serve2에서 실제로 뜬 크기
ZONE = "asia-northeast3-a"
GDISK, GVM = "depkb-disj4-disk", "depkb-disj4-vm"
_CODE = re.compile(r'"code":\s*"([^"]+)"|\(([A-Za-z]+[A-Za-z0-9]*)\)'
                   r"|Invalid value for field '([^']+)'")


def _run(exe, args, timeout=600):
    path = shutil.which(exe)
    if not path:
        return {"ok": False, "errorCodes": ["NO_CLI"], "excerpt": f"{exe} 없음"}
    r = subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    return {"ok": r.returncode == 0, "errorCodes": list(dict.fromkeys(codes)),
            "excerpt": text.strip().replace("\r", "")[:600], "_full": text}


def az(a, **k):
    return _run("az", [*a, "--only-show-errors"], **k)


def gcloud(a, **k):
    return _run("gcloud", a, **k)


def vm_template(*, image: bool, disk: bool) -> dict:
    storage: dict = {"osDisk": {"createOption": "FromImage" if image else "Attach",
                                "managedDisk": {}}}
    if image:
        storage["imageReference"] = {"publisher": "Canonical",
                                     "offer": "ubuntu-24_04-lts",
                                     "sku": "server", "version": "latest"}
    if disk:
        storage["osDisk"]["managedDisk"] = {
            "id": "[resourceId('Microsoft.Compute/disks','depkb-disj4-osdisk')]"}
        storage["osDisk"]["osType"] = "Linux"
        if not image:
            storage["osDisk"]["createOption"] = "Attach"
    return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/"
                       "deploymentTemplate.json#",
            "contentVersion": "1.0.0.0",
            "resources": [{"type": "Microsoft.Compute/virtualMachines",
                           "apiVersion": "2024-07-01", "name": "d4-vm",
                           "location": LOC,
                           "properties": {
                               "hardwareProfile": {"vmSize": SIZE},
                               "storageProfile": storage,
                               "networkProfile": {"networkInterfaces": [
                                   {"id": "[resourceId('Microsoft.Network/"
                                          "networkInterfaces','d4-nic')]"}]}}}]}


def main() -> None:
    doc = {"_note": ("배타성 4차 — azure는 크기를 바꿔 재판정, gcp는 --log-http로 "
                     "**API가 무엇을 받았는지** 직접 본다(클라이언트가 버린 것과 "
                     "API가 무시한 것은 다른 사실이다)."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save():
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if not k.startswith("_")}
        save()
        print(f"{name:32} "
              f"{'/'.join(result['errorCodes']) or ('OK' if result['ok'] else 'FAIL')}",
              flush=True)
        return result

    # ── azure 재판정 ───────────────────────────────────────────────────────
    step("A0.create-rg", az(["group", "create", "-n", RG, "-l", LOC]))
    for label, kw in (("A1.both-image-and-disk", dict(image=True, disk=True)),
                      ("A2.image-only", dict(image=True, disk=False)),
                      ("A3.disk-only", dict(image=False, disk=True))):
        path = HERE / f"{label.split('.')[1]}.json"
        path.write_text(json.dumps(vm_template(**kw)), encoding="utf-8")
        step(label, az(["deployment", "group", "validate", "-g", RG,
                        "--template-file", str(path), "-o", "json"]))
    step("A9.delete-rg", az(["group", "delete", "-n", RG, "--yes", "--no-wait"]))

    # ── gcp: 요청 본문을 본다 ──────────────────────────────────────────────
    step("G0.create-disk", gcloud(
        ["compute", "disks", "create", GDISK, "--zone", ZONE, "--size", "10GB",
         "--image-family", "ubuntu-2404-lts-amd64", "--image-project",
         "ubuntu-os-cloud", "--format", "json", "--quiet"]))
    both = gcloud(["compute", "instances", "create", GVM, "--zone", ZONE,
                   "--machine-type", "e2-micro",
                   "--image-family", "ubuntu-2404-lts-amd64",
                   "--image-project", "ubuntu-os-cloud",
                   "--disk", f"name={GDISK},boot=yes,auto-delete=no",
                   "--log-http", "--format", "json", "--quiet"])
    step("G1.both-with-log-http", both)
    # 요청 본문에 initializeParams(=sourceImage 경로)가 실렸는가.
    body = both.get("_full", "")
    sent_image = "initializeParams" in body
    step("G2.request-carried-image", {
        "ok": True, "errorCodes": [],
        "excerpt": ("요청 본문에 initializeParams "
                    + ("있음 — **API가 둘 다 받았고 하나를 무시했다**"
                       if sent_image else
                       "없음 — **gcloud가 이미지를 안 보냈다**(클라이언트 층)"))})
    if both["ok"]:
        step("G3.instance-shape", gcloud(
            ["compute", "instances", "describe", GVM, "--zone", ZONE,
             "--format", "json(disks[].source,disks[].boot)", "--quiet"]))
        step("G4.delete-vm", gcloud(
            ["compute", "instances", "delete", GVM, "--zone", ZONE, "--quiet"]))
    step("G9.delete-disk", gcloud(
        ["compute", "disks", "delete", GDISK, "--zone", ZONE, "--quiet"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
