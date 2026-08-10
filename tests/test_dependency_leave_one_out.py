from evaluation.research_protocol.commands.run_dependency_leave_one_out import run


def test_every_grounded_fixture_edge_is_detected_independently():
    result = run()

    assert result["baselineExpectedReferenceCount"] == 31
    assert result["interventionCount"] == 31
    assert result["detectedCount"] == 31
    assert result["nonTargetChangeCount"] == 0
