"""선택된 ResourcePlan에서 사람이 실행할 deployment package를 만든다."""

from __future__ import annotations

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
                    envs.append((name, f"Runtime endpoint configuration for {workload_id}."))
            for configuration in container.get("configuration") or []:
                name = str(configuration.get("name") or "")
                if name:
                    environment.append(f"      - {name}")
                    kind = str(configuration.get("kind") or "")
                    description = (
                        f"Secret reference or value for {workload_id}; obtain it through the selected cloud secret service."
                        if configuration.get("sensitive") or kind in {"secret", "secretBinding"}
                        else f"Runtime configuration for {workload_id}."
                    )
                    envs.append((name, description))
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
                "runtime/image-digests.env by prepare-images.*"
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


def _shell_scripts(
    resource_plan: dict[str, Any], rendered_tofu: dict[str, str]
) -> dict[str, str]:
    root = 'ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"\ncd "$ROOT/tofu"\n'
    prefix = "#!/usr/bin/env bash\nset -euo pipefail\n" + root
    load_runtime = (
        'if [ -f "$ROOT/runtime/.env" ]; then export TF_VAR_runtime_env="$(cat "$ROOT/runtime/.env")"; fi\n'
        'if [ -f "$ROOT/runtime/image-digests.env" ]; then set -a; . "$ROOT/runtime/image-digests.env"; set +a; fi\n'
    )
    provider = str(resource_plan.get("provider") or "")
    region = str(resource_plan.get("region") or "")
    targets = _registry_bootstrap_targets(resource_plan)
    prepare = [
        prefix.rstrip(),
        'APPLICATION_ROOT="$(CDPATH= cd -- "$ROOT/.." && pwd)"',
        'DIGEST_FILE="$ROOT/runtime/image-digests.env"',
        'PLACEHOLDER_DIGEST="sha256:' + "0" * 64 + '"',
        "tofu init",
    ]
    if targets:
        target_args = " ".join(f"-target={address}" for _, address, _ in targets)
        variable_args = " ".join(
            f'-var=image_digest_{workload}="$PLACEHOLDER_DIGEST"'
            for workload, _, _ in targets
        )
        prepare.append(f"tofu apply -auto-approve {target_args} {variable_args}")
        prepare.extend(['mkdir -p "$ROOT/runtime"', ': > "$DIGEST_FILE"'])
    for workload, _address, output in targets:
        prepare.extend(
            [
                f'REGISTRY_URL=$(tofu output -raw {output})',
                'REGISTRY_HOST=$(printf "%s" "$REGISTRY_URL" | cut -d/ -f1)',
            ]
        )
        if provider == "aws":
            prepare.append(
                f'aws ecr get-login-password --region "{region}" | docker login --username AWS --password-stdin "$REGISTRY_HOST"'
            )
        elif provider == "azure":
            prepare.append(
                'az acr login --name "$(printf "%s" "$REGISTRY_HOST" | cut -d. -f1)"'
            )
        elif provider == "gcp":
            prepare.append('gcloud auth configure-docker "$REGISTRY_HOST" --quiet')
        prepare.extend(
            [
                f'IMAGE_TAG="$REGISTRY_URL:easydep-{workload}"',
                'docker build --pull -t "$IMAGE_TAG" "$APPLICATION_ROOT"',
                'PUSH_OUTPUT=$(docker push "$IMAGE_TAG" 2>&1)',
                'printf "%s\\n" "$PUSH_OUTPUT"',
                'IMAGE_DIGEST=$(printf "%s\\n" "$PUSH_OUTPUT" | sed -n "s/.*digest: \\(sha256:[0-9a-f]\\{64\\}\\).*/\\1/p" | tail -1)',
                '[ -n "$IMAGE_DIGEST" ] || { echo "docker push did not report an immutable digest" >&2; exit 1; }',
                f'printf "TF_VAR_image_digest_{workload}=%s\\n" "$IMAGE_DIGEST" >> "$DIGEST_FILE"',
            ]
        )

    verify = [prefix.rstrip(), "tofu output -json"]
    health_outputs = _health_outputs(rendered_tofu)
    if health_outputs:
        for output in health_outputs:
            verify.extend(
                [
                    f'HEALTH_URL=$(tofu output -raw {output})',
                    'ATTEMPT=0',
                    'until curl --fail --silent --show-error "$HEALTH_URL"; do',
                    "  ATTEMPT=$((ATTEMPT + 1))",
                    '  [ "$ATTEMPT" -lt 60 ] || { echo "health check timed out: $HEALTH_URL" >&2; exit 1; }',
                    "  sleep 10",
                    "done",
                ]
            )
    else:
        verify.extend(
            [
                'echo "No public health URL is available; verify this private deployment from inside its network." >&2',
                "exit 2",
            ]
        )

    scripts = {
        "doctor.sh": prefix
        + "command -v tofu >/dev/null\ncommand -v docker >/dev/null\ncommand -v curl >/dev/null\n"
        + {
            "aws": "command -v aws >/dev/null\n",
            "azure": "command -v az >/dev/null\n",
            "gcp": "command -v gcloud >/dev/null\n",
        }.get(provider, "")
        + "tofu version\ndocker version\n",
        "prepare-images.sh": "\n".join(prepare) + "\n",
        "plan.sh": prefix
        + load_runtime
        + "tofu init\ntofu validate\ntofu plan -out=easydep.tfplan \"$@\"\n",
        "deploy.sh": prefix
        + '[ -f easydep.tfplan ] || { echo "Run scripts/plan.sh first." >&2; exit 1; }\n'
        + "tofu apply \"$@\" easydep.tfplan\n",
        "verify.sh": "\n".join(verify) + "\n",
        "destroy.sh": prefix + load_runtime + "tofu destroy \"$@\"\n",
    }
    scripts["smoke-test.sh"] = (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        'cleanup() { if [ "${KEEP_RESOURCES:-0}" != "1" ]; then "$SCRIPT_DIR/destroy.sh" -auto-approve || echo "Automatic cleanup failed; inspect the OpenTofu state." >&2; fi; }\n'
        "trap cleanup EXIT\n"
        '"$SCRIPT_DIR/doctor.sh"\n'
        '"$SCRIPT_DIR/prepare-images.sh"\n'
        '"$SCRIPT_DIR/plan.sh" -input=false\n'
        '"$SCRIPT_DIR/deploy.sh" -auto-approve\n'
        '"$SCRIPT_DIR/verify.sh"\n'
    )
    return scripts


def _powershell_scripts(
    resource_plan: dict[str, Any], rendered_tofu: dict[str, str]
) -> dict[str, str]:
    root = '$root = Resolve-Path (Join-Path $PSScriptRoot "..")\nSet-Location (Join-Path $root "tofu")\n'
    load_runtime = (
        "$envFile = Join-Path $root 'runtime\\.env'\n"
        "if (Test-Path $envFile) { $env:TF_VAR_runtime_env = [IO.File]::ReadAllText($envFile) }\n"
        "$digestFile = Join-Path $root 'runtime\\image-digests.env'\n"
        "if (Test-Path $digestFile) { Get-Content -Encoding UTF8 $digestFile | ForEach-Object { if ($_ -match '^(TF_VAR_[A-Za-z0-9_]+)=(.+)$') { Set-Item -Path (\"Env:\" + $matches[1]) -Value $matches[2] } } }\n"
    )
    provider = str(resource_plan.get("provider") or "")
    region = str(resource_plan.get("region") or "").replace("'", "''")
    targets = _registry_bootstrap_targets(resource_plan)
    prepare = [
        "$ErrorActionPreference = 'Stop'",
        root.rstrip(),
        '$applicationRoot = Resolve-Path (Join-Path $root "..")',
        "$placeholderDigest = 'sha256:" + "0" * 64 + "'",
        "tofu init",
        "if ($LASTEXITCODE -ne 0) { throw 'tofu init failed.' }",
    ]
    if targets:
        arguments = ["'apply'", "'-auto-approve'"]
        arguments.extend(f"'-target={address}'" for _, address, _ in targets)
        arguments.extend(
            f"('-var=image_digest_{workload}=' + $placeholderDigest)"
            for workload, _, _ in targets
        )
        prepare.extend(
            [
                "$bootstrapArgs = @(" + ", ".join(arguments) + ")",
                "& tofu @bootstrapArgs",
                "if ($LASTEXITCODE -ne 0) { throw 'Registry bootstrap failed.' }",
                "$digestLines = @()",
            ]
        )
    for workload, _address, output in targets:
        prepare.extend(
            [
                f"$registryUrl = (& tofu output -raw {output}).Trim()",
                "if ($LASTEXITCODE -ne 0) { throw 'Cannot read registry output.' }",
                "$registryHost = $registryUrl.Split('/')[0]",
            ]
        )
        if provider == "aws":
            prepare.extend(
                [
                    f"$registryPassword = aws ecr get-login-password --region '{region}'",
                    "if ($LASTEXITCODE -ne 0) { throw 'AWS registry login token failed.' }",
                    "$registryPassword | docker login --username AWS --password-stdin $registryHost",
                    "if ($LASTEXITCODE -ne 0) { throw 'Docker registry login failed.' }",
                ]
            )
        elif provider == "azure":
            prepare.extend(
                [
                    "$registryName = $registryHost.Split('.')[0]",
                    "az acr login --name $registryName",
                    "if ($LASTEXITCODE -ne 0) { throw 'Azure registry login failed.' }",
                ]
            )
        elif provider == "gcp":
            prepare.extend(
                [
                    "gcloud auth configure-docker $registryHost --quiet",
                    "if ($LASTEXITCODE -ne 0) { throw 'GCP registry login failed.' }",
                ]
            )
        prepare.extend(
            [
                f'$imageTag = $registryUrl + ":easydep-{workload}"',
                "docker build --pull -t $imageTag $applicationRoot",
                "if ($LASTEXITCODE -ne 0) { throw 'Docker image build failed.' }",
                "$pushOutput = docker push $imageTag 2>&1 | Out-String",
                "$pushExitCode = $LASTEXITCODE",
                "Write-Host $pushOutput",
                "if ($pushExitCode -ne 0) { throw 'Docker image push failed.' }",
                "$digestMatch = [regex]::Match($pushOutput, 'digest: (sha256:[0-9a-f]{64})')",
                "if (-not $digestMatch.Success) { throw 'docker push did not report an immutable digest.' }",
                f'$digestLines += "TF_VAR_image_digest_{workload}=$($digestMatch.Groups[1].Value)"',
            ]
        )
    if targets:
        prepare.extend(
            [
                "$digestFile = Join-Path $root 'runtime\\image-digests.env'",
                "$digestLines | Set-Content -Encoding UTF8 $digestFile",
            ]
        )

    verify = [
        "$ErrorActionPreference = 'Stop'",
        root.rstrip(),
        "tofu output -json",
        "if ($LASTEXITCODE -ne 0) { throw 'Cannot read OpenTofu outputs.' }",
    ]
    health_outputs = _health_outputs(rendered_tofu)
    if health_outputs:
        output_names = ", ".join(f"'{name}'" for name in health_outputs)
        verify.extend(
            [
                f"foreach ($outputName in @({output_names})) {{",
                "  $healthUrl = (& tofu output -raw $outputName).Trim()",
                "  $healthy = $false",
                "  for ($attempt = 0; $attempt -lt 60; $attempt++) {",
                "    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri $healthUrl | Out-Null; $healthy = $true; break } catch { Start-Sleep -Seconds 10 }",
                "  }",
                "  if (-not $healthy) { throw \"Health check timed out: $healthUrl\" }",
                "}",
            ]
        )
    else:
        verify.append(
            "throw 'No public health URL is available; verify this private deployment from inside its network.'"
        )

    provider_command = {"aws": "aws", "azure": "az", "gcp": "gcloud"}.get(
        provider, ""
    )
    scripts = {
        "doctor.ps1": "$ErrorActionPreference = 'Stop'\n"
        + root
        + "Get-Command tofu, docker | Out-Null\n"
        + (f"Get-Command {provider_command} | Out-Null\n" if provider_command else "")
        + "tofu version\ndocker version\n",
        "prepare-images.ps1": "\n".join(prepare) + "\n",
        "plan.ps1": "$ErrorActionPreference = 'Stop'\n"
        + root
        + load_runtime
        + "tofu init\nif ($LASTEXITCODE -ne 0) { throw 'tofu init failed.' }\n"
        + "tofu validate\nif ($LASTEXITCODE -ne 0) { throw 'tofu validate failed.' }\n"
        + "$planArgs = @('plan', '-out=easydep.tfplan') + @($args)\n"
        + "& tofu @planArgs\nif ($LASTEXITCODE -ne 0) { throw 'tofu plan failed.' }\n",
        "deploy.ps1": "$ErrorActionPreference = 'Stop'\n"
        + root
        + "if (-not (Test-Path easydep.tfplan)) { throw 'Run scripts/plan.ps1 first.' }\n"
        + "$applyArgs = @('apply') + @($args) + @('easydep.tfplan')\n"
        + "& tofu @applyArgs\nif ($LASTEXITCODE -ne 0) { throw 'tofu apply failed.' }\n",
        "verify.ps1": "\n".join(verify) + "\n",
        "destroy.ps1": "$ErrorActionPreference = 'Stop'\n"
        + root
        + load_runtime
        + "$destroyArgs = @('destroy') + @($args)\n"
        + "& tofu @destroyArgs\nif ($LASTEXITCODE -ne 0) { throw 'tofu destroy failed.' }\n",
    }
    scripts["smoke-test.ps1"] = (
        "$ErrorActionPreference = 'Stop'\n"
        "$scriptDir = $PSScriptRoot\n"
        "try {\n"
        "  & (Join-Path $scriptDir 'doctor.ps1')\n"
        "  & (Join-Path $scriptDir 'prepare-images.ps1')\n"
        "  & (Join-Path $scriptDir 'plan.ps1') '-input=false'\n"
        "  & (Join-Path $scriptDir 'deploy.ps1') '-auto-approve'\n"
        "  & (Join-Path $scriptDir 'verify.ps1')\n"
        "} finally {\n"
        "  if ($env:KEEP_RESOURCES -ne '1') { try { & (Join-Path $scriptDir 'destroy.ps1') '-auto-approve' } catch { Write-Error 'Automatic cleanup failed; inspect the OpenTofu state.' } }\n"
        "}\n"
    )
    return scripts


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


def _readme(provider: str) -> str:
    return f"""# EasyDep deployment package

This package targets **{provider.upper()}** and contains a deterministic OpenTofu module, a cloud-init template, Docker Compose runtime definition, and matching PowerShell/POSIX scripts. EasyDep never applies this module for you.

1. Install OpenTofu, Docker, and the {provider.upper()} CLI; authenticate locally.
2. Review `tofu/terraform.tfvars.example`, copy it to `tofu/terraform.tfvars`, choose a unique `resource_prefix`, and provide only local deployment inputs. Do not commit it.
3. Copy `runtime/.env.example` to `runtime/.env` and place only non-secret runtime settings there. `plan.*` passes that file to the active cloud-init template. Use the selected cloud secret service for passwords, API keys, and private keys; never put them in `runtime/.env`, cloud-init, Compose, or `terraform.tfvars`.
4. Run `scripts/doctor.*`, then `scripts/prepare-images.*`. The latter creates only the generated registries first, builds and pushes the application image, and records its immutable digest in `runtime/image-digests.env`.
5. Run `scripts/plan.sh` or `scripts/plan.ps1`; it reads the recorded image digest and creates `tofu/easydep.tfplan`. Review that plan, then run `scripts/deploy.*` to apply that exact file.
6. Run `scripts/verify.*`. It reads every generated public health URL and waits up to ten minutes for cloud-init and the application to become ready. Docker logs are available on the VM through `docker compose logs` and `/var/lib/docker/containers`.
7. Run `scripts/destroy.*` when the environment is no longer required. Retained data disks are intentionally not deleted automatically.

For a disposable public-ingress environment without retained disks, `scripts/smoke-test.*` runs steps 4-7 and destroys resources even when verification fails. A private-only deployment must be verified from inside its network instead. Set `KEEP_RESOURCES=1` only when you intentionally want to inspect a failed deployment and accept its cost.

The generated OpenTofu module passes the same selected ResourcePlan values (provider, region, zones, ports, health path, disks, image digests, VM SKU, and replica count) to AWS `user_data`, Azure `custom_data`, or GCP instance metadata.
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
        scripts = package / "scripts"
        tofu.mkdir(parents=True)
        runtime.mkdir()
        scripts.mkdir()
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
        env_example = "\n".join(f"# {description}\n{name}=" for name, description in envs) + "\n"
        _write_text(runtime / ".env.example", env_example)
        _write_text(tofu / "terraform.tfvars.example", _tfvars_example(resource_plan))
        # This stable filename is a copy of the exact per-compute template that
        # main.tf passes as user_data/custom_data/instance metadata.
        _write_text(tofu / "cloud-init.yaml.tftpl", cloud_init)
        _format_open_tofu(tofu)
        for name, content in {
            **_shell_scripts(resource_plan, rendered_tofu),
            **_powershell_scripts(resource_plan, rendered_tofu),
        }.items():
            _write_text(scripts / name, content)
        _write_text(package / "README.md", _readme(str(resource_plan.get("provider") or "")))
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
