$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $repositoryRoot "app/implementation/tools"
$gradleWrapper = Join-Path $toolRoot "gradle/gradlew.bat"
$env:GRADLE_USER_HOME = Join-Path $repositoryRoot ".easydep/gradle-cache"

foreach ($command in @("node", "npm", "java")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is not installed: $command"
    }
}

& $gradleWrapper --version --no-daemon
if ($LASTEXITCODE -ne 0) { throw "Gradle Wrapper bootstrap failed" }
Write-Output "EasyDep implementation tools are ready."
