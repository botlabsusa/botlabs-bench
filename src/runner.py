"""Orchestrate benchmark runs with progress, cancel, and incremental saves."""
from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from sensors import SensorSampler
from sysinfo import collect_sysinfo
from tests import (
    run_cpu_tests,
    run_disk_tests,
    run_gpu_tests,
    run_memory_tests,
    run_network_tests,
)
from reports import write_all_reports
from version import APP_VERSION, BENCH_VERSION, CPU_METHOD_NOTES

ProgressCb = Callable[[str, float, Optional[dict]], None]


class BenchmarkRunner:
    def __init__(
        self,
        results_dir: Path,
        mode: str,
        tests: list[str],
        duration_sec: float,
        single_pass: bool,
        disk_drive: str = "C:\\",
        lan_drive: Optional[str] = None,
        on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
        on_sensor: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.tests = [t.lower() for t in tests]
        self.duration_sec = float(duration_sec)
        self.single_pass = bool(single_pass)
        self.disk_drive = disk_drive
        self.lan_drive = lan_drive
        self.on_progress = on_progress
        self.on_sensor = on_sensor
        self._cancel = threading.Event()
        self.sampler = SensorSampler()
        if on_sensor:
            self.sampler.on_sample = on_sensor

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        host = collect_sysinfo().get("computer_name", "PC")
        self.run_id = f"{host}-{stamp}"
        self.json_path = self.results_dir / f"{self.run_id}.json"
        self.state: dict[str, Any] = {
            "app": "Botlabs Bench",
            "version": APP_VERSION,
            "bench_version": BENCH_VERSION,
            "cpu_method_notes": CPU_METHOD_NOTES,
            "run_id": self.run_id,
            "mode": mode,
            "tests_requested": list(self.tests),
            "duration_sec_requested": self.duration_sec,
            "single_pass": self.single_pass,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "status": "running",
            "sysinfo": collect_sysinfo(),
            "results": {},
            "sensor_summary": {},
            "errors": [],
        }
        self._save()

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _save(self) -> None:
        tmp = self.json_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        tmp.replace(self.json_path)

    def _emit(self, test: str, label: str, frac: float, detail: Optional[dict], overall: float) -> None:
        payload = {
            "test": test,
            "label": label,
            "test_progress": frac,
            "overall_progress": overall,
            "detail": detail or {},
            "run_id": self.run_id,
        }
        if self.on_progress:
            try:
                self.on_progress(payload)
            except Exception:
                pass

    def run(self) -> dict[str, Any]:
        self.sampler.start()
        t0 = time.time()
        try:
            n = max(1, len(self.tests))
            # Time budget per timed test (exclude purely single-pass portions handled inside)
            per_budget = 0.0 if self.single_pass else self.duration_sec / n

            for idx, name in enumerate(self.tests):
                if self.cancelled():
                    self.state["status"] = "cancelled"
                    break
                base = idx / n
                span = 1.0 / n

                def make_progress(test_name: str, base_=base, span_=span):
                    def _p(label: str, frac: float, detail: Optional[dict] = None) -> None:
                        overall = base_ + span_ * max(0.0, min(1.0, frac))
                        self._emit(test_name, label, frac, detail, overall)

                    return _p

                progress = make_progress(name)
                try:
                    if name == "cpu":
                        # Give CPU more of sustained budget if present
                        res = run_cpu_tests(per_budget, self.single_pass, progress, self.cancelled)
                    elif name == "memory":
                        res = run_memory_tests(per_budget, self.single_pass, progress, self.cancelled)
                    elif name == "disk":
                        res = run_disk_tests(
                            per_budget, self.single_pass, progress, self.cancelled, self.disk_drive
                        )
                    elif name == "network":
                        res = run_network_tests(
                            per_budget, self.single_pass, progress, self.cancelled, self.lan_drive
                        )
                    elif name == "gpu":
                        res = run_gpu_tests(per_budget, self.single_pass, progress, self.cancelled)
                    else:
                        res = {"name": name, "status": "skipped", "reason": "unknown test"}
                    self.state["results"][name] = res
                except Exception as e:
                    self.state["results"][name] = {
                        "name": name,
                        "status": "error",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                    self.state["errors"].append({"test": name, "error": str(e)})
                self._save()  # incremental

            if self.state["status"] == "running":
                self.state["status"] = "cancelled" if self.cancelled() else "completed"
        finally:
            snap = self.sampler.snapshot()
            self.sampler.stop()
            self.state["sensor_summary"] = {
                "temps_available": snap.get("temps_available"),
                "temps_note": snap.get("temps_note"),
                "cpu_avg": round(sum(snap["cpu"]) / len(snap["cpu"]), 2) if snap["cpu"] else None,
                "cpu_max": round(max(snap["cpu"]), 2) if snap["cpu"] else None,
                "mem_avg": round(sum(snap["mem"]) / len(snap["mem"]), 2) if snap["mem"] else None,
                "clock_avg_mhz": round(sum(snap["clock"]) / len(snap["clock"]), 1) if snap["clock"] else None,
                "samples": len(snap.get("times") or []),
            }
            # Keep a downsampled series for charts in HTML
            step = max(1, len(snap.get("times") or []) // 200)
            series = {}
            for key in ("times", "cpu", "mem", "clock", "temp", "disk_mb_s"):
                arr = snap.get(key) or []
                series[key] = [arr[i] for i in range(0, len(arr), step)]
            self.state["sensor_series"] = series
            self.state["finished_at"] = datetime.now().isoformat(timespec="seconds")
            self.state["elapsed_sec"] = round(time.time() - t0, 1)
            self._save()

            try:
                paths = write_all_reports(self.state, self.results_dir)
                self.state["report_paths"] = {k: str(v) for k, v in paths.items()}
                self._save()
            except Exception as e:
                self.state["errors"].append({"test": "reports", "error": str(e)})
                self._save()

        return self.state
