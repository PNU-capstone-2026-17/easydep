from app.design.rtm import build_design_rtm
from tests.design_validation_fixtures import CLEAN, CLEAN_STATE


def test_rtm_materializes_call_site_argument_provenance():
    state = {**CLEAN_STATE, "extracted_bce_classes": CLEAN}

    rows = {
        row["element"]: row
        for row in build_design_rtm(state)["rows"]
        if row["stage"] == "class_diagram"
    }

    call_id = "UC1::call:2"
    assert rows[call_id]["sources"]["class_operation"] == [
        "OrderController::placeOrder(request:String)"
    ]
    assert rows[f"{call_id}#request"]["sources"]["value_source"] == [
        "UC1::call:1#request"
    ]
