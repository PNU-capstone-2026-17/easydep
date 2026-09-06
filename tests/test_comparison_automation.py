from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from evaluation.comparison.adapters.chatdev import parse_chatdev_usage
from evaluation.comparison.adapters.metagpt import (
    parse_cost_manager,
    parse_metagpt_log,
    parse_metagpt_usage_events,
)
from evaluation.comparison.collect import collect_artifacts
from evaluation.comparison.evaluate import evaluate_run
from evaluation.comparison.gates import (
    run_artifact_contains,
    run_artifact_present,
    run_container_http_oracle,
)
from evaluation.comparison.models import (
    SUBJECT_RESULT_SCHEMA,
    load_manifest,
    load_subject_result,
)
from evaluation.comparison.oracle import run_http_oracle
from evaluation.comparison.prompts import render_arm_prompt, render_task_input
from evaluation.comparison.report import add_aggregates, render_markdown, write_reports
from evaluation.comparison.runner import run_experiment
from evaluation.comparison.subjects.artifacts import (
    collect_artifact_evidence,
    collect_requirement_evidence,
)
from evaluation.comparison.subjects.common import llm_settings
from evaluation.comparison.suite import load_suite, materialize_manifests


def _manifest_data() -> dict[str, object]:
    return {
        "schemaVersion": "easydep-comparison-manifest/v1",
        "experimentId": "test-experiment",
        "repetitions": 1,
        "requirements": [
            {
                "id": "FR-01",
                "text": "조회한다.",
                "verificationGates": ["api"],
                "evidenceStages": ["code", "test"],
            },
            {
                "id": "FR-02",
                "text": "등록한다.",
                "verificationGates": [],
                "evidenceStages": ["code"],
            },
        ],
        "constraints": [
            {"id": "C-01", "text": "서울 리전", "verificationGates": ["region"]}
        ],
        "gates": [
            {"id": "api", "kind": "fileExists", "paths": ["{workspace}/app.py"]},
            {"id": "region", "kind": "fileExists", "paths": ["{workspace}/region.txt"]},
        ],
        "arms": [
            {
                "id": "demo",
                "framework": "Demo",
                "command": ["unused"],
            }
        ],
    }


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _subject_data(workspace: Path) -> dict[str, object]:
    return {
        "schemaVersion": SUBJECT_RESULT_SCHEMA,
        "framework": "Demo",
        "frameworkVersion": "1",
        "status": "completed",
        "workspace": str(workspace),
        "usage": {
            "inputTokens": 80,
            "outputTokens": 20,
            "totalTokens": 100,
            "llmCalls": 2,
            "missingUsageCalls": 0,
            "source": "test",
        },
        "requirementEvidence": {
            "FR-01": {"code": ["app.py"], "test": ["test_app.py"]}
        },
        "metadata": {},
    }


def test_llm_settings_loads_dotenv_without_overriding_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "API_KEY=file-secret\n"
        "BASE_URL=https://gateway.example.test/v1/account/gateway/compat\n"
        "MODEL=workers-ai/@cf/openai/gpt-oss-120b\n",
        encoding="utf-8",
    )
    for name in (
        "COMPARISON_API_KEY",
        "OPENAI_API_KEY",
        "API_KEY",
        "COMPARISON_BASE_URL",
        "OPENAI_BASE_URL",
        "BASE_URL",
        "COMPARISON_MODEL",
        "OPENAI_MODEL",
        "MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert llm_settings(dotenv_path) == (
        "file-secret",
        "https://gateway.example.test/v1/account/gateway/compat",
        "workers-ai/@cf/openai/gpt-oss-120b",
    )

    monkeypatch.setenv("COMPARISON_API_KEY", "process-secret")
    monkeypatch.setenv("COMPARISON_MODEL", "process-model")
    assert llm_settings(dotenv_path) == (
        "process-secret",
        "https://gateway.example.test/v1/account/gateway/compat",
        "process-model",
    )


def test_llm_settings_prefers_complete_cloudflare_comparison_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "API_KEY=nvidia-secret\n"
        "BASE_URL=https://integrate.api.nvidia.com/v1\n"
        "MODEL=openai/gpt-oss-120b\n"
        "CLOUDFLARE_API_TOKEN=cloudflare-secret\n"
        "CLOUDFLARE_ACCOUNT_ID=account-id\n"
        "CLOUDFLARE_AI_GATEWAY_ID=gateway-id\n",
        encoding="utf-8",
    )
    for name in (
        "COMPARISON_API_KEY",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_AI_GATEWAY_ID",
        "CLOUDFLARE_COMPARISON_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert llm_settings(dotenv_path) == (
        "cloudflare-secret",
        "https://gateway.ai.cloudflare.com/v1/account-id/gateway-id/compat",
        "workers-ai/@cf/openai/gpt-oss-120b",
    )


def test_evaluation_uses_explicit_numerator_and_denominator(tmp_path: Path) -> None:
    manifest = load_manifest(_write_json(tmp_path / "manifest.json", _manifest_data()))
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("", encoding="utf-8")
    subject_path = _write_json(tmp_path / "subject.json", _subject_data(tmp_path))
    subject = load_subject_result(subject_path, run_directory=tmp_path)
    result = evaluate_run(
        manifest,
        subject,
        [
            {"id": "api", "status": "passed"},
            {"id": "region", "status": "failed"},
        ],
        wall_seconds=1.25,
    )

    assert result["implementedRequirements"]["display"] == "1/2 (50.0%)"
    assert result["implementedRequirements"]["notAutomatedIds"] == ["FR-02"]
    assert result["satisfiedConstraints"]["display"] == "0/1 (0.0%)"
    assert result["traceability"]["display"] == "1/2 (50.0%)"
    assert result["tokensPerImplementedRequirement"] == 100
    assert result["successful"] is False


def test_missing_evidence_file_does_not_count_as_traceable(tmp_path: Path) -> None:
    manifest = load_manifest(_write_json(tmp_path / "manifest.json", _manifest_data()))
    data = _subject_data(tmp_path)
    subject = load_subject_result(
        _write_json(tmp_path / "subject.json", data), run_directory=tmp_path
    )
    result = evaluate_run(
        manifest,
        subject,
        [{"id": "api", "status": "passed"}, {"id": "region", "status": "passed"}],
        wall_seconds=0,
    )
    assert result["traceability"]["numerator"] == 0


def test_requirement_can_reference_one_http_oracle_phase(tmp_path: Path) -> None:
    data = _manifest_data()
    data["requirements"] = [
        {
            "id": "FR-01",
            "text": "조회한다.",
            "verificationGates": ["api#course-catalog"],
            "evidenceStages": [],
        },
        {
            "id": "FR-02",
            "text": "등록한다.",
            "verificationGates": ["api#enroll"],
            "evidenceStages": [],
        },
    ]
    manifest = load_manifest(_write_json(tmp_path / "manifest.json", data))
    subject = load_subject_result(
        _write_json(tmp_path / "subject.json", _subject_data(tmp_path)),
        run_directory=tmp_path,
    )
    result = evaluate_run(
        manifest,
        subject,
        [
            {
                "id": "api",
                "status": "failed",
                "phases": [
                    {"id": "course-catalog", "status": "passed"},
                    {"id": "enroll", "status": "failed"},
                ],
            },
            {"id": "region", "status": "passed"},
        ],
        wall_seconds=0,
    )
    assert result["implementedRequirements"]["display"] == "1/2 (50.0%)"


def test_manifest_rejects_unknown_gate_reference(tmp_path: Path) -> None:
    data = _manifest_data()
    data["requirements"][0]["verificationGates"] = ["unknown"]  # type: ignore[index]
    with pytest.raises(ValueError, match="존재하지 않는 게이트"):
        load_manifest(_write_json(tmp_path / "manifest.json", data))


def test_manifest_rejects_common_artifact_profile_without_protocol(
    tmp_path: Path,
) -> None:
    data = _manifest_data()
    data["arms"][0]["promptProfile"] = "commonArtifacts"  # type: ignore[index]
    with pytest.raises(ValueError, match="promptProtocol이 없습니다"):
        load_manifest(_write_json(tmp_path / "manifest.json", data))


def test_usage_parsers_keep_provider_token_counts() -> None:
    chatdev = parse_chatdev_usage(
        "prompt_tokens: 10, completion_tokens: 4, total_tokens: 14\n"
        "prompt_tokens: 20, completion_tokens: 6, total_tokens: 26"
    )
    assert chatdev["inputTokens"] == 30
    assert chatdev["outputTokens"] == 10
    assert chatdev["totalTokens"] == 40
    assert chatdev["llmCalls"] == 2

    metagpt = parse_metagpt_log(
        "Current cost: 0.1, prompt_tokens: 12, completion_tokens: 5"
    )
    assert metagpt["totalTokens"] == 17
    aggregate = parse_cost_manager(
        {"total_prompt_tokens": 100, "total_completion_tokens": 30, "llm_calls": 3}
    )
    assert aggregate["totalTokens"] == 130
    assert aggregate["llmCalls"] == 3
    missing = parse_chatdev_usage("no provider usage in this log")
    assert missing["totalTokens"] is None
    assert missing["llmCalls"] is None


def test_metagpt_structured_usage_is_price_table_independent_and_deduplicated() -> None:
    usage = parse_metagpt_usage_events(
        "\n".join(
            [
                json.dumps(
                    {
                        "schemaVersion": "easydep-metagpt-provider-usage-event/v1",
                        "eventId": "42-1",
                        "model": "workers-ai/@cf/openai/gpt-oss-120b",
                        "promptTokens": 120,
                        "completionTokens": 30,
                    }
                ),
                json.dumps(
                    {
                        "schemaVersion": "easydep-metagpt-provider-usage-event/v1",
                        "eventId": "42-2",
                        "model": "workers-ai/@cf/openai/gpt-oss-120b",
                        "promptTokens": 80,
                        "completionTokens": 20,
                    }
                ),
                json.dumps(
                    {
                        "schemaVersion": "easydep-metagpt-provider-usage-event/v1",
                        "eventId": "42-2",
                        "model": "workers-ai/@cf/openai/gpt-oss-120b",
                        "promptTokens": 80,
                        "completionTokens": 20,
                    }
                ),
            ]
        )
    )
    assert usage["inputTokens"] == 200
    assert usage["outputTokens"] == 50
    assert usage["totalTokens"] == 250
    assert usage["llmCalls"] == 2
    assert usage["duplicateUsageRows"] == 1
    assert usage["source"] == "metagpt-structured-provider-usage"


def test_metagpt_structured_usage_exposes_invalid_rows() -> None:
    usage = parse_metagpt_usage_events(
        '{"schemaVersion":"easydep-metagpt-provider-usage-event/v1",'
        '"eventId":"1","promptTokens":10,"completionTokens":2}\nnot-json'
    )
    assert usage["totalTokens"] == 12
    assert usage["llmCalls"] == 1
    assert usage["missingUsageCalls"] == 1
    assert usage["invalidUsageRows"] == 1


def test_metagpt_startup_hook_records_usage_for_unknown_model(tmp_path: Path) -> None:
    fake_packages = tmp_path / "fake-packages"
    cost_manager = fake_packages / "metagpt" / "utils" / "cost_manager.py"
    cost_manager.parent.mkdir(parents=True)
    (fake_packages / "metagpt" / "__init__.py").write_text("", encoding="utf-8")
    (fake_packages / "metagpt" / "utils" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    cost_manager.write_text(
        "class CostManager:\n"
        "    def update_cost(self, prompt_tokens, completion_tokens, model):\n"
        "        return None\n",
        encoding="utf-8",
    )
    usage_path = tmp_path / "usage.jsonl"
    status_path = tmp_path / "status.json"
    hook_directory = (
        Path(__file__).resolve().parents[1]
        / "evaluation"
        / "baselines"
        / "metagpt_usage_hook"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(hook_directory), str(fake_packages)])
    env["EASYDEP_METAGPT_USAGE_LOG"] = str(usage_path)
    env["EASYDEP_METAGPT_USAGE_STATUS"] = str(status_path)
    process = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "from metagpt.utils.cost_manager import CostManager; "
            "CostManager().update_cost(321, 54, 'workers-ai/@cf/openai/gpt-oss-120b')",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "installed"
    usage = parse_metagpt_usage_events(usage_path.read_text(encoding="utf-8"))
    assert usage["inputTokens"] == 321
    assert usage["outputTokens"] == 54
    assert usage["totalTokens"] == 375
    assert usage["llmCalls"] == 1


class _OracleHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 계약
        body = json.dumps({"ok": True, "items": [{"id": 1}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_http_oracle_runs_sequential_and_concurrent_phases() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OracleHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        oracle = {
            "requestTimeoutSeconds": 2,
            "phases": [
                {
                    "id": "single",
                    "kind": "request",
                    "request": {"method": "GET", "path": "/health"},
                    "expect": {"status": 200, "jsonContains": {"ok": True}},
                },
                {
                    "id": "parallel",
                    "kind": "concurrentRequests",
                    "requests": [
                        {"method": "GET", "path": "/a"},
                        {"method": "GET", "path": "/b"},
                        {"method": "GET", "path": "/c"},
                    ],
                    "expect": {"statusCounts": {"200": 3}},
                },
            ],
        }
        result = run_http_oracle(oracle, f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
    assert result["status"] == "passed"
    assert result["passedPhases"] == 2
    assert result["totalPhases"] == 2
    assert run_http_oracle({"phases": []}, "http://127.0.0.1")["status"] == "failed"


def test_full_runner_and_reports(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    manifest = load_manifest(
        repository / "evaluation/comparison/examples/smoke-manifest.json"
    )
    report = run_experiment(manifest, output_root=tmp_path)
    assert len(report["runs"]) == 6
    assert all(
        run["implementedRequirements"]["display"] == "1/2 (50.0%)"
        for run in report["runs"]
    )
    assert all(
        run["commonArtifactCoverage"]["display"] == "9/9 (100.0%)"
        for run in report["runs"]
    )
    prompts_by_arm = {
        run["armId"]: Path(run["prompt"]["armPromptPath"]).read_text(
            encoding="utf-8"
        )
        for run in report["runs"]
        if run["repetition"] == 1
    }
    assert "Required deliverables:" not in prompts_by_arm["easydep-demo"]
    assert "Required deliverables:" in prompts_by_arm["metagpt-demo"]
    assert prompts_by_arm["metagpt-demo"] == prompts_by_arm["chatdev-demo"]
    baseline_hashes = {
        run["prompt"]["armPromptSha256"]
        for run in report["runs"]
        if run["armId"] in {"metagpt-demo", "chatdev-demo"}
    }
    assert len(baseline_hashes) == 1
    json_path, markdown_path = write_reports(
        report, tmp_path / manifest.experiment_id
    )
    assert json_path.is_file()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "1/2 (50.0%)" in markdown
    assert "9/9 (100.0%)" in markdown
    assert "commonArtifacts" in markdown
    assert "총 토큰 중앙값 (전체/성공)" in markdown


def test_runner_preserves_failed_attempt_in_denominator(tmp_path: Path) -> None:
    data = _manifest_data()
    data["arms"] = [
        {
            "id": "broken",
            "framework": "Broken",
            "command": ["executable-that-does-not-exist-easydep-test"],
        }
    ]
    manifest = load_manifest(_write_json(tmp_path / "manifest.json", data))
    report = run_experiment(manifest, output_root=tmp_path / "out")
    run = report["runs"][0]
    assert run["status"] == "failed"
    assert run["successful"] is False
    assert run["implementedRequirements"]["display"] == "0/2 (0.0%)"
    assert all(gate["status"] == "not_run" for gate in run["gates"])


def test_report_keeps_failed_run_in_aggregate_denominator() -> None:
    base = {
        "armId": "demo",
        "framework": "Demo",
        "implementedRequirements": {"numerator": 1, "denominator": 1},
        "traceability": {"numerator": 1, "denominator": 1},
        "usage": {"totalTokens": 10},
        "wallSeconds": 1,
    }
    report = add_aggregates(
        {
            "experimentId": "x",
            "runs": [
                {**base, "successful": True},
                {**base, "successful": False},
            ],
        }
    )
    assert report["aggregates"][0]["successfulRuns"]["display"] == "1/2 (50.0%)"


def test_rendered_report_explains_missing_usage() -> None:
    report = {
        "experimentId": "x",
        "aggregates": [],
        "runs": [],
    }
    markdown = render_markdown(report)
    assert "`미수집`은 0이 아니라" in markdown


def test_multi_domain_suite_materializes_three_real_arms(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    cases = [
        repository
        / "evaluation/baselines/course-registration-cases/e1-course-registration-aws.json",
        repository
        / "evaluation/easydep/requirements/inputs/dev_iot_monitoring.json",
    ]
    suite_path = _write_json(
        tmp_path / "suite.json",
        {
            "schemaVersion": "easydep-comparison-suite/v1",
            "suiteId": "test-suite",
            "repetitions": 2,
            "outputRoot": str(tmp_path / "output"),
            "cases": [
                {"id": f"case-{index}", "input": str(path)}
                for index, path in enumerate(cases, start=1)
            ],
        },
    )
    suite = load_suite(suite_path)
    manifests = materialize_manifests(suite)
    assert len(manifests) == 2
    manifest = load_manifest(manifests[0])
    assert manifest.repetitions == 2
    assert [arm.framework for arm in manifest.arms] == [
        "EasyDep",
        "MetaGPT",
        "ChatDev",
    ]
    assert manifest.arms[1].prompt_profile == "commonArtifacts"
    assert manifest.arms[2].prompt_profile == "commonArtifacts"


def test_artifact_discovery_does_not_treat_metagpt_class_view_as_data_model(
    tmp_path: Path,
) -> None:
    class_view = tmp_path / "resources/data_api_design/class_view.mmd"
    class_view.parent.mkdir(parents=True)
    class_view.write_text("class Order %% FR1", encoding="utf-8")
    (tmp_path / "schema.sql").write_text("-- FR1\ncreate table orders(id int);", encoding="utf-8")
    artifacts = collect_artifact_evidence(tmp_path)
    assert "resources/data_api_design/class_view.mmd" in artifacts["classDiagram"]
    assert artifacts["dataModel"] == ["schema.sql"]
    evidence = collect_requirement_evidence(tmp_path, ["FR1"], artifacts)
    assert evidence["FR1"]["design"] == [
        "resources/data_api_design/class_view.mmd",
        "schema.sql",
    ]


def _static_gate_subject(tmp_path: Path, artifact_evidence: dict[str, object]):
    data = _subject_data(tmp_path)
    data["artifactEvidence"] = artifact_evidence
    return load_subject_result(
        _write_json(tmp_path / "subject.json", data), run_directory=tmp_path
    )


def test_artifact_present_gate_requires_reported_file_to_exist(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text("terraform {}\n", encoding="utf-8")
    subject = _static_gate_subject(
        tmp_path, {"infrastructure": ["main.tf"], "container": ["Dockerfile"]}
    )

    present = run_artifact_present({"artifacts": ["infrastructure"]}, subject)
    absent = run_artifact_present({"artifacts": ["infrastructure", "container"]}, subject)

    assert present["status"] == "passed"
    assert absent["status"] == "failed"
    assert absent["missingArtifacts"] == ["container"]


def test_artifact_contains_gate_checks_region_and_forbidden_tokens(tmp_path: Path) -> None:
    terraform = 'provider "aws" {\n  region = "ap-northeast-2"\n}\nresource "aws_rds_cluster" "db" {}\n'
    (tmp_path / "main.tf").write_text(terraform, encoding="utf-8")
    subject = _static_gate_subject(tmp_path, {"infrastructure": ["main.tf"]})

    region = run_artifact_contains(
        {"artifact": "infrastructure", "anyOf": ["ap-northeast-2", "seoul"]}, subject
    )
    wrong_region = run_artifact_contains(
        {"artifact": "infrastructure", "anyOf": ["koreacentral"]}, subject
    )
    forbidden = run_artifact_contains(
        {"artifact": "infrastructure", "noneOf": ["aws_rds", "aws_eks"]}, subject
    )

    assert region["status"] == "passed"
    assert region["matchedTokens"] == ["ap-northeast-2"]
    assert wrong_region["status"] == "failed"
    assert wrong_region["missingTokens"] == ["koreacentral"]
    assert forbidden["status"] == "failed"
    assert forbidden["violatedTokens"] == ["aws_rds"]


def test_artifact_contains_gate_fails_when_artifact_was_never_produced(tmp_path: Path) -> None:
    subject = _static_gate_subject(tmp_path, {})

    result = run_artifact_contains(
        {"artifact": "infrastructure", "anyOf": ["ap-northeast-2"]}, subject
    )

    assert result["status"] == "failed"
    assert result["checkedFiles"] == []


def test_container_oracle_reports_missing_docker_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("evaluation.comparison.gates.shutil.which", lambda name: None)
    subject = _static_gate_subject(tmp_path, {"container": ["Dockerfile"]})

    result = run_container_http_oracle({"port": 8080}, subject, {"phases": []})

    assert result["status"] == "failed"
    assert result["phases"] == []
    assert "Docker" in result["reason"]


def _suite_with_case(tmp_path: Path, case: dict[str, object]):
    repository = Path(__file__).resolve().parents[1]
    return load_suite(
        _write_json(
            tmp_path / "suite.json",
            {
                "schemaVersion": "easydep-comparison-suite/v1",
                "suiteId": "gate-suite",
                "repetitions": 1,
                "outputRoot": str(tmp_path / "output"),
                "cases": [
                    case,
                    {
                        "id": "second-case",
                        "input": str(
                            repository
                            / "evaluation/easydep/requirements/inputs/dev_iot_monitoring.json"
                        ),
                    },
                ],
            },
        )
    )


def test_suite_generates_structural_gates_and_links_cloud_constraint(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    suite = _suite_with_case(
        tmp_path,
        {
            "id": "first-case",
            "input": str(
                repository / "evaluation/easydep/requirements/inputs/dev_checkout_gateway.json"
            ),
            "regionTokens": ["koreacentral"],
            "forbiddenTokens": ["aws_rds"],
        },
    )
    manifest = load_manifest(materialize_manifests(suite)[0])

    gate_ids = [gate.id for gate in manifest.gates]
    assert "iac-region" in gate_ids and "iac-forbidden" in gate_ids
    assert all(gate.required for gate in manifest.gates)
    assert manifest.constraints[0].verification_gates == (
        "iac-artifact",
        "iac-region",
        "iac-forbidden",
    )


def test_suite_omits_region_gate_when_case_declares_no_token(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    suite = _suite_with_case(
        tmp_path,
        {
            "id": "first-case",
            "input": str(
                repository / "evaluation/easydep/requirements/inputs/dev_checkout_gateway.json"
            ),
        },
    )
    manifest = load_manifest(materialize_manifests(suite)[0])

    assert "iac-region" not in [gate.id for gate in manifest.gates]
    assert manifest.constraints[0].verification_gates == ("iac-artifact",)


def test_gate_pack_wires_oracle_phases_to_requirements(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    pack = _write_json(
        tmp_path / "pack.json",
        {
            "schemaVersion": "easydep-comparison-gate-pack/v1",
            "apiContract": "GET /courses returns the catalog.",
            "gates": [
                {
                    "id": "business-api",
                    "kind": "containerHttpOracle",
                    "oraclePath": str(
                        repository
                        / "evaluation/baselines/course-registration-cases/business-oracle.json"
                    ),
                }
            ],
            "requirementGates": {"REQ-02": ["business-api#course-catalog"]},
        },
    )
    suite = _suite_with_case(
        tmp_path,
        {
            "id": "first-case",
            "input": str(
                repository
                / "evaluation/baselines/course-registration-cases/e1-course-registration-aws.json"
            ),
            "gatePack": str(pack),
        },
    )
    manifest = load_manifest(materialize_manifests(suite)[0])

    linked = {item.id: item.verification_gates for item in manifest.requirements}
    assert linked["REQ-02"] == ("business-api#course-catalog",)
    assert linked["REQ-01"] == ()
    assert manifest.prompt_protocol is not None
    assert "GET /courses" in manifest.prompt_protocol.api_contract


def test_gate_pack_rejects_unknown_requirement_id(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    pack = _write_json(
        tmp_path / "pack.json",
        {
            "schemaVersion": "easydep-comparison-gate-pack/v1",
            "gates": [{"id": "business-api", "kind": "artifactPresent", "artifacts": ["tests"]}],
            "requirementGates": {"REQ-99": ["business-api"]},
        },
    )
    suite = _suite_with_case(
        tmp_path,
        {
            "id": "first-case",
            "input": str(
                repository
                / "evaluation/baselines/course-registration-cases/e1-course-registration-aws.json"
            ),
            "gatePack": str(pack),
        },
    )

    with pytest.raises(ValueError, match="REQ-99"):
        materialize_manifests(suite)


def test_api_contract_reaches_every_arm_and_is_absent_by_default(tmp_path: Path) -> None:
    data = _manifest_data()
    data["promptProtocol"] = {
        "taskPreamble": "Build it.",
        "artifactContractPreamble": "Deliver these.",
        "artifactContract": [
            {"id": "tests", "title": "Tests", "description": "Automated tests."}
        ],
    }
    without = load_manifest(_write_json(tmp_path / "a.json", data))
    baseline = render_task_input(without)

    data["promptProtocol"]["apiContract"] = "GET /courses returns the catalog."
    with_contract = load_manifest(_write_json(tmp_path / "b.json", data))

    assert "API contract" not in baseline
    task = render_task_input(with_contract)
    assert "API contract:" in task and "GET /courses" in task
    for arm in with_contract.arms:
        assert "GET /courses" in render_arm_prompt(with_contract, arm)


def _collect_report(workspace: Path, evidence: dict[str, object], arm: str = "easydep"):
    return {
        "experimentId": "collect-test",
        "promptProtocol": {
            "artifactContract": [
                {"id": "classDiagram", "title": "Class diagram", "description": "d"},
                {"id": "infrastructure", "title": "IaC", "description": "d"},
            ]
        },
        "runs": [
            {
                "armId": arm,
                "framework": arm,
                "repetition": 1,
                "workspace": str(workspace),
                "artifactEvidence": evidence,
            }
        ],
    }


def test_collect_groups_files_by_arm_then_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "design").mkdir(parents=True)
    (workspace / "design/class-diagram.puml").write_text("@startuml", encoding="utf-8")
    (workspace / "deployment/tofu").mkdir(parents=True)
    (workspace / "deployment/tofu/main.tf").write_text("terraform {}", encoding="utf-8")
    report = _collect_report(
        workspace,
        {
            "classDiagram": ["design/class-diagram.puml"],
            "infrastructure": ["deployment/tofu/main.tf"],
        },
    )

    root = collect_artifacts(report, tmp_path / "out")

    assert (root / "easydep/classDiagram/design/class-diagram.puml").is_file()
    assert (root / "easydep/infrastructure/deployment/tofu/main.tf").is_file()
    assert (root / "easydep/classDiagram/design/class-diagram.puml").read_text(
        encoding="utf-8"
    ) == "@startuml"


def test_collect_marks_artifact_the_arm_never_produced(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "c.puml").write_text("@startuml", encoding="utf-8")
    report = _collect_report(workspace, {"classDiagram": ["c.puml"]})

    root = collect_artifacts(report, tmp_path / "out")
    index = (root / "INDEX.md").read_text(encoding="utf-8")

    assert not (root / "easydep/infrastructure").exists()
    assert "| infrastructure | - |" in index
    assert "| classDiagram | 1 |" in index


def test_collect_skips_evidence_whose_file_is_gone(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    report = _collect_report(workspace, {"classDiagram": ["missing.puml"]})

    root = collect_artifacts(report, tmp_path / "out")

    assert not (root / "easydep/classDiagram").exists()
    assert "| classDiagram | - |" in (root / "INDEX.md").read_text(encoding="utf-8")
