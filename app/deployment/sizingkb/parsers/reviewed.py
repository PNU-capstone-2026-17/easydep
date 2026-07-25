"""손 검수로 적어 넣은 사이징 상수.

## 왜 이 파일이 있나 — 소스를 찾다 없어서

`networkinfo.yaml`은 CSP 10곳 중 **3곳만** 서브넷 예약 IP를 적어 두었다
(alibaba·azure·ibm). **가장 많이 묻는 aws·gcp가 빠져 있다.** 그래서 서브넷 용량
도구가 그 둘에는 "모릅니다"만 돌려줬다.

기계 판독 소스를 찾았고 **없었다**:

    awsdocs/amazon-vpc-user-guide       2023-06-15 아카이브 · 파일 7개로 비워짐
    hashicorp/terraform-provider-aws    subnet/vpc 문서에 '예약' 언급 0건
    GoogleCloudPlatform/compute-docs    404
    tumblebug networkinfo.yaml          해당 칸이 빈칸

## 그래서 손으로 적되, 손으로 적었다고 밝힌다

이 저장소에는 `human-review` 근거 라벨이 이미 있다("사람이 직접 적어 넣은 것").
`cfn-lint-region`이 손 큐레이션이지만 stated인 것과 같은 자리다.

**값이 의심스러워서가 아니라 출처가 기계 판독이 아니라서** 따로 담는다. 산출물이
갈려 있으면 나중에 진짜 소스가 생겼을 때 이 파일만 지우면 된다.

각 항목에 **사람이 확인할 수 있는 곳**을 적는다. 핀은 못 박아도 검증은 되게 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.deployment.sizingkb.model import RESERVED_IPS, Rule, RuleSet

EVIDENCE = "human-review"

#: (프로바이더, 예약 수, 무엇이 예약되나, 사람이 확인할 곳)
#: **여기 없는 프로바이더는 추측해 넣지 않는다** — 모르면 도구가 모른다고 답한다.
#:
#: 2026-07-24 (재편 계획 ⑥-D): 판정 조인을 끝에서 끝까지 돌려 보니 oracle·tencent
#: 경로에서 "예약 IP 모름"이 실측되어, 실행 경로(cb-spider 12 CSP)의 나머지를
#: 조사했다. 공식 문서가 고정 수를 명시한 4곳만 담는다. **안 담은 곳과 이유**:
#:
#:   openstack   Neutron은 게이트웨이 유무·DHCP 포트 수가 구성에 따라 달라
#:               고정 상수가 아니다. NHN(OpenStack 기반)의 5개는 그 배포판의
#:               선택이지 상류의 상수가 아니다 — 상류에 그대로 옮기면 거짓이 된다.
#:   kt          매뉴얼(manual.cloud.kt.com)에서 고정 수 명시를 찾지 못했다.
#:               G-Cloud PDF(2021)의 "Tier당 약 170개"는 수가 아니라 어림이다.
_RESERVED: tuple[tuple[str, int, str, str], ...] = (
    (
        "aws",
        5,
        "network address · VPC router · Amazon DNS · reserved for future use · broadcast",
        "AWS VPC User Guide 'Subnet CIDR blocks' (no machine-readable source — "
        "awsdocs/amazon-vpc-user-guide was archived 2023-06-15 and emptied)",
    ),
    (
        "gcp",
        4,
        "network address · default gateway · second-to-last (future use) · broadcast",
        "GCP VPC docs 'Subnet ranges' (no machine-readable source)",
    ),
    (
        "tencent",
        3,
        "first two addresses · last address",
        "Tencent Cloud VPC 'Limits' doc (tencentcloud.com/document/product/215/31804): "
        "\"For each subnet, Tencent Cloud reserves its first two IPs and the last "
        "one for IP networking.\" (checked 2026-07-24)",
    ),
    (
        "oracle",
        3,
        "first two addresses of the CIDR · last address",
        "OCI 'Overview of VCNs and Subnets': \"the first two addresses and the "
        "last in the subnet's CIDR are reserved by the Networking service.\" "
        "(checked 2026-07-24)",
    ),
    (
        "nhn",
        5,
        "network address · gateway · 2 for DHCP/SNAT · broadcast",
        "NHN Cloud VPC console guide (docs.nhncloud.com Network/VPC console-guide), "
        "reserved address table — it lists 5 for the 192.168.0.0/24 example. "
        "(checked 2026-07-24)",
    ),
    (
        "ncp",
        7,
        "network address · broadcast · first 5 for internal management",
        "NAVER Cloud 'Subnet Management' (guide.ncloud-docs.com "
        "vpc-subnetmanage-vpc): \"/24인 경우 249개 … 사용 가능\" (\"for a /24, 249 … "
        "are usable\") — 256−249=7, and the arithmetic is consistent across three "
        "prefixes (/24 · /25 · /26). (checked 2026-07-24)",
    ),
)


def build_rules() -> RuleSet:
    out = RuleSet()
    for provider, reserved, what, where in _RESERVED:
        out.add(
            Rule(
                id=f"reviewed::reserved-ips/{provider}",
                kind=RESERVED_IPS,
                scope=provider,
                metric="reservedIps",
                value=reserved,
                unit="IPs",
                evidence=EVIDENCE,
                note=what,
                caveat=(
                    "**A hand-entered value — there is no machine-readable "
                    f"source.** Check it against: {where}"
                ),
            )
        )
    return out


def build(output: Path, *, refresh: bool = False) -> RuleSet:
    rules = build_rules()
    rules.provenance = [
        {
            "source": EVIDENCE,
            "pin_kind": "bundled",
            "pin": "(hand review)",
            "note": "Values entered directly into this repo. git is the version control.",
        }
    ]
    rules.coverage = [
        {
            "rules": len(rules.rules),
            "note": (
                "**Hand-entered sizing constants.** Subnet reserved IPs for the "
                "providers networkinfo.yaml left blank, read out of each CSP's "
                "official docs by hand (no machine-readable source — archived, 404, "
                "zero mentions). The dataset is kept separate not because the values "
                "are doubtful but because **the source is not machine-readable** — "
                "if a real source appears, delete just this file. Every entry states "
                "where a human can check it. **openstack (varies with the "
                "configuration) and kt (no fixed count stated) are left out on "
                "purpose** — if we do not know, the tool says it does not know."
            ),
        }
    ]
    from app.deployment.kbcommon import artifact

    artifact.write_dataset(output, rules.to_dict(), _schema())
    print(f"손 검수 사이징: 규칙 {len(rules.rules)}개 → {output}")
    return rules


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
