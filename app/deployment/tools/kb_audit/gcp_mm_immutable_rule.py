# -*- coding: utf-8 -*-
"""결정 가설 검증.

H4. MM의 리소스 수준 `immutable: true`는 "전부 불변"이 아니라
    **"일반 update 메서드가 없다"**를 뜻한다. 개별 필드는 update_url로 갱신될 수 있다.
    → 그렇다면 "MM에서 불변인 속성" = (리소스 immutable) AND (그 속성에 update_url 없음).
    이 규칙으로 계산한 집합이 KCC의 불변 집합과 맞으면 두 소스는 **모순이 아니라
    표현 방식이 다른 것**이고, 규칙만 알면 서로 변환된다.

H5. MM의 `name`은 KCC의 `resourceID`다 (8/8 케이스에서 KCC만 목록에 resourceID가 있었다).
"""
import sys, io, os, json, urllib.request, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml

TMP = r"C:\Users\projw\.claude\jobs\fd122c31\tmp"
RAW = "https://raw.githubusercontent.com/GoogleCloudPlatform/magic-modules/main/mmv1/products/"

def get(rel):
    dest = os.path.join(TMP, "mm", rel.replace("/", "_"))
    if os.path.exists(dest):
        return open(dest, encoding="utf-8").read()
    try:
        with urllib.request.urlopen(RAW + rel, timeout=30) as r:
            t = r.read().decode("utf-8")
    except Exception:
        return ""
    open(dest, "w", encoding="utf-8").write(t)
    return t

def scan(doc):
    """설정 가능한 속성 → (immutable 표시, update_url 보유)."""
    info = {}
    def walk(props, prefix):
        for p in props or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            path = f"{prefix}.{p['name']}" if prefix else p["name"]
            if not p.get("output"):
                info[path] = (bool(p.get("immutable")),
                              bool(p.get("update_url") or p.get("update_verb")))
            walk(p.get("properties"), path)
            it = p.get("item_type")
            if isinstance(it, dict):
                walk(it.get("properties"), path)
    walk(doc.get("properties"), "")
    return info

C = json.load(open("output/gcp-capacity.json", encoding="utf-8"))["constraints"]
kcc_imm = collections.defaultdict(set)
kcc_any = collections.defaultdict(set)
for x in C:
    kcc_any[x["type_id"]].add(x["property"])
    if x["kind"] == "mutability":
        kcc_imm[x["type_id"]].add(x["property"])

CASES = [
    ("compute/Subnetwork.yaml", "gcp::ComputeSubnetwork"),
    ("compute/Disk.yaml", "gcp::ComputeDisk"),
    ("compute/Address.yaml", "gcp::ComputeAddress"),
    ("compute/Firewall.yaml", "gcp::ComputeFirewall"),
    ("storage/Bucket.yaml", "gcp::StorageBucket"),
    ("pubsub/Topic.yaml", "gcp::PubSubTopic"),
    ("redis/Instance.yaml", "gcp::RedisInstance"),
    ("dns/ManagedZone.yaml", "gcp::DNSManagedZone"),
]

print("H4: MM 불변 = (리소스immutable AND update_url 없음) OR 속성immutable")
print("    이 규칙으로 계산한 뒤 KCC와 대조. name→resourceID 치환 적용.\n")
print(f"{'타입':26} {'규칙MM':>7} {'KCC':>5} {'겹침':>5} {'MM만':>5} {'KCC만':>6} {'일치율':>7}")

tot_both = tot_mm = tot_kcc = 0
detail = []
for rel, tid in CASES:
    raw = get(rel)
    if not raw:
        continue
    doc = yaml.safe_load(raw)
    info = scan(doc)
    res_imm = bool(doc.get("immutable"))
    derived = set()
    for path, (p_imm, has_upd) in info.items():
        if p_imm or (res_imm and not has_upd):
            derived.add("resourceID" if path == "name" else path)   # H5
    # KCC가 아는 필드로 한정해 비교한다 (MM 전용 필드는 애초에 비교 대상이 아니다)
    known = kcc_any.get(tid, set()) | kcc_imm.get(tid, set())
    derived_cmp = {p for p in derived if p in known or p == "resourceID"}
    k = kcc_imm.get(tid, set())
    both = derived_cmp & k
    tot_both += len(both); tot_mm += len(derived_cmp); tot_kcc += len(k)
    union = len(derived_cmp | k) or 1
    print(f"{tid[5:]:26} {len(derived_cmp):>7} {len(k):>5} {len(both):>5} "
          f"{len(derived_cmp-k):>5} {len(k-derived_cmp):>6} {len(both)/union:>6.0%}")
    detail.append((tid, sorted(derived_cmp - k), sorted(k - derived_cmp), info, res_imm))

u = tot_mm + tot_kcc - tot_both
print(f"\n  전체 일치율(Jaccard): {tot_both}/{u} = {tot_both/max(u,1):.0%}")

print("\n=== 남은 불일치의 정체")
for tid, mm_only, kcc_only, info, res_imm in detail:
    if not mm_only and not kcc_only:
        continue
    print(f"\n  {tid[5:]}  (리소스immutable={res_imm})")
    if mm_only:
        print(f"    MM만: {mm_only[:8]}")
    if kcc_only:
        print(f"    KCC만: {kcc_only[:8]}")
        for p in kcc_only[:4]:
            if p in info:
                print(f"       └ MM에서 {p}: immutable={info[p][0]}, update_url={info[p][1]}")
            else:
                print(f"       └ MM에 그 필드 없음: {p}")
