$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $repositoryRoot "app/implementation/tools"
$generatorVersion = "7.24.0"
$generatorSha256 = "4B83CCC6FD43056C8C631CD0195E5100BD0550912502527BAB09AC76152DAB0C"
$generatorDir = Join-Path $toolRoot "openapi-generator"
$generatorJar = Join-Path $generatorDir "openapi-generator-cli-$generatorVersion.jar"
$pumlRoot = Join-Path $toolRoot "puml2code-bce"
$gradleWrapper = Join-Path $toolRoot "gradle/gradlew.bat"
$env:GRADLE_USER_HOME = Join-Path $repositoryRoot ".easydep/gradle-cache"

foreach ($command in @("node", "npm", "java")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is not installed: $command"
    }
}

New-Item -ItemType Directory -Path $generatorDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $generatorJar)) {
    $uri = "https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/$generatorVersion/openapi-generator-cli-$generatorVersion.jar"
    Invoke-WebRequest -Uri $uri -OutFile $generatorJar
}
if ((Get-FileHash -LiteralPath $generatorJar -Algorithm SHA256).Hash -ne $generatorSha256) {
    throw "OpenAPI Generator checksum mismatch"
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
