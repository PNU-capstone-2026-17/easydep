"""에이전트용 사전 정의 질의 API.

text-to-Cypher 대신 안전한 정적 질의 함수들을 제공한다 (Phase 3 결정:
LLM이 임의 Cypher를 생성하면 검증이 어렵고 Neo4j 없이는 동작하지 않으므로,
output/의 JSON 그래프를 직접 읽는 사전 정의 질의를 1차 인터페이스로 한다).

모든 함수는 에이전트(LLM)가 그대로 읽을 수 있는 한국어 텍스트를 반환하고,
실패도 예외 대신 안내 메시지로 돌려준다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from graphkb.model import Graph
from graphkb.query import (
    _dependency_edges,
    dependency_chain_detail,
    dependents,
    equivalents,
    rank_types as _rank_types,
    resolve_node,
)
from kbcommon import artifact
from kbcommon.basis import describe
from kbcommon.display import display, evidence_name

DEFAULT_OUTPUT_DIR = Path("output")
GRAPH_FILES = (
    "core-graph.json",
    "aws-graph.json",
    "azure-graph.json",
    "gcp-graph.json",
    "mapping-graph.json",
    "azure-deploy-graph.json",
    "alibaba-graph.json",
    "tencent-graph.json",
    "ibm-graph.json",
    "ncp-graph.json",
    "openstack-graph.json",
    "oracle-graph.json",
)

_MISSING_MESSAGE = (
    "그래프 산출물이 없습니다. 먼저 `python -m graphkb build --source "
    "tumblebug|cfn|azure|gcp|mapping` 으로 그래프를 생성하세요."
)


@lru_cache(maxsize=4)
def _load_merged_cached(output_dir: str) -> Graph | None:
    base = Path(output_dir)
    merged = Graph()
    found = False
    for name in GRAPH_FILES:
        # output/ 이 먼저, 없으면 저장소에 커밋된 data/*.gz (kbcommon/artifact.py).
        path = artifact.resolve(base, name)
        if path is not None:
            merged.merge(Graph.from_dict(artifact.load_json(path)))
            found = True
    return merged if found else None


def load_merged(output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> Graph | None:
    """output/의 그래프 산출물을 모두 병합해 반환한다 (없으면 None, 캐시됨)."""
    return _load_merged_cached(str(output_dir))


def _resolve(graph: Graph, name: str):
    """resolve_node의 예외를 에이전트용 메시지로 바꾼다."""
    try:
        return resolve_node(graph, name), None
    except ValueError as exc:
        return None, str(exc)


def creation_order(
    resource_type: str,
    *,
    required_only: bool = False,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """생성 순서를 **필수와 선택으로 나눠** 텍스트로 반환한다.

    예전에는 필수·선택을 섞어 전방 폐포를 통째로 위상정렬했다. 그러면 선택적 연결까지
    다 따라가느라 관계없는 타입 수십 개가 딸려 들어와 뒤엉킨다 — `S3::BucketPolicy`가
    15단계에 순환 3그룹으로 나왔고, 실제 답은 "저장소 먼저, 정책 나중" 두 단계였다.

    그렇다고 필수만 보여줄 수도 없다. 스키마상 `required`가 아닌 실질 의존이 많아서
    **469개 타입이 "선행 리소스 없음"**이 되는데, `APS::Scraper`처럼 실제로는 구역·
    작업공간이 필요한 것들이라 명백한 거짓말이다. 뒤엉킨 답보다 나쁘다.

    그래서 둘 다 낸다:
    - **필수 체인** — 위상순. 순서를 강제하는 것만이라 깨끗하다(평균 1.8단계, 순환 4종).
    - **직접 선택 의존** — 순서 없이 목록. 전이 폐포가 아니라 1홉만 본다
      (평균 0.3개·중앙값 0이라 답을 늘리지 않는다).
    """
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error

    result = dependency_chain_detail(graph, node.id, required_only=True)
    required_ids = {n.id for n in result.ordered}
    optional = sorted(
        {
            edge.to_id
            for edge in _dependency_edges(graph)
            if edge.from_id == node.id and not edge.required
            and edge.to_id not in required_ids
        }
    )

    # **"필수 표시가 없다"를 "바로 만들어도 된다"로 바꿔 말하지 않는다.**
    # 예전 문구는 "바로 생성할 수 있습니다"였다. 스키마의 필수 표시는 실제 배포
    # 요건과 다르다 — Azure VM은 `networkProfile`이 스키마상 선택이라 필수 목록이
    # 비지만, NIC 없이는 만들 수 없다. 침묵을 허가로 바꾸면 fail-open이 아니라
    # 그냥 틀린 말이 된다.
    _SCHEMA_CAVEAT = (
        "스키마가 **필수로 표시한** 선행 리소스가 없다는 뜻이며, 실제로 아무것도 "
        "필요 없다는 뜻이 아닙니다. 스키마가 선택으로 두지만 실무에서는 있어야 하는 "
        "것이 있습니다(예: Azure VM의 네트워크 인터페이스). 아래 '함께 쓸 수 있는 것'을 "
        "함께 보세요."
    )

    if len(result.ordered) == 1 and (required_only or not optional):
        return f"{node.id} — {_SCHEMA_CAVEAT}"

    lines: list[str] = []
    if len(result.ordered) == 1:
        # 목록을 찍지 않는다 — 대상 자기 자신뿐이라 "1. 대상"은 정보가 아니다.
        lines.append(f"{node.id} — {_SCHEMA_CAVEAT}")
    else:
        lines.append(f"{node.id} 생성에 반드시 먼저 있어야 하는 것 (이 순서대로):")
        for i, step in enumerate(result.steps, start=1):
            if not step.cyclic:
                item = step.nodes[0]
                suffix = " ← 대상" if item.id == node.id else ""
                lines.append(f"{i}. {item.id}{suffix}")
                continue
            # 순환 그룹은 한 단계로 묶는다 — 이 안의 순서는 스키마로 정할 수 없다.
            lines.append(
                f"{i}. (아래 {len(step.nodes)}개는 서로 참조해 순서를 정할 수 없습니다)"
            )
            for item in step.nodes:
                suffix = " ← 대상" if item.id == node.id else ""
                lines.append(f"   - {item.id}{suffix}")

    if result.has_cycle:
        # 경고를 **반환 문자열에** 넣는다. 예전엔 stderr로만 나가서, 이 텍스트를 읽는
        # 모델은 순서가 위상순이 아니라는 걸 알 방법이 없었다(결함 C1).
        groups = len(result.cyclic_steps)
        lines.append(
            f"\n⚠ 의존성 순환이 {groups}곳 있습니다. 묶인 항목끼리는 스키마만으로 "
            "선후를 정할 수 없으니, 실제 생성 시에는 참조를 나중에 채우거나"
            "(예: 생성 후 업데이트) 순환을 끊는 방식을 검토하세요."
        )

    if optional and not required_only:
        lines.append(
            f"\n함께 쓸 수 있는 것 ({len(optional)}개, 선택이라 순서를 강제하지 않습니다):"
        )
        lines.extend(f"- {t}" for t in optional)

    practical = _practical_prerequisites(graph, node.id)
    if practical:
        lines.append(practical)
    return "\n".join(lines)


def _practical_prerequisites(graph, type_id: str) -> str | None:
    """**IaC 스키마가 선택으로 두지만 실무에서는 필요한 것.** 없으면 None.

    같은 것을 묻는데 답이 갈리는 문제가 있었다(사용자 지적으로 드러남):

        core::vm                  반드시 먼저 있어야 하는 것 **7개**
        aws::AWS::EC2::Instance   필수 **0개**

    둘 다 사실이고 근거가 다르다. 벤더 쪽은 CFN/ARM이라 `SubnetId`가 선택이고
    (기본 VPC를 쓸 수 있으니까), 코어 쪽은 **여러 CSP에 공통으로 필요한 구성**을
    담은 층이라 네트워크·서브넷·보안그룹을 필수로 본다. 스키마만 보고 "필수 없음"
    이라 답하면 **VM 하나 만들려는 사람에게 쓸모없는 답**이 된다.

    ## 도구 고유 개념과 카탈로그는 빼야 한다

    코어 층의 필수 목록을 그대로 옮기면 안 된다. `core::vm`의 7개에는 성격이
    다른 셋이 섞여 있다:

        만드는 리소스   vNet · subnet · securityGroup · sshKey
        고르는 카탈로그  spec · image          ← 만드는 게 아니라 선택하는 값
        도구 고유 개념   mci                   ← cb-tumblebug의 묶음 단위

    "EC2를 만들려면 mci가 필요하다"는 **가이드라인으로서 거짓**이다 — AWS에 그런
    것은 없다. 우리는 배포기가 아니라 **가이드라인 지식베이스**를 만들므로, 특정
    도구의 요구사항을 클라우드의 사실인 양 말하면 안 된다.

    판정 근거는 이미 데이터에 있다. `core_vendor_map`이 **spec·image·mci는
    등가물이 없다**고 명시하므로, **벤더 대응물이 있는 코어 타입만** 옮기면 셋이
    자동으로 걸러진다. 손으로 목록을 또 만들 필요가 없다.
    """
    if type_id.startswith("core::"):
        return None
    core_ids = {
        edge.from_id
        for edge in graph.edges
        if edge.type == "equivalent_to"
        and edge.to_id == type_id
        and edge.from_id.startswith("core::")
    }
    if not core_ids:
        return None
    # 같은 프로바이더에서 대응물이 있는 코어 타입만 — 도구 고유 개념·카탈로그 제외.
    provider = type_id.split("::", 1)[0]
    mapped = {
        edge.from_id
        for edge in graph.edges
        if edge.type == "equivalent_to"
        and edge.from_id.startswith("core::")
        and edge.to_id.startswith(f"{provider}::")
    }
    needed: set[str] = set()
    for core_id in core_ids:
        needed |= {
            edge.to_id
            for edge in graph.edges
            if edge.from_id == core_id
            and edge.required
            and edge.to_id != core_id
            and edge.to_id in mapped
        }
    if not needed:
        return None
    names = ", ".join(sorted(n.replace("core::", "") for n in needed))
    return (
        f"\n※ **스키마는 선택으로 두지만 실무에서는 보통 필요합니다**: {names}.\n"
        f"  위 '필수' 목록은 이 CSP의 IaC 스키마(CloudFormation·ARM 등)가 "
        f"required로 표시한 것만입니다 — 스키마가 받아준다는 뜻이지 그것만으로 "
        f"쓸 수 있는 구성이 된다는 뜻은 아닙니다."
    )


def deletion_impact(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """리소스 타입 삭제 시 영향받는(의존하는) 타입 목록을 반환한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error
    affected = dependents(graph, node.id)
    if not affected:
        return f"{node.id} 를 삭제해도 스키마상 직접 영향받는 타입은 없습니다."
    lines = [f"{node.id} 삭제 시 영향받는 타입 {len(affected)}개:"]
    lines.extend(f"- {item.id}" for item in affected)
    return "\n".join(lines)


def equivalent_types(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """다른 클라우드/코어 레이어에서 같은 것을 가리키는 타입들을 반환한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error
    peers = equivalents(graph, node.id)
    if not peers:
        return (
            f"{node.id} 의 동치 타입 정보가 없습니다 "
            "(mapping-graph.json이 없거나 매핑 미등록)."
        )
    lines = [f"{node.id} 와 같은 것을 가리키는 타입:"]
    lines.extend(f"- {item.id} ({item.provider})" for item in peers)
    return "\n".join(lines)


def describe_type(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """타입의 기본 정보와 나가는 의존 엣지(참조/포함)를 상세히 반환한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error
    lines = [
        display(node.id),
        f"- 레이어: {node.layer} / 프로바이더: {node.provider} / 출처: {node.source}",
    ]
    outgoing = [e for e in graph.edges if e.from_id == node.id]
    if outgoing:
        lines.append("- 의존(나가는 엣지):")
        for edge in sorted(outgoing, key=lambda e: (not e.is_fact, e.to_id)):
            required = "필수" if edge.required else "선택"
            via = f" via {edge.via_property}" if edge.via_property else ""
            lines.append(
                f"  · {edge.type} → {display(edge.to_id)}{via} "
                f"({required}, {edge.cardinality}, {evidence_name(edge.evidence)}, "
                f"{describe(edge.basis, edge.reviewed)})"
            )
    else:
        lines.append("- 의존(나가는 엣지): 없음")
    return "\n".join(lines)


def rank_types(
    by: str = "dependencies",
    *,
    provider: str | None = None,
    limit: int = 10,
    required_only: bool = False,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """의존 관계가 가장 많은 타입 순위를 반환한다 (전체 집계 질의)."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    try:
        ranked = _rank_types(
            graph, by=by, provider=provider, limit=limit, required_only=required_only
        )
    except ValueError as exc:
        return str(exc)
    if not ranked:
        scope = f" (provider={provider})" if provider else ""
        return f"순위를 낼 수 있는 타입이 없습니다{scope}."

    label = (
        "직접 의존하는 타입 수(이 타입을 만들려면 필요한 것)"
        if by == "dependencies"
        else "이 타입에 의존하는 타입 수(삭제 시 영향받는 것)"
    )
    scope = f"{provider} " if provider else ""
    lines = [f"{scope}타입 중 {label} 상위 {len(ranked)}개:"]
    for i, (node, count) in enumerate(ranked, start=1):
        lines.append(f"{i}. {node.id} — {count}개")
    return "\n".join(lines)


def count_types(
    keyword: str,
    *,
    provider: str | None = None,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> int:
    """`search_types`가 몇 건을 찾는지. 텍스트를 되파싱하지 않기 위한 것.

    호출부가 "타입을 못 찾았을 때만" 다른 축을 안내하려면 이 수가 필요하다.
    반환 문자열에서 "타입이 없습니다"를 문자열 매칭하면 문구를 다듬는 순간
    조용히 깨진다.
    """
    graph = load_merged(output_dir)
    if graph is None:
        return 0
    return len(_matching_nodes(graph, keyword, provider))


def _matching_nodes(graph, keyword: str, provider: str | None) -> list:
    lowered = keyword.lower()
    return [
        node
        for node in graph.nodes.values()
        if lowered in node.id.lower()
        and (provider is None or node.provider == provider)
    ]


def search_types(
    keyword: str,
    *,
    provider: str | None = None,
    limit: int = 20,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """키워드(부분 문자열, 대소문자 무시)로 리소스 타입을 검색한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    matches = _matching_nodes(graph, keyword, provider)
    if not matches:
        scope = f" (provider={provider})" if provider else ""
        return f"'{keyword}'{scope} 에 해당하는 타입이 없습니다."
    total = len(matches)
    shown = sorted(matches, key=lambda n: n.id)[: max(1, limit)]
    lines = [f"'{keyword}' 검색 결과 {total}개 중 {len(shown)}개:"]
    lines.extend(f"- {node.id}" for node in shown)
    return "\n".join(lines)
