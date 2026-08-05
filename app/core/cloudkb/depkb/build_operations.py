"""연산 성질 산출물 — 자원 생성·삭제가 **동기인가 비동기인가**.

## 왜 claims가 아닌가

claims는 (간선 × CSP × 질문)이다 — 의존의 형식이다. 그런데 "이 자원은 만들고
나서 기다려야 한다"는 **자원 하나의 성질**이지 두 자원의 관계가 아니다. 억지로
넣으면 `required: true` 하나가 세 판정을 겸하다 어긋났던 그 실수의 반복이다.
그래서 별도 산출물이다.

## 왜 필요한가 (worked example이 찾은 공백 B)

`provision_view`의 `createOrder`를 그대로 실행하면 클러스터가 `CREATING`인 채
다음 단계를 시도한다. 실험은 매번 폴링했지만(`K2.cluster-active`) 그 사실이
뷰에 없었다. IaC 생성기가 그대로 물릴 자리다.

## 근거 — 새 측정이 아니라 기존 기록의 사영

우리 실험이 **어디서 폴링했는지**가 데이터다. 판정 규율:

- **비동기 확실**: 실험 기록에 **중간 상태**가 관측됐다(`CREATING`·
  `PROVISIONING`·`Creating`·`DELETING`…). 서버가 "아직"이라고 답한 실물이다.
- **대기함(약)**: 폴링은 했는데 중간 상태가 기록에 없다. 우리가 기다린 것이지
  기다려야 한다는 증명은 아니다 — 그렇게 적는다.
- **미표시**: 폴링하지 않았다. "동기"라고 단정하지 않는다 — 우리가 안 기다렸을
  뿐이고, 그래도 됐다는 것이 그 자원의 성질이라는 증명은 아니다.

`unknown`을 빈칸으로 뭉개지 않는 규율과 같다.

실행: `python -m app.core.cloudkb.depkb.build_operations` → `operations.json`
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_EXPERIMENTS = _HERE / "experiments"
_ARTIFACT = _HERE / "operations.json"

ASYNC_CONFIRMED = "async-confirmed"
WAITED = "waited"

#: 자원별 (CSP, 연산) → 관측 규칙. **우리 구성**이되 각 항목이 실험 스텝과
#: 완료 신호를 인용하고, 빌드가 그 스텝의 실재를 대조한다.
#: 중간 상태 문자열은 실험 원자료에서 관측된 것만 적는다.
OBSERVATIONS: tuple[dict, ...] = (
    # ── aws: 자원별로 갈린다 ──
    dict(csp="aws", resource="k8sCluster", op="create", status=ASYNC_CONFIRMED,
         doneSignal="cluster.status == ACTIVE", intermediate="CREATING",
         evidence=("aws-eks3-2026-07-31", "K2.cluster-active")),
    dict(csp="aws", resource="k8sCluster", op="delete", status=ASYNC_CONFIRMED,
         doneSignal="describe-cluster 실패(404)", intermediate="DELETING",
         evidence=("aws-eks3-2026-07-31", "F1.cluster-gone")),
    dict(csp="aws", resource="k8sNodeGroup", op="create", status=ASYNC_CONFIRMED,
         doneSignal="nodegroup.status == ACTIVE", intermediate="CREATING",
         evidence=("aws-ekspvc-2026-07-31", "N2.nodegroup-active")),
    dict(csp="aws", resource="vm", op="create", status=ASYNC_CONFIRMED,
         doneSignal="State.Name == running", intermediate="pending",
         evidence=("aws-sig4-2026-07-31", "R15.running")),
    dict(csp="aws", resource="vm", op="delete", status=ASYNC_CONFIRMED,
         doneSignal="State.Name == terminated", intermediate="shutting-down",
         evidence=("aws-sig4-2026-07-31", "T2.terminated")),
    dict(csp="aws", resource="fileSystem", op="create", status=ASYNC_CONFIRMED,
         doneSignal="LifeCycleState == available", intermediate="creating",
         evidence=("aws-fs-2026-07-31", "A2.filesystem-available")),
    dict(csp="aws", resource="customImage", op="create", status=ASYNC_CONFIRMED,
         doneSignal="Images[0].State == available", intermediate="pending",
         evidence=("aws-cimg-2026-07-31", "A3.ami-available")),
    dict(csp="aws", resource="vpn", op="create", status=ASYNC_CONFIRMED,
         doneSignal="VpcAttachments[0].State == attached",
         intermediate="attaching",
         evidence=("aws-vpn-2026-07-31", "A4.attached")),
    # ── azure: ARM이 provisioningState로 통일. CLI가 기본 대기라 우리가
    # --no-wait로 비동기를 **고른** 자리에서만 중간 상태가 보인다 ──
    dict(csp="azure", resource="k8sCluster", op="create", status=ASYNC_CONFIRMED,
         doneSignal="provisioningState == Succeeded", intermediate="Creating",
         evidence=("azure-aks2-2026-07-31", "A2.provisioning-final")),
    dict(csp="azure", resource="k8sCluster", op="delete", status=ASYNC_CONFIRMED,
         doneSignal="aks show 실패", intermediate="Deleting",
         evidence=("azure-aks3-2026-07-31", "F1.cluster-gone")),
    dict(csp="azure", resource="vpn", op="create", status=ASYNC_CONFIRMED,
         doneSignal="provisioningState == Succeeded", intermediate="Updating",
         evidence=("azure-vpn2-2026-07-31", "K2.vng-state")),
    # ── gcp: 모든 mutate가 Operation을 반환한다(구조적) ──
    dict(csp="gcp", resource="k8sCluster", op="create", status=ASYNC_CONFIRMED,
         doneSignal="status == RUNNING", intermediate="PROVISIONING",
         evidence=("gcp-gke2-2026-07-31", "G2.status-final")),
    dict(csp="gcp", resource="k8sCluster", op="delete", status=ASYNC_CONFIRMED,
         doneSignal="GET 404", intermediate="STOPPING",
         evidence=("gcp-gke3-2026-07-31", "F1.cluster-gone")),
    dict(csp="gcp", resource="vm", op="create", status=ASYNC_CONFIRMED,
         doneSignal="status == RUNNING", intermediate="PROVISIONING",
         evidence=("gcp-func-2026-07-31", "R2.running")),
    dict(csp="gcp", resource="fileSystem", op="create", status=ASYNC_CONFIRMED,
         doneSignal="state == READY", intermediate="CREATING",
         evidence=("gcp-vpn-fs-2026-07-31", "F10.ready-b")),
    dict(csp="gcp", resource="customImage", op="create", status=ASYNC_CONFIRMED,
         doneSignal="status == READY", intermediate="PENDING",
         evidence=("gcp-cimg-2026-07-31", "A3.image-ready")),
)

#: CSP 단위의 성질 — 자원별 항목으로는 안 보이는 것. **우리 해석**이고 근거를
#: 함께 적는다.
CSP_CHARACTER: dict[str, str] = {
    "aws": "자원별로 갈린다 — VPC·서브넷·IGW·보안그룹·IAM 역할은 폴링 없이 "
           "다음 단계로 갔고(실험 기록에 대기 루프가 없다), EKS·EC2·EFS·AMI·"
           "VPN attach는 폴링했다.",
    "azure": "ARM이 provisioningState로 통일한다. **az CLI가 기본으로 완료를 "
             "기다리므로** 우리가 --no-wait로 비동기를 고른 자리에서만 중간 "
             "상태가 관측된다 — 즉 '동기로 보이는 것'은 CLI의 성질이지 자원의 "
             "성질이 아니다.",
    "gcp": "**모든 mutate가 Operation을 반환한다**(구조적 비동기). 우리 헬퍼의 "
           "wait_op이 그것을 감추고 있어 호출부에서는 동기처럼 보인다.",
}


def _step_exists(experiment: str, step: str) -> bool:
    path = _EXPERIMENTS / experiment / "results.json"
    if not path.exists():
        return False
    doc = json.loads(path.read_text(encoding="utf-8"))
    return step in (doc.get("steps") or {})


def _intermediate_seen(experiment: str, token: str) -> bool:
    """중간 상태가 실험 원자료에 실제로 찍혔는가 — 비동기의 직접 증거."""
    path = _EXPERIMENTS / experiment / "results.json"
    if not path.exists():
        return False
    blob = path.read_text(encoding="utf-8")
    return bool(re.search(re.escape(token), blob))


def build() -> dict:
    rows = []
    for o in OBSERVATIONS:
        exp, step = o["evidence"]
        assert _step_exists(exp, step), f"{exp}/{step}: 인용한 스텝이 없다"
        seen = _intermediate_seen(exp, o["intermediate"])
        rows.append({
            "csp": o["csp"], "resource": o["resource"], "op": o["op"],
            # 중간 상태가 원자료에 없으면 확신을 낮춘다 — 우리가 기다린 것과
            # 기다려야 하는 것을 구별한다.
            "status": o["status"] if seen else WAITED,
            "doneSignal": o["doneSignal"],
            "intermediateObserved": o["intermediate"] if seen else None,
            "evidence": {"experiment": exp, "step": step},
        })
    rows.sort(key=lambda r: (r["csp"], r["resource"], r["op"]))
    counts: dict[str, int] = {}
    for r in rows:
        key = f"{r['csp']}.{r['status']}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "_note": (
            "자원 생성·삭제의 연산 성질 — claims(간선×CSP×질문)에 안 들어가는 "
            "'자원 하나의 성질'이라 따로 낸다. 근거는 새 측정이 아니라 실험 "
            "기록의 사영이고, 빌드가 인용 스텝의 실재와 중간 상태 관측을 "
            "대조한다. **미표시는 '동기'가 아니다** — 우리가 안 기다렸을 뿐이다."
        ),
        "statusMeaning": {
            ASYNC_CONFIRMED: "중간 상태가 실험 원자료에 관측됐다(서버가 '아직'을 "
                             "답한 실물)",
            WAITED: "우리가 폴링은 했으나 중간 상태 기록이 없다 — 기다려야 "
                    "한다는 증명은 아니다",
        },
        "cspCharacter": CSP_CHARACTER,
        "counts": counts,
        "operations": rows,
    }


if __name__ == "__main__":
    result = build()
    _ARTIFACT.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print("operations:", len(result["operations"]), "|", result["counts"])
