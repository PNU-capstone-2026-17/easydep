"""azure 스키마 원문에서 의존 **후보**를 뽑는다 — 주장이 아니라 후보다.

스키마 층이 말할 수 있는 것과 없는 것을 가른다:

- 말할 수 있는 것: A의 입력 구조에 B 참조 슬롯이 있다(입력 참조) · B가 A를
  되가리키는 출력이 있다(readOnly 백링크) · A가 B의 경로 밑에서만 만들어진다
  (경로 중첩 — 소속의 후보).
- **말할 수 없는 것: 필연.** ARM 스키마는 `required`를 거의 쓰지 않는다 — NIC의
  PropertiesFormat조차 required 목록이 없다(실측). 필연 판정은 반사실 실험
  (preflight·apply)의 몫이고, 여기서는 `requiredInSchema`를 있는 그대로만 적는다.

후보마다 인용이 붙는다: `<캐시 키>#<JSON 포인터>`. 캐시는 커밋 SHA로 핀 박혀
있으므로(fetch_azure) 인용은 원문 실물로 되짚을 수 있다.

산출: `azure_candidates.json`. **재계산 가능해야 한다** — 저장값과 재계산의 정합을
`test_depkb.py`가 강제한다(graphkb `questions` 사영과 같은 규율).

실행: `python -m app.core.cloudkb.depkb.extract_azure`
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .fetch_azure import CACHE, COMMIT, FILES
from .vocabulary import PATH_SEGMENTS, REFERENCE_WRAPPERS, TYPES

#: 스키마 파일 간 $ref의 파일명 → 캐시 키.
_BASENAMES = {path.rsplit("/", 1)[1]: key for key, path in FILES.items()}

_ARTIFACT = Path(__file__).resolve().parent / "azure_candidates.json"

#: definition 이름 → 어휘 타입 (역결속).
_DEF_TO_TYPE = {(b.file, b.definition): t for t, b in TYPES.items()}


def _load(file_key: str) -> dict:
    return json.loads((CACHE / f"{file_key}.json").read_text(encoding="utf-8"))


def _resolve(ref: str, current_file: str) -> tuple[str, str] | None:
    """$ref → (캐시 키, definition 이름). 캐시 밖이면 None (세기만 한다)."""
    if ref.startswith("#/definitions/"):
        return current_file, ref.rsplit("/", 1)[1]
    m = re.match(r"^(?:\./)?([\w.]+\.json)#/definitions/(.+)$", ref)
    if m and m.group(1) in _BASENAMES:
        return _BASENAMES[m.group(1)], m.group(2)
    return None


def _walk_type(subject: str, docs: dict) -> tuple[list[dict], int]:
    """주체 타입의 구성 트리를 걷는다. 다른 어휘 타입을 만나면 후보를 내고 멈춘다."""
    binding = TYPES[subject]
    out: list[dict] = []
    unresolved = 0
    seen: set[tuple[str, str]] = set()
    #: (파일, definition, trail, 상속된 readOnly) — **readOnly는 하강을 따라
    #: 전파된다.** 상위 속성이 서버 채움(출력)이면 그 밑의 참조는 전부 백링크다.
    #: 전파를 빠뜨린 첫 판이 `publicIp→subnet`을 입력 참조로 오분류했다
    #: (`PublicIPAddressPropertiesFormat.ipConfiguration`이 readOnly인데 그 안의
    #: subnet 참조가 입력으로 읽혔다).
    queue: list[tuple[str, str, str, bool]] = [
        (binding.file, binding.definition, "", False)
    ]

    def note_ref(ref: str, file_key: str, trail: str, pointer: str,
                 required: bool, read_only: bool) -> None:
        nonlocal unresolved
        hit = _resolve(ref, file_key)
        if hit is None:
            unresolved += 1
            return
        hit_file, hit_def = hit
        target = _DEF_TO_TYPE.get((hit_file, hit_def))
        if target is None and hit_def in REFERENCE_WRAPPERS:
            target = REFERENCE_WRAPPERS[hit_def]
        if target is not None:
            if target != subject:
                out.append({
                    "subject": subject,
                    "object": target,
                    "form": "readonly-backlink" if read_only else "input-reference",
                    "trail": trail,
                    "cite": f"{file_key}.json#{pointer}",
                    "requiredInSchema": required,
                })
            return
        if (hit_file, hit_def) not in seen:
            seen.add((hit_file, hit_def))
            queue.append((hit_file, hit_def, trail, read_only))

    while queue:
        file_key, def_name, trail, inherited_ro = queue.pop(0)
        node = docs[file_key].get("definitions", {}).get(def_name)
        if node is None:
            unresolved += 1
            continue
        base = f"/definitions/{def_name}"
        parts = [node] + list(node.get("allOf", []))
        for part in parts:
            if "$ref" in part and part is not node:
                note_ref(part["$ref"], file_key, trail, f"{base}/allOf",
                         required=False, read_only=False)
                continue
            required_here = set(part.get("required", []))
            for prop, spec in part.get("properties", {}).items():
                ptr = f"{base}/properties/{prop}"
                new_trail = f"{trail}.{prop}" if trail else prop
                read_only = inherited_ro or bool(spec.get("readOnly"))
                target_spec, ptr2 = spec, ptr
                if spec.get("type") == "array" and isinstance(spec.get("items"), dict):
                    target_spec, ptr2 = spec["items"], f"{ptr}/items"
                if "$ref" in target_spec:
                    note_ref(target_spec["$ref"], file_key, new_trail, ptr2,
                             required=prop in required_here, read_only=read_only)
    return out, unresolved


def _nested_put_paths(docs: dict) -> list[dict]:
    """PUT 경로의 세그먼트 중첩 — `…/{부모형}/{이름}/{자식형}/{이름}` 꼴.

    자식이 부모의 경로 밑에서만 생성된다면 그것은 소속(존재+생명주기)의 후보다.
    여기서는 후보만 낸다 — "밑에서**만**"인지는 전 파일의 PUT 경로 전수가 말한다.
    """
    out = []
    seg = "|".join(map(re.escape, PATH_SEGMENTS))
    pat = re.compile(rf"/({seg})/\{{[^}}]+\}}/({seg})/\{{[^}}]+\}}$")
    for file_key, doc in docs.items():
        for path, ops in doc.get("paths", {}).items():
            if "put" not in {k.lower() for k in ops}:
                continue
            m = pat.search(path)
            if not m:
                continue
            parent, child = PATH_SEGMENTS[m.group(1)], PATH_SEGMENTS[m.group(2)]
            out.append({
                "subject": child,
                "object": parent,
                "form": "path-nesting",
                "trail": "",
                "cite": f"{file_key}.json#/paths/{path}",
                "requiredInSchema": True,
            })
    return out


def extract() -> dict:
    docs = {key: _load(key) for key in FILES}
    candidates: list[dict] = []
    unresolved_total = 0
    for subject in TYPES:
        found, unresolved = _walk_type(subject, docs)
        candidates.extend(found)
        unresolved_total += unresolved
    candidates.extend(_nested_put_paths(docs))
    candidates.sort(key=lambda c: (c["subject"], c["object"], c["cite"]))
    return {
        "_note": (
            "azure 스키마 원문에서 뽑은 의존 **후보**다 — 주장이 아니다. 필연은 "
            "스키마가 말하지 않으므로(requiredInSchema가 그 실측이다) 반사실 실험이 "
            "판정한다. 인용의 캐시는 cache/azure/manifest.json이 핀 박는다."
        ),
        "_pin": {"repo": "Azure/azure-rest-api-specs", "commit": COMMIT},
        "_coverage": {"unresolvedExternalRefs": unresolved_total},
        "candidates": candidates,
    }


if __name__ == "__main__":
    result = extract()
    _ARTIFACT.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    pairs: dict[tuple[str, str], list[str]] = {}
    for c in result["candidates"]:
        pairs.setdefault((c["subject"], c["object"]), []).append(c["form"])
    print(f"candidates: {len(result['candidates'])}  pairs: {len(pairs)}  "
          f"unresolved refs: {result['_coverage']['unresolvedExternalRefs']}")
    for (s, o), forms in sorted(pairs.items()):
        print(f"  {s} -> {o}: {len(forms)} ({', '.join(sorted(set(forms)))})")
