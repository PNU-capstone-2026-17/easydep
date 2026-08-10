from pathlib import Path

from app.core.cloudkb.kbcommon import artifact


def test_committed_bundle_wins_over_stale_default_output(tmp_path, monkeypatch):
    data = tmp_path / "data"
    output = tmp_path / "output"
    data.mkdir()
    output.mkdir()
    bundled = data / "catalog.json.gz"
    stale = output / "catalog.json"
    bundled.write_bytes(b"bundle")
    stale.write_bytes(b"stale")

    monkeypatch.setattr(artifact, "BUNDLED_DIR", data)
    monkeypatch.setattr(artifact, "DEFAULT_OUTPUT", output)

    assert artifact.resolve(output, "catalog.json") == bundled


def test_explicit_build_directory_does_not_fall_back_to_bundle(tmp_path, monkeypatch):
    data = tmp_path / "data"
    output = tmp_path / "output"
    explicit = tmp_path / "experiment-output"
    data.mkdir()
    output.mkdir()
    explicit.mkdir()
    (data / "catalog.json.gz").write_bytes(b"bundle")

    monkeypatch.setattr(artifact, "BUNDLED_DIR", data)
    monkeypatch.setattr(artifact, "DEFAULT_OUTPUT", output)

    assert artifact.resolve(explicit, "catalog.json") is None


def test_default_output_serves_unbundled_development_artifact(tmp_path, monkeypatch):
    data = tmp_path / "data"
    output = tmp_path / "output"
    data.mkdir()
    output.mkdir()
    generated = output / "local-only.json"
    generated.write_bytes(b"generated")

    monkeypatch.setattr(artifact, "BUNDLED_DIR", data)
    monkeypatch.setattr(artifact, "DEFAULT_OUTPUT", output)

    assert artifact.resolve(output, "local-only.json") == generated
