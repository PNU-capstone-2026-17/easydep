from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from evaluation.comparison.adapters.chatdev import parse_chatdev_usage
from evaluation.comparison.adapters.metagpt import parse_cost_manager, parse_metagpt_log
from evaluation.comparison.evaluate import evaluate_run
from evaluation.comparison.models import (
    SUBJECT_RESULT_SCHEMA,
    load_manifest,
    load_subject_result,
)
from evaluation.comparison.oracle import run_http_oracle
from evaluation.comparison.report import add_aggregates, render_markdown, write_reports
from evaluation.comparison.runner import run_experiment


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
