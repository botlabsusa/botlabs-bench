"""Custom-mode validation limits and helpers."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


from validate import (
    DISK_DEFAULT_SINGLE_MB,
    DISK_FREE_HEADROOM_MB,
    DISK_MAX_MB,
    DISK_MIN_MB,
    MAX_HOURS,
    MAX_MINUTES,
    MIN_HOURS,
    MIN_MINUTES,
    MIN_TOTAL_SEC,
    SEC_PER_TEST,
    VALID_TESTS,
    check_disk_space,
    duration_hint,
    min_duration_for_tests,
    normalize_drive,
    parse_int_clamped,
    planned_disk_size_mb,
    validate_run,
)


def test_parse_int_clamped_hours_bounds():
    assert parse_int_clamped(0, MIN_HOURS, MAX_HOURS, "Hours") == (0, "")
    assert parse_int_clamped(72, MIN_HOURS, MAX_HOURS, "Hours") == (72, "")
    assert parse_int_clamped(-1, MIN_HOURS, MAX_HOURS, "Hours")[0] is None
    assert parse_int_clamped(73, MIN_HOURS, MAX_HOURS, "Hours")[0] is None


def test_parse_int_clamped_minutes_bounds():
    assert parse_int_clamped(0, MIN_MINUTES, MAX_MINUTES, "Minutes") == (0, "")
    assert parse_int_clamped(59, MIN_MINUTES, MAX_MINUTES, "Minutes") == (59, "")
    assert parse_int_clamped(-1, MIN_MINUTES, MAX_MINUTES, "Minutes")[0] is None
    assert parse_int_clamped(60, MIN_MINUTES, MAX_MINUTES, "Minutes")[0] is None


def test_parse_int_clamped_rejects_non_integers():
    assert parse_int_clamped("abc", MIN_HOURS, MAX_HOURS, "Hours")[0] is None
    assert parse_int_clamped(True, MIN_HOURS, MAX_HOURS, "Hours")[0] is None
    assert parse_int_clamped(3.14, MIN_HOURS, MAX_HOURS, "Hours")[0] is None


def test_min_duration_for_tests():
    assert min_duration_for_tests(0) == MIN_TOTAL_SEC
    assert min_duration_for_tests(1) == max(MIN_TOTAL_SEC, SEC_PER_TEST * 1)
    assert min_duration_for_tests(1) == 60
    assert min_duration_for_tests(5) == max(MIN_TOTAL_SEC, SEC_PER_TEST * 5)
    assert min_duration_for_tests(5) == 150
    assert min_duration_for_tests(-1) == MIN_TOTAL_SEC


def test_planned_disk_size_mb_clamps_64_to_2048_range():
    assert planned_disk_size_mb(True, 0) == DISK_DEFAULT_SINGLE_MB
    assert planned_disk_size_mb(False, 0) == DISK_DEFAULT_SINGLE_MB
    # 10s * 8 = 80, but floor is 256
    assert planned_disk_size_mb(False, 10) == DISK_DEFAULT_SINGLE_MB
    # 1000s * 8 = 8000, cap at 2048
    assert planned_disk_size_mb(False, 1000) == DISK_MAX_MB
    # 40s * 8 = 320, in range
    assert planned_disk_size_mb(False, 40) == 320
    assert DISK_MIN_MB == 64
    assert DISK_MAX_MB == 2048


def test_duration_hint_mentions_limits():
    hint = duration_hint(3)
    assert "Custom timed runs need at least" in hint
    need = min_duration_for_tests(3)
    assert f"{need // 60} min" in hint
    assert f"{SEC_PER_TEST}s per test" in hint
    assert f"Hours 0-{MAX_HOURS}" in hint
    assert f"minutes 0-{MAX_MINUTES}" in hint


def test_validate_run_rejects_out_of_range_hours_minutes():
    assert not validate_run(mode="custom", tests=["cpu"], hours=-1, minutes=0, single_pass=False).ok
    assert not validate_run(mode="custom", tests=["cpu"], hours=73, minutes=0, single_pass=False).ok
    assert not validate_run(mode="custom", tests=["cpu"], hours=0, minutes=-1, single_pass=False).ok
    assert not validate_run(mode="custom", tests=["cpu"], hours=0, minutes=60, single_pass=False).ok


def test_validate_run_accepts_hour_and_minute_bounds(tmp_path):
    # 1 minute is the 60s floor for a single test
    ok_min = validate_run(
        mode="custom", tests=["cpu"], hours=0, minutes=1, single_pass=False, disk_drive=str(tmp_path)
    )
    assert ok_min.ok, ok_min.message
    assert ok_min.duration_sec == 60

    # Exactly 72 hours is allowed; 72h + 1 min is over the cap
    cap = validate_run(
        mode="custom", tests=["cpu"], hours=72, minutes=0, single_pass=False, disk_drive=str(tmp_path)
    )
    assert cap.ok, cap.message
    assert cap.duration_sec == 72 * 3600

    over = validate_run(
        mode="custom", tests=["cpu"], hours=72, minutes=1, single_pass=False, disk_drive=str(tmp_path)
    )
    assert not over.ok
    assert "too long" in over.message.lower()


def test_validate_run_requires_at_least_one_known_test():
    empty = validate_run(mode="custom", tests=[], single_pass=True)
    assert not empty.ok
    assert "at least one test" in empty.message.lower()

    unknown = validate_run(mode="custom", tests=["unknown"], single_pass=True)
    assert not unknown.ok
    assert "unknown test" in unknown.message.lower()


def test_validate_run_timed_minimums(tmp_path):
    too_short_one = validate_run(
        mode="custom", tests=["cpu"], hours=0, minutes=0, single_pass=False, disk_drive=str(tmp_path)
    )
    assert not too_short_one.ok
    assert "too short" in too_short_one.message.lower()

    # 5 tests need 150s (30s each, above the 60s floor)
    short_five = validate_run(
        mode="custom",
        tests=list(VALID_TESTS),
        duration_sec=149,
        single_pass=False,
        disk_drive=str(tmp_path),
    )
    assert not short_five.ok

    ok_five = validate_run(
        mode="custom",
        tests=list(VALID_TESTS),
        duration_sec=150,
        single_pass=False,
        disk_drive=str(tmp_path),
    )
    assert ok_five.ok, ok_five.message
    assert ok_five.duration_sec == 150


def test_validate_run_single_pass_ignores_duration(tmp_path):
    result = validate_run(
        mode="custom", tests=["cpu"], single_pass=True, hours=0, minutes=0, disk_drive=str(tmp_path)
    )
    assert result.ok, result.message
    assert result.single_pass is True
    assert result.duration_sec == 0


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("bad", [0, -10])
def test_validate_run_rejects_non_positive_duration_sec(tmp_path, bad, single):
    """duration_sec <= 0 is an error; it no longer falls back to hours/minutes."""
    result = validate_run(
        mode="custom", tests=["cpu"], single_pass=single, duration_sec=bad, hours=1, minutes=0, disk_drive=str(tmp_path)
    )
    assert not result.ok
    assert "positive" in result.message.lower()
    assert str(bad) in result.message


def test_validate_run_positive_duration_sec_is_used(tmp_path):
    result = validate_run(
        mode="custom", tests=["cpu"], single_pass=False, duration_sec=90, hours=1, minutes=0, disk_drive=str(tmp_path)
    )
    assert result.ok, result.message
    assert result.duration_sec == 90


def test_validate_run_quick_and_detailed_durations(tmp_path):
    quick = validate_run(mode="quick", tests=[], single_pass=False, disk_drive=str(tmp_path))
    assert quick.ok, quick.message
    assert quick.duration_sec == 600
    assert quick.tests == VALID_TESTS
    assert quick.single_pass is False

    detailed = validate_run(mode="detailed", tests=["cpu"], single_pass=False, disk_drive=str(tmp_path))
    assert detailed.ok, detailed.message
    assert detailed.duration_sec == 3600
    assert detailed.tests == VALID_TESTS


def test_validate_run_unknown_mode():
    result = validate_run(mode="invalid", tests=["cpu"], single_pass=True)
    assert not result.ok
    assert "unknown mode" in result.message.lower()


def test_validate_run_missing_disk_drive():
    result = validate_run(
        mode="custom", tests=["cpu"], single_pass=True, disk_drive="/nonexistent-botlabs-drive"
    )
    assert not result.ok


def test_validate_run_skips_disk_space_when_disk_not_selected(tmp_path):
    """A nearly full drive must not block a CPU/memory-only run."""
    usage = SimpleNamespace(free=1)  # 1 byte free

    with patch("validate.shutil.disk_usage", return_value=usage):
        result = validate_run(
            mode="custom", tests=["cpu", "memory"], single_pass=True, disk_drive=str(tmp_path)
        )
    assert result.ok, result.message


def test_validate_run_checks_disk_space_when_disk_selected(tmp_path):
    usage = SimpleNamespace(free=1)  # 1 byte free

    with patch("validate.shutil.disk_usage", return_value=usage):
        result = validate_run(
            mode="custom", tests=["cpu", "disk"], single_pass=True, disk_drive=str(tmp_path)
        )
    assert not result.ok
    assert "free space" in result.message.lower()


def test_validate_run_quick_still_checks_disk_space(tmp_path):
    """Quick/Detailed always include Disk, so the free-space check still applies."""
    usage = SimpleNamespace(free=1)

    with patch("validate.shutil.disk_usage", return_value=usage):
        result = validate_run(mode="quick", tests=[], single_pass=False, disk_drive=str(tmp_path))
    assert not result.ok
    assert "free space" in result.message.lower()


def test_check_disk_space_too_little_and_clamp(tmp_path):
    mb = 1024 * 1024
    # Just under the minimum (64 + 512 MB headroom) -> fail
    tight = SimpleNamespace(free=(DISK_MIN_MB + DISK_FREE_HEADROOM_MB - 1) * mb)
    with patch("validate.shutil.disk_usage", return_value=tight):
        ok, msg, size = check_disk_space(str(tmp_path), DISK_MIN_MB)
    assert not ok
    assert "not enough free space" in msg.lower()

    # Enough for 200 MB + headroom, but not for 1024 MB -> clamp
    mid = SimpleNamespace(free=(200 + DISK_FREE_HEADROOM_MB) * mb)
    with patch("validate.shutil.disk_usage", return_value=mid):
        ok, msg, size = check_disk_space(str(tmp_path), 1024)
    assert ok
    assert size == 200
    assert "reduced to 200 MB" in msg

    # Plenty of room
    plenty = SimpleNamespace(free=(4096 + DISK_FREE_HEADROOM_MB) * mb)
    with patch("validate.shutil.disk_usage", return_value=plenty):
        ok, msg, size = check_disk_space(str(tmp_path), 1024)
    assert ok
    assert msg == ""
    assert size == 1024


def test_normalize_drive_posix_or_windows():
    if sys.platform.startswith("win"):
        assert normalize_drive("C") == "C:\\"
        assert normalize_drive("C:") == "C:\\"
        assert normalize_drive("C:\\") == "C:\\"
    else:
        assert normalize_drive("/") == "/"
        assert normalize_drive("/tmp") == "/tmp/"
        assert normalize_drive("") == ""


def test_validate_run_lan_drive(tmp_path):
    missing = validate_run(
        mode="custom",
        tests=["cpu"],
        single_pass=True,
        disk_drive=str(tmp_path),
        lan_enabled=True,
        lan_drive="",
    )
    assert not missing.ok
    assert "lan" in missing.message.lower() or "no drive" in missing.message.lower()

    ok = validate_run(
        mode="custom",
        tests=["cpu"],
        single_pass=True,
        disk_drive=str(tmp_path),
        lan_enabled=True,
        lan_drive=str(tmp_path),
    )
    assert ok.ok, ok.message
    assert ok.lan_drive
