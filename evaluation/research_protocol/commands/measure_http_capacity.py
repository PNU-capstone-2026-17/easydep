"""HTTP 부하와 프로세스 자원을 함께 측정하는 개발용 용량 관측기."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psutil


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _process_tree(root_pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(root_pid)
        return [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def _resource_sample(root_pid: int, previous_cpu: float, elapsed: float) -> tuple[float, int, float]:
    processes = _process_tree(root_pid)
    cpu_total = 0.0
    rss_total = 0
    for process in processes:
        try:
            cpu = process.cpu_times()
            cpu_total += cpu.user + cpu.system
            rss_total += process.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    cores = max(0.0, (cpu_total - previous_cpu) / elapsed) if elapsed > 0 else 0.0
    return cpu_total, rss_total, cores


def _docker_resource_sample(container: str) -> tuple[int, float]:
    completed = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    cpu_cores = float(str(payload["CPUPerc"]).rstrip("%")) / 100
    used_memory = str(payload["MemUsage"]).split("/", 1)[0].strip()
    return _parse_binary_size(used_memory), cpu_cores


def _parse_binary_size(value: str) -> int:
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
    for unit in ("GiB", "MiB", "KiB", "B"):
        if value.endswith(unit):
            return round(float(value[: -len(unit)].strip()) * units[unit])
    raise ValueError(f"unsupported Docker memory unit: {value}")


def measure_http_capacity(
    *,
    url: str,
    duration_seconds: float,
    concurrency: int,
    timeout_seconds: float,
    process_id: int | None = None,
    docker_container: str | None = None,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    data_path: Path | None = None,
    sample_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """고정 동시성 구간을 측정한다. 최대 처리량을 자동 추정하지 않는다."""
    if duration_seconds <= 0 or concurrency <= 0 or sample_interval_seconds <= 0:
        raise ValueError("duration, concurrency, sample interval must be positive")
    if (process_id is None) == (docker_container is None):
        raise ValueError("exactly one process_id or docker_container is required")
    stop = threading.Event()
    lock = threading.Lock()
    latencies_ms: list[float] = []
    attempts = successes = 0

    def request_loop() -> None:
        nonlocal attempts, successes
        while not stop.is_set():
            request = urllib.request.Request(
                url, data=body, headers=headers or {}, method=method.upper()
            )
            started = time.perf_counter()
            ok = False
            try:
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    response.read()
                    ok = 200 <= response.status < 400
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            elapsed_ms = (time.perf_counter() - started) * 1000
            with lock:
                attempts += 1
                successes += int(ok)
                latencies_ms.append(elapsed_ms)

    data_bytes_before = _tree_size(data_path) if data_path else None
    cpu_samples: list[float] = []
    rss_samples: list[int] = []
    started = time.perf_counter()
    previous_at = started
    initial_processes = _process_tree(process_id) if process_id is not None else []
    previous_cpu = sum(
        process.cpu_times().user + process.cpu_times().system for process in initial_processes
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(request_loop) for _ in range(concurrency)]
        while True:
            now = time.perf_counter()
            if now - started >= duration_seconds:
                break
            time.sleep(min(sample_interval_seconds, duration_seconds - (now - started)))
            sampled_at = time.perf_counter()
            if docker_container is not None:
                rss, cores = _docker_resource_sample(docker_container)
            else:
                previous_cpu, rss, cores = _resource_sample(
                    int(process_id), previous_cpu, sampled_at - previous_at
                )
            previous_at = sampled_at
            cpu_samples.append(cores)
            rss_samples.append(rss)
        stop.set()
        for future in futures:
            future.result(timeout=timeout_seconds + 1)
    wall_seconds = time.perf_counter() - started
    data_bytes_after = _tree_size(data_path) if data_path else None
    growth = (
        max(0, data_bytes_after - data_bytes_before)
        if data_bytes_before is not None and data_bytes_after is not None
        else None
    )
    return {
        "schemaVersion": "easydep-http-capacity-measurement/v1",
        "measurementKind": "single-development-load-point",
        "url": url,
        "method": method.upper(),
        "resourceObserver": "docker-stats" if docker_container else "process-tree",
        "concurrency": concurrency,
        "requestedDurationSeconds": duration_seconds,
        "observedDurationSeconds": round(wall_seconds, 3),
        "attempts": attempts,
        "successes": successes,
        "sustainableRpsPerInstance": round(successes / wall_seconds, 3),
        "errorRate": round((attempts - successes) / attempts, 6) if attempts else 1.0,
        "p50LatencyMs": _rounded(_percentile(latencies_ms, 0.50)),
        "p95LatencyMs": _rounded(_percentile(latencies_ms, 0.95)),
        "p99LatencyMs": _rounded(_percentile(latencies_ms, 0.99)),
        "p95CpuCores": _rounded(_percentile(cpu_samples, 0.95)),
        "p99RssBytes": int(_percentile([float(value) for value in rss_samples], 0.99) or 0),
        "dataBytesBefore": data_bytes_before,
        "dataBytesAfter": data_bytes_after,
        "bytesGrowthPerDurableWrite": (
            round(growth / successes, 3) if growth is not None and successes else None
        ),
        "limitations": [
            "단일 동시성의 개발 환경 관측값이며 최대 지속 처리량을 뜻하지 않는다.",
            "클라우드 후보 선정 전 동일 이미지와 대표 부하로 여러 지점을 반복 측정해야 한다.",
        ],
    }


def _rounded(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    observer = parser.add_mutually_exclusive_group(required=True)
    observer.add_argument("--process-id", type=int)
    observer.add_argument("--docker-container")
    parser.add_argument("--duration-seconds", type=float, default=30)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--method", default="GET")
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--content-type", default="application/json")
    parser.add_argument("--data-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    body = args.body_file.read_bytes() if args.body_file else None
    result = measure_http_capacity(
        url=args.url,
        duration_seconds=args.duration_seconds,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout_seconds,
        process_id=args.process_id,
        docker_container=args.docker_container,
        method=args.method,
        body=body,
        headers={"Content-Type": args.content_type} if body else None,
        data_path=args.data_path,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
