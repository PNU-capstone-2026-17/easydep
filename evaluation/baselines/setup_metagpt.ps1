[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$InstallRoot,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA "EasyDep\comparison\metagpt"
}
$venvPath = [System.IO.Path]::GetFullPath($InstallRoot)
if (-not $PythonPath) {
    $PythonPath = (& (Join-Path $PSScriptRoot "ensure_python311.ps1") -InstallIfMissing $true | Select-Object -Last 1)
}
$python311 = [System.IO.Path]::GetFullPath($PythonPath)
$readyFile = Join-Path $venvPath ".easydep-ready-0.8.2"
$metagpt = Join-Path $venvPath "Scripts\metagpt.exe"
if (-not $Force -and (Test-Path -LiteralPath $readyFile) -and (Test-Path -LiteralPath $metagpt)) {
    Push-Location $venvPath
    try { & $metagpt --help | Out-Null } finally { Pop-Location }
    if ($LASTEXITCODE -eq 0) {
        Write-Output $venvPath
        exit 0
    }
    Remove-Item -LiteralPath $readyFile -Force
}

if (-not (Test-Path (Join-Path $venvPath "Scripts\python.exe"))) {
    & $python311 -m venv $venvPath
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "MetaGPT venv pip upgrade failed." }
& $venvPython -m pip install "uv==0.12.1"
if ($LASTEXITCODE -ne 0) { throw "MetaGPT uv installation failed." }

$uv = Join-Path $venvPath "Scripts\uv.exe"
$runtimeRequirements = Join-Path $PSScriptRoot "requirements-metagpt-runtime.txt"

# MetaGPT 0.8.2의 전체 메타데이터를 한 번에 풀면 오래된 광범위 제약 때문에 resolver가
# 장시간 backtracking한다. 본체와 Software Company 경로의 직접 의존성을 나눠 설치한다.
& $uv pip install --python $venvPython --no-deps "metagpt==0.8.2"
if ($LASTEXITCODE -ne 0) { throw "MetaGPT package installation failed." }
& $uv pip install --python $venvPython --requirement $runtimeRequirements
if ($LASTEXITCODE -ne 0) { throw "MetaGPT runtime dependency installation failed." }
# This is the last lightweight AgentOps line compatible with MetaGPT 0.8.2's
# protobuf<5 provider stack. AgentOps 0.4.x now requires protobuf 5+.
& $uv pip install --python $venvPython "agentops==0.1.12"
if ($LASTEXITCODE -ne 0) { throw "MetaGPT agentops package installation failed." }
Push-Location $venvPath
try {
    & $metagpt --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "MetaGPT CLI validation failed." }
} finally {
    Pop-Location
}
Set-Content -LiteralPath $readyFile -Encoding UTF8 -Value "MetaGPT 0.8.2"
Write-Output $venvPath
