# -*- coding: utf-8 -*-
"""KCC의 낡음이 계통적인가 — 두 소스의 허용값 집합을 대조한다.

KCC는 enum을 구조로 안 담고 설명문 끝에 "Possible values: [...]"로 적는다.
MM은 enum_values로 담는다. 둘을 뽑아 비교하면 어느 쪽이 최신인지 규모로 답할 수 있다.
"""
import sys, io, os, json, re, glob, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml

TMP = r"C:\Users\projw\.claude\jobs\fd122c31\tmp"
mapping = json.load(open(os.path.join(TMP, "kcc_to_mm.json"), encoding="utf-8"))

# --- MM: path -> enum_values
def mm_enums(doc):
    out = {}
    def walk(props, prefix):
        for p in props or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            path = f"{prefix}.{p['name']}" if prefix else p["name"]
            if p.get("enum_values"):
                out[path] = [str(v) for v in p["enum_values"]]
            walk(p.get("properties"), path)
            it = p.get("item_type")
            if isinstance(it, dict):
                walk(it.get("properties"), path)
    walk(doc.get("properties"), "")
    return out

# --- KCC: path -> 설명문 안의 Possible values
POSSIBLE = re.compile(r'Possible values:\s*\[([^\]]+)\]')
def kcc_enums(crd):
    out = {}
    def walk(node, prefix):
        if not isinstance(node, dict):
            return
        d = node.get("description")
        if isinstance(d, str) and prefix:
            m = POSSIBLE.search(d)
            if m:
                vals = [v.strip().strip('"\'') for v in m.group(1).split(",")]
                out[prefix] = [v for v in vals if v]
        props = node.get("properties")
        if isinstance(props, dict):
            for k, v in props.items():
                walk(v, f"{prefix}.{k}" if prefix else k)
        for k in ("items", "additionalProperties"):
            if isinstance(node.get(k), dict):
                walk(node[k], prefix)
    for v in crd.get("spec", {}).get("versions") or []:
        if not v.get("storage"):
            continue
        spec = ((v.get("schema") or {}).get("openAPIV3Schema") or {}).get("properties", {}).get("spec")
        if spec:
            walk(spec, "")
    return out

crd_by_kind = {}
for f in glob.glob(".cache/cloudkb/kcc-v1.153.0-*.yaml"):
    try:
        d = yaml.safe_load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if isinstance(d, dict) and d.get("kind") == "CustomResourceDefinition":
        k = ((d.get("spec") or {}).get("names") or {}).get("kind")
        if k:
            crd_by_kind[k] = d

agree = mm_more = kcc_more = differ = 0
only_mm = only_kcc = 0
examples = []
for kind, rel in mapping.items():
    path = os.path.join(TMP, "mm", rel.replace("/", "_"))
    if not os.path.exists(path) or kind not in crd_by_kind:
        continue
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8"))
    except Exception:
        continue
    me, ke = mm_enums(doc or {}), kcc_enums(crd_by_kind[kind])
    only_mm += len(set(me) - set(ke))
    only_kcc += len(set(ke) - set(me))
    for p in set(me) & set(ke):
        a, b = set(me[p]), set(ke[p])
        if a == b:
            agree += 1
        elif a > b:
            mm_more += 1
            if len(examples) < 8:
                examples.append(("MM이 더 많음", kind, p, sorted(a - b), sorted(b - a)))
        elif b > a:
            kcc_more += 1
            if len(examples) < 8:
                examples.append(("KCC가 더 많음", kind, p, sorted(a - b), sorted(b - a)))
        else:
            differ += 1
            if len(examples) < 8:
                examples.append(("서로 다름", kind, p, sorted(a - b), sorted(b - a)))

total = agree + mm_more + kcc_more + differ
print(f"두 소스가 **같은 속성**에 허용값을 가진 경우 {total}건")
print(f"  완전 일치        {agree:>5}  ({agree/max(total,1):.0%})")
print(f"  MM이 더 많음     {mm_more:>5}  ← KCC가 낡았다는 신호")
print(f"  KCC가 더 많음    {kcc_more:>5}")
print(f"  서로 엇갈림      {differ:>5}")
print(f"\nMM에만 허용값이 있는 속성 {only_mm:,}  /  KCC에만 {only_kcc:,}")
print("\n■ 예시 (MM에만 / KCC에만)")
for tag, kind, p, a, b in examples:
    print(f"  [{tag}] {kind}.{p}")
    if a: print(f"      MM에만: {a}")
    if b: print(f"      KCC에만: {b}")
