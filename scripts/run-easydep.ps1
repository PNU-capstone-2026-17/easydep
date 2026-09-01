[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$OpenBrowser,
    [switch]$SkipFrontendBuild,
    [switch]$ForceFrontendBuild,
    [switch]$SkipBootstrap,
    [switch]$ForceToolchainBuild,
    [switch]$ResetDatabase,
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [ValidateRange(1024, 65535)]
    [int]$DatabasePort = 33060,
    [string]$DatabaseImage = "mysql:8.4"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontendRoot = Join-Path $repoRoot "frontend"
$runRoot = Join-Path $repoRoot ".easydep\dev"
$pidPath = Join-Path $runRoot "server.json"
$stdoutPath = Join-Path $runRoot "server.stdout.log"
$stderrPath = Join-Path $runRoot "server.stderr.log"
$frontendBuildHashPath = Join-Path $runRoot "frontend-build.sha256"
$requirementsHashPath = Join-Path $runRoot "requirements.sha256"
$toolchainHashPath = Join-Path $runRoot "toolchain-build.sha256"
$environmentPath = Join-Path $repoRoot ".env"
$environmentExamplePath = Join-Path $repoRoot ".env.example"
$toolchainImage = "easydep-toolchain:local"
$memberGradleCacheVolume = "easydep-member-gradle-cache"
$databaseContainer = "easydep-mysql-dev"
$databaseVolume = "easydep-mysql-dev-data"
$databasePassword = "easydep-local"

New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

function Test-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Read-HashRecord {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8).Trim()
}

function Get-CombinedFileHash {
    param(
        [Parameter(Mandatory = $true)][System.IO.FileInfo[]]$Files,
        [string[]]$ExtraRecords = @()
    )
    $manifest = foreach ($file in @($Files | Sort-Object FullName -Unique)) {
        $relativePath = $file.FullName.Substring($repoRoot.Length).TrimStart("\", "/").Replace("\", "/")
        $fileHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        "$relativePath=$fileHash"
    }
    $manifest += $ExtraRecords
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($manifest -join "`n"))
        return ([System.BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-FrontendBuildHash {
    $files = @()
    foreach ($directoryName in @("src", "static")) {
        $directory = Join-Path $frontendRoot $directoryName
        if (Test-Path -LiteralPath $directory) {
            $files += Get-ChildItem -LiteralPath $directory -Recurse -File
        }
    }
    foreach ($fileName in @(
        "package.json",
        "package-lock.json",
        "svelte.config.js",
        "vite.config.ts",
        "tsconfig.json",
        "components.json"
    )) {
        $file = Join-Path $frontendRoot $fileName
        if (Test-Path -LiteralPath $file) {
            $files += Get-Item -LiteralPath $file
        }
    }
    $files += Get-ChildItem -LiteralPath $frontendRoot -File -Filter ".env*" -ErrorAction SilentlyContinue

    $manifest = foreach ($file in @($files | Sort-Object FullName -Unique)) {
        $relativePath = $file.FullName.Substring($frontendRoot.Length).TrimStart("\", "/").Replace("\", "/")
        $fileHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        "$relativePath=$fileHash"
    }
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($manifest -join "`n"))
        return ([System.BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
    }
}

function Read-ServerRecord {
    if (-not (Test-Path -LiteralPath $pidPath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $pidPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-MatchingProcess {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [int]$ToleranceSeconds = 2
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    $expected = [datetime]::Parse($StartedAt).ToUniversalTime()
    $actual = $process.StartTime.ToUniversalTime()
    if ([math]::Abs(($actual - $expected).TotalSeconds) -gt $ToleranceSeconds) {
        return $null
    }
    return $process
}

function Get-OwnedServerProcesses {
    $record = Read-ServerRecord
    if ($null -eq $record -or -not $record.pid -or -not $record.startedAt) {
        return @()
    }
    $owned = @()
    if ($record.listenerPid -and $record.listenerStartedAt) {
        $listener = Get-MatchingProcess `
            -ProcessId ([int]$record.listenerPid) `
            -StartedAt ([string]$record.listenerStartedAt)
        if ($null -ne $listener) {
            $owned += $listener
        }
    }
    elseif ($record.port) {
        # Backward-compatible recovery for records written before listenerPid existed.
        $expected = [datetime]::Parse([string]$record.startedAt).ToUniversalTime()
        $connections = Get-NetTCPConnection -State Listen -LocalPort ([int]$record.port) -ErrorAction SilentlyContinue
        foreach ($connection in $connections) {
            $candidate = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
            if ($null -ne $candidate -and [math]::Abs(($candidate.StartTime.ToUniversalTime() - $expected).TotalSeconds) -le 30) {
                $owned += $candidate
            }
        }
    }
    $launcher = Get-MatchingProcess `
        -ProcessId ([int]$record.pid) `
        -StartedAt ([string]$record.startedAt)
    if ($null -ne $launcher) {
        $owned += $launcher
    }
    return @($owned | Sort-Object Id -Unique)
}

function Stop-OwnedServer {
    foreach ($process in @(Get-OwnedServerProcesses)) {
        Write-Host "[EasyDep] Stopping backend process $($process.Id)."
        Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
        $process.WaitForExit(15000) | Out-Null
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE."
    }
}

function Test-DockerImage {
    param([Parameter(Mandatory = $true)][string]$Image)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker image inspect $Image *> $null
    $available = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $previousPreference
    return $available
}

function Initialize-PythonEnvironment {
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $createdEnvironment = $false
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Host "[EasyDep] Creating the Python virtual environment."
        if (Test-CommandAvailable "py") {
            & py -3 -m venv (Join-Path $repoRoot ".venv")
        }
        elseif (Test-CommandAvailable "python") {
            & python -m venv (Join-Path $repoRoot ".venv")
        }
        else {
            throw "Python 3.11 or newer is required."
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) {
            throw "Python virtual environment creation failed."
        }
        $createdEnvironment = $true
    }

    & $python -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
    if ($LASTEXITCODE -ne 0) {
        throw "EasyDep requires Python 3.11 or newer."
    }

    $requirementsPath = Join-Path $repoRoot "requirements.txt"
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
    if ($createdEnvironment -or $requirementsHash -ne (Read-HashRecord -Path $requirementsHashPath)) {
        Write-Host "[EasyDep] Installing Python packages from requirements.txt."
        & $python -X utf8 -m pip install -r $requirementsPath
        if ($LASTEXITCODE -ne 0) {
            throw "Python package installation failed."
        }
        Set-Content -LiteralPath $requirementsHashPath -Value $requirementsHash -Encoding UTF8
    }
    else {
        Write-Host "[EasyDep] Python dependencies are unchanged."
    }
}

function Initialize-Toolchain {
    $toolchainHash = Get-ToolchainBuildHash
    $imageExists = Test-DockerImage -Image $toolchainImage
    $recordedHash = Read-HashRecord -Path $toolchainHashPath
    if ($ForceToolchainBuild -or -not $imageExists -or $toolchainHash -ne $recordedHash) {
        Write-Host "[EasyDep] Building the shared implementation and testing toolchain."
        Invoke-Docker -Arguments @("build", "-t", $toolchainImage, $repoRoot)
        Invoke-Docker -Arguments @(
            "run", "--rm", "--entrypoint", "sh", $toolchainImage,
            "./scripts/bootstrap-implementation-tools.sh"
        )
        Set-Content -LiteralPath $toolchainHashPath -Value $toolchainHash -Encoding UTF8
    }
    else {
        Write-Host "[EasyDep] Toolchain inputs are unchanged; reusing $toolchainImage."
    }
}

function Repair-ToolchainCacheOwnership {
    # 예전 툴체인 컨테이너가 root로 만든 named volume을 새 appuser 컨테이너가 이어 쓰면
    # Gradle의 libnative-platform.so를 읽고 갱신하지 못한다. 서버를 시작할 때 쓰기 권한만
    # 가볍게 확인하고, 문제가 있는 기존 volume에 한해서 한 번 소유권을 바로잡는다.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker run --rm --entrypoint sh `
        -v "$memberGradleCacheVolume`:/tmp/easydep-gradle-cache" `
        $toolchainImage `
        -c "test -w /tmp/easydep-gradle-cache && { test ! -d /tmp/easydep-gradle-cache/native || test -w /tmp/easydep-gradle-cache/native; }" `
        *> $null
    $needsRepair = $LASTEXITCODE -ne 0
    $ErrorActionPreference = $previousPreference
    if (-not $needsRepair) {
        return
    }

    Write-Host "[EasyDep] Repairing ownership of the shared Gradle cache volume."
    Invoke-Docker -Arguments @(
        "run", "--rm", "--user", "root", "--entrypoint", "chown",
        "-v", "$memberGradleCacheVolume`:/tmp/easydep-gradle-cache",
        $toolchainImage,
        "-R", "1000:1000", "/tmp/easydep-gradle-cache"
    )
}

function Export-FrontendBuild {
    # 프론트엔드는 공용 이미지의 고정 Node/npm으로 이미 빌드됐다. 임시 컨테이너에서 결과만
    # 복사하므로 팀원 PC에 Node.js나 node_modules를 따로 만들 필요가 없다.
    $containerId = (& docker create $toolchainImage).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($containerId)) {
        throw "Could not create a temporary container for the frontend build."
    }
    $buildRoot = Join-Path $frontendRoot "build"
    try {
        if (Test-Path -LiteralPath $buildRoot) {
            Remove-Item -LiteralPath $buildRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
        & docker cp "$containerId`:/app/frontend/build/." $buildRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Could not copy the frontend build from $toolchainImage."
        }
    }
    finally {
        & docker rm -f $containerId *> $null
    }
}

function Wait-ForDatabase {
    $deadline = [datetime]::UtcNow.AddSeconds(180)
    do {
        $health = & docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $databaseContainer 2>$null
        if ($LASTEXITCODE -eq 0 -and ($health -eq "healthy" -or $health -eq "running")) {
            & docker exec -e "MYSQL_PWD=$databasePassword" $databaseContainer mysqladmin ping -h 127.0.0.1 -uroot --silent 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return
            }
        }
        Start-Sleep -Seconds 2
    } while ([datetime]::UtcNow -lt $deadline)
    throw "MySQL was not ready within 180 seconds. Run: docker logs $databaseContainer"
}

function Get-ToolchainBuildHash {
    # Dockerfile이 실제로 복사하는 입력만 추적한다. 거대한 BERT 조각은 manifest가 각 조각의
    # SHA-256을 이미 기록하므로 시작할 때마다 400MB를 다시 읽지 않는다. 새 이미지 빌드에서는
    # model_assets.py가 조각 자체를 검증하므로 손상된 파일이 이미지에 들어갈 수 없다.
    $files = @()
    foreach ($relativePath in @(
        ".dockerignore",
        "Dockerfile",
        "requirements.txt",
        "server.py",
        "scripts/bootstrap-implementation-tools.sh",
        "materials/BERT_FR_NFR_Classifier/bert_model/config.json",
        "materials/BERT_FR_NFR_Classifier/bert_model/tokenizer.json",
        "materials/BERT_FR_NFR_Classifier/bert_model/tokenizer_config.json",
        "materials/BERT_FR_NFR_Classifier/bert_model/training_args.bin",
        "materials/BERT_FR_NFR_Classifier/bert_model/weights/manifest.json"
    )) {
        $path = Join-Path $repoRoot $relativePath
        if (Test-Path -LiteralPath $path) {
            $files += Get-Item -LiteralPath $path
        }
    }
    $appRoot = Join-Path $repoRoot "app"
    $files += Get-ChildItem -LiteralPath $appRoot -Recurse -File | Where-Object {
        $relativePath = $_.FullName.Substring($appRoot.Length).Replace("\", "/")
        $relativePath -notmatch "/(__pycache__|\.cache|output|tests)/" -and
            $_.Extension -notin @(".pyc", ".pyo")
    }
    return Get-CombinedFileHash -Files $files -ExtraRecords @(
        "frontend=$(Get-FrontendBuildHash)"
    )
}

function Read-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Get-DatabaseHostPort {
    # ``docker port``는 컨테이너 상태나 Docker CLI 버전에 따라 빈 결과를 돌려줄 수 있다.
    # 생성 시 저장된 HostConfig를 읽으면 중지된 컨테이너도 같은 방식으로 확인할 수 있다.
    $raw = & docker inspect $databaseContainer 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($raw -join ""))) {
        return $null
    }
    try {
        $details = @($raw | ConvertFrom-Json)[0]
        $bindings = $details.HostConfig.PortBindings.'3306/tcp'
    }
    catch {
        return $null
    }
    if ($null -eq $bindings -or $bindings.Count -eq 0 -or -not $bindings[0].HostPort) {
        return $null
    }
    return [int]$bindings[0].HostPort
}

function Test-HttpEndpoint {
    param([Parameter(Mandatory = $true)][string]$Uri)
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

if ($Stop) {
    Stop-OwnedServer
    if (Test-CommandAvailable "docker") {
        $existing = & docker ps -a --filter "name=^/$databaseContainer$" --format "{{.Names}}" 2>$null
        if ($existing -eq $databaseContainer) {
            Write-Host "[EasyDep] Stopping MySQL. Its data volume is retained."
            Invoke-Docker -Arguments @("stop", $databaseContainer)
        }
    }
    Write-Host "[EasyDep] Stopped."
    exit 0
}

if (-not (Test-CommandAvailable "docker")) {
    throw "Docker Desktop and the docker CLI are required."
}
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& docker info *> $null
$ErrorActionPreference = $prevEAP
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running."
}

if ($ResetDatabase) {
    # 개발 DB 구조가 현재 코드와 맞지 않을 때만 사용한다. 컨테이너와 그 전용 volume을
    # 함께 지우므로, 기존 앱과 checkpoint도 모두 삭제된다는 사실을 출력해 둔다.
    Stop-OwnedServer
    $existingDatabase = & docker ps -a --filter "name=^/$databaseContainer$" --format "{{.Names}}"
    if ($existingDatabase -eq $databaseContainer) {
        Write-Host "[EasyDep] Removing the development MySQL container and all saved app data." -ForegroundColor Yellow
        Invoke-Docker -Arguments @("rm", "-f", $databaseContainer)
    }
    $existingVolume = & docker volume ls --filter "name=^$databaseVolume$" --format "{{.Name}}"
    if ($existingVolume -eq $databaseVolume) {
        Invoke-Docker -Arguments @("volume", "rm", $databaseVolume)
    }
}

if (-not (Test-Path -LiteralPath $environmentPath)) {
    Copy-Item -LiteralPath $environmentExamplePath -Destination $environmentPath
    Write-Host (
        "[EasyDep] Created .env from .env.example. " +
        "Set API_KEY, BASE_URL and MODEL before running an LLM stage."
    ) -ForegroundColor Yellow
}
$configuredToolchainImage = Read-DotEnvValue -Path $environmentPath -Name "EASYDEP_TOOLCHAIN_IMAGE"
if (-not [string]::IsNullOrWhiteSpace($configuredToolchainImage)) {
    $toolchainImage = $configuredToolchainImage
}

if ($SkipBootstrap) {
    Write-Host "[EasyDep] Skipping Python and toolchain bootstrap by explicit request."
    if (-not (Test-DockerImage -Image $toolchainImage)) {
        throw "-SkipBootstrap requires the existing Docker image: $toolchainImage"
    }
}
else {
    Initialize-PythonEnvironment
    Initialize-Toolchain
}
Repair-ToolchainCacheOwnership

if ($SkipFrontendBuild) {
    if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "build\index.html"))) {
        throw "-SkipFrontendBuild requires an existing frontend/build/index.html."
    }
    Write-Host "[EasyDep] Reusing the frontend build by explicit request."
}
else {
    $buildHash = Get-FrontendBuildHash
    $builtHash = Read-HashRecord -Path $frontendBuildHashPath
    $buildIndex = Join-Path $frontendRoot "build\index.html"
    if (-not $ForceFrontendBuild -and (Test-Path -LiteralPath $buildIndex) -and $buildHash -eq $builtHash) {
        Write-Host "[EasyDep] Frontend inputs are unchanged; reusing the existing build."
    }
    else {
        Write-Host "[EasyDep] Exporting the SvelteKit build from $toolchainImage."
        Export-FrontendBuild
        if (-not (Test-Path -LiteralPath $buildIndex)) {
            throw "The toolchain image did not contain frontend/build/index.html."
        }
        Set-Content -LiteralPath $frontendBuildHashPath -Value $buildHash -Encoding UTF8
    }
}

$existingDatabase = & docker ps -a --filter "name=^/$databaseContainer$" --format "{{.Names}}"
if ($existingDatabase -eq $databaseContainer) {
    $currentDatabasePort = Get-DatabaseHostPort
    if ($null -eq $currentDatabasePort) {
        throw (
            "The existing MySQL container port could not be read. " +
            "The container was left unchanged: $databaseContainer"
        )
    }
    if ($currentDatabasePort -ne $DatabasePort) {
        throw (
            "The existing MySQL container uses host port $currentDatabasePort, " +
            "not $DatabasePort. The container was left unchanged."
        )
    }
}

if ($existingDatabase -eq $databaseContainer) {
    $runningDatabase = & docker ps --filter "name=^/$databaseContainer$" --format "{{.Names}}"
    if ($runningDatabase -ne $databaseContainer) {
        Write-Host "[EasyDep] Restarting the existing MySQL container."
        Invoke-Docker -Arguments @("start", $databaseContainer)
    }
}
else {
    Write-Host "[EasyDep] Creating the development MySQL container."
    Invoke-Docker -Arguments @(
        "run", "-d",
        "--name", $databaseContainer,
        "--restart", "unless-stopped",
        "-e", "MYSQL_ROOT_PASSWORD=$databasePassword",
        "-e", "MYSQL_DATABASE=easydep",
        "-p", "127.0.0.1:$DatabasePort`:3306",
        "-v", "$databaseVolume`:/var/lib/mysql",
        "--health-cmd", "mysqladmin ping -h 127.0.0.1 -uroot -p$databasePassword --silent",
        "--health-interval", "5s",
        "--health-timeout", "5s",
        "--health-retries", "24",
        $DatabaseImage
    ) | Out-Null
}
Write-Host "[EasyDep] Waiting for MySQL."
Wait-ForDatabase

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw ".venv is missing. Create it and install requirements.txt first."
}

Stop-OwnedServer
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = [string]$DatabasePort
$env:DB_USER = "root"
$env:DB_PASSWORD = $databasePassword
$env:DB_NAME = "easydep"

Write-Host "[EasyDep] Starting the FastAPI backend. Logs: $runRoot"
$server = Start-Process `
    -FilePath $python `
    -ArgumentList @("-X", "utf8", "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", [string]$Port) `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

@{
    pid = $server.Id
    startedAt = $server.StartTime.ToUniversalTime().ToString("O")
    repoRoot = $repoRoot
    port = $Port
} | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8

$healthUri = "http://127.0.0.1:$Port/api/health"
$deadline = [datetime]::UtcNow.AddSeconds(600)
do {
    $server.Refresh()
    if ($server.HasExited) {
        $tail = if (Test-Path -LiteralPath $stderrPath) {
            (Get-Content -LiteralPath $stderrPath -Tail 80) -join [Environment]::NewLine
        } else {
            "No backend error log is available."
        }
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        throw "The backend exited before becoming ready.`n$tail"
    }
    if (Test-HttpEndpoint $healthUri) {
        break
    }
    Start-Sleep -Seconds 2
} while ([datetime]::UtcNow -lt $deadline)

if (-not (Test-HttpEndpoint $healthUri)) {
    Stop-OwnedServer
    throw "The backend was not ready within 600 seconds. See $stderrPath"
}

$listenerConnection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
    Where-Object { $_.LocalAddress -eq "127.0.0.1" } |
    Select-Object -First 1
if ($null -eq $listenerConnection) {
    Stop-OwnedServer
    throw "The backend is healthy but its listener process could not be identified."
}
$listenerProcess = Get-Process -Id $listenerConnection.OwningProcess -ErrorAction Stop
@{
    pid = $server.Id
    startedAt = $server.StartTime.ToUniversalTime().ToString("O")
    listenerPid = $listenerProcess.Id
    listenerStartedAt = $listenerProcess.StartTime.ToUniversalTime().ToString("O")
    repoRoot = $repoRoot
    port = $Port
} | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8

$checks = @(
    "http://127.0.0.1:$Port/",
    "http://127.0.0.1:$Port/workspace/",
    "http://127.0.0.1:$Port/api/workspace/apps?limit=1"
)
foreach ($uri in $checks) {
    if (-not (Test-HttpEndpoint $uri)) {
        Stop-OwnedServer
        throw "End-to-end integration check failed: $uri"
    }
}

$workspaceUri = "http://127.0.0.1:$Port/"
Write-Host ""
Write-Host "[EasyDep] Ready" -ForegroundColor Green
Write-Host "  UI:   $workspaceUri"
Write-Host "  API:  http://127.0.0.1:$Port/docs"
Write-Host "  Logs: $runRoot"
Write-Host "  Stop: powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -Stop"

if ($OpenBrowser) {
    Start-Process $workspaceUri
}
