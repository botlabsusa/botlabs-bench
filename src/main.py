"""Botlabs Bench entry point - GUI or headless CLI."""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
import traceback
from pathlib import Path


def _writable_base() -> Path:
    """Prefer beside AppImage / exe; fall back to ~/BotlabsBench when read-only."""
    import os
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


def _setup_logging() -> Path:
    base = _writable_base()
    log_path = base / "BotlabsBench.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Matplotlib scans system fonts and logs INFO for non-scalable emoji/bitmap fonts
    # (e.g. NotoColorEmoji.ttf). Harmless — keep the console clean.
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    return log_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Botlabs Bench - portable cross-platform benchmark",
        epilog="Support: support@botlabsusa.com",
    )
    p.add_argument("--headless", action="store_true", help="Run without GUI")
    p.add_argument("--mode", choices=["quick", "detailed", "custom"], default="custom")
    p.add_argument(
        "--tests",
        default="cpu,memory,disk,network,gpu",
        help="Comma-separated: cpu,memory,disk,network,gpu",
    )
    p.add_argument("--single-pass", action="store_true", help="One pass of each test (quick check)")
    p.add_argument("--duration-sec", type=int, default=0, help="Custom duration in seconds")
    p.add_argument("--minutes", type=float, default=0, help="Custom duration in minutes (alt)")
    p.add_argument("--hours", type=int, default=0, help="Custom duration hours (with --minutes)")
    p.add_argument("--disk-drive", default="", help="Drive/mount root for disk test (e.g. C:\\ or /home)")
    p.add_argument("--lan-drive", default="", help="Mapped drive / NAS mount for LAN test")
    p.add_argument("--results-dir", default="", help="Override results directory")
    # Hidden automation hooks (suppressed from --help)
    p.add_argument("--gui-selftest", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--selftest-stop-after", type=float, default=45.0, help=argparse.SUPPRESS)
    p.add_argument(
        "--selftest-tests",
        default="cpu,memory,disk,network,gpu",
        help=argparse.SUPPRESS,
    )
    p.add_argument("--selftest-timeout", type=float, default=360.0, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def run_headless(args: argparse.Namespace) -> int:
    from runner import BenchmarkRunner
    from validate import default_disk_root, validate_run

    log = logging.getLogger("botlabsbench")
    base = _writable_base()
    results = Path(args.results_dir) if args.results_dir else base / "BenchResults"
    results.mkdir(parents=True, exist_ok=True)

    tests = [t.strip().lower() for t in args.tests.split(",") if t.strip()]
    duration_sec = args.duration_sec
    hours = args.hours
    minutes = args.minutes
    if minutes and not duration_sec and not hours:
        # treat --minutes as total minutes when hours not set
        duration_sec = int(float(minutes) * 60)
        hours = 0
        minutes = 0

    single_flag = bool(args.single_pass)
    # Preserve old convenience: custom headless with no duration defaults to single-pass
    if args.mode == "custom" and not single_flag and not duration_sec and not hours and not minutes:
        single_flag = True

    vr = validate_run(
        mode=args.mode,
        tests=tests,
        single_pass=single_flag,
        hours=int(hours or 0),
        minutes=int(minutes or 0) if not duration_sec else 0,
        duration_sec=int(duration_sec) if duration_sec else None,
        disk_drive=args.disk_drive or default_disk_root(),
        lan_drive=args.lan_drive or None,
        lan_enabled=bool(args.lan_drive),
    )
    if not vr.ok:
        print(f"ERROR: {vr.message}", file=sys.stderr)
        log.error("Validation failed: %s", vr.message)
        return 1
    if vr.message:
        log.info("Validation note: %s", vr.message)

    def on_progress(payload: dict) -> None:
        log.info(
            "progress test=%s label=%s test=%.0f%% overall=%.0f%% detail=%s",
            payload.get("test"),
            payload.get("label"),
            (payload.get("test_progress") or 0) * 100,
            (payload.get("overall_progress") or 0) * 100,
            payload.get("detail"),
        )

    runner = BenchmarkRunner(
        results_dir=results,
        mode=args.mode,
        tests=list(vr.tests),
        duration_sec=vr.duration_sec,
        single_pass=vr.single_pass,
        disk_drive=vr.disk_drive,
        lan_drive=vr.lan_drive,
        on_progress=on_progress,
    )
    log.info(
        "Starting headless run id=%s tests=%s single_pass=%s duration=%s",
        runner.run_id,
        vr.tests,
        vr.single_pass,
        vr.duration_sec,
    )
    state = runner.run()
    log.info(
        "Finished status=%s elapsed=%ss reports=%s",
        state.get("status"),
        state.get("elapsed_sec"),
        state.get("report_paths"),
    )
    print("RUN_ID", state.get("run_id"))
    print("STATUS", state.get("status"))
    print("RESULTS_DIR", results)
    for k, v in (state.get("report_paths") or {}).items():
        print(f"REPORT_{k.upper()}", v)
    return 0 if state.get("status") in ("completed", "cancelled") else 1


def run_gui() -> int:
    from app import BotlabsBenchApp

    app = BotlabsBenchApp()
    app.run()
    return 0


def run_gui_selftest(args: argparse.Namespace) -> int:
    from app import run_gui_selftest as _run

    tests = [t.strip().lower() for t in (args.selftest_tests or "").split(",") if t.strip()]
    return _run(
        stop_after_sec=float(args.selftest_stop_after or 45.0),
        tests=tests or None,
        safety_timeout_sec=float(args.selftest_timeout or 360.0),
    )


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    log_path = _setup_logging()
    log = logging.getLogger("botlabsbench")
    log.info("Botlabs Bench starting log=%s frozen=%s", log_path, getattr(sys, "frozen", False))
    args = _parse_args(argv)
    try:
        if args.gui_selftest:
            return run_gui_selftest(args)
        if args.headless:
            return run_headless(args)
        return run_gui()
    except Exception:
        log.error("Fatal error:\n%s", traceback.format_exc())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
