"""aws·gcp 스키마 원문에서 의존 **후보**를 뽑는다 — azure와 같은 규율, 다른 형태.

증거의 형태가 CSP마다 다르다는 것 자체가 산출물의 일부다:

- **azure**: `$ref`로 타입이 박혀 있다(`input-reference`) — 참조가 강타입.
- **aws(CFN)**: 참조가 문자열이라 속성 **이름**으로 겨눈다(`name-reference`,
  휴리스틱 — 우리 구성). 대신 **Required 플래그가 실데이터다**(azure는 침묵).
- **gcp(디스커버리)**: 내장 구조는 `$ref`(`schema-ref`), 자원 간 참조는 URL
  문자열이라 (스키마, 속성) 쌍으로 좁혀 겨눈다(`pair-reference` — 우리 구성).
  `annotations.required`가 있으면 requiredInSchema로 싣는다.

산출: `aws_candidates.json` · `gcp_candidates.json`. 재계산 정합은
`test_depkb_vendors.py`가 강제한다(원문 캐시가 있는 환경에서).

실행: `python -m app.core.cloudkb.depkb.extract_vendors`
"""

from __future__ import annotations

import json
from pathlib import Path

from .fetch_vendors import SOURCES, load
from .vocabulary import AWS_NAME_REFS, AWS_TYPES, GCP_PAIR_REFS, GCP_TYPES

_HERE = Path(__file__).resolve().parent


# ── aws ──────────────────────────────────────────────────────────────

def extract_aws() -> dict:
    spec = load("aws-cfn")
    resource_types = spec["ResourceTypes"]
    property_types = spec["PropertyTypes"]
    candidates: list[dict] = []

    def walk(subject: str, cfn_type: str, props: dict, base: str, trail: str,
             seen: set[str]) -> None:
        for name, p in props.items():
            new_trail = f"{trail}.{name}" if trail else name
            ptr = f"{base}/Properties/{name}"
            target = AWS_NAME_REFS.get(name)
            if target and target != subject:
                candidates.append({
                    "subject": subject, "object": target,
                    "form": "name-reference", "trail": new_trail,
                    "cite": f"aws-cfn#{ptr}",
                    "requiredInSchema": bool(p.get("Required")),
                })
                continue
            # 복합 속성(PropertyType)은 같은 리소스 타입의 것만 내려간다
            sub = p.get("ItemType") or p.get("Type")
            if sub and sub != "List":
                key = f"{cfn_type}.{sub}"
                if key in property_types and key not in seen:
                    seen.add(key)
                    walk(subject, cfn_type,
                         property_types[key].get("Properties", {}),
                         f"/PropertyTypes/{key}", new_trail, seen)

    for subject, cfn_type in AWS_TYPES.items():
        rt = resource_types.get(cfn_type)
        assert rt is not None, f"{subject}: CFN에 {cfn_type}이 없다"
        walk(subject, cfn_type, rt.get("Properties", {}),
             f"/ResourceTypes/{cfn_type}", "", set())

    candidates.sort(key=lambda c: (c["subject"], c["object"], c["cite"]))
    return {
        "_note": ("CFN 스펙에서 뽑은 의존 후보 — name-reference는 휴리스틱"
                  "(우리 구성)이고 Required는 스펙 실데이터다. **주의: Required는 "
                  "그 속성 위치의 플래그다** — trail이 중첩이면(예: Volumes."
                  "VolumeId) '그 블록을 쓸 때 필수'이지 간선 필수가 아니다. "
                  "간선 필수성 판정은 반사실 실험의 몫이다."),
        "_pin": {"source": "aws-cfn", **{k: SOURCES["aws-cfn"][k]
                                         for k in ("version", "sha256")}},
        "candidates": candidates,
    }


# ── gcp ──────────────────────────────────────────────────────────────

def extract_gcp() -> dict:
    doc = load("gcp-compute")
    schemas = doc["schemas"]
    bound = {v: k for k, v in GCP_TYPES.items() if v}
    candidates: list[dict] = []

    def walk(subject: str, schema_name: str, trail: str, seen: set[str]) -> None:
        for name, p in schemas[schema_name].get("properties", {}).items():
            new_trail = f"{trail}.{name}" if trail else name
            ptr = f"/schemas/{schema_name}/properties/{name}"
            required = bool(p.get("annotations", {}).get("required"))
            pair_target = GCP_PAIR_REFS.get((schema_name, name))
            if pair_target and pair_target != subject:
                candidates.append({
                    "subject": subject, "object": pair_target,
                    "form": "pair-reference", "trail": new_trail,
                    "cite": f"gcp-compute#{ptr}",
                    "requiredInSchema": required,
                })
                continue
            ref = p.get("$ref") or (p.get("items") or {}).get("$ref")
            if not ref:
                continue
            target = bound.get(ref)
            if target and target != subject:
                candidates.append({
                    "subject": subject, "object": target,
                    "form": "schema-ref", "trail": new_trail,
                    "cite": f"gcp-compute#{ptr}",
                    "requiredInSchema": required,
                })
            elif ref in schemas and ref not in seen:
                seen.add(ref)
                walk(subject, ref, new_trail, seen)

    for subject, schema_name in GCP_TYPES.items():
        if schema_name is None:
            continue  # sshKey — 자원 부재가 결속에 박혀 있다
        assert schema_name in schemas, f"{subject}: 디스커버리에 {schema_name} 없음"
        walk(subject, schema_name, "", {schema_name})

    candidates.sort(key=lambda c: (c["subject"], c["object"], c["cite"]))
    return {
        "_note": ("gcp compute 디스커버리에서 뽑은 의존 후보 — pair-reference는 "
                  "(스키마,속성) 쌍 한정 휴리스틱(우리 구성), schema-ref는 내장 "
                  "구조의 강타입 참조다. sshKey는 대응 자원이 없어 후보 자체가 "
                  "없다(결속 None이 그 기록이다)."),
        "_pin": {"source": "gcp-compute", **{k: SOURCES["gcp-compute"][k]
                                             for k in ("version", "sha256")}},
        "candidates": candidates,
    }


if __name__ == "__main__":
    for name, result in [("aws_candidates.json", extract_aws()),
                         ("gcp_candidates.json", extract_gcp())]:
        (_HERE / name).write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        pairs = sorted({(c["subject"], c["object"], c["requiredInSchema"])
                        for c in result["candidates"]})
        print(f"== {name}: {len(result['candidates'])} candidates")
        for s, o, r in pairs:
            print(f"  {s} -> {o}{'  [required]' if r else ''}")
