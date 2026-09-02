[CmdletBinding()]
param(
    [string]$Suite = "evaluation\comparison\suites\multi-domain-pilot.json",
    [string[]]$Case,
    [int]$Repetitions = 1,
    [switch]$SetupOnly,
    [switch]$SkipSetup,
    [switch]$KeepEasyDepRunning
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$baselineRoot = Join-Path $repoRoot "evaluation\baselines"
$easyDepPort = 8100
$easyDepBaseUrl = "http://127.0.0.1:$easyDepPort"

function Test-EasyDepHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$easyDepBaseUrl/api/health" -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    } catch { return $false }
}

function Import-ComparisonLlmSettings {
    $values = @{}
    $envFile = Join-Path $repoRoot ".env"
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
            if ($line -match '^\s*#' -or $line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { continue }
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $values[$name] = $value
        }
    }
    $apiKey = if ($env:COMPARISON_API_KEY) { $env:COMPARISON_API_KEY } elseif ($env:OPENAI_API_KEY) { $env:OPENAI_API_KEY } elseif ($env:API_KEY) { $env:API_KEY } else { $values["API_KEY"] }
    $baseUrl = if ($env:COMPARISON_BASE_URL) { $env:COMPARISON_BASE_URL } elseif ($env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL } elseif ($env:BASE_URL) { $env:BASE_URL } else { $values["BASE_URL"] }
    $model = if ($env:COMPARISON_MODEL) { $env:COMPARISON_MODEL } elseif ($env:OPENAI_MODEL) { $env:OPENAI_MODEL } elseif ($env:MODEL) { $env:MODEL } else { $values["MODEL"] }
    if (-not $apiKey -or $apiKey -match 'x{8,}|YOUR_API_KEY|changeme') {
        throw "LLM API 키가 없습니다. 저장소 .env의 API_KEY 또는 COMPARISON_API_KEY 환경변수를 설정하세요."
    }
    if (-not $baseUrl) { $baseUrl = "https://api.openai.com/v1" }
    if (-not $model) { $model = "gpt-4o-mini" }
    $env:COMPARISON_API_KEY = $apiKey
    $env:COMPARISON_BASE_URL = $baseUrl
    $env:COMPARISON_MODEL = $model
}

Push-Location $repoRoot
$startedByThisScript = $false
try {
    Write-Host "[1/5] Python 3.11 실행환경 확인"
    $python = (& (Join-Path $baselineRoot "ensure_python311.ps1") -InstallIfMissing (-not $SkipSetup) | Select-Object -Last 1)
    if (-not $python -or -not (Test-Path -LiteralPath $python)) { throw "Python 3.11 실행 파일을 확인하지 못했습니다." }

    $defaultToolRoot = Join-Path $env:LOCALAPPDATA "EasyDep\comparison"
    $metagptHome = Join-Path $defaultToolRoot "metagpt"
    $chatdevHome = Join-Path $defaultToolRoot "chatdev"
    if (-not $SkipSetup) {
        Write-Host "[2/5] MetaGPT 0.8.2와 ChatDev 1.1.6 준비 (준비된 환경은 재사용)"
        $metagptHome = (& (Join-Path $baselineRoot "setup_metagpt.ps1") -PythonPath $python -InstallRoot $metagptHome | Select-Object -Last 1)
        $chatdevHome = (& (Join-Path $baselineRoot "setup_chatdev.ps1") -PythonPath $python -InstallRoot $chatdevHome | Select-Object -Last 1)
    } else { Write-Host "[2/5] 비교 프레임워크 설치 생략" }
    $env:EASYDEP_METAGPT_HOME = $metagptHome
    $env:EASYDEP_CHATDEV_HOME = $chatdevHome
    if (-not (Test-Path -LiteralPath (Join-Path $metagptHome "Scripts\metagpt.exe"))) { throw "MetaGPT 환경이 준비되지 않았습니다: $metagptHome" }
    if (-not (Test-Path -LiteralPath (Join-Path $chatdevHome "source\run.py"))) { throw "ChatDev 환경이 준비되지 않았습니다: $chatdevHome" }

    $suitePath = if ([System.IO.Path]::IsPathRooted($Suite)) { $Suite } else { Join-Path $repoRoot $Suite }
    $caseArgs = @()
    foreach ($caseId in $Case) { $caseArgs += @("--case", $caseId) }
    & $python -X utf8 -m evaluation.comparison validate-suite $suitePath --repetitions $Repetitions @caseArgs
    if ($LASTEXITCODE -ne 0) { throw "비교 suite 검증에 실패했습니다." }
    if ($SetupOnly) {
        Write-Host "환경 구성과 suite 검증이 완료되었습니다."
        Write-Host "실제 비교 실행: .\scripts\run-comparison.ps1"
        exit 0
    }

    Import-ComparisonLlmSettings
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker Desktop이 필요합니다. 설치·실행한 뒤 다시 시도하세요." }
    docker info --format '{{.ServerVersion}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker 엔진에 연결할 수 없습니다. Docker Desktop을 실행한 뒤 다시 시도하세요." }

    Write-Host "[3/5] EasyDep 서버 확인 및 시작"
    if (-not (Test-EasyDepHealth)) {
        & (Join-Path $repoRoot "scripts\run-easydep.ps1") -Port $easyDepPort -ProductionLike
        if ($LASTEXITCODE -ne 0 -or -not (Test-EasyDepHealth)) { throw "EasyDep 서버 시작 또는 health check에 실패했습니다." }
        $startedByThisScript = $true
    } else { Write-Host "기존 EasyDep 서버를 재사용합니다: $easyDepBaseUrl" }

    Write-Host "[4/5] 다중 도메인 비교 실행"
    & $python -X utf8 -m evaluation.comparison run-suite $suitePath --repetitions $Repetitions @caseArgs
    if ($LASTEXITCODE -ne 0) { throw "비교 실행기가 비정상 종료되었습니다." }

    $suiteData = Get-Content -LiteralPath $suitePath -Encoding UTF8 | ConvertFrom-Json
    $outputRoot = [string]$suiteData.outputRoot
    if (-not [System.IO.Path]::IsPathRooted($outputRoot)) { $outputRoot = Join-Path $repoRoot $outputRoot }
    $reportPath = Join-Path $outputRoot "$($suiteData.suiteId)\comparison.md"
    Write-Host "[5/5] 완료"
    Write-Host "통합 보고서: $reportPath"
} finally {
    if ($startedByThisScript -and -not $KeepEasyDepRunning) {
        Write-Host "이 스크립트가 시작한 EasyDep 서버를 종료합니다."
        & (Join-Path $repoRoot "scripts\run-easydep.ps1") -Stop -Port $easyDepPort
    }
    Pop-Location
}
