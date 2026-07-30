"""AKS 생성 라운드 — 국면형(kickoff/continue/pools/finish), 10분 제한 대응.

측정 대상:
- **서브넷 생략 하 생성 성공**(kickoff→continue) → azure k8sCluster→subnet
  optional + **관리형 네트워크 합성 관측**(노드 리소스 그룹의 vnet — CB 드라이버
  합성과 같은 자리의 서버/서비스판).
- **노드풀 add/delete**(pools) → k8sNodeGroup의 독립 CRUD.
- **클러스터 삭제 캐스케이드**(pools→finish) — 노드풀·노드 RG가 함께 사라지는가.

각 국면은 결과를 results.json에 병합하고 상태를 출력한 뒤 끝난다 — 미완이면
같은 국면을 다시 부른다. 비용: B2s 1~2노드 × 총 20~30분.

실행: `python run.py {kickoff|continue|pools|finish} <resource-group>`
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
AZ = shutil.which("az")


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("AKS 생성 라운드 — 국면형. 서브넷 생략 생성·관리형 네트워크 "
                      "합성·노드풀 CRUD·삭제 캐스케이드."),
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
        step("A1.create-no-subnet-nowait", az(
            ["aks", "create", "-g", rg, "-n", "depkb-aks",
             "--node-count", "1", "--node-vm-size", "Standard_B2s_v2",
             "--no-ssh-key", "--no-wait"], timeout=300))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 480
        while time.time() < deadline:
            r = az(["aks", "show", "-g", rg, "-n", "depkb-aks",
                    "--query", "provisioningState", "-o", "tsv"])
            state = r["excerpt"].strip()
            print(f"provisioningState: {state}", flush=True)
            if state in ("Succeeded", "Failed", "Canceled"):
                break
            time.sleep(30)
        step("A2.provisioning-final", {"ok": state == "Succeeded",
                                       "errorCodes": [state] if state != "Succeeded" else [],
                                       "excerpt": state})
        if state == "Succeeded":
            info = az(["aks", "show", "-g", rg, "-n", "depkb-aks",
                       "--query", "{node:nodeResourceGroup, plugin:networkProfile.networkPlugin, "
                       "pools:length(agentPoolProfiles)}", "-o", "json"])
            step("A3.cluster-shape", info)
            node_rg = json.loads(info["excerpt"])["node"]
            vnets = az(["network", "vnet", "list", "-g", node_rg,
                        "--query", "[].name", "-o", "json"])
            step("A4.synthesized-vnets-in-node-rg", vnets)
        return

    if phase == "pools":
        step("B1.nodepool-add", az(
            ["aks", "nodepool", "add", "-g", rg, "--cluster-name", "depkb-aks",
             "-n", "np2", "--node-count", "1",
             "--node-vm-size", "Standard_B2s"], timeout=540))
        step("B2.nodepool-delete", az(
            ["aks", "nodepool", "delete", "-g", rg,
             "--cluster-name", "depkb-aks", "-n", "np2"], timeout=540))
        step("B3.cluster-delete-nowait", az(
            ["aks", "delete", "-g", rg, "-n", "depkb-aks", "--yes", "--no-wait"]))
        return

    if phase == "finish":
        deadline = time.time() + 480
        gone = False
        while time.time() < deadline:
            r = az(["aks", "show", "-g", rg, "-n", "depkb-aks",
                    "--query", "provisioningState", "-o", "tsv"])
            if not r["ok"] and any("NotFound" in c or "ResourceNotFound" in c
                                   for c in r["errorCodes"]):
                gone = True
                break
            print(f"deleting… {r['excerpt'].strip()[:40]}", flush=True)
            time.sleep(30)
        step("C1.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "NotFound" if gone else "timeout"})
        step("C2.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
