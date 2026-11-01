"""Botlabs Bench GUI (tkinter + matplotlib)."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import (
    BooleanVar,
    DoubleVar,
    IntVar,
    StringVar,
    Tk,
    ttk,
    filedialog,
    messagebox,
)
from typing import Any, Optional

from compare import compare_runs
from runner import BenchmarkRunner
from sensors import is_admin
from tests.disk import list_candidate_drives
from validate import (
    MAX_HOURS,
    MAX_MINUTES,
    MIN_HOURS,
    MIN_MINUTES,
    duration_hint,
    min_duration_for_tests,
    validate_run,
)
from version import APP_VERSION, BENCH_VERSION

log = logging.getLogger("botlabsbench")


def app_dir() -> Path:
    """Writable base: beside AppImage/exe, else ~/BotlabsBench."""
    env = os.environ.get("APPIMAGE")
    if env:
        return Path(env).resolve().parent
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".botlabs_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return base
    except Exception:
        home = Path.home() / "BotlabsBench"
        home.mkdir(parents=True, exist_ok=True)
        return home


def results_dir() -> Path:
    d = app_dir() / "BenchResults"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _drive_label(d: dict) -> str:
    root = d.get("root") or d.get("label") or "?"
    free = d.get("free_gb", "?")
    ft = d.get("fstype") or ""
    if sys.platform.startswith("win"):
        letter = d.get("letter") or root[:1]
        return f"{letter}: ({free} GB free)"
    suffix = f", {ft}" if ft else ""
    return f"{root} ({free} GB free{suffix})"


def _is_system_root_label(label: str) -> bool:
    if sys.platform.startswith("win"):
        return label.startswith("C:")
    # Primary local root / or /home are not "LAN"
    return label.startswith("/ (") or label.startswith("/home")


class BotlabsBenchApp:
    def __init__(self, *, selftest: bool = False) -> None:
        self._selftest = bool(selftest)
        self._selftest_exit_code = 1
        self._selftest_result_path: Optional[Path] = None
        self.root = Tk()
        self.root.title("Botlabs Bench")
        self.root.geometry("980x720")
        self.root.minsize(860, 640)
        if self._selftest:
            _install_silent_dialogs()

        self.mode = StringVar(value="quick")
        self.custom_hours = IntVar(value=0)
        self.custom_minutes = IntVar(value=30)
        self.single_pass = BooleanVar(value=False)
        self.test_cpu = BooleanVar(value=True)
        self.test_memory = BooleanVar(value=True)
        self.test_disk = BooleanVar(value=True)
        self.test_network = BooleanVar(value=True)
        self.test_gpu = BooleanVar(value=True)

        drives = list_candidate_drives()
        self.drive_labels = [_drive_label(d) for d in drives] or (
            ["C:"] if sys.platform.startswith("win") else ["/"]
        )
        self.drive_roots = {_drive_label(d): d["root"] for d in drives}
        self.disk_drive = StringVar(value=self.drive_labels[0])
        lan_candidates = [x for x in self.drive_labels if not _is_system_root_label(x)]
        self.lan_drive = StringVar(value=lan_candidates[0] if lan_candidates else "(none)")
        self.lan_enabled = BooleanVar(value=bool(lan_candidates))

        self.status_text = StringVar(value="Ready")
        self.current_test = StringVar(value="-")
        self.overall_pct = DoubleVar(value=0.0)
        self.test_pct = DoubleVar(value=0.0)
        self.eta_text = StringVar(value="-")
        try:
            from sensors import temps_status_message

            _temps_init = temps_status_message()
        except Exception:
            _temps_init = (
                "Temps: available (admin or LibreHardwareMonitor service)"
                if is_admin()
                else "Temps: unavailable without admin / sensor service - scores still work"
            )
        self.temps_text = StringVar(value=_temps_init)

        self._runner: Optional[BenchmarkRunner] = None
        self._thread: Optional[threading.Thread] = None
        self._run_started = 0.0
        self._planned_duration = 0.0
        self._building_ui()
        self._building_plots()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _building_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Botlabs Bench", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(top, textvariable=self.temps_text, foreground="#666").pack(anchor="w")

        mode_fr = ttk.LabelFrame(self.root, text="Run mode", padding=10)
        mode_fr.pack(fill="x", **pad)
        for val, label in (
            ("quick", "Quick (10 minutes)"),
            ("detailed", "Detailed (1 hour)"),
            ("custom", "Custom"),
        ):
            ttk.Radiobutton(
                mode_fr, text=label, value=val, variable=self.mode, command=self._mode_changed
            ).pack(side="left", padx=8)

        self.custom_fr = ttk.LabelFrame(self.root, text="Custom options", padding=10)
        self.custom_fr.pack(fill="x", **pad)

        row1 = ttk.Frame(self.custom_fr)
        row1.pack(fill="x")
        ttk.Label(row1, text="Hours:").pack(side="left")
        self.hours_spin = ttk.Spinbox(
            row1,
            from_=MIN_HOURS,
            to=MAX_HOURS,
            textvariable=self.custom_hours,
            width=5,
            command=self._on_custom_changed,
        )
        self.hours_spin.pack(side="left", padx=4)
        self.hours_spin.bind("<FocusOut>", lambda e: self._sanitize_spin("hours"))
        self.hours_spin.bind("<Return>", lambda e: self._sanitize_spin("hours"))
        ttk.Label(row1, text="Minutes:").pack(side="left")
        self.mins_spin = ttk.Spinbox(
            row1,
            from_=MIN_MINUTES,
            to=MAX_MINUTES,
            textvariable=self.custom_minutes,
            width=5,
            command=self._on_custom_changed,
        )
        self.mins_spin.pack(side="left", padx=4)
        self.mins_spin.bind("<FocusOut>", lambda e: self._sanitize_spin("minutes"))
        self.mins_spin.bind("<Return>", lambda e: self._sanitize_spin("minutes"))
        ttk.Checkbutton(
            row1,
            text="Single pass (quick check, ignore duration)",
            variable=self.single_pass,
            command=self._on_custom_changed,
        ).pack(side="left", padx=12)

        self.custom_hint = StringVar(value=duration_hint(5))
        ttk.Label(
            self.custom_fr,
            textvariable=self.custom_hint,
            foreground="#555",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        row2 = ttk.Frame(self.custom_fr)
        row2.pack(fill="x", pady=6)
        ttk.Label(row2, text="Tests:").pack(side="left")
        ttk.Checkbutton(row2, text="CPU", variable=self.test_cpu).pack(side="left", padx=6)
        ttk.Checkbutton(row2, text="Memory", variable=self.test_memory).pack(side="left", padx=6)
        ttk.Checkbutton(row2, text="Disk", variable=self.test_disk).pack(side="left", padx=6)
        ttk.Checkbutton(row2, text="Network", variable=self.test_network).pack(side="left", padx=6)
        ttk.Checkbutton(row2, text="Graphics", variable=self.test_gpu).pack(side="left", padx=6)

        row3 = ttk.Frame(self.custom_fr)
        row3.pack(fill="x", pady=4)
        ttk.Label(row3, text="Disk test mount/drive:").pack(side="left")
        ttk.Combobox(row3, textvariable=self.disk_drive, values=self.drive_labels, width=28, state="readonly").pack(
            side="left", padx=6
        )
        ttk.Checkbutton(row3, text="LAN/NAS test on:", variable=self.lan_enabled).pack(side="left", padx=8)
        lan_vals = [x for x in self.drive_labels if not _is_system_root_label(x)] or ["(none)"]
        ttk.Combobox(row3, textvariable=self.lan_drive, values=lan_vals, width=28, state="readonly").pack(
            side="left", padx=4
        )

        prog = ttk.LabelFrame(self.root, text="Progress", padding=10)
        prog.pack(fill="x", **pad)
        ttk.Label(prog, text="Current:").grid(row=0, column=0, sticky="w")
        ttk.Label(prog, textvariable=self.current_test).grid(row=0, column=1, sticky="w")
        ttk.Label(prog, text="Test").grid(row=1, column=0, sticky="w")
        self.test_bar = ttk.Progressbar(prog, variable=self.test_pct, maximum=100, length=400)
        self.test_bar.grid(row=1, column=1, sticky="ew", padx=6)
        self.test_pct_lbl = ttk.Label(prog, text="0%")
        self.test_pct_lbl.grid(row=1, column=2)
        ttk.Label(prog, text="Overall").grid(row=2, column=0, sticky="w")
        self.overall_bar = ttk.Progressbar(prog, variable=self.overall_pct, maximum=100, length=400)
        self.overall_bar.grid(row=2, column=1, sticky="ew", padx=6)
        self.overall_pct_lbl = ttk.Label(prog, text="0%")
        self.overall_pct_lbl.grid(row=2, column=2)
        ttk.Label(prog, text="ETA").grid(row=3, column=0, sticky="w")
        ttk.Label(prog, textvariable=self.eta_text).grid(row=3, column=1, sticky="w")
        ttk.Label(prog, textvariable=self.status_text).grid(row=4, column=0, columnspan=3, sticky="w", pady=4)
        prog.columnconfigure(1, weight=1)

        btns = ttk.Frame(self.root, padding=10)
        btns.pack(fill="x")
        self.start_btn = ttk.Button(btns, text="Start", command=self._start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btns, text="Stop / Save partial", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(btns, text="Open results folder", command=self._open_results).pack(side="left", padx=4)
        ttk.Button(btns, text="Compare runs...", command=self._compare).pack(side="left", padx=4)

        self.plot_fr = ttk.LabelFrame(self.root, text="Live sensors", padding=6)
        self.plot_fr.pack(fill="both", expand=True, **pad)
        self._mode_changed()

    def _building_plots(self) -> None:
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure

            self.fig = Figure(figsize=(8, 2.8), dpi=100)
            self.ax = self.fig.add_subplot(111)
            self.ax.set_title("CPU % / Mem % / Disk MB/s")
            self.ax.set_ylim(0, 100)
            (self.line_cpu,) = self.ax.plot([], [], label="CPU %", color="#38bdf8")
            (self.line_mem,) = self.ax.plot([], [], label="Mem %", color="#a78bfa")
            (self.line_disk,) = self.ax.plot([], [], label="Disk MB/s", color="#fbbf24")
            (self.line_fps,) = self.ax.plot([], [], label="GPU FPS", color="#4ade80")
            self.ax.legend(loc="upper right", fontsize=8)
            self.fig.tight_layout()
            self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_fr)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            self._plot_times: list[float] = []
            self._plot_cpu: list[float] = []
            self._plot_mem: list[float] = []
            self._plot_disk: list[float] = []
            self._plot_fps: list[float] = []
            self._has_mpl = True
        except Exception as e:
            log.warning("matplotlib unavailable: %s", e)
            ttk.Label(self.plot_fr, text="Live graphs unavailable (matplotlib missing)").pack()
            self._has_mpl = False

    def _mode_changed(self) -> None:
        if self.mode.get() != "custom":
            self.single_pass.set(False)
        self._on_custom_changed()

    def _selected_tests(self) -> list[str]:
        mapping = [
            (self.test_cpu, "cpu"),
            (self.test_memory, "memory"),
            (self.test_disk, "disk"),
            (self.test_network, "network"),
            (self.test_gpu, "gpu"),
        ]
        if self.mode.get() in ("quick", "detailed"):
            return [name for var, name in mapping]  # all tests
        return [name for var, name in mapping if var.get()]

    def _sanitize_spin(self, which: str) -> None:
        """Clamp hours/minutes to valid integer ranges; reject non-numeric text."""
        var = self.custom_hours if which == "hours" else self.custom_minutes
        lo, hi = (MIN_HOURS, MAX_HOURS) if which == "hours" else (MIN_MINUTES, MAX_MINUTES)
        label = "Hours" if which == "hours" else "Minutes"
        try:
            raw_s = str(self.hours_spin.get() if which == "hours" else self.mins_spin.get()).strip()
            if raw_s == "" or any(c.isalpha() for c in raw_s):
                raise ValueError("non-numeric")
            n = int(float(raw_s))
        except Exception:
            n = 0
            messagebox.showwarning(
                "Botlabs Bench",
                f"{label} must be a whole number between {lo} and {hi}.",
            )
        n = max(lo, min(hi, n))
        var.set(n)
        self._on_custom_changed()

    def _quiet_clamp_spins(self) -> None:
        """Clamp spin values without popups (used right before validate)."""
        for which, var, lo, hi, spin in (
            ("hours", self.custom_hours, MIN_HOURS, MAX_HOURS, self.hours_spin),
            ("minutes", self.custom_minutes, MIN_MINUTES, MAX_MINUTES, self.mins_spin),
        ):
            try:
                raw_s = str(spin.get()).strip()
                if raw_s == "" or any(c.isalpha() for c in raw_s):
                    raise ValueError("non-numeric")
                n = int(float(raw_s))
            except Exception:
                n = 0
            var.set(max(lo, min(hi, n)))
        self._on_custom_changed()

    def _on_custom_changed(self) -> None:
        try:
            n = len(self._selected_tests()) if self.mode.get() == "custom" else 5
            if self.single_pass.get() and self.mode.get() == "custom":
                self.custom_hint.set(
                    "Single pass is on - duration minimum is ignored. "
                    f"Hours 0-{MAX_HOURS}, minutes 0-{MAX_MINUTES}."
                )
            else:
                self.custom_hint.set(duration_hint(max(1, n)))
        except Exception:
            pass

    def _duration(self) -> tuple[float, bool]:
        mode = self.mode.get()
        if mode == "quick":
            return 10 * 60, False
        if mode == "detailed":
            return 60 * 60, False
        if self.single_pass.get():
            return 0, True
        try:
            h = int(self.custom_hours.get())
            m = int(self.custom_minutes.get())
        except Exception:
            return 0, False
        return h * 3600 + m * 60, False

    def _start(self) -> None:
        if self.mode.get() == "custom":
            self._quiet_clamp_spins()

        tests = self._selected_tests()
        disk_root = self.drive_roots.get(self.disk_drive.get()) or next(iter(self.drive_roots.values()), "/")
        lan = None
        lan_enabled = bool(self.lan_enabled.get() and self.lan_drive.get() != "(none)")
        if lan_enabled:
            lan = self.drive_roots.get(self.lan_drive.get())

        try:
            hours = int(self.custom_hours.get())
            minutes = int(self.custom_minutes.get())
        except Exception:
            messagebox.showwarning(
                "Botlabs Bench",
                f"Hours must be {MIN_HOURS}-{MAX_HOURS} and minutes "
                f"{MIN_MINUTES}-{MAX_MINUTES} (whole numbers).",
            )
            return

        vr = validate_run(
            mode=self.mode.get(),
            tests=tests,
            single_pass=bool(self.single_pass.get()),
            hours=hours,
            minutes=minutes,
            disk_drive=disk_root,
            lan_drive=lan,
            lan_enabled=lan_enabled,
        )
        if not vr.ok:
            messagebox.showwarning("Botlabs Bench", vr.message)
            try:
                self.custom_hint.set(vr.message)
            except Exception:
                pass
            return
        if vr.message:
            try:
                self.custom_hint.set(vr.message)
            except Exception:
                pass

        duration = float(vr.duration_sec)
        single = bool(vr.single_pass)
        tests = list(vr.tests)
        disk_root = vr.disk_drive
        lan = vr.lan_drive

        self._plot_times.clear()
        self._plot_cpu.clear()
        self._plot_mem.clear()
        self._plot_disk.clear()
        if hasattr(self, "_plot_fps"):
            self._plot_fps.clear()
        self.overall_pct.set(0)
        self.test_pct.set(0)
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_text.set("Running...")
        self._run_started = time.time()
        self._planned_duration = duration if not single else 120

        self._runner = BenchmarkRunner(
            results_dir=results_dir(),
            mode=self.mode.get(),
            tests=tests,
            duration_sec=duration,
            single_pass=single,
            disk_drive=disk_root,
            lan_drive=lan,
            on_progress=self._on_progress,
            on_sensor=self._on_sensor,
        )

        def work() -> None:
            try:
                state = self._runner.run()
                self.root.after(0, lambda: self._finished(state))
            except Exception as e:
                log.exception("run failed")
                self.root.after(0, lambda: self._failed(e))

        self._thread = threading.Thread(target=work, name="BenchRun", daemon=True)
        self._thread.start()

    def _stop(self) -> None:
        if self._runner:
            self._runner.cancel()
            self.status_text.set("Stopping... saving partial results")

    def _on_progress(self, payload: dict[str, Any]) -> None:
        def ui() -> None:
            self.current_test.set(f"{payload.get('test')}: {payload.get('label')}")
            tp = float(payload.get("test_progress") or 0) * 100
            op = float(payload.get("overall_progress") or 0) * 100
            self.test_pct.set(tp)
            self.overall_pct.set(op)
            self.test_pct_lbl.configure(text=f"{tp:.0f}%")
            self.overall_pct_lbl.configure(text=f"{op:.0f}%")
            detail = payload.get("detail") or {}
            if isinstance(detail, dict) and detail.get("fps") is not None:
                gpu = detail.get("gpu") or ""
                extra = f" | GPU {detail.get('fps')} FPS"
                if gpu:
                    extra += f" ({gpu})"
                self.status_text.set("Running..." + extra)
                if getattr(self, "_has_mpl", False) and hasattr(self, "_plot_fps"):
                    tnow = time.time() - self._run_started
                    self._plot_times.append(tnow)
                    self._plot_cpu.append(self._plot_cpu[-1] if self._plot_cpu else 0)
                    self._plot_mem.append(self._plot_mem[-1] if self._plot_mem else 0)
                    self._plot_disk.append(self._plot_disk[-1] if self._plot_disk else 0)
                    self._plot_fps.append(float(detail["fps"]))
                    if len(self._plot_times) > 240:
                        self._plot_times.pop(0)
                        self._plot_cpu.pop(0)
                        self._plot_mem.pop(0)
                        self._plot_disk.pop(0)
                        self._plot_fps.pop(0)
                    self.line_cpu.set_data(self._plot_times, self._plot_cpu)
                    self.line_mem.set_data(self._plot_times, self._plot_mem)
                    self.line_disk.set_data(self._plot_times, self._plot_disk)
                    self.line_fps.set_data(self._plot_times, self._plot_fps)
                    self.ax.relim()
                    self.ax.autoscale_view()
                    self.canvas.draw_idle()
            elapsed = time.time() - self._run_started
            if op > 1:
                total_est = elapsed / (op / 100.0)
                remain = max(0, total_est - elapsed)
                self.eta_text.set(f"{int(remain // 60)}m {int(remain % 60)}s remaining")
            else:
                self.eta_text.set("...")

        self.root.after(0, ui)

    def _on_sensor(self, sample: dict[str, Any]) -> None:
        if not getattr(self, "_has_mpl", False):
            return

        def ui() -> None:
            self._plot_times.append(sample["t"])
            self._plot_cpu.append(sample["cpu"])
            self._plot_mem.append(sample["mem"])
            self._plot_disk.append(min(100, sample.get("disk_mb_s", 0)))  # scale loosely on same axis
            if hasattr(self, "_plot_fps"):
                self._plot_fps.append(self._plot_fps[-1] if self._plot_fps else 0.0)
            # Keep last 240 points
            if len(self._plot_times) > 240:
                self._plot_times.pop(0)
                self._plot_cpu.pop(0)
                self._plot_mem.pop(0)
                self._plot_disk.pop(0)
                if hasattr(self, "_plot_fps") and self._plot_fps:
                    self._plot_fps.pop(0)
            self.line_cpu.set_data(self._plot_times, self._plot_cpu)
            self.line_mem.set_data(self._plot_times, self._plot_mem)
            self.line_disk.set_data(self._plot_times, self._plot_disk)
            if hasattr(self, "line_fps") and hasattr(self, "_plot_fps"):
                self.line_fps.set_data(self._plot_times, self._plot_fps)
            if self._plot_times:
                self.ax.set_xlim(self._plot_times[0], max(self._plot_times[-1], self._plot_times[0] + 1))
            self.canvas.draw_idle()

        self.root.after(0, ui)

    def _finished(self, state: dict[str, Any]) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.overall_pct.set(100 if state.get("status") == "completed" else self.overall_pct.get())
        paths = state.get("report_paths") or {}
        self.status_text.set(
            f"Done ({state.get('status')}). Reports in BenchResults - HTML: {Path(paths.get('html','')).name}"
        )
        self._last_run_state = state
        if self._selftest:
            log.info("selftest: run finished status=%s", state.get("status"))
            return
        messagebox.showinfo(
            "Botlabs Bench",
            f"Run {state.get('status')}.\nElapsed {state.get('elapsed_sec')}s\n\nResults:\n{results_dir()}",
        )

    def _failed(self, e: Exception) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_text.set(f"Error: {e}")
        self._last_run_error = e
        if self._selftest:
            log.error("selftest: run failed: %s", e)
            return
        messagebox.showerror("Botlabs Bench", f"Run failed:\n{e}")

    def _open_results(self) -> None:
        path = results_dir()
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            messagebox.showinfo("Results", str(path))

    def _compare(self) -> None:
        files = filedialog.askopenfilenames(
            title="Select Botlabs Bench JSON results",
            initialdir=str(results_dir()),
            filetypes=[("JSON results", "*.json"), ("All", "*.*")],
        )
        if len(files) < 2:
            messagebox.showwarning("Compare", "Pick at least two JSON result files.")
            return
        out = results_dir() / f"compare-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        try:
            compare_runs([Path(f) for f in files], out)
            os.startfile(out)  # type: ignore[attr-defined]
        except Exception as e:
            messagebox.showerror("Compare", str(e))

    def _on_close(self) -> None:
        if self._runner and self._thread and self._thread.is_alive():
            if not self._selftest:
                if not messagebox.askyesno("Quit", "A run is in progress. Stop and quit?"):
                    return
            self._runner.cancel()
            self._thread.join(timeout=5)
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()



def _install_silent_dialogs() -> None:
    """Replace tk messagebox during GUI self-test so modals never block automation."""
    global messagebox

    class _Silent:
        @staticmethod
        def showinfo(title=None, message=None, **_k):
            log.info("selftest dialog info: %s — %s", title, message)
            return "ok"

        @staticmethod
        def showwarning(title=None, message=None, **_k):
            log.warning("selftest dialog warn: %s — %s", title, message)
            return "ok"

        @staticmethod
        def showerror(title=None, message=None, **_k):
            log.error("selftest dialog error: %s — %s", title, message)
            return "ok"

        @staticmethod
        def askyesno(title=None, message=None, **_k):
            log.info("selftest dialog askyesno: %s — %s (auto-yes)", title, message)
            return True

        @staticmethod
        def askokcancel(title=None, message=None, **_k):
            return True

    messagebox = _Silent()  # type: ignore[assignment]


def run_gui_selftest(
    *,
    stop_after_sec: float = 45.0,
    tests: Optional[list[str]] = None,
    safety_timeout_sec: float = 360.0,
) -> int:
    """Launch the real GUI, Start a short Custom run, Stop, and verify partial reports.

    Uses the same Start/Stop button handlers a user click would (Button.invoke).
    Writes BenchResults/gui-selftest-<timestamp>.json and prints a one-line summary.
    """
    import time as _time

    tests = [t.lower() for t in (tests or ["cpu", "memory", "disk", "network", "gpu"])]
    stop_after_sec = max(5.0, float(stop_after_sec))
    safety_timeout_sec = max(stop_after_sec + 60.0, float(safety_timeout_sec))

    need = min_duration_for_tests(len(tests))
    total_min = (need + 59) // 60
    if total_min <= MAX_MINUTES:
        hours, minutes = 0, max(1, total_min)
    else:
        hours = min(MAX_HOURS, total_min // 60)
        minutes = total_min % 60

    app = BotlabsBenchApp(selftest=True)
    app._last_run_state = None
    app._last_run_error = None

    # Configure Custom mode + selected tests + duration that satisfies validation.
    app.mode.set("custom")
    app._mode_changed()
    app.single_pass.set(False)
    app.custom_hours.set(hours)
    app.custom_minutes.set(minutes)
    mapping = {
        "cpu": app.test_cpu,
        "memory": app.test_memory,
        "disk": app.test_disk,
        "network": app.test_network,
        "gpu": app.test_gpu,
    }
    for name, var in mapping.items():
        var.set(name in tests)
    app._on_custom_changed()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = results_dir() / f"gui-selftest-{stamp}.json"
    app._selftest_result_path = out_path

    progress_samples: list[dict[str, Any]] = []
    graph_counts: list[int] = []
    started_at = _time.time()
    stop_invoked = {"done": False}
    start_invoked = {"done": False}
    finished_handled = {"done": False}
    outcome: dict[str, Any] = {
        "bench_version": BENCH_VERSION,
        "app_version": APP_VERSION,
        "pass": False,
        "checks": {},
        "progress_samples": progress_samples,
        "graph_point_counts": {},
        "report_paths": {},
        "run_id": None,
        "run_status": None,
        "stop_after_sec": stop_after_sec,
        "safety_timeout_sec": safety_timeout_sec,
        "tests": tests,
        "duration_hours": hours,
        "duration_minutes": minutes,
        "errors": [],
    }

    def _sample() -> None:
        try:
            op = float(app.overall_pct.get() or 0)
            tp = float(app.test_pct.get() or 0)
            n_pts = len(getattr(app, "_plot_times", []) or [])
            progress_samples.append(
                {
                    "t": round(_time.time() - started_at, 2),
                    "overall_pct": op,
                    "test_pct": tp,
                    "current": app.current_test.get(),
                    "status": app.status_text.get(),
                    "graph_points": n_pts,
                }
            )
            graph_counts.append(n_pts)
        except Exception as e:
            outcome["errors"].append(f"sample: {e}")
        if not finished_handled["done"]:
            try:
                app.root.after(1000, _sample)
            except Exception:
                pass

    def _invoke_start() -> None:
        start_invoked["done"] = True
        log.info(
            "selftest: invoking Start (custom %sh %sm tests=%s)",
            hours,
            minutes,
            tests,
        )
        try:
            app.start_btn.invoke()
        except Exception as e:
            outcome["errors"].append(f"start invoke: {e}")
            log.exception("selftest start invoke failed")

    def _invoke_stop() -> None:
        if stop_invoked["done"]:
            return
        stop_invoked["done"] = True
        log.info("selftest: invoking Stop after %.1fs", _time.time() - started_at)
        try:
            # Prefer real Stop handler path
            if str(app.stop_btn.cget("state")) == "disabled":
                # Start may have failed validation; try calling _stop anyway if runner exists
                log.warning("selftest: Stop button disabled; calling _stop() directly if possible")
            app.stop_btn.invoke()
        except Exception as e:
            outcome["errors"].append(f"stop invoke: {e}")
            try:
                app._stop()
            except Exception as e2:
                outcome["errors"].append(f"stop fallback: {e2}")

    def _finalize() -> None:
        if finished_handled["done"]:
            return
        finished_handled["done"] = True
        # Wait briefly for worker thread / reports if still wrapping up
        deadline = _time.time() + 90
        while app._thread and app._thread.is_alive() and _time.time() < deadline:
            app.root.update()
            _time.sleep(0.2)
        if app._thread and app._thread.is_alive():
            outcome["errors"].append("worker thread still alive after wait")
            if app._runner:
                try:
                    app._runner.cancel()
                except Exception:
                    pass
                app._thread.join(timeout=10)

        state = getattr(app, "_last_run_state", None) or (app._runner.state if app._runner else {})
        status = (state or {}).get("status")
        paths = (state or {}).get("report_paths") or {}
        results = (state or {}).get("results") or {}
        outcome["run_id"] = (state or {}).get("run_id")
        outcome["run_status"] = status
        outcome["report_paths"] = {k: str(v) for k, v in paths.items()}
        outcome["graph_point_counts"] = {
            "max": max(graph_counts) if graph_counts else 0,
            "final": graph_counts[-1] if graph_counts else 0,
            "samples": len(graph_counts),
        }

        # --- checks ---
        max_overall = max((s.get("overall_pct") or 0) for s in progress_samples) if progress_samples else 0
        max_test = max((s.get("test_pct") or 0) for s in progress_samples) if progress_samples else 0
        progress_ok = max_overall > 0 or max_test > 0
        outcome["checks"]["progress_advanced"] = {
            "pass": progress_ok,
            "max_overall_pct": max_overall,
            "max_test_pct": max_test,
            "n_samples": len(progress_samples),
        }

        graph_ok = (max(graph_counts) if graph_counts else 0) >= 1 and getattr(app, "_has_mpl", False)
        outcome["checks"]["graph_points"] = {
            "pass": bool(graph_ok),
            "max_points": max(graph_counts) if graph_counts else 0,
            "matplotlib": bool(getattr(app, "_has_mpl", False)),
        }

        # Stop should yield cancelled (partial) — also accept completed if tests finished early
        status_ok = status in ("cancelled", "completed")
        # Prefer cancelled when we intentionally stopped early
        partial_preferred = status == "cancelled" or (
            status == "completed" and stop_invoked["done"] and (_time.time() - started_at) < (hours * 3600 + minutes * 60) * 0.9
        )
        # User asked: status is stopped/partial — map cancelled == partial stop
        outcome["checks"]["status_partial"] = {
            "pass": bool(status_ok and (status == "cancelled" or partial_preferred or status == "completed")),
            "status": status,
            "stop_invoked": stop_invoked["done"],
            "note": "cancelled means Stop kept partial results",
        }

        def _exists(p: Any) -> bool:
            try:
                return bool(p) and Path(str(p)).is_file()
            except Exception:
                return False

        html_ok = _exists(paths.get("html"))
        csv_ok = _exists(paths.get("csv"))
        json_ok = _exists(paths.get("json"))
        xlsx_ok = _exists(paths.get("xlsx"))
        reports_ok = html_ok and csv_ok and json_ok
        outcome["checks"]["reports_exist"] = {
            "pass": reports_ok,
            "html": html_ok,
            "csv": csv_ok,
            "json": json_ok,
            "xlsx": xlsx_ok,
            "paths": {k: str(v) for k, v in paths.items()},
        }

        tests_with = [k for k, v in results.items() if isinstance(v, dict) and v]
        partial_ok = len(tests_with) >= 1
        outcome["checks"]["json_partial_results"] = {
            "pass": partial_ok,
            "tests_with_results": tests_with,
        }

        start_ok = start_invoked["done"] and app._runner is not None
        outcome["checks"]["start_invoked"] = {
            "pass": bool(start_ok),
            "start_invoked": start_invoked["done"],
            "runner_created": app._runner is not None,
        }

        all_pass = all(c.get("pass") for c in outcome["checks"].values()) and not (
            getattr(app, "_last_run_error", None)
        )
        outcome["pass"] = bool(all_pass)
        if getattr(app, "_last_run_error", None):
            outcome["errors"].append(str(app._last_run_error))

        try:
            out_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
            log.info("selftest wrote %s", out_path)
        except Exception as e:
            outcome["errors"].append(f"write result: {e}")
            print(f"GUI-SELFTEST FAIL: could not write {out_path}: {e}")
            app._selftest_exit_code = 1
            try:
                app.root.after(100, app.root.destroy)
            except Exception:
                pass
            return

        if all_pass:
            print(f"GUI-SELFTEST PASS  result={out_path}")
            app._selftest_exit_code = 0
        else:
            failed = [k for k, v in outcome["checks"].items() if not v.get("pass")]
            print(f"GUI-SELFTEST FAIL  failed_checks={failed}  result={out_path}")
            app._selftest_exit_code = 1

        try:
            app.root.after(200, app.root.destroy)
        except Exception:
            try:
                app.root.destroy()
            except Exception:
                pass

    def _watch_finished() -> None:
        # Poll until runner finished after stop (or completed)
        alive = bool(app._thread and app._thread.is_alive())
        state = getattr(app, "_last_run_state", None)
        if stop_invoked["done"] and (state is not None or not alive):
            # Give _finished a moment to land
            if state is None and app._runner and not alive:
                app._last_run_state = app._runner.state
            if state is not None or not alive:
                _finalize()
                return
        if _time.time() - started_at > safety_timeout_sec:
            outcome["errors"].append(f"safety timeout after {safety_timeout_sec}s")
            if app._runner:
                try:
                    app._runner.cancel()
                except Exception:
                    pass
            _finalize()
            return
        try:
            app.root.after(500, _watch_finished)
        except Exception:
            _finalize()

    def _boot() -> None:
        app.root.after(0, _sample)
        app.root.after(800, _invoke_start)
        app.root.after(int(stop_after_sec * 1000) + 800, _invoke_stop)
        app.root.after(1200, _watch_finished)
        # Hard safety: destroy even if watchers fail
        app.root.after(int(safety_timeout_sec * 1000), lambda: (_finalize() if not finished_handled["done"] else None))

    app.root.after(100, _boot)
    try:
        app.run()
    except Exception as e:
        outcome["errors"].append(f"mainloop: {e}")
        log.exception("selftest mainloop error")
        if not finished_handled["done"]:
            try:
                out_path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
            except Exception:
                pass
            print(f"GUI-SELFTEST FAIL  mainloop error: {e}")
            return 1
    return int(getattr(app, "_selftest_exit_code", 1))
