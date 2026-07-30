"""AKS 생명주기 라운드 — 사용자 서브넷 위 클러스터로 사용 중 삭제를 잰다.

앞 라운드(azure-aks2)는 서브넷 없이 만들어 서비스가 vnet을 합성했고, 그 vnet은
노드 리소스 그룹에 있어 우리 손으로 지우는 실험 대상이 아니었다. 여기서는
**우리가 만든 서브넷**을 클러스터에 붙여(vnet 통합) 그 서브넷·vnet의 삭제를
시도한다 — aws·gcp에서 확인된 삭제 보호가 azure k8s에도 있는가.

kubenet이 아니라 azure CNI 경로이므로 서브넷은 넉넉히 잡는다(/24).

국면: kickoff → continue → life → finish. 실행: `python run.py <phase> <rg>`
"""

import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
AZ = shutil.which("az")
VM_SIZE = "Standard_B2s_v2"  # 한 곳에서만 정한다(aks2의 결함 교훈)


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("AKS 생명주기 — 사용자 서브넷 위 클러스터로 사용 중 삭제를 "
                      "잰다(앞 라운드는 서비스 합성 vnet이라 대상이 아니었다)."),
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

    if phase == "kickoff":
        step("R.create-vnet", az(
            ["network", "vnet", "create", "-g", rg, "-n", "depkb-aks3-vnet",
             "--address-prefix", "10.97.0.0/16", "--subnet-name", "nodes",
             "--subnet-prefix", "10.97.0.0/24", "-o", "json"]))
        # **발췌를 파싱하지 않는다** — 600자에서 잘려 죽는다(aws 라운드의 교훈이
        # 여기 남아 있었다). id만 tsv로 따로 받는다.
        got = step("R.subnet-id", az(
            ["network", "vnet", "subnet", "show", "-g", rg,
             "--vnet-name", "depkb-aks3-vnet", "-n", "nodes",
             "--query", "id", "-o", "tsv"]))
        ids["subnetId"] = got["excerpt"].strip()
        save(doc)
        step("K1.create-cluster-nowait", az(
            ["aks", "create", "-g", rg, "-n", "depkb-aks3",
             "--node-count", "1", "--node-vm-size", VM_SIZE,
             "--vnet-subnet-id", ids["subnetId"], "--no-ssh-key",
             "--no-wait"], timeout=300))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 540
        while time.time() < deadline:
            r = az(["aks", "show", "-g", rg, "-n", "depkb-aks3",
                    "--query", "provisioningState", "-o", "tsv"])
            state = r["excerpt"].strip()
            print(f"provisioningState: {state}", flush=True)
            if state in ("Succeeded", "Failed", "Canceled"):
                break
            time.sleep(30)
        step("K2.provisioning-final", {"ok": state == "Succeeded",
                                       "errorCodes": [] if state == "Succeeded" else [state],
                                       "excerpt": state})
        return

    if phase == "life":
        step("L1.delete-subnet-in-use", az(
            ["network", "vnet", "subnet", "delete", "-g", rg,
             "--vnet-name", "depkb-aks3-vnet", "-n", "nodes"]))
        step("L2.delete-vnet-in-use", az(
            ["network", "vnet", "delete", "-g", rg, "-n", "depkb-aks3-vnet"]))
        step("L3.delete-cluster-nowait", az(
            ["aks", "delete", "-g", rg, "-n", "depkb-aks3", "--yes", "--no-wait"]))
        return

    if phase == "finish":
        gone = False
        deadline = time.time() + 540
        while time.time() < deadline:
            r = az(["aks", "show", "-g", rg, "-n", "depkb-aks3",
                    "--query", "provisioningState", "-o", "tsv"])
            if not r["ok"]:
                gone = True
                break
            print(f"deleting… {r['excerpt'].strip()[:30]}", flush=True)
            time.sleep(30)
        step("F1.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "gone" if gone else "timeout"})
        step("F2.delete-subnet", az(
            ["network", "vnet", "subnet", "delete", "-g", rg,
             "--vnet-name", "depkb-aks3-vnet", "-n", "nodes"]))
        step("F3.delete-vnet", az(
            ["network", "vnet", "delete", "-g", rg, "-n", "depkb-aks3-vnet"]))
        step("F4.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
