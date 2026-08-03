"""부하 프로브 — 서빙 CPU·메모리를 **방법론 등급으로** 잰다 (P0~P4 구현).

    python -m app.core.cloudkb.tools.load_probe app/core/cloudkb/appkb/samples/<이름>

## 무엇이 바뀌었나 (2026-08-02, capacity-measurement-plan의 실행)

첫 판은 닫힌 루프(N워커) + 단일 실행 피크 + 부팅 로그 폐기였다 — 조사
(`capacity-measurement-methodology-2026-08-02.md`)가 셋 다 문헌 밖으로 판정했다.
이 판은 그 계획(P0~P4)을 구현한다:

- **P0 관측 가능성**: 부팅 stdout/stderr를 `.easydep/impl-runs/<이름>/load-probe/`에
  남기고, 프로세스가 죽으면 **기다리지 않고** 종료코드·로그 꼬리·hs_err를 보고한다.
  실측 전례: 2026-08-02 20:11, field-report 부팅이 27초 만에 JVM 네이티브 OOM으로
  죽었다(hs_err: AvailPageFile 0M — 시스템 커밋 고갈). 앱 결함이 아니라 환경이었고,
  당시 도구는 로그를 버려서 그걸 볼 수 없었다. 그래서 **측정 전 커밋 여유를 확인**하고
  부족하면 재지 않는다(지어내지 않는다).
- **P1 open 모델**: 고정 도착 스케줄(t0 + i/λ)로 요청을 낸다 — 응답이 늦어도
  발사 시각은 밀리지 않는다(coordinated omission 회피). 지연은 **예정 시각 기준**으로
  계산한다. 동시 in-flight 상한(호스트 보호)에 걸려 늦게 나간 발사는 lag로 기록한다.
- **P2 워밍업 + 정상상태**: 워밍업 구간을 버리고 측정 구간만 집계한다. 정상상태는
  측정 구간을 부분창으로 나눈 CPU 평균의 변동계수(CV)로 판정하되, **임계값은 우리
  결정**임을 산출물에 적는다. 미도달도 결과다.
- **P3 백분위 + 반복**: 측정 구간의 CPU·RSS를 p50/p95/p99로 집계하고, 프로세스
  실행을 반복(기본 3회)해 평균·95% 신뢰구간(t 분포)을 낸다.
- **P4 메모리**: RSS 전량의 p99를 하한으로, 25~50% 헤드룸은 **구간으로만** 보고한다
  (단일 계수를 고르면 그 계수에 출처가 없다).

## 도착률의 출처 (T-도착률)

`scale`이 있으면 동시 사용자 × **1 req/user/s(선언된 가정 — 생각시간의 출처가
없다)**로 환산한다. field-report에는 `scale` 자체가 없다 — 그때 도착률은 요구가
아니라 **프로브 파라미터**이고, 산출물에 그렇게 적힌다. Little의 법칙(L=λW)으로
in-flight 동시성을 사후 계산해 함께 기록한다.

## 정직의 자리 (그대로 유지)

- 앱이 안 뜨면 수치를 지어내지 않고 **왜 안 떴는지 증거**(로그·hs_err·환경)를 남긴다.
- 상태 분포를 엔드포인트별로 남긴다 — 4xx/5xx로 잰 CPU는 '처리+거부'의 CPU다.
- **하한이다**: 이 부하에서 적어도 이만큼 썼다는 것. 포화 램프(최대 용량 탐색)는
  하지 않는다 — 생성기와 SUT가 같은 코어를 나누는 단일 머신에서 포화점은 앱이
  아니라 머신을 재게 된다. 그건 위협으로 선언하지 흉내 내지 않는다.
"""
from __future__ import annotations

import argparse
import json
import math
import queue
import re
import socket
import statistics
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

#: 정상상태 판정의 CV 임계 — **우리 결정**이다(계획 P2: "고정 워밍업 + 안정성
#: 검사(이동 창의 변동계수 임계)"). 문헌 상수가 아니므로 산출물에 그렇게 적는다.
_CV_THRESHOLD = 0.25
#: 정상상태 판정에 쓰는 부분창 수.
_CV_WINDOWS = 6
#: 측정을 거부하는 커밋 여유(GiB). 근거: 2026-08-02 20:11 hs_err — 시스템 커밋이
#: 0이 되자 JVM이 부팅 27초에 네이티브 OOM으로 죽었다. 그 상태에서 재면 측정이
#: 아니라 환경 붕괴를 잰다.
_MIN_COMMIT_GIB = 3.0
#: 95% 양측 t 임계값(df→t). 반복 수가 작아서 표로 족하다.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
        6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}


# ── 표본·통계 ────────────────────────────────────────────────────────────────

def _percentile(values: list[float], q: float) -> float:
    """최근접 순위(nearest-rank) 백분위. 표본이 적어 보간보다 보수적으로."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(1, math.ceil(q * len(s)))
    return s[k - 1]


def _mean_ci(values: list[float]) -> dict:
    """반복 실행 값들의 평균 + 95% CI 반폭(Kalibera&Jones의 자세, t 분포).

    실행이 1회면 CI를 **내지 않는다** — 지어내지 않는다.
    """
    n = len(values)
    if n == 0:
        return {"mean": None, "ci95": None, "perRun": []}
    mean = statistics.fmean(values)
    ci = None
    if n >= 2:
        sd = statistics.stdev(values)
        t = _T95.get(n - 1, 1.96)
        ci = t * sd / math.sqrt(n)
    return {"mean": round(mean, 4), "ci95": round(ci, 4) if ci is not None else None,
            "perRun": [round(v, 4) for v in values]}


# ── 표본 앱 좌표 ─────────────────────────────────────────────────────────────

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
    boot = [j for j in jars if not j.stem.endswith("-plain")]
    return (boot or jars)[0] if jars else None


def _endpoints(sample: Path) -> list[tuple[str, str]]:
    """수용 스위트에서 몰 엔드포인트 — GET 전부 + POST /auth(핫 패스), 중복 제거.

    경로 변수는 `{var}` 전체를 `1`로 치환한다. 예전 코드는 `Id`라는 글자를
    치환해서 `/reports/{reportId}/pdf` → `/reports/report/1/pdf`(여분 세그먼트,
    404)를 만들었다 — 실측으로 확인한 도구 버그였다(`/reports/1/pdf`는 200).
    """
    acc = sample / "design" / "acceptance.json"
    picks: list[tuple[str, str]] = []
    if acc.exists():
        doc = json.loads(acc.read_text(encoding="utf-8"))
        for c in doc.get("checks", []):
            ep = c.get("endpoint")
            if not ep:
                continue
            m, path = ep["method"], re.sub(r"\{[^}]*\}", "1", ep["path"])
            if (m == "GET" or "auth" in path or "login" in path) \
                    and (m, path) not in picks:
                picks.append((m, path))
    return picks or [("GET", "/")]


def _rate_from_scale(sample: Path) -> tuple[float, str]:
    """도착률과 **그 출처**. scale이 없으면 도구 기본값임을 명시해 돌려준다."""
    spec_path = sample / "requirements" / "resource_spec.json"
    if spec_path.exists():
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            scale = spec.get("scale") or {}
            value, unit = scale.get("value"), scale.get("unit")
            if value and unit == "requestsPerSecond":
                return float(value), f"stated scale: {value:g} requests/s"
            if value and unit == "concurrentUsers":
                # 선언된 가정: 사용자당 1 req/s. 생각시간(Z)의 출처가 없어
                # Little의 법칙(L=λ(W+Z))을 닫을 수 없다 — 가정을 명시한다.
                return float(value), (
                    f"stated scale {value:g} concurrent users × 1 req/user/s "
                    "(declared assumption — no source states think time)")
        except (json.JSONDecodeError, OSError):
            pass
    return 40.0, ("probe parameter (tool default) — the sample states no scale; "
                  "this rate is ours, not a requirement")


# ── 환경 (P0: 커밋 고갈이 실측된 사인이라 재기 전에 확인한다) ────────────────

def _env_snapshot() -> dict:
    import psutil
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        "physTotalGiB": round(vm.total / 2**30, 2),
        "physAvailableGiB": round(vm.available / 2**30, 2),
        "pageFileTotalGiB": round(sw.total / 2**30, 2),
        "pageFileFreeGiB": round(sw.free / 2**30, 2),
        # 근사치: 커밋 여유 ≈ 물리 가용 + 페이지파일 여유.
        "commitAvailableGiB": round((vm.available + sw.free) / 2**30, 2),
        "cpuLogical": psutil.cpu_count(logical=True),
    }


# ── 프로세스 표본 ────────────────────────────────────────────────────────────

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


class _Sampler(threading.Thread):
    """0.5초 간격으로 (모노토닉 초, RSS MiB, CPU 코어)를 쌓는다. 첫 표본은
    CPU 기준선만 잡고 버린다(psutil cpu_percent의 계약)."""

    def __init__(self, pid: int, t0: float) -> None:
        super().__init__(daemon=True)
        self.pid, self.t0 = pid, t0
        self.rows: list[tuple[float, float, float]] = []
        self._stop = threading.Event()
        self._cache: dict = {}

    def run(self) -> None:
        _sample(self.pid, self._cache)
        time.sleep(0.5)
        while not self._stop.is_set():
            rss, cpu = _sample(self.pid, self._cache)
            self.rows.append((time.monotonic() - self.t0, rss, cpu))
            time.sleep(0.5)

    def stop(self) -> None:
        self._stop.set()


# ── P1: open 모델 부하 (고정 도착 스케줄) ────────────────────────────────────

def _open_load(base: str, endpoints: list[tuple[str, str]], rate: float,
               total: float, max_inflight: int, t0: float) -> list[tuple]:
    """t0 + i/rate 시각에 요청을 낸다. 응답이 늦어도 스케줄은 밀리지 않는다.

    반환: (예정, 발사, 완료, 상태, 엔드포인트 인덱스) 튜플들. 시각은 t0 기준 초.
    in-flight 상한(워커 수)에 막혀 늦게 나간 발사는 (발사-예정)의 lag로 남는다 —
    그 lag 자체가 '생성기가 스케줄을 못 지켰다'는 기록이다(감추지 않는다).
    """
    n = int(rate * total)
    q_: queue.Queue = queue.Queue()
    for i in range(n):
        q_.put((i / rate, i % len(endpoints)))
    for _ in range(max_inflight):
        q_.put(None)

    records: list[tuple] = []
    lock = threading.Lock()
    body = json.dumps({"username": "probe", "password": "probe"}).encode()
    hard_stop = t0 + total + 30  # SUT가 늪이어도 프로브는 끝난다.

    def worker() -> None:
        while True:
            item = q_.get()
            if item is None or time.monotonic() > hard_stop:
                return
            sched, ei = item
            now = time.monotonic() - t0
            if sched > now:
                time.sleep(sched - now)
            sent = time.monotonic() - t0
            method, path = endpoints[ei]
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
            done = time.monotonic() - t0
            with lock:
                records.append((sched, sent, done, code, ei))

    threads = [threading.Thread(target=worker, daemon=True)
               for _ in range(max_inflight)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=total + 40)
    return records


# ── 한 번의 실행 (부팅 → 워밍업 → 측정 → 종료) ──────────────────────────────

def _hs_err_after(app_root: Path, since: float) -> list[str]:
    return [p.name for p in app_root.glob("hs_err_pid*.log")
            if p.stat().st_mtime >= since]


def _boot_and_measure(k: int, jar: Path, app_root: Path, port: int,
                      endpoints: list[tuple[str, str]], rate: float,
                      warmup: float, duration: float, max_inflight: int,
                      boot_deadline: float, log_dir: Path, env: dict) -> dict:
    """실행 하나. 실패해도 **왜**를 담아 돌려준다(지어내지 않는다)."""
    base = f"http://127.0.0.1:{port}"
    log_path = log_dir / f"run{k}-boot.log"
    started_wall = time.time()

    # 포트가 이미 열려 있으면 남의 프로세스를 재게 된다 — 거부.
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return {"ok": False, "reason": f"port {port} already open — "
                    "another process would contaminate the measurement"}
    except OSError:
        pass

    environment = _env_snapshot()
    if environment["commitAvailableGiB"] < _MIN_COMMIT_GIB:
        return {"ok": False, "environment": environment,
                "reason": f"commit headroom {environment['commitAvailableGiB']} GiB "
                          f"< {_MIN_COMMIT_GIB} GiB — a JVM died of native OOM in "
                          "exactly this state (hs_err 2026-08-02); measuring now "
                          "would measure the collapsing host, not the app"}

    log_f = open(log_path, "w", encoding="utf-8", errors="replace")  # noqa: SIM115
    t0 = time.monotonic()
    app = subprocess.Popen(["java", "-jar", str(jar)], cwd=app_root, env=env,
                           stdout=log_f, stderr=subprocess.STDOUT)
    sampler = _Sampler(app.pid, t0)
    sampler.start()
    result: dict = {"ok": False, "environment": environment,
                    "bootLog": str(log_path)}
    try:
        # 부팅 대기 — 프로세스가 죽으면 즉시 끊는다(예전엔 90초를 다 기다렸다).
        t_port = None
        deadline = t0 + boot_deadline
        while time.monotonic() < deadline:
            if app.poll() is not None:
                log_f.flush()
                tail = "\n".join(log_path.read_text(
                    encoding="utf-8", errors="replace").splitlines()[-15:])
                result.update({
                    "reason": f"process exited rc={app.returncode} before "
                              f"opening port {port}",
                    "bootLogTail": tail,
                    "hsErr": _hs_err_after(app_root, started_wall),
                })
                return result
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    t_port = time.monotonic() - t0
                    break
            except OSError:
                time.sleep(0.5)
        if t_port is None:
            result["reason"] = (f"port {port} not open within {boot_deadline:g}s "
                                f"and process still alive — boot log kept at "
                                f"{log_path}")
            return result

        # 부하 (워밍업 + 측정을 한 스케줄로 잇는다 — JIT가 부하 밑에서 덥혀진다).
        t_load = time.monotonic()
        records = _open_load(base, endpoints, rate, warmup + duration,
                             max_inflight, t_load)
        load_off = t_load - t0  # sampler 시각축으로 옮기는 오프셋

        # ── 표본 분리: 시동 / 워밍업(버림) / 측정 ──
        rows = sampler.rows
        boot_rows = [r for r in rows if r[0] < load_off]
        meas_lo, meas_hi = load_off + warmup, load_off + warmup + duration
        meas = [r for r in rows if meas_lo <= r[0] < meas_hi]
        cpu = [r[2] for r in meas]
        rss = [r[1] for r in meas]

        # P2: 정상상태 — 측정 구간 부분창 평균의 CV.
        steady, cv = None, None
        if len(cpu) >= _CV_WINDOWS * 2:
            size = len(cpu) // _CV_WINDOWS
            means = [statistics.fmean(cpu[i * size:(i + 1) * size])
                     for i in range(_CV_WINDOWS)]
            m = statistics.fmean(means)
            cv = statistics.pstdev(means) / m if m > 0 else None
            steady = cv is not None and cv <= _CV_THRESHOLD

        # 요청 통계 — 측정 창에 **예정된** 것만(경계의 워밍업 꼬리를 섞지 않는다).
        in_meas = [r for r in records if warmup <= r[0] < warmup + duration]
        lat = [(r[2] - r[0]) * 1000 for r in in_meas]      # 예정→완료 (CO 보정)
        svc = [(r[2] - r[1]) * 1000 for r in in_meas]      # 발사→완료
        lag = [(r[1] - r[0]) * 1000 for r in in_meas]      # 스케줄 미준수
        status: dict[str, int] = {}
        per_ep: dict[str, dict[str, int]] = {}
        for sched, sent, done, code, ei in records:
            status[code] = status.get(code, 0) + 1
            key = f"{endpoints[ei][0]} {endpoints[ei][1]}"
            per_ep.setdefault(key, {})[code] = per_ep.get(key, {}).get(code, 0) + 1

        result.update({
            "ok": True,
            "timeToPortSec": round(t_port, 1),
            "startup": {
                "peakVCpu": round(max((r[2] for r in boot_rows), default=0.0), 2),
                "peakRssMiB": round(max((r[1] for r in boot_rows), default=0.0), 1),
            },
            "cpu": {"p50": round(_percentile(cpu, 0.50), 3),
                    "p95": round(_percentile(cpu, 0.95), 3),
                    "p99": round(_percentile(cpu, 0.99), 3),
                    "samples": len(cpu)},
            "rssMiB": {"p50": round(_percentile(rss, 0.50), 1),
                       "p95": round(_percentile(rss, 0.95), 1),
                       "p99": round(_percentile(rss, 0.99), 1)},
            "steadyState": {"reached": steady, "cv": round(cv, 3) if cv else cv},
            "latencyMs": {  # 예정 시각 기준(coordinated-omission 보정)
                "p50": round(_percentile(lat, 0.50), 1),
                "p95": round(_percentile(lat, 0.95), 1),
                "p99": round(_percentile(lat, 0.99), 1)},
            "serviceMs": {"p50": round(_percentile(svc, 0.50), 1),
                          "p95": round(_percentile(svc, 0.95), 1)},
            "dispatchLagMs": {"mean": round(statistics.fmean(lag), 1) if lag else None,
                              "max": round(max(lag), 1) if lag else None},
            "requests": {"scheduled": int(rate * (warmup + duration)),
                         "completed": len(records),
                         "inMeasureWindow": len(in_meas)},
            "achievedRps": round(len(in_meas) / duration, 1),
            # Little의 법칙 사후 기록: L = λW (측정 창의 실측으로 닫는다).
            "littleLawInflight": round(
                (len(in_meas) / duration) *
                (statistics.fmean(svc) / 1000), 2) if svc else None,
            "statusDistribution": status,
            "perEndpoint": per_ep,
            "hsErr": _hs_err_after(app_root, started_wall),
        })

        # 원자료를 남긴다 — 집계가 원자료를 대체하지 않는다.
        with open(log_dir / f"run{k}-samples.csv", "w", encoding="utf-8") as f:
            f.write("t_sec,rss_mib,cpu_cores\n")
            for t, r_, c in rows:
                f.write(f"{t:.1f},{r_:.1f},{c:.3f}\n")
        with open(log_dir / f"run{k}-requests.csv", "w", encoding="utf-8") as f:
            f.write("sched_sec,sent_sec,done_sec,status,endpoint\n")
            for sched, sent, done, code, ei in records:
                f.write(f"{sched:.3f},{sent:.3f},{done:.3f},{code},"
                        f"{endpoints[ei][0]} {endpoints[ei][1]}\n")
        return result
    finally:
        sampler.stop()
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()
        log_f.close()


# ── 진입점 ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(
        prog="load_probe",
        description="생성 앱을 부팅해 open 모델 부하로 서빙 CPU·RSS 분포를 측정")
    parser.add_argument("sample_dir")
    parser.add_argument("--rate", type=float, default=None,
                        help="도착률 req/s (기본: scale에서 환산, 없으면 40 — "
                             "출처가 산출물에 적힌다)")
    parser.add_argument("--warmup", type=float, default=30.0,
                        help="워밍업 초(버린다, 기본 30)")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="측정 창 초(기본 60)")
    parser.add_argument("--runs", type=int, default=3,
                        help="프로세스 실행 반복 수(기본 3) — 순차, 겹치지 않는다")
    parser.add_argument("--max-inflight", type=int, default=64,
                        help="동시 in-flight 상한(호스트 보호; 걸리면 lag로 기록)")
    parser.add_argument("--boot-deadline", type=float, default=120.0,
                        help="부팅 대기 한도 초(기본 120; 실측 정상 부팅 ~32s)")
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

    rate, rate_why = (args.rate, "probe parameter (CLI)") if args.rate \
        else _rate_from_scale(sample)
    port = _server_port(app_root)
    endpoints = _endpoints(sample)
    log_dir = REPO / ".easydep" / "impl-runs" / sample.name / "load-probe"
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"→ open 모델: {rate:g} req/s ({rate_why})")
    print(f"→ 워밍업 {args.warmup:g}s(버림) + 측정 {args.duration:g}s × "
          f"{args.runs}회 · 엔드포인트 {len(endpoints)}종 · port {port}")

    runs: list[dict] = []
    failures: list[dict] = []
    for k in range(1, args.runs + 1):
        print(f"→ 실행 {k}/{args.runs}: 부팅 …", end=" ", flush=True)
        r = _boot_and_measure(k, jar, app_root, port, endpoints, rate,
                              args.warmup, args.duration, args.max_inflight,
                              args.boot_deadline, log_dir, env)
        if not r.get("ok"):
            print("실패")
            print(f"  ✗ {r.get('reason')}", file=sys.stderr)
            if r.get("bootLogTail"):
                print("  --- 부팅 로그 꼬리 ---\n" + r["bootLogTail"],
                      file=sys.stderr)
            failures.append(r)
            continue
        print(f"{r['timeToPortSec']}s에 떴음 · CPU p95 {r['cpu']['p95']} · "
              f"RSS p99 {r['rssMiB']['p99']} MiB · "
              f"정상상태 {r['steadyState']['reached']} (cv={r['steadyState']['cv']})"
              f" · 요청 {r['requests']['completed']}")
        runs.append(r)
        time.sleep(2)  # 포트 TIME_WAIT·프로세스 정리 여유. 절대 겹치지 않는다.

    if not runs:
        print("성공한 실행이 없다 — capacity-load.json을 쓰지 않는다"
              "(수치를 지어내지 않는다).", file=sys.stderr)
        (log_dir / "FAILED.json").write_text(
            json.dumps({"failures": failures, "at": datetime.now(UTC).isoformat()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    # ── P3: 반복 집계 ──
    agg = {
        "cpu": {p: _mean_ci([r["cpu"][p] for r in runs])
                for p in ("p50", "p95", "p99")},
        "rssMiB": {p: _mean_ci([r["rssMiB"][p] for r in runs])
                   for p in ("p50", "p95", "p99")},
        "latencyMs": {p: _mean_ci([r["latencyMs"][p] for r in runs])
                      for p in ("p50", "p95", "p99")},
        "startupPeakVCpu": _mean_ci([r["startup"]["peakVCpu"] for r in runs]),
        "startupPeakRssMiB": _mean_ci([r["startup"]["peakRssMiB"] for r in runs]),
        "timeToPortSec": _mean_ci([r["timeToPortSec"] for r in runs]),
        "achievedRps": _mean_ci([r["achievedRps"] for r in runs]),
    }
    status_all: dict[str, int] = {}
    for r in runs:
        for code, cnt in r["statusDistribution"].items():
            status_all[code] = status_all.get(code, 0) + cnt
    steady_all = [r["steadyState"]["reached"] for r in runs]

    vcpu = agg["cpu"]["p95"]["mean"]
    mem_gib = (agg["rssMiB"]["p99"]["mean"] or 0.0) / 1024
    under = (f"open-model HTTP load, fixed arrival {rate:g} req/s, "
             f"{args.duration:g}s measure window after {args.warmup:g}s warmup, "
             f"{len(runs)} process runs")
    evidence = (
        f"serving CPU = mean of per-run p95 over {len(runs)} runs "
        f"(CI95 ±{agg['cpu']['p95']['ci95']}); memory = mean of per-run RSS p99 "
        f"(CI95 ±{agg['rssMiB']['p99']['ci95']} MiB). Arrival rate provenance: "
        f"{rate_why}. Status distribution {status_all} — non-2xx responses mean "
        f"part of this CPU is request-rejection work, not full business logic. "
        f"Steady-state (CV≤{_CV_THRESHOLD}, our threshold): {steady_all}. "
        f"Startup spike is recorded separately (startup block; see also "
        f"design/capacity.json) — it belongs to readiness/limit, not request. "
        f"This is a floor under THIS synthetic load on a shared dev machine, "
        f"not a production distribution."
    )

    capacity = {
        "schemaVersion": "easydep-capacity/v1alpha2",
        "vcpu": round(vcpu, 2) if vcpu else 0,
        "mem_gib": round(mem_gib, 3),
        "under": under,
        "evidence": evidence,
        "production_load": True,
        "measuredAt": datetime.now(UTC).isoformat(),
        "code": git_head(),
        "method": {
            "loadModel": "open — fixed arrival schedule t0+i/rate; latency is "
                         "measured from the SCHEDULED send time (coordinated-"
                         "omission aware); in-flight is capped and any schedule "
                         "slip is recorded as dispatchLagMs",
            "rateRps": rate,
            "rateProvenance": rate_why,
            "warmupSec": args.warmup,
            "measureSec": args.duration,
            "runs": len(runs),
            "maxInflight": args.max_inflight,
            "cvThreshold": {"value": _CV_THRESHOLD,
                            "provenance": "our decision (plan P2 stability check)"
                                          " — not a literature constant"},
        },
        "aggregate": agg,
        "statusDistribution": status_all,
        "perEndpoint": runs[0]["perEndpoint"],
        "memoryHeadroom": {
            "basis": "RSS p99 mean across runs",
            "requestRangeGiB": [round(mem_gib * 1.25, 3), round(mem_gib * 1.5, 3)],
            "provenance": "industry practice: RSS + 25–50% headroom (AWS JVM "
                          "container guidance, methodology doc pillar 4). A range "
                          "is reported because no source picks one coefficient.",
        },
        "threats": [
            "single app, single workload — no generalization",
            "load generator shares CPU cores with the SUT (same machine); "
            "saturation ramp deliberately not attempted for this reason",
            "synthetic request mix (unauthenticated; some endpoints return "
            "4xx/5xx — see statusDistribution)",
            "dev machine under memory pressure; environment snapshot per run",
            f"steady-state verdicts {steady_all} use our CV threshold, "
            "not a change-point method",
        ],
        "runs": runs,
        "failedRuns": failures,
    }
    out = sample / "design" / "capacity-load.json"
    out.write_text(json.dumps(capacity, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"기록: {out}")
    print(f"  → 서빙 CPU p95 평균 {capacity['vcpu']} vCPU "
          f"(±{agg['cpu']['p95']['ci95']}) · RSS p99 평균 {capacity['mem_gib']} GiB"
          f" · 시동 피크 {agg['startupPeakVCpu']['mean']} vCPU. "
          "intake_report가 capacity-load.json을 우선해 measured 층으로 싣는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
