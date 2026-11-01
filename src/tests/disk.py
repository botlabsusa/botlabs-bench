"""Disk sequential and random 4K read/write benchmarks."""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

ProgressCb = Callable[[str, float, Optional[dict]], None]
CancelCb = Callable[[], bool]


def list_candidate_drives() -> list[dict[str, str]]:
    """List writable local / mapped volumes (Windows letters or Linux mounts)."""
    if sys.platform.startswith("win"):
        return _list_windows_drives()
    return _list_linux_mounts()


def _list_windows_drives() -> list[dict[str, str]]:
    drives = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        try:
            usage = shutil.disk_usage(root)
            drives.append(
                {
                    "letter": letter,
                    "root": root,
                    "label": f"{letter}:",
                    "free_gb": f"{usage.free / (1024**3):.1f}",
                    "total_gb": f"{usage.total / (1024**3):.1f}",
                    "fstype": "",
                }
            )
        except Exception:
            continue
    return drives


def _list_linux_mounts() -> list[dict[str, str]]:
    """Prefer /, /home, /mnt/*, /run/media/*, /media/*; include cifs/nfs."""
    interesting_fstypes = {
        "ext4", "ext3", "ext2", "xfs", "btrfs", "zfs", "f2fs", "ntfs", "ntfs3",
        "vfat", "exfat", "fuseblk", "fuse", "cifs", "nfs", "nfs4", "smb3",
        "tmpfs",  # only if explicitly under /mnt
    }
    prefer_prefixes = ("/", "/home", "/mnt", "/run/media", "/media", "/data", "/storage")
    seen_roots: set[str] = set()
    mounts: list[dict[str, str]] = []

    parts = []
    try:
        import psutil

        parts = list(psutil.disk_partitions(all=True))
    except Exception:
        parts = []

    candidates: list[tuple[int, str, str, str]] = []  # priority, mount, fstype, device
    if parts:
        for p in parts:
            mp = p.mountpoint
            ft = (p.fstype or "").lower()
            if not mp or not os.path.isdir(mp):
                continue
            # Skip virtual/system mounts
            if mp.startswith(("/proc", "/sys", "/dev", "/run/user", "/snap", "/var/lib/docker")):
                continue
            if ft in ("proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "squashfs", "overlay"):
                continue
            if ft == "tmpfs" and not mp.startswith(("/mnt", "/media", "/run/media")):
                continue
            prio = 50
            if mp == "/":
                prio = 0
            elif mp == "/home" or mp.startswith("/home/"):
                prio = 1
            elif mp.startswith("/mnt"):
                prio = 2
            elif mp.startswith(("/run/media", "/media")):
                prio = 3
            elif ft in ("cifs", "nfs", "nfs4", "smb3"):
                prio = 4
            elif ft in interesting_fstypes or ft.startswith("fuse"):
                prio = 10
            else:
                # still allow if under prefer prefixes
                if not any(mp == pref or mp.startswith(pref.rstrip("/") + "/") for pref in prefer_prefixes if pref != "/"):
                    if mp != "/":
                        continue
            candidates.append((prio, mp, ft or "unknown", p.device or ""))
    else:
        for mp in ("/", "/home", "/mnt", "/media"):
            if os.path.isdir(mp):
                candidates.append((0 if mp == "/" else 2, mp, "unknown", ""))

    # Always ensure / is present
    if not any(c[1] == "/" for c in candidates):
        candidates.append((0, "/", "unknown", ""))

    candidates.sort(key=lambda x: (x[0], x[1]))
    for _prio, mp, ft, dev in candidates:
        root = os.path.abspath(mp)
        if not root.endswith(os.sep) and root != "/":
            # keep without forcing trailing slash for POSIX roots; normalize later
            pass
        key = root.rstrip("/") or "/"
        if key in seen_roots:
            continue
        try:
            usage = shutil.disk_usage(root)
        except Exception:
            continue
        # Skip unusable / agent mounts (near-zero capacity)
        if usage.total < 64 * 1024 * 1024:
            continue
        if "agent-store" in ft or "cursor" in (dev or "").lower():
            continue
        seen_roots.add(key)
        label = key if key != "/" else "/"
        short = label
        mounts.append(
            {
                "letter": short,  # reused as display key by GUI
                "root": root if root == "/" else root.rstrip("/") + "/",
                "label": short,
                "free_gb": f"{usage.free / (1024**3):.1f}",
                "total_gb": f"{usage.total / (1024**3):.1f}",
                "fstype": ft,
                "device": dev,
            }
        )
    return mounts


def _fs_key(path: Path) -> object:
    """Identify the mount/volume so we stay on the same filesystem."""
    try:
        target = path if path.exists() else path.parent
        while not target.exists() and target != target.parent:
            target = target.parent
        if sys.platform.startswith("win"):
            drive = Path(target).resolve().drive or Path(path).drive
            return (drive or "").upper()
        return target.resolve().stat().st_dev
    except Exception:
        return None


def _probe_writable(dir_path: Path) -> bool:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        probe = dir_path / ".botlabs_write_probe"
        probe.write_bytes(b"ok")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def resolve_writable_workdir(drive_root: str) -> tuple[Optional[Path], str]:
    """Pick a writable BotlabsBench_tmp on the same mount as drive_root.

    Order: <root>/BotlabsBench_tmp, $HOME/BotlabsBench_tmp (if same mount),
    tempfile on same mount, Windows Public/LOCALAPPDATA when on that volume.
    Returns (path, note) or (None, error_message).
    """
    import tempfile

    root = Path(drive_root)
    root_key = _fs_key(root)
    candidates: list[Path] = [root / "BotlabsBench_tmp"]

    home = Path.home()
    try:
        if home.exists() and _fs_key(home) == root_key:
            candidates.append(home / "BotlabsBench_tmp")
    except Exception:
        pass

    if sys.platform.startswith("win"):
        public = root / "Users" / "Public"
        if public.is_dir():
            candidates.append(public / "BotlabsBench_tmp")
        for envk in ("LOCALAPPDATA", "TEMP", "TMP"):
            val = os.environ.get(envk)
            if not val:
                continue
            p = Path(val)
            try:
                if p.exists() and _fs_key(p) == root_key:
                    candidates.append(p / "BotlabsBench_tmp")
            except Exception:
                pass
    else:
        for td in (Path(tempfile.gettempdir()), Path("/var/tmp"), Path("/tmp")):
            try:
                if td.exists() and _fs_key(td) == root_key:
                    candidates.append(td / "BotlabsBench_tmp")
            except Exception:
                pass

    tried: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        tried.append(key)
        if _probe_writable(c):
            return c, f"workdir={c}"

    # Last resort: unique temp dir under $HOME when it shares the mount
    try:
        if root_key is not None and home.exists() and _fs_key(home) == root_key:
            d = Path(tempfile.mkdtemp(prefix="BotlabsBench_tmp_", dir=str(home)))
            tried.append(str(d))
            if _probe_writable(d):
                return d, f"workdir={d}"
    except Exception as e:
        tried.append(f"mkdtemp(home):{e}")

    return (
        None,
        (
            f"No writable folder on mount '{drive_root}' for the disk test. "
            f"Tried: {', '.join(tried)}. Choose a writable mount (e.g. your home directory)."
        ),
    )


def _ensure_workdir(drive_root: str) -> Path:
    """Backward-compatible wrapper; raises PermissionError with a clear message."""
    path, note = resolve_writable_workdir(drive_root)
    if path is None:
        raise PermissionError(note)
    return path


def _cleanup(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            for p in path.glob("*"):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            try:
                path.rmdir()
            except Exception:
                pass
    except Exception:
        pass


def _seq_write(path: Path, size_mb: int, cancel: CancelCb) -> float:
    buf = os.urandom(1024 * 1024)
    written = 0
    t0 = time.perf_counter()
    with open(path, "wb", buffering=1024 * 1024) as f:
        for _ in range(size_mb):
            if cancel():
                break
            f.write(buf)
            written += len(buf)
        f.flush()
        os.fsync(f.fileno())
    dt = max(time.perf_counter() - t0, 1e-9)
    return (written / (1024 * 1024)) / dt


def _seq_read(path: Path, cancel: CancelCb) -> float:
    size = path.stat().st_size
    if size <= 0:
        return 0.0
    read = 0
    t0 = time.perf_counter()
    with open(path, "rb", buffering=1024 * 1024) as f:
        while True:
            if cancel():
                break
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            read += len(chunk)
    dt = max(time.perf_counter() - t0, 1e-9)
    return (read / (1024 * 1024)) / dt


def _random_4k(path: Path, mode: str, ops: int, cancel: CancelCb) -> float:
    size = path.stat().st_size
    if size < 4096:
        return 0.0
    nblocks = size // 4096
    offsets = [((i * 1103515245 + 12345) % nblocks) * 4096 for i in range(ops)]
    buf = b"\x5a" * 4096
    completed = 0
    t0 = time.perf_counter()
    flags = os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(path), flags)
    try:
        for off in offsets:
            if cancel():
                break
            if mode == "write":
                os.lseek(fd, off, os.SEEK_SET)
                os.write(fd, buf)
            else:
                os.lseek(fd, off, os.SEEK_SET)
                os.read(fd, 4096)
            completed += 1
        if mode == "write":
            os.fsync(fd)
    finally:
        os.close(fd)
    dt = max(time.perf_counter() - t0, 1e-9)
    return completed / dt


def run_disk_tests(
    duration_sec: float,
    single_pass: bool,
    progress: ProgressCb,
    cancel: CancelCb,
    drive_root: str = "",
) -> dict[str, Any]:
    if not drive_root:
        drive_root = "C:\\" if sys.platform.startswith("win") else "/"
    results: dict[str, Any] = {
        "name": "disk",
        "status": "ok",
        "drive": drive_root,
    }
    workdir = None
    testfile = None
    try:
        progress("Disk prepare", 0.02, {"drive": drive_root})
        workdir, wd_note = resolve_writable_workdir(drive_root)
        if workdir is None:
            results["status"] = "skipped"
            results["error"] = wd_note
            results["note"] = wd_note
            progress("Disk skipped (not writable)", 1.0, {"error": wd_note})
            return results
        results["workdir"] = str(workdir)
        results["workdir_note"] = wd_note
        testfile = workdir / "bench_seq.bin"

        from validate import (
            DISK_DEFAULT_SINGLE_MB,
            DISK_MAX_MB,
            check_disk_space,
        )

        if single_pass:
            size_mb = DISK_DEFAULT_SINGLE_MB
            rand_ops = 4000
            loops = 1
        else:
            size_mb = min(DISK_MAX_MB, max(DISK_DEFAULT_SINGLE_MB, int(duration_sec * 8)))
            rand_ops = min(40000, max(4000, int(duration_sec * 200)))
            loops = max(1, int(min(8, duration_sec / 30)))

        ok_space, space_msg, size_mb = check_disk_space(drive_root, size_mb)
        if not ok_space:
            raise RuntimeError(space_msg)
        if space_msg:
            results["size_note"] = space_msg
        results["size_mb_planned"] = size_mb

        seq_w = []
        seq_r = []
        for i in range(loops):
            if cancel():
                results["status"] = "cancelled"
                break
            progress("Disk sequential write", 0.05 + 0.35 * (i / loops), {"loop": i + 1, "size_mb": size_mb})
            w = _seq_write(testfile, size_mb, cancel)
            seq_w.append(w)
            progress("Disk sequential read", 0.40 + 0.25 * (i / loops), {"loop": i + 1})
            r = _seq_read(testfile, cancel)
            seq_r.append(r)

        results["sequential"] = {
            "size_mb": size_mb,
            "loops": len(seq_w),
            "write_mb_s_avg": round(sum(seq_w) / max(1, len(seq_w)), 2) if seq_w else 0,
            "read_mb_s_avg": round(sum(seq_r) / max(1, len(seq_r)), 2) if seq_r else 0,
            "write_mb_s_samples": [round(x, 2) for x in seq_w],
            "read_mb_s_samples": [round(x, 2) for x in seq_r],
        }

        if cancel():
            results["status"] = "cancelled"
        elif testfile.exists():
            progress("Disk random 4K write", 0.70, None)
            w_iops = _random_4k(testfile, "write", rand_ops, cancel)
            progress("Disk random 4K read", 0.85, None)
            r_iops = _random_4k(testfile, "read", rand_ops, cancel)
            results["random_4k"] = {
                "ops": rand_ops,
                "write_iops": round(w_iops, 2),
                "read_iops": round(r_iops, 2),
            }

        if not single_pass and duration_sec >= 60 and not cancel() and len(seq_w) >= 3:
            first = sum(seq_w[: max(1, len(seq_w) // 3)]) / max(1, len(seq_w[: max(1, len(seq_w) // 3)]))
            last = sum(seq_w[-max(1, len(seq_w) // 3) :]) / max(1, len(seq_w[-max(1, len(seq_w) // 3) :]))
            drop = 0.0 if first <= 0 else max(0.0, (first - last) / first * 100)
            results["sustained_write"] = {
                "first_third_mb_s": round(first, 2),
                "last_third_mb_s": round(last, 2),
                "slowdown_pct": round(drop, 2),
            }

        progress("Disk done", 1.0, None)
    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)
        progress("Disk error", 1.0, {"error": str(e)})
    finally:
        if testfile is not None:
            _cleanup(testfile)
        if workdir is not None:
            _cleanup(workdir)
    return results
