from __future__ import annotations

import gzip
import json

from evaluation.research_protocol.commands.native_inventory import (
    _read_json,
    _sha256_file,
    _sha256_lf_normalized,
)


def test_read_json_supports_plain_and_gzip(tmp_path):
    plain = tmp_path / "model.json"
    compressed = tmp_path / "model.json.gz"
    plain.write_text('{"version":"plain"}', encoding="utf-8")
    with gzip.open(compressed, "wt", encoding="utf-8") as stream:
        json.dump({"version": "gzip"}, stream)

    assert _read_json(plain) == {"version": "plain"}
    assert _read_json(compressed) == {"version": "gzip"}
    assert len(_sha256_file(plain)) == 64


def test_source_digest_ignores_only_git_line_ending_conversion(tmp_path):
    windows = tmp_path / "windows.json"
    unix = tmp_path / "unix.json"
    windows.write_bytes(b'{\r\n  "value": 1\r\n}\r\n')
    unix.write_bytes(b'{\n  "value": 1\n}\n')

    assert _sha256_lf_normalized(windows) == _sha256_file(unix)
