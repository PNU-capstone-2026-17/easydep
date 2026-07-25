"""배포 에이전트 테스트.

**이 파일이 있어야 하는 이유는 이름 공간이다.** easydep 테스트(`tests/`)에도
`test_cli.py`가 있어서, 이 디렉터리가 패키지가 아니면 pytest가 두 파일을 같은
모듈명 `test_cli`로 잡아 충돌한다. 패키지로 두면 여기 모듈은
`app.deployment.tests.*`가 되어 겹치지 않는다.

`--import-mode=importlib`로도 풀 수 있지만 그건 easydep 테스트의
`from conftest import ...`(기본 prepend 모드가 만들어 주는 경로)를 깨뜨린다.
"""
