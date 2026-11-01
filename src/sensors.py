"""Sensor helpers: CPU%, memory%, clocks, optional temperatures (Windows + Linux)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Optional

import psutil

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps(cmd: str, timeout: float = 8.0) -> str:
    if not sys.platform.startswith("win"):
        return ""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _read_hwmon_temps() -> list[tuple[str, float]]:
    """Parse /sys/class/hwmon for temperature inputs (Celsius)."""
    out: list[tuple[str, float]] = []
    base = Path("/sys/class/hwmon")
    if not base.is_dir():
        return out
    for hw in sorted(base.glob("hwmon*")):
        try:
            name = (hw / "name").read_text(encoding="utf-8").strip()
        except Exception:
            name = hw.name
        for tf in sorted(hw.glob("temp*_input")):
            try:
                raw = int(tf.read_text().strip())
                # Usually millidegrees
                c = raw / 1000.0 if raw > 200 else float(raw)
                if 0 < c < 125:
                    label = name
                    labelf = Path(str(tf).replace("_input", "_label"))
                    if labelf.is_file():
                        try:
                            label = f"{name}/{labelf.read_text().strip()}"
                        except Exception:
                            pass
                    out.append((label, c))
            except Exception:
                continue
    return out


def _read_nvidia_smi() -> dict[str, Any]:
    """Best-effort NVIDIA GPU stats via nvidia-smi."""
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,temperature.gpu,utilization.gpu,clocks.sm,clocks.mem",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return {}
        line = r.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            return {}
        return {
            "name": parts[0],
            "temp_c": float(parts[1]) if parts[1] else None,
            "util_pct": float(parts[2]) if parts[2] else None,
            "clock_sm_mhz": float(parts[3]) if parts[3] else None,
            "clock_mem_mhz": float(parts[4]) if parts[4] else None,
        }
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _read_amdgpu_hwmon() -> dict[str, Any]:
    base = Path("/sys/class/drm")
    info: dict[str, Any] = {}
    if not base.is_dir():
        return info
    for card in sorted(base.glob("card*/device/hwmon/hwmon*")):
        try:
            name = (card / "name").read_text().strip()
        except Exception:
            name = "amdgpu"
        if "amdgpu" not in name.lower() and "radeon" not in name.lower():
            # still accept if temp present
            pass
        temps = []
        for tf in card.glob("temp*_input"):
            try:
                temps.append(int(tf.read_text().strip()) / 1000.0)
            except Exception:
                pass
        if temps:
            info["temp_c"] = max(temps)
            info["name"] = name
            break
    return info


def read_cpu_temps_c() -> Optional[list[float]]:
    """Best-effort CPU package/core temps."""
    if sys.platform.startswith("win"):
        out = _ps(
            r"""
$ErrorActionPreference='SilentlyContinue'
$temps=@()
try {
  $ns='root\OpenHardwareMonitor'
  Get-CimInstance -Namespace $ns -ClassName Sensor |
    Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU|Package|Core' } |
    ForEach-Object { $temps += [double]$_.Value }
} catch {}
if (-not $temps) {
  try {
    $ns='root\LibreHardwareMonitor'
    Get-CimInstance -Namespace $ns -ClassName Sensor |
      Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU|Package|Core' } |
      ForEach-Object { $temps += [double]$_.Value }
  } catch {}
}
if ($temps) { $temps -join ',' } else { '' }
"""
        )
        if not out:
            return None
        try:
            vals = [float(x) for x in out.split(",") if x.strip()]
            vals = [v for v in vals if 0 < v < 120]
            return vals or None
        except Exception:
            return None

    # Linux: psutil first, then hwmon
    vals: list[float] = []
    try:
        st = psutil.sensors_temperatures(fahrenheit=False) or {}
        for chip, entries in st.items():
            for e in entries:
                if e.current is None:
                    continue
                label = f"{chip} {e.label or ''}".lower()
                if any(k in label for k in ("cpu", "core", "package", "tctl", "tdie", "k10temp", "coretemp", "zenpower")):
                    if 0 < float(e.current) < 125:
                        vals.append(float(e.current))
        if not vals:
            for chip, entries in st.items():
                for e in entries:
                    if e.current is not None and 0 < float(e.current) < 125:
                        vals.append(float(e.current))
    except Exception:
        pass
    if not vals:
        for _label, c in _read_hwmon_temps():
            vals.append(c)
    return vals or None


def read_extra_gpu_sensors() -> dict[str, Any]:
    """NVIDIA / AMD extras for reports and live status."""
    nv = _read_nvidia_smi()
    if nv:
        return {"source": "nvidia-smi", **nv}
    amd = _read_amdgpu_hwmon()
    if amd:
        return {"source": "amdgpu-hwmon", **amd}
    return {}


def is_admin() -> bool:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except Exception:
        return False


def temps_status_message() -> str:
    temps = read_cpu_temps_c()
    gpu = read_extra_gpu_sensors()
    bits = []
    if temps:
        bits.append(f"CPU {max(temps):.0f}C")
    if gpu.get("temp_c") is not None:
        bits.append(f"GPU {gpu['temp_c']:.0f}C ({gpu.get('name') or gpu.get('source')})")
    if bits:
        return "Temps: " + " | ".join(bits)
    if sys.platform.startswith("win"):
        return "Temps: unavailable without admin / sensor service - scores still work"
    return "Temps: unavailable (no hwmon/psutil sensors) - scores still work"


class SensorSampler:
    """Background sampler for live graphs."""

    def __init__(self, max_points: int = 300, interval: float = 0.5):
        self.max_points = max_points
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.times: Deque[float] = deque(maxlen=max_points)
        self.cpu: Deque[float] = deque(maxlen=max_points)
        self.mem: Deque[float] = deque(maxlen=max_points)
        self.clock: Deque[float] = deque(maxlen=max_points)
        self.temp: Deque[float] = deque(maxlen=max_points)
        self.disk_mb_s: Deque[float] = deque(maxlen=max_points)
        self.temps_available = False
        self.temps_note = temps_status_message()
        self._last_disk = psutil.disk_io_counters()
        self._last_disk_t = time.time()
        self.on_sample: Optional[Callable[[dict[str, Any]], None]] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.t0 = time.time()
        psutil.cpu_percent(interval=None)
        self._thread = threading.Thread(target=self._loop, name="SensorSampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "times": list(self.times),
                "cpu": list(self.cpu),
                "mem": list(self.mem),
                "clock": list(self.clock),
                "temp": list(self.temp),
                "disk_mb_s": list(self.disk_mb_s),
                "temps_available": self.temps_available,
                "temps_note": self.temps_note,
            }

    def _loop(self) -> None:
        temp_fail = 0
        while not self._stop.is_set():
            now = time.time() - self.t0
            cpu = float(psutil.cpu_percent(interval=None))
            mem = float(psutil.virtual_memory().percent)
            clock = 0.0
            try:
                f = psutil.cpu_freq()
                if f and f.current:
                    clock = float(f.current)
            except Exception:
                pass

            disk_rate = 0.0
            try:
                dio = psutil.disk_io_counters()
                dt = time.time() - self._last_disk_t
                if dio and self._last_disk and dt > 0:
                    bytes_delta = (dio.read_bytes + dio.write_bytes) - (
                        self._last_disk.read_bytes + self._last_disk.write_bytes
                    )
                    disk_rate = max(0.0, bytes_delta / dt / (1024 * 1024))
                self._last_disk = dio
                self._last_disk_t = time.time()
            except Exception:
                pass

            temp_val = float("nan")
            temps = read_cpu_temps_c()
            gpu = read_extra_gpu_sensors()
            if temps:
                temp_val = max(temps)
                self.temps_available = True
                temp_fail = 0
            elif gpu.get("temp_c") is not None:
                temp_val = float(gpu["temp_c"])
                self.temps_available = True
                temp_fail = 0
            else:
                temp_fail += 1
                if temp_fail > 3:
                    self.temps_available = False
            self.temps_note = temps_status_message()

            sample = {
                "t": now,
                "cpu": cpu,
                "mem": mem,
                "clock": clock,
                "temp": temp_val,
                "disk_mb_s": disk_rate,
                "gpu_sensors": gpu,
            }
            with self.lock:
                self.times.append(now)
                self.cpu.append(cpu)
                self.mem.append(mem)
                self.clock.append(clock)
                self.temp.append(temp_val)
                self.disk_mb_s.append(disk_rate)

            if self.on_sample:
                try:
                    self.on_sample(sample)
                except Exception:
                    pass

            self._stop.wait(self.interval)
