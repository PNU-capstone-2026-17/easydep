from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.core.orchestration.graph import build_orchestration_graph


class FakeRequirements:
    def __init__(self):
        self.starts = 0
        self.resumes = []

    def start(self, **_kwargs):
        self.starts += 1
        return {"status": "need_clarification", "phase": "clarify", "questions": ["Budget?"]}

    def resume(self, **kwargs):
        self.resumes.append(kwargs["answer"])
        return {"status": "completed", "use_case_specs": [{"id": "UC-1"}]}


class FakeDesign:
    def __init__(self):
        self.starts = 0
        self.resumes = []

    def start(self, **kwargs):
        self.starts += 1
        assert kwargs["requirements_result"]["use_case_specs"]
        return {"status": "need_feedback", "stage": "class_diagram"}

    def resume(self, **kwargs):
        self.resumes.append(kwargs["feedback"])
        if len(self.resumes) == 1:
            return {"status": "need_feedback", "stage": "deployment_diagram"}
        return {"status": "completed", "stage": None}


class FakeCloudDesign:
    def finalize(self, **_kwargs):
        return {"status": "completed", "dependency_plan": {}, "kb_used": ["depkb"]}


class FakeInfrastructure:
    def recommend(self, **_kwargs):
        return {"status": "provisional", "method": "llm_prompt_only"}


class FakeImplementation:
    def start(self, **_kwargs):
        return {"status": "needs_approval", "transmission_request": {"requestId": "r1"}}

    def resume(self, _result, *, approved):
        assert approved is True
        return {"status": "completed"}


def build_test_graph(requirements, design):
    return build_orchestration_graph(
        requirements=requirements,
        design=design,
        cloud_design=FakeCloudDesign(),
        infrastructure=FakeInfrastructure(),
        implementation=FakeImplementation(),
        checkpointer=MemorySaver(),
    )


def test_requirements_and_design_sessions_are_resumed_without_restarting():
    requirements = FakeRequirements()
    design = FakeDesign()
    graph = build_test_graph(requirements, design)
    config = {"configurable": {"thread_id": "run-1"}}
    initial = {
        "run_id": "run-1",
        "app_id": "app-1",
        "requirements_thread_id": "req-1",
        "requirements": ["Users can place orders."],
    }

    first = graph.invoke(initial, config)
    assert first["__interrupt__"][0].value["questions"] == ["Budget?"]

    second = graph.invoke(Command(resume="100 USD/month"), config)
    assert second["__interrupt__"][0].value["stage"] == "class_diagram"
    assert requirements.starts == 1
    assert requirements.resumes == ["100 USD/month"]
    assert design.starts == 1

    third = graph.invoke(Command(resume=""), config)
    assert third["__interrupt__"][0].value["stage"] == "deployment_diagram"
    assert design.starts == 1

    implementation_gate = graph.invoke(Command(resume=""), config)
    assert implementation_gate["__interrupt__"][0].value["stage"] == "implementation"

    transmission_gate = graph.invoke(Command(resume=True), config)
    assert transmission_gate["__interrupt__"][0].value["transmission_request"]

    final = graph.invoke(Command(resume=True), config)
    assert final["status"] == "completed"
    assert final["current_stage"] == "completed"
    assert design.resumes == ["", ""]


def test_completed_requirements_can_start_at_design():
    requirements = FakeRequirements()
    design = FakeDesign()
    graph = build_test_graph(requirements, design)
    config = {"configurable": {"thread_id": "design-only"}}
    result = graph.invoke(
        {
            "run_id": "design-only",
            "app_id": "app-1",
            "requirements_thread_id": "req-1",
            "requirements_result": {
                "status": "completed",
                "use_case_specs": [{"id": "UC-1"}],
            },
            "current_stage": "design",
        },
        config,
    )

    assert result["__interrupt__"][0].value["stage"] == "class_diagram"
    assert requirements.starts == 0
    assert design.starts == 1
