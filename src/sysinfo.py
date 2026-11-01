"""Collect system information for reports (Windows + Linux)."""
from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil


def _ps(cmd: str) -> str:
    if not sys.platform.startswith("win"):
        return ""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def get_cpu_name() -> str:
    if sys.platform.startswith("win"):
        name = _ps("(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)")
        if name:
            return name
    else:
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name") or line.lower().startswith("hardware"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
        try:
            out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=5)
            for line in (out.stdout or "").splitlines():
                if line.lower().startswith("model name:"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return platform.processor() or platform.machine() or "Unknown CPU"


def get_gpu_names() -> list[str]:
    if sys.platform.startswith("win"):
        out = _ps("Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name")
        if out:
            gpus = [line.strip() for line in out.splitlines() if line.strip()]
            return gpus or ["Unknown GPU"]
        return ["Unknown GPU"]

    gpus: list[str] = []
    # nvidia-smi
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            for line in (r.stdout or "").splitlines():
                if line.strip():
                    gpus.append(line.strip())
    except Exception:
        pass
    # DRM cards
    drm = Path("/sys/class/drm")
    if drm.is_dir():
        for card in sorted(drm.glob("card[0-9]")):
            vendor = ""
            device = ""
            try:
                vendor = (card / "device" / "vendor").read_text().strip()
                device = (card / "device" / "device").read_text().strip()
            except Exception:
                pass
            # Prefer uevent DRIVER / PCI_ID
            name = card.name
            try:
                uevent = (card / "device" / "uevent").read_text()
                for line in uevent.splitlines():
                    if line.startswith("DRIVER="):
                        name = f"{line.split('=',1)[1]} ({card.name})"
                        break
            except Exception:
                pass
            label = name
            if vendor or device:
                label = f"{name} [{vendor}:{device}]"
            if label not in gpus:
                # avoid duplicating nvidia already listed
                if gpus and "nvidia" in label.lower():
                    continue
                gpus.append(label)
    return gpus or ["Unknown GPU"]


def get_disks() -> list[dict[str, Any]]:
    disks = []
    try:
        parts = psutil.disk_partitions(all=not sys.platform.startswith("win"))
    except Exception:
        parts = []
    for part in parts:
        mp = part.mountpoint
        if sys.platform.startswith("linux"):
            if mp.startswith(("/proc", "/sys", "/dev", "/run/user", "/snap")):
                continue
            ft = (part.fstype or "").lower()
            if ft in ("proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "squashfs"):
                continue
        try:
            usage = psutil.disk_usage(mp)
            disks.append(
                {
                    "device": part.device,
                    "mount": mp,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024**3), 2),
                    "free_gb": round(usage.free / (1024**3), 2),
                }
            )
        except Exception:
            disks.append(
                {
                    "device": part.device,
                    "mount": mp,
                    "fstype": part.fstype,
                    "total_gb": None,
                    "free_gb": None,
                }
            )
    return disks


def collect_sysinfo() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    freq = None
    try:
        f = psutil.cpu_freq()
        if f:
            freq = {"current_mhz": f.current, "max_mhz": f.max}
    except Exception:
        pass

    # Extra Linux sensor snapshot for reports (optional, compatible extra keys OK)
    extras: dict[str, Any] = {}
    if sys.platform.startswith("linux"):
        try:
            from sensors import read_cpu_temps_c, read_extra_gpu_sensors

            temps = read_cpu_temps_c()
            if temps:
                extras["cpu_temp_c_max"] = max(temps)
                extras["cpu_temps_c"] = temps[:16]
            gpu_s = read_extra_gpu_sensors()
            if gpu_s:
                extras["gpu_sensors"] = gpu_s
        except Exception:
            pass

    info = {
        "computer_name": socket.gethostname(),
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "os_build": platform.version(),
        "architecture": platform.machine(),
        "cpu_name": get_cpu_name(),
        "cpu_physical_cores": psutil.cpu_count(logical=False) or 0,
        "cpu_logical_cores": psutil.cpu_count(logical=True) or 0,
        "cpu_freq": freq,
        "ram_total_gb": round(vm.total / (1024**3), 2),
        "ram_available_gb": round(vm.available / (1024**3), 2),
        "gpus": get_gpu_names(),
        "disks": get_disks(),
        "python": platform.python_version(),
    }
    info.update(extras)
    return info
