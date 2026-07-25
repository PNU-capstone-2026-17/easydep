"""Azure 작업 소요 (x-ms-long-running-operation).

여기서 지켜야 하는 것:
- **모순은 담지 않는다.** 같은 타입·메서드를 파일마다 다르게 말하는 것이 있다.
  하나를 고르면 그건 우리 짐작이다(`aws_limits`와 같은 원칙).
- **POST 액션의 마지막 마디는 액션이지 타입이 아니다.** 안 떼면 없는 타입이 나온다.
- **"원본이 말 안 함"과 "즉시"는 다른 답이다.** 섞으면 타임아웃을 짧게 잡게 된다.
"""

from __future__ import annotations

from app.deployment.capacitykb.parsers.azure_operations import action_type, parse_tarball
from app.deployment.kbcommon.type_ids import AzureTypeIndex


def test_action_segment_is_stripped_before_typing() -> None:
    """`/virtualMachines/{vm}/start`의 `start`는 액션이다."""
    base = "/subscriptions/{s}/resourceGroups/{g}/providers/"
    assert action_type(base + "Microsoft.Compute/virtualMachines/{vm}/start") == (
        "Microsoft.Compute/virtualMachines"
    )
    assert action_type(base + "Microsoft.ContainerService/managedClusters/{n}/abort") == (
        "Microsoft.ContainerService/managedClusters"
    )


def test_action_type_differs_from_raw_arm_type() -> None:
    """액션을 안 떼면 다른(없는) 타입이 나온다 — 이 차이가 버그의 정체였다."""
    from app.deployment.capacitykb.parsers.azure_mutability import arm_type

    url = (
        "/subscriptions/{s}/providers/Microsoft.Compute/locations/{l}"
        "/virtualMachinesBulkCancel"
    )
    assert arm_type(url) != action_type(url)


import io
import json
import tarfile

INDEX = AzureTypeIndex(
    latest={"Microsoft.Compute/virtualMachines": ("2024-01-01", "x.json")},
    by_lower={"microsoft.compute/virtualmachines": "Microsoft.Compute/virtualMachines"},
)

BASE = "/subscriptions/{s}/providers/Microsoft.Compute/virtualMachines/{vm}"


def _tar(tmp_path, docs: dict[str, dict]):
    tar = tmp_path / "specs.tar.gz"
    with tarfile.open(tar, "w:gz") as archive:
        for name, doc in docs.items():
            member = (
                f"specification/compute/resource-manager/Microsoft.Compute/"
                f"stable/2024-01-01/{name}"
            )
            raw = json.dumps(doc).encode()
            info = tarfile.TarInfo(member)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return tar


def test_methods_and_actions_are_collected(tmp_path) -> None:
    doc = {
        "paths": {
            BASE: {
                "put": {"x-ms-long-running-operation": True},
                "delete": {"x-ms-long-running-operation": True},
                "patch": {"x-ms-long-running-operation": False},
            },
            BASE + "/start": {"post": {"x-ms-long-running-operation": True}},
            BASE + "/generalize": {"post": {"x-ms-long-running-operation": False}},
        }
    }
    records, report = parse_tarball(_tar(tmp_path, {"vm.json": doc}), type_index=INDEX)
    assert len(records) == 1
    record = records[0]
    assert record["create"] is True
    assert record["delete"] is True
    assert record["update"] is False
    assert record["actions"] == [
        {"name": "generalize", "long_running": False},
        {"name": "start", "long_running": True},
    ]
    assert report.actions == 2


def test_conflicting_method_is_dropped_not_guessed(tmp_path) -> None:
    """두 파일이 다르게 말하면 담지 않고 센다."""
    yes = {"paths": {BASE: {"put": {"x-ms-long-running-operation": True}}}}
    no = {"paths": {BASE: {"put": {"x-ms-long-running-operation": False}}}}
    # 두 파일 이름이 달라야 최신 stable 선택에서 둘 다 살아남는다
    records, report = parse_tarball(
        _tar(tmp_path, {"a.json": yes, "b.json": no}), type_index=INDEX
    )
    assert len(report.conflicting) == 1
    assert not any("create" in r for r in records)


def test_unknown_type_is_not_kept(tmp_path) -> None:
    doc = {
        "paths": {
            "/subscriptions/{s}/providers/Microsoft.Nope/things/{n}": {
                "put": {"x-ms-long-running-operation": True}
            }
        }
    }
    records, report = parse_tarball(_tar(tmp_path, {"n.json": doc}), type_index=INDEX)
    assert records == []
    assert report.unknown_types["Microsoft.Nope/things"] == 1
