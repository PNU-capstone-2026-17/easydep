from app.testing.schemas.testing_state import TestingState



def dynamic_functional_node(state: TestingState) -> dict:
    """
    Placeholder for dynamic functional (acceptance) testing.
    """
    report = {
        "status": "SKIPPED",
        "message": "Dynamic functional testing is not yet implemented."
    }
    return {"current_node": "dynamic_functional", "dynamic_functional_report": report}

def dynamic_nfr_node(state: TestingState) -> dict:
    """
    Placeholder for dynamic non-functional (load/stress) testing.
    """
    report = {
        "status": "SKIPPED",
        "message": "Dynamic NFR testing is not yet implemented."
    }
    return {"current_node": "dynamic_nfr", "dynamic_nfr_report": report}
