"""오케스트레이터가 시작한 멤버 Python 프로세스에만 적용되는 실행 훅."""

import os

from app.core.config import settings
from app.core.orchestration.docker_path_adapter import install as install_docker_paths

install_docker_paths()
if settings.easydep_fixed_linux_runner == "1":
    from app.core.orchestration.runner_compat import install as install_runner_compat

    install_runner_compat()
