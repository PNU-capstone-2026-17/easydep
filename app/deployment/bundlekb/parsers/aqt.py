"""Azure Quickstart Templates → **동시 출현 빈도**.

## 이게 왜 필요한가

graphkb는 스키마의 참조를 따라간다. 그래서 "가능한 것"은 다 주지만 **"실제로 필요한
것"을 못 가른다.** `EC2::Instance`에서 `KMS::ReplicaKey`까지 이어 주는 식이다.

가설을 실측으로 확인했다 — **사람이 실제로 쓴 템플릿 수천 개에서 같이 나오는 비율**이
그 판별자다.

    VM이 있는 템플릿 330개 중
      100.0%  networkInterfaces      ← 사실상 필수
       92.4%  virtualNetworks
       72.4%  networkSecurityGroups  ← 강한 관행
        5.8%  routeTables            ← 드묾
        5.5%  bastionHosts

**분포에 큰 골이 있다.** 100%·92% 무리와 5~7% 꼬리가 뚜렷이 갈린다.

## 이건 `stated`도 `inferred`도 아니다

Azure 문서가 "VM에 NIC이 필요하다"고 말한 적 없다. 우리가 **센 것**이다. 그래서
`basis=observed`를 새로 뒀다(`kbcommon/basis.py`). 뜻은 **"이 코퍼스에서 그렇게
나왔다"**이지 "클라우드가 강제한다"가 아니다.

판정에 쓰면 안 된다 — 100%가 "없으면 안 된다"를 증명하지 못한다.

## 표본 수를 반드시 함께 담는다

`ContainerService/managedClusters`는 템플릿이 17개뿐이라 35.3%가 **6건**이다.
비율만 담으면 숫자에 없는 확신을 준다. `MIN_SAMPLES` 아래 앵커는 아예 안 담는다.

## 표본 편향은 데이터가 아니라 고지로 다룬다

Quickstart는 데모·튜토리얼 쪽으로 기운다. VM과 스토리지 계정이 53.6%로 같이 나오는
것은 옛 부트 진단 관행의 흔적이다. **값을 손보지 않고 coverage에 적는다** — 보정하면
그게 짐작이 된다.
"""

from __future__ import annotations

import collections
import json
import sys
import tarfile
from pathlib import Path

from bundlekb.dataset import MIN_SAMPLES
from bundlekb.model import BundleSet, Companion
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

EVIDENCE = "aqt-corpus"
PROVIDER = "azure"

_TEMPLATE = "/azuredeploy.json"
_DEPLOY = "Microsoft.Resources/deployments"
_MAX_DEPTH = 8

#: 한 앵커에 딸린 동반자 중 이 아래는 안 담는다. 1~2건은 우연과 구분이 안 된다.
MIN_HITS = 3


def _children(resource: dict) -> list:
    found = resource.get("resources")
    if isinstance(found, dict):
        return list(found.values())
    return found or []


def types_in(doc: dict, out: set[str] | None = None, depth: int = 0) -> set[str]:
    """템플릿 하나가 담은 리소스 타입 집합.

    중첩 리소스(`resources` 안의 `resources`)와 중첩 배포(`properties.template`)를
    둘 다 따라간다 — 한쪽만 보면 조용히 적게 센다.
    """
    out = set() if out is None else out
    if not isinstance(doc, dict) or depth > _MAX_DEPTH:
        return out
    for resource in _children(doc):
        if not isinstance(resource, dict):
            continue
        type_name = resource.get("type")
        if isinstance(type_name, str) and "/" in type_name and type_name != _DEPLOY:
            out.add(type_name)
        types_in(resource, out, depth + 1)
        # `properties`가 문자열인 템플릿이 실제로 있다(표현식을 통째로 넣은 것).
        # dict라고 가정하면 코퍼스 전체가 한 파일에서 멈춘다.
        props = resource.get("properties")
        inner = props.get("template") if isinstance(props, dict) else None
        if isinstance(inner, dict):
            types_in(inner, out, depth + 1)
    return out


class Report:
    def __init__(self) -> None:
        self.templates = 0
        self.unreadable = 0
        self.types = 0
        self.anchors_kept = 0
        self.anchors_dropped = 0


def count_cooccurrence(tar: Path) -> tuple[list[Companion], Report]:
    report = Report()
    docs: list[set[str]] = []
    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.replace("\\", "/").endswith(_TEMPLATE):
                continue
            try:
                # BOM이 붙은 파일이 실제로 있다.
                doc = json.loads(archive.extractfile(member).read().decode("utf-8-sig"))
            except Exception:
                report.unreadable += 1
                continue
            found = types_in(doc)
            if found:
                docs.append(found)
    report.templates = len(docs)
    report.types = len({t for d in docs for t in d})

    anchor_counts = collections.Counter(t for d in docs for t in d)
    out: list[Companion] = []
    for anchor, samples in anchor_counts.items():
        if samples < MIN_SAMPLES:
            report.anchors_dropped += 1
            continue
        report.anchors_kept += 1
        pairs: collections.Counter = collections.Counter()
        for d in docs:
            if anchor in d:
                pairs.update(d - {anchor})
        for companion, hits in pairs.items():
            if hits < MIN_HITS:
                continue
            out.append(
                Companion(
                    anchor=f"{PROVIDER}::{anchor}",
                    type_id=f"{PROVIDER}::{companion}",
                    hits=hits,
                    samples=samples,
                    evidence=EVIDENCE,
                )
            )
    return out, report


def build(output: Path, *, refresh: bool = False) -> BundleSet:
    source = SOURCES["azure-quickstart-templates"]
    tar = fetch_cached(source.url, f"aqt-{source.pin[:12]}.tar.gz", refresh=refresh)
    companions, report = count_cooccurrence(tar)

    out = BundleSet(companions=companions)
    out.provenance = [describe_source_set([tar], source.key)]
    out.coverage = [
        {
            "provider": PROVIDER,
            "templates": report.templates,
            "note": (
                f"실제 ARM 템플릿 {report.templates:,}개에서 센 동시 출현. 타입 "
                f"{report.types:,}종 중 표본 {MIN_SAMPLES}개 이상인 앵커 "
                f"{report.anchors_kept}종만 담았다(버린 앵커 {report.anchors_dropped}종). "
                "**코퍼스의 사실이지 클라우드의 사실이 아니다** — basis=observed. "
                "표본 편향이 있다: Quickstart는 데모·튜토리얼 쪽으로 기울어, VM과 "
                "스토리지 계정이 함께 나오는 비율에는 옛 부트 진단 관행이 섞여 있다. "
                "**값을 보정하지 않았다** — 보정하면 그게 짐작이 된다."
            ),
        }
    ]
    from kbcommon import artifact

    artifact.write_dataset(output, out.to_dict(), _schema())
    print(
        f"aqt 동시 출현: 앵커 {report.anchors_kept}종 · 쌍 {len(companions):,}건 "
        f"(템플릿 {report.templates:,}개) → {output}"
    )
    if report.unreadable:
        print(f"  못 읽은 템플릿 {report.unreadable}개", file=sys.stderr)
    return out


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
