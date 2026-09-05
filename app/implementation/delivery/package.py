"""선택된 ResourcePlan에서 사람이 실행할 deployment package를 만든다."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import DEFAULT_CONTAINER_PORT
from ..domain.implementation_ir import remove_readonly


def _label(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or "workload")).upper()


def _tofu_label(value: Any) -> str:
    """renderer와 같은 규칙으로 OpenTofu 식별자를 만든다.

    Compose 환경 변수는 대문자를 쓰지만 OpenTofu 변수와 output 이름은 원래 대소문자를
    유지한다. 두 규칙을 섞으면 ``TF_VAR_image_digest_web`` 대신 존재하지 않는 대문자
    변수를 내보내므로 별도 함수로 분리한다.
    """

    text = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "resource"))
    if text[:1].isdigit():
        text = f"r_{text}"
    return text or "resource"


def _write_text(path: Path, content: str) -> None:
    """Windows에서 생성해도 Linux가 실행할 수 있도록 LF 줄바꿈으로 저장한다."""
    path.write_text(content, encoding="utf-8", newline="\n")


def _compose(resource_plan: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    """runtimeUnits를 Compose와 비밀값 없는 env 변수 목록으로 옮긴다."""

    lines = ["services:"]
    envs: list[tuple[str, str]] = []
    networks: set[str] = set()
    for unit in resource_plan.get("runtimeUnits") or []:
        network = str(unit.get("containerNetwork") or "easydep")
        networks.add(network)
        for container in unit.get("containers") or []:
            workload_id = str(container.get("workloadRef") or "workload")
            service = re.sub(r"[^a-z0-9-]", "-", workload_id.lower()).strip("-") or "workload"
            image_env = f"{_label(workload_id)}_IMAGE"
            raw_image = container.get("image")
            image = raw_image if isinstance(raw_image, str) else ""
            if (container.get("artifact") or {}).get("kind") == "generatedApplication" or not image:
                image = "${" + image_env + "}"
                envs.append((image_env, f"Immutable container image for {workload_id}."))
            lines.extend([f"  {service}:", f"    image: {image}", "    restart: unless-stopped"])
            ports: list[str] = []
            for interface in container.get("interfaces") or []:
                port = interface.get("port")
                if isinstance(port, int) and interface.get("exposure") in {"public", "internal"}:
                    ports.append(f'      - "{port}:{port}"')
            if ports:
                lines.append("    ports:")
                lines.extend(ports)
            environment: list[str] = []
            for binding in container.get("runtimeBindings") or []:
                name = str(binding.get("environmentName") or "")
                if name:
                    environment.append(f"      - {name}")
            for configuration in container.get("configuration") or []:
                name = str(configuration.get("name") or "")
                if name:
                    environment.append(f"      - {name}")
                    kind = str(configuration.get("kind") or "")
                    # resource binding과 Secret은 cloud-init이 공급한다. 이를 로컬
                    # ``.env.example``에 다시 노출하면 사용자가 필수 입력으로 오해하거나
                    # 비밀값을 평문 파일에 넣을 수 있으므로, 일반 설정만 예시에 싣는다.
                    if not configuration.get("sensitive") and kind not in {
                        "secret",
                        "secretBinding",
                    }:
                        envs.append((name, f"Optional runtime setting for {workload_id}."))
            if environment:
                lines.append("    environment:")
                lines.extend(dict.fromkeys(environment))
            volumes = [
                f'      - "/mnt/easydep/{mount.get("storageRef") or "data"!s}'
                f'/data:{mount.get("mountPath")!s}"'
                for mount in container.get("mounts") or []
                if isinstance(mount.get("mountPath"), str)
            ]
            if volumes:
                lines.append("    volumes:")
                lines.extend(volumes)
            lines.extend(["    networks:", f"      - {network}"])
    if networks:
        lines.append("networks:")
        for network in sorted(networks):
            lines.extend([f"  {network}:", f'    name: "{network}"', "    external: true"])
    return "\n".join(lines) + "\n", list(dict.fromkeys(envs))


def _tfvars_example(resource_plan: dict[str, Any]) -> str:
    lines = [
        "# Copy this file to terraform.tfvars and fill deployment inputs locally.",
        "# Use a unique prefix for disposable deployments so concurrent runs do not collide.",
        'resource_prefix = "easydep"',
    ]
    provider = str(resource_plan.get("provider") or "")
    if provider == "aws":
        lines.extend(['boot_image_id = "ami-REPLACE_ME"', 'ssh_public_key = ""'])
    elif provider == "azure":
        lines.extend(['subscription_id = ""', 'ssh_public_key = ""'])
    elif provider == "gcp":
        lines.append('project_id = ""')
    for workload in resource_plan.get("workloads") or []:
        workload_label = _tofu_label(workload.get("id"))
        if (workload.get("artifact") or {}).get("kind") == "generatedApplication":
            lines.append(
                f"# image_digest_{workload_label} is written to "
                "runtime/image-digests.env by easydep.ps1."
            )
        for interface in workload.get("interfaces") or []:
            if isinstance(interface.get("port"), int):
                continue
            interface_label = _tofu_label(interface.get("id"))
            lines.append(
                f"container_port_{workload_label}_{interface_label} = "
                f"{DEFAULT_CONTAINER_PORT} # Replace when the app uses another port."
            )
    for slot in resource_plan.get("bindingSlots") or []:
        if slot.get("kind") in {"secretReference", "externalEndpoint"}:
            lines.append(f'{_tofu_label(slot.get("id"))} = ""')
    return "\n".join(lines) + "\n"


def _registry_bootstrap_targets(
    resource_plan: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """생성 앱별 ``(workload, resource address, registry output)``을 돌려준다."""

    nodes = {
        str(node.get("id") or ""): node for node in resource_plan.get("nodes") or []
    }
    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for unit in resource_plan.get("runtimeUnits") or []:
        for container in unit.get("containers") or []:
            registry_ref = str(container.get("registryRef") or "")
            workload_ref = str(container.get("workloadRef") or "")
            if not registry_ref or not workload_ref:
                continue
            workload_label = _tofu_label(workload_ref)
            node = nodes.get(registry_ref) or {}
            terraform_types = list(node.get("terraformTypes") or [])
            if node.get("handling") != "create" or not terraform_types:
                raise ValueError(
                    f"Generated workload {workload_label} has no creatable registry: {registry_ref}"
                )
            address = f"{terraform_types[0]}.{_tofu_label(registry_ref)}"
            key = (workload_label, address)
            if key not in seen:
                targets.append(
                    (workload_label, address, f"registry_{workload_label}_url")
                )
                seen.add(key)
    return targets


def _health_outputs(rendered_tofu: dict[str, str]) -> list[str]:
    """실제 outputs.tf에 존재하는 공개 health URL 이름만 고른다."""

    return re.findall(
        r'^output\s+"(health_url_[A-Za-z0-9_]+)"\s*\{',
        rendered_tofu.get("outputs.tf", ""),
        re.MULTILINE,
    )


def _interactive_powershell_script(
    resource_plan: dict[str, Any], rendered_tofu: dict[str, str]
) -> str:
    """인자 없이 실행하는 단일 대화형 배포 스크립트를 만든다.

    기존에는 내부 단계를 각각 별도 파일로 노출했다. 사용자는 어느 파일부터 실행하고
    어디서 재개할지 직접 판단해야 했으므로, 여기서는 같은 단계를 함수로 감추고
    ``배포/재개``와 ``삭제``만 선택하게 한다.
    """

    config = {
        "provider": str(resource_plan.get("provider") or ""),
        "region": str(resource_plan.get("region") or ""),
        "registryTargets": [
            {"workload": workload, "address": address, "output": output}
            for workload, address, output in _registry_bootstrap_targets(resource_plan)
        ],
        "healthOutputs": _health_outputs(rendered_tofu),
        "retainedResources": [
            (
                f"{next(iter(node.get('terraformTypes') or []), '')}."
                f"{_tofu_label(node.get('id'))}"
            )
            for node in resource_plan.get("nodes") or []
            if isinstance(node, dict)
            and (node.get("attributes") or {}).get("deletionPolicy") == "retain"
            and next(iter(node.get("terraformTypes") or []), "")
        ],
    }
    config_json = json.dumps(config, ensure_ascii=True, separators=(",", ":"))
    script = r'''$ErrorActionPreference = 'Stop'
$Config = ConvertFrom-Json @'
__CONFIG__
'@
$Root = $PSScriptRoot
$TofuRoot = Join-Path $Root 'tofu'
$RuntimeRoot = Join-Path $Root 'runtime'
$TfvarsPath = Join-Path $TofuRoot 'terraform.tfvars'
$DigestPath = Join-Path $RuntimeRoot 'image-digests.env'
$PlanPath = Join-Path $TofuRoot 'easydep.tfplan'

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
  & $Program @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE." }
}

function Read-Required([string]$Prompt, [string]$Default = '') {
  while ($true) {
    $suffix = if ($Default) { " [$Default]" } else { '' }
    $value = Read-Host "$Prompt$suffix"
    if (-not $value) { $value = $Default }
    if ($value) { return $value }
    Write-Host 'A value is required.' -ForegroundColor Yellow
  }
}

function Set-TfValue([string]$Name, [string]$Value) {
  $escaped = $Value.Replace('\', '\\').Replace('"', '\"')
  $content = Get-Content -Raw -Encoding UTF8 $TfvarsPath
  $pattern = '(?m)^' + [regex]::Escape($Name) + '\s*=.*$'
  $replacement = $Name + ' = "' + $escaped + '"'
  if ([regex]::IsMatch($content, $pattern)) {
    $content = [regex]::Replace($content, $pattern, $replacement)
  } else {
    $content = $content.TrimEnd() + [Environment]::NewLine + $replacement + [Environment]::NewLine
  }
  Set-Content -Encoding UTF8 -LiteralPath $TfvarsPath -Value $content
}

function Test-CloudLogin {
  switch ($Config.provider) {
    'aws' {
      $identityJson = & aws sts get-caller-identity --region $Config.region --output json
      if ($LASTEXITCODE -ne 0) {
        throw 'AWS authentication failed. Run aws configure or aws sso login, then run this script again.'
      }
      $identity = $identityJson | ConvertFrom-Json
      if ($identity.Arn -like '*:root') {
        throw 'Refusing to deploy with AWS root credentials. Configure a short-lived IAM or SSO identity.'
      }
    }
    'azure' {
      & az account show --output none
      if ($LASTEXITCODE -ne 0) {
        throw 'Azure authentication failed. Run az login and select a subscription, then run this script again.'
      }
    }
    'gcp' {
      & gcloud auth application-default print-access-token | Out-Null
      if ($LASTEXITCODE -ne 0) {
        throw 'GCP credentials are unavailable. Run gcloud auth application-default login, then run this script again.'
      }
    }
    default { throw "Unsupported cloud provider: $($Config.provider)" }
  }
}

function Test-Prerequisites([bool]$NeedsDocker) {
  Get-Command tofu -ErrorAction Stop | Out-Null
  $cloudCommand = @{ aws = 'aws'; azure = 'az'; gcp = 'gcloud' }[$Config.provider]
  Get-Command $cloudCommand -ErrorAction Stop | Out-Null
  if ($NeedsDocker) {
    Get-Command docker -ErrorAction Stop | Out-Null
    & docker info | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker is installed but its daemon is not running.' }
  }
  Test-CloudLogin
}

function Initialize-Inputs {
  if (Test-Path $TfvarsPath) { return }
  Copy-Item (Join-Path $TofuRoot 'terraform.tfvars.example') $TfvarsPath
  Write-Host 'Enter the deployment values. They remain only in this extracted folder.' -ForegroundColor Cyan
  Set-TfValue 'resource_prefix' (Read-Required 'Unique resource prefix' 'easydep')

  switch ($Config.provider) {
    'aws' {
      $ami = ''
      try {
        $ami = (& aws ssm get-parameter --region $Config.region --name '/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64' --query 'Parameter.Value' --output text 2>$null).Trim()
      } catch { $ami = '' }
      if (-not $ami.StartsWith('ami-')) { $ami = '' }
      Set-TfValue 'boot_image_id' (Read-Required "x86_64 Linux AMI in $($Config.region)" $ami)
    }
    'azure' {
      $subscription = (& az account show --query id --output tsv).Trim()
      Set-TfValue 'subscription_id' (Read-Required 'Azure subscription ID' $subscription)
      $keyPath = Read-Required 'Path to an OpenSSH public key'
      Set-TfValue 'ssh_public_key' (Get-Content -Raw -Encoding UTF8 $keyPath).Trim()
    }
    'gcp' {
      $project = (& gcloud config get-value project 2>$null).Trim()
      Set-TfValue 'project_id' (Read-Required 'GCP project ID' $project)
    }
  }

  # Provider 기본값 이외에 외부 endpoint나 Secret 참조가 있으면 이름 그대로 묻는다.
  $content = Get-Content -Raw -Encoding UTF8 $TfvarsPath
  $emptyValues = [regex]::Matches($content, '(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*""\s*$')
  foreach ($match in $emptyValues) {
    $name = $match.Groups[1].Value
    if ($Config.provider -eq 'aws' -and $name -eq 'ssh_public_key') { continue }
    Set-TfValue $name (Read-Required "Value for $name")
  }
}

function Import-RuntimeValues {
  $envPath = Join-Path $RuntimeRoot '.env'
  if (Test-Path $envPath) {
    $env:TF_VAR_runtime_env = [IO.File]::ReadAllText($envPath)
  }
  if (Test-Path $DigestPath) {
    Get-Content -Encoding UTF8 $DigestPath | ForEach-Object {
      if ($_ -match '^(TF_VAR_[A-Za-z0-9_]+)=(.+)$') {
        Set-Item -Path ('Env:' + $matches[1]) -Value $matches[2]
      }
    }
  }
}

function Initialize-Tofu {
  Set-Location $TofuRoot
  Write-Host 'Initializing OpenTofu. The first provider download can take several minutes.' -ForegroundColor Cyan
  Invoke-Checked 'tofu' @('init', '-input=false')
}

function Initialize-Images {
  if (Test-Path $DigestPath) {
    Write-Host 'Reusing the recorded application image digest.' -ForegroundColor Green
    return $true
  }
  $answer = Read-Host 'Container registries will now be created and may incur cloud charges. Continue? [y/N]'
  if ($answer -notmatch '^(?i)y(?:es)?$') {
    Write-Host 'Deployment cancelled before creating cloud resources.' -ForegroundColor Yellow
    return $false
  }

  Initialize-Tofu
  $placeholder = 'sha256:' + ('0' * 64)
  $targetArgs = @()
  foreach ($target in @($Config.registryTargets)) {
    $targetArgs += "-target=$($target.address)"
    $targetArgs += "-var=image_digest_$($target.workload)=$placeholder"
  }
  if ($targetArgs.Count -gt 0) {
    Invoke-Checked 'tofu' (@('apply', '-auto-approve') + $targetArgs)
  }

  $applicationRoot = Resolve-Path (Join-Path $Root '..')
  $digestLines = @()
  foreach ($target in @($Config.registryTargets)) {
    $registryUrl = (& tofu output -raw $target.output).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Cannot read registry output $($target.output)." }
    $registryHost = $registryUrl.Split('/')[0]
    switch ($Config.provider) {
      'aws' {
        $password = aws ecr get-login-password --region $Config.region
        if ($LASTEXITCODE -ne 0) { throw 'AWS registry login token failed.' }
        $password | docker login --username AWS --password-stdin $registryHost
      }
      'azure' { az acr login --name $registryHost.Split('.')[0] }
      'gcp' { gcloud auth configure-docker $registryHost --quiet }
    }
    if ($LASTEXITCODE -ne 0) { throw 'Container registry login failed.' }

    $tag = 'easydep-' + $target.workload + '-' + (Get-Date -Format 'yyyyMMddHHmmss')
    $imageTag = $registryUrl + ':' + $tag
    Invoke-Checked 'docker' @('build', '-t', $imageTag, $applicationRoot)
    $pushOutput = docker push $imageTag 2>&1 | Out-String
    $pushExitCode = $LASTEXITCODE
    Write-Host $pushOutput
    if ($pushExitCode -ne 0) { throw 'Docker image push failed.' }
    $digest = [regex]::Match($pushOutput, 'digest: (sha256:[0-9a-f]{64})')
    if (-not $digest.Success) { throw 'The registry did not report an immutable image digest.' }
    $digestLines += "TF_VAR_image_digest_$($target.workload)=$($digest.Groups[1].Value)"
  }
  $digestLines | Set-Content -Encoding UTF8 $DigestPath
  return $true
}

function New-AndApplyPlan {
  Set-Location $TofuRoot
  Import-RuntimeValues
  Invoke-Checked 'tofu' @('validate', '-no-color')
  Invoke-Checked 'tofu' @('plan', '-input=false', '-out=easydep.tfplan')
  Invoke-Checked 'tofu' @('show', '-no-color', 'easydep.tfplan')
  $answer = Read-Host 'Apply the plan shown above? [y/N]'
  if ($answer -notmatch '^(?i)y(?:es)?$') {
    Write-Host 'The plan was saved but not applied.' -ForegroundColor Yellow
    return $false
  }
  Invoke-Checked 'tofu' @('apply', 'easydep.tfplan')
  return $true
}

function Test-DeployedApplication {
  if (@($Config.healthOutputs).Count -eq 0) {
    Write-Host 'Deployment completed. This private application has no public health URL; verify it from inside the cloud network.' -ForegroundColor Yellow
    return
  }
  foreach ($outputName in @($Config.healthOutputs)) {
    $healthUrl = (& tofu output -raw $outputName).Trim()
    $healthy = $false
    Write-Host "Waiting for $healthUrl"
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
      try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri $healthUrl | Out-Null
        $healthy = $true
        break
      } catch { Start-Sleep -Seconds 10 }
    }
    if (-not $healthy) { throw "Health check timed out after ten minutes: $healthUrl" }
  }
  Write-Host 'Deployment and health verification completed.' -ForegroundColor Green
}

function Start-OrContinueDeployment {
  Test-Prerequisites $true
  Initialize-Inputs
  if (-not (Initialize-Images)) { return }
  if (New-AndApplyPlan) { Test-DeployedApplication }
}

function Remove-Deployment {
  Test-Prerequisites $false
  if (-not (Test-Path (Join-Path $TofuRoot 'terraform.tfstate'))) {
    Write-Host 'No local OpenTofu state was found. Nothing can be destroyed from this folder.' -ForegroundColor Yellow
    return
  }
  $answer = Read-Host 'Destroy resources managed by this deployment state? Type DESTROY to continue'
  if ($answer -cne 'DESTROY') { Write-Host 'Destroy cancelled.'; return }
  Initialize-Tofu
  Import-RuntimeValues
  $retainedLog = Join-Path $Root 'retained-resources.txt'
  foreach ($address in @($Config.retainedResources)) {
    $stateAddresses = @(& tofu state list)
    if ($stateAddresses -contains $address) {
      $stateText = (& tofu state show -no-color $address | Out-String)
      $idMatch = [regex]::Match($stateText, '(?m)^\s*id\s*=\s*"?([^"\r\n]+)')
      $cloudId = if ($idMatch.Success) { $idMatch.Groups[1].Value.Trim() } else { 'unknown' }
      Add-Content -Encoding UTF8 -LiteralPath $retainedLog -Value "$address cloud_id=$cloudId"
      Invoke-Checked 'tofu' @('state', 'rm', $address)
      Write-Host "Retained $address. Its cloud ID was written to retained-resources.txt." -ForegroundColor Yellow
    }
  }
  Invoke-Checked 'tofu' @('destroy', '-auto-approve')
  Remove-Item -LiteralPath $DigestPath -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $PlanPath -ErrorAction SilentlyContinue
  Write-Host 'Managed resources were destroyed.' -ForegroundColor Green
}

function Show-Status {
  $image = if (Test-Path $DigestPath) { 'ready' } else { 'not prepared' }
  $state = if (Test-Path (Join-Path $TofuRoot 'terraform.tfstate')) { 'present' } else { 'not created' }
  Write-Host "Provider: $($Config.provider.ToUpper())  Region: $($Config.region)"
  Write-Host "Local state: $state  Application image: $image"
}

while ($true) {
  Write-Host ''
  Write-Host 'EasyDep Deployment' -ForegroundColor Cyan
  Show-Status
  Write-Host ''
  Write-Host '1. Start or continue deployment'
  Write-Host '2. Destroy deployed resources'
  Write-Host '0. Exit'
  $choice = Read-Host 'Select'
  try {
    switch ($choice) {
      '1' { Start-OrContinueDeployment }
      '2' { Remove-Deployment }
      '0' { return }
      default { Write-Host 'Select 1, 2, or 0.' -ForegroundColor Yellow }
    }
  } catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host 'Fix the reported cause and choose Start or continue deployment again.' -ForegroundColor Yellow
  }
}
'''
    return script.replace("__CONFIG__", config_json)


def _format_open_tofu(directory: Path) -> None:
    """설치된 formatter가 있으면 package의 HCL을 검사와 같은 형식으로 고정한다."""

    executable = shutil.which("tofu") or shutil.which("terraform")
    if executable is None:
        return
    completed = subprocess.run(
        [executable, "fmt", "-recursive"],
        cwd=directory,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        output = ((completed.stderr or "") + (completed.stdout or ""))[-1000:]
        raise RuntimeError(f"OpenTofu formatting failed: {output}")


def _readme(resource_plan: dict[str, Any]) -> str:
    """패키지만 받은 사용자도 시작할 수 있는 짧은 공급자별 안내를 만든다."""

    provider = str(resource_plan.get("provider") or "")
    region = str(resource_plan.get("region") or "")
    authentication = {
        "aws": (
            "Run `aws configure`, or run `aws sso login --profile <profile>` and set "
            "`AWS_PROFILE`. The script verifies the identity with "
            f"`aws sts get-caller-identity --region {region}`."
        ),
        "azure": (
            "Run `az login`, then `az account set --subscription <subscription-id>`."
        ),
        "gcp": (
            "Run `gcloud auth login`, `gcloud auth application-default login`, and "
            "`gcloud config set project <project-id>`."
        ),
    }.get(provider, "Authenticate with the cloud provider CLI.")
    return f"""# EasyDep deployment package

This package targets **{provider.upper()}** in **{region}**. Keep `Dockerfile`, `build.gradle`, `frontend/`, `src/`, and `deployment/` together after extracting the ZIP.

## Prerequisites

Install PowerShell, OpenTofu 1.8 or newer, Docker with a running daemon, and the {provider.upper()} CLI. {authentication}

Use a short-lived, least-privilege cloud identity. Do not use a cloud account's root or owner identity. The first OpenTofu provider download can take several minutes; wait for it instead of starting another copy.

## Deploy or resume

Open PowerShell in this `deployment` directory and run:

```powershell
.\\easydep.ps1
```

Choose **Start or continue deployment**. The script checks the environment and login, asks only for missing deployment values, prepares and uploads the image, displays the OpenTofu plan, asks before applying it, and verifies the public health URL. It detects the local state and image digest when you run it again after a failure.

The script clearly warns before it creates the first billable cloud resource. OpenTofu state, `terraform.tfvars`, and image digests stay in this extracted folder; do not commit or share them. Passwords, API keys, and private keys belong in the selected cloud secret service, not in these files or VM metadata.

Choose **Destroy deployed resources** from the same menu when finished. Data marked for retention is removed from OpenTofu management before the other resources are destroyed. Its cloud ID is written to `retained-resources.txt`; you remain responsible for that resource and its charges.
"""


def render_deployment_package(
    application: Path, resource_plan: dict[str, Any], rendered_tofu: dict[str, str]
) -> Path:
    """`application/deployment`에 관리되는 사용자 배포 package를 원자적으로 쓴다."""

    destination = application / "deployment"
    marker = destination / ".easydep-managed"
    # 시스템 임시 폴더에서 만든 디렉터리를 Windows 앱 폴더로 옮기면 원래 앱의 ACL을
    # 상속하지 않아 Docker가 생성물을 읽지 못할 수 있다. 대상 앱 안에서 staging을
    # 만들면 같은 파일시스템에서 원자적으로 옮길 수 있고 접근 권한도 앱과 같게 유지된다.
    staging = Path(tempfile.mkdtemp(prefix=".easydep-deployment-", dir=application))
    try:
        package = staging / "deployment"
        tofu = package / "tofu"
        runtime = package / "runtime"
        tofu.mkdir(parents=True)
        runtime.mkdir()
        _write_text(package / ".easydep-managed", "easydep deployment package\n")
        # Keep every renderer-owned .tf/.tftpl file. main/variables/outputs are
        # the stable human entry points; auxiliary templates are referenced by it.
        for name, content in rendered_tofu.items():
            if name.endswith((".tf", ".tftpl")):
                _write_text(tofu / name, content)
        cloud_init = next(
            (
                content
                for name, content in rendered_tofu.items()
                if name.startswith("cloud-init_") and name.endswith(".yaml.tftpl")
            ),
            None,
        )
        if not isinstance(cloud_init, str):
            raise TypeError("OpenTofu rendering did not produce an active cloud-init template")
        compose, envs = _compose(resource_plan)
        _write_text(runtime / "compose.yaml", compose)
        env_example = (
            "# Optional non-secret overrides only. Resource bindings are supplied by cloud-init.\n"
            + "\n".join(f"# {description}\n{name}=" for name, description in envs)
            + "\n"
        )
        _write_text(runtime / ".env.example", env_example)
        _write_text(tofu / "terraform.tfvars.example", _tfvars_example(resource_plan))
        # This stable filename is a copy of the exact per-compute template that
        # main.tf passes as user_data/custom_data/instance metadata.
        _write_text(tofu / "cloud-init.yaml.tftpl", cloud_init)
        _format_open_tofu(tofu)
        _write_text(
            package / "easydep.ps1",
            _interactive_powershell_script(resource_plan, rendered_tofu),
        )
        _write_text(package / "README.md", _readme(resource_plan))
        if destination.exists():
            if not marker.is_file():
                raise ValueError(f"Refusing to replace unmanaged deployment package: {destination}")
            shutil.rmtree(destination, onerror=remove_readonly)
        shutil.move(str(package), str(destination))
        return destination
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


__all__ = ["render_deployment_package"]
