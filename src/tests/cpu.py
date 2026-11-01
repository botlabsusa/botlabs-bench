"""CPU single-core, all-core, and sustained stability benchmarks."""
from __future__ import annotations

import math
import multiprocessing as mp
import time
from typing import Any, Callable, Optional

ProgressCb = Callable[[str, float, Optional[dict]], None]
CancelCb = Callable[[], bool]


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    r = int(math.isqrt(n))
    for i in range(3, r + 1, 2):
        if n % i == 0:
            return False
    return True


def _worker_chunk(start: int, end: int) -> int:
    count = 0
    for n in range(start, end):
        if _is_prime(n):
            count += 1
    return count


def _burn_for(seconds: float) -> int:
    """CPU burn loop used by worker processes. Returns iteration count."""
    end = time.perf_counter() + seconds
    iters = 0
    x = 0.0
    # Mix float and integer work so the optimizer cannot erase it
    n = 3
    while time.perf_counter() < end:
        for i in range(2000):
            x += math.sin(i) * math.cos(i)
            n = (n * 1103515245 + 12345) & 0x7FFFFFFF
            if _is_prime((n % 9000) + 10007):
                x += 1.0
        iters += 1
    if x < -1e300:
        return 0
    return iters


def _prime_range_score(start: int, end: int) -> tuple[float, int, float]:
    t0 = time.perf_counter()
    count = _worker_chunk(start, end)
    dt = max(time.perf_counter() - t0, 1e-9)
    return count / dt, count, dt


def _parallel_burn(seconds: float, workers: int) -> tuple[float, float]:
    """Return (total_iters_per_sec, elapsed)."""
    if workers <= 1:
        t0 = time.perf_counter()
        total = _burn_for(seconds)
        dt = max(time.perf_counter() - t0, 1e-9)
        return total / dt, dt

    # Each worker burns for the same wall duration
    t0 = time.perf_counter()
    with mp.get_context("spawn").Pool(processes=workers) as pool:
        counts = pool.map(_burn_for, [seconds] * workers)
    dt = max(time.perf_counter() - t0, 1e-9)
    # Score = aggregate useful work / wall time
    return sum(counts) / dt, dt


def _busy_work(seconds: float, cancel: CancelCb) -> float:
    end = time.perf_counter() + seconds
    iters = 0
    x = 0.0
    while time.perf_counter() < end:
        if cancel():
            break
        for i in range(5000):
            x += math.sin(i) * math.cos(i)
        iters += 1
    return float(iters)


def run_cpu_tests(
    duration_sec: float,
    single_pass: bool,
    progress: ProgressCb,
    cancel: CancelCb,
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "name": "cpu",
        "status": "ok",
        "single_core": {},
        "all_core": {},
        "sustained": {},
        "start_method": "spawn",  # forced for Win/Linux parity (see version.CPU_METHOD_NOTES)
        "range_end": 250_000,
        "algorithm": "count_primes_in_range+burn_float_ops",
        "normalization": "aggregate_iters_or_primes / wall_time",
    }
    logical = mp.cpu_count() or 1
    physical = max(1, logical // 2)

    RANGE_END = 250_000
    # Timed burn window - short for single-pass, longer when budget allows
    burn_sec = 3.0 if single_pass or duration_sec <= 0 else min(15.0, max(3.0, duration_sec * 0.05))

    progress("CPU single-core", 0.05, None)
    if cancel():
        results["status"] = "cancelled"
        return results

    # Single-core: prime sieve score (easy to read) + timed burn baseline
    score1, count1, dt1 = _prime_range_score(2, RANGE_END)
    burn1, burn1_dt = _parallel_burn(burn_sec, workers=1)
    results["single_core"] = {
        "score_primes_per_sec": round(score1, 2),
        "range": f"2..{RANGE_END}",
        "primes_found": count1,
        "elapsed_sec": round(dt1, 3),
        "burn_iters_per_sec": round(burn1, 2),
        "burn_seconds": round(burn_sec, 2),
        "workers": 1,
    }
    progress("CPU single-core", 0.30, {"score": round(score1, 1), "burn": round(burn1, 1)})

    if cancel():
        results["status"] = "cancelled"
        return results

    progress("CPU all-core", 0.35, None)
    burn_all, burn_all_dt = _parallel_burn(burn_sec, workers=logical)
    speedup = (burn_all / burn1) if burn1 > 0 else 0.0
    results["all_core"] = {
        "burn_iters_per_sec": round(burn_all, 2),
        "burn_seconds": round(burn_sec, 2),
        "elapsed_sec": round(burn_all_dt, 3),
        "workers": logical,
        "logical_cores": logical,
        "physical_cores_est": physical,
        "speedup_vs_single": round(speedup, 2),
        # Keep a primes/sec field for report compatibility: scale by speedup
        "score_primes_per_sec": round(score1 * speedup, 2),
    }
    progress("CPU all-core", 0.55, {"burn": round(burn_all, 1), "speedup": round(speedup, 2)})

    if single_pass or duration_sec <= 0:
        results["sustained"] = {"skipped": True, "reason": "single-pass mode"}
        progress("CPU sustained", 1.0, None)
        return results

    progress("CPU sustained load", 0.58, None)
    samples = []
    sample_len = 5.0
    n_samples = max(2, int(duration_sec / sample_len))
    if n_samples > 120:
        sample_len = duration_sec / 120
        n_samples = 120

    t_start = time.perf_counter()
    for i in range(n_samples):
        if cancel():
            results["status"] = "cancelled"
            break
        iters = _busy_work(sample_len, cancel)
        rate = iters / sample_len
        samples.append({"t": round(time.perf_counter() - t_start, 2), "iters_per_sec": round(rate, 2)})
        frac = 0.58 + 0.40 * ((i + 1) / n_samples)
        progress("CPU sustained load", frac, {"sample": i + 1, "of": n_samples, "rate": round(rate, 1)})

    if samples:
        rates = [s["iters_per_sec"] for s in samples]
        q = max(1, len(rates) // 5)
        first = sum(rates[:q]) / q
        last = sum(rates[-q:]) / q
        drop_pct = 0.0 if first <= 0 else max(0.0, (first - last) / first * 100.0)
        results["sustained"] = {
            "samples": samples,
            "avg_iters_per_sec": round(sum(rates) / len(rates), 2),
            "first_quintile_avg": round(first, 2),
            "last_quintile_avg": round(last, 2),
            "throttle_drop_pct": round(drop_pct, 2),
            "throttling_suspected": drop_pct > 15.0,
            "duration_sec": round(time.perf_counter() - t_start, 1),
        }
    else:
        results["sustained"] = {"skipped": True, "reason": "no samples"}

    progress("CPU done", 1.0, None)
    return results
