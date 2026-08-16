from app.testing.schemas.testing_state import TestingState


def dynamic_nfr_node(state: TestingState) -> dict:
    """
    Placeholder for dynamic non-functional (load/stress) testing.
    """
    report = {
        "status": "SKIPPED",
        "message": "Dynamic NFR testing is not yet implemented."
    }
    return {"current_node": "dynamic_nfr", "dynamic_nfr_report": report}
