"""배포 에이전트 — 클라우드 지식베이스와 배포 계획 구성기.

2026-07-25에 agent-sdk 저장소를 이력째 흡수했다. 안의 패키지들(`*kb`·`kbcommon`·
`nim_agent`)은 그 저장소의 구조를 그대로 유지한다 — `kbcommon.artifact`의
`REPO_ROOT`가 이 디렉터리를 가리키고 `data/`·`output/`이 그 아래에 있다.

임포트 규약은 `tests/test_architecture.py`가 AST로 지킨다.
"""
