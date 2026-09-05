[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$InstallRoot,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA "EasyDep\comparison\chatdev"
}
$venvPath = [System.IO.Path]::GetFullPath($InstallRoot)
$sourcePath = Join-Path $venvPath "source"
$revision = "bcab15717940818938402394a04aea2052d76665"

function Invoke-ChatDevImportValidation([string]$Python, [string]$Source) {
    $hadKey = Test-Path Env:OPENAI_API_KEY
    $priorKey = $env:OPENAI_API_KEY
    try {
        if (-not $hadKey) { $env:OPENAI_API_KEY = "setup-validation-only" }
        Push-Location $Source
        try {
            & $Python -c "from chatdev.chat_chain import ChatChain; print('ChatDev import ready')"
            if ($LASTEXITCODE -ne 0) { throw "ChatDev import validation failed." }
            & $Python run.py --help | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "ChatDev CLI validation failed." }
        } finally { Pop-Location }
    } finally {
        if ($hadKey) { $env:OPENAI_API_KEY = $priorKey } else { Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue }
    }
}

function Invoke-ChatDevResponseFieldPatch([string]$Python, [string]$Source) {
    $patchScript = Join-Path $PSScriptRoot "patch_chatdev_response_fields.py"
    & $Python -X utf8 $patchScript --source $Source
    if ($LASTEXITCODE -ne 0) { throw "ChatDev response-field compatibility patch failed." }
}
if (-not $PythonPath) {
    $PythonPath = (& (Join-Path $PSScriptRoot "ensure_python311.ps1") -InstallIfMissing $true | Select-Object -Last 1)
}
$python311 = [System.IO.Path]::GetFullPath($PythonPath)
$readyFile = Join-Path $venvPath ".easydep-ready-$revision-model-bridge-v1"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (-not $Force -and (Test-Path -LiteralPath $readyFile) -and (Test-Path -LiteralPath $venvPython)) {
    Invoke-ChatDevResponseFieldPatch $venvPython $sourcePath
    Invoke-ChatDevImportValidation $venvPython $sourcePath
    Write-Output $venvPath
    exit 0
}

if (-not (Test-Path (Join-Path $venvPath "Scripts\python.exe"))) {
    & $python311 -m venv $venvPath
}

if (-not (Test-Path (Join-Path $sourcePath ".git"))) {
    git clone --filter=blob:none --no-checkout https://github.com/OpenBMB/ChatDev.git $sourcePath
    if ($LASTEXITCODE -ne 0) { throw "ChatDev clone failed." }
}
git -C $sourcePath fetch --depth 1 origin $revision
if ($LASTEXITCODE -ne 0) { throw "ChatDev revision fetch failed." }
git -C $sourcePath checkout --detach $revision
if ($LASTEXITCODE -ne 0) { throw "ChatDev revision checkout failed." }
$actualRevision = (git -C $sourcePath rev-parse HEAD).Trim()
if ($actualRevision -ne $revision) {
    throw "ChatDev revision mismatch: expected $revision, got $actualRevision"
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "ChatDev venv pip upgrade failed." }
& $venvPython -m pip install "uv==0.12.1"
if ($LASTEXITCODE -ne 0) { throw "ChatDev uv installation failed." }
$uv = Join-Path $venvPath "Scripts\uv.exe"
& $uv pip install --python $venvPython --no-cache --requirement (Join-Path $sourcePath "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "ChatDev requirements installation failed." }
# ChatDev 1.1.6 pins openai 1.3.3 but did not constrain its indirect httpx
# dependency. httpx 0.28 removed the `proxies` argument used by that client.
& $uv pip install --python $venvPython --no-cache "httpx==0.27.2"
if ($LASTEXITCODE -ne 0) {
    throw "ChatDev dependency installation failed."
}

# The pinned ChatDev CLI exposes only a fixed GPT enum. Keep its workflow unchanged,
# but let the backend send the same OpenAI-compatible model used by all three arms.
$modelBackend = Join-Path $sourcePath "camel\model_backend.py"
$backendText = Get-Content -LiteralPath $modelBackend -Raw -Encoding UTF8
if ($backendText -notmatch 'EASYDEP_COMPARISON_MODEL_BRIDGE') {
    $backendText = $backendText.Replace(
        "OPENAI_API_KEY = os.environ['OPENAI_API_KEY']",
        "OPENAI_API_KEY = os.environ['OPENAI_API_KEY']`n# EASYDEP_COMPARISON_MODEL_BRIDGE`nREQUESTED_MODEL = os.environ.get('OPENAI_MODEL')"
    )
    $backendText = $backendText.Replace(
        'encoding = tiktoken.encoding_for_model(self.model_type.value)',
        'encoding = tiktoken.get_encoding("cl100k_base")'
    )
    $backendText = $backendText.Replace(
        'num_max_token = num_max_token_map[self.model_type.value]',
        'num_max_token = num_max_token_map.get(REQUESTED_MODEL or self.model_type.value, 16384)'
    )
    $backendText = $backendText.Replace(
        'model=self.model_type.value,',
        'model=REQUESTED_MODEL or self.model_type.value,'
    )
    Set-Content -LiteralPath $modelBackend -Value $backendText -Encoding UTF8
}
Invoke-ChatDevResponseFieldPatch $venvPython $sourcePath
Invoke-ChatDevImportValidation $venvPython $sourcePath
Set-Content -LiteralPath $readyFile -Encoding UTF8 -Value "ChatDev 1.1.6 $revision"
Write-Output $venvPath
