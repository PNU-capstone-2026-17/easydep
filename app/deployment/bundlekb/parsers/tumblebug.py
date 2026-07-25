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

from app.deployment.bundlekb.model import ALWAYS, REQUIRED, Bundle, BundleSet, Member
from app.deployment.kbcommon.fetch import describe_source_set, fetch_cached
from app.deployment.kbcommon.sources import SOURCES

EVIDENCE_DYNAMIC = "tumblebug-dynamic"
EVIDENCE_TEMPLATE = "tumblebug-template"

#: `_DYNAMIC_MEMBERS`를 사람이 읽어 확정한 시점. 핀이 올라가면 다시 읽어야 한다.
_READ_AT_PIN = "v0.12.25"

#: VM 하나를 동적으로 요청했을 때 tumblebug이 확보하는 것.
#: `getNodeGroupReqFromDynamicReq`(provisioning.go:3216~3529)를 읽어 확정했다.
#: 넷 다 **연결(connection)당 공유**이고, 없으면 `CreateSharedResourceWithOptions`로
#: 만든다. 이미 있으면 재사용하므로 "매번 새로 생긴다"는 뜻은 아니다.
_DYNAMIC_MEMBERS: tuple[tuple[str, str, str], ...] = (
    ("vNet", ALWAYS, "shared per connection. A default vNet is created if there is none"),
    (
        "subnet",
        ALWAYS,
        "same name as the vNet by default. If a zone is given, a subnet in that zone",
    ),
    ("sshKey", ALWAYS, "shared per connection. Created if there is none"),
    ("securityGroup", ALWAYS, "shared per connection. A template can change the policy"),
    (
        "image",
        REQUIRED,
        "the request must supply an image ID. If it is not in the DB it is "
        "registered from the CSP automatically",
    ),
    ("vm", ALWAYS, "the thing you asked for"),
)

#: 우리 core 층 타입 이름과 그대로 맞춘다 — graphkb의 `core::vNet` 등.
_CORE = "core"


def dynamic_bundle() -> Bundle:
    """VM 하나 → 리소스 군. **core 층으로 담는다** (CSP 중립이라서)."""
    return Bundle(
        id="tumblebug::dynamic-vm",
        name="tumblebug dynamic VM creation",
        provider=_CORE,
        evidence=EVIDENCE_DYNAMIC,
        anchor=f"{_CORE}::vm",
        members=tuple(
            Member(f"{_CORE}::{name}", tier, note) for name, tier, note in _DYNAMIC_MEMBERS
        ),
        description=(
            "Resources acquired along with a single VM when you request one "
            "dynamically from cb-tumblebug. Confirmed by reading provisioning.go "
            f"as of {_READ_AT_PIN}."
        ),
        caveat=(
            "**This is what this tool creates, not what the cloud requires.** "
            "The four resources (vNet·subnet·sshKey·securityGroup) are shared per "
            "connection, so existing ones are reused."
        ),
    )


def _positive(value: object, default: int = 1) -> int:
    """원본의 개수 필드. **없거나 이상하면 세지 않은 것으로 보고 1로 둔다.**

    0으로 두면 "이 리소스를 안 만든다"가 되어 뜻이 뒤집힌다.
    """
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if number >= 1 else default


def _plural(count: int, noun: str) -> str:
    """`2 node groups` / `1 node group` — 개수 1에 복수형을 붙이지 않는다."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _member_of_template(doc: dict) -> tuple[str, list[Member], str | None]:
    """템플릿 하나에서 (종류, 구성원, 앵커)를 뽑는다.

    **한 타입은 한 줄이고 개수는 `count`에 담는다.** 예전에는 노드 그룹마다 줄을
    하나씩 만들어 `core::vm`이 28줄이 됐고, 그러면서도 `nodeGroupSize`를 안 봐서
    **대수는 오히려 틀렸다**(그룹 17개 × 2대 = 34대를 17로 셌다).
    """
    kind = doc.get("resourceType")
    members: list[Member] = []
    anchor = None
    if kind == "infra":
        groups = (doc.get("infraDynamicReq") or {}).get("nodeGroups") or []
        if groups:
            # 노드 그룹은 전부 VM이다. 스펙 id는 구성원이 아니라 **사이징 참조점**이라
            # 여기 담지 않는다 — 타입과 값을 같은 칸에 섞지 않는다.
            total = sum(_positive(g.get("nodeGroupSize")) for g in groups)
            members.append(
                Member(f"{_CORE}::vm", ALWAYS, _plural(len(groups), "node group"), count=total)
            )
            anchor = f"{_CORE}::vm"
    elif kind == "securityGroup":
        rules = (doc.get("securityGroupReq") or {}).get("firewallRules") or []
        members.append(
            Member(f"{_CORE}::securityGroup", ALWAYS, _plural(len(rules), "inbound rule"))
        )
        anchor = f"{_CORE}::securityGroup"
    elif kind == "vNet":
        members.append(Member(f"{_CORE}::vNet", ALWAYS, None))
        # 서브넷을 적는 방식이 **템플릿마다 다르다.** `vNetPolicy.subnetCount`는
        # CSP 중립 정책이고, `vNetReq.subnetInfoList`는 CIDR까지 박은 구체 목록이다.
        # 뒤쪽만 보던 코드가 없어서 3개짜리 AWS 템플릿이 서브넷 0개로 나왔다.
        policy = doc.get("vNetPolicy") or {}
        listed = (doc.get("vNetReq") or {}).get("subnetInfoList") or []
        count = _positive(policy.get("subnetCount"), default=0) or len(listed)
        if count:
            note = (
                "CIDR is specified too"
                if listed
                else "CSP-neutral policy (adjusted at provisioning time)"
            )
            members.append(Member(f"{_CORE}::subnet", ALWAYS, note, count=count))
        anchor = f"{_CORE}::vNet"
    elif kind == "k8sCluster":
        # 타입 id를 `core::cluster`로 적고 있었는데 **그런 노드는 없다.**
        # core 층 이름은 `core::k8sCluster`다 — 안 맞으면 다른 KB와 조인이 끊긴다.
        clusters = (doc.get("k8sMultiClusterDynamicReq") or {}).get("clusters") or []
        members.append(
            Member(f"{_CORE}::k8sCluster", ALWAYS, None, count=max(1, len(clusters)))
        )
        if clusters:
            nodes = sum(_positive(c.get("desiredNodeSize")) for c in clusters)
            members.append(
                Member(
                    f"{_CORE}::k8sNodeGroup",
                    ALWAYS,
                    f"one per cluster · desired nodes total {nodes}",
                    count=len(clusters),
                )
            )
        anchor = f"{_CORE}::k8sCluster"
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
                "Resource groups that cb-tumblebug creates. **This is what our "
                "execution path creates, not what the cloud requires.** The 1 "
                "dynamic bundle was confirmed by **a human reading** "
                f"provisioning.go as of {_READ_AT_PIN} (scraping Go conditional "
                "branches with a regex misses them); the rest were read "
                f"mechanically from `init/templates/*.json` ({dict(report.kinds)}). "
                "Spec ids in the use-case templates are a **sizing reference "
                "point**, not members, so they were left in the note only."
            ),
        }
    ]
    from app.deployment.kbcommon import artifact

    artifact.write_dataset(output, bundles.to_dict(), _schema())
    print(f"tumblebug 번들: {len(bundles.bundles)}개 → {output}")
    print(f"  템플릿 종류별: {dict(report.kinds)} · 건너뜀 {report.skipped}", file=sys.stderr)
    return bundles


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
