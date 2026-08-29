"""Workspace 공개 HTTP 경로가 서버에 등록되는지 검증한다."""


def test_workspace_command_and_event_routes_are_registered() -> None:
    from server import app

    paths = {route.path for route in app.routes}
    assert "/api/workspace/apps" in paths
    assert "/api/workspace/apps/{app_id}" in paths
    assert "/api/workspace/apps/{app_id}/commands" in paths
    assert "/api/workspace/apps/{app_id}/events" in paths
    assert "/api/apps/{app_id}/design/retry" in paths
