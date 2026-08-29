#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tool_root="$repository_root/app/implementation/tools"
export GRADLE_USER_HOME="$repository_root/.easydep/gradle-cache"

for command in node npm java; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command is not installed: $command" >&2; exit 1; }
done

sh "$tool_root/gradle/gradlew" --version --no-daemon
echo "EasyDep implementation tools are ready."
