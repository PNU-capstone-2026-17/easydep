"""Round-trip check for the MySQL artifact store.

Run after filling DB_* in .env:
    python verify_db.py
"""

from __future__ import annotations

from app.db.models import ORIGIN_FEEDBACK_REVISED, ORIGIN_GENERATED
from app.db.session import database_settings, init_db
from app.repositories import artifact_repository


def main() -> None:
    settings = database_settings()
    print(
        f"connecting to mysql://{settings['user']}@{settings['host']}:"
        f"{settings['port']}/{settings['name']}"
    )
    init_db()
    print("schema ready")

    app_id = artifact_repository.create_app(
        scenario_text='{"UseCaseName": "Login"}',
    )
    print("app_id:", app_id)

    artifact_repository.save_stage(
        app_id,
        "class_diagram",
        {
            "class_diagram_puml": "@startuml\nclass LoginBoundary\n@enduml",
            "class_diagram_syntax_valid": True,
            "class_diagram_syntax_errors": [],
        },
        origin=ORIGIN_GENERATED,
    )
    artifact_repository.save_stage(
        app_id,
        "class_diagram",
        {
            "class_diagram_puml": "@startuml\nclass LoginBoundary\nclass LoginControl\n@enduml",
            "class_diagram_syntax_valid": True,
            "class_diagram_syntax_errors": [],
        },
        origin=ORIGIN_FEEDBACK_REVISED,
    )
    artifact_repository.save_stage(
        app_id,
        "api_spec",
        {
            "api_spec": {"openapi": "3.0.0", "paths": {"/login": {}}},
            "api_spec_syntax_valid": True,
            "api_spec_syntax_errors": [],
        },
    )

    state = artifact_repository.load_state(app_id)
    print("scenario_text:", state["scenario_text"])
    print("class_diagram_puml:", state["class_diagram_puml"].replace("\n", " | "))
    print("api_spec:", state["api_spec"])
    print("artifact_status:", state["artifact_status"])
    print("class diagram history:")
    for version in artifact_repository.list_versions(app_id, "class_diagram"):
        print("   ", version)

    assert "LoginControl" in state["class_diagram_puml"], "latest version not current"
    assert state["api_spec"]["openapi"] == "3.0.0", "api spec did not round trip"
    print("\nOK: artifacts saved and reloaded by app_id")


if __name__ == "__main__":
    main()
