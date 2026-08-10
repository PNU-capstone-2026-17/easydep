from app.core.orchestration import vm_selection
from app.core.orchestration.iac_binding_validation import (
    validate_vm_selection_binding,
)


def _spec(**values):
    return {
        "id": "aws+ap-northeast-2+t3.medium",
        "provider": "aws",
        "region": "ap-northeast-2",
        "specName": "t3.medium",
        "vCPU": 2,
        "memGiB": 4.0,
        "hourlyUSD": 0.05,
        "architecture": "x86_64",
    } | values


def _catalog(monkeypatch, specs=None, performance_status="ok"):
    monkeypatch.setattr(
        vm_selection, "resolve_region", lambda _region: ({"ap-northeast-2"}, "exact")
    )
    monkeypatch.setattr(vm_selection, "filter_specs", lambda *_args, **_kwargs: specs or [_spec()])
    monkeypatch.setattr(vm_selection, "load_warning", lambda: None)
    monkeypatch.setattr(
        vm_selection,
        "recommend_note",
        lambda *_args, **_kwargs: type(
            "Note", (), {"status": performance_status, "text": None}
        )(),
    )


def test_selection_is_deferred_without_a_capacity_floor(monkeypatch):
    _catalog(monkeypatch)

    result = vm_selection.select_vm_candidates({
        "provider": "aws", "region": "ap-northeast-2", "monthlyBudgetUSD": 100
    }, {})

    assert result["status"] == "deferred"
    assert result["reason"] == "missing_capacity_floor"


def test_selection_filters_capacity_and_compute_budget(monkeypatch):
    _catalog(monkeypatch, [_spec(hourlyUSD=0.05), _spec(specName="large", hourlyUSD=0.2)])

    result = vm_selection.select_vm_candidates({
        "provider": "aws",
        "region": "ap-northeast-2",
        "monthlyBudgetUSD": 100,
        "minVCpu": 2,
        "minMemoryGiB": 4,
    }, {})

    assert result["status"] == "selected"
    assert result["recommended"]["specName"] == "t3.medium"
    assert result["recommended"]["monthlyComputeListPriceUsd"] == 36.5
    assert result["budgetScope"] == "compute-only"


def test_high_availability_applies_the_two_vm_compute_floor(monkeypatch):
    _catalog(monkeypatch)

    result = vm_selection.select_vm_candidates({
        "provider": "aws",
        "region": "ap-northeast-2",
        "monthlyBudgetUSD": 50,
        "minVCpu": 2,
        "multiZone": True,
    }, {})

    assert result["constraints"]["minimumVmCount"] == 2
    assert result["status"] == "infeasible"
    assert "recommended" not in result


def test_partial_region_match_is_not_used_as_one_price(monkeypatch):
    monkeypatch.setattr(
        vm_selection, "resolve_region", lambda _region: ({"us-east-1", "us-east-2"}, "partial")
    )

    result = vm_selection.select_vm_candidates({
        "provider": "aws", "region": "us-east", "minVCpu": 2
    }, {})

    assert result["status"] == "deferred"
    assert result["reason"] == "region_not_exact_in_catalog"


def test_steady_workload_prefers_a_checked_non_warning_candidate(monkeypatch):
    specs = [_spec(specName="burst", hourlyUSD=0.04), _spec(specName="steady", hourlyUSD=0.08)]
    _catalog(monkeypatch, specs)

    def note(_provider, name, *_args):
        status = "warn" if name == "burst" else "ok"
        return type("Note", (), {"status": status, "text": None})()

    monkeypatch.setattr(vm_selection, "recommend_note", note)
    result = vm_selection.select_vm_candidates({
        "provider": "aws",
        "region": "ap-northeast-2",
        "monthlyBudgetUSD": 100,
        "minVCpu": 2,
        "trafficPattern": "steady",
    }, {})

    assert result["recommended"]["specName"] == "steady"
    assert result["selectionBasis"].startswith("lowest-cost-candidate-with-no-recorded")


def test_vm_selection_binding_reads_literals_and_variable_defaults():
    cases = {
        "aws": (
            "t3.medium",
            'variable "vm_type" { default = "t3.medium" }\n'
            'resource "aws_instance" "app" { instance_type = var.vm_type }',
        ),
        "azure": (
            "Standard_D2s_v5",
            'resource "azurerm_linux_virtual_machine" "app" '
            '{ size = "Standard_D2s_v5" }',
        ),
        "gcp": (
            "e2-medium",
            'resource "google_compute_instance" "app" '
            '{ machine_type = "e2-medium" }',
        ),
    }

    for provider, (expected, terraform) in cases.items():
        report = validate_vm_selection_binding(
            {"main.tf": terraform},
            provider=provider,
            expected_spec_name=expected,
        )
        assert report["status"] == "passed"


def test_vm_selection_binding_rejects_a_different_or_unresolved_size():
    different = validate_vm_selection_binding(
        {"main.tf": 'resource "aws_instance" "app" { instance_type = "t3.small" }'},
        provider="aws",
        expected_spec_name="t3.medium",
    )
    unresolved = validate_vm_selection_binding(
        {"main.tf": 'resource "aws_instance" "app" { instance_type = var.vm_type }'},
        provider="aws",
        expected_spec_name="t3.medium",
    )

    assert different["status"] == "failed"
    assert unresolved["status"] == "failed"
    assert different["diagnostics"][0]["code"] == "BIND-VM-SIZE-001"
