# -*- coding: utf-8 -*-
"""못 붙인 4,757건의 정체를 유형별로 가른다."""
import sys, io, collections, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
import yaml

from capacitykb.parsers import gcp, tpg

crds = []
for p in sorted(Path(r"C:\Users\projw\.claude\jobs\fd122c31\tmp\crds").glob("*.yaml")):
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    if isinstance(d, dict) and d.get("kind") == "CustomResourceDefinition":
        crds.append(d)
gcp.parse_crds(crds)
KCC = dict(gcp.KCC_PATHS)
print(f"KCC 경로를 아는 kind {len(KCC)}개\n")

# 프로바이더 경로를 kind별로 다시 모은다 (필터 없이)
TAR = Path(".cache/cloudkb/tpg-v7.40.0.tar.gz")
_, report = tpg.parse_provider(TAR, kcc_kinds=set(KCC), kcc_paths=None)

import tarfile
FUNC = tpg._FUNC
SM = "Schema: " + tpg._SCHEMA_MAP
tf_paths: dict[str, set[str]] = {}
with tarfile.open(TAR) as tar:
    for m in tar.getmembers():
        n = m.name
        if not (m.isfile() and "/google/services/" in n and "/resource_" in n
                and n.endswith(".go") and "_test" not in n and "_sweeper" not in n):
            continue
        text = tar.extractfile(m).read().decode("utf-8", "replace")
        for mt in FUNC.finditer(text):
            kind = tpg.resource_to_kind(mt.group(1)[len("Resource"):], set(KCC))
            if not kind:
                continue
            a = text.find(SM, mt.end())
            if a < 0:
                continue
            props = {}
            tpg._parse_schema_map(text, a + len(SM) - 1, "", props)
            tf_paths.setdefault(kind, set()).update(props)

unmatched = collections.Counter()
samples = collections.defaultdict(list)
total = 0
for kind, paths in tf_paths.items():
    known = KCC.get(kind, set())
    for tf in paths:
        head = tf.split(".", 1)[0]
        if head in tpg._TF_ONLY:
            continue
        conv = tpg.tf_path_to_kcc(tf)
        if conv in known:
            continue
        total += 1
        # 유형 분류
        if conv + "Ref" in known or conv.endswith("Ref"):
            cat = "참조 필드 (KCC는 ~Ref로 쓴다)"
        elif any(k.lower() == conv.lower() for k in known):
            cat = "대소문자만 다름"
        elif conv.split(".")[0] not in {k.split(".")[0] for k in known}:
            cat = "최상위 이름부터 없음"
        else:
            cat = "중첩 경로가 다름"
        unmatched[cat] += 1
        if len(samples[cat]) < 8:
            samples[cat].append((kind, tf, conv))

print(f"못 붙인 경로 {total:,}건\n")
for cat, n in unmatched.most_common():
    print(f"  {cat:32} {n:>5,} ({n/total:.0%})")
for cat, items in samples.items():
    print(f"\n[{cat}]")
    for kind, tf, conv in items:
        near = [k for k in KCC.get(kind, ()) if k.lower().startswith(conv.split('.')[0].lower()[:6])][:3]
        print(f"   {kind}.{tf}  →  {conv}    KCC 근처: {near}")
