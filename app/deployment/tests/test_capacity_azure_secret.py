"""Azure `x-ms-secret` → 비밀 속성 (azure_secret.py).

여기서 지키는 것 셋:
- **PUT 본문 안의 것만** 담는다 (배포 때 넣는 값). 응답 전용 secret은 읽을 수 있어
  이 축의 뜻과 어긋나므로 뺀다.
- 우리 인덱스에 **없는 타입은 담지 않는다** (조인 안 되면 답할 길이 없다).
- `x-ms-mutability`와 **직교**한다 — 같은 속성에 둘 다 붙어도 중복이 아니다.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from capacitykb.parsers.azure_secret import _walk_secret, parse_tarball
from kbcommon.type_ids import AzureTypeIndex


@pytest.fixture
def index() -> AzureTypeIndex:
    # DBforMySQL/flexibleServers만 아는 인덱스. Foo/bar는 일부러 뺀다.
    return AzureTypeIndex(
        latest={"Microsoft.DBforMySQL/flexibleServers": ("2023-01-01", "x.json")},
        by_lower={"microsoft.dbformysql/flexibleservers": "Microsoft.DBforMySQL/flexibleServers"},
    )


def _spec(namespace: str, type_seg: str, body_props: dict) -> dict:
    """최신 stable 경로 규약에 맞는 최소 스펙 문서."""
    return {
        "paths": {
            f"/subscriptions/{{s}}/providers/{namespace}/{type_seg}/{{n}}": {
                "put": {
                    "parameters": [
                        {"in": "body", "name": "p", "schema": {"$ref": "#/definitions/Body"}}
                    ]
                }
            }
        },
        "definitions": {"Body": {"properties": body_props}},
    }


def _build_tar(tmp_path, doc: dict):
    # tar 멤버 이름은 **항상 forward slash**다. Windows 경로 구분자를 쓰면
    # _STABLE 정규식(`/resource-manager/…`)이 안 맞아 조용히 0건이 된다.
    member = (
        "specification/mysql/resource-manager/Microsoft.DBforMySQL/"
        "stable/2023-01-01/mysql.json"
    )
    tar = tmp_path / "specs.tar.gz"
    with tarfile.open(tar, "w:gz") as archive:
        raw = json.dumps(doc).encode()
        info = tarfile.TarInfo(member)
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    return tar


def test_walk_collects_only_marked_properties() -> None:
    """순회 함수는 `x-ms-secret: true`인 것만 모은다. 중첩·$ref도 따라간다."""
    defs = {
        "Body": {
            "properties": {
                "administratorLoginPassword": {"type": "string", "x-ms-secret": True},
                "administratorLogin": {"type": "string"},  # 비밀 아님
                "nested": {"$ref": "#/definitions/Nested"},
            }
        },
        "Nested": {"properties": {"sasToken": {"type": "string", "x-ms-secret": True}}},
    }
    out: list = []
    _walk_secret(defs, defs["Body"], "", out, frozenset())
    assert set(out) == {"administratorLoginPassword", "nested.sasToken"}


def test_put_body_secret_becomes_constraint(tmp_path, index) -> None:
    doc = _spec(
        "Microsoft.DBforMySQL", "flexibleServers",
        {"administratorLoginPassword": {"type": "string", "x-ms-secret": True}},
    )
    tar = _build_tar(tmp_path, doc)
    capacity, report = parse_tarball(tar, type_index=index)
    secrets = [c for c in capacity.constraints if c.kind == "secret"]
    assert len(secrets) == 1
    c = secrets[0]
    assert c.property == "administratorLoginPassword"
    assert c.value is True
    assert c.evidence == "swagger-secret"
    assert c.basis == "stated"  # 원본이 단 주석


def test_unknown_type_is_counted_not_kept(tmp_path, index) -> None:
    """인덱스에 없는 타입은 담지 않고 센다 — 조인 안 되면 답할 길이 없다."""
    doc = {
        "paths": {
            "/subscriptions/{s}/providers/Microsoft.Foo/bars/{n}": {
                "put": {
                    "parameters": [
                        {"in": "body", "schema": {"$ref": "#/definitions/Body"}}
                    ]
                }
            }
        },
        "definitions": {"Body": {"properties": {"key": {"x-ms-secret": True}}}},
    }
    tar = _build_tar(tmp_path, doc)
    capacity, report = parse_tarball(tar, type_index=index)
    assert not [c for c in capacity.constraints if c.kind == "secret"]
    assert report.unknown_types["Microsoft.Foo/bars"] == 1


def test_response_only_secret_is_not_collected(tmp_path, index) -> None:
    """PUT 본문이 없으면(응답 전용) 담지 않는다 — 읽을 수 있는 값이라 뜻이 어긋난다."""
    doc = {
        "paths": {
            "/subscriptions/{s}/providers/Microsoft.DBforMySQL/flexibleServers/{n}": {
                # get만 있고 put 없음
                "get": {"responses": {"200": {"schema": {"$ref": "#/definitions/Body"}}}}
            }
        },
        "definitions": {"Body": {"properties": {"key": {"x-ms-secret": True}}}},
    }
    tar = _build_tar(tmp_path, doc)
    capacity, _ = parse_tarball(tar, type_index=index)
    assert not [c for c in capacity.constraints if c.kind == "secret"]


def test_secret_is_orthogonal_to_mutability(tmp_path, index) -> None:
    """secret과 mutability가 같은 속성에 붙어도 서로 다른 kind라 중복이 아니다."""
    doc = _spec(
        "Microsoft.DBforMySQL", "flexibleServers",
        {
            "administratorLoginPassword": {
                "type": "string",
                "x-ms-secret": True,
                "x-ms-mutability": ["create", "update"],  # 비밀이지만 변경 가능
            }
        },
    )
    tar = _build_tar(tmp_path, doc)
    capacity, _ = parse_tarball(tar, type_index=index)
    # 이 파서는 secret만 담는다. mutability는 azure_mutability.py 몫이다.
    kinds = {c.kind for c in capacity.constraints}
    assert kinds == {"secret"}
