"""Memory bandwidth, latency, and pattern error-check."""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import numpy as np
import psutil

ProgressCb = Callable[[str, float, Optional[dict]], None]
CancelCb = Callable[[], bool]


def _bandwidth_mb_s(op: str, size_mb: int, repeats: int, cancel: CancelCb) -> float:
    n = size_mb * (1024 * 1024) // 8  # float64
    a = np.empty(n, dtype=np.float64)
    b = np.empty(n, dtype=np.float64)
    # Touch pages
    a[:] = 1.0
    b[:] = 2.0
    if cancel():
        return 0.0

    t0 = time.perf_counter()
    bytes_moved = 0
    for _ in range(repeats):
        if cancel():
            break
        if op == "write":
            a[:] = 3.14159
            bytes_moved += a.nbytes
        elif op == "read":
            s = float(np.sum(a))
            bytes_moved += a.nbytes
            if s == -1:  # prevent optimizing away
                a[0] = s
        else:  # copy
            np.copyto(b, a)
            bytes_moved += a.nbytes
    dt = max(time.perf_counter() - t0, 1e-9)
    return (bytes_moved / (1024 * 1024)) / dt


def _latency_ns(cancel: CancelCb) -> float:
    """Pointer-chase style latency using a random permutation walk on int array."""
    n = 2_000_000
    idx = np.arange(n, dtype=np.int64)
    rng = np.random.default_rng(42)
    rng.shuffle(idx)
    # Build cycle
    nxt = np.empty(n, dtype=np.int64)
    nxt[idx[:-1]] = idx[1:]
    nxt[idx[-1]] = idx[0]

    steps = 5_000_000
    i = int(idx[0])
    t0 = time.perf_counter()
    for s in range(steps):
        if (s & 0xFFFFF) == 0 and cancel():
            break
        i = int(nxt[i])
    dt = max(time.perf_counter() - t0, 1e-9)
    # Keep i live
    if i < 0:
        return 0.0
    return (dt / steps) * 1e9


def _error_check(size_mb: int, progress: ProgressCb, cancel: CancelCb, base_frac: float) -> dict[str, Any]:
    """Write/read patterned data over size_mb of RAM."""
    nbytes = size_mb * 1024 * 1024
    # Use uint32 patterns
    n = nbytes // 4
    # Cap to avoid OOM: never more than ~40% of available
    avail = psutil.virtual_memory().available
    max_n = int((avail * 0.40) // 4)
    n = min(n, max_n)
    if n < 1024 * 256:
        return {"skipped": True, "reason": "not enough free RAM for error check"}

    arr = np.empty(n, dtype=np.uint32)
    patterns = [0x00000000, 0xFFFFFFFF, 0xAAAAAAAA, 0x55555555, 0xC3C3C3C3]
    errors = 0
    checked_bytes = 0
    t0 = time.perf_counter()

    for pi, pat in enumerate(patterns):
        if cancel():
            return {
                "cancelled": True,
                "errors": errors,
                "bytes_checked": checked_bytes,
                "duration_sec": round(time.perf_counter() - t0, 2),
            }
        arr[:] = pat
        # Verify
        bad = int(np.count_nonzero(arr != pat))
        errors += bad
        checked_bytes += n * 4
        frac = base_frac + (0.95 - base_frac) * ((pi + 1) / len(patterns))
        progress("Memory error-check", frac, {"pattern": hex(pat), "errors": errors})

    # Address-dependent pattern
    if not cancel():
        # Fill with index xor constant in chunks to avoid huge temp arrays
        chunk = 4_000_000
        for start in range(0, n, chunk):
            if cancel():
                break
            end = min(n, start + chunk)
            idxs = np.arange(start, end, dtype=np.uint32)
            expected = (idxs * np.uint32(2654435761)) ^ np.uint32(0xA5A5A5A5)
            arr[start:end] = expected
        for start in range(0, n, chunk):
            if cancel():
                break
            end = min(n, start + chunk)
            idxs = np.arange(start, end, dtype=np.uint32)
            expected = (idxs * np.uint32(2654435761)) ^ np.uint32(0xA5A5A5A5)
            bad = int(np.count_nonzero(arr[start:end] != expected))
            errors += bad
            checked_bytes += (end - start) * 4
        progress("Memory error-check", 0.98, {"errors": errors})

    return {
        "errors": errors,
        "bytes_checked": checked_bytes,
        "size_mb": round(n * 4 / (1024 * 1024), 1),
        "passed": errors == 0,
        "duration_sec": round(time.perf_counter() - t0, 2),
    }


def run_memory_tests(
    duration_sec: float,
    single_pass: bool,
    progress: ProgressCb,
    cancel: CancelCb,
) -> dict[str, Any]:
    results: dict[str, Any] = {"name": "memory", "status": "ok"}
    progress("Memory bandwidth", 0.05, None)

    # Scale buffer with available RAM but keep modest for laptops
    avail_mb = int(psutil.virtual_memory().available / (1024 * 1024))
    size_mb = min(256, max(64, avail_mb // 20))
    repeats = 4 if single_pass else max(4, int(min(30, duration_sec / 8)))

    if cancel():
        results["status"] = "cancelled"
        return results

    write = _bandwidth_mb_s("write", size_mb, repeats, cancel)
    progress("Memory write bandwidth", 0.25, {"mb_s": round(write, 1)})
    read = _bandwidth_mb_s("read", size_mb, repeats, cancel)
    progress("Memory read bandwidth", 0.40, {"mb_s": round(read, 1)})
    copy = _bandwidth_mb_s("copy", size_mb, repeats, cancel)
    progress("Memory copy bandwidth", 0.55, {"mb_s": round(copy, 1)})

    results["bandwidth"] = {
        "buffer_mb": size_mb,
        "repeats": repeats,
        "write_mb_s": round(write, 2),
        "read_mb_s": round(read, 2),
        "copy_mb_s": round(copy, 2),
    }

    if cancel():
        results["status"] = "cancelled"
        return results

    progress("Memory latency", 0.60, None)
    lat = _latency_ns(cancel)
    results["latency_ns"] = round(lat, 2)
    progress("Memory latency", 0.70, {"ns": round(lat, 1)})

    # Error check: use more RAM / more time for longer runs
    err_mb = min(512 if single_pass else min(2048, max(256, int(duration_sec * 20))), avail_mb // 3)
    progress("Memory error-check", 0.72, {"size_mb": err_mb})
    results["error_check"] = _error_check(err_mb, progress, cancel, 0.72)

    if cancel():
        results["status"] = "cancelled"

    progress("Memory done", 1.0, None)
    return results
