# -*- coding: utf-8 -*-
"""조사 검증 — KCC의 세 백엔드를 라벨로 가르고, '낡음'이 tf2crd에 몰려 있나 본다.

조사 주장: tf2crd 계열은 2023-09-26 버전의 terraform-provider-google-beta 4.84.0을
벤더링한 것에서 스키마를 뽑는다 → 2년 8개월 낡았다.
내 측정: MM에만 있는 최신 허용값 19건.
둘이 맞물리면, 그 19건은 tf2crd 리소스에 몰려 있어야 한다.
"""
import sys, io, glob, json, re, os, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml

backend = {}
for f in glob.glob(".cache/cloudkb/kcc-v1.153.0-*.yaml"):
    try:
        d = yaml.safe_load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(d, dict) or d.get("kind") != "CustomResourceDefinition":
        continue
    kind = ((d.get("spec") or {}).get("names") or {}).get("kind")
    labels = ((d.get("metadata") or {}).get("labels") or {})
    if labels.get("cnrm.cloud.google.com/tf2crd") == "true":
        backend[kind] = "tf2crd"
    elif labels.get("cnrm.cloud.google.com/dcl2crd") == "true":
        backend[kind] = "dcl2crd"
    else:
        backend[kind] = "direct"

print("■ KCC 백엔드 분포 (CRD 라벨 실측)")
print("  ", dict(collections.Counter(backend.values())))

# 우리 산출물의 제약이 백엔드별로 어떻게 나뉘나
C = json.load(open("output/gcp-capacity.json", encoding="utf-8"))["constraints"]
per = collections.defaultdict(collections.Counter)
for x in C:
    kind = x["type_id"][5:]
    per[backend.get(kind, "?")][x["kind"]] += 1
print("\n■ 우리가 뽑은 제약 4,421건의 백엔드별 분포")
for b in ("direct", "tf2crd", "dcl2crd", "?"):
    if per[b]:
        tot = sum(per[b].values())
        print(f"  {b:8} {tot:>6,}건  {dict(per[b])}")

# 낡은 enum 19건이 어디에 몰려 있나
STALE = ["BigQueryRoutine", "ComputeBackendService", "ComputeImage",
         "ComputeInterconnectAttachment", "ComputeSubnetwork"]
print("\n■ MM이 더 최신이었던 리소스의 백엔드")
for k in STALE:
    print(f"  {k:32} {backend.get(k, '(없음)')}")

# 불변 표시가 백엔드별로 얼마나 되나 — 조사: direct 271개 중 91개만 CEL 강제
imm = collections.Counter()
cel = collections.Counter()
for x in C:
    if x["kind"] != "mutability":
        continue
    b = backend.get(x["type_id"][5:], "?")
    imm[b] += 1
    if x["evidence"] == "kcc-cel-immutable":
        cel[b] += 1
print("\n■ 불변 표시 2,003건의 백엔드별 분포 / 그중 CEL로 강제되는 것")
for b in ("direct", "tf2crd", "dcl2crd"):
    print(f"  {b:8} 불변 {imm[b]:>5,}  CEL 강제 {cel[b]:>4}  "
          f"({cel[b]/max(imm[b],1):.0%})")
