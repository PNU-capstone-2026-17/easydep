"""capacitykb CLI: build / query 서브커맨드.

사용 예:
    python -m capacitykb build --source cfn
    python -m capacitykb build --source cfn --no-prose
    python -m capacitykb build --source azure --providers microsoft.network
    python -m capacitykb build --source azure-quota
    python -m capacitykb build --source gcp
    python -m capacitykb build --source aws-limits
    python -m capacitykb query --limits AWS::EC2::Volume --property Size
    python -m capacitykb query --check AWS::EC2::Volume --property Size --value 100000
    python -m capacitykb query --immutable AWS::EC2::Subnet
    python -m capacitykb query --quota subnet
    python -m capacitykb audit --graph output/aws-capacity.json
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from capacitykb.model import CapacitySet

DEFAULT_OUTPUTS = {
    "cfn": Path("output") / "aws-capacity.json",
    "azure": Path("output") / "azure-capacity.json",
    "azure-quota": Path("output") / "azure-quota.json",
    "gcp": Path("output") / "gcp-capacity.json",
    "aws-limits": Path("output") / "aws-limits.json",
    "aws-tf": Path("output") / "aws-tf.json",
    "aws-regions": Path("output") / "aws-regions.json",
    "aws-conditional": Path("output") / "aws-conditional.json",
    "aws-endpoints": Path("output") / "aws-endpoints.json",
    "alicloud": Path("output") / "alibaba-capacity.json",
    "tencent": Path("output") / "tencent-capacity.json",
    "ibm": Path("output") / "ibm-capacity.json",
    "ncp": Path("output") / "ncp-capacity.json",
    "openstack": Path("output") / "openstack-capacity.json",
    "nhn": Path("output") / "nhn-capacity.json",
    "oracle": Path("output") / "oracle-capacity.json",
    "azure-mutability": Path("output") / "azure-mutability.json",
    "azure-secret": Path("output") / "azure-secret.json",
    "azure-operations": Path("output") / "azure-operations.json",
}


def _tpcsp_keys() -> frozenset[str]:
    """tpcsp가 다루는 소스 키. **여기서 손으로 적지 않는다.**

    예전엔 이 목록이 `tpcsp.PROVIDERS`와 따로 하드코딩돼 있었다. nhn을 PROVIDERS에
    더했더니 이 분기에 안 걸려 **다음 elif(azure-quota)로 흘러갔고**, 오류 없이
    `nhn-capacity.json`에 Azure 쿼터 542건이 쓰였다. 조용히 틀린 산출물이 나오는
    쪽이 빌드가 죽는 것보다 나쁘다.
    """
    from capacitykb.parsers.tpcsp import PROVIDERS

    return frozenset(PROVIDERS)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capacitykb",
        description="클라우드 리소스 용량·제약 지식베이스",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="공개 스키마·문서에서 제약/쿼터를 추출·저장")
    build.add_argument(
        "--source", choices=tuple(DEFAULT_OUTPUTS), required=True, help="추출 소스"
    )
    build.add_argument("--output", type=Path, help="출력 JSON 경로 (기본: output/)")
    build.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 다운로드")
    build.add_argument(
        "--prose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="설명문에서 수치 제약 추출 (기본: 켜짐, cfn 전용)",
    )
    build.add_argument("--zip-url", help="CFN 스키마 zip URL 또는 로컬 경로")
    build.add_argument("--base-url", help="소스 루트 URL 또는 로컬 디렉터리")
    build.add_argument("--providers", help="azure 전용, 쉼표 구분 (microsoft.network,…)")
    build.add_argument("--tag", help="gcp 전용, KCC 태그 (기본은 kbcommon/sources.py의 핀)")
    build.add_argument("--crd-dir", help="gcp 전용, 네트워크 대신 로컬 CRD 디렉터리를 읽는다")
    build.add_argument(
        "--no-provider", dest="provider", action="store_false",
        help="gcp 전용, terraform-provider-google 릴리스로 값을 보강하지 않는다",
    )
    build.add_argument("--includes", help="azure-quota 전용, 쉼표 구분 (*-limits.md)")

    query = sub.add_parser("query", help="제약/쿼터 질의")
    query.add_argument("--limits", metavar="TYPE", help="타입의 제약 목록")
    query.add_argument("--check", metavar="TYPE", help="값이 허용 범위인지 판정")
    query.add_argument("--immutable", metavar="TYPE", help="변경 시 재생성되는 속성")
    query.add_argument("--quota", metavar="KEYWORD", help="서비스 쿼터 검색")
    query.add_argument("--property", help="대상 속성 (limits/check 용)")
    query.add_argument("--value", help="판정할 값 (check 용)")
    query.add_argument(
        "--facts-only",
        action="store_true",
        help="짐작(산문·이름 추론)에서 나온 제약은 빼고 원본이 명시한 것만 본다",
    )
    query.add_argument(
        "--data",
        type=Path,
        action="append",
        help="산출물 경로 (복수 지정, 기본: output/의 산출물)",
    )

    audit = sub.add_parser(
        "audit", help="산문 추출 교차검증 — 스키마 값과 겹치는 곳의 일치율 측정"
    )
    audit.add_argument("--zip-url", help="CFN 스키마 zip URL 또는 로컬 경로")
    audit.add_argument("--refresh", action="store_true")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    output = args.output or DEFAULT_OUTPUTS[args.source]
    if args.source == "cfn":
        from capacitykb.parsers import cfn

        kwargs: dict = {"prose": args.prose, "refresh": args.refresh}
        if args.zip_url:
            kwargs["zip_url"] = args.zip_url
        cfn.build(output, **kwargs)
    elif args.source == "aws-limits":
        from capacitykb.parsers import aws_limits

        aws_limits.build(output, refresh=args.refresh)
    elif args.source == "aws-tf":
        from capacitykb.parsers import tpaws

        tpaws.build(output, refresh=args.refresh)
    elif args.source == "aws-regions":
        from capacitykb.parsers import cfnlint

        cfnlint.build(output, refresh=args.refresh)
    elif args.source == "aws-conditional":
        from capacitykb.parsers import cfnlint

        cfnlint.build_conditions(output, refresh=args.refresh)
    elif args.source in _tpcsp_keys():
        from capacitykb.parsers import tpcsp

        tpcsp.build(output, key=args.source, refresh=args.refresh)
    elif args.source == "aws-endpoints":
        from capacitykb.parsers import aws_endpoints

        aws_endpoints.build(output, refresh=args.refresh)
    elif args.source == "azure-mutability":
        from capacitykb.parsers import azure_mutability

        azure_mutability.build(output, refresh=args.refresh)
    elif args.source == "azure-secret":
        from capacitykb.parsers import azure_secret

        azure_secret.build(output, refresh=args.refresh)
    elif args.source == "azure-operations":
        from capacitykb.parsers import azure_operations

        azure_operations.build(output, refresh=args.refresh)
    elif args.source == "gcp":
        from capacitykb.parsers import gcp

        kwargs = {"refresh": args.refresh, "provider": args.provider}
        if args.tag:
            kwargs["tag"] = args.tag
        if args.crd_dir:
            kwargs["crd_dir"] = args.crd_dir
        gcp.build(output, **kwargs)
    elif args.source == "azure":
        from capacitykb.parsers import azure

        kwargs = {"refresh": args.refresh}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        if args.providers:
            kwargs["providers"] = tuple(
                p.strip() for p in args.providers.split(",") if p.strip()
            )
        azure.build(output, **kwargs)
    else:
        from capacitykb.parsers import azure_quota

        kwargs = {"refresh": args.refresh}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        if args.includes:
            kwargs["includes"] = tuple(
                i.strip() for i in args.includes.split(",") if i.strip()
            )
        azure_quota.build(output, **kwargs)
    return 0


def _load(paths: list[Path] | None) -> CapacitySet:
    if not paths:
        paths = [p for p in DEFAULT_OUTPUTS.values() if p.exists()]
        if not paths:
            raise FileNotFoundError(
                "산출물이 없습니다. 먼저 `capacitykb build --source cfn` 을 실행하거나 "
                "--data로 경로를 지정하세요."
            )
    merged = CapacitySet()
    for path in paths:
        merged.merge(CapacitySet.load(path))
    return merged


def _coerce(raw: str) -> float | str:
    try:
        number = float(raw)
    except ValueError:
        return raw
    return int(number) if number.is_integer() else number


def _cmd_query(args: argparse.Namespace) -> int:
    from capacitykb import agent_api
    from capacitykb.query import (
        check_value,
        find_quota,
        immutable_properties,
        limits_for,
        resolve_type,
    )

    if not any((args.limits, args.check, args.immutable, args.quota)):
        print(
            "--limits / --check / --immutable / --quota 중 하나를 지정하세요.",
            file=sys.stderr,
        )
        return 1
    capacity = _load(args.data)

    if args.limits:
        type_id = resolve_type(capacity, args.limits)
        found = limits_for(
            capacity,
            type_id,
            prop=args.property,
            facts_only=args.facts_only,
        )
        print(f"{type_id} 제약 {len(found)}건:")
        for item in found:
            print(agent_api._describe(item))

    if args.check:
        if not args.property or args.value is None:
            print("--check 에는 --property 와 --value 가 필요합니다.", file=sys.stderr)
            return 1
        type_id = resolve_type(capacity, args.check)
        kwargs = (
            {"facts_only": args.facts_only}
            if args.facts_only
            else {}
        )
        result = check_value(capacity, type_id, args.property, _coerce(args.value), **kwargs)
        headline = {
            "violation": "불가",
            "advisory": "판정 보류 (확정 제약 위반은 없으나 참고 정보상 벗어남)",
            "ok": "가능",
            "unknown": "판정 불가 — 알려진 제약이 없습니다",
        }
        print(headline[result.verdict])
        for violation in result.violations:
            print(f"  - {violation}")
        for advisory in result.advisories:
            print(f"  참고: {advisory}")

    if args.immutable:
        type_id = resolve_type(capacity, args.immutable)
        found = immutable_properties(capacity, type_id)
        print(f"{type_id} 변경 시 재생성되는 속성 {len(found)}개:")
        for item in found:
            print(agent_api._describe(item))

    if args.quota:
        found = find_quota(capacity, args.quota)
        print(f"'{args.quota}' 쿼터 {len(found)}건:")
        for quota in found:
            maximum = f" / 최대 {quota.maximum}" if quota.maximum is not None else ""
            print(f"  - [{quota.provider}] {quota.name}: 기본 {quota.default}{maximum}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    """산문 추출을 스키마 값이 있는 프로퍼티에도 돌려 일치율을 잰다.

    정답이 있는 시험지라 정밀도 프록시가 된다 — 불일치가 1건이라도 나오면
    정규식 버그 신호이므로 실패로 처리한다.
    """
    from capacitykb.parsers.cfn import DEFAULT_ZIP_URL, _scalar_type, iter_schemas
    from capacitykb.prose import extract_ranges
    from kbcommon.fetch import fetch_cached

    zip_path = fetch_cached(
        args.zip_url or DEFAULT_ZIP_URL, "CloudformationSchema.zip", refresh=args.refresh
    )
    overlap = 0
    mismatches: list[str] = []

    def walk(node: dict, type_name: str) -> None:
        nonlocal overlap
        if not isinstance(node, dict):
            return
        for name, prop in (node.get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            if _scalar_type(prop) in ("integer", "number") and prop.get("description"):
                found = {e.kind: e.value for e in extract_ranges(prop["description"])}
                for kind, keyword in (("min", "minimum"), ("max", "maximum")):
                    if keyword in prop and kind in found:
                        overlap += 1
                        if found[kind] != prop[keyword]:
                            mismatches.append(
                                f"{type_name}.{name} [{kind}]: 산문={found[kind]} "
                                f"vs 스키마={prop[keyword]}"
                            )
            walk(prop, type_name)
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, type_name)

    for schema in iter_schemas(zip_path):
        type_name = schema.get("typeName")
        if not type_name:
            continue
        walk(schema, type_name)
        for definition in (schema.get("definitions") or {}).values():
            walk(definition, type_name)

    print(f"산문 교차검증: 겹침 {overlap}건, 불일치 {len(mismatches)}건")
    for line in mismatches:
        print(f"  - {line}", file=sys.stderr)
    return 1 if mismatches else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {"build": _cmd_build, "query": _cmd_query, "audit": _cmd_audit}
    try:
        return handlers[args.command](args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI 사용자에게 traceback 대신 메시지
        print(f"예기치 못한 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
