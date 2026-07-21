# -*- coding: utf-8 -*-
"""빈칸 3,634종이 왜 비었나 — 안 읽어서인가, 원본에 없어서인가."""
import sys, io, json, glob, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

C = []
for f in glob.glob("output/*-capacity.json"):
    C += json.load(open(f, encoding="utf-8"))["constraints"]
have = {x["type_id"] for x in C}

NODES = []
for f in glob.glob("output/*-graph.json"):
    NODES += json.load(open(f, encoding="utf-8")).get("nodes", [])
vendor = [n for n in NODES if n.get("layer") == "vendor"]

by_prov = collections.defaultdict(lambda: [0, 0])
for n in vendor:
    p = n.get("provider", "?")
    by_prov[p][0] += 1
    if n["id"] in have:
        by_prov[p][1] += 1

print(f"{'프로바이더':10} {'타입':>8} {'제약있음':>9} {'커버율':>8}")
for p, (tot, cov) in sorted(by_prov.items(), key=lambda kv: -kv[1][0]):
    print(f"{p:10} {tot:>8,} {cov:>9,} {cov/tot:>7.1%}")

print("\n■ capacitykb가 선언한 수집 범위")
for f in glob.glob("output/*-capacity.json"):
    d = json.load(open(f, encoding="utf-8"))
    print(f"  {f}: {d.get('_coverage')}")

print("\n■ 수치 제약(min/max)이 있는 타입은 몇 종인가 — '용량 KB'의 실체")
numeric_types = {x["type_id"] for x in C if x["kind"] in ("min", "max")}
print(f"  min/max 보유 타입 {len(numeric_types):,} / 벤더 타입 {len(vendor):,} ({len(numeric_types)/len(vendor):.1%})")
shape_kinds = ("required", "mutability", "min_length", "max_length", "pattern", "min_items", "max_items")
shape = sum(1 for x in C if x["kind"] in shape_kinds)
cap = sum(1 for x in C if x["kind"] in ("min", "max", "enum", "default"))
print(f"  레코드 성격: 스키마 모양 {shape:,}건 ({shape/len(C):.1%}) vs 실제 한도·값 {cap:,}건 ({cap/len(C):.1%})")
