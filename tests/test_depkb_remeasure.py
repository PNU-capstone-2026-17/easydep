import importlib.util
import subprocess
from pathlib import Path

from app.cloudkb.depkb import remeasure


def _empty_snapshot():
    return {
        "instances": set(), "volumes": set(), "enis": set(), "eips": set(),
        "lbs": set(), "subnets": set(), "security_groups": set(), "igws": set(),
        "vpcs": set(), "keypairs": set(), "roles": set(), "profiles": set(),
    }


def test_aws_cleanup_does_not_mutate_resources_present_before_run(monkeypatch):
    snapshot = _empty_snapshot()
    snapshot["vpcs"] = {"vpc-existing"}
    commands = []
    monkeypatch.setattr(remeasure, "_aws_snapshot", lambda _region: snapshot)
    monkeypatch.setattr(remeasure, "_run", lambda command, **_kwargs: commands.append(command))

    assert remeasure._aws_cleanup(snapshot, "ap-northeast-2") == {}
    assert commands == []


def test_aws_cleanup_targets_only_post_snapshot_resource_ids(monkeypatch):
    before = _empty_snapshot()
    before["lbs"] = {"existing-lb"}
    after = _empty_snapshot()
    after["lbs"] = {"existing-lb", "new-lb"}
    snapshots = iter((after, before))
    commands = []
    monkeypatch.setattr(remeasure, "_aws_snapshot", lambda _region: next(snapshots))
    monkeypatch.setattr(remeasure, "_run", lambda command, **_kwargs: commands.append(command))
    monkeypatch.setattr(remeasure.time, "sleep", lambda _seconds: None)

    assert remeasure._aws_cleanup(before, "ap-northeast-2") == {}
    assert commands == [[
        "aws", "--region", "ap-northeast-2", "elbv2", "delete-load-balancer",
        "--load-balancer-arn", "new-lb",
    ]]


def test_azure_runner_deletes_and_waits_for_every_disposable_group(monkeypatch):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(remeasure, "_run", fake_run)
    monkeypatch.setattr(
        remeasure.subprocess, "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "false\n", ""),
    )

    result = remeasure.run_azure("koreacentral")

    assert result == {"failures": [], "residual": []}
    created = [command[4] for command in commands if command[:4] == ["az", "group", "create", "-n"]]
    deleted = [command[4] for command in commands if command[:4] == ["az", "group", "delete", "-n"]]
    waited = [command[4] for command in commands if command[:4] == ["az", "group", "wait", "-n"]]
    assert sorted(created) == sorted(deleted) == sorted(waited)
    assert len(created) == len(remeasure.AZURE_EXPERIMENTS)


def test_targeted_azure_sig4_runs_only_disk_and_direct_io_fixup(monkeypatch):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(remeasure, "_run", fake_run)
    monkeypatch.setattr(
        remeasure.subprocess, "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "false\n", ""),
    )

    result = remeasure.run_azure("koreacentral", "azure-sig4-2026-07-31")

    assert result == {"failures": [], "residual": []}
    python_files = [command[1] for command in commands if command and command[0] == remeasure.PYTHON]
    assert python_files == ["run.py", "run.py", "redo_disk.py", "run.py"]


def test_targeted_aws_apply2_runs_main_and_both_fixups_under_one_snapshot(monkeypatch):
    snapshot = _empty_snapshot()
    commands = []
    monkeypatch.setattr(remeasure, "_aws_snapshot", lambda _region: snapshot)
    monkeypatch.setattr(remeasure, "_aws_cleanup", lambda before, region: {})
    monkeypatch.setattr(
        remeasure, "_run",
        lambda command, **_kwargs: commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    result = remeasure.run_aws("ap-northeast-2", "aws-apply2-2026-07-31")

    assert result == {"failures": [], "residual": {}}
    assert [command[1] for command in commands] == ["run.py", "run_fix.py", "run_fix2.py"]


def test_gcp_cleanup_uses_resource_scope_and_reserved_prefix_results(monkeypatch):
    first = {
        "instances": [{"name": "depkb-vm", "zone": "zones/asia-northeast3-a"}],
        "forwarding-rules": [], "backend-services": [], "health-checks": [],
        "firewall-rules": [], "routes": [],
        "disks": [{"name": "depkb-disk", "zone": "zones/asia-northeast3-a"}],
        "subnets": [{"name": "depkb-sub", "region": "regions/asia-northeast3"}],
        "networks": [{"name": "depkb-net"}],
    }
    calls = dict.fromkeys(first, 0)

    def resources(kind, _project, *, subnets=False):
        key = "subnets" if subnets else kind
        calls[key] += 1
        # snapshot after run, then final snapshot after cleanup
        return first[key] if calls[key] == 1 else []

    commands = []
    monkeypatch.setattr(remeasure, "_gcloud_resources", resources)
    monkeypatch.setattr(
        remeasure, "_run",
        lambda command, **_kwargs: commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    before = {key: {} for key in remeasure.GCP_RESOURCE_KINDS}
    assert remeasure._gcp_cleanup("cloud-resource-testing", before) == {}
    assert any(command[-2:] == ["--zone", "asia-northeast3-a"] for command in commands)
    assert any(command[-2:] == ["--region", "asia-northeast3"] for command in commands)
    assert all("depkb" in " ".join(command) for command in commands)


def test_gcp_cleanup_preserves_preexisting_prefixed_resources(monkeypatch):
    existing = {key: {} for key in remeasure.GCP_RESOURCE_KINDS}
    existing["networks"] = {"depkb-existing": {"name": "depkb-existing"}}
    snapshots = iter((existing, existing))
    commands = []
    monkeypatch.setattr(remeasure, "_gcp_snapshot", lambda _project: next(snapshots))
    monkeypatch.setattr(
        remeasure, "_run",
        lambda command, **_kwargs: commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    assert remeasure._gcp_cleanup("cloud-resource-testing", existing) == {}
    assert commands == []


def test_gcp_default_route_match_ignores_api_hostname() -> None:
    path = (
        Path(__file__).parents[1]
        / "app/cloudkb/depkb/experiments/gcp-func2-2026-07-31/run.py"
    )
    spec = importlib.util.spec_from_file_location("gcp_func2_experiment", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    found = module.find_default_route(
        [
            {
                "name": "default-route-1",
                "destRange": "0.0.0.0/0",
                "network": (
                    "https://www.googleapis.com/compute/v1/projects/example/global/"
                    "networks/depkbf2-net"
                ),
            }
        ],
        "depkbf2-net",
    )

    assert found["name"] == "default-route-1"
