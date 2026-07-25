"""cb-tumblebug 자산 → 사이징 규칙 넷.

같은 tarball을 bundlekb도 읽는다. 저쪽은 `init/templates`의 **구성**을 보고 이쪽은
**규모**를 본다.

    assets/networkinfo.yaml     서브넷 예약 IP · vNet/서브넷 프리픽스 범위
    assets/k8sclusterinfo.yaml  클러스터가 요구하는 서브넷 수 등
    init/templates/*.json       워크로드별 스펙 참조점
    src/core/infra/recommendation.go  K8s 노드 최소치 (사람이 읽어 확정)

## 채움이 고르지 않다 — 비어 있는 것을 0으로 읽으면 안 된다

`networkinfo.yaml`은 손 큐레이션이라 CSP마다 채움이 다르다. **AWS의 예약 IP 칸이
비어 있는데 AWS는 실제로 5개를 예약한다.** 즉 이 파일의 공백은 "예약이 없다"가 아니라
"이 파일이 안 적었다"이다. 비어 있으면 **규칙을 만들지 않는다** — 만들면 251대 자리에
256대라고 답하게 된다.

## 참조점은 정답이 아니다

`infra-usecase-*`는 이 프로젝트가 만든 예시 인프라다. "웹서버는 t3.small이 정답"이
아니라 "이 소스의 웹서버 예시가 t3.small"이다. 그 차이를 `note`와 `caveat`에 적는다.
"""

from __future__ import annotations

import json
import sys
import tarfile
from collections import Counter
from pathlib import Path

import yaml

from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES
from sizingkb.model import (
    MINIMUM,
    REFERENCE_POINT,
    REQUIRED_COUNT,
    RESERVED_IPS,
    Rule,
    RuleSet,
)

EVIDENCE_NETWORK = "tumblebug-networkinfo"
EVIDENCE_K8S = "tumblebug-k8sinfo"
EVIDENCE_TEMPLATE = "tumblebug-template"
EVIDENCE_CODE = "tumblebug-dynamic"

#: `recommendation.go`의 `validateK8sMinimumRequirements`를 사람이 읽어 확정했다.
#: 핀이 올라가면 다시 읽어야 한다.
_READ_AT_PIN = "v0.12.25"
_K8S_NODE_MIN = (("vCPU", 2, None), ("memoryGiB", 4.0, "GiB"))

#: k8sclusterinfo.yaml에서 규모에 해당하는 칸만 뽑는다. 나머지(버전 목록 등)는
#: 사이징이 아니라 호환성이라 여기 담지 않는다.
_K8S_COUNT_FIELDS = {
    "requiredSubnetCount": ("subnets", "subnets required to create a cluster"),
}


def _load(archive: tarfile.TarFile, suffix: str):
    for member in archive:
        if member.isfile() and member.name.replace("\\", "/").endswith(suffix):
            return archive.extractfile(member).read()
    return None


class Report:
    def __init__(self) -> None:
        self.kinds: Counter = Counter()
        self.no_reserved: list[str] = []


def parse_tarball(tar: Path) -> tuple[RuleSet, Report]:
    out = RuleSet()
    report = Report()

    with tarfile.open(tar, "r:gz") as archive:
        members = {m.name.replace("\\", "/"): m for m in archive if m.isfile()}

        def read(suffix: str):
            for name, member in members.items():
                if name.endswith(suffix):
                    return archive.extractfile(member).read()
            return None

        # --- 1. 네트워크 계획 ---
        raw = read("/assets/networkinfo.yaml")
        if raw:
            network = (yaml.safe_load(raw) or {}).get("network") or {}
            for csp, info in sorted(network.items()):
                subnet = (info or {}).get("subnet") or {}
                reserved = ((subnet.get("reserved-ips") or {}).get("value"))
                if reserved is None:
                    # **비어 있는 것을 0으로 읽지 않는다.** AWS가 실제로 그 경우다.
                    report.no_reserved.append(csp)
                    continue
                out.add(
                    Rule(
                        id=f"tumblebug::reserved-ips/{csp}",
                        kind=RESERVED_IPS,
                        scope=csp,
                        metric="reservedIps",
                        value=int(reserved),
                        unit="IPs",
                        evidence=EVIDENCE_NETWORK,
                        note=(subnet.get("reserved-ips") or {}).get("description"),
                    )
                )
                report.kinds[RESERVED_IPS] += 1

        # --- 2. K8s 클러스터 요구 ---
        raw = read("/assets/k8sclusterinfo.yaml")
        if raw:
            k8s = yaml.safe_load(raw) or {}
            table = k8s.get("k8scluster") if isinstance(k8s.get("k8scluster"), dict) else k8s
            for csp, info in sorted((table or {}).items()):
                if not isinstance(info, dict):
                    continue
                for field_name, (unit, note) in _K8S_COUNT_FIELDS.items():
                    value = info.get(field_name)
                    if value is None:
                        continue
                    out.add(
                        Rule(
                            id=f"tumblebug::k8s-{field_name}/{csp}",
                            kind=REQUIRED_COUNT,
                            scope=csp,
                            metric=field_name,
                            value=int(value),
                            unit=unit,
                            evidence=EVIDENCE_K8S,
                            note=note,
                        )
                    )
                    report.kinds[REQUIRED_COUNT] += 1

        # --- 3. 워크로드 참조점 ---
        for name, member in sorted(members.items()):
            if "/init/templates/" not in name or not name.endswith(".json"):
                continue
            stem = name.rsplit("/", 1)[-1].removesuffix(".json")
            # "리전마다 VM 하나"짜리는 테스트 픽스처지 워크로드 예시가 아니다.
            if not stem.startswith("infra-usecase-") and stem != "infra-aws-gpu-simple":
                continue
            try:
                doc = json.loads(archive.extractfile(member).read())
            except Exception:
                continue
            groups = (doc.get("infraDynamicReq") or {}).get("nodeGroups") or []
            for group in groups:
                spec = group.get("specId")
                if not spec:
                    continue
                disk = group.get("rootDiskSize")
                out.add(
                    Rule(
                        id=f"tumblebug::ref/{stem}/{group.get('name') or 'node'}",
                        kind=REFERENCE_POINT,
                        scope=f"workload:{stem.removeprefix('infra-usecase-')}",
                        metric="specId",
                        value=spec,
                        evidence=EVIDENCE_TEMPLATE,
                        note=(
                            f"{doc.get('description') or stem}"
                            + (f" · root disk {disk}GB" if disk else "")
                        ),
                        caveat=(
                            "An **example** from this source, not the right answer — "
                            "it varies widely with the nature of the workload. "
                            "Verify it with a load test."
                        ),
                    )
                )
                report.kinds[REFERENCE_POINT] += 1

    # --- 4. K8s 노드 최소치 (사람이 읽어 확정) ---
    for metric, value, unit in _K8S_NODE_MIN:
        out.add(
            Rule(
                id=f"tumblebug::k8s-node-min/{metric}",
                kind=MINIMUM,
                scope="k8s-node",
                metric=metric,
                value=value,
                unit=unit,
                evidence=EVIDENCE_CODE,
                note=f"fixed by reading recommendation.go at {_READ_AT_PIN}",
                caveat=(
                    "**A minimum this tool enforces**, not a value set by "
                    "Kubernetes or the cloud."
                ),
            )
        )
        report.kinds[MINIMUM] += 1
    return out, report


def build(output: Path, *, refresh: bool = False) -> RuleSet:
    source = SOURCES["tumblebug-src"]
    tar = fetch_cached(source.url, f"tumblebug-src-{source.pin}.tar.gz", refresh=refresh)
    rules, report = parse_tarball(tar)
    rules.provenance = [describe_source_set([tar], source.key)]
    rules.coverage = [
        {
            "rules": len(rules.rules),
            "note": (
                f"Sizing rules taken from cb-tumblebug assets {dict(report.kinds)}. "
                "**Reserved IPs are included only for the CSPs the source states "
                f"them for** — the {len(report.no_reserved)} blank ones "
                f"({', '.join(report.no_reserved)}) mean 'this file did not state "
                "it', not 'there are none'. AWS is exactly such a case, and reading "
                "a blank as 0 makes the answer 256 where it should be 251. "
                "Reference points are this source's **examples**, not the right answer."
            ),
        }
    ]
    from kbcommon import artifact

    artifact.write_dataset(output, rules.to_dict(), _schema())
    print(f"tumblebug 사이징: 규칙 {len(rules.rules)}개 → {output}")
    print(f"  종류별 {dict(report.kinds)}", file=sys.stderr)
    if report.no_reserved:
        print(f"  예약 IP 미기재: {report.no_reserved}", file=sys.stderr)
    return rules


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
