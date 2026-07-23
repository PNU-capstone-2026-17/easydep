#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tool_root="$repository_root/app/implementation/tools"
generator_version="7.24.0"
generator_sha256="4b83ccc6fd43056c8c631cd0195e5100bd0550912502527bab09ac76152dab0c"
generator_dir="$tool_root/openapi-generator"
generator_jar="$generator_dir/openapi-generator-cli-$generator_version.jar"
puml_root="$tool_root/puml2code-bce"
export GRADLE_USER_HOME="$repository_root/.easydep/gradle-cache"

for command in node npm java curl; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command is not installed: $command" >&2; exit 1; }
done

mkdir -p "$generator_dir"
if [ ! -f "$generator_jar" ]; then
  curl --fail --location --retry 3 \
    "https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/$generator_version/openapi-generator-cli-$generator_version.jar" \
    --output "$generator_jar"
fi
if command -v sha256sum >/dev/null 2>&1; then
  printf '%s  %s\n' "$generator_sha256" "$generator_jar" | sha256sum --check --status
elif command -v shasum >/dev/null 2>&1; then
  [ "$(shasum -a 256 "$generator_jar" | awk '{print $1}')" = "$generator_sha256" ]
else
  echo "Required SHA-256 tool is not installed: sha256sum or shasum" >&2
  exit 1
fi

npm ci --omit=dev --ignore-scripts --prefix "$puml_root"
node "$puml_root/node_modules/pegjs/bin/pegjs" \
  --output "$puml_root/src/parser/plantuml.js" \
  "$puml_root/src/parser/plantuml.pegjs"
sh "$tool_root/gradle/gradlew" --version --no-daemon
echo "EasyDep implementation tools are ready."
