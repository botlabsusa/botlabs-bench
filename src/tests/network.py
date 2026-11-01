"""Internet download speed and LAN/NAS copy throughput."""
from __future__ import annotations

import os
import shutil
import ssl
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

ProgressCb = Callable[[str, float, Optional[dict]], None]
CancelCb = Callable[[], bool]

# Public download endpoints
DEFAULT_URLS = [
    "https://speed.cloudflare.com/__down?bytes=25000000",  # 25 MB
    "https://proof.ovh.net/files/10Mb.dat",
]

_SYSTEM_CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu/Arch
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL/Fedora
    "/etc/ssl/ca-bundle.pem",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
)


def _ssl_context() -> ssl.SSLContext:
    """Prefer OS trust store on Linux; fall back to certifi (bundled in frozen builds)."""
    if sys.platform.startswith("linux"):
        for ca in _SYSTEM_CA_CANDIDATES:
            if os.path.isfile(ca):
                try:
                    return ssl.create_default_context(cafile=ca)
                except Exception:
                    continue
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    # Windows / macOS default store, or last-resort defaults
    try:
        return ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx


def _download_speed(url: str, cancel: CancelCb, progress: ProgressCb, frac0: float, frac1: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    total = 0
    err = None
    try:
        ctx = _ssl_context()
        req = urllib.request.Request(url, headers={"User-Agent": "BotlabsBench/1.1"})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            while True:
                if cancel():
                    break
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                elapsed = max(time.perf_counter() - t0, 1e-9)
                mbps = (total * 8 / 1_000_000) / elapsed
                frac = frac0 + (frac1 - frac0) * min(1.0, total / 25_000_000)
                if total == len(chunk) or (total // (2_000_000)) != ((total - len(chunk)) // (2_000_000)):
                    progress("Internet download", frac, {"mbps": round(mbps, 2), "mb": round(total / 1e6, 1)})
    except Exception as e:
        err = str(e)
    dt = max(time.perf_counter() - t0, 1e-9)
    return {
        "url": url,
        "bytes": total,
        "seconds": round(dt, 2),
        "mbps": round((total * 8 / 1_000_000) / dt, 2) if total else 0.0,
        "mbyte_s": round((total / 1_000_000) / dt, 2) if total else 0.0,
        "error": err,
        "cancelled": cancel(),
    }


def _lan_copy(target_root: str, size_mb: int, cancel: CancelCb, progress: ProgressCb) -> dict[str, Any]:
    from tests.disk import resolve_writable_workdir, _cleanup

    work_path, note = resolve_writable_workdir(target_root)
    if work_path is None:
        return {"target": target_root, "error": note, "skipped": True}
    work = work_path
    src = None
    dst = None
    try:
        src = work / "lan_src.bin"
        dst = work / "lan_dst.bin"
        buf = os.urandom(1024 * 1024)
        t0 = time.perf_counter()
        with open(src, "wb") as f:
            for i in range(size_mb):
                if cancel():
                    break
                f.write(buf)
                if i % 8 == 0:
                    progress("LAN/NAS write", 0.55 + 0.15 * (i / size_mb), {"mb": i})
            f.flush()
            os.fsync(f.fileno())
        write_dt = max(time.perf_counter() - t0, 1e-9)
        write_mb_s = (src.stat().st_size / (1024 * 1024)) / write_dt

        if cancel():
            return {"cancelled": True, "write_mb_s": round(write_mb_s, 2), "workdir": str(work)}

        t1 = time.perf_counter()
        shutil.copyfile(src, dst)
        copy_dt = max(time.perf_counter() - t1, 1e-9)
        copy_mb_s = (dst.stat().st_size / (1024 * 1024)) / copy_dt
        progress("LAN/NAS copy", 0.90, {"mb_s": round(copy_mb_s, 1)})
        return {
            "target": target_root,
            "workdir": str(work),
            "size_mb": size_mb,
            "write_mb_s": round(write_mb_s, 2),
            "copy_mb_s": round(copy_mb_s, 2),
        }
    except Exception as e:
        return {"target": target_root, "error": str(e), "workdir": str(work)}
    finally:
        for p in (dst, src):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
        _cleanup(work)


def run_network_tests(
    duration_sec: float,
    single_pass: bool,
    progress: ProgressCb,
    cancel: CancelCb,
    lan_drive: Optional[str] = None,
) -> dict[str, Any]:
    results: dict[str, Any] = {"name": "network", "status": "ok"}
    progress("Internet speed", 0.05, None)

    downloads = []
    for i, url in enumerate(DEFAULT_URLS):
        if cancel():
            results["status"] = "cancelled"
            break
        if single_pass and i > 0:
            break
        if (not single_pass) and duration_sec < 30 and i > 0:
            break
        frac0 = 0.05 + 0.40 * (i / max(1, len(DEFAULT_URLS)))
        frac1 = 0.05 + 0.40 * ((i + 1) / max(1, len(DEFAULT_URLS)))
        d = _download_speed(url, cancel, progress, frac0, frac1)
        downloads.append(d)
        if d.get("mbps", 0) > 1:
            break

    best = max((d.get("mbps") or 0) for d in downloads) if downloads else 0.0
    errors = [d.get("error") for d in downloads if d.get("error")]
    results["internet"] = {
        "downloads": downloads,
        "best_mbps": best,
    }

    # If every attempt failed to download bytes, mark error (not ok @ 0 Mbps)
    if downloads and best <= 0 and not cancel():
        any_bytes = any((d.get("bytes") or 0) > 0 for d in downloads)
        if not any_bytes:
            reason = "; ".join(errors) if errors else "all downloads returned 0 bytes"
            results["status"] = "error"
            results["error"] = f"Internet download failed: {reason}"
            results["internet"]["error"] = reason

    if lan_drive and not cancel():
        progress("LAN/NAS throughput", 0.55, {"drive": lan_drive})
        size_mb = 64 if single_pass else min(512, max(64, int(duration_sec * 4)))
        results["lan"] = _lan_copy(lan_drive, size_mb, cancel, progress)
    else:
        results["lan"] = {"skipped": True, "reason": "no LAN/NAS drive selected"}

    if cancel():
        results["status"] = "cancelled"
    progress("Network done", 1.0, None)
    return results
