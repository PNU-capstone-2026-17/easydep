"""생성된 앱을 **실제로 돌려** 자원 하한을 잰다 — 공식이 아니라 측정.

    python -m app.core.cloudkb.tools.measure_capacity app/core/cloudkb/appkb/samples/<이름>

## 왜 이게 필요한가

이 저장소는 'N명 → vCPU M' 공식을 두 번 조사 끝에 **배제**하고
(`sizingkb/__init__.py`), 정직한 답을 *"부하 테스트로 검증하라"*로 적어 뒀다.
그런데 그 측정 능력을 안 만들었다 — 사슬이 실제 앱을 낳기 전에는 잴 대상이
없었기 때문이다. `build_implementation`이 앱을 낳은 지금, 그 앱을 재면 된다.
depkb가 의존을 컨트롤 플레인으로 잰 것과 같은 자리: 가정 대신 실측.

## 무엇을 재나 (두 측정점 중 첫째 — 부산물)

**단위·통합 테스트가 도는 동안** JVM 프로세스 트리의 피크 RSS(메모리)와 CPU를
잰다. 테스트는 앱을 부팅해 빈을 다 올리고 흐름을 태우므로, 그때의 자원이
**하한의 하한**이다 — 서빙 부하가 아니라 테스트 부하라, 프로덕션 하한이 아니라
"이만큼은 쓴다"는 바닥이다. 그 사실을 `production_load: false`로 함께 적는다.
둘째 측정점(scale 부하의 부하 프로브)은 인수 테스트 때 얹는다.

## 산출물

`<이름>/design/capacity.json` — `sizing_floor.Measurement`이 읽는 필드
(vcpu·mem_gib·under·evidence·production_load) + 테스트 결과·표본 수.
plan(design_tools)이 이걸 읽어 measured 층으로 싣는다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from ..kbcommon.console import use_utf8
from .provenance import git_head

REPO = Path(__file__).resolve().parents[4]
_GRADLEW = REPO / "app" / "implementation" / "tools" / "gradle" / "gradlew.bat"
_GRADLE_HOME = REPO / ".easydep" / "gradle-cache"


def _app_root(sample: Path) -> Path | None:
    run_json = REPO / ".easydep" / "impl-runs" / sample.name / "RUN.json"
    if not run_json.exists():
        return None
    app = json.loads(run_json.read_text(encoding="utf-8")).get("applicationRoot")
    return Path(app) if app else None


def _sample_tree(root_pid: int, cache: dict) -> tuple[float, float]:
    """프로세스 트리(부모+자식)의 (RSS MiB 합, CPU 코어 합) 한 표본.

    `cache`(pid→psutil.Process)를 **표본 간 유지**해야 cpu_percent가 직전 호출
    이후의 사용률을 누적해 준다 — 매번 새 객체를 만들면 항상 0이 나온다(버그였다).
    """
    import psutil

    try:
        root = cache.get(root_pid) or psutil.Process(root_pid)
        cache[root_pid] = root
        live = [root, *root.children(recursive=True)]
    except psutil.Error:
        return 0.0, 0.0
    rss = cpu = 0.0
    for p in live:
        proc = cache.get(p.pid)
        if proc is None:
            proc = p
            cache[p.pid] = proc
            proc.cpu_percent(interval=None)  # 이 프로세스의 기준선을 잡는다
        try:
            rss += proc.memory_info().rss / (1024 * 1024)
            cpu += proc.cpu_percent(interval=None) / 100.0
        except psutil.Error:
            continue
    return rss, cpu


def _measure(app_root: Path) -> dict:
    """gradle test를 돌리며 프로세스 트리 자원의 피크를 샘플한다."""
    import psutil

    cmd = [str(_GRADLEW), "-p", str(app_root), "test", "--no-daemon"]
    env = {**__import__("os").environ, "GRADLE_USER_HOME": str(_GRADLE_HOME)}
    proc = subprocess.Popen(cmd, cwd=REPO, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")

    peak_rss = peak_cpu = 0.0
    samples = 0
    stop = threading.Event()
    cache: dict = {}

    def sampler() -> None:
        nonlocal peak_rss, peak_cpu, samples
        # 첫 호출은 CPU 기준선만 잡는다(0을 돌려줌).
        _sample_tree(proc.pid, cache)
        time.sleep(0.5)
        while not stop.is_set():
            rss, cpu = _sample_tree(proc.pid, cache)
            peak_rss = max(peak_rss, rss)
            peak_cpu = max(peak_cpu, cpu)
            samples += 1
            time.sleep(0.5)

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    out, _ = proc.communicate()
    stop.set()
    thread.join(timeout=2)

    tail = "\n".join((out or "").splitlines()[-8:])
    return {
        "testExitCode": proc.returncode,
        "peakRssMiB": round(peak_rss, 1),
        "peakVCpu": round(peak_cpu, 2),
        "samples": samples,
        "gradleTail": tail,
    }


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(
        prog="measure_capacity",
        description="생성된 앱을 테스트로 돌려 자원 하한을 측정")
    parser.add_argument("sample_dir")
    args = parser.parse_args(argv)

    sample = Path(args.sample_dir).resolve()
    app_root = _app_root(sample)
    if app_root is None or not (app_root / "build.gradle").exists() \
            and not (app_root / "build.gradle.kts").exists():
        print("생성된 앱이 없습니다 — build_implementation을 먼저 돌리십시오.",
              file=sys.stderr)
        return 2

    print(f"표본: {sample.name}")
    print(f"앱: {app_root}")
    print("→ gradle test (자원 샘플링 중) …", flush=True)
    result = _measure(app_root)

    peak_rss_gib = result["peakRssMiB"] / 1024
    print(f"  피크(원자료): {result['peakVCpu']:g} vCPU · "
          f"{peak_rss_gib:.2f} GiB (표본 {result['samples']})"
          f" · test rc={result['testExitCode']}")

    # **CPU는 하한으로 안 싣는다.** gradle test의 피크 CPU는 컴파일·테스트를
    # 병렬로 도는 빌드 동시성이지 앱이 요청을 처리하는 CPU가 아니다 — 그걸
    # 서빙 하한으로 주장하면 이 저장소가 배격하는 근거 없는 수치가 된다.
    # 서빙 CPU는 부하 프로브(둘째 측정점)가 잰다. 메모리는 앱이 그 안에서
    # 컨텍스트를 올리므로 **거친 하한의 하한**으로 싣되, 그 성질을 밝힌다.
    capacity = {
        "schemaVersion": "easydep-capacity/v1alpha1",
        "vcpu": 0,   # 서빙 CPU는 이 측정점이 못 잰다 — 부하 프로브가 채운다
        "mem_gib": round(peak_rss_gib, 3),
        "under": "unit and integration tests (gradle test process tree)",
        "evidence": f"peak memory of {result['samples']} samples of the JVM/"
                    f"build process tree during gradle test (test rc="
                    f"{result['testExitCode']}); a rough lower bound on the "
                    f"running app, which loads its context within this tree. "
                    f"Serving CPU is not measured here — the raw test-time CPU "
                    f"peak ({result['peakVCpu']:g} vCPU) is build concurrency, "
                    f"not serving load; measure it with a load probe.",
        "production_load": False,
        "measuredAt": datetime.now(UTC).isoformat(),
        "code": git_head(),
        "raw": result,
    }
    out_path = sample / "design" / "capacity.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(capacity, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"기록: {out_path}")
    print(f"  → 메모리 하한(하한의 하한): {peak_rss_gib:.2f} GiB. "
          "서빙 CPU는 부하 프로브(둘째 측정점) 몫 — 빌드 CPU는 안 싣는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
