"""설계 산출물 → **소스코드·테스트·배포 파일**을 생성한다(구현 에이전트를 돌려서).

    python -m app.core.cloudkb.tools.build_implementation app/core/cloudkb/appkb/samples/<이름>

`build_sample`(요구사항)·`build_design`(설계)에 이어 사슬의 **구현 단계**를
파일에서 돌린다. `<이름>/design/`의 클래스·시퀀스·ERD·OpenAPI·cloud.json을
읽어 시스템 구현 에이전트(`app/implementation/`)로 Java/Spring 소스 + 테스트 +
배포 파일을 생성·컴파일·검증한다.

## 남의 코드를 부르기만 한다

`app/implementation/`은 **팀원 코드이고 고치지 않는다.** 서빙 경로(worker →
prototype_client)가 하는 것과 똑같이 엔진 CLI를 **서브프로세스**로 부른다
(`app.implementation.engine.cli`). 그 경로는 순수 파일 기반이라 MySQL을
안 거친다 — DB는 서빙 래퍼(worker.py)만 건드린다.

세 호출(prototype_client.py와 동형):

    cli <job.json>                               generate — puml→BCE Java·
                                                 OpenAPI→Spring 선생성 + 컴파일 +
                                                 control 태스크 계획. output=run_root
    cli plan-workflow <run_root> <job.json>      나머지 페이즈 계획 + 승인 요청
    cli run-workflow  <run_root> <job.json>      한 페이즈 실행(승인 필요)
        --approval <approval.json>

## 승인은 한 장이면 된다 (위임)

`plan-workflow`가 전 페이즈를 미리 계획하므로, 첫 승인 요청의 requestId로
`delegatedRepairApprovals: true` + 전 태스크 id를 범위로 하는 승인 하나를 쓰면
이후 라운드가 그 위임으로 통과한다. HITL 게이트를 사람 없이 넘는 자리이므로,
**무엇을 승인했는지**(전송 요청·태스크·해시)가 approval.json과 RUN.json에 남는다.

## 재개

같은 입력이면 run_root가 같고(input 해시), 워크플로가 매 태스크 전후로
체크포인트를 남긴다(reports/workflow-state.json). 끊겨도 다시 돌리면 성공한
태스크는 건너뛰고 이어 간다 — 이 저장소의 무인 측정 규율 그대로.

## 비용·시간

구현은 OpenHands LLM 호출을 **태스크마다** 한다(페이즈 6~7 + 수리). 수십 분이
걸릴 수 있고 NIM 예산을 쓴다. 백그라운드로 띄우고 로그를 남기는 편이 낫다.

## 환경

OpenHands SDK·JDK 21·Gradle·OpenAPI Generator JAR·puml2code가 부트스트랩돼
있어야 한다(`scripts/bootstrap-implementation-tools.*`). 엔진은 NIM 키를
`NVIDIA_API_KEY`에서 읽는데 이 저장소의 `.env`는 `API_KEY`로 갖고 있어, 여기서
서브프로세스 환경에 이름을 맞춰 넘긴다(.env는 안 고친다).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from ..kbcommon.console import use_utf8
from .provenance import git_head

#: 클래스 다이어그램의 **행위 연관선**을 가리는 정규식(`A --> B : 문장.` 꼴).
#: 설계 에이전트는 클래스 정의 뒤에 시퀀스에서 온 방향 화살표(-->·..>)를 덧붙이는데,
#: 이건 PlantUML로 렌더는 되지만(설계 검증 통과) **BCE 파서(puml2code)가 못 읽어**
#: 구현 단계를 막는다. 문법상 directional 화살표는 어차피 null을 반환해 BCE 코드에
#: 기여하지 않으므로(plantuml.pegjs `directional { return null }`), 다리에서 이
#: 장식 줄만 걷어 낸다 — **커밋된 설계 실물은 안 고친다**(정규화 사본만 엔진에 준다).
#: 구조 관계(<|--·*--·o-- = extends·composition·aggregation)는 |*o 마커로 남긴다.
_ASSOCIATION_LINE = re.compile(
    r"^\s*[A-Za-z_]\w*\s+"          # 원본 이름
    r"(?:<?[-.]{1,}>|<[-.]{1,})"    # directional 화살표(-->·->·..>·<--·<..)
    r"\s+[A-Za-z_]\w*",             # 대상 이름
)

#: **타입 없는 필드**(`- passwordHash`). BCE 추출이 필드 타입을 안 내면(gpt-oss-120b
#: 실측) puml2code가 `private void passwordHash;`로 내 Gradle 컴파일이 죽는다.
#: 메서드(괄호 있음)·타입 있는 필드(`: String`)는 안 걸린다. 다리에서 String으로
#: 채워 컴파일을 세운다 — 크루드하지만 **문서화된 어댑터 가정**이고, 커밋 실물은
#: 안 고친다. 정확한 타입은 설계 에이전트(또는 서빙의 피드백 게이트)의 몫이다.
_UNTYPED_FIELD = re.compile(r"^(\s*[-+#~]\s*[A-Za-z_]\w*)\s*$")

#: **파라미터 있는 메서드**(`+ requestPhotos(reportId)`). BCE 추출이 파라미터를
#: 이름만 내면(타입 없이) puml2code가 그 이름을 **타입으로** 읽어 `void
#: method(reportId param0)` → 'cannot find symbol: class reportId'로 컴파일이 죽는다.
#: 파라미터를 비워 무인자로 정규화한다(서명 축소 — 문서화된 가정, 실물은 안 고침).
_METHOD_WITH_PARAMS = re.compile(r"^(\s*[-+#~]\s*[A-Za-z_]\w*\s*)\([^)]*\)(.*)$")


def _normalize_class_diagram(text: str) -> tuple[str, int, int, int]:
    """BCE 구현 입력으로 정규화한다.

    (텍스트, 제거 연관선, 타입 채운 필드, 무인자화한 메서드). 셋 다 gpt-oss-120b의
    느슨한 BCE를 컴파일 가능한 Java로 잇는 어댑터 정규화다 — 구조 관계선(|*o)은 남긴다.
    """
    kept: list[str] = []
    dropped = typed = voided = 0
    for line in text.splitlines():
        if _ASSOCIATION_LINE.match(line) and not any(
                m in line for m in ("|", "*", "o--", "--o")):
            dropped += 1
            continue
        method = _METHOD_WITH_PARAMS.match(line)
        if method:
            new = f"{method.group(1)}(){method.group(2)}"
            if new != line:
                voided += 1
            kept.append(new)
            continue
        field = _UNTYPED_FIELD.match(line)
        if field:
            kept.append(f"{field.group(1)}: String")
            typed += 1
            continue
        kept.append(line)
    return "\n".join(kept), dropped, typed, voided

#: 저장소 루트 — 이 파일에서 네 단계 위(app/core/cloudkb/tools/build_implementation.py).
REPO = Path(__file__).resolve().parents[4]

#: 설계 산출물 → job.json inputs 키. 원문(puml/json)을 준다 — 선생성(BCE Java·
#: Spring)은 엔진이 generate 단계에서 한다. required는 bceClass·openapi 둘.
_INPUTS = {
    "bceClass": "design/class_diagram.puml",
    "openapi": "design/api_spec.json",
    "sequence": "design/sequence_diagram.puml",
    "erd": "design/erd.puml",
    "cloud": "design/cloud.json",
}
_REQUIRED = ["bceClass", "openapi"]
_TERMINAL = {"COMPLETE", "FAILED", "NEEDS_INPUT", "NEEDS_PLANNER"}


def _read_env_file() -> dict[str, str]:
    """.env의 KEY=VALUE를 읽는다. 엔진이 os.environ에서 직접 읽으므로 여기서
    서브프로세스 환경에 실어 준다(pydantic settings는 이 프로세스만 채운다)."""
    env_path = REPO / ".env"
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _subprocess_env() -> dict[str, str]:
    import os

    env = os.environ.copy()
    dotenv = _read_env_file()
    # 엔진이 찾는 이름으로 맞춘다(.env는 API_KEY/BASE_URL/MODEL).
    if dotenv.get("API_KEY"):
        env.setdefault("NVIDIA_API_KEY", dotenv["API_KEY"])
    if dotenv.get("BASE_URL"):
        env.setdefault("LLM_BASE_URL", dotenv["BASE_URL"])
    env.setdefault("GRADLE_USER_HOME", str(REPO / ".easydep" / "gradle-cache"))
    return env


def _agent_block(dotenv: dict[str, str]) -> dict[str, str]:
    # 엔진/litellm은 nvidia_nim/ 접두 모델 id를 쓴다. .env MODEL은 접두 없음.
    model = dotenv.get("MODEL", "openai/gpt-oss-120b")
    if not model.startswith("nvidia_nim/"):
        model = f"nvidia_nim/{model}"
    return {
        "mode": "openhands",
        "model": model,
        "baseUrl": dotenv.get("BASE_URL", "https://integrate.api.nvidia.com/v1"),
    }


def _rel(path: Path) -> str:
    return path.resolve().relative_to(REPO).as_posix()


def _cli(args: list, env: dict[str, str]) -> dict:
    """엔진 CLI를 서브프로세스로 부른다(prototype_client._call과 동형).
    stdout의 **마지막 JSON 줄**이 결과다."""
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "app.implementation.engine.cli",
         *[str(a) for a in args]],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-4000:]
        raise RuntimeError(f"cli {args[0]} 실패(rc={proc.returncode}):\n{tail}")
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise RuntimeError(f"cli {args[0]}: JSON 결과 줄이 없다\n{proc.stdout[-2000:]}")


def _write_job(root: Path, work: Path, dotenv: dict[str, str]) -> tuple[Path, dict]:
    inputs = {}
    norm = {"associationsStripped": 0, "fieldsTyped": 0, "methodParamsVoided": 0}
    for key, rel in _INPUTS.items():
        path = root / rel
        if not path.exists():
            continue
        if key == "bceClass":
            # 커밋된 실물은 안 고친다 — BCE 파서·컴파일용 정규화 사본만 워크스페이스에.
            normalized, stripped, typed, voided = _normalize_class_diagram(
                path.read_text(encoding="utf-8"))
            norm = {"associationsStripped": stripped, "fieldsTyped": typed,
                    "methodParamsVoided": voided}
            norm_path = work / "design-normalized" / "class_diagram.puml"
            norm_path.parent.mkdir(parents=True, exist_ok=True)
            norm_path.write_text(normalized, encoding="utf-8")
            inputs[key] = _rel(norm_path)
        else:
            inputs[key] = _rel(path)
    missing = [k for k in _REQUIRED if k not in inputs]
    if missing:
        raise SystemExit(
            f"구현의 필수 입력이 없습니다: {missing}. build_design을 먼저 돌리십시오.")
    job = {
        "name": f"easydep-{root.name}",
        "jobType": "INITIAL_IMPLEMENTATION",
        "feedback": "",
        "workspaceRoot": str(REPO),
        "inputs": inputs,
        "requiredInputs": _REQUIRED,
        "outputRoot": _rel(work / "generated" / "runs"),
        "generation": {
            "basePackage": f"com.example.{root.name.replace('-', '')}",
            "allowAssumptions": True,
        },
        "verification": {"compile": True},
        "tools": {
            "puml2codeRoot": "app/implementation/tools/puml2code-bce",
            "openapiGeneratorJar":
                "app/implementation/tools/openapi-generator/"
                "openapi-generator-cli-7.24.0.jar",
        },
        "agent": _agent_block(dotenv),
    }
    job_path = work / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return job_path, norm


def _write_approval(run_root: Path, work: Path) -> Path:
    """첫 전송 요청 + 매니페스트에서 위임 승인 한 장을 쓴다(worker.approve 동형)."""
    req = json.loads(
        (run_root / "reports" / "external-transmission-request.json")
        .read_text(encoding="utf-8"))
    manifest = json.loads(
        (run_root / "reports" / "run-manifest.json").read_text(encoding="utf-8"))
    task_ids = sorted(str(t["task_id"])
                      for t in manifest.get("implementation_tasks", []))
    approval = {
        "requestId": req["requestId"],
        "approved": True,
        "approvedAt": datetime.now(UTC).isoformat(),
        "approvedBy": "build_implementation.py",
        "delegatedRepairApprovals": True,
        "delegationScope": {
            "runId": run_root.name,
            "inputHash": manifest.get("input_hash"),
            "initialTaskIds": task_ids,
            "maxRepairRounds": 3,
            "maxTaskAttempts": 50,
        },
    }
    approval_path = work / "approval.json"
    approval_path.write_text(json.dumps(approval, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return approval_path, req["requestId"], len(task_ids)


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(
        prog="build_implementation",
        description="설계 산출물 → 소스코드·테스트·배포 파일 (구현 에이전트 실행)")
    parser.add_argument("sample_dir")
    parser.add_argument("--max-phases", type=int, default=20,
                        help="run-workflow 호출 상한(페이즈 수 + 여유)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="FAILED 태스크를 재시도한다")
    args = parser.parse_args(argv)

    root = Path(args.sample_dir).resolve()
    dotenv = _read_env_file()
    if not dotenv.get("API_KEY"):
        print("⚠ .env에 API_KEY가 없습니다 — 구현 에이전트가 NIM 키 없이는 못 돕니다.",
              file=sys.stderr)
        return 2

    work = REPO / ".easydep" / "impl-runs" / root.name
    work.mkdir(parents=True, exist_ok=True)
    env = _subprocess_env()

    print(f"표본: {root.name}")
    job_path, norm = _write_job(root, work, dotenv)
    print(f"모델: {_agent_block(dotenv)['model']}")
    if any(norm.values()):
        print(f"  · 설계→BCE 정규화(커밋 실물 안 고침): 연관선 "
              f"{norm['associationsStripped']}줄 제거 · 타입 없는 필드 "
              f"{norm['fieldsTyped']}개 String · 메서드 파라미터 "
              f"{norm['methodParamsVoided']}개 무인자화")
    print()

    log: list[dict] = []
    started = datetime.now(UTC).isoformat()

    # 1. generate — 선생성(BCE·Spring) + 컴파일 + control 태스크
    print("  → generate (puml→BCE·OpenAPI→Spring·컴파일) …", end=" ", flush=True)
    gen = _cli([job_path], env)
    run_root = Path(gen["output"])
    print(f"{gen.get('status')}  run={run_root.name}")
    log.append({"step": "generate", "status": gen.get("status"),
                "runRoot": str(run_root)})

    # 2. plan-workflow — 나머지 페이즈 계획 + 첫 승인 요청
    print("  → plan-workflow …", end=" ", flush=True)
    state = _cli(["plan-workflow", run_root, job_path], env)
    print(f"{state.get('status')}  phase={state.get('currentPhase')}")

    # 3. 위임 승인 한 장
    approval_path, request_id, n_tasks = _write_approval(run_root, work)
    print(f"  · 승인 작성 — requestId={request_id[:12]}… · 태스크 {n_tasks}건 위임\n")

    # 4. run-workflow 루프 — 페이즈마다 한 번, terminal까지
    final = state
    for i in range(args.max_phases):
        run_args = ["run-workflow", run_root, job_path, "--approval", approval_path]
        if args.retry_failed:
            run_args.append("--retry-failed")
        print(f"  → run-workflow #{i + 1} …", end=" ", flush=True)
        final = _cli(run_args, env)
        status, phase = final.get("status"), final.get("currentPhase")
        print(f"{status}  phase={phase}")
        log.append({"step": f"run-workflow#{i + 1}", "status": status,
                    "phase": phase})
        if status in _TERMINAL:
            break
    else:
        print("  ⚠ 페이즈 상한 도달 — 아직 안 끝났습니다(재개 가능).")

    # 5. 산출물 위치 + 기록
    app_root = run_root / "application"
    conformance = final.get("sourceDesignConformance")
    print(f"\n상태: {final.get('status')}"
          + (f" · 적합성: {conformance}" if conformance else ""))
    print(f"소스·테스트·배포: {app_root}")

    (work / "RUN.json").write_text(json.dumps({
        "startedAt": started,
        "finishedAt": datetime.now(UTC).isoformat(),
        "sample": root.name,
        "code": git_head(),
        "runRoot": str(run_root),
        "applicationRoot": str(app_root),
        "designToBceNormalization": norm,
        "finalStatus": final.get("status"),
        "sourceDesignConformance": conformance,
        "requestId": request_id,
        "delegatedTasks": n_tasks,
        "log": log,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if final.get("status") == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
