"""CSI RWX 재측정(azure) + AKS identity 관측 — 미해결 둘을 닫는다.

배경: 합성 2라운드에서 azurefile CSI가 스토리지 계정 합성에 실패했다
("authenticated requests are not permitted for non TLS protected (https)
endpoints"). 그 뒤 dns·fs 라운드에서 **Microsoft.Storage RP가 미등록**
이었다는 사실이 드러났고, 등록 후 사용자 직접 생성은 성공했다. 그래서
CSI 실패의 원인이 갈리지 않았다 — 여기서 **RP 등록 상태로 같은 PVC를
다시** 던져 가른다.

  - 이번에 Bound면: 원인은 RP 미등록이었다(CSI 경로는 정상).
  - 이번에도 같은 TLS 오류면: 원인은 CSI 경로/구독 정책이다.
  - 다른 오류면: 그대로 기록한다.

덤: AKS의 identity 실물(iamRole 대기열의 미판정)을 함께 관측한다.

국면: kickoff → continue → probe → finish. 실행: `python run.py <phase> <rg>`
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
KUBECTL = shutil.which("kubectl")
KUBECONFIG = HERE / "kubeconfig"  # gitignore됨
VM_SIZE = "Standard_B2s_v2"
CLUSTER = "depkb-csi2"


def kc(args: list[str], timeout: int = 120) -> dict:
    r = subprocess.run([KUBECTL, "--kubeconfig", str(KUBECONFIG), *args],
                       capture_output=True, text=True, timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    return {"ok": r.returncode == 0, "errorCodes": [],
            "excerpt": text.strip().replace("\r", "")[:600]}


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("CSI RWX 재측정 + AKS identity — RP 등록 후 같은 PVC를 "
                      "다시 던져 실패 원인을 가른다."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ids": {}, "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    phase, rg = sys.argv[1], sys.argv[2]
    doc = load()
    steps, ids = doc["steps"], doc["ids"]

    def step(name, result):
        steps[name] = result
        save(doc)
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    if phase == "kickoff":
        step("K0.storage-rp-state", az(
            ["provider", "show", "-n", "Microsoft.Storage",
             "--query", "registrationState", "-o", "tsv"]))
        step("K1.create-cluster-nowait", az(
            ["aks", "create", "-g", rg, "-n", CLUSTER, "--node-count", "1",
             "--node-vm-size", VM_SIZE, "--no-ssh-key", "--no-wait"],
            timeout=300))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 720
        while time.time() < deadline:
            r = az(["aks", "show", "-g", rg, "-n", CLUSTER,
                    "--query", "provisioningState", "-o", "tsv"])
            state = r["excerpt"].strip()
            print(f"provisioningState: {state}", flush=True)
            if state in ("Succeeded", "Failed", "Canceled"):
                break
            time.sleep(30)
        step("K2.provisioning-final", {"ok": state == "Succeeded",
                                       "errorCodes": [] if state == "Succeeded" else [state],
                                       "excerpt": state})
        got = step("K3.node-rg", az(
            ["aks", "show", "-g", rg, "-n", CLUSTER,
             "--query", "nodeResourceGroup", "-o", "tsv"]))
        ids["nodeRg"] = got["excerpt"].strip()
        save(doc)
        # iamRole 대기열: AKS identity 실물
        step("K4.aks-identity-shape", az(
            ["aks", "show", "-g", rg, "-n", CLUSTER, "--query",
             "{identity:identity.type,kubeletId:identityProfile.kubeletidentity."
             "resourceId}", "-o", "json"]))
        step("K5.get-credentials", az(
            ["aks", "get-credentials", "-g", rg, "-n", CLUSTER,
             "--file", str(KUBECONFIG), "--overwrite-existing"]))
        return

    if phase == "probe":
        step("P1.apply-rwx-pvc", kc(["apply", "-f", str(HERE / "rwx-pvc.yaml")]))
        bound = ""
        deadline = time.time() + 420
        while time.time() < deadline:
            r = kc(["get", "pvc", "depkb-csi2-rwx", "-o",
                    "jsonpath={.status.phase}"])
            bound = r["excerpt"].strip()
            print(f"pvc phase: {bound}", flush=True)
            if bound == "Bound":
                break
            time.sleep(20)
        step("P2.rwx-bound", {"ok": bound == "Bound",
                              "errorCodes": [] if bound == "Bound" else [bound],
                              "excerpt": bound})
        step("P3.rwx-events", kc(
            ["get", "events", "--field-selector",
             "involvedObject.name=depkb-csi2-rwx",
             "-o", "jsonpath={range .items[*]}{.reason}: {.message}\\n{end}"]))
        step("P4.storage-accounts-after", az(
            ["storage", "account", "list", "-g", ids["nodeRg"],
             "--query", "[].name", "-o", "json"]))
        step("P5.pv-volumehandle-hint", kc(
            ["get", "pv", "-o", "jsonpath={range .items[*]}{.spec.csi.volumeHandle}\\n{end}"]))
        step("P6.delete-pvc", kc(["delete", "pvc", "depkb-csi2-rwx",
                                  "--wait=false"], timeout=180))
        time.sleep(60)
        step("P7.storage-accounts-after-delete", az(
            ["storage", "account", "list", "-g", ids["nodeRg"],
             "--query", "[].name", "-o", "json"]))
        return

    if phase == "finish":
        step("F1.delete-cluster-nowait", az(
            ["aks", "delete", "-g", rg, "-n", CLUSTER, "--yes", "--no-wait"]))
        gone = False
        deadline = time.time() + 720
        while time.time() < deadline:
            r = az(["aks", "show", "-g", rg, "-n", CLUSTER,
                    "--query", "provisioningState", "-o", "tsv"])
            if not r["ok"]:
                gone = True
                break
            print(f"deleting… {r['excerpt'].strip()[:30]}", flush=True)
            time.sleep(30)
        step("F2.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "gone" if gone else "timeout"})
        step("F3.residual-our-rg", az(["resource", "list", "-g", rg,
                                       "-o", "json"]))
        r = az(["group", "show", "-n", ids["nodeRg"], "-o", "json"])
        step("F4.node-rg-gone", {"ok": not r["ok"], "errorCodes": r["errorCodes"],
                                 "excerpt": "gone" if not r["ok"] else r["excerpt"][:200]})
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
