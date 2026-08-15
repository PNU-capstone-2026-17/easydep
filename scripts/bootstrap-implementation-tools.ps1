$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $repositoryRoot "app/implementation/tools"
$pumlRoot = Join-Path $toolRoot "puml2code-bce"
$gradleWrapper = Join-Path $toolRoot "gradle/gradlew.bat"
$env:GRADLE_USER_HOME = Join-Path $repositoryRoot ".easydep/gradle-cache"

foreach ($command in @("node", "npm", "java")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is not installed: $command"
    }
}

npm ci --omit=dev --ignore-scripts --prefix $pumlRoot
if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
node (Join-Path $pumlRoot "node_modules/pegjs/bin/pegjs") `
    --output (Join-Path $pumlRoot "src/parser/plantuml.js") `
    (Join-Path $pumlRoot "src/parser/plantuml.pegjs")
if ($LASTEXITCODE -ne 0) { throw "PlantUML parser generation failed" }

& $gradleWrapper --version --no-daemon
if ($LASTEXITCODE -ne 0) { throw "Gradle Wrapper bootstrap failed" }
Write-Output "EasyDep implementation tools are ready."
