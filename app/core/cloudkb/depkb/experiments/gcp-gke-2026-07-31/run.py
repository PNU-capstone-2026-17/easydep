"""gcp GKE 거부 라운드 — container API 직접, 자원 무생성.

GKE 클러스터 생성에서 허상 네트워크·허상 서브넷을 잰다(빠른 거부 예상).
network 생략은 default 대체가 예상되어 **생성이 실제로 시작되므로** 이 라운드에
넣지 않는다 — 생성 기반 라운드의 몫(gcloud CLI가 아니라 API 기본값 자체의 측정).

실행: `python run.py <project> <zone>`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import call, codes_of, token  # noqa: E402

HERE = Path(__file__).resolve().parent
BASE = "https://container.googleapis.com/v1"


def main() -> None:
    project, zone = sys.argv[1], sys.argv[2]
    tok = token()
    url = f"{BASE}/projects/{project}/zones/{zone}/clusters"
    steps: dict[str, dict] = {}

    def attempt(name, cluster):
        status, doc = call("POST", url, {"cluster": cluster}, tok)
        res = {"ok": status < 400, "httpStatus": status,
               "errorCodes": codes_of(doc) or ([] if status < 400 else [str(status)]),
               "excerpt": json.dumps(doc, ensure_ascii=False)[:400]}
        steps[name] = res
        print(f"{name:34} {'OK' if res['ok'] else '/'.join(res['errorCodes'])}",
              flush=True)
        return res

    attempt("G1.dangling-network", {
        "name": "depkb-gke", "initialNodeCount": 1,
        "network": "depkbg-absent-net"})
    attempt("G2.dangling-subnetwork", {
        "name": "depkb-gke", "initialNodeCount": 1,
        "network": "default", "subnetwork": "depkbg-absent-sub"})
    # 만약 위가 OK(비동기 수락)면 즉시 삭제해야 한다 — 수락 여부 자체가 발견
    for name in ("G1.dangling-network", "G2.dangling-subnetwork"):
        if steps[name]["ok"]:
            call("DELETE", f"{url}/depkb-gke", None, tok)
            steps[name]["excerpt"] += " | 수락됨 → 즉시 삭제 요청"

    status, doc = call("GET", url, None, tok)
    residual = [c.get("name") for c in doc.get("clusters", [])
                if str(c.get("name", "")).startswith("depkb")]
    steps["residual"] = {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)}
    print(f"{'residual':34} {residual}", flush=True)

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("GKE 거부 라운드 — container API 직접, 자원 무생성 목표. "
                  "network 생략(default 대체 예상)은 생성 기반 라운드의 몫."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project, "zone": zone, "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
