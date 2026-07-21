# -*- coding: utf-8 -*-
"""'봉투 붕괴(envelope collapse)' 계량 — 변종별 범위를 한 칸에 뭉갠 제약이 몇 건인가."""
import sys, io, json, glob, re, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from capacitykb import agent_api as c

C = []
for f in glob.glob("output/*-capacity.json"):
    C += json.load(open(f, encoding="utf-8"))["constraints"]

num = [x for x in C if x["kind"] in ("min", "max")]
print(f"수치 제약(min/max) {len(num):,}건")
print("근거별:", dict(collections.Counter(x["evidence"] for x in num)))

# 같은 (type, property)에 min/max가 여러 벌 있는가?
by = collections.defaultdict(list)
for x in num:
    by[(x["type_id"], x["property"], x["kind"])].append(x)
multi = {k: v for k, v in by.items() if len(v) > 1}
print(f"\n같은 속성·같은 kind에 값이 2개 이상: {len(multi)}건")

# note 안에 '+ 이름: 범위' 패턴 = 변종별 범위가 산문에만 남아 있음
VARIANT = re.compile(r"\+\s*[\w., ]+:\s*``")
collapsed = [x for x in num if x.get("note") and VARIANT.search(x["note"])]
print(f"\n■ 변종별 범위가 note에만 있고 값은 한 개로 뭉개진 제약: {len(collapsed)}건")
for x in collapsed:
    print(f"   {x['type_id'][5:]}.{x['property']} {x['kind']}={x['value']}")
    print(f"      실제: {x['note'][:150]}")

print("\n■ 그래서 무슨 오답이 나오나 — gp2 볼륨 30,000 GiB")
print(c.check("AWS::EC2::Volume", "Size", 30000))
print("\n■ gp3 볼륨 IOPS 200,000")
print(c.check("AWS::EC2::Volume", "Iops", 200000))

print("\n■ conditional 플래그가 붙은 제약 전체")
for x in C:
    if x.get("conditional"):
        print(f"   {x['type_id'][5:]}.{x['property']} {x['kind']}={x['value']}  note={'있음' if x.get('note') else '없음'}")

print("\n■ note가 달린 제약 10건 전부 (자유서술 채널이 얼마나 쓰이나)")
for x in C:
    if x.get("note"):
        print(f"   {x['type_id'][5:]}.{x['property']} {x['kind']}={x['value']}")
