"""러너 테스트 (파이프라인 배선 + 아티팩트 저장).

  1. run_pipeline — 6개 스테이지를 목킹해 호출 순서/상태 병합 검증(LLM 없음).
  2. persist_run — tmp_path에 실제 파일을 써서 산출물 구조/manifest 검증(순수 IO).
  3. load_input — inputs/*.json 로드.
"""
import json

from app import runner


# ---------------------------------------------------------------------------
# 1. run_pipeline — 스테이지 배선
# ---------------------------------------------------------------------------
def test_run_pipeline_calls_stages_in_order(monkeypatch):
    calls = []

    def stage(name, key):
        def fn(state):
            calls.append(name)
            return {key: f"<{name}>", "phase": name}
        return fn

    monkeypatch.setattr(runner, "identify_actors", stage("actors", "actors"))
    monkeypatch.setattr(runner, "identify_use_cases", stage("use_cases", "use_cases"))
    monkeypatch.setattr(runner, "check_coverage", stage("coverage", "coverage"))
    monkeypatch.setattr(runner, "generate_specs", stage("specs", "use_case_specs"))
    monkeypatch.setattr(runner, "check_specs", stage("check_specs", "spec_report"))
    monkeypatch.setattr(runner, "identify_relationships", stage("rel", "relationships"))
    monkeypatch.setattr(runner, "check_relationships", stage("check_rel", "relationship_report"))
    monkeypatch.setattr(runner, "render_diagram", stage("diagram", "diagram"))

    state = runner.run_pipeline([{"id": "R1", "text": "x", "type": "FR"}])

    assert calls == ["actors", "use_cases", "coverage", "specs", "check_specs",
                     "rel", "check_rel", "diagram"]
    assert state["classified"][0]["id"] == "R1"      # 원본 입력 유지
    assert state["actors"] == "<actors>"
    assert state["diagram"] == "<diagram>"


# ---------------------------------------------------------------------------
# 2. persist_run — 산출물 구조 & manifest
# ---------------------------------------------------------------------------
def _sample_state():
    return {
        "actors": [{"name": "User", "kind": "primary", "description": "d"}],
        "use_cases": [
            {"id": "UC1", "name": "Log in", "primary_actor": "User",
             "requirement_ids": ["R1"], "nfr_ids": []},
            {"id": "UC2", "name": "Place order", "primary_actor": "User",
             "requirement_ids": ["R2"], "nfr_ids": ["N1"]},
        ],
        "coverage": {"fr_total": 2, "orphan_fr_ids": [], "coverage_ratio": 1.0},
        "use_case_specs": [
            {"use_case_id": "UC1", "name": "Log in", "main_scenario": [], "issues": []},
            {"use_case_id": "UC2", "name": "Place order", "main_scenario": [],
             "issues": ["2a: branch_step 9가 주 시나리오에 없음"]},
        ],
        "relationships": {
            "associations": [{"actor": "User", "use_case": "Log in"}],
            "includes": [], "extends": [], "generalizations": [], "derived_use_cases": [],
        },
        "diagram": "@startuml\n@enduml",
    }


def test_persist_run_writes_expected_tree(tmp_path):
    input_obj = {"name": "demo", "classified": [{"id": "R1", "text": "x", "type": "FR"}]}
    run_dir = runner.persist_run(input_obj, _sample_state(), dataset_name="demo", artifact_root=tmp_path)

    # 최상위 산출물
    for f in ("input.json", "manifest.json", "actors.json", "use_cases.json",
              "coverage.json", "relationships.json", "diagram.puml"):
        assert (run_dir / f).exists(), f"{f} 누락"

    # UC별 디렉토리 + spec
    assert (run_dir / "use_cases" / "uc_01_log_in" / "use_case.json").exists()
    assert (run_dir / "use_cases" / "uc_01_log_in" / "spec.json").exists()
    assert (run_dir / "use_cases" / "uc_02_place_order" / "spec.json").exists()

    # run_id / 디렉토리명 규칙
    assert run_dir.name.startswith("run_") and run_dir.parent == tmp_path

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"] == "demo"
    assert len(manifest["input_sha256"]) == 64
    assert manifest["run_id"].endswith(manifest["input_sha256"][:10])
    # 요약: 개수 + 위반 있는 UC만 노출 + 관계 카운트
    summ = manifest["summary"]
    assert summ["n_actors"] == 1 and summ["n_use_cases"] == 2 and summ["n_specs"] == 2
    assert summ["coverage"]["coverage_ratio"] == 1.0
    assert list(summ["spec_issues"].keys()) == ["UC2"]     # UC1은 이슈 없어 제외
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


# ---------------------------------------------------------------------------
# 3. load_input
# ---------------------------------------------------------------------------
def test_load_input_by_name():
    obj = runner.load_input("shopping_mall")
    assert obj["name"] == "shopping_mall"
    assert any(r["id"] == "R1" for r in obj["classified"])
