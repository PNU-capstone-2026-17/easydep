"""AWS CloudFormation 샘플 → 동시 출현 빈도.

`aqt.py`와 **같은 방법을 AWS에 적용한다.** 조사 문서에 "Azure에서만 쟀으므로 통한다고
말하지 않는다"로 남겼던 항목이고, 재 보니 통했다.

## 통했지만 다르게 통했다

    AWS::Lambda::Function  → AWS::IAM::Role        100.0% (38/38)   구조적 필수
    AWS::EC2::Instance     → AWS::EC2::SecurityGroup  90.2%
                             AWS::EC2::Subnet         78.0%
                             AWS::EC2::KeyPair        75.6%

Lambda는 실행 역할이 **없으면 안 되므로** 100%가 나온다. EC2는 기본 VPC·기본 SG를 쓸
수 있어서 100%가 안 나온다 — **분포가 "구조적 필수"와 "관행"을 갈라 보여준다.**
값을 손보면 그 정보가 사라지므로 그대로 담는다.

## 코퍼스가 둘이다 — 얇아서 더했다

AWS 공식 샘플 하나로는 템플릿 299개·앵커 22종뿐이었다(Azure는 1,152개·43종).
`widdix/aws-cf-templates`를 더해 **362개·30종**이 됐다.

**성격이 다른 둘을 섞는다는 사실을 밝힌다** — AWS 샘플은 서비스별 데모이고 widdix는
운영용 스택이라, 후자에서 CloudWatch 경보가 55.6%로 나온다. 표본 편향의 방향이
다르므로 섞으면 어느 쪽 편향인지 알 수 없다. 그래서 coverage에 둘의 규모를 따로 적는다.

**RDS(15건)·EKS(2건)는 그래도 임계에 못 미친다.** 채운 척하지 않고 구멍으로 남긴다 —
`MIN_SAMPLES`를 낮춰 맞추면 그 문턱을 둔 이유가 사라진다.

## YAML을 통째로 파싱하지 않는다

CFN YAML은 `!Ref`·`!GetAtt` 같은 커스텀 태그 때문에 표준 파서로 못 읽는다. 필요한 것은
`Type: AWS::X::Y` 한 줄뿐이라 그 모양만 집는다 — `perfkb/parsers/details.py`가 Go의
`%v` 포맷에 쓴 것과 같은 방식이다.
"""

from __future__ import annotations

import collections
import json
import re
import sys
import tarfile
from pathlib import Path

from bundlekb.dataset import MIN_SAMPLES
from bundlekb.model import BundleSet, Companion
from bundlekb.parsers.aqt import MIN_HITS
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

EVIDENCE = "awscfn-corpus"
PROVIDER = "aws"

#: `  Type: AWS::EC2::Instance` / `"Type": "AWS::EC2::Instance"` 둘 다.
_TYPE = re.compile(
    r"""^\s+["']?Type["']?\s*:\s*["']?(AWS::[A-Za-z0-9]+::[A-Za-z0-9]+)""", re.M
)
#: 템플릿이 아닌 파일(워크플로 등)을 거른다.
_MARKERS = ("AWSTemplateFormatVersion", "Resources")


class Report:
    def __init__(self) -> None:
        self.templates = 0
        self.skipped = 0
        self.types = 0
        self.anchors_kept = 0
        self.anchors_dropped = 0
        self.per_corpus: dict[str, int] = {}


def _templates_in(tar: Path, report: Report, label: str) -> list[set[str]]:
    docs: list[set[str]] = []
    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            name = member.name.replace("\\", "/")
            if not member.isfile() or not name.endswith((".yaml", ".yml", ".json")):
                continue
            # 워크플로·테스트 픽스처는 배포 템플릿이 아니다.
            if "/.github/" in name or "/test/" in name:
                continue
            text = archive.extractfile(member).read().decode("utf-8-sig", "replace")
            if not any(marker in text for marker in _MARKERS):
                report.skipped += 1
                continue
            found = set(_TYPE.findall(text))
            if found:
                docs.append(found)
    report.per_corpus[label] = len(docs)
    return docs


def count_cooccurrence(tars: dict[str, Path]) -> tuple[list[Companion], Report]:
    report = Report()
    docs: list[set[str]] = []
    for label, tar in tars.items():
        docs.extend(_templates_in(tar, report, label))
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
    official = SOURCES["aws-cfn-templates"]
    widdix = SOURCES["widdix-cf-templates"]
    tars = {
        official.key: fetch_cached(
            official.url, f"awscfn-{official.pin[:12]}.tar.gz", refresh=refresh
        ),
        widdix.key: fetch_cached(
            widdix.url, f"widdix-{widdix.pin[:12]}.tar.gz", refresh=refresh
        ),
    }
    companions, report = count_cooccurrence(tars)

    out = BundleSet(companions=companions)
    out.provenance = [
        describe_source_set([tars[official.key]], official.key),
        describe_source_set([tars[widdix.key]], widdix.key),
    ]
    out.coverage = [
        {
            "provider": PROVIDER,
            "templates": report.templates,
            "note": (
                f"Co-occurrence counted over {report.templates} CFN templates "
                f"({report.per_corpus}). Of {report.types} types, only the "
                f"{report.anchors_kept} anchors with {MIN_SAMPLES} or more samples "
                "are included. **Still thinner than the Azure corpus (1,152 "
                "templates · 43 anchors)** — ratios alone make the two corpora "
                "read as equally weighty, so the difference is recorded here. "
                "**Two corpora of different character are mixed**: the official "
                "samples are per-service demos and widdix is production stacks, so "
                "their bias runs in different directions. **RDS (15) and EKS (2) "
                "fall below the threshold and are still holes** — no pretending "
                "they are filled. basis=observed, and not a fact about the cloud. "
                "The distribution separates 'structurally required' from "
                "'convention' — Lambda cannot run without an execution role so it "
                "comes out at 100%, while EC2 can use the default VPC and SG so it "
                "stays in the 90s. **The values were not corrected.**"
            ),
        }
    ]
    from kbcommon import artifact

    artifact.write_dataset(output, out.to_dict(), _schema())
    print(
        f"awscfn 동시 출현: 앵커 {report.anchors_kept}종 · 쌍 {len(companions):,}건 "
        f"(템플릿 {report.templates}개) → {output}"
    )
    if report.skipped:
        print(f"  템플릿이 아닌 파일 {report.skipped}개 건너뜀", file=sys.stderr)
    return out


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
