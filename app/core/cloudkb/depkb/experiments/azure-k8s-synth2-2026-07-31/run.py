"""k8s 합성 2라운드(azure) — Ingress(기본 구성)·RWX PVC.

계획: `document/archive/k8s-synthesis2-plan-2026-07-31.md`. 1라운드 하네스
재사용. 판정은 **관리형 기본 구성**에서만 유효하다.

국면: kickoff → continue → rwx → ing → finish.
실행: `python run.py <phase> <rg>`
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
CLUSTER = "depkb-synth2"


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
    return {"_note": ("합성 2라운드(azure) — Ingress 기본 구성·RWX PVC. "
                      "오라클은 컨트롤 플레인 열거."),
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
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    def storage_accounts():
        return az(["storage", "account", "list", "-g", ids["nodeRg"],
                   "--query", "[].name", "-o", "json"])

    if phase == "kickoff":
        step("K1.create-cluster-nowait", az(
            ["aks", "create", "-g", rg, "-n", CLUSTER,
             "--node-count", "1", "--node-vm-size", VM_SIZE,
             "--no-ssh-key", "--no-wait"], timeout=300))
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
        step("K4.get-credentials", az(
            ["aks", "get-credentials", "-g", rg, "-n", CLUSTER,
             "--file", str(KUBECONFIG), "--overwrite-existing"]))
        step("K5.ingressclasses", kc(["get", "ingressclass", "-o", "name"]))
        step("K6.storage-accounts-baseline", storage_accounts())
        return

    if phase == "rwx":
        step("P1.apply-rwx-pvc", kc(["apply", "-f", str(HERE / "rwx-pvc.yaml")]))
        bound = ""
        deadline = time.time() + 300
        while time.time() < deadline:
            r = kc(["get", "pvc", "depkb-synth2-rwx", "-o",
                    "jsonpath={.status.phase}"])
            bound = r["excerpt"].strip()
            print(f"pvc phase: {bound}", flush=True)
            if bound == "Bound":
                break
            time.sleep(15)
        # Immediate 바인딩 — Pod 트리거 없이 Bound가 되는지 자체가 관측
        step("P2.bound-without-pod", {"ok": bound == "Bound", "errorCodes": [],
                                      "excerpt": bound})
        step("P3.storage-accounts-after", storage_accounts())
        step("P4.pv-volumehandle-hint", kc(
            ["get", "pv", "-o", "jsonpath={range .items[*]}{.spec.csi.volumeHandle}\\n{end}"]))
        step("P5.delete-pvc", kc(["delete", "pvc", "depkb-synth2-rwx"],
                                 timeout=180))
        time.sleep(45)
        step("P6.storage-accounts-after-delete", storage_accounts())
        return

    if phase == "ing":
        step("I1.apply-np-svc", kc(["apply", "-f", str(HERE / "svc-np.yaml")]))
        step("I2.apply-ingress", kc(["apply", "-f", str(HERE / "ingress.yaml")]))
        time.sleep(120)
        step("I3.ingress-address-hint", kc(
            ["get", "ingress", "depkb-synth2-ing", "-o",
             "jsonpath=addr={.status.loadBalancer.ingress[0].ip}"]))
        step("I4.lb-after-ingress", az(
            ["network", "lb", "list", "-g", ids["nodeRg"],
             "--query", "[].{name:name,rules:length(loadBalancingRules)}",
             "-o", "json"]))
        step("I5.appgw-after-ingress", az(
            ["network", "application-gateway", "list", "-g", ids["nodeRg"],
             "--query", "[].name", "-o", "json"]))
        step("I6.delete-ingress", kc(["delete", "ingress", "depkb-synth2-ing",
                                      "--wait=false"]))
        step("I7.delete-np-svc", kc(["delete", "svc", "depkb-synth2-np"]))
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
        step("F3.residual-our-rg", az(["resource", "list", "-g", rg, "-o", "json"]))
        r = az(["group", "show", "-n", ids["nodeRg"], "-o", "json"])
        step("F4.node-rg-gone", {"ok": not r["ok"], "errorCodes": r["errorCodes"],
                                 "excerpt": "gone" if not r["ok"] else r["excerpt"][:200]})
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
