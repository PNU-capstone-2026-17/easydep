"""Resolve every schema source locator used by the active dependency ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import fetch_vendors

HERE = Path(__file__).resolve().parent
AZURE_CACHE = HERE / "cache" / "azure"


@dataclass(frozen=True)
class Resolution:
    locator: str
    source: str
    exists: bool


def _descend(document: object, segments: list[str]) -> object:
    """Resolve our historical slash locator, including Azure path keys with slashes."""
    if not segments:
        return document
    if isinstance(document, list):
        index = int(segments[0])
        return _descend(document[index], segments[1:])
    if not isinstance(document, dict):
        raise KeyError("path continues through a scalar")
    # Historical Azure locators did not JSON-Pointer escape '/' inside a path
    # template.  Longest-key matching makes that legacy syntax explicit.
    for end in range(len(segments), 0, -1):
        key = "/".join(segments[:end])
        if key in document:
            return _descend(document[key], segments[end:])
    raise KeyError("/".join(segments))


def resolve(locator: str) -> object:
    source, separator, fragment = locator.partition("#/")
    if not separator or not source or not fragment:
        raise ValueError(f"invalid schema source locator: {locator}")
    if source in fetch_vendors.SOURCES:
        document = fetch_vendors.load(source)
    else:
        path = (AZURE_CACHE / source).resolve()
        if AZURE_CACHE.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"unregistered schema source: {source}")
        manifest = json.loads((AZURE_CACHE / "manifest.json").read_text(encoding="utf-8"))
        entries = {
            f"{name}.json": metadata for name, metadata in manifest["files"].items()
        }
        metadata = entries.get(source)
        if metadata is None:
            raise ValueError(f"Azure schema is absent from the pinned manifest: {source}")
        # The manifest pins upstream LF bytes. Git may materialize committed JSON
        # with CRLF on Windows, so normalize only line endings before comparison.
        digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if digest != metadata["sha256"]:
            raise ValueError(f"Azure schema hash differs from manifest: {source}")
        document = json.loads(path.read_text(encoding="utf-8"))
    return _descend(document, fragment.split("/"))


def verify_claims(claims_path: Path | None = None) -> list[Resolution]:
    path = claims_path or HERE / "claims.json"
    claims = json.loads(path.read_text(encoding="utf-8"))["claims"]
    locators = sorted({
        str(observation["cite"])
        for claim in claims
        for observation in claim["observations"]
        if observation["acquisitionMethod"] == "schemaDeclaration"
    })
    resolutions: list[Resolution] = []
    for locator in locators:
        source = locator.split("#", 1)[0]
        resolve(locator)
        resolutions.append(Resolution(locator, source, True))
    return resolutions


def main() -> None:
    resolutions = verify_claims()
    counts: dict[str, int] = {}
    for item in resolutions:
        counts[item.source] = counts.get(item.source, 0) + 1
    print(json.dumps({"resolved": len(resolutions), "bySource": counts}, indent=2))


if __name__ == "__main__":
    main()
