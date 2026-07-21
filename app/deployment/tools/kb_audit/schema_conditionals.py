# -*- coding: utf-8 -*-
"""원본 스키마가 조건부를 이미 표현하고 있나 — 있는데 우리가 안 읽는 건지 확인."""
import sys, io, json, zipfile, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
Z = zipfile.ZipFile(".cache/cloudkb/CloudformationSchema.zip")

KEYWORDS = ("if", "then", "else", "dependentSchemas", "dependentRequired",
            "oneOf", "anyOf", "allOf", "not")
found = collections.Counter()
examples = collections.defaultdict(list)
total = 0

def walk(node, path, tn):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in KEYWORDS:
                found[k] += 1
                if len(examples[k]) < 3:
                    examples[k].append((tn, path + "/" + k))
            walk(v, path + "/" + str(k), tn)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]", tn)

for n in Z.namelist():
    if not n.endswith(".json"): continue
    try: sc = json.loads(Z.read(n))
    except Exception: continue
    total += 1
    walk(sc, "", sc.get("typeName", n))

print(f"CFN 스키마 {total:,}개에서 JSON Schema 조건부 키워드 사용")
for k in KEYWORDS:
    print(f"  {k:20} {found[k]:>6,}회")
print()
for k in ("if", "then", "dependentSchemas", "dependentRequired", "oneOf"):
    if examples[k]:
        print(f"  [{k}] 예:")
        for tn, p in examples[k]:
            print(f"     {tn}  {p}")

# min/max가 실제로 어디에 붙어 있나 — 조건부 표현 여부
print("\n■ minimum/maximum이 붙은 위치와, 그 형제에 조건부가 있나")
mm = 0
with_cond = 0
for n in Z.namelist():
    if not n.endswith(".json"): continue
    try: sc = json.loads(Z.read(n))
    except Exception: continue
    def scan(node):
        global mm, with_cond
        if isinstance(node, dict):
            if "minimum" in node or "maximum" in node:
                mm += 1
                if any(k in node for k in ("if", "oneOf", "anyOf", "dependentSchemas")):
                    with_cond += 1
            for v in node.values(): scan(v)
        elif isinstance(node, list):
            for v in node: scan(v)
    scan(sc)
print(f"  minimum/maximum이 붙은 노드 {mm:,}개 중, 같은 자리에 조건부 키워드가 있는 것 {with_cond}개")
