[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$OpenBrowser,
    [switch]$SkipFrontendBuild,
    [switch]$ForceFrontendBuild,
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
$frontendHashPath = Join-Path $runRoot "frontend-lock.sha256"
$frontendBuildHashPath = Join-Path $runRoot "frontend-build.sha256"
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

if ($SkipFrontendBuild) {
    if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "build\index.html"))) {
        throw "-SkipFrontendBuild requires an existing frontend/build/index.html."
    }
    Write-Host "[EasyDep] Reusing the frontend build by explicit request."
}
else {
    if (-not (Test-CommandAvailable "npm")) {
        throw "Node.js and npm are required to build the frontend."
    }
    $npmCommand = (Get-Command npm.cmd -ErrorAction Stop).Source
    $lockPath = Join-Path $frontendRoot "package-lock.json"
    $lockHash = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash
    $installedHash = Read-HashRecord -Path $frontendHashPath
    Push-Location $frontendRoot
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules")) -or $lockHash -ne $installedHash) {
            Write-Host "[EasyDep] Installing pinned frontend packages."
            & $npmCommand ci
            if ($LASTEXITCODE -ne 0) {
                throw "npm ci failed."
            }
            Set-Content -LiteralPath $frontendHashPath -Value $lockHash -Encoding UTF8
        }
        $buildHash = Get-FrontendBuildHash
        $builtHash = Read-HashRecord -Path $frontendBuildHashPath
        $buildIndex = Join-Path $frontendRoot "build\index.html"
        if (-not $ForceFrontendBuild -and (Test-Path -LiteralPath $buildIndex) -and $buildHash -eq $builtHash) {
            Write-Host "[EasyDep] Frontend inputs are unchanged; reusing the existing build."
        }
        else {
            Write-Host "[EasyDep] Building the SvelteKit frontend."
            & $npmCommand run build
            if ($LASTEXITCODE -ne 0) {
                throw "The frontend build failed."
            }
            Set-Content -LiteralPath $frontendBuildHashPath -Value $buildHash -Encoding UTF8
        }
    }
    finally {
        Pop-Location
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
