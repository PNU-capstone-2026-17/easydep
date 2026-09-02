[CmdletBinding()]
param(
    [bool]$InstallIfMissing = $true
)

$ErrorActionPreference = "Stop"
$version = "3.11.9"

function Find-Python311 {
    $candidates = @()
    try {
        $launched = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null).Trim()
        if ($launched) { $candidates += $launched }
    } catch {}
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $minor = (& $candidate -X utf8 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
        if ($minor -eq "3.11") { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    return $null
}

$python = Find-Python311
if ($python) {
    Write-Output $python
    exit 0
}
if (-not $InstallIfMissing) {
    throw "Python 3.11이 없습니다. -InstallIfMissing `$true로 다시 실행하세요."
}
if (-not $env:LOCALAPPDATA -or -not $env:TEMP) {
    throw "LOCALAPPDATA 또는 TEMP 환경변수를 확인할 수 없습니다."
}

$downloadRoot = Join-Path $env:TEMP "easydep-python311-setup"
$expectedTempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
$resolvedDownloadRoot = [System.IO.Path]::GetFullPath($downloadRoot)
if (-not $resolvedDownloadRoot.StartsWith($expectedTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "임시 설치 경로가 시스템 TEMP 밖을 가리킵니다: $resolvedDownloadRoot"
}
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
$installer = Join-Path $downloadRoot "python-$version-amd64.exe"
$url = "https://www.python.org/ftp/python/$version/python-$version-amd64.exe"
try {
    Write-Host "Python $version 설치 파일을 공식 python.org에서 내려받습니다."
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer
    $target = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311"
    $arguments = @(
        "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=1",
        "Include_pip=1", "Include_test=0", "Include_tcltk=0", "Include_doc=0",
        "Shortcuts=0", "TargetDir=$target"
    )
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Python 설치 프로그램이 종료 코드 $($process.ExitCode)를 반환했습니다."
    }
} finally {
    if (Test-Path -LiteralPath $installer) {
        Remove-Item -LiteralPath $installer -Force
    }
    if ((Test-Path -LiteralPath $downloadRoot) -and -not (Get-ChildItem -LiteralPath $downloadRoot -Force)) {
        Remove-Item -LiteralPath $downloadRoot -Force
    }
}
$python = Find-Python311
if (-not $python) { throw "설치 후에도 Python 3.11 실행 파일을 찾지 못했습니다." }
Write-Output $python

