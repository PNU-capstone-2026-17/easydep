from fastapi import Response, status

import server


def test_api_health_is_ready_only_during_the_application_lifespan() -> None:
    previous = getattr(server.app.state, "ready", None)
    try:
        server.app.state.ready = False
        stopping_response = Response()

        assert server.health(stopping_response) == {"ok": False}
        assert stopping_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

        server.app.state.ready = True
        ready_response = Response()

        assert server.health(ready_response) == {"ok": True}
        assert ready_response.status_code == status.HTTP_200_OK
    finally:
        if previous is None:
            delattr(server.app.state, "ready")
        else:
            server.app.state.ready = previous
