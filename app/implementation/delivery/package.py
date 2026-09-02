"""선택된 ResourcePlan에서 사람이 실행할 deployment package를 만든다."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..domain.implementation_ir import remove_readonly


def _label(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or "workload")).upper()


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
    lines = ["# Copy this file to terraform.tfvars and fill deployment inputs locally."]
    provider = str(resource_plan.get("provider") or "")
    if provider == "aws":
        lines.extend(['boot_image_id = "ami-REPLACE_ME"', 'ssh_public_key = ""'])
    elif provider == "azure":
        lines.extend(['subscription_id = ""', 'ssh_public_key = ""'])
    elif provider == "gcp":
        lines.append('project_id = ""')
    for workload in resource_plan.get("workloads") or []:
        if (workload.get("artifact") or {}).get("kind") == "generatedApplication":
            lines.append(f'image_digest_{_label(workload.get("id"))} = ""')
    return "\n".join(lines) + "\n"


def _shell_scripts() -> dict[str, str]:
    root = 'ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"\ncd "$ROOT/tofu"\n'
    prefix = "#!/usr/bin/env bash\nset -euo pipefail\n" + root
    return {
        "plan.sh": prefix
        + 'if [ -f "$ROOT/runtime/.env" ]; then export TF_VAR_runtime_env="$(cat "$ROOT/runtime/.env")"; fi\n'
        + 'if [ -f "$ROOT/runtime/image-digests.env" ]; then set -a; . "$ROOT/runtime/image-digests.env"; set +a; fi\n'
        + "tofu init\ntofu validate\ntofu plan -out=easydep.tfplan \"$@\"\n",
        "deploy.sh": prefix
        + '[ -f easydep.tfplan ] || { echo "Run scripts/plan.sh first." >&2; exit 1; }\n'
        + "tofu apply \"$@\" easydep.tfplan\n",
        "verify.sh": prefix + "tofu output -json\n: \"${HEALTH_URL:?set HEALTH_URL from the generated health_url output}\"\ncurl --fail --silent --show-error \"$HEALTH_URL\"\n",
        "destroy.sh": prefix + "tofu destroy \"$@\"\n",
        "build-and-push.sh": (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            ": \"${IMAGE_URI:?set IMAGE_URI to the selected registry repository and tag}\"\n"
            ": \"${IMAGE_DIGEST_VARIABLE:?set IMAGE_DIGEST_VARIABLE, for example image_digest_WEB}\"\n"
            "case \"$IMAGE_DIGEST_VARIABLE\" in image_digest_[A-Z0-9_]*) ;; *) echo 'IMAGE_DIGEST_VARIABLE must be an image_digest_<WORKLOAD> Terraform variable.' >&2; exit 1 ;; esac\n"
            "ROOT=\"$(CDPATH= cd -- \"$(dirname -- \"$0\")/../..\" && pwd)\"\n"
            "docker build --pull -t \"$IMAGE_URI\" \"$ROOT\"\n"
            "PUSH_OUTPUT=$(docker push \"$IMAGE_URI\" 2>&1)\n"
            "printf '%s\\n' \"$PUSH_OUTPUT\"\n"
            "IMAGE_DIGEST=$(printf '%s\\n' \"$PUSH_OUTPUT\" | sed -n 's/.*digest: \\(sha256:[0-9a-f]\\{64\\}\\).*/\\1/p' | tail -1)\n"
            "[ -n \"$IMAGE_DIGEST\" ] || { echo 'docker push did not report an immutable digest' >&2; exit 1; }\n"
            "DIGEST_FILE=\"$ROOT/deployment/runtime/image-digests.env\"\n"
            "mkdir -p \"$(dirname \"$DIGEST_FILE\")\"\n"
            "if [ -f \"$DIGEST_FILE\" ]; then grep -v \"^TF_VAR_${IMAGE_DIGEST_VARIABLE}=\" \"$DIGEST_FILE\" > \"$DIGEST_FILE.tmp\" || true; mv \"$DIGEST_FILE.tmp\" \"$DIGEST_FILE\"; fi\n"
            "printf 'TF_VAR_%s=%s\\n' \"$IMAGE_DIGEST_VARIABLE\" \"$IMAGE_DIGEST\" >> \"$DIGEST_FILE\"\n"
            "echo \"Recorded $IMAGE_DIGEST_VARIABLE in deployment/runtime/image-digests.env; run scripts/plan.sh next.\"\n"
        ),
    }


def _powershell_scripts() -> dict[str, str]:
    root = '$root = Resolve-Path (Join-Path $PSScriptRoot "..")\nSet-Location (Join-Path $root "tofu")\n'
    return {
        "plan.ps1": "$ErrorActionPreference = 'Stop'\n"
        + root
        + "$envFile = Join-Path $root 'runtime\\.env'\nif (Test-Path $envFile) { $env:TF_VAR_runtime_env = [IO.File]::ReadAllText($envFile) }\n$digestFile = Join-Path $root 'runtime\\image-digests.env'\nif (Test-Path $digestFile) { Get-Content -Encoding UTF8 $digestFile | ForEach-Object { if ($_ -match '^(TF_VAR_[A-Za-z0-9_]+)=(.+)$') { Set-Item -Path (\"Env:\" + $matches[1]) -Value $matches[2] } } }\ntofu init\ntofu validate\ntofu plan -out=easydep.tfplan @args\n",
        "deploy.ps1": "$ErrorActionPreference = 'Stop'\n"
        + root
        + "if (-not (Test-Path easydep.tfplan)) { throw 'Run scripts/plan.ps1 first.' }\ntofu apply @args easydep.tfplan\n",
        "verify.ps1": "$ErrorActionPreference = 'Stop'\n" + root + "tofu output -json\nif (-not $env:HEALTH_URL) { throw 'Set HEALTH_URL from the generated health_url output.' }\nInvoke-WebRequest -UseBasicParsing -Uri $env:HEALTH_URL | Out-Null\n",
        "destroy.ps1": "$ErrorActionPreference = 'Stop'\n" + root + "tofu destroy @args\n",
        "build-and-push.ps1": "$ErrorActionPreference = 'Stop'\nif (-not $env:IMAGE_URI) { throw 'Set IMAGE_URI to the selected registry repository and tag.' }\nif (-not $env:IMAGE_DIGEST_VARIABLE -or $env:IMAGE_DIGEST_VARIABLE -notmatch '^image_digest_[A-Z0-9_]+$') { throw 'Set IMAGE_DIGEST_VARIABLE to image_digest_<WORKLOAD>.' }\n$root = Resolve-Path (Join-Path $PSScriptRoot \"..\\\\..\")\ndocker build --pull -t $env:IMAGE_URI $root\n$pushOutput = docker push $env:IMAGE_URI 2>&1 | Out-String\nWrite-Host $pushOutput\n$match = [regex]::Match($pushOutput, 'digest: (sha256:[0-9a-f]{64})')\nif (-not $match.Success) { throw 'docker push did not report an immutable digest.' }\n$digestFile = Join-Path $root 'deployment\\runtime\\image-digests.env'\n$existing = if (Test-Path $digestFile) { Get-Content -Encoding UTF8 $digestFile | Where-Object { $_ -notmatch (\"^TF_VAR_\" + [regex]::Escape($env:IMAGE_DIGEST_VARIABLE) + '=') } } else { @() }\n$existing + (\"TF_VAR_\" + $env:IMAGE_DIGEST_VARIABLE + '=' + $match.Groups[1].Value) | Set-Content -Encoding UTF8 $digestFile\nWrite-Host \"Recorded $($env:IMAGE_DIGEST_VARIABLE) in deployment/runtime/image-digests.env; run scripts/plan.ps1 next.\"\n",
    }


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
2. Review `tofu/terraform.tfvars.example`, copy it to `tofu/terraform.tfvars`, and provide only local deployment inputs. Do not commit it.
3. Copy `runtime/.env.example` to `runtime/.env` and place only non-secret runtime settings there. `plan.*` passes that file to the active cloud-init template. Use the selected cloud secret service for passwords, API keys, and private keys; never put them in `runtime/.env`, cloud-init, Compose, or `terraform.tfvars`.
4. Prepare a writable tag in the selected registry, then run `scripts/build-and-push.*` with `IMAGE_URI` set to that tag and `IMAGE_DIGEST_VARIABLE` set to the matching `image_digest_<WORKLOAD>` variable in `tofu/terraform.tfvars.example`. The script records the pushed immutable digest in `runtime/image-digests.env`.
5. Run `scripts/plan.sh` or `scripts/plan.ps1`; it reads the recorded image digest and creates `tofu/easydep.tfplan`. Review that plan, then run `scripts/deploy.*` to apply that exact file.
6. Set `HEALTH_URL` from the generated outputs and run `scripts/verify.*`. Docker logs are available on the VM through `docker compose logs` and `/var/lib/docker/containers`.
7. Run `scripts/destroy.*` when the environment is no longer required. Retained data disks are intentionally not deleted automatically.

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
        for name, content in {**_shell_scripts(), **_powershell_scripts()}.items():
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
