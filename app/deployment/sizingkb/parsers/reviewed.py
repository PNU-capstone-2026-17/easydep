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

from sizingkb.model import RESERVED_IPS, Rule, RuleSet

EVIDENCE = "human-review"

#: (프로바이더, 예약 수, 무엇이 예약되나, 사람이 확인할 곳)
#: **여기 없는 프로바이더는 추측해 넣지 않는다** — 모르면 도구가 모른다고 답한다.
_RESERVED: tuple[tuple[str, int, str, str], ...] = (
    (
        "aws",
        5,
        "네트워크 주소 · VPC 라우터 · Amazon DNS · 향후 사용 예약 · 브로드캐스트",
        "AWS VPC 사용 설명서 'Subnet CIDR blocks' (기계 판독 소스 없음 — "
        "awsdocs/amazon-vpc-user-guide는 2023-06-15 아카이브되며 비워짐)",
    ),
    (
        "gcp",
        4,
        "네트워크 주소 · 기본 게이트웨이 · 끝에서 두 번째(향후 사용) · 브로드캐스트",
        "GCP VPC 문서 'Subnet ranges' (기계 판독 소스 없음)",
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
                unit="개",
                evidence=EVIDENCE,
                note=what,
                caveat=(
                    f"**기계 판독 소스가 없어 사람이 적은 값**입니다. 확인처: {where}"
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
            "pin": "(손 검수)",
            "note": "이 저장소에 직접 적어 넣은 값. git이 곧 버전 관리다.",
        }
    ]
    rules.coverage = [
        {
            "rules": len(rules.rules),
            "note": (
                "**손으로 적은 사이징 상수.** networkinfo.yaml이 aws·gcp의 서브넷 예약 "
                "IP를 비워 두었고 기계 판독 소스를 찾지 못해(아카이브·404·언급 0건) "
                "여기 담는다. 값이 의심스러워서가 아니라 **출처가 기계 판독이 아니라서** "
                "산출물을 갈라 두었다 — 진짜 소스가 생기면 이 파일만 지우면 된다. "
                "항목마다 사람이 확인할 곳을 적었다."
            ),
        }
    ]
    from kbcommon import artifact

    artifact.write_dataset(output, rules.to_dict(), _schema())
    print(f"손 검수 사이징: 규칙 {len(rules.rules)}개 → {output}")
    return rules


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
