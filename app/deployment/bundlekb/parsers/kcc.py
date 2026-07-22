"""GCP — Config Connector 샘플이 보여주는 리소스 군.

## 왜 이 소스인가 — 두 번 헛짚고 찾았다

GCP 번들을 두 갈래로 시도했다.

1. **`cloud-foundation-fabric` 모듈** — 모듈 86개 중 63개가 무조건 리소스 0개.
   Terraform은 주 리소스에도 `count = var.x_create ? 1 : 0`을 걸어 "만들거나
   참조하거나"를 표현하기 때문이다.
2. **변수 기본값까지 추적** — 그래도 리소스 선언 719개 중 **46개(6.4%)만** 풀렸다.
   535개가 `for_each`이고 기본값이 빈 맵이라, 모듈이 설계상 보장하는 것이 거의 없다.

**억지로 밀지 않고** 이미 핀이 박힌 `kcc-crd`(v1.153.0)의 안 쓰던 디렉터리를 봤다.
`config/samples/resources/<kind>/`는 **Google이 직접 만든 최소 동작 구성**이고,
한 디렉터리가 시나리오 하나다.

## 무엇이 나오나 (실측)

    시나리오 443개 · kind 2개 이상인 것 **296개**
    alloydbcluster/regular-cluster
      → AlloyDBCluster · ComputeAddress · ComputeNetwork · IAMPartialPolicy
        KMSCryptoKey · KMSKeyRing · ServiceIdentity · ServiceNetworkingConnection
    KMSKeyRing → KMSCryptoKey 16회   (키링만으로는 쓸모가 없다)

kind가 우리 graphkb 노드 id와 **그대로 맞는다**(`gcp::AlloyDBCluster`).

## '샘플'이지 '요구'가 아니다

샘플에 든 것은 적용하면 다 만들어지므로 `always`로 담는다. 다만 **Google이 예시로
고른 구성**이지 API가 강제하는 최소 집합이라는 뜻은 아니다 — `avm-module`·
`tumblebug-dynamic`에 적어 둔 것과 같은 경계이고 `caveat`에 적는다.

## 네임스페이스·시크릿은 구성원이 아니다

`Namespace`·`Secret`·`ConfigMap`은 쿠버네티스 살림이지 클라우드 리소스가 아니다.
`apiVersion`이 `k8s`로 시작하는 것도 같은 이유로 뺀다.
"""

from __future__ import annotations

import collections
import json
import re
import sys
import tarfile
from pathlib import Path

from bundlekb.model import ALWAYS, Bundle, BundleSet, Member
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

EVIDENCE = "kcc-sample"
PROVIDER = "gcp"

_SAMPLES = "/config/samples/resources/"
_DOC = re.compile(r"^---\s*$", re.M)
_KIND = re.compile(r"^kind:\s*(\w+)", re.M)
_APIVERSION = re.compile(r"^apiVersion:\s*([\w./-]+)", re.M)

#: 쿠버네티스 살림. 클라우드 리소스가 아니다.
_NOT_CLOUD = {"Namespace", "Secret", "ConfigMap", "ServiceAccount"}


def kinds_in(text: str) -> list[str]:
    """YAML 문서 하나하나에서 `kind`를 뽑는다.

    한 파일에 `---`로 여러 문서가 들어 있는 경우가 있어 **문서 단위로 자른 뒤**
    본다 — 파일 전체에서 정규식을 돌리면 apiVersion과 kind의 짝이 어긋난다.
    """
    out: list[str] = []
    for chunk in _DOC.split(text):
        kind = _KIND.search(chunk)
        if not kind:
            continue
        api = _APIVERSION.search(chunk)
        if api and api.group(1).startswith("k8s"):
            continue
        if kind.group(1) in _NOT_CLOUD:
            continue
        out.append(kind.group(1))
    return out


def anchor_of(scenario: str, kinds: set[str]) -> str | None:
    """디렉터리 이름과 같은 kind를 앵커로 본다 (`alloydbcluster` → `AlloyDBCluster`)."""
    stem = scenario.split("/")[0].replace("-", "").replace("_", "").lower()
    for kind in sorted(kinds):
        if kind.lower() == stem:
            return kind
    return None


class Report:
    def __init__(self) -> None:
        self.scenarios = 0
        self.single = 0
        self.no_anchor = 0
        self.sizes: collections.Counter = collections.Counter()


def parse_tarball(tar: Path) -> tuple[BundleSet, Report]:
    out = BundleSet()
    report = Report()
    scenarios: dict[str, list[str]] = collections.defaultdict(list)

    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            name = member.name.replace("\\", "/")
            if not member.isfile() or _SAMPLES not in name:
                continue
            if not name.endswith((".yaml", ".yml")):
                continue
            text = archive.extractfile(member).read().decode("utf-8", "replace")
            scenario = name.split(_SAMPLES, 1)[1].rsplit("/", 1)[0]
            scenarios[scenario].extend(kinds_in(text))

    for scenario, found in sorted(scenarios.items()):
        kinds = set(found)
        report.scenarios += 1
        report.sizes[len(kinds)] += 1
        if len(kinds) < 2:
            # 리소스 하나짜리는 '리소스 군'이 아니다.
            report.single += 1
            continue
        anchor = anchor_of(scenario, kinds)
        if anchor is None:
            report.no_anchor += 1
        out.add(
            Bundle(
                id=f"kcc::{scenario}",
                name=f"kcc/{scenario}",
                provider=PROVIDER,
                evidence=EVIDENCE,
                anchor=f"{PROVIDER}::{anchor}" if anchor else None,
                members=tuple(
                    Member(f"{PROVIDER}::{kind}", ALWAYS) for kind in sorted(kinds)
                ),
                description=f"Config Connector 샘플 — {scenario}",
                caveat=(
                    "**Google이 예시로 고른 최소 동작 구성**입니다. 적용하면 다 "
                    "만들어지지만, API가 이 집합을 강제한다는 뜻은 아닙니다."
                ),
            )
        )
    return out, report


def build(output: Path, *, refresh: bool = False) -> BundleSet:
    source = SOURCES["kcc-crd"]
    tar = fetch_cached(
        "https://codeload.github.com/GoogleCloudPlatform/k8s-config-connector/"
        f"tar.gz/refs/tags/{source.pin}",
        f"kcc-src-{source.pin}.tar.gz",
        refresh=refresh,
    )
    bundles, report = parse_tarball(tar)
    bundles.provenance = [describe_source_set([tar], source.key)]
    bundles.coverage = [
        {
            "provider": PROVIDER,
            "bundles": len(bundles.bundles),
            "note": (
                f"Config Connector 샘플 시나리오 {report.scenarios}개 중 리소스가 "
                f"2종 이상인 {len(bundles.bundles)}개. 하나짜리 {report.single}개는 "
                "'리소스 군'이 아니라 담지 않았다. 앵커를 못 정한 시나리오 "
                f"{report.no_anchor}개는 앵커 없이 담았다(디렉터리 이름과 같은 kind가 "
                "없는 경우). **Google이 예시로 고른 구성이지 API가 강제하는 최소 "
                "집합이 아니다.** Terraform 모듈(cloud-foundation-fabric)로는 이 축을 "
                "못 열었다 — 리소스 선언 719개 중 6.4%만 조건이 풀렸다."
            ),
        }
    ]
    from kbcommon import artifact

    artifact.write_dataset(output, bundles.to_dict(), _schema())
    print(f"kcc 번들: {len(bundles.bundles)}개 / 시나리오 {report.scenarios}개 → {output}")
    print(f"  구성원 수 분포: {dict(sorted(report.sizes.items()))}", file=sys.stderr)
    return bundles


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
