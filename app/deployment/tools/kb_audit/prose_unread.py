# -*- coding: utf-8 -*-
"""과대주장 방지 — 설명문 중 '규칙을 말하는' 것이 실제로 몇 건인지 유형별로."""
import sys, io, json, zipfile, re, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
Z = zipfile.ZipFile(".cache/cloudkb/CloudformationSchema.zip")

PATTERNS = {
    "변경 불가/생성시에만": r"\b(cannot be (changed|modified|updated)|only used at creation|immutable|cannot be updated|can't be changed)\b",
    "되돌릴 수 없음":       r"\b(irreversible|cannot be (disabled|reversed|undone)|permanently)\b",
    "쓰기 전용":            r"\bwrite[- ]only\b",
    "열거값이 산문에":      r"\b(must be one of|valid values are|the only valid)\b",
    "다른 속성에 의존":     r"\b(if you specify|you must also|required (if|when)|cannot be used with|mutually exclusive)\b",
    "변종별로 다름":        r"\b(depends on the|for (gp2|io1|RabbitMQ|ActiveMQ)|varies by)\b",
}
hits = collections.defaultdict(list)
tot = 0
for n in Z.namelist():
    if not n.endswith(".json"): continue
    try: sc = json.loads(Z.read(n))
    except Exception: continue
    for pname, prop in (sc.get("properties") or {}).items():
        d = prop.get("description") or ""
        if len(d) < 40: continue
        tot += 1
        for label, pat in PATTERNS.items():
            if re.search(pat, d, re.I):
                hits[label].append((sc.get("typeName", n), pname, d))

print(f"설명문 있는 속성 {tot:,}개\n")
print("■ 규칙을 말하고 있는데 우리가 안 읽는 문장 (유형별)")
seen = set()
for label, items in sorted(hits.items(), key=lambda kv: -len(kv[1])):
    print(f"  {label:22} {len(items):>5,}건")
    seen |= {(t, p) for t, p, _ in items}
print(f"  {'중복 제외 합계':22} {len(seen):>5,}건 ({len(seen)/tot:.1%})")

print("\n■ 유형별 실제 예시 2건씩")
for label, items in sorted(hits.items(), key=lambda kv: -len(kv[1])):
    print(f"\n  [{label}]")
    for t, p, d in items[:2]:
        print(f"    {t}.{p}")
        print(f"      {re.sub(r'\\s+', ' ', d)[:190]}")
