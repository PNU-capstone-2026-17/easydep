# -*- coding: utf-8 -*-
"""데이터셋 충분성 — 실무 질문을 답하는 데 필요한 정보가 실제로 있나."""
import sys, io, json, glob, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

C, Q, NODES, EDGES = [], [], [], []
for f in glob.glob("output/*-capacity.json"):
    C += json.load(open(f, encoding="utf-8"))["constraints"]
for f in glob.glob("output/*-quota.json"):
    Q += json.load(open(f, encoding="utf-8"))["quotas"]
for f in glob.glob("output/*-graph.json"):
    d = json.load(open(f, encoding="utf-8"))
    NODES += d.get("nodes", []); EDGES += d.get("edges", [])

def hdr(t): print("\n" + "="*74 + f"\n{t}\n" + "="*74)

hdr("A. 커버리지 — 타입 하나당 정보가 얼마나 붙어 있나")
by_type = collections.Counter(x["type_id"] for x in C)
vendor_nodes = {n["id"] for n in NODES if n.get("layer") == "vendor"}
print(f"그래프가 아는 벤더 타입: {len(vendor_nodes):,}")
print(f"제약 레코드가 있는 타입: {len(by_type):,}")
print(f"제약이 하나도 없는 타입: {len(vendor_nodes - set(by_type)):,}")
dist = collections.Counter()
for t in vendor_nodes:
    n = by_type.get(t, 0)
    dist["0" if n == 0 else "1-9" if n < 10 else "10-49" if n < 50 else "50+"] += 1
print("타입당 제약 수 분포:", dict(dist))

hdr("B. 종류별 — '가이드라인'을 만들 재료가 있나")
kinds = collections.Counter(x["kind"] for x in C)
total_types = len(vendor_nodes)
print(f"{'제약 종류':22} {'레코드':>8} {'해당 타입 수':>12} {'전체 대비':>8}")
for k, n in kinds.most_common():
    types_with = len({x["type_id"] for x in C if x["kind"] == k})
    print(f"{k:22} {n:>8,} {types_with:>12,} {types_with/total_types:>7.1%}")

hdr("C. 답할 수 있는 질문 vs 없는 질문 (질문 유형별)")
QUESTIONS = [
    ("이 값 넣어도 되나?",        lambda: kinds["min"] + kinds["max"] + kinds["enum"] + kinds["pattern"]),
    ("나중에 못 바꾸는 건?",       lambda: kinds["mutability"]),
    ("뭘 꼭 넣어야 하나?",         lambda: kinds["required"]),
    ("기본값이 뭔가?",            lambda: kinds["default"]),
    ("뭘 먼저 만들어야 하나?",     lambda: len(EDGES)),
    ("몇 개까지 만들 수 있나?",    lambda: len(Q)),
]
for q, fn in QUESTIONS:
    print(f"  {q:26} 재료 {fn():>8,}건")

print("\n  ■ 재료가 아예 없는 질문 유형:")
for q in ["되돌릴 수 없는 설정은?", "이 둘은 같이 못 쓴다",
          "성능이 언제 떨어지나(비용축 밖)", "이 설정의 실패 방식은?",
          "권장값은? (허용값 말고)", "왜 이 제약이 있나"]:
    print(f"    - {q}")

hdr("D. 조건부 제약이 필요한 실제 규모 — 값이 다른 속성에 의존하는 경우")
# 같은 타입 안에서 enum을 가진 속성 = 변종 축이 될 후보
enum_props = collections.defaultdict(list)
for x in C:
    if x["kind"] == "enum":
        enum_props[x["type_id"]].append((x["property"], len(x["value"]) if isinstance(x["value"], list) else 0))
# 그 타입에 수치 제약도 함께 있는 경우 = 봉투 붕괴 위험 지점
risk = 0
examples = []
for t, props in enum_props.items():
    nums = [x for x in C if x["type_id"] == t and x["kind"] in ("min", "max")]
    if nums and props:
        risk += 1
        if len(examples) < 8:
            examples.append((t[5:], [p for p, _ in props][:3], len(nums)))
print(f"enum(변종 축 후보)과 수치 제약을 **함께** 가진 타입: {risk:,}")
print("  = 조건부 제약이 필요할 수 있는 지점. 예:")
for t, props, n in examples:
    print(f"    {t:52} 변종축?{props} 수치제약{n}건")

hdr("E. 근거의 강도 — 판정에 쓸 수 있는 비율")
basis = collections.Counter(x.get("basis") for x in C)
print("basis 분포:", dict(basis))
rev = sum(1 for x in C if x.get("reviewed"))
print(f"검수 표시: {rev:,}건")
fact = sum(1 for x in C if x.get("basis") == "stated" or x.get("reviewed"))
print(f"사실로 취급(stated or reviewed): {fact:,} / {len(C):,} ({fact/len(C):.1%})")
