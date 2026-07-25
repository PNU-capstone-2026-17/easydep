"""bitnami 공용 차트 → 컨테이너 규모 프리셋.

`bitnami/common`의 `_resources.tpl`은 `nano`~`2xlarge` 이름에 CPU·메모리를 붙인
**구조화된 표**다. 수천 개 배포가 이 이름으로 규모를 고른다.

## 원본이 스스로 경고를 달았다

    These presets are for basic testing and **not meant to be used in production**

**이 문장을 값과 함께 담는다.** 떼면 테스트용 숫자가 권장값으로 둔갑한다 —
tumblebug `sg-default`에서 겪은 것과 같은 함정이다.

## Go 템플릿을 파싱하지 않는다

`_resources.tpl`은 Helm(Go) 템플릿이라 완전한 파싱이 비싸고, 이 저장소는 산문·코드
추출에서 데인 적이 있다. 필요한 것은 **`dict "cpu" "100m" "memory" "128Mi"` 꼴의
평평한 키-값**뿐이라 그 모양만 정규식으로 집는다. 못 읽으면 **0건으로 두고 밝힌다** —
반쯤 읽은 값을 담는 것보다 낫다.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES
from sizingkb.model import PRESET, Rule, RuleSet

EVIDENCE = "bitnami-preset"

#: `"nano" (dict` 로 시작하는 블록을 이름 단위로 자른다.
_PRESET_BLOCK = re.compile(r'"([a-z0-9]+)"\s*\(dict\s*(.*?)\n\s*\)', re.S)
#: 그 안의 `"requests" (dict "cpu" "100m" "memory" "128Mi" ...)`.
_SECTION = re.compile(r'"(requests|limits)"\s*\(dict\s*([^)]*)\)')
_PAIR = re.compile(r'"([\w-]+)"\s+"([^"]+)"')

#: 원본 주석에 이 말이 있으면 경고로 옮긴다. **문장을 지어내지 않는다.**
_CAVEAT_MARK = "not meant to be used in production"


def parse_template(text: str) -> tuple[list[Rule], str | None, Counter]:
    caveat = None
    for line in text.splitlines():
        if _CAVEAT_MARK in line:
            caveat = line.strip().lstrip("{/*").strip()
            break

    rules: list[Rule] = []
    seen: Counter = Counter()
    for name, body in _PRESET_BLOCK.findall(text):
        for section, pairs in _SECTION.findall(body):
            for key, value in _PAIR.findall(pairs):
                if key not in ("cpu", "memory"):
                    continue  # ephemeral-storage 등은 규모 축이 아니다
                rules.append(
                    Rule(
                        id=f"bitnami::{name}/{section}/{key}",
                        kind=PRESET,
                        scope=f"container:{name}",
                        metric=f"{section}.{key}",
                        value=value,
                        evidence=EVIDENCE,
                        note=f"{section} of the '{name}' preset",
                        caveat=caveat,
                    )
                )
                seen[name] += 1
    return rules, caveat, seen


def build(output: Path, *, refresh: bool = False) -> RuleSet:
    source = SOURCES["bitnami-charts"]
    path = fetch_cached(source.url, f"bitnami-resources-{source.pin[:12]}.tpl", refresh=refresh)
    text = path.read_text(encoding="utf-8", errors="replace")
    rules, caveat, seen = parse_template(text)

    out = RuleSet(rules=rules)
    out.provenance = [describe_source_set([path], source.key)]
    out.coverage = [
        {
            "rules": len(rules),
            "note": (
                f"{len(seen)} container size presets from the bitnami common chart "
                f"({', '.join(sorted(seen))}). "
                + (
                    "**The source itself says they are 'not meant to be used in "
                    "production', and that sentence ships with every value.**"
                    if caveat
                    else "⚠ Could not find the source's warning sentence — the "
                    "source format may have changed."
                )
                + " These are container sizes, not instance sizes."
            ),
        }
    ]
    from kbcommon import artifact

    artifact.write_dataset(output, out.to_dict(), _schema())
    print(f"프리셋 사이징: 규칙 {len(rules)}개 ({len(seen)}종) → {output}")
    if not caveat:
        print("  ⚠ 원본 경고 문장을 못 찾았습니다", file=sys.stderr)
    return out


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
