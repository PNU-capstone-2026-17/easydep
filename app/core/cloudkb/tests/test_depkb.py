"""depkb(제로베이스 의존 지식)의 구조적 불변식.

지키는 것 넷: **핀**(원천이 해시로 고정되나) · **결속**(어휘가 원문 실물과 맞나) ·
**사영**(산출물이 원천에서 재계산되나) · **인용**(모든 후보가 원문 실물로
되짚어지나). 값의 옳음은 반사실 실험(preflight·apply)의 몫이라 여기 없다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.core.cloudkb.depkb import extract_azure, vocabulary
from app.core.cloudkb.depkb.fetch_azure import CACHE, FILES

_ARTIFACT = Path(extract_azure.__file__).resolve().parent / "azure_candidates.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((CACHE / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def artifact() -> dict:
    return json.loads(_ARTIFACT.read_text(encoding="utf-8"))


def test_cache_matches_pinned_manifest(manifest) -> None:
    """캐시 실물의 해시가 manifest와 어긋나면 인용 전부가 허공을 가리킨다."""
    assert manifest["_pin"]["commit"], "핀 없는 소스는 재현이 안 된다"
    for key in FILES:
        entry = manifest["files"][key]
        blob = (CACHE / f"{key}.json").read_bytes()
        assert hashlib.sha256(blob).hexdigest() == entry["sha256"], (
            f"{key}: 캐시가 manifest와 다르다 — 재수집하고 핀을 갱신하라"
        )


def test_vocabulary_bindings_resolve_to_real_definitions() -> None:
    """어휘의 azure 결속은 핀 박힌 원문에 실재해야 한다.

    판(api-version)을 올리면 definition 이름이 움직일 수 있다(실제로 이 판에서
    `VirtualNetwork`가 `Common.VirtualNetwork`로 옮겨져 있었다). 결속이 조용히
    낡는 대신 여기서 죽는다.
    """
    for type_id, b in vocabulary.TYPES.items():
        doc = json.loads((CACHE / f"{b.file}.json").read_text(encoding="utf-8"))
        assert b.definition in doc.get("definitions", {}), (
            f"{type_id}: {b.file}에 {b.definition}이 없다"
        )


def test_candidates_are_recomputable_from_originals(artifact) -> None:
    """산출물은 원천의 사영이다 — 재계산과 다르면 손이 들어간 것이다."""
    assert artifact == extract_azure.extract(), (
        "azure_candidates.json이 재계산과 다르다 — 산출물을 고치지 말고 "
        "추출기 또는 원천을 보라"
    )


def test_every_cite_resolves_into_the_cached_original(artifact) -> None:
    """후보의 인용은 전부 원문 실물로 열려야 한다 — 안 열리면 주장이다."""
    docs = {k: json.loads((CACHE / f"{k}.json").read_text(encoding="utf-8"))
            for k in FILES}
    for c in artifact["candidates"]:
        file_part, pointer = c["cite"].split("#", 1)
        doc = docs[file_part.removesuffix(".json")]
        if pointer.startswith("/paths/"):
            assert pointer.removeprefix("/paths/") in doc["paths"], c["cite"]
            continue
        node = doc
        for part in pointer.strip("/").split("/"):
            assert isinstance(node, dict) and part in node, c["cite"]
            node = node[part]


def test_azure_native_skeleton_is_present_as_input_references(artifact) -> None:
    """azure 원문이 말하는 골격 — VM→NIC→Subnet(→VNet), NSG·Disk.

    이 다섯이 사라지면 추출기가 깨진 것이고, 형태가 바뀌면(입력↔백링크) 원문
    판이 바뀐 것이다. tumblebug 어휘에는 **NIC 층이 없었다** — 이 골격이
    제로베이스가 스파인을 바꿔서 얻은 것의 실물이다.
    """
    inputs = {
        (c["subject"], c["object"])
        for c in artifact["candidates"] if c["form"] == "input-reference"
    }
    for pair in [("nic", "subnet"), ("nic", "firewall"), ("vm", "nic"),
                 ("vm", "disk"), ("subnet", "firewall")]:
        assert pair in inputs, f"골격 간선이 사라졌다: {pair}"
    nesting = {
        (c["subject"], c["object"])
        for c in artifact["candidates"] if c["form"] == "path-nesting"
    }
    assert ("subnet", "network") in nesting, "subnet의 소속(경로 중첩)이 사라졌다"


def test_backlinks_never_masquerade_as_inputs(artifact) -> None:
    """서버가 채우는 백링크(readOnly)는 입력 참조로 승격되지 않는다.

    readOnly는 상위 속성에 붙고 참조는 그 안에 있다 — 전파를 빠뜨린 첫 판이
    `publicIp→subnet`을 입력으로 오분류했다. 이 셋은 원문에서 readOnly를 확인한
    백링크다(PIP.ipConfiguration · NSG.networkInterfaces · Subnet.ipConfigurations).
    """
    by_pair: dict[tuple[str, str], set[str]] = {}
    for c in artifact["candidates"]:
        by_pair.setdefault((c["subject"], c["object"]), set()).add(c["form"])
    for pair in [("publicIp", "subnet"), ("firewall", "nic"), ("subnet", "nic")]:
        assert by_pair.get(pair) == {"readonly-backlink"}, (
            f"{pair}: 백링크가 입력으로 승격됐다 — readOnly 전파를 보라"
        )


def test_vm_does_not_reference_the_ssh_key_resource(artifact) -> None:
    """azure VM은 `SshPublicKeyResource`를 참조하지 않는다 — 키는 인라인 값이다.

    "sshKey 필수"는 도구(tumblebug)의 요구였다는 이전 판정(커밋 a490071)이,
    이번에는 그 산출물을 전혀 쓰지 않은 제로베이스 추출에서 **독립적으로
    재현**됐다. 이 간선이 나타나면 원문 판이 바뀐 것이니 사람이 봐야 한다.
    """
    assert not [
        c for c in artifact["candidates"]
        if c["subject"] == "vm" and c["object"] == "sshKey"
    ], "vm→sshKey가 생겼다 — azure가 키를 자원 참조로 바꿨는지 원문을 보라"


def test_unresolved_refs_are_counted_not_hidden(artifact) -> None:
    """캐시 밖으로 나가는 $ref는 숨기지 않고 센다.

    0으로 적히면 "다 봤다"로 읽힌다 — graphkb `unjudgedIdFields`와 같은 규율.
    지금 값은 공통 타입 파일(types.json 등)을 캐시에 안 담아서 생기는 것이다.
    """
    assert artifact["_coverage"]["unresolvedExternalRefs"] > 0
