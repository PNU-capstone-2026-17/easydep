"""주장 산출물 — 스키마·실험의 모든 증거를 (간선 × CSP × 질문)으로 통합한다.

이것이 의존성 분석의 **결과물**이다. 후보(스키마 층)와 측정(실험 기록)은 재료고,
소비자(계획기·사람)가 읽는 것은 이 파일이다.

판정 어휘와 규율:

- `required` / `optional` / `holds`(lifecycle 제약 실재) — **apply 층 증거가
  있을 때만.** 단 preflight **거부**는 required의 충분 증거다(실물 컨트롤
  플레인의 답이라서). preflight/스키마 **통과·침묵은 어떤 판정의 증거도 아니다**
  — 그런 칸은 `unknown`으로 남는다(aws·gcp 전부가 지금 이 상태다 — 계정 없음,
  T9: 정적 상한).
- 실험 증거는 (실험 산출물, 스텝 키, 기대 코드)로 인용하고, **빌드가 그 스텝의
  실측 코드와 대조해 어긋나면 죽는다** — 판정이 측정에서 떨어져 나가는 것을
  기계가 막는다.
- 판정 배정 자체는 **우리 구성**이다(EXPERIMENT_JUDGMENTS). 표시하고, 근거 없는
  배정은 빌드가 거부한다.

실행: `python -m app.core.cloudkb.depkb.build_claims` → `claims.json`
"""

from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LAYER_RANK = {"schema": 0, "preflight": 1, "apply": 2}

#: 실험 판정표 — 우리 구성. ref = (실험 디렉터리, results.json의 스텝 키, 기대 코드
#: 또는 "ok"). 빌드가 실측과 대조한다.
EXPERIMENT_JUDGMENTS: list[dict] = [
    # ── existence (azure) ──
    dict(csp="azure", subject="nic", object="subnet", question="existence",
         verdict="required",
         evidence=[
             ("azure-preflight-2026-07-30", "omit-nic-subnet.validate",
              "SubnetIsRequired", "preflight"),
             ("azure-apply-2026-07-30", "A.apply.dangling-nic-subnet",
              "InvalidResourceReference", "apply"),
         ]),
    dict(csp="azure", subject="vm", object="nic", question="existence",
         verdict="required",
         evidence=[
             ("azure-apply-2026-07-30", "A.apply.omit-vm-nic",
              "InvalidParameter", "apply"),
             ("azure-apply-2026-07-30", "A.apply.dangling-vm-nic",
              "NotFound", "apply"),
         ],
         note="preflight는 침묵했다(Compute RP 깊이 — P5a findings §3)"),
    dict(csp="azure", subject="subnet", object="network", question="existence",
         verdict="required",
         evidence=[
             ("azure-apply-2026-07-30", "A.apply.dangling-subnet-parent",
              "ResourceNotFound", "apply"),
         ],
         note="경로 중첩(소속) — 부모 없이 만들 방법 자체가 없다"),
    dict(csp="azure", subject="nic", object="firewall", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply-2026-07-30", "B.build-chain", "ok", "apply"),
         ],
         note="사슬의 nic1이 NSG 없이 생성 성공 — 부재 하 성공이 optional의 증거"),
    dict(csp="azure", subject="loadBalancer", object="subnet|publicIp|publicIPPrefix",
         question="existence", verdict="required",
         predicate="disjunctive: 셋 중 하나",
         evidence=[
             ("azure-preflight-2026-07-30", "omit-lb-frontend-ref.validate",
              "FrontendIPConfigurationHasNoSubnetOrPublicIPAddressOrPublicIPPrefix",
              "preflight"),
             ("azure-apply-2026-07-30", "A.apply.dangling-lb-pip",
              "InvalidResourceReference", "apply"),
         ]),
    # ── existence 2라운드 (azure) ──
    dict(csp="azure", subject="network", object="subnet", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply2-2026-07-30", "E1.apply-vnet-without-subnet",
              "ok", "apply"),
         ],
         note="서브넷 없는 VNet이 실제로 만들어졌다 — 1라운드 preflight 통과는 "
              "증거가 아니었고 이것이 증거다"),
    dict(csp="azure", subject="nic", object="publicIp", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply-2026-07-30", "B.build-chain", "ok", "apply"),
         ],
         note="1라운드 nic1이 PIP 없이 생성 성공"),
    dict(csp="azure", subject="subnet", object="firewall", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply-2026-07-30", "B.build-chain", "ok", "apply"),
         ],
         note="1라운드 s1이 NSG 없이 생성 성공"),
    dict(csp="azure", subject="loadBalancer", object="subnet",
         question="existence", verdict="optional",
         evidence=[
             ("azure-apply2-2026-07-30", "E0.build-chain2", "ok", "apply"),
         ],
         note="공용 LB(lbp)가 PIP만으로 성공 — 단독으론 선택, 선언 술어의 구성원"),
    dict(csp="azure", subject="loadBalancer", object="publicIp",
         question="existence", verdict="optional",
         evidence=[
             ("azure-apply2-2026-07-30", "E0.build-chain2", "ok", "apply"),
         ],
         note="내부 LB(lbi)가 subnet만으로 성공 — 단독으론 선택, 선언 술어의 구성원"),
    dict(csp="azure", subject="vm", object="disk", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply3-2026-07-30", "F0.build-vm-no-datadisk", "ok", "apply"),
         ],
         note="데이터 디스크 없이 VM 생성 성공. 덤 관측: 선언 안 한 OS 디스크가 "
              "서버 이름으로 생성됐다(F0.disks-after-create) — 서버측 합성"),
    # ── lifecycle (azure) ──
    dict(csp="azure", subject="vm", object="disk", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply3-2026-07-30", "C1.delete-disk-attached",
              "OperationNotAllowed", "apply"),
             ("azure-apply3-2026-07-30", "D.delete-data-disk", "ok", "apply"),
         ],
         note="붙은 디스크 삭제 거부 + 분리 후 성공. 역방향 관측: VM 삭제가 OS "
              "디스크를 남긴다(D.disks-after-vm-delete) — CB 드라이버가 디스크를 "
              "직접 지우는 이유"),
    dict(csp="azure", subject="nic", object="publicIp", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply2-2026-07-30", "C.delete-pip-attached",
              "PublicIPAddressCannotBeDeleted", "apply"),
             ("azure-apply2-2026-07-30", "D.delete-pip1", "ok", "apply"),
         ],
         note="선택 참조인데 붙어 있으면 삭제 금지 — nic→firewall과 같은 꼴"),
    dict(csp="azure", subject="nic", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply-2026-07-30", "C.delete-subnet-in-use",
              "InUseSubnetCannotBeDeleted", "apply"),
             ("azure-apply-2026-07-30", "D.delete-subnet", "ok", "apply"),
         ],
         note="사용 중 삭제 거부 + NIC 제거 후 삭제 성공(양성 대조)"),
    dict(csp="azure", subject="subnet", object="network", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply-2026-07-30", "C.delete-vnet-in-use",
              "InUseSubnetCannotBeDeleted", "apply"),
             ("azure-apply-2026-07-30", "D.delete-vnet", "ok", "apply"),
         ]),
    dict(csp="azure", subject="nic", object="firewall", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply-2026-07-30", "C.delete-nsg-attached",
              "InUseNetworkSecurityGroupCannotBeDeleted", "apply"),
             ("azure-apply-2026-07-30", "D.delete-nsg", "ok", "apply"),
         ],
         note="선택 참조여도 붙어 있는 동안 삭제는 막힌다 — existence와 lifecycle이 독립"),
    # ── gcp (REST 직접 — gcloud CLI 기본값 주입 배제) ──
    dict(csp="gcp", subject="subnet", object="network", question="existence",
         verdict="required",
         evidence=[
             ("gcp-apply-2026-07-31", "A.subnet-omit-network", "invalid", "apply"),
             ("gcp-apply-2026-07-31", "A.subnet-dangling-network", "notFound", "apply"),
         ]),
    dict(csp="gcp", subject="firewall", object="network", question="existence",
         verdict="optional",
         predicate="server-default: 미지정 시 default 네트워크로 대체",
         evidence=[
             ("gcp-apply-2026-07-31", "A.firewall-omit-network", "ok", "apply"),
             ("gcp-apply-2026-07-31", "A.firewall-dangling-network", "notFound", "apply"),
         ],
         note="명시는 선택이나 관계가 없는 것이 아니다 — 서버가 default로 채운다"
              "(스키마 서술의 실측 확인). 명시하면 실재해야 한다(dangling 거부)"),
    dict(csp="gcp", subject="vm", object="nic", question="existence",
         verdict="required",
         evidence=[
             ("gcp-apply-2026-07-31", "A.instance-omit-nic", "invalid", "apply"),
         ],
         note="NIC는 독립 자원이 아니라 내장 구조인데도 최소 하나는 필수다"),
    dict(csp="gcp", subject="vm", object="disk", question="existence",
         verdict="required",
         evidence=[
             ("gcp-apply-2026-07-31", "A.instance-omit-disks", "invalid", "apply"),
         ],
         note="**azure와 양상 반전** — azure는 OS 디스크를 서버가 합성해 선택, "
              "gcp는 부트 디스크 명세가 필수다. CSP 색인이 필요한 이유의 실측"),
    dict(csp="gcp", subject="nic", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("gcp-apply-2026-07-31", "C.delete-subnet-in-use",
              "resourceInUseByAnotherResource", "apply"),
             ("gcp-apply-2026-07-31", "D.delete-subnet", "ok", "apply"),
         ]),
    dict(csp="gcp", subject="subnet", object="network", question="lifecycle",
         verdict="holds",
         evidence=[
             ("gcp-apply-2026-07-31", "C.delete-network-in-use",
              "RESOURCE_IN_USE_BY_ANOTHER_RESOURCE", "apply"),
             ("gcp-apply-2026-07-31", "D.delete-network", "ok", "apply"),
         ]),
    dict(csp="gcp", subject="vm", object="disk", question="lifecycle",
         verdict="holds",
         evidence=[
             ("gcp-apply-2026-07-31", "C.delete-bootdisk-attached",
              "resourceInUseByAnotherResource", "apply"),
             ("gcp-apply-2026-07-31", "D.delete-disk.depkbg-vm", "ok", "apply"),
         ],
         note="부트 디스크가 인스턴스 삭제 후 살아남았다(D.disks-after-delete — "
              "API 기본 autoDelete=false의 실측). azure OS 디스크 잔존과 쌍이다"),
]


def _experiment_step(exp: str, key: str) -> dict:
    doc = json.loads(
        (_HERE / "experiments" / exp / "results.json").read_text(encoding="utf-8"))
    pool = doc.get("steps") or doc.get("tests")
    if "." in key and key.split(".")[-1] in ("validate", "what-if"):
        name, phase = key.rsplit(".", 1)
        return pool[name][phase]
    return pool[key]


def _schema_evidence() -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = {}
    for csp, fname in [("azure", "azure_candidates.json"),
                       ("aws", "aws_candidates.json"),
                       ("gcp", "gcp_candidates.json")]:
        doc = json.loads((_HERE / fname).read_text(encoding="utf-8"))
        for c in doc["candidates"]:
            if c["form"] == "readonly-backlink":
                continue
            out.setdefault((csp, c["subject"], c["object"]), []).append({
                "layer": "schema", "cite": c["cite"], "form": c["form"],
                "requiredInSchema": c["requiredInSchema"],
            })
    return out


def build() -> dict:
    schema = _schema_evidence()
    claims: list[dict] = []
    judged: set[tuple] = set()

    for j in EXPERIMENT_JUDGMENTS:
        evid = []
        for exp, key, expect, layer in j["evidence"]:
            step = _experiment_step(exp, key)
            if expect == "ok":
                assert step["ok"], f"{exp}/{key}: 성공을 인용했는데 실측은 실패다"
            else:
                assert expect in step["errorCodes"], (
                    f"{exp}/{key}: 인용 코드 {expect}가 실측에 없다 "
                    f"{step['errorCodes']}"
                )
            evid.append({"layer": layer, "experiment": exp, "step": key,
                         "code": expect})
        pair_key = (j["csp"], j["subject"], j["object"].split("|")[0])
        evid = schema.get(pair_key, []) + evid
        judged.add((j["csp"], j["subject"], j["object"], j["question"]))
        claims.append({
            "subject": j["subject"], "object": j["object"], "csp": j["csp"],
            "question": j["question"], "verdict": j["verdict"],
            "predicate": j.get("predicate"), "note": j.get("note"),
            "oracle": max((e["layer"] for e in evid),
                          key=lambda x: _LAYER_RANK[x]),
            "evidence": evid,
        })

    # 판정 없는 스키마 후보 → unknown (aws·gcp 전부와 azure 잔여)
    for (csp, s, o), evid in sorted(schema.items()):
        if (csp, s, o, "existence") in judged:
            continue
        claims.append({
            "subject": s, "object": o, "csp": csp, "question": "existence",
            "verdict": "unknown", "predicate": None,
            "note": "스키마 후보만 있다 — 동적 층 미실행"
                    + (" (계정 없음, 정적 상한 T9)" if csp != "azure" else ""),
            "oracle": "schema", "evidence": evid,
        })

    claims.sort(key=lambda c: (c["csp"], c["subject"], c["object"], c["question"]))
    counts: dict[str, int] = {}
    for c in claims:
        counts[f"{c['csp']}.{c['verdict']}"] = counts.get(
            f"{c['csp']}.{c['verdict']}", 0) + 1
    return {
        "_note": (
            "의존 주장의 통합 산출물 — (간선 × CSP × 질문)마다 판정·증거·도달 "
            "오라클 층. 판정 배정은 우리 구성(build_claims.EXPERIMENT_JUDGMENTS)"
            "이고, 인용 코드가 실험 실측과 어긋나면 빌드가 죽는다. unknown은 "
            "빈칸이 아니라 '동적 층 미실행'의 기록이다."
        ),
        "verdictCounts": counts,
        "claims": claims,
    }


if __name__ == "__main__":
    result = build()
    (_HERE / "claims.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("claims:", len(result["claims"]), "|", result["verdictCounts"])
    for c in result["claims"]:
        if c["verdict"] != "unknown":
            print(f"  {c['csp']:6} {c['subject']}→{c['object']}"
                  f" [{c['question']}] = {c['verdict']} ({c['oracle']})")
