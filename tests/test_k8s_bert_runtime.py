"""Deployment resources/probes required by the in-process BERT model."""

from pathlib import Path

import yaml

DEPLOYMENT = Path(__file__).parents[1] / "k8s" / "base" / "deployment.yaml"


def _container() -> dict:
    manifest = yaml.safe_load(DEPLOYMENT.read_text(encoding="utf-8"))
    return manifest["spec"]["template"]["spec"]["containers"][0]


def test_startup_probe_allows_cold_bert_warmup_before_liveness():
    container = _container()
    probe = container["startupProbe"]

    assert probe["httpGet"]["path"] == "/healthz"
    assert probe["periodSeconds"] * probe["failureThreshold"] >= 180


def test_memory_limit_can_hold_api_torch_and_bert_checkpoint():
    resources = _container()["resources"]

    assert resources["requests"]["memory"] == "1Gi"
    assert resources["limits"]["memory"] == "2Gi"
