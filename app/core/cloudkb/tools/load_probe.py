"""부하 프로브 — 용량 측정의 **둘째 측정점**: 서빙 vCPU를 잰다.

    python -m app.core.cloudkb.tools.load_probe app/core/cloudkb/appkb/samples/<이름>

## 첫째 측정점과 무엇이 다른가

`measure_capacity`(테스트 부산물)는 **메모리만** 하한으로 냈다 — gradle test의
피크 CPU는 앱의 서빙 CPU가 아니라 **빌드 동시성**이라 안 실었다. 이 프로브는
그 자리를 채운다: 생성 앱을 **독립 부팅**(bootJar)해 `scale` 부하로 HTTP를
때리고, **앱 프로세스만** 재서 서빙 CPU를 얻는다. gradle이 없으니 CPU가 깨끗하고,
`scale` 부하라 `production_load: True`로 실린다(sizing_floor가 단서 없이 하한으로 쓴다).

## 정직의 자리

- **앱이 독립 부팅돼야 한다.** field-report는 H2 인메모리 + Flyway라 외부 DB
  없이 뜬다. 안 뜨면 수치를 지어내지 않고 **"안 떴다"를 결과로** 남긴다.
- **부하의 성질을 함께 적는다.** 인증·바디가 필요한 엔드포인트는 4xx가 날 수
  있고, 그때 잰 CPU는 '요청 처리+거부'의 CPU다 — 상태 분포를 evidence에 남겨
  수치가 문맥 없이 서빙 처리량으로 둔갑하지 않게 한다.
- **하한이다.** 앱이 이 부하에서 **적어도 이만큼** 썼다는 것. 더 무거운 경로
  (인증 후 보호 자원)는 더 쓸 수 있다.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
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


def _server_port(app_root: Path) -> int:
    """application.yml에서 server.port를 읽는다(없으면 8080)."""
    for name in ("application.yml", "application.yaml", "application.properties"):
        p = app_root / "src" / "main" / "resources" / name
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("port:"):
                    try:
                        return int(s.split(":", 1)[1].strip())
                    except ValueError:
                        pass
    return 8080


def _build_jar(app_root: Path, env: dict) -> Path | None:
    jars = list((app_root / "build" / "libs").glob("*.jar"))
    if not jars:
        print("  → bootJar 빌드 …", end=" ", flush=True)
        r = subprocess.run(
            [str(_GRADLEW), "-p", str(app_root), "bootJar", "--no-daemon"],
            cwd=REPO, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        print("OK" if r.returncode == 0 else "실패")
        if r.returncode != 0:
            print((r.stderr or r.stdout)[-500:], file=sys.stderr)
            return None
        jars = list((app_root / "build" / "libs").glob("*.jar"))
    # 평문 jar(-plain)이 아니라 실행 가능한 bootJar를 고른다.
    boot = [j for j in jars if not j.stem.endswith("-plain")]
    return (boot or jars)[0] if jars else None


def _wait_ready(port: int, deadline: float) -> bool:
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def _sample(pid: int, cache: dict) -> tuple[float, float]:
    """앱 프로세스 트리(부모+자식)의 (RSS MiB, CPU 코어). 지속 캐시로 CPU 누적."""
    import psutil
    try:
        root = cache.get(pid) or psutil.Process(pid)
        cache[pid] = root
        live = [root, *root.children(recursive=True)]
    except psutil.Error:
        return 0.0, 0.0
    rss = cpu = 0.0
    for p in live:
        proc = cache.get(p.pid)
        if proc is None:
            proc = p
            cache[p.pid] = proc
            proc.cpu_percent(interval=None)
        try:
            rss += proc.memory_info().rss / (1024 * 1024)
            cpu += proc.cpu_percent(interval=None) / 100.0
        except psutil.Error:
            continue
    return rss, cpu


def _endpoints(sample: Path) -> list[tuple[str, str]]:
    """수용 스위트에서 몰 엔드포인트를 고른다 — GET 전부 + POST /auth(핫 패스)."""
    acc = sample / "design" / "acceptance.json"
    picks: list[tuple[str, str]] = []
    if acc.exists():
        doc = json.loads(acc.read_text(encoding="utf-8"))
        for c in doc.get("checks", []):
            ep = c.get("endpoint")
            if not ep:
                continue
            m, path = ep["method"], ep["path"]
            # 경로 변수는 더미로 채운다.
            path = path.replace("{", "").replace("}", "").replace("Id", "/1") \
                if "{" in path else path
            if m == "GET" or "auth" in path or "login" in path:
                picks.append((m, path))
    return picks or [("GET", "/")]


def _drive(base: str, endpoints: list[tuple[str, str]], concurrency: int,
           duration: float) -> dict:
    """concurrency 워커가 duration초 동안 엔드포인트를 라운드로빈으로 때린다."""
    stop = time.time() + duration
    status: dict[str, int] = {}
    lock = threading.Lock()
    body = json.dumps({"username": "probe", "password": "probe"}).encode()

    def worker(wid: int) -> None:
        i = wid
        while time.time() < stop:
            method, path = endpoints[i % len(endpoints)]
            i += 1
            req = urllib.request.Request(base + path, method=method)
            data = None
            if method != "GET":
                req.add_header("Content-Type", "application/json")
                data = body
            try:
                with urllib.request.urlopen(req, data=data, timeout=5) as r:
                    code = str(r.status)
            except urllib.error.HTTPError as e:
                code = str(e.code)
            except Exception as e:  # noqa: BLE001
                code = type(e).__name__
            with lock:
                status[code] = status.get(code, 0) + 1

    threads = [threading.Thread(target=worker, args=(w,), daemon=True)
               for w in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=duration + 5)
    return status


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(
        prog="load_probe",
        description="생성 앱을 부팅해 부하로 서빙 vCPU를 측정")
    parser.add_argument("sample_dir")
    parser.add_argument("--concurrency", type=int, default=40,
                        help="동시 워커 수(기본 40 = field-report scale)")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="부하 지속 초")
    args = parser.parse_args(argv)

    import os
    sample = Path(args.sample_dir).resolve()
    app_root = _app_root(sample)
    if app_root is None:
        print("생성 앱이 없습니다 — build_implementation을 먼저.", file=sys.stderr)
        return 2

    env = {**os.environ, "GRADLE_USER_HOME": str(_GRADLE_HOME)}
    print(f"표본: {sample.name}\n앱: {app_root}")
    jar = _build_jar(app_root, env)
    if jar is None:
        print("bootJar를 못 만들었습니다.", file=sys.stderr)
        return 1

    port = _server_port(app_root)
    base = f"http://127.0.0.1:{port}"
    print(f"→ java -jar {jar.name} (port {port}) …", end=" ", flush=True)
    app = subprocess.Popen(["java", "-jar", str(jar)], cwd=app_root, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait_ready(port, time.time() + 90):
            print("안 떴음")
            print("  앱이 90초 안에 포트를 안 열었다 — 측정 없음(수치를 지어내지 "
                  "않는다). 부팅 로그를 확인할 것.", file=sys.stderr)
            return 1
        print("떴음")

        endpoints = _endpoints(sample)
        print(f"→ 부하: 동시 {args.concurrency} · {args.duration:g}s · "
              f"엔드포인트 {len(endpoints)}종 …", flush=True)

        peak_rss = peak_cpu = 0.0
        samples = 0
        stop = threading.Event()
        cache: dict = {}

        def sampler() -> None:
            nonlocal peak_rss, peak_cpu, samples
            _sample(app.pid, cache)
            time.sleep(0.5)
            while not stop.is_set():
                rss, cpu = _sample(app.pid, cache)
                peak_rss, peak_cpu = max(peak_rss, rss), max(peak_cpu, cpu)
                samples += 1
                time.sleep(0.3)

        th = threading.Thread(target=sampler, daemon=True)
        th.start()
        status = _drive(base, endpoints, args.concurrency, args.duration)
        stop.set()
        th.join(timeout=2)
    finally:
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()

    peak_rss_gib = peak_rss / 1024
    total_req = sum(status.values())
    print(f"  피크: {peak_cpu:g} vCPU · {peak_rss_gib:.2f} GiB "
          f"(표본 {samples}) · 요청 {total_req} {dict(status)}")

    capacity = {
        "schemaVersion": "easydep-capacity/v1alpha1",
        "vcpu": round(peak_cpu, 2),
        "mem_gib": round(peak_rss_gib, 3),
        "under": f"{args.concurrency} concurrent workers for {args.duration:g}s",
        "evidence": f"peak of the running app process ({jar.name}) under HTTP "
                    f"load; {total_req} requests, status distribution {dict(status)}"
                    f". This is serving CPU (no build process). A floor: the app "
                    f"used at least this much under this load; heavier authed "
                    f"paths may use more.",
        "production_load": True,
        "measuredAt": datetime.now(UTC).isoformat(),
        "code": git_head(),
    }
    out = sample / "design" / "capacity-load.json"
    out.write_text(json.dumps(capacity, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"기록: {out}")
    print("  → 서빙 vCPU 하한. intake_report가 capacity-load.json을 measured "
          "층으로 싣는다(production_load=True → 단서 없이 하한).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
