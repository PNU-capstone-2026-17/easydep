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


#: gradle가 **테스트 실행용으로 포크하는 JVM**의 표식. 이 프로세스만 재면
#: 컴파일러·gradle 데몬의 빌드 CPU가 빠지고 **앱-처리 CPU**가 남는다 — 통합
#: 테스트(SpringBootTest)가 앱 컨텍스트를 여기서 띄우고 컨트롤러를 태운다.
_TEST_JVM_MARKERS = ("GradleWorkerMain", "Gradle Test Executor")


def _is_test_jvm(proc, cache_cmd: dict) -> bool:
    if proc.pid in cache_cmd:
        return cache_cmd[proc.pid]
    try:
        cmd = " ".join(proc.cmdline())
    except Exception:  # noqa: BLE001 — 못 읽으면 미확정(캐시 안 함, 다음에 재시도)
        return False
    is_test = any(m in cmd for m in _TEST_JVM_MARKERS)
    cache_cmd[proc.pid] = is_test
    return is_test


def _sample_tree(root_pid: int, cache: dict, cache_cmd: dict) -> tuple:
    """프로세스 트리 한 표본. (전체 RSS, 전체 CPU, 테스트JVM RSS, 테스트JVM CPU).

    `cache`(pid→psutil.Process)를 **표본 간 유지**해야 cpu_percent가 직전 호출
    이후의 사용률을 누적한다 — 매번 새 객체를 만들면 항상 0이 나온다(버그였다).
    테스트JVM 부분집합(`_is_test_jvm`)이 **빌드 CPU를 뺀 앱-처리 CPU**다.
    """
    import psutil

    try:
        root = cache.get(root_pid) or psutil.Process(root_pid)
        cache[root_pid] = root
        live = [root, *root.children(recursive=True)]
    except (psutil.Error, OSError):
        # WinError 1455(페이징 파일 부족) 등 일시 오류로 트리 열거가 실패해도
        # 한 표본만 건너뛴다 — 전체 측정을 죽이지 않는다.
        return 0.0, 0.0, 0.0, 0.0
    rss = cpu = trss = tcpu = 0.0
    for p in live:
        proc = cache.get(p.pid)
        if proc is None:
            proc = p
            cache[p.pid] = proc
            proc.cpu_percent(interval=None)  # 이 프로세스의 기준선을 잡는다
        try:
            r = proc.memory_info().rss / (1024 * 1024)
            c = proc.cpu_percent(interval=None) / 100.0
        except psutil.Error:
            continue
        rss += r
        cpu += c
        if _is_test_jvm(proc, cache_cmd):
            trss += r
            tcpu += c
    return rss, cpu, trss, tcpu


def _measure(app_root: Path) -> dict:
    """gradle test를 돌리며 프로세스 트리 자원의 피크를 샘플한다."""
    import psutil

    # **cleanTest**로 테스트를 강제 재실행한다 — UP-TO-DATE면 gradle이 스킵해
    # 테스트 JVM이 안 포크되고, 그러면 앱-처리 CPU를 못 잰다(측정할 대상이 없다).
    cmd = [str(_GRADLEW), "-p", str(app_root), "cleanTest", "test", "--no-daemon"]
    env = {**__import__("os").environ, "GRADLE_USER_HOME": str(_GRADLE_HOME)}
    proc = subprocess.Popen(cmd, cwd=REPO, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")

    peak_rss = peak_cpu = 0.0
    peak_trss = peak_tcpu = 0.0
    samples = 0
    stop = threading.Event()
    cache: dict = {}
    cache_cmd: dict = {}

    def sampler() -> None:
        nonlocal peak_rss, peak_cpu, peak_trss, peak_tcpu, samples
        # 첫 호출은 CPU 기준선만 잡는다(0을 돌려줌).
        _sample_tree(proc.pid, cache, cache_cmd)
        time.sleep(0.5)
        while not stop.is_set():
            rss, cpu, trss, tcpu = _sample_tree(proc.pid, cache, cache_cmd)
            peak_rss = max(peak_rss, rss)
            peak_cpu = max(peak_cpu, cpu)
            peak_trss = max(peak_trss, trss)
            peak_tcpu = max(peak_tcpu, tcpu)
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
        # 테스트 실행 JVM만 — 빌드 CPU 뺀 앱-처리 자원.
        "appPeakRssMiB": round(peak_trss, 1),
        "appPeakVCpu": round(peak_tcpu, 2),
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

    app_cpu = result["appPeakVCpu"]
    app_rss_gib = result["appPeakRssMiB"] / 1024
    tree_rss_gib = result["peakRssMiB"] / 1024
    print(f"  전체 트리 피크: {result['peakVCpu']:g} vCPU · "
          f"{tree_rss_gib:.2f} GiB (빌드 포함)")
    print(f"  **앱-처리(테스트 JVM) 피크**: {app_cpu:g} vCPU · "
          f"{app_rss_gib:.2f} GiB (표본 {result['samples']}) "
          f"· test rc={result['testExitCode']}")

    # **테스트 실행 JVM만** 싣는다 — gradle 데몬·컴파일러의 빌드 CPU를 뺀
    # 앱-처리 자원이다. 통합 테스트가 앱 컨텍스트를 이 JVM에서 띄우고 컨트롤러를
    # 태우므로, 그 CPU는 (테스트 부하의) 서빙 CPU다. 독립 부팅 없이 잰다.
    # 여전히 프로덕션 부하가 아니라 테스트 부하라 production_load=False.
    if app_cpu <= 0 and app_rss_gib <= 0:
        print("  ⚠ 테스트 실행 JVM을 못 식별했다(인프로세스 실행일 수 있음) — "
              "capacity 미기록. 수치를 지어내지 않는다.", file=sys.stderr)
        return 1
    capacity = {
        "schemaVersion": "easydep-capacity/v1alpha1",
        "vcpu": app_cpu,
        "mem_gib": round(app_rss_gib, 3),
        "under": "unit and integration tests (forked test-executor JVM)",
        "evidence": f"peak of the gradle test-executor JVM over "
                    f"{result['samples']} samples (test rc="
                    f"{result['testExitCode']}); build CPU (gradle daemon, "
                    f"compiler) is excluded. **This CPU peak is dominated by "
                    f"Spring context startup (classpath scan, bean wiring, "
                    f"Flyway, JIT warmup) and parallel test execution — a "
                    f"transient startup spike, not steady-state serving "
                    f"throughput.** Read it as: the app briefly needs ~this many "
                    f"cores. Steady serving CPU needs a load probe against a "
                    f"booted app (load_probe.py); field-report did not boot "
                    f"standalone. Memory is the app's test-JVM RSS.",
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
    print(f"  → 앱-처리 피크: {app_cpu:g} vCPU · {app_rss_gib:.2f} GiB. 빌드 CPU는 "
          "뺐다. ※ CPU는 Spring 시동 스파이크가 지배 — 서빙 처리량이 아니다"
          "(정상 서빙 CPU는 load_probe).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
