"""cb-tumblebug → 우리 실행 경로가 실제로 만드는 리소스 군.

두 가지를 담는다.

1. **동적 생성 번들** — VM 하나를 요청하면 무엇이 함께 만들어지나
   (`src/core/infra/provisioning.go`의 `getNodeGroupReqFromDynamicReq`)
2. **큐레이션 템플릿** — `init/templates/*.json`의 이름 붙은 번들

## 이건 클라우드의 요구가 아니다

**우리 실행 경로가 만드는 것**이다. AWS에서 EC2를 만들려면 VPC가 "필요하다"와
tumblebug이 "만들어 준다"는 다른 말이다. `guideline-not-deployer` 원칙이 그대로
적용되고, 그래서 `coverage`와 도구 출력에 범위를 밝힌다.

## 동적 번들은 코드에서 읽었지 파싱하지 않았다

`provisioning.go`는 23만 자짜리 Go 소스다. 정규식으로 긁으면 조건 분기를 놓친다 —
이 저장소가 산문 추출에서 겪은 것과 같은 실패다. 그래서 **사람이 읽고 확정한 표**를
상수로 둔다(`_DYNAMIC_MEMBERS`). 소스에 핀이 박혀 있으므로 그 확인은 다음 빌드에서도
유효하고, 핀이 올라가면 다시 읽어야 한다는 것을 `_READ_AT_PIN`에 적어 둔다.

**"완벽한 데이터셋이 목표지 완벽한 파서가 아니다"** — 핀을 고정했으니 손 검수를 쓴다.

## 템플릿은 기계로 읽는다

`init/templates/*.json`은 구조화돼 있어 짐작이 필요 없다. 다만 **원본이 스스로 단
경고**를 값과 함께 담는다 — `sg-default`는 "전 포트를 연다, 프로덕션엔 쓰지 말라"고
자기가 적어 두었다. 그 문장을 떼면 위험한 기본값이 안전해 보인다.
"""

from __future__ import annotations

import json
import sys
import tarfile
from collections import Counter
from pathlib import Path

from bundlekb.model import ALWAYS, REQUIRED, Bundle, BundleSet, Member
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

EVIDENCE_DYNAMIC = "tumblebug-dynamic"
EVIDENCE_TEMPLATE = "tumblebug-template"

#: `_DYNAMIC_MEMBERS`를 사람이 읽어 확정한 시점. 핀이 올라가면 다시 읽어야 한다.
_READ_AT_PIN = "v0.12.25"

#: VM 하나를 동적으로 요청했을 때 tumblebug이 확보하는 것.
#: `getNodeGroupReqFromDynamicReq`(provisioning.go:3216~3529)를 읽어 확정했다.
#: 넷 다 **연결(connection)당 공유**이고, 없으면 `CreateSharedResourceWithOptions`로
#: 만든다. 이미 있으면 재사용하므로 "매번 새로 생긴다"는 뜻은 아니다.
_DYNAMIC_MEMBERS: tuple[tuple[str, str, str], ...] = (
    ("vNet", ALWAYS, "연결당 공유. 없으면 기본 vNet을 만든다"),
    ("subnet", ALWAYS, "vNet과 같은 이름이 기본. 존을 지정하면 그 존의 서브넷"),
    ("sshKey", ALWAYS, "연결당 공유. 없으면 만든다"),
    ("securityGroup", ALWAYS, "연결당 공유. 템플릿으로 정책을 바꿀 수 있다"),
    ("image", REQUIRED, "요청에 이미지 ID를 줘야 한다. DB에 없으면 CSP에서 자동 등록"),
    ("vm", ALWAYS, "요청한 것 자체"),
)

#: 우리 core 층 타입 이름과 그대로 맞춘다 — graphkb의 `core::vNet` 등.
_CORE = "core"


def dynamic_bundle() -> Bundle:
    """VM 하나 → 리소스 군. **core 층으로 담는다** (CSP 중립이라서)."""
    return Bundle(
        id="tumblebug::dynamic-vm",
        name="tumblebug 동적 VM 생성",
        provider=_CORE,
        evidence=EVIDENCE_DYNAMIC,
        anchor=f"{_CORE}::vm",
        members=tuple(
            Member(f"{_CORE}::{name}", tier, note) for name, tier, note in _DYNAMIC_MEMBERS
        ),
        description=(
            "cb-tumblebug에 VM 하나를 동적으로 요청하면 함께 확보되는 리소스. "
            f"provisioning.go를 {_READ_AT_PIN} 시점에 읽어 확정했다."
        ),
        caveat=(
            "**클라우드가 요구하는 것이 아니라 이 도구가 만드는 것**입니다. "
            "네 리소스(vNet·subnet·sshKey·securityGroup)는 연결당 공유라 이미 있으면 "
            "재사용합니다."
        ),
    )


def _member_of_template(doc: dict) -> tuple[str, list[Member], str | None]:
    """템플릿 하나에서 (앵커, 구성원, 앵커타입)을 뽑는다."""
    kind = doc.get("resourceType")
    members: list[Member] = []
    anchor = None
    if kind == "infra":
        groups = (doc.get("infraDynamicReq") or {}).get("nodeGroups") or []
        # 노드 그룹은 전부 VM이다. 스펙 id는 구성원이 아니라 **사이징 참조점**이라
        # note로만 남긴다 — 타입과 값을 같은 칸에 섞지 않는다.
        for group in groups:
            spec = group.get("specId")
            members.append(
                Member(f"{_CORE}::vm", ALWAYS, f"{group.get('name')}: {spec}" if spec else None)
            )
        anchor = f"{_CORE}::vm" if groups else None
    elif kind == "securityGroup":
        rules = (doc.get("securityGroupReq") or {}).get("firewallRules") or []
        members.append(
            Member(f"{_CORE}::securityGroup", ALWAYS, f"인바운드 규칙 {len(rules)}개")
        )
        anchor = f"{_CORE}::securityGroup"
    elif kind == "vNet":
        policy = doc.get("vNetPolicy") or {}
        members.append(Member(f"{_CORE}::vNet", ALWAYS, None))
        count = policy.get("subnetCount")
        if count:
            members.append(Member(f"{_CORE}::subnet", ALWAYS, f"서브넷 {count}개"))
        anchor = f"{_CORE}::vNet"
    elif kind == "k8sCluster":
        members.append(Member(f"{_CORE}::cluster", ALWAYS, None))
        anchor = f"{_CORE}::cluster"
    return kind or "?", members, anchor


class Report:
    def __init__(self) -> None:
        self.kinds: Counter = Counter()
        self.skipped = 0


def parse_tarball(tar: Path) -> tuple[BundleSet, Report]:
    out = BundleSet()
    report = Report()
    out.add(dynamic_bundle())

    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            name = member.name.replace("\\", "/")
            if not member.isfile() or "/init/templates/" not in name:
                continue
            if not name.endswith(".json"):
                continue
            try:
                doc = json.loads(archive.extractfile(member).read())
            except Exception:
                report.skipped += 1
                continue
            kind, members, anchor = _member_of_template(doc)
            if not members:
                report.skipped += 1
                continue
            report.kinds[kind] += 1
            stem = name.rsplit("/", 1)[-1].removesuffix(".json")
            description = doc.get("description")
            out.add(
                Bundle(
                    id=f"tumblebug::{stem}",
                    name=doc.get("name") or stem,
                    provider=_CORE,
                    evidence=EVIDENCE_TEMPLATE,
                    anchor=anchor,
                    members=tuple(members),
                    description=description,
                    # 원본이 스스로 경고를 달았으면 그대로 옮긴다.
                    caveat=_caveat_of(description),
                )
            )
    return out, report


#: 원본 설명문에 경고가 섞여 있는 경우가 있다. **문장을 지어내지 않고 그대로 옮긴다.**
_WARN_WORDS = ("development/testing", "not meant", "For production", "Opens all")


def _caveat_of(description: str | None) -> str | None:
    if not description:
        return None
    for word in _WARN_WORDS:
        if word in description:
            return description
    return None


def build(output: Path, *, refresh: bool = False) -> BundleSet:
    source = SOURCES["tumblebug-src"]
    tar = fetch_cached(source.url, f"tumblebug-src-{source.pin}.tar.gz", refresh=refresh)
    bundles, report = parse_tarball(tar)
    bundles.provenance = [describe_source_set([tar], source.key)]
    bundles.coverage = [
        {
            "provider": _CORE,
            "bundles": len(bundles.bundles),
            "note": (
                "cb-tumblebug이 만드는 리소스 군. **클라우드가 요구하는 것이 아니라 "
                "우리 실행 경로가 만드는 것**이다. 동적 번들 1개는 provisioning.go를 "
                f"{_READ_AT_PIN} 시점에 **사람이 읽어** 확정했고(정규식으로 Go 조건 "
                "분기를 긁으면 놓친다), 나머지는 `init/templates/*.json`을 기계로 "
                f"읽었다({dict(report.kinds)}). 유스케이스 템플릿의 스펙 id는 구성원이 "
                "아니라 **사이징 참조점**이라 note에만 남겼다."
            ),
        }
    ]
    from kbcommon import artifact

    artifact.write_dataset(output, bundles.to_dict(), _schema())
    print(f"tumblebug 번들: {len(bundles.bundles)}개 → {output}")
    print(f"  템플릿 종류별: {dict(report.kinds)} · 건너뜀 {report.skipped}", file=sys.stderr)
    return bundles


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
