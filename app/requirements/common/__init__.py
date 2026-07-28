"""에이전트가 공유할 수 있는 기반 모듈.

지금은 `app/requirements/` 아래에 있지만 **여기 있는 것들은 요구사항 에이전트를
전혀 모른다.** 다른 에이전트가 쓰게 되면 `app/core/`로 옮기면 되고, 그때 바뀌는 것은
import 경로뿐이다. 그래서 이 패키지 안에서는 `app.requirements.*` 를 import 하지 않는다
— 그 규칙은 `tests/test_common_isolation.py` 가 지킨다.
"""
