from types import SimpleNamespace

from app.design.services.class_diagram.validation.collaboration import (
    CollaborationContext,
    _collaboration_contract,
)


def _context():
    groups = (
        SimpleNamespace(
            id="UC1:main:1", use_case_id="UC1", actor_step="s1",
            required_step_ids=("s1", "s2"),
        ),
        SimpleNamespace(
            id="UC1:main:2", use_case_id="UC1", actor_step="s3",
            required_step_ids=("s3",),
        ),
    )
    operations = [
        {"operationId": "b1", "stepRefs": ["s1"], "parameters": []},
        {"operationId": "c1", "stepRefs": ["s1", "s2"], "parameters": []},
        {"operationId": "c2", "stepRefs": ["s2"], "parameters": []},
        {"operationId": "b2", "stepRefs": ["s3"], "parameters": []},
        {"operationId": "c3", "stepRefs": ["s3"], "parameters": []},
    ]
    classes = [
        {"className": "B1", "stereotype": "boundary", "operations": [operations[0]]},
        {"className": "C1", "stereotype": "control", "operations": [operations[1], operations[2]]},
        {"className": "B2", "stereotype": "boundary", "operations": [operations[3]]},
        {"className": "C2", "stereotype": "control", "operations": [operations[4]]},
    ]
    return CollaborationContext(
        index=SimpleNamespace(groups=groups),
        model={"Classes": classes},
        use_case=SimpleNamespace(id="UC1"),
    )


def _calls(include_sibling=False):
    calls = [
        {"callId": "r1", "receiverOperationId": "b1", "stepRefs": ["s1"], "argumentBindings": []},
        {"callId": "c1", "parentCallId": "r1", "receiverOperationId": "c1", "stepRefs": ["s1", "s2"], "argumentBindings": []},
        {"callId": "r2", "receiverOperationId": "b2", "stepRefs": ["s3"], "argumentBindings": []},
        {"callId": "c3", "parentCallId": "r2", "receiverOperationId": "c3", "stepRefs": ["s3"], "argumentBindings": []},
    ]
    if include_sibling:
        calls.insert(2, {
            "callId": "c2", "parentCallId": "r1", "receiverOperationId": "c2",
            "stepRefs": ["s2"], "argumentBindings": [],
        })
    return calls


def test_actor_roots_have_one_direct_control_handoff_each():
    context = _context()
    valid = _collaboration_contract(
        {"collaborationId": "UC1", "calls": _calls()}, context
    )
    assert not any("exactly one Control" in item.message for item in valid)

    sibling = _collaboration_contract(
        {"collaborationId": "UC1", "calls": _calls(include_sibling=True)}, context
    )
    assert sum("exactly one Control" in item.message for item in sibling) == 1
