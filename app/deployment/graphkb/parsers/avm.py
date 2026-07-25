"""Azure Verified Modules → **실무 배포 순서** 엣지.

**무엇이 다른가.** 우리 Azure 그래프는 이름의 계층(`arm-hierarchy` 2,223)과 스키마의
참조(`bicep-ref` 291)로 되어 있다. 둘 다 "구조가 그렇다"는 사실이다. AVM은 다른 것을
준다 — **실제로 배포할 때 무엇을 먼저 만드는가.**

실측(storage-account 모듈 하나): 타입 쌍 6개 중 **5개가 우리 그래프에 없는 관계**였다.

    Microsoft.Insights/diagnosticSettings   → Microsoft.Storage/storageAccounts
    Microsoft.Authorization/roleAssignments → Microsoft.Storage/storageAccounts
    Microsoft.Storage/storageAccounts       → Microsoft.KeyVault/vaults

마지막 것이 이 소스의 성격을 잘 보여준다. 스토리지 계정이 KeyVault를 **스키마상**
요구하지는 않는다 — 고객 관리 키를 쓸 때만 필요하다. AVM은 그 실무 구성을 담고 있다.

## 그래서 근거를 구분해 적는다

`avm-dependson`은 **"AVM 모듈이 이 순서로 배포한다"**이지 **"API가 이 순서를 강제한다"**가
아니다. `tpg-schema`의 ForceNew를 "Terraform이 재생성한다"이지 "API가 거부한다"가
아니라고 적어 둔 것과 같은 구분이다. 모듈 저자가 명시한 것이라 `basis=stated`지만,
무엇을 명시한 것인지는 라벨이 밝힌다.

## 걸러내는 것

- `Microsoft.Resources/deployments` — AVM이 사용량 집계용으로 넣는 텔레메트리 배포다.
  모든 모듈에 있어서 담으면 **모든 타입이 여기에 의존하는** 가짜 허브가 생긴다.
- 자기 자신을 가리키는 쌍.
- 우리 Azure 인덱스에 없는 타입 — 담지 않고 센다.

## 핀

저장소 **전체 태그가 없다.** 태그가 모듈별 semver(`storage/storage-account/3.0.1`)라
저장소 상태를 가리키지 못한다. 그래서 커밋 SHA로 고정한다.
"""

from __future__ import annotations

import json
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path

from graphkb.model import Edge, Graph, Node
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES
from kbcommon.type_ids import AzureTypeIndex

EVIDENCE = "avm-dependson"

#: AVM이 모든 모듈에 넣는 텔레메트리. 담으면 가짜 허브가 된다.
_TELEMETRY = "microsoft.resources/deployments"

#: `avm/res/<그룹>/<모듈>/main.json` — 자원 모듈만. `avm/ptn`(패턴)·`avm/utl`은 뺀다.
_MODULE = re.compile(r"/avm/res/([^/]+)/([^/]+)/main\.json$")


class Report:
    def __init__(self) -> None:
        self.modules = 0
        self.pairs = 0
        self.unknown_types: Counter = Counter()
        self.telemetry_skipped = 0


def type_pairs(template: dict) -> set[tuple[str, str]]:
    """컴파일된 ARM 템플릿의 `dependsOn`에서 (의존하는 타입, 의존 대상 타입).

    `dependsOn`은 **심볼 이름**(`storageAccount`)이라 같은 템플릿의 리소스 표에서
    타입을 찾아 바꾼다. `resourceId(...)` 식으로 적힌 것은 심볼이 아니라 못 푸므로
    건너뛴다 — 억지로 문자열을 파싱하면 틀린 타입을 만들어낸다.
    """
    resources = template.get("resources")
    if not isinstance(resources, dict):
        # 옛 스키마는 배열이다. 그때는 심볼 이름이 없어 dependsOn을 못 푼다.
        return set()
    symbols = {
        name: body.get("type")
        for name, body in resources.items()
        if isinstance(body, dict) and body.get("type")
    }
    out: set[tuple[str, str]] = set()
    for name, body in resources.items():
        if not isinstance(body, dict):
            continue
        source = symbols.get(name)
        if not source:
            continue
        for dep in body.get("dependsOn") or []:
            target = symbols.get(dep) if isinstance(dep, str) else None
            if not target or target == source:
                continue
            out.add((source, target))
    return out


def parse_tarball(tar: Path, *, type_index: AzureTypeIndex) -> tuple[Graph, Report]:
    """AVM tarball → 배포 순서 그래프."""
    graph = Graph()
    report = Report()
    seen: set[tuple[str, str]] = set()

    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or not _MODULE.search("/" + member.name):
                continue
            report.modules += 1
            try:
                template = json.loads(archive.extractfile(member).read())
            except Exception:
                continue
            for source, target in type_pairs(template):
                if _TELEMETRY in (source.lower(), target.lower()):
                    report.telemetry_skipped += 1
                    continue
                missing = [
                    t for t in (source, target) if t.lower() not in type_index.by_lower
                ]
                if missing:
                    for name in missing:
                        report.unknown_types[name] += 1
                    continue
                from_id = type_index.type_id(source)
                to_id = type_index.type_id(target)
                if (from_id, to_id) in seen:
                    continue
                seen.add((from_id, to_id))
                report.pairs += 1
                for node_id, name in ((from_id, source), (to_id, target)):
                    graph.add_node(
                        Node(
                            id=node_id,
                            layer="vendor",
                            provider="azure",
                            display_name=type_index.canonical(name),
                            source="avm-bicep",
                        )
                    )
                graph.add_edge(
                    Edge(
                        from_id=from_id,
                        to_id=to_id,
                        type="references",
                        via_property="",
                        # 배포 순서가 필요하다는 것이지 스키마상 필수라는 뜻이 아니다.
                        required=False,
                        cardinality="one",
                        evidence=EVIDENCE,
                    )
                )
    return graph, report


def build(output: Path, *, refresh: bool = False) -> Graph:
    from kbcommon.fetch import fetch_relative
    from kbcommon.type_ids import read_azure_index

    source = SOURCES["avm-bicep"]
    tar = fetch_cached(source.url, f"avm-{source.pin[:12]}.tar.gz", refresh=refresh)

    # capacitykb의 Azure 파서와 같은 캐시 접두("azure-")를 쓴다 — index.json을
    # 한쪽이 이미 받았으면 재다운로드하지 않는다.
    index_path = fetch_relative(
        SOURCES["bicep-types-az"].url, "index.json",
        cache_prefix="azure-", refresh=refresh,
    )
    type_index = read_azure_index(json.loads(index_path.read_text(encoding="utf-8")))

    graph, report = parse_tarball(tar, type_index=type_index)
    graph.provenance = [describe_source_set([tar], source.key)]

    print(
        f"azure-deploy: 배포 순서 엣지 {len(graph.edges):,}개 "
        f"(모듈 {report.modules}개 · 노드 {len(graph.nodes)}개)"
    )
    if report.telemetry_skipped:
        print(f"  텔레메트리 배포로 건너뛴 쌍 {report.telemetry_skipped}개")
    if report.unknown_types:
        total = sum(report.unknown_types.values())
        print(
            f"  우리 Azure 인덱스에 없는 타입 {len(report.unknown_types)}종({total}건)은 "
            f"담지 않음: {list(report.unknown_types)[:4]}",
            file=sys.stderr,
        )
    graph.save(output)
    return graph
