"""k8s 층 합성 라운드(azure) — Service→LB · PVC→디스크.

계획: `document/archive/k8s-synthesis-plan-2026-07-31.md`.
질문 넷: k8s 오브젝트 생성이 클라우드 실물(LB·디스크)을 합성하는가 ·
오브젝트 삭제가 그 실물을 정리하는가. **오라클은 클라우드 컨트롤 플레인
열거다** — kubectl 상태는 k8s 층의 주장이라 힌트로만 기록한다.

함정 반영: PVC 단독 관측을 Pod 트리거보다 먼저 둔다(WaitForFirstConsumer면
PVC만으론 디스크가 안 생긴다 — 그걸 "합성 없음"으로 읽으면 오판).
소멸 관측은 시한부 폴링 — 시한 내 미소멸은 잔존이 아니라 미판정.

국면: kickoff → continue → pvc → svc → life → finish.
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
KUBECONFIG = HERE / "kubeconfig"  # gitignore됨 — 자격증명
VM_SIZE = "Standard_B2s_v2"  # 한 곳에서만 정한다(aks2의 결함 교훈)
CLUSTER = "depkb-synth"


def kc(args: list[str], timeout: int = 120) -> dict:
    """kubectl 호출 — az 헬퍼와 같은 모양으로 기록한다."""
    r = subprocess.run([KUBECTL, "--kubeconfig", str(KUBECONFIG), *args],
                       capture_output=True, text=True, timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    return {"ok": r.returncode == 0, "errorCodes": [],
            "excerpt": text.strip().replace("\r", "")[:600]}


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("k8s 층 합성(azure) — Service→LB·PVC→디스크. 오라클은 "
                      "클라우드 컨트롤 플레인 열거, kubectl 상태는 힌트."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ids": {}, "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def disks_in(rg: str) -> dict:
    return az(["disk", "list", "-g", rg,
               "--query", "[].{name:name,size:diskSizeGB,state:diskState}",
               "-o", "json"])


def pvc_disks(rg: str) -> dict:
    """판정용 — 노드 OS 디스크도 이 RG의 managed disk라 전체 빈 목록을
    기다리면 영원히 안 온다. CSI가 만드는 디스크만(pvc- 접두) 센다."""
    return az(["disk", "list", "-g", rg,
               "--query", "[?starts_with(name, 'pvc-')].name", "-o", "json"])


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
        # 기본 StorageClass 실물 — volumeBindingMode·reclaimPolicy는 실측으로 적는다
        step("K5.default-storageclass", kc(
            ["get", "sc", "-o", "jsonpath={range .items[*]}{.metadata.name} "
             "default={.metadata.annotations.storageclass\\.kubernetes\\.io/is-default-class} "
             "bind={.volumeBindingMode} reclaim={.reclaimPolicy}\\n{end}"]))
        step("K6.disks-baseline", disks_in(ids["nodeRg"]))
        return

    if phase == "pvc":
        step("P1.apply-pvc", kc(["apply", "-f", str(HERE / "pvc.yaml")]))
        time.sleep(60)
        step("P2.pvc-status-alone", kc(
            ["get", "pvc", "depkb-synth-pvc", "-o",
             "jsonpath=phase={.status.phase}"]))
        # PVC 단독으로 디스크가 생겼는가 — WaitForFirstConsumer 실측
        step("P3.disks-after-pvc-alone", disks_in(ids["nodeRg"]))
        step("P3b.pvc-disks-alone", pvc_disks(ids["nodeRg"]))
        step("P4.apply-pod-trigger", kc(["apply", "-f", str(HERE / "pod.yaml")]))
        bound = ""
        deadline = time.time() + 360
        while time.time() < deadline:
            r = kc(["get", "pvc", "depkb-synth-pvc", "-o",
                    "jsonpath={.status.phase}"])
            bound = r["excerpt"].strip()
            print(f"pvc phase: {bound}", flush=True)
            if bound == "Bound":
                break
            time.sleep(20)
        step("P5.pvc-bound", {"ok": bound == "Bound", "errorCodes": [],
                              "excerpt": bound})
        step("P6.disks-after-pod", disks_in(ids["nodeRg"]))
        step("P6b.pvc-disks-after-pod", pvc_disks(ids["nodeRg"]))
        step("P7.pv-volumehandle-hint", kc(
            ["get", "pv", "-o", "jsonpath={range .items[*]}{.spec.csi.volumeHandle}\\n{end}"]))
        return

    if phase == "svc":
        step("S1.lb-baseline", az(
            ["network", "lb", "list", "-g", ids["nodeRg"],
             "--query", "[].{name:name,rules:length(loadBalancingRules)}",
             "-o", "json"]))
        step("S2.apply-svc", kc(["apply", "-f", str(HERE / "svc.yaml")]))
        ip = ""
        deadline = time.time() + 360
        while time.time() < deadline:
            r = kc(["get", "svc", "depkb-synth-svc", "-o",
                    "jsonpath={.status.loadBalancer.ingress[0].ip}"])
            ip = r["excerpt"].strip()
            print(f"svc ingress: {ip or '(pending)'}", flush=True)
            if ip:
                break
            time.sleep(20)
        step("S3.svc-ingress-hint", {"ok": bool(ip), "errorCodes": [],
                                     "excerpt": ip or "pending-timeout"})
        step("S4.lb-after-svc", az(
            ["network", "lb", "list", "-g", ids["nodeRg"],
             "--query", "[].{name:name,rules:length(loadBalancingRules),"
             "frontends:length(frontendIPConfigurations)}", "-o", "json"]))
        step("S5.pips-after-svc", az(
            ["network", "public-ip", "list", "-g", ids["nodeRg"],
             "--query", "[].{name:name,ip:ipAddress}", "-o", "json"]))
        return

    if phase == "life":
        step("L1.delete-svc", kc(["delete", "svc", "depkb-synth-svc"]))
        gone = False
        deadline = time.time() + 360
        while time.time() < deadline:
            r = az(["network", "lb", "list", "-g", ids["nodeRg"],
                    "--query", "[].loadBalancingRules[].name", "-o", "json"])
            rules = r["excerpt"].strip()
            print(f"lb rules: {rules[:60]}", flush=True)
            if r["ok"] and rules in ("[]", ""):
                gone = True
                break
            time.sleep(20)
        step("L2.lb-rules-after-delete", {"ok": gone, "errorCodes": [],
                                          "excerpt": "cleaned" if gone else "timeout-미판정"})
        step("L3.pips-after-delete", az(
            ["network", "public-ip", "list", "-g", ids["nodeRg"],
             "--query", "[].{name:name,ip:ipAddress}", "-o", "json"]))
        step("L4.delete-pod", kc(["delete", "pod", "depkb-synth-pod",
                                  "--wait=false"]))
        time.sleep(30)
        step("L5.delete-pvc", kc(["delete", "pvc", "depkb-synth-pvc"],
                                 timeout=180))
        gone = False
        deadline = time.time() + 360
        while time.time() < deadline:
            r = pvc_disks(ids["nodeRg"])
            body = r["excerpt"].strip()
            print(f"pvc-disks: {body[:60]}", flush=True)
            if r["ok"] and body == "[]":
                gone = True
                break
            time.sleep(20)
        step("L6.pvc-disks-after-delete", {"ok": gone, "errorCodes": [],
                                           "excerpt": "cleaned" if gone else "timeout-미판정"})
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
        # 노드 RG는 클러스터 삭제가 함께 지워야 정상 — show 실패가 곧 잔여 0
        r = az(["group", "show", "-n", ids["nodeRg"], "-o", "json"])
        step("F4.node-rg-gone", {"ok": not r["ok"], "errorCodes": r["errorCodes"],
                                 "excerpt": "gone" if not r["ok"] else r["excerpt"][:200]})
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
