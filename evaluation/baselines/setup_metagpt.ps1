$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venvPath = Join-Path $repoRoot ".venv-metagpt"

$python311 = $null
try {
    $python311 = (py -3.11 -c "import sys; print(sys.executable)" 2>$null).Trim()
} catch {
    $userPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path $userPython) {
        $python311 = $userPython
    }
}
if (-not $python311) {
    throw "Python 3.11 is required. Install it, then run this script again."
}

if (-not (Test-Path (Join-Path $venvPath "Scripts\python.exe"))) {
    & $python311 -m venv $venvPath
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install "uv==0.12.1"

$uv = Join-Path $venvPath "Scripts\uv.exe"
$runtimeRequirements = Join-Path $PSScriptRoot "requirements-metagpt-runtime.txt"

# MetaGPT 0.8.2의 전체 메타데이터를 한 번에 풀면 오래된 광범위 제약 때문에 resolver가
# 장시간 backtracking한다. 본체와 Software Company 경로의 직접 의존성을 나눠 설치한다.
& $uv pip install --python $venvPython --no-deps "metagpt==0.8.2"
& $uv pip install --python $venvPython --requirement $runtimeRequirements
$metagpt = Join-Path $venvPath "Scripts\metagpt.exe"
Push-Location $venvPath
try {
    & $metagpt --help | Out-Null
} finally {
    Pop-Location
}
Write-Output "MetaGPT 0.8.2 CLI is ready: $venvPath"
