"""기능 신호 6(gcp) — 서비스 디스커버리: k8sService의 기능 면.

계획: `document/archive/functional-signals56-plan-2026-07-31.md`.
**이 라운드에서 유일하게 관측자 이미지가 필요한 자리다** — 컨테이너가
응답해야 "이름으로 찾아진다"를 잴 수 있다. agnhost는 k8s 공식 테스트
이미지이고 우리 워크로드가 아니라 관측 도구다(판정 note에 명시).

겨누는 것: `k8sService→k8sCluster`의 기능 면. Service를 지우면 클러스터
내부 DNS(CoreDNS)가 이름을 못 풀고 접속이 끊긴다. 존재 판정
(k8sService→loadBalancer)과 **다른 간선**이다 — 저쪽은 클라우드 LB 합성,
이쪽은 클러스터 안의 이름.

사다리: Pod+Service → 클라이언트 Pod에서 이름으로 접속 200 → **Service
삭제**(무방비) → 해석·접속 상실 → 재생성 → 회복.

국면: kickoff → continue → disc → finish.
실행: `python run.py <phase> <project> <region> <zone>`
"""

import base64
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent
GKE = "https://container.googleapis.com/v1"
KUBECTL = shutil.which("kubectl")
CA = HERE / "ca.crt"
NET, SUB, CLUSTER = "depkbsd-net", "depkbsd-sub", "depkbsd-gke"
#: 클라이언트 Pod 안에서 서비스 이름으로 접속. `-sf`의 rc를 직접 본다
#: (파이프로 감싸면 종료 코드가 삼켜진다 — 신호 4종 라운드의 교훈).
CURL_SVC = "curl -sf --max-time 5 http://depkb-svc/hostname"


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("기능 신호 6(gcp) — 서비스 디스커버리. agnhost는 "
                      "애플리케이션이 아니라 관측자다."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ids": {}, "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    phase, project, region, zone = sys.argv[1:5]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    r = f"{BASE}/projects/{project}/regions/{region}"
    net, sub = f"{g}/networks/{NET}", f"{r}/subnetworks/{SUB}"
    cluster = f"{GKE}/projects/{project}/zones/{zone}/clusters/{CLUSTER}"
    doc = load()
    steps, ids = doc["steps"], doc["ids"]

    def step(name, result):
        steps[name] = result
        save(doc)
        print(f"{name:38} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    def gke(method, url, body=None):
        status, resp = call(method, url, body, tok)
        return {"ok": status < 400, "httpStatus": status,
                "errorCodes": [] if status < 400 else [str(status)],
                "excerpt": json.dumps(resp, ensure_ascii=False)[:400]}

    def kc(args, timeout=120):
        pre = ["--server", f"https://{ids['endpoint']}",
               "--certificate-authority", str(CA), "--token", token()]
        p = subprocess.run([KUBECTL, *pre, *args],
                           capture_output=True, text=True, timeout=timeout)
        text = (p.stderr or "") + (p.stdout or "")
        return {"ok": p.returncode == 0,
                "errorCodes": [] if p.returncode == 0 else [f"EXIT_{p.returncode}"],
                "excerpt": text.strip().replace("\r", "")[:500]}

    def in_client(cmd, timeout=90):
        return kc(["exec", "depkb-client", "--", "sh", "-c", cmd],
                  timeout=timeout)

    def disc_probe(want: bool, budget: int, confirm: int = 1) -> dict:
        deadline = time.time() + budget
        tries = streak = 0
        last = {}
        while time.time() < deadline:
            tries += 1
            last = in_client(CURL_SVC)
            got = last["ok"]
            streak = streak + 1 if got == want else 0
            print(f"svc-disc ok={got} want={want} streak={streak}", flush=True)
            if streak >= confirm:
                return {"ok": True, "errorCodes": [],
                        "excerpt": f"want={want} 도달 (시도 {tries}, 연속 "
                                   f"{streak}) out={last['excerpt'][:100]}"}
            time.sleep(10)
        return {"ok": False, "errorCodes": ["PROBE_TIMEOUT"],
                "excerpt": f"{budget}초 내 want={want}×{confirm} 미도달 — "
                           f"{last.get('excerpt', '')[:150]}"}

    if phase == "kickoff":
        step("G1.create-network", mutate("POST", f"{g}/networks", {
            "name": NET, "autoCreateSubnetworks": False}, tok))
        step("G2.create-subnet", mutate("POST", f"{r}/subnetworks", {
            "name": SUB, "ipCidrRange": "10.96.5.0/24", "network": net}, tok))
        step("K1.create-cluster", gke(
            "POST", f"{GKE}/projects/{project}/zones/{zone}/clusters",
            {"cluster": {"name": CLUSTER, "initialNodeCount": 1,
                         "network": NET, "subnetwork": SUB,
                         "nodeConfig": {"machineType": "e2-small",
                                        "diskSizeGb": 50}}}))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 900
        while time.time() < deadline:
            status, c = call("GET", cluster, None, tok)
            state = c.get("status", f"http{status}")
            print(f"status: {state}", flush=True)
            if state in ("RUNNING", "ERROR") or status >= 400:
                break
            time.sleep(30)
        step("K2.cluster-running", {"ok": state == "RUNNING",
                                    "errorCodes": [] if state == "RUNNING" else [state],
                                    "excerpt": state})
        _, c = call("GET", cluster, None, tok)
        ids["endpoint"] = c.get("endpoint", "")
        CA.write_bytes(base64.b64decode(
            c.get("masterAuth", {}).get("clusterCaCertificate", "")))
        save(doc)
        step("K3.apply-manifests", kc(
            ["apply", "-f", str(HERE / "manifests.yaml")]))
        ready = ""
        deadline = time.time() + 420
        while time.time() < deadline:
            r2 = kc(["get", "pods", "-o",
                     "jsonpath={range .items[*]}{.metadata.name}={.status.phase} {end}"])
            ready = r2["excerpt"].strip()
            print(f"pods: {ready}", flush=True)
            if ready.count("Running") >= 2:
                break
            time.sleep(15)
        step("K4.pods-running", {"ok": ready.count("Running") >= 2,
                                 "errorCodes": [], "excerpt": ready})
        return

    if phase == "disc":
        step("F1.discovery-works", disc_probe(True, 300))
        # M1 — 변이: Service 삭제(성공 = 무방비)
        step("M1.delete-service", kc(["delete", "svc", "depkb-svc"]))
        step("M1b.pods-still-running", kc(
            ["get", "pods", "-o",
             "jsonpath={range .items[*]}{.metadata.name}={.status.phase} {end}"]))
        step("F2.discovery-lost", disc_probe(False, 300, confirm=2))
        # M2 — 복원
        step("M2.recreate-service", kc(
            ["apply", "-f", str(HERE / "manifests.yaml")]))
        step("F3.discovery-again", disc_probe(True, 420))
        return

    if phase == "finish":
        step("T1.delete-cluster", gke("DELETE", cluster))
        gone = False
        deadline = time.time() + 900
        while time.time() < deadline:
            status, _ = call("GET", cluster, None, tok)
            if status == 404:
                gone = True
                break
            print("deleting…", flush=True)
            time.sleep(30)
        step("T2.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "404" if gone else "timeout"})
        step("T3.delete-subnet", mutate("DELETE", sub, None, tok))
        step("T4.delete-network", mutate("DELETE", net, None, tok))
        _, nets = call("GET", f"{g}/networks", None, tok)
        residual = [n["name"] for n in nets.get("items", [])
                    if n["name"].startswith("depkbsd")]
        step("T5.residual", {"ok": not residual, "errorCodes": [],
                             "excerpt": json.dumps(residual)})
        if CA.exists():
            CA.unlink()
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
