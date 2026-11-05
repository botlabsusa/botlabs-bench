"""Botlabs Bench visual theme (sv-ttk dark + accent colors).

MIT-compatible: sv-ttk is MIT. Falls back to a hand-built ttk dark style if
sv-ttk is unavailable so headless CI / older trees still import.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

# Palette — dark slate + cyan accent (matches report HTML vibe)
BG = "#0f172a"
BG_PANEL = "#1e293b"
BG_INPUT = "#334155"
FG = "#e2e8f0"
FG_MUTED = "#94a3b8"
FG_DIM = "#64748b"
ACCENT = "#38bdf8"
ACCENT_HOVER = "#7dd3fc"
SUCCESS = "#4ade80"
WARN = "#fbbf24"
DANGER = "#f87171"
BORDER = "#334155"
PLOT_BG = "#0b1220"
PLOT_GRID = "#1e293b"
PLOT_CPU = "#38bdf8"
PLOT_MEM = "#a78bfa"
PLOT_DISK = "#fbbf24"
PLOT_FPS = "#4ade80"


def ui_font(size: int = 10, weight: str = "normal") -> tuple:
    """Segoe UI on Windows; DejaVu Sans / TkDefaultFont-friendly sans elsewhere."""
    if sys.platform.startswith("win"):
        family = "Segoe UI"
    else:
        # AppImage strips fontconfig; Tk falls back to built-in families reliably.
        family = "DejaVu Sans"
    if weight == "bold":
        return (family, size, "bold")
    return (family, size)


def asset_path(*parts: str) -> Path:
    """Resolve assets/ next to the frozen exe, AppImage, or source tree."""
    import os

    candidates: list[Path] = []
    env = os.environ.get("APPIMAGE")
    if env:
        candidates.append(Path(env).resolve().parent / "assets")
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "assets")
        candidates.append(Path(sys.executable).resolve().parent / "assets")
    here = Path(__file__).resolve().parent
    candidates.append(here.parent / "assets")
    candidates.append(here / "assets")
    for base in candidates:
        p = base.joinpath(*parts)
        if p.is_file():
            return p
    return candidates[0].joinpath(*parts)


def apply_theme(root) -> str:
    """Apply dark theme to a Tk root. Returns which theme backend was used."""
    try:
        import sv_ttk

        sv_ttk.set_theme("dark")
        _tune_ttk_styles(root)
        try:
            root.configure(bg=BG)
        except Exception:
            pass
        return "sv-ttk-dark"
    except Exception:
        _apply_fallback_dark(root)
        return "fallback-dark"


def _tune_ttk_styles(root) -> None:
    from tkinter import ttk

    style = ttk.Style(root)
    # Accent-ish progress bars
    try:
        # Fixed pt sizes — chrome must stay readable on huge maximized / high-DPI windows
        # (the graph can grow; Progress / Labelframe labels should not look tiny).
        style.configure("Accent.Horizontal.TProgressbar", thickness=16)
        style.configure("TLabelframe.Label", font=ui_font(11, "bold"))
        style.configure("Title.TLabel", font=ui_font(18, "bold"), foreground=FG)
        style.configure("Subtitle.TLabel", font=ui_font(10), foreground=FG_MUTED)
        style.configure("Muted.TLabel", font=ui_font(10), foreground=FG_MUTED)
        style.configure("Progress.TLabel", font=ui_font(11), foreground=FG)
        style.configure("ProgressMuted.TLabel", font=ui_font(10), foreground=FG_MUTED)
        style.configure("Warn.TLabel", font=ui_font(9), foreground=WARN)
        style.configure("Success.TLabel", font=ui_font(9), foreground=SUCCESS)
        style.configure("Metric.TLabel", font=ui_font(9), foreground=FG_MUTED)
        style.configure("MetricValue.TLabel", font=ui_font(9), foreground=FG)
        style.configure("StatusLine.TLabel", font=ui_font(10, "bold"), foreground=FG)
        style.configure("Header.TFrame", background=BG)
        style.configure("Card.TLabelframe", padding=12)
        style.configure("TButton", font=ui_font(10), padding=(14, 6))
        style.map(
            "TButton",
            foreground=[("disabled", FG_DIM)],
            background=[("disabled", BG_PANEL)],
        )
        style.configure("Accent.TButton", font=ui_font(10, "bold"), padding=(16, 7))
        style.map(
            "Accent.TButton",
            foreground=[("disabled", FG_DIM)],
            background=[("disabled", BG_INPUT)],
        )
        style.configure("Link.TButton", font=ui_font(9), padding=(10, 4), foreground=ACCENT)
        style.configure("Pill.TLabel", font=ui_font(9), foreground=FG, background=BG_INPUT, padding=(10, 4))
        style.configure("PillAdmin.TLabel", font=ui_font(9, "bold"), foreground="#0f172a", background=SUCCESS, padding=(10, 4))
        style.configure("PillUser.TLabel", font=ui_font(9), foreground=FG, background=BG_INPUT, padding=(10, 4))
        style.configure("PillTemps.TLabel", font=ui_font(9), foreground=FG_MUTED, background=BG_INPUT, padding=(10, 4))
        style.configure("TRadiobutton", font=ui_font(10))
        style.configure("TCheckbutton", font=ui_font(10))
        style.configure("TCombobox", font=ui_font(10), padding=4)
        style.configure("TSpinbox", font=ui_font(10), padding=4)
        style.configure("Treeview", font=ui_font(9), rowheight=26)
        style.configure("Treeview.Heading", font=ui_font(9, "bold"))
    except Exception:
        pass


def _apply_fallback_dark(root) -> None:
    from tkinter import ttk

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=FG, fieldbackground=BG_INPUT, bordercolor=BORDER)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG, font=ui_font(10))
    style.configure("Title.TLabel", background=BG, foreground=FG, font=ui_font(18, "bold"))
    style.configure("Subtitle.TLabel", background=BG, foreground=FG_MUTED, font=ui_font(10))
    style.configure("Muted.TLabel", background=BG, foreground=FG_MUTED, font=ui_font(10))
    style.configure("Progress.TLabel", background=BG, foreground=FG, font=ui_font(11))
    style.configure("ProgressMuted.TLabel", background=BG, foreground=FG_MUTED, font=ui_font(10))
    style.configure("Warn.TLabel", background=BG, foreground=WARN, font=ui_font(9))
    style.configure("Success.TLabel", background=BG, foreground=SUCCESS, font=ui_font(9))
    style.configure("Metric.TLabel", background=BG, foreground=FG_MUTED, font=ui_font(9))
    style.configure("MetricValue.TLabel", background=BG, foreground=FG, font=ui_font(9))
    style.configure("StatusLine.TLabel", background=BG, foreground=FG, font=ui_font(10, "bold"))
    style.configure("TLabelframe", background=BG, foreground=FG, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG, foreground=FG, font=ui_font(11, "bold"))
    style.configure("TButton", background=BG_INPUT, foreground=FG, padding=(14, 6), font=ui_font(10))
    style.map("TButton", foreground=[("disabled", FG_DIM)], background=[("disabled", BG_PANEL), ("active", BG_PANEL)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#0f172a", padding=(16, 7), font=ui_font(10, "bold"))
    style.map("Accent.TButton", foreground=[("disabled", FG_DIM)], background=[("active", ACCENT_HOVER), ("disabled", BG_INPUT)])
    style.configure("Link.TButton", background=BG_INPUT, foreground=ACCENT, padding=(10, 4), font=ui_font(9))
    style.configure("Pill.TLabel", background=BG_INPUT, foreground=FG, font=ui_font(9), padding=(10, 4))
    style.configure("PillAdmin.TLabel", background=SUCCESS, foreground="#0f172a", font=ui_font(9, "bold"), padding=(10, 4))
    style.configure("PillUser.TLabel", background=BG_INPUT, foreground=FG, font=ui_font(9), padding=(10, 4))
    style.configure("PillTemps.TLabel", background=BG_INPUT, foreground=FG_MUTED, font=ui_font(9), padding=(10, 4))
    style.configure("TRadiobutton", background=BG, foreground=FG, font=ui_font(10))
    style.configure("TCheckbutton", background=BG, foreground=FG, font=ui_font(10))
    style.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_INPUT, foreground=FG, font=ui_font(10))
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BG_INPUT, thickness=16)
    style.configure("Accent.Horizontal.TProgressbar", background=ACCENT, troughcolor=BG_INPUT, thickness=16)
    style.configure("TSpinbox", fieldbackground=BG_INPUT, foreground=FG, background=BG_INPUT, font=ui_font(10))
    style.configure("Treeview", background=BG_PANEL, foreground=FG, fieldbackground=BG_PANEL, rowheight=26)
    style.configure("Treeview.Heading", background=BG_INPUT, foreground=FG, font=ui_font(9, "bold"))
    style.configure("Header.TFrame", background=BG)


def style_matplotlib(fig, ax) -> None:
    """Match the live graph to the dark theme; dual Y for % vs FPS."""
    fig.patch.set_facecolor(PLOT_BG)
    ax.set_facecolor(PLOT_BG)
    ax.tick_params(colors=FG_MUTED, labelsize=8)
    ax.xaxis.label.set_color(FG_MUTED)
    ax.yaxis.label.set_color(FG_MUTED)
    ax.title.set_color(FG)
    for spine in ax.spines.values():
        spine.set_color(BORDER)
    ax.grid(True, color=PLOT_GRID, linestyle="--", linewidth=0.6, alpha=0.8)


def set_window_icon(root) -> Optional[Path]:
    """Load window / taskbar icon from assets. Returns path used or None."""
    ico = asset_path("botlabsbench.ico")
    png = asset_path("botlabsbench-128.png")
    if not png.is_file():
        png = asset_path("botlabsbench.png")
    try:
        if sys.platform.startswith("win") and ico.is_file():
            root.iconbitmap(default=str(ico))
            return ico
    except Exception:
        pass
    try:
        from tkinter import PhotoImage

        if png.is_file():
            img = PhotoImage(file=str(png))
            root.iconphoto(True, img)
            # Keep a reference so Tk doesn't GC it
            root._botlabs_icon_ref = img  # type: ignore[attr-defined]
            return png
    except Exception:
        pass
    return None


def admin_status_text() -> str:
    """Short badge label for the header pill."""
    try:
        from sensors import is_admin

        if is_admin():
            return "Administrator"
        return "Standard user"
    except Exception:
        return "Standard user"


def temps_status_short() -> str:
    """Compact temps string for the header pill (no long caveats)."""
    try:
        from sensors import read_cpu_temps_c, read_extra_gpu_sensors

        bits = []
        temps = read_cpu_temps_c()
        if temps:
            bits.append(f"CPU {max(temps):.0f}°C")
        gpu = read_extra_gpu_sensors()
        if gpu.get("temp_c") is not None:
            bits.append(f"GPU {float(gpu['temp_c']):.0f}°C")
        if bits:
            return " · ".join(bits)
    except Exception:
        pass
    # Keep the header pill short — long text clips on Windows at restored sizes.
    if sys.platform.startswith("win"):
        return "Temps n/a"
    return "Temps n/a"
