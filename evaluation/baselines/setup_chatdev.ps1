$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venvPath = Join-Path $repoRoot ".venv-chatdev"
$sourcePath = Join-Path $venvPath "source"
$revision = "bcab15717940818938402394a04aea2052d76665"

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

if (-not (Test-Path (Join-Path $sourcePath ".git"))) {
    git clone --filter=blob:none --no-checkout https://github.com/OpenBMB/ChatDev.git $sourcePath
}
git -C $sourcePath fetch --depth 1 origin $revision
git -C $sourcePath checkout --detach $revision
$actualRevision = (git -C $sourcePath rev-parse HEAD).Trim()
if ($actualRevision -ne $revision) {
    throw "ChatDev revision mismatch: expected $revision, got $actualRevision"
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install "uv==0.12.1"
$uv = Join-Path $venvPath "Scripts\uv.exe"
& $uv pip install --python $venvPython --no-cache --requirement (Join-Path $sourcePath "requirements.txt")
# ChatDev 1.1.6 pins openai 1.3.3 but did not constrain its indirect httpx
# dependency. httpx 0.28 removed the `proxies` argument used by that client.
& $uv pip install --python $venvPython --no-cache "httpx==0.27.2"
if ($LASTEXITCODE -ne 0) {
    throw "ChatDev dependency installation failed."
}

$env:OPENAI_API_KEY = "setup-validation-only"
Push-Location $sourcePath
try {
    & $venvPython -c "from chatdev.chat_chain import ChatChain; print('ChatDev import ready')"
    if ($LASTEXITCODE -ne 0) {
        throw "ChatDev import validation failed."
    }
    & $venvPython run.py --help | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "ChatDev CLI validation failed."
    }
} finally {
    Pop-Location
}
Write-Output "ChatDev 1.1.6 ($revision) is ready: $venvPath"
