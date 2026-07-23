"""graphkb CLI: build / query / export 서브커맨드.

사용 예:
    python -m graphkb build --source tumblebug
    python -m graphkb build --source cfn --no-heuristics
    python -m graphkb build --source azure --providers microsoft.network
    python -m graphkb build --source gcp --services compute,container
    python -m graphkb build --source mapping
    python -m graphkb suggest-mapping
    python -m graphkb query --deps vm
    python -m graphkb query --deps AWS::EC2::Subnet --required-only
    python -m graphkb export --format dot --graph output/core-graph.json
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from graphkb.model import Graph

DEFAULT_OUTPUTS = {
    "tumblebug": Path("output") / "core-graph.json",
    "cfn": Path("output") / "aws-graph.json",
    "azure": Path("output") / "azure-graph.json",
    "gcp": Path("output") / "gcp-graph.json",
    "mapping": Path("output") / "mapping-graph.json",
    # AVM 배포 순서는 azure-graph를 덮지 않고 옆에 둔다 — 근거 성격이 다르고,
    # 기존 빌드를 안 건드려야 회귀를 구분할 수 있다.
    "avm": Path("output") / "azure-deploy-graph.json",
    # 앱 개념 ↔ 관리형 서비스. core-graph(tumblebug 미러)에 섞지 않는다.
    "svcmap": Path("output") / "svcmap-graph.json",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graphkb",
        description="멀티클라우드 리소스 타입 의존성 그래프 지식베이스",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="공개 스키마에서 그래프를 추출·저장")
    build.add_argument(
        "--source",
        choices=("tumblebug", "cfn", "azure", "gcp", "mapping", "avm", "svcmap"),
        required=True,
        help="추출 소스",
    )
    build.add_argument("--output", type=Path, help="출력 JSON 경로 (기본: output/)")
    build.add_argument(
        "--refresh", action="store_true", help="캐시를 무시하고 다시 다운로드"
    )
    build.add_argument("--swagger-url", help="tumblebug swagger URL 또는 로컬 경로")
    build.add_argument("--zip-url", help="CFN 스키마 zip URL 또는 로컬 경로")
    build.add_argument("--oob-url", help="CDK 관계 데이터 URL 또는 로컬 경로")
    build.add_argument(
        "--heuristics",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="속성명 휴리스틱 (기본: tumblebug=off, 나머지=on)",
    )
    build.add_argument(
        "--no-cdk-oob",
        action="store_true",
        help="CDK out-of-band 관계 데이터를 쓰지 않음 (cfn 전용)",
    )
    build.add_argument(
        "--base-url", help="bicep-types-az generated 루트 URL 또는 로컬 경로 (azure 전용)"
    )
    build.add_argument(
        "--providers",
        help="참조 엣지를 추출할 프로바이더 (azure 전용, 쉼표 구분: microsoft.network,...)",
    )
    build.add_argument("--tag", help="k8s-config-connector 태그 (gcp 전용)")
    build.add_argument(
        "--services", help="내려받을 KCC 서비스 (gcp 전용, 쉼표 구분: compute,container)"
    )
    build.add_argument(
        "--crd-dir", help="로컬 CRD 디렉터리 — 지정 시 네트워크 미사용 (gcp 전용)"
    )
    build.add_argument(
        "--mapping-file", type=Path, help="검수 완료 매핑 JSON (mapping 전용)"
    )

    suggest = sub.add_parser(
        "suggest-mapping", help="이름 유사도 기반 매핑 후보 생성 (사람 검수용)"
    )
    suggest.add_argument(
        "--core-graph",
        type=Path,
        default=DEFAULT_OUTPUTS["tumblebug"],
        help="코어 그래프 경로 (기본: output/core-graph.json)",
    )
    suggest.add_argument(
        "--vendor-graph",
        type=Path,
        action="append",
        help="벤더 그래프 경로 (복수 지정, 기본: output/의 aws/azure/gcp 산출물)",
    )
    suggest.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "mapping-candidates.json",
        help="후보 파일 출력 경로",
    )

    query = sub.add_parser("query", help="그래프 질의")
    query.add_argument("--deps", metavar="TYPE", help="TYPE의 선행 리소스 체인 출력")
    query.add_argument(
        "--dependents", metavar="TYPE", help="TYPE 삭제 시 영향받는 타입 출력"
    )
    query.add_argument(
        "--rank",
        choices=("dependencies", "dependents"),
        help="의존 관계가 많은 타입 순위 (전체 집계)",
    )
    query.add_argument("--provider", help="순위 대상 프로바이더 (aws|azure|gcp|common)")
    query.add_argument("--limit", type=int, default=10, help="순위 상위 개수 (기본 10)")
    query.add_argument(
        "--graph",
        type=Path,
        action="append",
        help="그래프 JSON 경로 (복수 지정 가능, 기본: output/의 산출물)",
    )
    query.add_argument(
        "--required-only", action="store_true", help="required 엣지만 제약으로 취급"
    )

    export = sub.add_parser("export", help="검토용 GraphML/DOT/Cypher 내보내기")
    export.add_argument("--format", choices=("graphml", "dot", "cypher"), required=True)
    export.add_argument("--graph", type=Path, required=True, help="그래프 JSON 경로")
    export.add_argument("--output", type=Path, help="출력 경로 (기본: 입력과 같은 이름)")

    load = sub.add_parser("load-neo4j", help="그래프를 Neo4j에 적재 (드라이버 필요)")
    load.add_argument(
        "--graph",
        type=Path,
        action="append",
        help="적재할 그래프 JSON (복수 지정, 기본: output/의 산출물 전체 병합)",
    )
    load.add_argument("--uri", default="bolt://localhost:7687", help="bolt URI")
    load.add_argument("--user", default="neo4j", help="사용자명 (기본 neo4j)")
    load.add_argument(
        "--password", help="비밀번호 (기본: NEO4J_PASSWORD 환경변수)"
    )
    load.add_argument("--database", default="neo4j", help="데이터베이스 (기본 neo4j)")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    output = args.output or DEFAULT_OUTPUTS[args.source]
    heuristics_on = True if args.heuristics is None else args.heuristics

    if args.source == "tumblebug":
        from graphkb.parsers import tumblebug

        kwargs: dict = {
            "heuristics": bool(args.heuristics),
            "refresh": args.refresh,
        }
        if args.swagger_url:
            kwargs["swagger_url"] = args.swagger_url
        tumblebug.build(output, **kwargs)
    elif args.source == "cfn":
        from graphkb.parsers import cfn

        kwargs = {
            "heuristics": heuristics_on,
            "cdk_oob": not args.no_cdk_oob,
            "refresh": args.refresh,
        }
        if args.zip_url:
            kwargs["zip_url"] = args.zip_url
        if args.oob_url:
            kwargs["oob_url"] = args.oob_url
        cfn.build(output, **kwargs)
    elif args.source == "azure":
        from graphkb.parsers import azure

        kwargs = {"heuristics": heuristics_on, "refresh": args.refresh}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        if args.providers:
            kwargs["providers"] = tuple(
                p.strip() for p in args.providers.split(",") if p.strip()
            )
        azure.build(output, **kwargs)
    elif args.source == "avm":
        from graphkb.parsers import avm

        avm.build(output, refresh=args.refresh)
    elif args.source == "svcmap":
        from graphkb.parsers import svcmap

        svcmap.build(output)
    elif args.source == "gcp":
        from graphkb.parsers import gcp

        kwargs = {"heuristics": heuristics_on, "refresh": args.refresh}
        if args.tag:
            kwargs["tag"] = args.tag
        if args.services:
            kwargs["services"] = tuple(
                s.strip() for s in args.services.split(",") if s.strip()
            )
        if args.crd_dir:
            kwargs["crd_dir"] = args.crd_dir
        gcp.build(output, **kwargs)
    else:  # mapping
        from graphkb.parsers import mapping

        mapping.build(output, mapping_file=args.mapping_file)
    return 0


def _cmd_suggest_mapping(args: argparse.Namespace) -> int:
    from graphkb.parsers.mapping import write_candidates

    if not args.core_graph.exists():
        raise FileNotFoundError(
            f"코어 그래프가 없습니다: {args.core_graph} — 먼저 "
            "`graphkb build --source tumblebug`을 실행하세요."
        )
    core_graph = Graph.load(args.core_graph)

    vendor_paths = args.vendor_graph or [
        DEFAULT_OUTPUTS[s] for s in ("cfn", "azure", "gcp") if DEFAULT_OUTPUTS[s].exists()
    ]
    if not vendor_paths:
        raise FileNotFoundError(
            "벤더 그래프가 없습니다. 먼저 build --source cfn|azure|gcp 를 실행하거나 "
            "--vendor-graph로 경로를 지정하세요."
        )
    vendor_graphs: dict[str, Graph] = {}
    for path in vendor_paths:
        graph = Graph.load(path)
        providers = {n.provider for n in graph.nodes.values()}
        for provider in providers:
            vendor_graphs[provider] = graph
    write_candidates(args.output, core_graph, vendor_graphs)
    return 0


def _load_graphs(paths: list[Path] | None) -> Graph:
    if not paths:
        paths = [p for p in DEFAULT_OUTPUTS.values() if p.exists()]
        if not paths:
            raise FileNotFoundError(
                "그래프 파일이 없습니다. 먼저 `graphkb build`를 실행하거나 "
                "--graph로 경로를 지정하세요."
            )
    merged = Graph()
    for path in paths:
        merged.merge(Graph.load(path))
    return merged


def _cmd_query(args: argparse.Namespace) -> int:
    from graphkb.query import (
        dependency_chain_detail,
        dependents,
        rank_types,
        resolve_node,
    )

    if not args.deps and not args.dependents and not args.rank:
        print("--deps / --dependents / --rank 중 하나를 지정하세요.", file=sys.stderr)
        return 1
    graph = _load_graphs(args.graph)

    if args.rank:
        ranked = rank_types(
            graph,
            by=args.rank,
            provider=args.provider,
            limit=args.limit,
            required_only=args.required_only,
        )
        label = "의존하는 타입 수" if args.rank == "dependencies" else "의존받는 타입 수"
        scope = f"{args.provider} " if args.provider else ""
        print(f"{scope}타입 {label} 상위 {len(ranked)}개:")
        for i, (node, count) in enumerate(ranked, start=1):
            print(f"  {i}. {node.id} — {count}개")

    if args.deps:
        node = resolve_node(graph, args.deps)
        result = dependency_chain_detail(graph, node.id, required_only=args.required_only)
        print(f"{node.id} 생성에 필요한 선행 체인 (위상순):")
        for i, step in enumerate(result.steps, start=1):
            if step.cyclic:
                print(f"  {i}. [순환 {len(step.nodes)}개 — 내부 순서 미정]")
            for item in step.nodes:
                marker = " (self)" if item.id == node.id else ""
                bullet = "     -" if step.cyclic else f"  {i}."
                print(f"{bullet} {item.id}{marker}")
        if result.has_cycle:
            print(f"\n⚠ 의존성 순환 {len(result.cyclic_steps)}곳 — 묶인 항목은 선후 미정")

    if args.dependents:
        node = resolve_node(graph, args.dependents)
        affected = dependents(graph, node.id)
        print(f"{node.id} 삭제 시 영향받는 타입 {len(affected)}개:")
        for item in affected:
            print(f"  - {item.id}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    graph = Graph.load(args.graph)
    suffixes = {"graphml": ".graphml", "dot": ".dot", "cypher": ".cypher"}
    output = args.output or args.graph.with_suffix(suffixes[args.format])
    if args.format == "graphml":
        from graphkb.export import write_graphml

        write_graphml(graph, output)
    elif args.format == "dot":
        from graphkb.export import write_dot

        write_dot(graph, output)
    else:
        from graphkb.export import write_cypher

        write_cypher(graph, output)
    print(f"내보내기 완료 → {output}")
    return 0


def _cmd_load_neo4j(args: argparse.Namespace) -> int:
    import os

    from graphkb.neo4j_load import load_to_neo4j

    graph = _load_graphs(args.graph)
    password = args.password or os.environ.get("NEO4J_PASSWORD")
    if not password:
        print(
            "오류: 비밀번호가 없습니다. --password 또는 NEO4J_PASSWORD 환경변수를 지정하세요.",
            file=sys.stderr,
        )
        return 1
    counts = load_to_neo4j(
        graph,
        uri=args.uri,
        user=args.user,
        password=password,
        database=args.database,
    )
    print(
        f"Neo4j 적재 완료: 노드 {counts['nodes']}개, 엣지 {counts['edges']}개 "
        f"→ {args.uri} (db={args.database})"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "build": _cmd_build,
        "query": _cmd_query,
        "export": _cmd_export,
        "suggest-mapping": _cmd_suggest_mapping,
        "load-neo4j": _cmd_load_neo4j,
    }
    try:
        return handlers[args.command](args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI 사용자에게 traceback 대신 메시지
        print(f"예기치 못한 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
