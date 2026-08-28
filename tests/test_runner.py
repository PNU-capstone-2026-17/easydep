"""러너 테스트 (파이프라인 배선 + 아티팩트 저장).

1. run_pipeline — 6개 스테이지를 목킹해 호출 순서/상태 병합 검증(LLM 없음).
2. persist_run — tmp_path에 실제 파일을 써서 산출물 구조/manifest 검증(순수 IO).
3. load_input — inputs/*.json 로드.
"""

import json

from app.requirements.orchestration import runner


# ---------------------------------------------------------------------------
# 1. run_pipeline — 스테이지 배선
# ---------------------------------------------------------------------------
def test_run_pipeline_calls_stages_in_order(monkeypatch):
    calls = []
    handoff_checks = []

    def stage(name, key, value=None):
        def fn(state):
            calls.append(name)
            return {key: f"<{name}>" if value is None else value, "phase": name}

        return fn

    # 감독자(되돌아가기)가 읽는 세 키는 **실제 모양**이어야 한다. 여기 문자열을 넣으면
    # 그건 배선 오류이고, 감독자가 방어할 일이 아니다(결함 0건 = 되돌리지 않음).
    empty_specs = []
    empty_rel = {}
    empty_review = {"issues": [], "semantic_status": "ok", "unexamined_rules": []}

    monkeypatch.setattr(runner, "identify_actors", stage("actors", "actors"))
    monkeypatch.setattr(
        runner,
        "analyze_cloud_inputs",
        stage(
            "cloud_inputs",
            "deployment_needs",
            {},
        ),
    )
    monkeypatch.setattr(
        runner, "build_resource_spec", stage("resource_spec", "resource_intake", {"valid": False})
    )
    monkeypatch.setattr(runner, "identify_use_cases", stage("use_cases", "use_cases"))
    monkeypatch.setattr(runner, "review_model", stage("review_model", "model_review", empty_review))
    monkeypatch.setattr(runner, "check_coverage", stage("coverage", "coverage"))
    monkeypatch.setattr(runner, "generate_specs", stage("specs", "use_case_specs", empty_specs))
    monkeypatch.setattr(runner, "check_specs", stage("check_specs", "spec_report"))
    monkeypatch.setattr(runner, "identify_relationships", stage("rel", "relationships", empty_rel))
    monkeypatch.setattr(runner, "check_relationships", stage("check_rel", "relationship_report"))
    monkeypatch.setattr(runner, "render_diagram", stage("diagram", "diagram"))
    monkeypatch.setattr(
        runner.supervisor,
        "blocking_issues",
        lambda state: handoff_checks.append(state) or [],
    )

    state = runner.run_pipeline([{"id": "R1", "text": "x", "type": "FR"}])

    assert calls == [
        "cloud_inputs",
        "resource_spec",
        "actors",
        "use_cases",
        "review_model",
        "coverage",
        "specs",
        "check_specs",
        "rel",
        "check_rel",
        "diagram",
    ]
    assert state["classified"][0]["id"] == "R1"  # 원본 입력 유지
    assert state["actors"] == "<actors>"
    assert state["diagram"] == "<diagram>"
    assert handoff_checks == [state]


def test_batch_runner_goes_back_when_a_stage_could_not_repair_itself(monkeypatch):
    """배치 경로에도 되돌아가기가 있어야 한다 — **평가 세트가 재는 실행이 이 배치**다.

    그래프는 조건부 엣지로 되돌리고 러너는 함수를 직접 부르므로, 같은 판단을 러너에서도
    돌린다. 여기에 없으면 C2의 효과가 측정에 잡히지 않는다.
    """
    from app.requirements.knowledge import rules
    from app.requirements.orchestration import supervisor

    monkeypatch.setattr(supervisor.settings, "max_redo_rounds", 1)
    issue = f"[semantic] fix {rules.tag_of('spec.remerge-re-establishes-state')}"
    passes = {"specs": 0}

    def fake_specs(state):
        passes["specs"] += 1
        # 1회차엔 스스로 못 고친 결함이 남고, 되돌린 뒤(2회차)엔 깨끗하다.
        first = passes["specs"] == 1
        return {
            "use_case_specs": [
                {
                    "use_case_id": "UC1",
                    "issues": [issue] if first else [],
                    "repair_stopped": "no_improvement" if first else "clean",
                }
            ]
        }

    def noop(key, value):
        return lambda state: {key: value}

    monkeypatch.setattr(runner, "identify_actors", noop("actors", []))
    monkeypatch.setattr(runner, "analyze_cloud_inputs", noop("deployment_needs", {}))
    monkeypatch.setattr(runner, "build_resource_spec", noop("resource_intake", {"valid": False}))
    monkeypatch.setattr(runner, "identify_use_cases", noop("use_cases", []))
    monkeypatch.setattr(runner, "review_model", noop("model_review", {"issues": []}))
    monkeypatch.setattr(runner, "check_coverage", noop("coverage", {}))
    monkeypatch.setattr(runner, "generate_specs", fake_specs)
    monkeypatch.setattr(runner, "check_specs", noop("spec_report", {}))
    monkeypatch.setattr(runner, "identify_relationships", noop("relationships", {}))
    monkeypatch.setattr(runner, "check_relationships", noop("relationship_report", {}))
    monkeypatch.setattr(runner, "render_diagram", noop("diagram", ""))

    state = runner.run_pipeline([{"id": "FR1", "text": "x", "type": "FR"}])

    assert passes["specs"] == 2  # 되돌아가서 다시 만들었다
    assert state["redo_rounds"] == 1
    entry = state["redo_history"][0]
    assert entry["owner"] == "use_cases"  # specs가 포기했으니 그 위로
    assert entry["escalated"] is True
    # 되돌린 지점부터 끝까지 다시 돌았다(집계 노드 포함).
    assert entry["rerun"][0] == "identify_use_cases"
    assert entry["rerun"][-1] == "render_diagram"
    assert state["stage_feedback"] == {}  # 낡은 지시는 남지 않는다


# ---------------------------------------------------------------------------
# 2. persist_run — 산출물 구조 & manifest
# ---------------------------------------------------------------------------
def _sample_state():
    return {
        "actors": [{"name": "User", "kind": "primary", "description": "d"}],
        "use_cases": [
            {
                "id": "UC1",
                "name": "Log in",
                "primary_actor": "User",
                "requirement_ids": ["R1"],
                "nfr_ids": [],
            },
            {
                "id": "UC2",
                "name": "Place order",
                "primary_actor": "User",
                "requirement_ids": ["R2"],
                "nfr_ids": ["N1"],
            },
        ],
        "coverage": {"fr_total": 2, "orphan_fr_ids": [], "coverage_ratio": 1.0},
        "use_case_specs": [
            {"use_case_id": "UC1", "name": "Log in", "main_scenario": [], "issues": []},
            {
                "use_case_id": "UC2",
                "name": "Place order",
                "main_scenario": [],
                "issues": ["2a: branch_step 9가 주 시나리오에 없음"],
            },
        ],
        "relationships": {
            "associations": [{"actor": "User", "use_case": "Log in"}],
            "includes": [],
            "extends": [],
            "generalizations": [],
            "derived_use_cases": [],
        },
        "diagram": "@startuml\n@enduml",
    }


def test_persist_run_writes_expected_tree(tmp_path):
    input_obj = {"name": "demo", "classified": [{"id": "R1", "text": "x", "type": "FR"}]}
    run_dir = runner.persist_run(
        input_obj,
        _sample_state(),
        dataset_name="demo",
        artifact_root=tmp_path,
        run_metrics={"llm_calls": 9, "prompt_tokens": 100},
    )

    # 최상위 산출물
    for f in (
        "input.json",
        "manifest.json",
        "actors.json",
        "use_cases.json",
        "coverage.json",
        "deployment_needs.json",
        "resource_spec.json",
        "resource_intake.json",
        "traceability.json",
        "relationships.json",
        "diagram.puml",
    ):
        assert (run_dir / f).exists(), f"{f} 누락"

    # UC별 디렉토리 + spec
    assert (run_dir / "use_cases" / "uc_01_log_in" / "use_case.json").exists()
    assert (run_dir / "use_cases" / "uc_01_log_in" / "spec.json").exists()
    assert (run_dir / "use_cases" / "uc_02_place_order" / "spec.json").exists()

    # run_id / 디렉토리명 규칙
    assert run_dir.name.startswith("easydep-full-demo-") and run_dir.parent == tmp_path

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"] == "demo"
    assert manifest["metrics"]["llm_calls"] == 9
    assert manifest["config"]["max_repair_iters"] >= 0
    assert len(manifest["input_sha256"]) == 64
    assert manifest["runId"] == run_dir.name == manifest["run_id"]
    assert manifest["system"] == "easydep"
    assert manifest["variant"] == "full"
    assert manifest["caseId"] == "demo"
    assert manifest["purpose"] == "normal"
    assert manifest["completedStages"] == ["requirements"]
    # 요약: 개수 + 위반 있는 UC만 노출 + 관계 카운트
    summ = manifest["summary"]
    assert summ["n_actors"] == 1 and summ["n_use_cases"] == 2 and summ["n_specs"] == 2
    assert summ["coverage"]["coverage_ratio"] == 1.0
    assert list(summ["spec_issues"].keys()) == ["UC2"]  # UC1은 이슈 없어 제외
    assert summ["relationships"]["associations"] == 1
    # config 스냅샷에 api_key는 없어야 함
    assert "api_key" not in manifest["config"]
    assert (run_dir / "diagram.puml").read_text(encoding="utf-8") == "@startuml\n@enduml"


def test_persist_run_input_sha_is_deterministic(tmp_path):
    input_obj = {"name": "demo", "classified": [{"id": "R1", "text": "x", "type": "FR"}]}
    d1 = runner.persist_run(input_obj, _sample_state(), artifact_root=tmp_path / "a")
    d2 = runner.persist_run(input_obj, _sample_state(), artifact_root=tmp_path / "b")
    sha1 = json.loads((d1 / "manifest.json").read_text(encoding="utf-8"))["input_sha256"]
    sha2 = json.loads((d2 / "manifest.json").read_text(encoding="utf-8"))["input_sha256"]
    assert sha1 == sha2  # 같은 입력 → 같은 해시


def test_load_state_restores_cloud_requirement_artifacts(tmp_path):
    input_obj = {"name": "demo", "classified": [{"id": "R1", "text": "x", "type": "FR"}]}
    state = _sample_state() | {
        "deployment_needs": {"https_ingress": {"requirementIds": ["R1"]}},
        "resource_spec": {"schemaVersion": "3", "workloads": ["vm"]},
        "resource_intake": {"valid": True, "questions": []},
    }
    run_dir = runner.persist_run(input_obj, state, artifact_root=tmp_path)

    restored = runner.load_state(run_dir)

    assert restored["deployment_needs"] == state["deployment_needs"]
    assert restored["resource_spec"] == state["resource_spec"]
    assert restored["resource_intake"] == state["resource_intake"]
    assert restored["traceability"]["requirements"]["R1"]["deployment_needs"] == ["https_ingress"]


def test_load_state_preserves_an_explicit_global_constraint(tmp_path):
    input_obj = {
        "name": "demo",
        "classified": [
            {"id": "R1", "text": "The service preserves audit records.", "type": "NFR"}
        ],
    }
    state = _sample_state() | {
        "classified": input_obj["classified"],
        "constraint_applicability": {"R1": []},
    }
    run_dir = runner.persist_run(input_obj, state, artifact_root=tmp_path)

    restored = runner.load_state(run_dir)

    assert restored["constraint_applicability"] == {"R1": []}


# ---------------------------------------------------------------------------
# 3. load_input
# ---------------------------------------------------------------------------
def test_load_input_by_name():
    obj = runner.load_input("shopping_mall")
    assert obj["name"] == "shopping_mall"
    assert any(r["id"] == "R1" for r in obj["classified"])
