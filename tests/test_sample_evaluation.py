from app.core.orchestration.sample_evaluation import inspect_result


def test_inspection_reads_nested_design_artifacts():
    puml = (
        '@startuml\ncloud "AWS" {\nnode "vm" as resource_vm\n}\n'
        'node "Docker runtime"\nartifact "Application container"\n@enduml'
    )
    response = {
        "status": "completed",
        "result": {
            "requirements_result": {
                "status": "completed",
                "requirements": ["r"],
                "actors": [{"name": "user"}],
                "use_cases": [{"name": "do"}],
                "use_case_specs": [{"name": "do"}],
            },
            "design_result": {
                "artifacts": {
                    "class_diagram": "class",
                    "sequence_diagram": "sequence",
                    "api_spec": {"openapi": "3.0.0"},
                },
                "validation": {},
            },
            "cloud_design_result": {
                "logical_deployment_diagram_puml": "@startuml\n@enduml",
                "deployment_diagram_puml": puml,
                "kb_used": ["depkb"],
            },
        },
    }

    inspection = inspect_result(response)

    assert inspection["passed"] is True
    assert inspection["counts"]["use_case_specs"] == 1
