#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export GRADLE_USER_HOME="$repository_root/.easydep/gradle-cache"

for command in node npm java gradle tofu trivy; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "Required command is not installed: $command" >&2
    exit 1
  }
done

# 공용 툴체인에는 Gradle 자체가 들어 있다. wrapper 배포본을 이미지 빌드 중 다시 받아
# 저장하지 않고, 설치된 고정 버전만 확인한다.
gradle --version --no-daemon
tofu version
trivy --version
echo "EasyDep implementation tools are ready."
