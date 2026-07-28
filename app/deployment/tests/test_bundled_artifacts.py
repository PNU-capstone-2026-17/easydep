"""저장소에 커밋한 산출물(`data/*.gz`) 폴백.

클론 직후 빌드 없이 동작하게 하는 장치라, **깨져도 티가 안 나는** 종류다 —
빌드된 환경에서는 `output/`이 늘 이기므로 폴백이 죽어도 아무도 모른다.
그래서 따로 지킨다.
"""

from __future__ import annotations

import gzip
import json

from app.deployment.kbcommon import artifact


def test_output_wins_over_bundled(tmp_path, monkeypatch) -> None:
    """새로 빌드한 것이 커밋된 것을 이긴다."""
    monkeypatch.setattr(artifact, "DEFAULT_OUTPUT", tmp_path / "output")
    monkeypatch.setattr(artifact, "BUNDLED_DIR", tmp_path / "data")
    (tmp_path / "output").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "output" / "x.json").write_text('{"v": "fresh"}', encoding="utf-8")
    with gzip.open(tmp_path / "data" / "x.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"v": "bundled"}, f)

    found = artifact.resolve(tmp_path / "output", "x.json")
    assert artifact.load_json(found)["v"] == "fresh"


def test_bundled_used_when_output_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(artifact, "DEFAULT_OUTPUT", tmp_path / "output")
    monkeypatch.setattr(artifact, "BUNDLED_DIR", tmp_path / "data")
    (tmp_path / "output").mkdir()
    (tmp_path / "data").mkdir()
    with gzip.open(tmp_path / "data" / "x.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"v": "bundled"}, f)

    found = artifact.resolve(tmp_path / "output", "x.json")
    assert found is not None and artifact.load_json(found)["v"] == "bundled"


def test_explicit_directory_never_falls_back(tmp_path, monkeypatch) -> None:
    """호출자가 다른 디렉터리를 명시하면 폴백하지 않는다.

    **이게 없으면 테스트가 검사하려던 상황이 사라진다.** costkb는 빈 디렉터리를
    묶어 "번들 36건 폴백"을, perfkb는 "미빌드"를 검사하는데, 거기에 커밋된
    73,083건이 슬쩍 끼어들면 그 회귀들이 통째로 무의미해진다.
    """
    monkeypatch.setattr(artifact, "BUNDLED_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    with gzip.open(tmp_path / "data" / "x.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"v": "bundled"}, f)

    assert artifact.resolve(tmp_path / "somewhere-else", "x.json") is None


def test_read_dataset_accepts_gzip(tmp_path) -> None:
    """`read_dataset`이 `.gz`를 못 읽으면 폴백이 조용히 '빌드 안 됨'이 된다.

    실제로 그렇게 새어 나갔다 — graph·capacity는 되는데 cost·perf만 0건이었다.
    """
    path = tmp_path / "x.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"v": 1}, f)
    data, error = artifact.read_dataset(path, {"type": "object"})
    assert error is None and data == {"v": 1}


def test_pack_refuses_forbidden_and_roundtrips(tmp_path) -> None:
    """`python -m kbcommon pack` — 반복 수작업(output→data gzip)의 명령화.

    핵심은 거부다: 재배포 금지 산출물(aws-managed)은 포장 자체를 거부해야
    라이선스 방벽이 명령 계층에서도 선다."""
    import gzip
    import json

    from app.deployment.kbcommon.__main__ import pack_artifacts

    out = tmp_path / "output"; out.mkdir()
    data = tmp_path / "data"; data.mkdir()
    (out / "x.json").write_text('{"v": 1}', encoding="utf-8")
    (out / "aws-managed-pricing.json").write_text('{"v": 2}', encoding="utf-8")

    assert pack_artifacts(["x.json"], out, data) == 0
    with gzip.open(data / "x.json.gz", "rt", encoding="utf-8") as handle:
        assert json.load(handle) == {"v": 1}

    assert pack_artifacts(["aws-managed-pricing.json"], out, data) == 1
    assert not (data / "aws-managed-pricing.json.gz").exists()


def test_committed_artifacts_carry_their_pins() -> None:
    """커밋한 파일에 `_source`가 살아 있어야 한다.

    핀이 없으면 이 파일이 어느 소스 어느 커밋에서 나왔는지 알 수 없고, 그러면
    "커밋된 데이터"가 그냥 출처 불명 덩어리가 된다.
    """
    if not artifact.BUNDLED_DIR.exists():
        return  # 아직 안 만든 환경
    packed = sorted(artifact.BUNDLED_DIR.glob("*.json.gz"))
    assert packed, "data/ 가 비어 있다"
    for path in packed:
        data = artifact.load_json(path)
        assert "_source" in data or "provenance" in data, f"{path.name}에 출처가 없다"


def test_fast_validator_rejects_what_the_slow_one_rejects(tmp_path) -> None:
    """읽기 경로만 다른 검증기를 쓴다 — **같은 것을 거부해야** 한다.

    빠른데 조용히 통과시키면 느린 것보다 나쁘다. 바꾸기 전에 망가뜨린 입력
    10가지로 두 검증기를 대조했고 판정이 전부 일치했다. 여기서는 그 계약을
    고정한다(대표 사례 셋).
    """
    import jsonschema

    schema = {
        "type": "object",
        "required": ["specs"],
        "properties": {
            "specs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "vcpu": {"type": "integer"},
                        "provider": {"enum": ["aws", "gcp"]},
                    },
                },
            }
        },
    }
    cases = [
        ({"specs": [{"name": "a", "vcpu": 2, "provider": "aws"}]}, True),
        ({"specs": [{"name": "a", "vcpu": "둘"}]}, False),      # 숫자 자리에 문자열
        ({"specs": [{"vcpu": 2}]}, False),                       # 필수 칸 없음
        ({"specs": [{"name": "a", "provider": "없는회사"}]}, False),  # enum 밖
        ({}, False),                                             # 목록 자체가 없음
    ]
    for data, expected_ok in cases:
        try:
            jsonschema.validate(data, schema)
            slow_ok = True
        except jsonschema.ValidationError:
            slow_ok = False
        fast_ok = artifact._validate_fast(data, schema) is None
        assert slow_ok == fast_ok == expected_ok, f"판정이 갈렸다: {data}"


def test_fast_parser_reads_the_same_values(tmp_path) -> None:
    """파서를 바꿔도 **같은 파이썬 값**이 나와야 한다.

    검증기와 달리 여기는 판정이 갈릴 여지가 없지만, 한글·큰 수·null·중첩처럼
    인코딩이 얽히는 것들은 실제로 확인해 둔다.
    """
    import gzip
    import json as stdlib_json

    payload = {
        "_note": "한글과 특수문자 ⚠ · — ‑",
        "big": 9007199254740993,
        "float": 0.1 + 0.2,
        "null": None,
        "nested": [{"a": [1, 2, {"b": "값"}]}],
        "empty": {},
    }
    plain = tmp_path / "x.json"
    plain.write_text(stdlib_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    packed = tmp_path / "y.json.gz"
    with gzip.open(packed, "wt", encoding="utf-8") as handle:
        stdlib_json.dump(payload, handle, ensure_ascii=False)

    assert artifact.load_json(plain) == payload
    assert artifact.load_json(packed) == payload
