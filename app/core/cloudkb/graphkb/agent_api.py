"""에이전트용 사전 정의 질의 API.

text-to-Cypher 대신 안전한 정적 질의 함수들을 제공한다 (Phase 3 결정:
LLM이 임의 Cypher를 생성하면 검증이 어렵고 Neo4j 없이는 동작하지 않으므로,
output/의 JSON 그래프를 직접 읽는 사전 정의 질의를 1차 인터페이스로 한다).

모든 함수는 에이전트(LLM)가 그대로 읽을 수 있는 한국어 텍스트를 반환하고,
실패도 예외 대신 안내 메시지로 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.cloudkb.graphkb.model import Graph
from app.core.cloudkb.graphkb.query import (
    _dependency_edges,
    dependency_chain_detail,
    dependents,
    equivalents,
    resolve_node,
)
from app.core.cloudkb.graphkb.query import (
    rank_types as _rank_types,
)
from app.core.cloudkb.kbcommon import artifact
from app.core.cloudkb.kbcommon.basis import describe, is_fact, needs_hedge
from app.core.cloudkb.kbcommon.display import display, evidence_name

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
    "nhn-graph.json",
    # 앱 개념(app::) ↔ 관리형 서비스. 병합에 넣는 것만으로 equivalents()가
    # DynamoDB↔CosmosDB 같은 관리형 대응을 타기 시작한다.
    "svcmap-graph.json",
)

_MISSING_MESSAGE = (
    "No graph artifact found. Build one first with `python -m graphkb build --source "
    "tumblebug|cfn|azure|gcp|mapping`."
)


@lru_cache(maxsize=4)
def _load_merged_cached(output_dir: str) -> Graph | None:
    return artifact.load_merged(output_dir, GRAPH_FILES, Graph, Graph.from_dict)


def load_merged(output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> Graph | None:
    """output/의 그래프 산출물을 모두 병합해 반환한다 (없으면 None, 캐시됨)."""
    return _load_merged_cached(str(output_dir))


def _resolve(graph: Graph, name: str):
    """resolve_node의 예외를 에이전트용 메시지로 바꾼다."""
    try:
        return resolve_node(graph, name), None
    except ValueError as exc:
        return None, str(exc)


#: core 층 노드의 provider 값. 벤더 노드와 구분하는 유일한 표시다.
CORE_PROVIDER = "common"


@dataclass(frozen=True)
class CoreLink:
    """벤더 타입이 core 층에서 무엇인가.

    **이 파일에서 유일하게 텍스트가 아닌 것을 돌려주는 함수의 반환형이다.**
    다른 축(비용·성능)과 조인하려면 문자열이 아니라 값이 필요한데, 문장을 다시
    파싱하는 것은 우리가 모델에게 하지 말라고 하는 바로 그 일이다.
    `perfkb.PerfNote`가 같은 이유로 있다.

    `hedged`는 **다리를 건너며 근거 등급이 떨어졌는가**다. `core::vm`을 직접 물으면
    안 건너므로 False이고, `aws::AWS::EC2::Instance`에서 오면 cb-spider 드라이버를
    읽어 사람이 맞춘 대응(짐작·검수됨)을 한 번 거치므로 True다.
    """

    core_id: str
    hedged: bool


#: 스펙 카탈로그 자체. `core::X --references via specId--> core::spec`인 X만 단가가 붙는다.
SPEC_NODE = "core::spec"


@lru_cache(maxsize=4)
def concepts_with_spec(output_dir: str = str(DEFAULT_OUTPUT_DIR)) -> frozenset[str]:
    """스펙(= 단가)이 붙는 core 개념들.

    **손으로 적지 않고 그래프에서 끌어온다.** `core::vm`만 적어 두면 노드 그룹처럼
    나중에 같은 성질을 갖게 된 개념이 조용히 빠진다 — 이 저장소에서 "같은 목록이
    두 벌"이 갈라진 사고가 세 번 있었다.
    """
    graph = load_merged(output_dir)
    if graph is None:
        return frozenset()
    return frozenset(
        edge.from_id
        for edge in graph.edges
        if edge.to_id == SPEC_NODE and edge.type == "references"
    )


def core_concept(
    type_id: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> CoreLink | None:
    """이 타입이 core 층에서 무엇인가. 대응이 없으면 None.

    비용·성능 축은 **core 층 개념 단위**로만 붙는다(스펙 카탈로그가 곧 `core::spec`
    이므로). 그래서 벤더 타입에서 출발한 질의는 여기를 반드시 거친다.
    """
    graph = load_merged(output_dir)
    if graph is None:
        return None
    node, _ = _resolve(graph, type_id)
    if node is None:
        return None
    if node.provider == CORE_PROVIDER:
        return CoreLink(node.id, hedged=False)
    for peer in equivalents(graph, node.id):
        if peer.provider == CORE_PROVIDER:
            edge = _weakest_link(graph, node.id, peer.id)
            hedged = edge is None or needs_hedge(edge.basis, edge.reviewed)
            return CoreLink(peer.id, hedged=hedged)
    return None


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
        "the schema **marks no prerequisite as required**, which does not mean "
        "nothing is actually needed. Some things the schema leaves optional are "
        "needed in practice (an Azure VM's network interface, for example). Read the "
        "'can be used alongside' list below as well."
    )

    if len(result.ordered) == 1 and (required_only or not optional):
        return f"{node.id} — {_SCHEMA_CAVEAT}"

    lines: list[str] = []
    if len(result.ordered) == 1:
        # 목록을 찍지 않는다 — 대상 자기 자신뿐이라 "1. 대상"은 정보가 아니다.
        lines.append(f"{node.id} — {_SCHEMA_CAVEAT}")
    else:
        lines.append(f"What must exist before {node.id} is created (in this order):")
        for i, step in enumerate(result.steps, start=1):
            if not step.cyclic:
                item = step.nodes[0]
                suffix = " ← target" if item.id == node.id else ""
                lines.append(f"{i}. {item.id}{suffix}")
                continue
            # 순환 그룹은 한 단계로 묶는다 — 이 안의 순서는 스키마로 정할 수 없다.
            lines.append(
                f"{i}. (the {len(step.nodes)} below reference each other, so their "
                "order cannot be determined)"
            )
            for item in step.nodes:
                suffix = " ← target" if item.id == node.id else ""
                lines.append(f"   - {item.id}{suffix}")

    if result.has_cycle:
        # 경고를 **반환 문자열에** 넣는다. 예전엔 stderr로만 나가서, 이 텍스트를 읽는
        # 모델은 순서가 위상순이 아니라는 걸 알 방법이 없었다(결함 C1).
        groups = len(result.cyclic_steps)
        lines.append(
            f"\n⚠ Dependency cycles: {groups}. Items grouped together "
            "cannot be ordered from the schema alone, so when you actually create "
            "them, consider filling the reference in later (create, then update) or "
            "breaking the cycle."
        )

    if optional and not required_only:
        lines.append(
            f"\nCan be used alongside ({len(optional)}, optional so no order is enforced):"
        )
        lines.extend(f"- {t}" for t in optional)

    # 근거를 밝힌다. 용량 축은 5개 도구 전부 근거를 내는데 그래프 축은 버리고
    # 있었다 — 그래서 "짐작(검수됨)"인 관계가 답변에서 단언이 됐다.
    involved = required_ids | set(optional)
    footer = _evidence_footer(graph, involved, node.id)
    if footer:
        lines.append(footer)

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
        f"\n※ **the schema leaves these optional, but in practice they are usually "
        f"needed**: {names}.\n"
        f"  The 'required' list above holds only what this CSP's IaC schema "
        f"(CloudFormation, ARM, and so on) marks as required — that means the schema "
        f"accepts it, not that it alone gives you a usable setup."
    )


#: 이 개수까지는 목록을 통째로 찍는다. 넘으면 묶어서 요약한다.
#:
#: **실측으로 정했다**(2026-07-28, 노드 9,822개 전수). 영향 개수는 중앙값 0·평균 2.3이고,
#: 25를 넘는 노드는 **158개(1.6%)뿐**이다. 즉 이 문턱은 98.4%의 질문에서 답을 한 글자도
#: 바꾸지 않고, 진짜 폭발 반경(VPC 466 · IAM Role 470 · KeyVault 561)만 요약으로 보낸다.
#: 문턱을 20으로 낮춰도 대상이 177개(1.8%)라 거의 같아서, 읽히는 목록 길이 쪽을 택했다.
_SUMMARY_THRESHOLD = 25

#: 요약할 때 보여 줄 서비스 그룹 수와, 그룹마다 이름을 몇 개까지 뽑을지.
_GROUPS_SHOWN = 8
_NAMES_PER_GROUP = 4

#: 묶어도 안 줄어들 때(그룹이 하나뿐) 대신 찍을 앞머리 개수.
_PLAIN_HEAD = 15


def _group_key(type_id: str) -> str:
    """타입 id에서 **묶을 이름**. 서비스 부분이 있으면 서비스, 없으면 프로바이더.

    두 경우뿐이고 **둘 다 id에 실제로 적혀 있는 것**이다.

        aws::AWS::EC2::VPC                     → AWS::EC2        (구분자 `::`)
        azure::Microsoft.KeyVault/vaults       → Microsoft.KeyVault (구분자 `/`)
        gcp::ComputeNetwork · core::subnet     → gcp · core       (구분자 없음)

    **CamelCase를 쪼개지 않는다.** `ComputeNetwork`를 `Compute`로 읽고 싶어지지만
    `PubSubTopic`·`Organization`에서 같은 규칙이 무엇을 낼지 말할 수 없다. 이름을
    짐작해 만든 그룹은 19장이 경고하는 "속성 이름을 짐작하지 마라"와 같은 실패다 —
    묶이지 않는 층은 아래에서 **묶이지 않는다고 말한다.**
    """
    body = type_id.split("::", 1)[1] if "::" in type_id else type_id
    if "::" in body:
        return "::".join(body.split("::")[:2])
    if "/" in body:
        return body.split("/", 1)[0]
    return type_id.split("::", 1)[0] if "::" in type_id else type_id


def _leaf(type_id: str, key: str) -> str:
    """그룹 안에서 그 줄을 **구별하는 부분**만. 못 떼면 통째로 돌려준다.

    그룹 이름이 `AWS::EC2`인데 줄마다 `aws::AWS::EC2::`를 반복하면 70줄이 전부
    같아 보인다 — 13장에서 리전 요약으로 이미 겪은 실패다(39줄이 똑같아 보였는데,
    빠진 그 부분이 그 줄의 뜻 전부였다).
    """
    body = type_id.split("::", 1)[1] if "::" in type_id else type_id
    for sep in ("::", "/"):
        if body.startswith(key + sep):
            return body[len(key) + len(sep) :]
    return type_id


def _summarise_affected(target: str, ids: list[str]) -> list[str]:
    """긴 영향 목록을 서비스별로 묶는다. **버린 것은 반드시 센다.**"""
    groups: dict[str, list[str]] = {}
    for type_id in ids:
        groups.setdefault(_group_key(type_id), []).append(type_id)

    if len(groups) == 1:
        # 묶어도 그룹 줄이 총계 줄과 똑같아진다 — 요약이 아니라 한 줄 늘리는 것이다.
        # GCP(KCC)처럼 타입 이름에 서비스 부분이 없는 층이 여기 온다.
        head = sorted(ids)[:_PLAIN_HEAD]
        return [
            f"Types affected when {target} is deleted ({len(ids)}) — too many to list "
            f"in full, so these are the first {len(head)} by name:",
            *[f"- {t}" for t in head],
            f"… and {len(ids) - len(head)} more. **This is a cut list, not the whole "
            "answer** — these type names carry no service part, so there is nothing "
            "to group them by.",
        ]

    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    shown, rest = ordered[:_GROUPS_SHOWN], ordered[_GROUPS_SHOWN:]
    lines = [
        f"Types affected when {target} is deleted ({len(ids)}) — too many to list in "
        "full, so they are grouped by service. **The per-service counts are complete; "
        "the names under each are a sample.**",
    ]
    for key, members in shown:
        members = sorted(members)
        names = ", ".join(_leaf(m, key) for m in members[:_NAMES_PER_GROUP])
        more = (
            f" … and {len(members) - _NAMES_PER_GROUP} more"
            if len(members) > _NAMES_PER_GROUP
            else ""
        )
        lines.append(f"- {key} ({len(members)}): {names}{more}")
    if rest:
        rest_types = sum(len(m) for _, m in rest)
        singles = sum(1 for _, m in rest if len(m) == 1)
        lines.append(
            f"… and {len(rest)} more services covering {rest_types} types "
            f"({singles} of them a single type)."
        )
    return lines


def deletion_impact(
    resource_type: str,
    *,
    full: bool = False,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """리소스 타입 삭제 시 영향받는(의존하는) 타입을 반환한다.

    **목록을 통째로 찍지 않는다.** `AWS::EC2::VPC` 하나가 466줄, `KeyVault/vaults`가
    561줄이었다. 한 응답이 377,439자였던 사고와 같은 모양이고(13장), 그 길이면
    아래 붙는 근거 꼬리말 — *"이 중 32%는 이름 추론이니 실제 참조를 확인하라"* —
    이 목록에 묻혀 안 읽힌다. **긴 목록이 진짜 경고를 가린다.**

    그래서 `_SUMMARY_THRESHOLD`를 넘으면 서비스별로 묶는다. 요약이 지켜야 하는 것:

    - **총계와 그룹별 개수는 완전하다.** 줄어드는 것은 이름 예시뿐이고, 예시라고 밝힌다.
    - **버린 것을 센다.** "and N more services covering M types" — 조용한 절단은
      "이게 전부"로 읽힌다.
    - **묶을 수 없으면 묶을 수 없다고 말한다**(`_group_key` 참조).

    `full=True`면 예전처럼 전부 돌려준다. 목록 전체가 필요한 프로그램 호출자를 위한
    것이라, **이 사실을 답변 문자열에는 적지 않는다** — 도구 출력에 스위치 이름을
    적으면 모델이 그대로 사용자에게 복사한다(13장의 실측 누출 3/3).
    """
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error
    affected = dependents(graph, node.id)
    if not affected:
        return f"Deleting {node.id} affects no type directly, per the schema."

    ids = [item.id for item in affected]
    if full or len(ids) <= _SUMMARY_THRESHOLD:
        lines = [f"Types affected when {node.id} is deleted ({len(ids)}):"]
        lines.extend(f"- {type_id}" for type_id in ids)
    else:
        lines = _summarise_affected(node.id, ids)

    # 근거 꼬리말은 **요약하든 아니든 전수 기준**이다. 보여 준 8개 그룹이 아니라
    # 영향받는 466건 전체에서 짐작 비율을 내야 그 경고가 사실이다.
    footer = _evidence_footer(graph, set(ids), node.id)
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def _evidence_footer(graph, affected: set[str], target: str) -> str | None:
    """근거를 **블록당 한 번** 밝힌다. 없으면 None.

    줄마다 붙이지 않는 이유는 개수다 — `AWS::EC2::VPC` 삭제 영향이 466건이라
    줄마다 근거를 달면 노이즈가 되고, 노이즈가 되면 진짜 경고가 안 보인다
    (capacitykb의 `_backend_footer`에서 이미 겪은 실패다).

    **짐작이 섞였는지가 핵심이다.** AWS 엣지의 47%가 이름 휴리스틱이라, 이 목록을
    "스키마가 보증하는 관계"로 읽으면 안 된다.
    """
    from collections import Counter

    counts: Counter = Counter()
    for edge in _dependency_edges(graph):
        if edge.from_id in affected or edge.from_id == target:
            if edge.to_id in affected or edge.to_id == target:
                counts[
                    (evidence_name(edge.evidence), describe(edge.basis, edge.reviewed))
                ] += 1
    if not counts:
        return None
    total = sum(counts.values())
    # **"짐작"이었다는 사실은 검수 뒤에도 남는다.** `describe`가 "짐작(검수됨)"을
    # 주므로 그대로 쓴다 — 검수는 확인이지 원본이 선언했다는 뜻이 아니다.
    guessed = sum(n for (_, mark), n in counts.items() if "a guess" in mark)
    top = ", ".join(f"{name} {n} ({mark})" for (name, mark), n in counts.most_common(3))
    line = f"\n※ Evidence for these relationships: {top}"
    if guessed:
        line += (
            f"\n※ Of those, **{guessed} ({guessed / total:.0%}) came out of name "
            "inference**. A person checked them, but the source never declared the "
            "relationship, so confirm the actual references before acting on a "
            "deletion plan."
        )
    return line


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
            f"No equivalent-type information for {node.id} "
            "(mapping-graph.json is missing, or the mapping is not registered)."
        )
    lines = [f"Types that point at the same thing as {node.id}:"]
    guessed = 0
    for item in peers:
        edge = _weakest_link(graph, node.id, item.id)
        if edge is None:
            lines.append(f"- {item.id} ({item.provider})")
            continue
        mark = describe(edge.basis, edge.reviewed)
        # **검수됐어도 짐작은 짐작이다.** `is_fact`로 세면 검수된 것이 빠지는데,
        # 클라우드 간 동치는 검수를 거쳐도 "딱 맞는 짝"이라는 뜻이 아니다 —
        # 사람이 "가장 가깝다"고 판단한 것이지 원본이 선언한 관계가 아니다.
        # 이 갈림이 `needs_hedge`와 `is_fact`가 따로 있는 이유다.
        if needs_hedge(edge.basis, edge.reviewed):
            guessed += 1
        lines.append(
            f"- {item.id} ({item.provider}) — evidence {evidence_name(edge.evidence)}, {mark}"
        )
    if guessed:
        # **짐작을 단언으로 옮기지 못하게 한다.** 실측에서 "AWS ALB는 GCP에서 뭐야?"에
        # 모델이 `ComputeForwardingRule`을 단언했다 — 데이터의 basis는 짐작이었는데
        # 출력에 안 실려서 모델이 알 방법이 없었다.
        lines.append(
            f"\n※ Entries above marked **a guess**: {guessed}. Clouds divide resources "
            "along different lines, so an exact counterpart sometimes does not exist "
            "(a GCP firewall is a network-scoped rule, so it is not the same thing as "
            "an AWS security group, which attaches to an instance). Say 'the closest "
            "thing is', not 'X is Y'."
        )
    if node.layer == "app" or any(item.layer == "app" for item in peers):
        # 관리형 서비스 대응은 svcmap이 잇는다 — **안내이지 배포 가능이 아니다.**
        # cb-tumblebug 실행 경로는 VM·k8s까지라, 이 구분을 빼면 "우리 도구로
        # 만들 수 있다"로 읽힌다(cap_csp_supports가 하는 것과 같은 구분).
        lines.append(
            "\n※ A managed-service counterpart is **guidance, not a guarantee it can "
            "be deployed** — it does not mean cb-tumblebug's execution path (VM, k8s) "
            "can create these services. Deploy them from each cloud's console or IaC."
        )
    return "\n".join(lines)


def _weakest_link(graph, a: str, b: str):
    """`a`↔`b` 동치의 근거 엣지. 추이적이면 **경로에서 가장 약한 고리**.

    동치는 `aws VPC → core vNet → gcp ComputeNetwork`처럼 코어를 거쳐 이어진다.
    그래서 두 벤더 타입 사이에는 직접 엣지가 없고, 경로의 근거가 곧 그 쌍의 근거다.
    **여러 고리 중 하나라도 짐작이면 결과도 짐작**이다 — 사슬은 가장 약한 곳에서
    끊어진다.
    """
    from collections import deque

    links: dict[str, list] = {}
    for edge in graph.edges:
        if edge.type != "equivalent_to":
            continue
        links.setdefault(edge.from_id, []).append((edge.to_id, edge))
        links.setdefault(edge.to_id, []).append((edge.from_id, edge))

    queue = deque([(a, [])])
    seen = {a}
    while queue:
        current, path = queue.popleft()
        if current == b:
            if not path:
                return None
            # 짐작이 하나라도 있으면 그것을, 없으면 마지막 고리를 대표로.
            for edge in path:
                if not is_fact(edge.basis, edge.reviewed):
                    return edge
            return path[-1]
        for nxt, edge in links.get(current, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + [edge]))
    return None


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
        f"- layer: {node.layer} / provider: {node.provider} / source: {node.source}",
    ]
    outgoing = [e for e in graph.edges if e.from_id == node.id]
    if outgoing:
        lines.append("- dependencies (outgoing edges):")
        for edge in sorted(outgoing, key=lambda e: (not e.is_fact, e.to_id)):
            required = "required" if edge.required else "optional"
            via = f" via {edge.via_property}" if edge.via_property else ""
            lines.append(
                f"  · {edge.type} → {display(edge.to_id)}{via} "
                f"({required}, {edge.cardinality}, {evidence_name(edge.evidence)}, "
                f"{describe(edge.basis, edge.reviewed)})"
            )
    else:
        lines.append("- dependencies (outgoing edges): none")
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
        return f"No types available to rank{scope}."

    label = (
        "number of types it directly depends on (what you need to create it)"
        if by == "dependencies"
        else "number of types that depend on it (what is affected when you delete it)"
    )
    scope = f"{provider} " if provider else ""
    lines = [f"Top {len(ranked)} {scope}types by {label}:"]
    for i, (node, count) in enumerate(ranked, start=1):
        lines.append(f"{i}. {node.id} — {count}")
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
        return f"No type matches '{keyword}'{scope}."
    total = len(matches)
    shown = sorted(matches, key=lambda n: n.id)[: max(1, limit)]
    lines = [f"Search results for '{keyword}': {len(shown)} of {total}"]
    lines.extend(f"- {node.id}" for node in shown)
    return "\n".join(lines)
