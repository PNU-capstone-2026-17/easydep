from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.implementation.planning.design_context import (
    _materialize_integration_semantic_evidence,
)


def test_integration_semantic_evidence_projects_context_values_and_api_bindings(
    tmp_path: Path,
) -> None:
    use_cases = tmp_path / "use-cases.json"
    api_model = tmp_path / "api-model.json"
    use_cases.write_text(
        json.dumps(
            {
                "use_case_specs": [
                    {
                        "use_case_id": "UC-11",
                        "public_contract": {
                            "identity_obligations": [
                                {"obligation_ref": "identify-admin", "obligation": "identify"}
                            ],
                            "required_values": [
                                {
                                    "value_ref": "admin-id",
                                    "name": "administrator identifier",
                                    "source": "authenticated_actor_context",
                                    "identity_obligation_ref": "identify-admin",
                                }
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    api_model.write_text(
        json.dumps(
            {
                "Endpoints": [
                    {
                        "operation_id": "manageCourseOffering",
                        "method": "POST",
                        "path": "/offerings",
                        "use_case_ids": ["UC-11"],
                        "control_binding": {
                            "control": "CourseControl",
                            "method": "manage",
                            "arguments": [
                                {"name": "adminId", "source": "$context.adminId"}
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    spec = SimpleNamespace(inputs={"useCaseSpec": use_cases, "apiModel": api_model})
    (tmp_path / "reports" / "implementation-tasks").mkdir(parents=True)

    relative = _materialize_integration_semantic_evidence(spec, tmp_path, ["UC-11"])

    evidence = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
    assert evidence["publicContracts"] == [
        {
            "useCaseId": "UC-11",
            "identityObligations": [
                {"obligation_ref": "identify-admin", "obligation": "identify"}
            ],
            "requiredValues": [
                {
                    "value_ref": "admin-id",
                    "name": "administrator identifier",
                    "source": "authenticated_actor_context",
                    "identity_obligation_ref": "identify-admin",
                }
            ],
        }
    ]
    assert evidence["apiControlBindings"][0]["controlBinding"]["arguments"] == [
        {"name": "adminId", "source": "$context.adminId"}
    ]
