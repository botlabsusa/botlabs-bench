"""CLI argument parsing for Botlabs Bench."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


from main import _parse_args


def test_parse_args_defaults():
    args = _parse_args([])
    assert args.headless is False
    assert args.mode == "custom"
    assert args.tests == "cpu,memory,disk,network,gpu"
    assert args.single_pass is False
    assert args.duration_sec is None
    assert args.hours == 0
    assert args.minutes == 0
    assert args.disk_drive == ""
    assert args.lan_drive == ""


def test_parse_args_headless():
    args = _parse_args(["--headless"])
    assert args.headless is True


def test_parse_args_mode_choices():
    for mode in ("quick", "detailed", "custom"):
        assert _parse_args(["--mode", mode]).mode == mode


def test_parse_args_invalid_mode_exits():
    with pytest.raises(SystemExit):
        _parse_args(["--mode", "invalid"])


def test_parse_args_tests_single_pass_duration_and_disk_drive():
    args = _parse_args(
        [
            "--headless",
            "--mode",
            "custom",
            "--tests",
            "cpu,memory",
            "--single-pass",
            "--duration-sec",
            "180",
            "--disk-drive",
            "/tmp",
        ]
    )
    assert args.headless is True
    assert args.mode == "custom"
    assert args.tests == "cpu,memory"
    assert args.single_pass is True
    assert args.duration_sec == 180
    assert args.disk_drive == "/tmp"


def test_parse_args_hours_and_minutes():
    args = _parse_args(["--hours", "1", "--minutes", "30"])
    assert args.hours == 1
    assert args.minutes == 30


def test_parse_args_duration_sec_parses_negative_int():
    """argparse type=int keeps negatives; run_headless rejects them (see below)."""
    args = _parse_args(["--duration-sec", "-10"])
    assert args.duration_sec == -10


@pytest.mark.parametrize("value", ["0", "-10"])
def test_run_headless_rejects_non_positive_duration_sec(value, tmp_path, capsys):
    from main import run_headless

    args = _parse_args(
        ["--headless", "--tests", "cpu", "--duration-sec", value, "--results-dir", str(tmp_path)]
    )
    rc = run_headless(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "--duration-sec must be a positive" in err
    # Rejected before any run starts, so nothing is written.
    assert not any(tmp_path.glob("*.json"))
