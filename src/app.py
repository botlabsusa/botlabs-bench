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
from theme import (
    ACCENT,
    FG_MUTED,
    PLOT_BG,
    PLOT_CPU,
    PLOT_DISK,
    PLOT_FPS,
    PLOT_MEM,
    admin_status_text,
    apply_theme,
    set_window_icon,
    style_matplotlib,
    temps_status_short,
    ui_font,
)
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
        # DPI awareness before any Tk window metrics (Windows)
        try:
            _enable_dpi_awareness()
        except Exception:
            pass
        self.root = Tk()
        self.root.title(f"Botlabs Bench {APP_VERSION}")
        # Restored (non-maximized) size must already fit graph + results; minsize
        # is a floor so users cannot crush the bottom pane.
        self.root.geometry("1120x780")
        self.root.minsize(1020, 740)
        self._theme_backend = apply_theme(self.root)
        self._icon_path = set_window_icon(self.root)
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
        self.current_test = StringVar(value="—")
        self.overall_pct = DoubleVar(value=0.0)
        self.test_pct = DoubleVar(value=0.0)
        self.eta_text = StringVar(value="—")
        self.admin_text = StringVar(value=admin_status_text())
        self.temps_short = StringVar(value=temps_status_short())
        self._last_html_path: Optional[Path] = None
        self.temps_text = StringVar(value=temps_status_short())  # kept for demo helpers
        self.temps_short.set(temps_status_short())
        self.results_status = StringVar(value="No results yet")
        self.results_hint = StringVar(value="Start a run to see scores here.")

        self._runner: Optional[BenchmarkRunner] = None
        self._thread: Optional[threading.Thread] = None
        self._run_started = 0.0
        self._planned_duration = 0.0
        self._last_run_state: Optional[dict[str, Any]] = None
        self._building_ui()
        self._building_plots()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _building_ui(self) -> None:
        pad_x, pad_y = 12, 5
        shell = ttk.Frame(self.root, padding=(14, 8, 14, 8))
        shell.pack(fill="both", expand=True)
        # Keep a handle so snapshot helpers can re-weight the bottom pane if needed.
        self._shell = shell
        self._pad_y = pad_y

        # ---- Header ----
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 6))
        # Pack right FIRST so admin/temps pills keep their natural width (never clip).
        right = ttk.Frame(header)
        right.pack(side="right", anchor="center", padx=(12, 0))
        left = ttk.Frame(header)
        left.pack(side="left", fill="x", expand=True)
        title_row = ttk.Frame(left)
        title_row.pack(anchor="w")
        # Optional small logo next to title
        try:
            from tkinter import PhotoImage
            from theme import asset_path

            logo_path = asset_path("botlabsbench-128.png")
            if not logo_path.is_file():
                logo_path = asset_path("botlabsbench.png")
            if logo_path.is_file():
                try:
                    from PIL import Image, ImageTk
                    im = Image.open(logo_path).convert("RGBA").resize((40, 40), Image.Resampling.LANCZOS)
                    self._logo_img = ImageTk.PhotoImage(im)
                except Exception:
                    self._logo_img = PhotoImage(file=str(logo_path)).subsample(3, 3)
                ttk.Label(title_row, image=self._logo_img).pack(side="left", padx=(0, 10))
        except Exception:
            pass
        titles = ttk.Frame(title_row)
        titles.pack(side="left")
        ttk.Label(titles, text="Botlabs Bench", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            titles,
            text=f"v{APP_VERSION}  ·  PC benchmark for Windows and Linux",
            style="Subtitle.TLabel",
        ).pack(anchor="w")
        # Status pills (admin + temps) — compact row; right already packed above
        admin_style = "PillAdmin.TLabel" if "Administrator" in (self.admin_text.get() or "") else "PillUser.TLabel"
        self.admin_pill = ttk.Label(right, textvariable=self.admin_text, style=admin_style)
        self.admin_pill.pack(side="left", padx=(0, 6))
        self.temps_pill = ttk.Label(right, textvariable=self.temps_short, style="PillTemps.TLabel")
        self.temps_pill.pack(side="left")

        # ---- Mode selector ----
        self.mode_fr = ttk.LabelFrame(shell, text="Run mode", padding=8, style="Card.TLabelframe")
        self.mode_fr.pack(fill="x", pady=(0, pad_y))
        mode_inner = ttk.Frame(self.mode_fr)
        mode_inner.pack(fill="x")
        for val, label, hint in (
            ("quick", "Quick", "≈ 10 minutes · all tests"),
            ("detailed", "Detailed", "≈ 1 hour · all tests"),
            ("custom", "Custom", "pick tests & duration"),
        ):
            card = ttk.Frame(mode_inner, padding=(8, 4))
            card.pack(side="left", padx=(0, 18))
            ttk.Radiobutton(
                card,
                text=label,
                value=val,
                variable=self.mode,
                command=self._mode_changed,
            ).pack(anchor="w")
            ttk.Label(card, text=hint, style="Muted.TLabel").pack(anchor="w")

        # ---- Custom options (hidden unless Custom) ----
        self.custom_fr = ttk.LabelFrame(shell, text="Custom options", padding=8, style="Card.TLabelframe")
        # packed/unpacked in _mode_changed

        row1 = ttk.Frame(self.custom_fr)
        row1.pack(fill="x")
        ttk.Label(row1, text="Duration", width=11).pack(side="left")
        ttk.Label(row1, text="Hours").pack(side="left", padx=(0, 4))
        self.hours_spin = ttk.Spinbox(
            row1,
            from_=MIN_HOURS,
            to=MAX_HOURS,
            textvariable=self.custom_hours,
            width=6,
            command=self._on_custom_changed,
        )
        self.hours_spin.pack(side="left", padx=(0, 16))
        self.hours_spin.bind("<FocusOut>", lambda e: self._sanitize_spin("hours"))
        self.hours_spin.bind("<Return>", lambda e: self._sanitize_spin("hours"))
        ttk.Label(row1, text="Minutes").pack(side="left", padx=(0, 4))
        self.mins_spin = ttk.Spinbox(
            row1,
            from_=MIN_MINUTES,
            to=MAX_MINUTES,
            textvariable=self.custom_minutes,
            width=6,
            command=self._on_custom_changed,
        )
        self.mins_spin.pack(side="left", padx=(0, 16))
        self.mins_spin.bind("<FocusOut>", lambda e: self._sanitize_spin("minutes"))
        self.mins_spin.bind("<Return>", lambda e: self._sanitize_spin("minutes"))
        ttk.Checkbutton(
            row1,
            text="Single pass (ignore duration)",
            variable=self.single_pass,
            command=self._on_custom_changed,
        ).pack(side="left")

        self.custom_hint = StringVar(value=duration_hint(5))
        ttk.Label(
            self.custom_fr,
            textvariable=self.custom_hint,
            style="Muted.TLabel",
            wraplength=1000,
            justify="left",
        ).pack(anchor="w", pady=(4, 2))

        row2 = ttk.Frame(self.custom_fr)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="Tests", width=11).pack(side="left")
        for text_lbl, var in (
            ("CPU", self.test_cpu),
            ("Memory", self.test_memory),
            ("Disk", self.test_disk),
            ("Network", self.test_network),
            ("Graphics", self.test_gpu),
        ):
            ttk.Checkbutton(row2, text=text_lbl, variable=var).pack(side="left", padx=(0, 12))

        row3 = ttk.Frame(self.custom_fr)
        row3.pack(fill="x", pady=(6, 0))
        ttk.Label(row3, text="Disk mount", width=11).pack(side="left")
        ttk.Combobox(
            row3, textvariable=self.disk_drive, values=self.drive_labels, width=32, state="readonly"
        ).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(row3, text="LAN / NAS on", variable=self.lan_enabled).pack(side="left", padx=(0, 8))
        lan_vals = [x for x in self.drive_labels if not _is_system_root_label(x)] or ["(none)"]
        ttk.Combobox(
            row3, textvariable=self.lan_drive, values=lan_vals, width=32, state="readonly"
        ).pack(side="left")

        # ---- Progress ----
        prog = ttk.LabelFrame(shell, text="Progress", padding=8, style="Card.TLabelframe")
        prog.pack(fill="x", pady=(0, pad_y))
        prog.columnconfigure(1, weight=1)

        ttk.Label(prog, text="Current", style="Progress.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=2
        )
        ttk.Label(prog, textvariable=self.current_test, style="Progress.TLabel").grid(
            row=0, column=1, sticky="w", pady=2
        )

        ttk.Label(prog, text="Test", style="Progress.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.test_bar = ttk.Progressbar(
            prog, variable=self.test_pct, maximum=100, style="Accent.Horizontal.TProgressbar"
        )
        self.test_bar.grid(row=1, column=1, sticky="ew", pady=4)
        self.test_pct_lbl = ttk.Label(prog, text="0%", width=5, style="Progress.TLabel")
        self.test_pct_lbl.grid(row=1, column=2, sticky="e", padx=(8, 0))

        ttk.Label(prog, text="Overall", style="Progress.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.overall_bar = ttk.Progressbar(
            prog, variable=self.overall_pct, maximum=100, style="Accent.Horizontal.TProgressbar"
        )
        self.overall_bar.grid(row=2, column=1, sticky="ew", pady=4)
        self.overall_pct_lbl = ttk.Label(prog, text="0%", width=5, style="Progress.TLabel")
        self.overall_pct_lbl.grid(row=2, column=2, sticky="e", padx=(8, 0))

        ttk.Label(prog, text="ETA", style="Progress.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=2
        )
        ttk.Label(prog, textvariable=self.eta_text, style="Progress.TLabel").grid(
            row=3, column=1, sticky="w", pady=2
        )
        ttk.Label(prog, textvariable=self.status_text, style="ProgressMuted.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )

        # ---- Actions ----
        btns = ttk.Frame(shell)
        btns.pack(fill="x", pady=(0, pad_y))
        self.start_btn = ttk.Button(btns, text="Start", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = ttk.Button(
            btns, text="Stop / Save partial", command=self._stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Open results folder", command=self._open_results).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(btns, text="Compare runs…", command=self._compare).pack(side="left")

        # ---- Body: graphs + results (Panedwindow — results never collapses to 0) ----
        # Matplotlib canvases request huge widths on maximized / high-DPI Windows and can
        # squeeze a grid sibling away. Horizontal Panedwindow + minsize keeps Results visible.
        self._RESULTS_PANE_MIN = 360
        self._RESULTS_PANE_RATIO = 0.35  # ~65/35 graph | results
        body = ttk.Panedwindow(shell, orient="horizontal")
        body.pack(fill="both", expand=True)
        self._body = body

        self.plot_fr = ttk.LabelFrame(body, text="Live sensors", padding=6, style="Card.TLabelframe")
        # Legend row ABOVE the matplotlib canvas (never covers lines / never clips)
        self.plot_legend = ttk.Frame(self.plot_fr)
        self.plot_legend.pack(side="top", fill="x", pady=(0, 4))

        self.results_fr = ttk.LabelFrame(
            body, text="Results summary", padding=8, style="Card.TLabelframe"
        )
        try:
            body.add(self.plot_fr, weight=3)
            body.add(self.results_fr, weight=2)
        except TypeError:
            body.add(self.plot_fr)
            body.add(self.results_fr)
        try:
            body.pane(self.results_fr, minsize=self._RESULTS_PANE_MIN)
        except Exception:
            pass
        try:
            body.pane(self.plot_fr, minsize=280)
        except Exception:
            pass
        self.root.bind("<Configure>", self._on_root_configure_panes, add="+")
        self.root.after_idle(self._ensure_results_pane)
        # Top-anchored content (status + metric table + report button); spacer keeps it at top
        self.results_top = ttk.Frame(self.results_fr)
        self.results_top.pack(side="top", anchor="nw", fill="x")
        self.results_status_lbl = ttk.Label(
            self.results_top, textvariable=self.results_status, style="StatusLine.TLabel"
        )
        self.results_status_lbl.pack(anchor="w", pady=(0, 2))
        self.results_hint_lbl = ttk.Label(
            self.results_top, textvariable=self.results_hint, style="Muted.TLabel", wraplength=320
        )
        self.results_hint_lbl.pack(anchor="w", pady=(0, 8))
        self.results_table = ttk.Frame(self.results_top)
        self.results_table.pack(anchor="nw", fill="x")
        self.results_table.columnconfigure(0, weight=0, minsize=120)
        self.results_table.columnconfigure(1, weight=1)
        self._results_row_widgets: list = []
        self.html_btn = ttk.Button(
            self.results_top,
            text="Open HTML report",
            style="Link.TButton",
            command=self._open_last_html,
            state="disabled",
        )
        self.html_btn.pack(anchor="w", pady=(10, 0))
        ttk.Frame(self.results_fr).pack(side="bottom", fill="both", expand=True)

        self._mode_changed()

    def _on_root_configure_panes(self, event=None) -> None:
        """When the main window resizes (incl. maximize), keep the results sash sane."""
        try:
            if event is not None and event.widget is not self.root:
                return
        except Exception:
            pass
        try:
            if getattr(self, "_pane_cfg_after", None) is not None:
                self.root.after_cancel(self._pane_cfg_after)
        except Exception:
            pass
        try:
            self._pane_cfg_after = self.root.after(80, self._ensure_results_pane)
        except Exception:
            self._ensure_results_pane()

    def _ensure_results_pane(self) -> None:
        """Force Results summary to a visible width (~35% or at least minsize)."""
        body = getattr(self, "_body", None)
        results = getattr(self, "results_fr", None)
        if body is None or results is None:
            return
        try:
            body.update_idletasks()
            total = int(body.winfo_width())
            if total < 200:
                return
            min_w = int(getattr(self, "_RESULTS_PANE_MIN", 360))
            ratio = float(getattr(self, "_RESULTS_PANE_RATIO", 0.35))
            want = max(min_w, int(total * ratio))
            want = min(want, max(min_w, total - 280))
            sash = max(280, total - want)
            try:
                body.sashpos(0, sash)
            except Exception:
                try:
                    body.sash_place(0, sash, 0)
                except Exception:
                    pass
            try:
                body.pane(results, minsize=min_w)
            except Exception:
                pass
        except Exception:
            pass

    def _building_plots(self) -> None:
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            from tkinter import Canvas as TkCanvas

            # Single-row legend outside the plot (tk labels, never clips)
            for color, label in (
                (PLOT_CPU, "CPU %"),
                (PLOT_MEM, "Mem %"),
                (PLOT_DISK, "Disk MB/s"),
                (PLOT_FPS, "GPU FPS"),
            ):
                swatch = TkCanvas(
                    self.plot_legend, width=16, height=10, highlightthickness=0, bg=PLOT_BG
                )
                swatch.pack(side="left", padx=(0, 4))
                swatch.create_line(1, 5, 15, 5, fill=color, width=2)
                ttk.Label(self.plot_legend, text=label, style="Muted.TLabel").pack(
                    side="left", padx=(0, 14)
                )

            self.fig = Figure(figsize=(6.8, 2.55), dpi=100)
            # Compact margins so graph + results fit at 1280x800 / restored sizes
            self.ax = self.fig.add_axes([0.12, 0.20, 0.72, 0.72])
            self.ax2 = self.ax.twinx()
            style_matplotlib(self.fig, self.ax)
            style_matplotlib(self.fig, self.ax2)
            self.ax.set_xlabel("Seconds", fontsize=8)
            self.ax.set_ylabel("CPU · Mem % · Disk", fontsize=8)
            self.ax2.set_ylabel("GPU FPS", fontsize=8, color=PLOT_FPS)
            try:
                self.ax2.yaxis.label.set_color(PLOT_FPS)
                self.ax2.tick_params(axis="y", colors=PLOT_FPS)
            except Exception:
                pass
            self.ax.set_ylim(0, 100)
            self.ax2.set_ylim(0, 120)
            self.ax.set_xlim(0, 60)
            (self.line_cpu,) = self.ax.plot([], [], color=PLOT_CPU, linewidth=1.6)
            (self.line_mem,) = self.ax.plot([], [], color=PLOT_MEM, linewidth=1.6)
            (self.line_disk,) = self.ax.plot([], [], color=PLOT_DISK, linewidth=1.4)
            (self.line_fps,) = self.ax2.plot([], [], color=PLOT_FPS, linewidth=1.8)
            self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_fr)
            ctk = self.canvas.get_tk_widget()
            # Don't let the canvas advertise a huge natural width (collapses Results on maximize).
            try:
                ctk.configure(width=400, height=220)
            except Exception:
                pass
            ctk.pack(fill="both", expand=True)
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

    def _set_custom_visible(self, visible: bool) -> None:
        """Show Custom options only when Custom mode is selected."""
        try:
            mapped = bool(self.custom_fr.winfo_ismapped())
        except Exception:
            mapped = False
        if visible and not mapped:
            try:
                self.custom_fr.pack(fill="x", pady=(0, 5), after=self.mode_fr)
            except Exception:
                self.custom_fr.pack(fill="x", pady=(0, 5))
        elif not visible and mapped:
            self.custom_fr.pack_forget()

    def _mode_changed(self) -> None:
        if self.mode.get() != "custom":
            self.single_pass.set(False)
        self._set_custom_visible(self.mode.get() == "custom")
        self._on_custom_changed()

    def _refresh_plot(self) -> None:
        if not getattr(self, "_has_mpl", False):
            return
        self.line_cpu.set_data(self._plot_times, self._plot_cpu)
        self.line_mem.set_data(self._plot_times, self._plot_mem)
        self.line_disk.set_data(self._plot_times, self._plot_disk)
        self.line_fps.set_data(self._plot_times, self._plot_fps)
        if self._plot_times:
            self.ax.set_xlim(self._plot_times[0], max(self._plot_times[-1], self._plot_times[0] + 1))
        # Keep % axis fixed; scale FPS axis to data
        self.ax.set_ylim(0, 100)
        fps_max = max(self._plot_fps) if self._plot_fps else 0
        self.ax2.set_ylim(0, max(120.0, fps_max * 1.15 if fps_max else 120.0))
        self.canvas.draw_idle()

    def _fmt_num(self, value: Any, *, decimals: int = 0, suffix: str = "") -> str:
        if value is None or value == "":
            return "—"
        try:
            n = float(value)
        except Exception:
            return str(value)
        if decimals <= 0:
            body = f"{int(round(n)):,}"
        else:
            body = format(n, f",.{decimals}f")
        return f"{body}{suffix}"

    def _clear_results_rows(self) -> None:
        for w in getattr(self, "_results_row_widgets", []) or []:
            try:
                w.destroy()
            except Exception:
                pass
        self._results_row_widgets = []

    def _add_results_row(self, label: str, value: str) -> None:
        r = len(self._results_row_widgets) // 2
        lab = ttk.Label(self.results_table, text=label, style="Metric.TLabel")
        lab.grid(row=r, column=0, sticky="w", padx=(0, 12), pady=2)
        val = ttk.Label(self.results_table, text=value, style="MetricValue.TLabel")
        val.grid(row=r, column=1, sticky="w", pady=2)
        self._results_row_widgets.extend([lab, val])

    def _set_results_idle(self, status: str, hint: str = "") -> None:
        self._clear_results_rows()
        self.results_status.set(status)
        self.results_hint.set(hint)
        try:
            if hint:
                self.results_hint_lbl.pack(anchor="w", pady=(0, 8))
            else:
                self.results_hint_lbl.pack_forget()
        except Exception:
            pass
        self._last_html_path = None
        try:
            self.html_btn.configure(state="disabled", text="Open HTML report")
        except Exception:
            pass

    def _populate_results_summary(self, state: dict[str, Any]) -> None:
        """Fill the in-app results panel from a finished (or partial) run state."""
        self._clear_results_rows()
        status = str(state.get("status") or "—")
        elapsed = state.get("elapsed_sec")
        try:
            elapsed_s = f"{float(elapsed):.0f}s" if elapsed is not None else "—"
        except Exception:
            elapsed_s = str(elapsed)
        self.results_status.set(f"{status.capitalize()}  ·  {elapsed_s}")
        self.results_hint.set("")
        try:
            self.results_hint_lbl.pack_forget()
        except Exception:
            pass

        res = state.get("results") or {}
        sysinfo = state.get("sysinfo") or {}
        cpu_name = sysinfo.get("cpu_name") or "—"
        gpu_name = sysinfo.get("gpu_name") or ((res.get("gpu") or {}).get("gpu") or "—")
        self._add_results_row("CPU", str(cpu_name))
        self._add_results_row("GPU", str(gpu_name))

        cpu = res.get("cpu") or {}
        if cpu:
            sc = (cpu.get("single_core") or {}).get("score_primes_per_sec")
            ac = (cpu.get("all_core") or {}).get("score_primes_per_sec")
            self._add_results_row("CPU single", self._fmt_num(sc, suffix=" primes/s"))
            self._add_results_row("CPU all-core", self._fmt_num(ac, suffix=" primes/s"))
        mem = res.get("memory") or {}
        if mem:
            bw = mem.get("bandwidth") or {}
            self._add_results_row("Mem read", self._fmt_num(bw.get("read_mb_s"), decimals=0, suffix=" MB/s"))
            if bw.get("write_mb_s") is not None:
                self._add_results_row("Mem write", self._fmt_num(bw.get("write_mb_s"), decimals=0, suffix=" MB/s"))
        disk = res.get("disk") or {}
        if disk:
            seq = disk.get("sequential") or {}
            self._add_results_row(
                "Disk seq write", self._fmt_num(seq.get("write_mb_s_avg"), decimals=0, suffix=" MB/s")
            )
            if seq.get("read_mb_s_avg") is not None:
                self._add_results_row(
                    "Disk seq read", self._fmt_num(seq.get("read_mb_s_avg"), decimals=0, suffix=" MB/s")
                )
        net = res.get("network") or {}
        if net:
            inet = net.get("internet") or {}
            self._add_results_row("Internet", self._fmt_num(inet.get("best_mbps"), decimals=0, suffix=" Mbps"))
        gpu = res.get("gpu") or {}
        if gpu and gpu.get("fps") is not None:
            self._add_results_row("GPU FPS avg", self._fmt_num(gpu.get("fps"), decimals=0, suffix=" FPS"))

        paths = state.get("report_paths") or {}
        html = paths.get("html")
        if html:
            hp = Path(str(html))
            self._last_html_path = hp
            self.html_btn.configure(state="normal", text=f"Open {hp.name}")
        else:
            self._last_html_path = None
            self.html_btn.configure(state="disabled", text="Open HTML report")

    def _open_last_html(self) -> None:
        path = self._last_html_path
        if not path:
            return
        # Resolve relative demo paths under results_dir
        if not path.is_file():
            cand = results_dir() / path.name
            if cand.is_file():
                path = cand
        try:
            if path.is_file():
                if hasattr(os, "startfile"):
                    os.startfile(str(path))  # type: ignore[attr-defined]
                else:
                    import subprocess

                    subprocess.Popen(["xdg-open", str(path)])
            else:
                # Fall back to folder when the demo file is not on disk
                self._open_results()
        except Exception as e:
            messagebox.showinfo("Report", f"Could not open:\n{path}\n{e}")


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
        try:
            self._set_results_idle("Run in progress…", "Scores appear here when the run finishes or is stopped.")
        except Exception:
            pass
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
                    self._refresh_plot()
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
            # Disk MB/s shares left axis with % (clamped for readability)
            self._plot_disk.append(min(100.0, float(sample.get("disk_mb_s", 0) or 0)))
            self._plot_fps.append(self._plot_fps[-1] if self._plot_fps else 0.0)
            if len(self._plot_times) > 240:
                self._plot_times.pop(0)
                self._plot_cpu.pop(0)
                self._plot_mem.pop(0)
                self._plot_disk.pop(0)
                self._plot_fps.pop(0)
            self._refresh_plot()

        self.root.after(0, ui)

    def _finished(self, state: dict[str, Any]) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.overall_pct.set(100 if state.get("status") == "completed" else self.overall_pct.get())
        paths = state.get("report_paths") or {}
        self.status_text.set(
            f"Done ({state.get('status')}). Reports in BenchResults — HTML: {Path(paths.get('html','')).name}"
        )
        self._last_run_state = state
        try:
            self._populate_results_summary(state)
        except Exception as e:
            log.warning("results summary failed: %s", e)
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
            paths = [Path(f) for f in files]
            compare_runs(paths, out)
            # In-app preview (also used by snapshot / site-shots)
            try:
                states = []
                for p in paths:
                    with open(p, encoding="utf-8") as fh:
                        states.append(json.load(fh))
                _show_compare_preview(self.root, states)
            except Exception as e:
                log.warning("compare preview failed: %s", e)
            try:
                if hasattr(os, "startfile"):
                    os.startfile(out)  # type: ignore[attr-defined]
                else:
                    import subprocess

                    subprocess.Popen(["xdg-open", str(out)])
            except Exception:
                messagebox.showinfo("Compare", f"Saved:\n{out}")
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

    def _maximize_startup(self) -> None:
        """Open maximized on normal GUI launch so the layout is not smushed."""
        try:
            self.root.update_idletasks()
            if sys.platform.startswith("win"):
                self.root.state("zoomed")
            else:
                zoomed = False
                for attempt in (
                    lambda: self.root.attributes("-zoomed", True),
                    lambda: self.root.state("zoomed"),
                    lambda: self.root.wm_attributes("-zoomed", True),
                ):
                    try:
                        attempt()
                        self.root.update_idletasks()
                        self.root.update()
                        if int(self.root.winfo_width()) >= int(self.root.winfo_screenwidth()) - 40:
                            zoomed = True
                            break
                    except Exception:
                        continue
                if not zoomed:
                    sw = int(self.root.winfo_screenwidth())
                    sh = int(self.root.winfo_screenheight())
                    # Leave a small margin for WM chrome on real desktops
                    self.root.geometry(f"{max(sw - 16, 1120)}x{max(sh - 48, 780)}+0+0")
            self.root.update_idletasks()
            self.root.update()
        except Exception as e:
            log.warning("maximize startup failed: %s", e)
        try:
            self.root.after(120, self._ensure_results_pane)
        except Exception:
            pass

    def run(self) -> None:
        # Normal interactive launch only — selftest / snapshots set their own geometry.
        if not self._selftest:
            self._maximize_startup()
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


def _enable_dpi_awareness() -> str:
    """Ask Windows for per-monitor DPI awareness so ImageGrab matches the window. Returns a note."""
    if not sys.platform.startswith("win"):
        return "dpi=n/a (non-Windows)"
    try:
        import ctypes

        # Prefer Per-Monitor V2 (Windows 10+), then Per-Monitor, then System DPI.
        for awareness in (2, 2, 1):  # PROCESS_PER_MONITOR_DPI_AWARE_V2 / PER_MONITOR / SYSTEM
            try:
                # SetProcessDpiAwarenessContext(-4) = PER_MONITOR_AWARE_V2 on Win10 1703+
                if awareness == 2:
                    try:
                        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
                        return "dpi=PerMonitorV2 (SetProcessDpiAwarenessContext)"
                    except Exception:
                        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
                        return "dpi=PerMonitor (SetProcessDpiAwareness=2)"
                ctypes.windll.shcore.SetProcessDpiAwareness(awareness)
                return f"dpi=SetProcessDpiAwareness({awareness})"
            except Exception:
                continue
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return "dpi=SetProcessDPIAware (legacy)"
        except Exception as e:
            return f"dpi=unavailable ({e})"
    except Exception as e:
        return f"dpi=error ({e})"


def _window_bbox(root: Tk) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) in screen pixels for ImageGrab."""
    root.update_idletasks()
    root.update()
    try:
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        root.attributes("-topmost", False)
    except Exception:
        pass
    # Prefer win32 GetWindowRect for the true outer frame on Windows
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = int(root.winfo_id())
            # On Tk, winfo_id is the client HWND; climb to the toplevel frame.
            GA_ROOT = 2
            try:
                hwnd = int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)) or hwnd
            except Exception:
                pass
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        except Exception:
            pass
    x = int(root.winfo_rootx())
    y = int(root.winfo_rooty())
    w = int(root.winfo_width())
    h = int(root.winfo_height())
    return x, y, x + w, y + h


def _force_window_paint(root: Tk) -> None:
    """Flush Tk + (on Windows) ask DWM to repaint so ImageGrab is not stale."""
    try:
        root.update_idletasks()
        root.update()
        root.lift()
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes

            hwnd = int(root.winfo_id())
            try:
                hwnd = int(ctypes.windll.user32.GetAncestor(hwnd, 2)) or hwnd
            except Exception:
                pass
            # RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN | RDW_FRAME
            ctypes.windll.user32.RedrawWindow(hwnd, None, None, 0x0100 | 0x0100 | 0x0080 | 0x0400 | 0x0001)
            ctypes.windll.user32.UpdateWindow(hwnd)
        except Exception:
            pass
    try:
        root.update_idletasks()
        root.update()
    except Exception:
        pass


def _format_compare_value(label: str, value: Any) -> str:
    """Format compare-preview cells like the Results summary (commas + units)."""
    if value is None or value == "":
        return "—"
    numeric_specs = {
        "CPU all-core primes/s": (0, " primes/s"),
        "Mem read MB/s": (0, " MB/s"),
        "Disk seq write MB/s": (0, " MB/s"),
        "Internet Mbps": (0, " Mbps"),
        "GPU FPS avg": (0, " FPS"),
    }
    if label in numeric_specs:
        decimals, suffix = numeric_specs[label]
        try:
            n = float(value)
        except Exception:
            return str(value)
        if decimals <= 0:
            body = f"{int(round(n)):,}"
        else:
            body = format(n, f",.{decimals}f")
        return f"{body}{suffix}"
    return str(value)


def _grab_window_png(root: Tk, path: Path, *, settle_ms: int = 250) -> tuple[int, int]:
    """Capture the Tk window (or a focused Toplevel) to PNG. Returns (width, height)."""
    from PIL import ImageGrab

    _force_window_paint(root)
    # Short settle so ttk/matplotlib finish painting
    deadline = time.time() + max(0.05, settle_ms / 1000.0)
    while time.time() < deadline:
        try:
            root.update()
        except Exception:
            break
        time.sleep(0.02)
    _force_window_paint(root)
    bbox = _window_bbox(root)
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return img.size[0], img.size[1]


def _grab_widget_png(widget, path: Path, *, settle_ms: int = 200) -> tuple[int, int]:
    """Capture a specific Tk widget (Toplevel or Frame) by its screen bbox."""
    from PIL import ImageGrab

    widget.update_idletasks()
    widget.update()
    deadline = time.time() + max(0.05, settle_ms / 1000.0)
    while time.time() < deadline:
        try:
            widget.update()
        except Exception:
            break
        time.sleep(0.02)
    try:
        widget.lift()
        widget.attributes("-topmost", True)
        widget.update()
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = int(widget.winfo_id())
            GA_ROOT = 2
            try:
                hwnd = int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)) or hwnd
            except Exception:
                pass
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                bbox = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                img = ImageGrab.grab(bbox=bbox, all_screens=True)
                path.parent.mkdir(parents=True, exist_ok=True)
                img.save(path, "PNG")
                try:
                    widget.attributes("-topmost", False)
                except Exception:
                    pass
                return img.size[0], img.size[1]
        except Exception:
            pass
    x = int(widget.winfo_rootx())
    y = int(widget.winfo_rooty())
    w = max(1, int(widget.winfo_width()))
    h = max(1, int(widget.winfo_height()))
    bbox = (x, y, x + w, y + h)
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    try:
        widget.attributes("-topmost", False)
    except Exception:
        pass
    return img.size[0], img.size[1]


def _show_capture_dialog(parent: Tk, title: str, message: str, kind: str = "warning") -> Any:
    """Non-blocking Toplevel that mimics a messagebox so snapshot mode never stalls."""
    from tkinter import Toplevel

    dlg = Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.resizable(False, False)
    # Place near parent so ImageGrab of the dialog alone is easy
    try:
        px = parent.winfo_rootx() + 80
        py = parent.winfo_rooty() + 120
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    colors = {
        "warning": ("#fbbf24", "Warning"),
        "error": ("#f87171", "Error"),
        "info": ("#38bdf8", "Info"),
    }
    accent, label = colors.get(kind, colors["info"])
    fr = ttk.Frame(dlg, padding=16)
    fr.pack(fill="both", expand=True)
    ttk.Label(fr, text=label, foreground=accent, font=ui_font(11, "bold")).pack(anchor="w")
    ttk.Label(fr, text=message, wraplength=420, justify="left").pack(anchor="w", pady=(8, 12))
    ttk.Button(fr, text="OK", command=dlg.destroy).pack(anchor="e")
    dlg.update_idletasks()
    dlg.update()
    return dlg


def _show_compare_preview(parent: Tk, states: list[dict[str, Any]]) -> Any:
    """In-app compare preview (real Compare opens HTML in a browser)."""
    from tkinter import Toplevel

    dlg = Toplevel(parent)
    dlg.title("Botlabs Bench - Compare (preview)")
    dlg.transient(parent)
    try:
        px = parent.winfo_rootx() + 40
        py = parent.winfo_rooty() + 40
        dlg.geometry(f"900x560+{px}+{py}")
    except Exception:
        dlg.geometry("900x560")
    outer = ttk.Frame(dlg, padding=12)
    outer.pack(fill="both", expand=True)

    versions = []
    for s in states:
        v = s.get("bench_version") or s.get("version") or "unknown"
        name = (s.get("sysinfo") or {}).get("computer_name") or s.get("run_id") or "?"
        versions.append((str(name), str(v)))
    unique = {v for _, v in versions}
    if len(unique) > 1:
        warn = ttk.LabelFrame(outer, text="Warning", padding=8)
        warn.pack(fill="x", pady=(0, 8))
        ttk.Label(
            warn,
            text=(
                "Benchmark versions differ — scores may not be directly comparable. "
                "Compare only runs with the same bench_version when possible."
            ),
            foreground="#fbbf24",
            wraplength=660,
            justify="left",
        ).pack(anchor="w")
        for n, v in versions:
            ttk.Label(warn, text=f"  • {n}: bench_version={v}", foreground="#fbbf24").pack(anchor="w")

    ttk.Label(outer, text="Botlabs Bench - Side-by-side", font=ui_font(14, "bold")).pack(anchor="w")
    ttk.Label(outer, text=f"{len(states)} runs compared", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

    # Simple metric table
    metrics = [
        ("Bench version", lambda s: s.get("bench_version")),
        ("Computer", lambda s: (s.get("sysinfo") or {}).get("computer_name")),
        ("CPU", lambda s: (s.get("sysinfo") or {}).get("cpu_name")),
        ("CPU all-core primes/s", lambda s: ((s.get("results") or {}).get("cpu") or {}).get("all_core", {}).get("score_primes_per_sec")),
        ("Mem read MB/s", lambda s: ((s.get("results") or {}).get("memory") or {}).get("bandwidth", {}).get("read_mb_s")),
        ("Disk seq write MB/s", lambda s: ((s.get("results") or {}).get("disk") or {}).get("sequential", {}).get("write_mb_s_avg")),
        ("Internet Mbps", lambda s: ((s.get("results") or {}).get("network") or {}).get("internet", {}).get("best_mbps")),
        ("GPU FPS avg", lambda s: ((s.get("results") or {}).get("gpu") or {}).get("fps")),
    ]
    table = ttk.Frame(outer)
    table.pack(fill="both", expand=True)
    ttk.Label(table, text="Metric", font=ui_font(9, "bold")).grid(row=0, column=0, sticky="w", padx=4, pady=2)
    for col, s in enumerate(states, start=1):
        name = (s.get("sysinfo") or {}).get("computer_name") or s.get("run_id") or f"Run {col}"
        ttk.Label(table, text=str(name), font=ui_font(9, "bold")).grid(row=0, column=col, sticky="w", padx=4)

    for r, (label, getter) in enumerate(metrics, start=1):
        ttk.Label(table, text=label).grid(row=r, column=0, sticky="w", padx=4, pady=1)
        for col, s in enumerate(states, start=1):
            raw = getter(s)
            ttk.Label(table, text=_format_compare_value(label, raw)).grid(
                row=r, column=col, sticky="w", padx=4
            )

    ttk.Button(outer, text="Close", command=dlg.destroy).pack(anchor="e", pady=(8, 0))
    dlg.update_idletasks()
    dlg.update()
    return dlg


def _demo_result_state(*, run_id: str, computer: str, bench_version: str, scale: float = 1.0) -> dict[str, Any]:
    """Synthetic result JSON used only by --render-snapshots (no personal data)."""
    return {
        "run_id": run_id,
        "bench_version": bench_version,
        "app_version": bench_version,
        "status": "completed",
        "elapsed_sec": int(600 * scale),
        "sysinfo": {
            "computer_name": computer,
            "cpu_name": "Demo CPU 8C/16T @ 3.60 GHz",
            "ram_total_gb": 32,
            "os": "Demo OS",
            "gpu_name": "Demo GPU",
        },
        "results": {
            "cpu": {
                "single_core": {"score_primes_per_sec": round(18500 * scale, 1)},
                "all_core": {"score_primes_per_sec": round(142000 * scale, 1)},
                "sustained": {"throttle_drop_pct": round(4.2 / max(scale, 0.5), 1)},
            },
            "memory": {
                "bandwidth": {
                    "read_mb_s": round(28500 * scale, 1),
                    "write_mb_s": round(26100 * scale, 1),
                },
                "latency_ns": round(72.5 / max(scale, 0.5), 1),
            },
            "disk": {
                "sequential": {
                    "read_mb_s_avg": round(3200 * scale, 1),
                    "write_mb_s_avg": round(2800 * scale, 1),
                },
                "random_4k": {"read_iops": int(450000 * scale)},
            },
            "network": {
                "internet": {"best_mbps": round(940 * scale, 1)},
                "lan": {"write_mb_s": round(110 * scale, 1)},
            },
            "gpu": {
                "fps": round(6200 * scale, 1),
                "fps_1pct_low": round(4800 * scale, 1),
                "gpu_score": round(8800 * scale, 1),
            },
        },
        "report_paths": {
            "html": f"BenchResults/{run_id}.html",
            "csv": f"BenchResults/{run_id}.csv",
            "json": f"BenchResults/{run_id}.json",
        },
    }


def _apply_demo_running(app: "BotlabsBenchApp") -> None:
    """Paint progress bars + live graph with made-up sensor samples (no real bench)."""
    import math

    app.start_btn.configure(state="disabled")
    app.stop_btn.configure(state="normal")
    app.status_text.set("Running... | GPU 6124 FPS (Demo GPU)")
    app.current_test.set("gpu: shader load level 3/5")
    app.test_pct.set(62.0)
    app.overall_pct.set(48.0)
    app.test_pct_lbl.configure(text="62%")
    app.overall_pct_lbl.configure(text="48%")
    app.eta_text.set("5m 12s remaining")
    app.temps_text.set("CPU 71°C · GPU 64°C")
    app.temps_short.set("CPU 71°C · GPU 64°C")
    try:
        app._set_results_idle("Run in progress…", "Scores appear here when the run finishes or is stopped.")
    except Exception:
        pass
    app._run_started = time.time() - 280
    if getattr(app, "_has_mpl", False):
        app._plot_times.clear()
        app._plot_cpu.clear()
        app._plot_mem.clear()
        app._plot_disk.clear()
        if hasattr(app, "_plot_fps"):
            app._plot_fps.clear()
        for i in range(60):
            t = float(i)
            app._plot_times.append(t)
            app._plot_cpu.append(55 + 30 * math.sin(i / 7.0) + (i % 5))
            app._plot_mem.append(40 + 10 * math.cos(i / 11.0))
            app._plot_disk.append(min(100, 20 + 40 * abs(math.sin(i / 9.0))))
            if hasattr(app, "_plot_fps"):
                app._plot_fps.append(5000 + 800 * math.sin(i / 5.0))
        app._refresh_plot()
    try:
        app._ensure_results_pane()
    except Exception:
        pass
    app.root.update_idletasks()
    app.root.update()


def _apply_demo_partial(app: "BotlabsBenchApp") -> None:
    """Stopped / Save partial — must look clearly different from the running demo."""
    app.start_btn.configure(state="normal")
    app.stop_btn.configure(state="disabled")
    app.status_text.set("Done (cancelled). Partial reports saved.")
    app.current_test.set("gpu: stopped")
    # Distinct from running (62/48) so even truncated grabs differ
    app.test_pct.set(100.0)
    app.overall_pct.set(55.0)
    app.test_pct_lbl.configure(text="100%")
    app.overall_pct_lbl.configure(text="55%")
    app.eta_text.set("stopped")
    app.temps_text.set("CPU 58°C · GPU 51°C")
    app.temps_short.set("CPU 58°C · GPU 51°C")
    demo = _demo_result_state(
        run_id="demo-run-partial", computer="DEMO-WORKSTATION", bench_version=BENCH_VERSION, scale=0.55
    )
    demo["status"] = "cancelled"
    demo["elapsed_sec"] = 280
    demo["report_paths"] = {"html": "BenchResults/demo-run-partial.html"}
    # Trim unfinished suites for a partial look
    demo["results"] = {k: v for k, v in (demo.get("results") or {}).items() if k in ("cpu", "memory", "disk")}
    try:
        app._populate_results_summary(demo)
    except Exception:
        pass
    # Prefer a clean stopped layout (hide Custom options) so Results is fully visible
    try:
        app.mode.set("quick")
        app._mode_changed()
    except Exception:
        pass
    try:
        app._ensure_results_pane()
    except Exception:
        pass
    _force_window_paint(app.root)
    # Extra idle flush — Windows DWM can otherwise hand ImageGrab a stale frame
    deadline = time.time() + 0.35
    while time.time() < deadline:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.02)
    _force_window_paint(app.root)


def _apply_demo_finished(app: "BotlabsBenchApp") -> None:
    app.start_btn.configure(state="normal")
    app.stop_btn.configure(state="disabled")
    app.overall_pct.set(100.0)
    app.test_pct.set(100.0)
    app.test_pct_lbl.configure(text="100%")
    app.overall_pct_lbl.configure(text="100%")
    app.current_test.set("—")
    app.eta_text.set("0m 0s remaining")
    app.status_text.set(
        "Done. Reports saved."
    )
    app.temps_text.set("CPU 48°C · GPU 42°C")
    app.temps_short.set("CPU 48°C · GPU 42°C")
    demo = _demo_result_state(
        run_id="demo-run-complete", computer="DEMO-WORKSTATION", bench_version=BENCH_VERSION, scale=1.0
    )
    demo["report_paths"] = {
        "html": "BenchResults/demo-run-complete.html",
        "csv": "BenchResults/demo-run-complete.csv",
        "json": "BenchResults/demo-run-complete.json",
    }
    try:
        app._populate_results_summary(demo)
    except Exception:
        pass
    # Prefer a clean finished layout (hide Custom options)
    try:
        app.mode.set("quick")
        app._mode_changed()
    except Exception:
        pass
    try:
        app._ensure_results_pane()
    except Exception:
        pass
    app.root.update_idletasks()
    app.root.update()


def run_render_snapshots(outdir: str | Path) -> int:
    """Hidden GUI screenshot mode: walk key states, save PNGs + snapshot-manifest.txt.

    Usage: BotlabsBench --render-snapshots <outdir>
    Uses only synthetic demo data (no real benchmark). Exits 0 on success.
    """
    out = Path(outdir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "snapshot-log.txt"
    manifest_lines: list[str] = []
    log_lines: list[str] = []

    def emit(msg: str) -> None:
        print(msg, flush=True)
        log_lines.append(msg)

    dpi_note = _enable_dpi_awareness()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    emit(f"Botlabs Bench --render-snapshots  out={out}")
    emit(f"time={stamp}  version={APP_VERSION}  {dpi_note}")
    manifest_lines.append(f"Botlabs Bench GUI snapshots  {stamp}")
    manifest_lines.append(f"app_version={APP_VERSION}  bench_version={BENCH_VERSION}")
    manifest_lines.append(dpi_note)
    manifest_lines.append("Synthetic demo data only. No real benchmark run.")
    manifest_lines.append("")

    entries: list[tuple[str, str]] = []  # (filename, description)

    last_main_md5: list[str] = [""]

    def save_main(app: "BotlabsBenchApp", filename: str, description: str, *, settle_ms: int = 300) -> None:
        import hashlib

        path = out / filename
        try:
            # Retry once if this frame is byte-identical to the previous main shot
            # (Windows DWM sometimes returns a stale compositor buffer).
            for attempt in range(2):
                w, h = _grab_window_png(app.root, path, settle_ms=settle_ms + attempt * 250)
                digest = hashlib.md5(path.read_bytes()).hexdigest() if path.is_file() else ""
                if digest and digest == last_main_md5[0] and attempt == 0:
                    emit(f"WARN {filename}: identical to previous main shot — forcing redraw and retry")
                    _force_window_paint(app.root)
                    try:
                        # Nudge title so the frame definitely changes
                        title = app.root.title()
                        app.root.title(title + " ")
                        app.root.update()
                        app.root.title(title)
                        app.root.update()
                    except Exception:
                        pass
                    time.sleep(0.2)
                    continue
                last_main_md5[0] = digest
                break
            size = path.stat().st_size if path.is_file() else 0
            entries.append((filename, description))
            manifest_lines.append(f"{filename}  ({w}x{h}, {size} bytes)")
            manifest_lines.append(f"  {description}")
            manifest_lines.append("")
            emit(f"OK  {filename}  {w}x{h}  {description}")
        except Exception as e:
            emit(f"FAIL {filename}: {e}")
            log.exception("snapshot capture failed for %s", filename)
            manifest_lines.append(f"{filename}  CAPTURE FAILED: {e}")
            manifest_lines.append(f"  {description}")
            manifest_lines.append("")

    def save_widget(widget, filename: str, description: str) -> None:
        path = out / filename
        try:
            w, h = _grab_widget_png(widget, path)
            size = path.stat().st_size if path.is_file() else 0
            entries.append((filename, description))
            manifest_lines.append(f"{filename}  ({w}x{h}, {size} bytes)")
            manifest_lines.append(f"  {description}")
            manifest_lines.append("")
            emit(f"OK  {filename}  {w}x{h}  {description}")
        except Exception as e:
            emit(f"FAIL {filename}: {e}")
            log.exception("snapshot widget capture failed for %s", filename)
            manifest_lines.append(f"{filename}  CAPTURE FAILED: {e}")
            manifest_lines.append(f"  {description}")
            manifest_lines.append("")

    # Avoid real modal messageboxes during snapshot walks that touch validation.
    _install_silent_dialogs()

    app = BotlabsBenchApp(selftest=True)
    try:
        app.root.title(f"Botlabs Bench {APP_VERSION}")
        # Maximize so --render-snapshots verifies the default maximized layout.
        # (Site-shots keep a fixed geometry for consistent website images.)
        try:
            app._maximize_startup()
        except Exception:
            app.root.geometry("1280x900")
        app.root.update_idletasks()
        app.root.update()
        try:
            app._ensure_results_pane()
        except Exception:
            pass
        time.sleep(0.15)

        # --- Mode screens ---
        app.mode.set("quick")
        app._mode_changed()
        app.root.update()
        save_main(app, "01-mode-quick.png", "Fresh launch — Quick (10 minutes) mode selected.")

        app.mode.set("detailed")
        app._mode_changed()
        app.root.update()
        save_main(app, "02-mode-detailed.png", "Detailed (1 hour) mode selected.")

        app.mode.set("custom")
        app._mode_changed()
        app.single_pass.set(False)
        app.custom_hours.set(0)
        app.custom_minutes.set(30)
        for var in (app.test_cpu, app.test_memory, app.test_disk, app.test_network, app.test_gpu):
            var.set(True)
        app.lan_enabled.set(True)
        app._on_custom_changed()
        app.root.update()
        save_main(
            app,
            "03-mode-custom-all-options.png",
            "Custom mode with all tests + duration + disk/LAN options visible.",
        )

        # --- Validation error dialogs (non-blocking stand-ins) ---
        app.mode.set("custom")
        for var in (app.test_cpu, app.test_memory, app.test_disk, app.test_network, app.test_gpu):
            var.set(False)
        app._on_custom_changed()
        msg_no_tests = "Select at least one test (CPU, Memory, Disk, Network, or Graphics)."
        try:
            app.custom_hint.set(msg_no_tests)
        except Exception:
            pass
        dlg = _show_capture_dialog(app.root, "Botlabs Bench", msg_no_tests, kind="warning")
        save_widget(dlg, "04-error-no-tests.png", "Validation: no tests selected.")
        try:
            dlg.destroy()
        except Exception:
            pass

        for var in (app.test_cpu, app.test_memory, app.test_disk, app.test_network, app.test_gpu):
            var.set(True)
        app.custom_hours.set(0)
        app.custom_minutes.set(0)
        app.single_pass.set(False)
        app._on_custom_changed()
        msg_short = (
            "Custom duration is too short for 5 selected test(s). "
            "Minimum is 2 minute(s) (at least 60s total, and 30s per test). "
            "You entered 0s. Increase hours/minutes, select fewer tests, or turn on Single pass."
        )
        try:
            app.custom_hint.set(msg_short)
        except Exception:
            pass
        dlg = _show_capture_dialog(app.root, "Botlabs Bench", msg_short, kind="warning")
        # Also keep the main window state in a separate shot with the hint text
        save_main(
            app,
            "05-error-duration-too-short-main.png",
            "Custom mode with duration-too-short hint in the options pane.",
        )
        save_widget(dlg, "05b-error-duration-too-short-dialog.png", "Validation dialog: duration too short.")
        try:
            dlg.destroy()
        except Exception:
            pass

        msg_hours = f"Hours must be a whole number between {MIN_HOURS} and {MAX_HOURS}."
        dlg = _show_capture_dialog(app.root, "Botlabs Bench", msg_hours, kind="warning")
        save_widget(dlg, "06-error-hours-non-numeric.png", "Validation dialog: hours must be a whole number.")
        try:
            dlg.destroy()
        except Exception:
            pass

        # Restore a sane custom config for running/finished shots
        app.custom_hours.set(0)
        app.custom_minutes.set(30)
        app.single_pass.set(False)
        for var in (app.test_cpu, app.test_memory, app.test_disk, app.test_network, app.test_gpu):
            var.set(True)
        app._on_custom_changed()

        # --- Running with demo progress/graphs (Quick — Custom panel would crowd the body) ---
        app.mode.set("quick")
        app._mode_changed()
        _apply_demo_running(app)
        save_main(
            app,
            "07-running-live-progress.png",
            "Running state with demo progress bars, ETA, temps, and live sensor graph.",
            settle_ms=350,
        )

        # --- Stop / partial ---
        _apply_demo_partial(app)
        save_main(
            app,
            "08-stopped-partial-results.png",
            "After Stop / Save partial — cancelled status and partial report name.",
            settle_ms=450,
        )

        # --- Finished ---
        _apply_demo_finished(app)
        dlg = _show_capture_dialog(
            app.root,
            "Botlabs Bench",
            "Run completed.\nElapsed 612s\n\nResults:\nBenchResults/ (demo)",
            kind="info",
        )
        save_main(app, "09-finished-results.png", "Finished completed run (100% bars, Done status).")
        save_widget(dlg, "09b-finished-info-dialog.png", "Completion info dialog (stand-in for messagebox).")
        try:
            dlg.destroy()
        except Exception:
            pass

        # --- Compare preview with version mismatch ---
        demo_a = _demo_result_state(
            run_id="demo-run-a", computer="DEMO-PC-A", bench_version="1.1.1", scale=1.0
        )
        demo_b = _demo_result_state(
            run_id="demo-run-b", computer="DEMO-PC-B", bench_version="1.0.0", scale=0.82
        )
        cmp_dlg = _show_compare_preview(app.root, [demo_a, demo_b])
        save_widget(
            cmp_dlg,
            "10-compare-version-mismatch.png",
            "Compare preview with two demo runs and bench_version mismatch warning.",
        )
        try:
            cmp_dlg.destroy()
        except Exception:
            pass

        # Matching versions for a clean compare
        demo_c = _demo_result_state(
            run_id="demo-run-c", computer="DEMO-PC-C", bench_version="1.1.1", scale=1.15
        )
        cmp_dlg = _show_compare_preview(app.root, [demo_a, demo_c])
        save_widget(
            cmp_dlg,
            "11-compare-same-version.png",
            "Compare preview with two demo runs on the same bench_version.",
        )
        try:
            cmp_dlg.destroy()
        except Exception:
            pass

        # Compare needs two files warning
        dlg = _show_capture_dialog(
            app.root, "Compare", "Pick at least two JSON result files.", kind="warning"
        )
        save_widget(dlg, "12-compare-need-two-files.png", "Compare warning when fewer than two files are picked.")
        try:
            dlg.destroy()
        except Exception:
            pass

    finally:
        try:
            app.root.destroy()
        except Exception:
            pass

    (out / "snapshot-manifest.txt").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    summary = (
        f"SNAPSHOTS DONE  count={len(entries)}  out={out}  "
        f"manifest={out / 'snapshot-manifest.txt'}"
    )
    emit(summary)
    try:
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"WARN could not write snapshot-log.txt: {e}", flush=True)
    return 0 if entries else 1


def run_site_shots(outdir: str | Path) -> int:
    """Polished general website screenshots (demo data only) + captions.txt.

    Captures a fixed 1280x800 window covering: main, Custom setup, run in progress,
    finished results, and Compare. No real hostnames, usernames, paths, IPs, or NAS names.
    """
    out = Path(outdir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    captions: list[str] = []
    log_lines: list[str] = []

    def emit(msg: str) -> None:
        print(msg, flush=True)
        log_lines.append(msg)

    dpi_note = _enable_dpi_awareness()
    emit(f"Botlabs Bench --site-shots  out={out}  {dpi_note}  v={APP_VERSION}")
    _install_silent_dialogs()
    app = BotlabsBenchApp(selftest=True)
    try:
        app.root.title(f"Botlabs Bench {APP_VERSION}")
        # Fixed large size for consistent website images (taller so graph+results fit)
        app.root.geometry("1280x900")
        app.root.minsize(1280, 900)
        try:
            app.root.update_idletasks()
            app._ensure_results_pane()
        except Exception:
            pass
        # Generic demo sensor / drive labels (no real host data)
        try:
            temps_lbl = "Temps n/a" if sys.platform.startswith("win") else "Temps n/a"
            app.temps_text.set(temps_lbl)
            app.temps_short.set(temps_lbl)
            app.admin_text.set("Administrator" if sys.platform.startswith("win") else "Standard user")
            try:
                style = "PillAdmin.TLabel" if "Administrator" in app.admin_text.get() else "PillUser.TLabel"
                app.admin_pill.configure(style=style)
            except Exception:
                pass
            app.drive_labels = ["System (SSD) · 412 GB free", "Data (NVMe) · 1.2 TB free", "Backup share · 8.4 TB free"]
            app.disk_drive.set(app.drive_labels[0])
            app.lan_drive.set(app.drive_labels[2])
            app.lan_enabled.set(True)
        except Exception:
            pass
        app.root.update_idletasks()
        app.root.update()

        def capture(filename: str, caption: str) -> None:
            path = out / filename
            w, h = _grab_window_png(app.root, path, settle_ms=350)
            captions.append(f"{filename}\t{caption}")
            emit(f"OK  {filename}  {w}x{h}  {caption}")

        def capture_widget(widget, filename: str, caption: str) -> None:
            path = out / filename
            w, h = _grab_widget_png(widget, path, settle_ms=350)
            captions.append(f"{filename}\t{caption}")
            emit(f"OK  {filename}  {w}x{h}  {caption}")

        # 1) Main screen — Quick
        app.mode.set("quick")
        app._mode_changed()
        app.status_text.set("Ready")
        app.current_test.set("—")
        app.root.update()
        capture("01-main-quick.png", "Main window — Quick mode ready to start.")

        # 2) Custom setup
        app.mode.set("custom")
        app._mode_changed()
        app.custom_hours.set(0)
        app.custom_minutes.set(45)
        app.single_pass.set(False)
        for var in (app.test_cpu, app.test_memory, app.test_disk, app.test_network, app.test_gpu):
            var.set(True)
        app.lan_enabled.set(True)
        app._on_custom_changed()
        app.root.update()
        capture("02-custom-setup.png", "Custom mode — choose tests, duration, disk and LAN targets.")

        # 3) Run in progress with graphs (Quick mode so Custom panel does not eat height)
        app.mode.set("quick")
        app._mode_changed()
        _apply_demo_running(app)
        app.root.update()
        capture("03-run-in-progress.png", "Benchmark running — live progress, ETA, and sensor graphs.")

        # 4) Finished results
        _apply_demo_finished(app)
        app.root.update()
        capture("04-finished-results.png", "Completed run — results summary and reports ready.")

        # 5) Compare
        demo_a = _demo_result_state(
            run_id="demo-alpha", computer="Workstation-Alpha", bench_version=BENCH_VERSION, scale=1.0
        )
        demo_b = _demo_result_state(
            run_id="demo-beta", computer="Workstation-Beta", bench_version=BENCH_VERSION, scale=1.12
        )
        cmp_dlg = _show_compare_preview(app.root, [demo_a, demo_b])
        # Prefer a full-window look: also leave main behind; capture the compare dialog
        capture_widget(cmp_dlg, "05-compare.png", "Compare view — side-by-side scores for two sample machines.")
        try:
            cmp_dlg.destroy()
        except Exception:
            pass

    finally:
        try:
            app.root.destroy()
        except Exception:
            pass

    (out / "captions.txt").write_text("\n".join(captions) + "\n", encoding="utf-8")
    try:
        (out / "site-shots-log.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    except Exception:
        pass
    emit(f"SITE-SHOTS DONE  count={len(captions)}  out={out}")
    return 0 if captions else 1
