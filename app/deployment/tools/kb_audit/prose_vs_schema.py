# -*- coding: utf-8 -*-
"""산문이 '변경 불가'라 말하는 속성이 우리 mutability 레코드에 있나."""
import sys, io, json, zipfile, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
Z = zipfile.ZipFile(".cache/cloudkb/CloudformationSchema.zip")

have = set()
for f in glob.glob("output/*-capacity.json"):
    for x in json.load(open(f, encoding="utf-8"))["constraints"]:
        if x["kind"] == "mutability":
            have.add((x["type_id"], x["property"]))

PAT = r"\b(cannot be (changed|modified|updated)|only used at creation|immutable after|cannot be updated|can't be changed|once it is enabled it cannot be disabled)\b"
miss, covered = [], 0
for n in Z.namelist():
    if not n.endswith(".json"): continue
    try: sc = json.loads(Z.read(n))
    except Exception: continue
    tn = sc.get("typeName")
    if not tn: continue
    for pname, prop in (sc.get("properties") or {}).items():
        d = prop.get("description") or ""
        if len(d) < 40 or not re.search(PAT, d, re.I): continue
        key = (f"aws::{tn}", pname)
        if key in have: covered += 1
        else: miss.append((tn, pname, re.sub(r"\s+", " ", d)[:170]))

print(f"산문이 '변경 불가'라 말하는 속성 {covered+len(miss)}건")
print(f"  스키마에도 있어 우리가 아는 것: {covered}건")
print(f"  ■ 산문에만 있어 우리가 놓친 것: {len(miss)}건\n")
for t, p, d in miss:
    print(f"  - {t}.{p}\n      {d}")
