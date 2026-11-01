"""Shared input validation for Botlabs Bench GUI and CLI.

Limits (chosen for reliable results without needless waiting):
- Hours: 0..72 (integer)
- Minutes: 0..59 (integer)
- Custom total duration (when not single-pass): at least max(60s, 30s * N selected tests)
- Max custom duration: 72 hours
- At least one test must be selected
- Known tests: cpu, memory, disk, network, gpu
- Disk drive must exist and be readable; planned test file must fit in free space
  (default planned size 256 MB single-pass, up to 2048 MB timed; leave 512 MB headroom)
- LAN drive optional; if set must exist
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional, Sequence

VALID_TESTS = ("cpu", "memory", "disk", "network", "gpu")

MIN_HOURS = 0
MAX_HOURS = 72
MIN_MINUTES = 0
MAX_MINUTES = 59

MIN_TOTAL_SEC = 60  # absolute floor for timed custom runs
SEC_PER_TEST = 30  # each selected test needs at least this much budget
MAX_TOTAL_SEC = MAX_HOURS * 3600

DISK_MIN_MB = 64
DISK_DEFAULT_SINGLE_MB = 256
DISK_MAX_MB = 2048
DISK_FREE_HEADROOM_MB = 512  # leave this much free after the test file


@dataclass
class ValidationResult:
    ok: bool
    message: str = ""
    duration_sec: int = 0
    single_pass: bool = False
    tests: tuple[str, ...] = ()
    disk_drive: str = ""
    lan_drive: Optional[str] = None
    disk_size_mb: int = DISK_DEFAULT_SINGLE_MB


def min_duration_for_tests(n_tests: int) -> int:
    """Minimum total seconds for a timed custom run with n_tests selected."""
    n = max(1, int(n_tests))
    return max(MIN_TOTAL_SEC, SEC_PER_TEST * n)


def parse_int_clamped(value, lo: int, hi: int, name: str) -> tuple[Optional[int], str]:
    try:
        if isinstance(value, bool):
            raise ValueError("bool")
        if isinstance(value, float) and not value.is_integer():
            return None, f"{name} must be a whole number (got {value})."
        n = int(value)
    except (TypeError, ValueError):
        return None, f"{name} must be a whole number (0 or positive integer)."
    if n < lo or n > hi:
        return None, f"{name} must be between {lo} and {hi} (got {n})."
    return n, ""


def default_disk_root() -> str:
    import sys
    if sys.platform.startswith("win"):
        return "C:\\"
    # Prefer /home if distinct and writable, else /
    home = os.path.expanduser("~")
    if home and os.path.isdir(home):
        return home if home.endswith(os.sep) else home + os.sep
    return "/"


def normalize_drive(root: str) -> str:
    """Normalize a Windows drive letter or a Linux mount path."""
    import sys

    root = (root or "").strip()
    if not root:
        return ""
    if sys.platform.startswith("win"):
        # Accept "D", "D:", "D:\\", "D:/"
        if len(root) == 1 and root.isalpha():
            root = root + ":\\"
        elif len(root) == 2 and root[0].isalpha() and root[1] == ":":
            root = root + "\\"
        if not root.endswith(("\\", "/")):
            root = root + "\\"
        return os.path.normpath(root) + ("\\" if not root.endswith("\\") else "")
    # Linux / POSIX mount
    root = os.path.expanduser(root)
    root = os.path.abspath(root)
    if root != "/" and not root.endswith("/"):
        root = root + "/"
    return root


def check_drive_exists(root: str) -> tuple[bool, str, Optional[int]]:
    """Return (ok, message, free_bytes)."""
    root = normalize_drive(root)
    if not root:
        return False, "Drive path is empty.", None
    if not os.path.exists(root):
        return False, f"Drive '{root}' does not exist or is not available.", None
    try:
        usage = shutil.disk_usage(root)
        return True, "", usage.free
    except Exception as e:
        return False, f"Cannot read free space on '{root}': {e}", None


def planned_disk_size_mb(single_pass: bool, duration_sec: float) -> int:
    if single_pass:
        return DISK_DEFAULT_SINGLE_MB
    return min(DISK_MAX_MB, max(DISK_DEFAULT_SINGLE_MB, int(duration_sec * 8)))


def check_disk_space(drive: str, size_mb: int) -> tuple[bool, str, int]:
    """Ensure size_mb fits with headroom. Returns (ok, msg, clamped_size_mb)."""
    ok, msg, free = check_drive_exists(drive)
    if not ok or free is None:
        return False, msg, size_mb
    size_mb = max(DISK_MIN_MB, min(DISK_MAX_MB, int(size_mb)))
    need = (size_mb + DISK_FREE_HEADROOM_MB) * 1024 * 1024
    if free < need:
        # Clamp down to what fits, if anything above minimum
        max_fit = int((free // (1024 * 1024)) - DISK_FREE_HEADROOM_MB)
        if max_fit < DISK_MIN_MB:
            free_gb = free / (1024**3)
            return (
                False,
                (
                    f"Not enough free space on {normalize_drive(drive)} "
                    f"({free_gb:.1f} GB free). Need at least "
                    f"{DISK_MIN_MB + DISK_FREE_HEADROOM_MB} MB free for the disk test."
                ),
                size_mb,
            )
        return True, f"Disk test file reduced to {max_fit} MB to fit free space.", max_fit
    return True, "", size_mb


def validate_run(
    *,
    mode: str,
    tests: Sequence[str],
    single_pass: bool,
    hours: Optional[int] = None,
    minutes: Optional[int] = None,
    duration_sec: Optional[int] = None,
    disk_drive: str = "",
    lan_drive: Optional[str] = None,
    lan_enabled: bool = False,
) -> ValidationResult:
    mode = (mode or "custom").lower().strip()
    if mode not in ("quick", "detailed", "custom"):
        return ValidationResult(False, f"Unknown mode '{mode}'. Use quick, detailed, or custom.")

    # Normalize tests
    cleaned = []
    for t in tests:
        name = str(t).strip().lower()
        if not name:
            continue
        if name not in VALID_TESTS:
            return ValidationResult(
                False,
                f"Unknown test '{t}'. Choose from: {', '.join(VALID_TESTS)}.",
            )
        if name not in cleaned:
            cleaned.append(name)

    if mode in ("quick", "detailed"):
        cleaned = list(VALID_TESTS)

    if not cleaned:
        return ValidationResult(False, "Select at least one test (CPU, Memory, Disk, Network, or Graphics).")

    # Duration
    if mode == "quick":
        dur = 10 * 60
        single = False
    elif mode == "detailed":
        dur = 60 * 60
        single = False
    else:
        single = bool(single_pass)
        if single:
            dur = 0
        else:
            if duration_sec is not None and duration_sec > 0:
                try:
                    dur = int(duration_sec)
                except (TypeError, ValueError):
                    return ValidationResult(False, "Duration (--duration-sec) must be a whole number of seconds.")
            else:
                h, err = parse_int_clamped(hours if hours is not None else 0, MIN_HOURS, MAX_HOURS, "Hours")
                if err:
                    return ValidationResult(False, err)
                m, err = parse_int_clamped(minutes if minutes is not None else 0, MIN_MINUTES, MAX_MINUTES, "Minutes")
                if err:
                    return ValidationResult(False, err)
                assert h is not None and m is not None
                dur = h * 3600 + m * 60

            if dur < 0:
                return ValidationResult(False, "Duration cannot be negative.")
            if dur > MAX_TOTAL_SEC:
                return ValidationResult(
                    False,
                    f"Duration is too long. Maximum is {MAX_HOURS} hours ({MAX_TOTAL_SEC} seconds).",
                )
            need = min_duration_for_tests(len(cleaned))
            if dur < need:
                mins = need // 60
                secs = need % 60
                need_txt = f"{mins} minute(s)" if secs == 0 else f"{mins}m {secs}s"
                return ValidationResult(
                    False,
                    (
                        f"Custom duration is too short for {len(cleaned)} selected test(s). "
                        f"Minimum is {need_txt} "
                        f"(at least {MIN_TOTAL_SEC}s total, and {SEC_PER_TEST}s per test). "
                        f"You entered {dur}s. "
                        f"Increase hours/minutes, select fewer tests, or turn on Single pass."
                    ),
                )

    # Drives
    disk = normalize_drive(disk_drive or default_disk_root())
    ok, msg, _free = check_drive_exists(disk)
    if not ok:
        return ValidationResult(False, msg)

    size_mb = planned_disk_size_mb(single if mode == "custom" else False, float(dur))
    # For quick/detailed also plan size from duration share roughly
    if mode != "custom":
        size_mb = planned_disk_size_mb(False, float(dur) / max(1, len(cleaned)))

    space_ok, space_msg, size_mb = check_disk_space(disk, size_mb)
    if not space_ok:
        return ValidationResult(False, space_msg)

    lan = None
    if lan_enabled or (lan_drive and str(lan_drive).strip() and str(lan_drive).strip() != "(none)"):
        lan = normalize_drive(lan_drive or "")
        if not lan:
            return ValidationResult(False, "LAN/NAS drive is enabled but no drive was chosen.")
        ok, msg, _ = check_drive_exists(lan)
        if not ok:
            return ValidationResult(False, f"LAN/NAS drive: {msg}")
        # Soft check: at least DISK_MIN for LAN copy
        lan_ok, lan_msg, _ = check_disk_space(lan, DISK_MIN_MB)
        if not lan_ok:
            return ValidationResult(False, f"LAN/NAS drive: {lan_msg}")

    note = space_msg  # may mention size clamp
    return ValidationResult(
        ok=True,
        message=note,
        duration_sec=int(dur),
        single_pass=bool(single) if mode == "custom" else False,
        tests=tuple(cleaned),
        disk_drive=disk,
        lan_drive=lan,
        disk_size_mb=size_mb,
    )


def duration_hint(n_tests: int) -> str:
    need = min_duration_for_tests(n_tests)
    return (
        f"Custom timed runs need at least {need // 60} min "
        f"({SEC_PER_TEST}s per test, minimum {MIN_TOTAL_SEC}s). "
        f"Hours 0-{MAX_HOURS}, minutes 0-{MAX_MINUTES}. Single pass ignores duration."
    )
