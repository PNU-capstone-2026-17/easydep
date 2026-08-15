from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _source(path: str) -> str:
    return (FRONTEND / path).read_text(encoding="utf-8")


def test_each_workflow_screen_exposes_the_four_distinct_stage_tabs() -> None:
    routes = {
        "/requirements/": "요구사항 분석",
        "/design/": "시스템 설계",
        "/implementation/": "시스템 구현",
        "/testing/": "테스팅",
    }
    for page in ("index.html", "requirements/index.html", "design/index.html", "implementation/index.html", "testing/index.html"):
        source = _source(page)
        for route, label in routes.items():
            assert f'href="{route}"' in source
            assert label in source


def test_design_completion_action_continues_to_implementation() -> None:
    source = _source("design/index.html")

    assert 'nextButton.addEventListener("click", advanceToImplementation)' in source
    assert 'window.location.assign("/implementation/")' in source
    assert '"시스템 구현으로 이동"' in source


def test_implementation_and_testing_have_separate_ui_and_scripts() -> None:
    implementation = _source("implementation/index.html")
    testing = _source("testing/index.html")

    assert 'id="startTesting"' not in implementation
    assert 'src="/assets/implementation.js"' in implementation
    assert 'id="startTesting"' in testing
    assert 'src="/assets/testing.js"' in testing
