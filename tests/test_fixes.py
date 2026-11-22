"""Regression tests for the v1.2.1 fixes (no GUI, no real benchmarks, no network)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import reports
from version import APP_VERSION


# Memory latency after cancel divides by completed steps
def test_latency_ns_after_cancel_uses_completed_steps(monkeypatch):
    from tests import memory

    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1  # cancel at the second check (after 0x100000 steps)

    clock = iter([0.0, 1.0])
    monkeypatch.setattr(memory.time, "perf_counter", lambda: next(clock))
    ns = memory._latency_ns(cancel)
    done = 0x100000
    assert ns == pytest.approx(1e9 / done)
    # The old math (divide by the planned 5,000,000 steps) would report ~200 ns.
    assert ns > 1e9 / 5_000_000 * 4


def test_latency_ns_cancel_before_any_step_returns_zero(monkeypatch):
    from tests import memory

    assert memory._latency_ns(lambda: True) == 0.0


# _fs_key failed lookups never count as the same filesystem
def test_same_fs_treats_unknown_as_different():
    from tests.disk import _same_fs

    assert _same_fs(None, None) is False
    assert _same_fs("", "") is False
    assert _same_fs(None, 5) is False
    assert _same_fs(5, 5) is True
    assert _same_fs("C:", "C:") is True
    assert _same_fs("C:", "D:") is False


def test_resolve_workdir_ignores_home_when_lookups_fail(monkeypatch, tmp_path):
    from tests import disk

    root = tmp_path / "readonly-root"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(disk, "_fs_key", lambda p: None)
    monkeypatch.setattr(disk.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(disk, "_probe_writable", lambda p: False)
    monkeypatch.setattr(disk.sys, "platform", "linux")
    path, note = disk.resolve_writable_workdir(str(root))
    assert path is None
    assert str(home) not in note


# HTML chart skips non-numeric points instead of crashing
def test_svg_line_skips_non_numeric_points():
    svg = reports._svg_line([0, 1, 2, 3, 4], [10.0, None, "bad", float("nan"), 30.0], title="CPU %")
    assert "polyline" in svg
    assert "nan" not in svg.lower()


def test_svg_line_all_non_numeric_shows_na():
    svg = reports._svg_line([0, 1], [None, "x"], title="Temp")
    assert "Temp: n/a" in svg


def test_render_html_with_bad_sensor_values():
    state = {
        "run_id": "bad-series",
        "status": "completed",
        "sensor_series": {"times": [0, 1, 2], "cpu": [1, None, "x"], "temp": [None, None, None]},
    }
    html = reports.render_html(state)
    assert "bad-series" in html
    assert f"Botlabs Bench {APP_VERSION}" in html


# XLSX failures are logged, and only real files are returned
def test_write_all_reports_logs_xlsx_failure(monkeypatch, tmp_path, caplog):
    def boom(state, path):
        raise RuntimeError("disk full")

    monkeypatch.setattr(reports, "write_xlsx", boom)
    with caplog.at_level(logging.WARNING, logger="reports"):
        paths = reports.write_all_reports({"run_id": "r1"}, tmp_path)
    assert "xlsx" not in paths
    assert paths["html"].is_file() and paths["csv"].is_file()
    assert any("XLSX report failed" in r.getMessage() for r in caplog.records)


def test_write_all_reports_lists_json_when_runner_wrote_it(tmp_path):
    (tmp_path / "r2.json").write_text("{}", encoding="utf-8")
    paths = reports.write_all_reports({"run_id": "r2"}, tmp_path)
    assert paths["json"] == tmp_path / "r2.json"


# collect_sysinfo runs once per runner
def test_runner_collects_sysinfo_once(monkeypatch, tmp_path):
    import runner

    calls = {"n": 0}

    def fake_sysinfo():
        calls["n"] += 1
        return {"computer_name": "UNIT-PC"}

    monkeypatch.setattr(runner, "collect_sysinfo", fake_sysinfo)
    r = runner.BenchmarkRunner(tmp_path, "custom", ["cpu"], 0, True, disk_drive=str(tmp_path))
    assert calls["n"] == 1
    assert r.run_id.startswith("UNIT-PC-")
    assert r.state["sysinfo"] == {"computer_name": "UNIT-PC"}
    saved = json.loads(r.json_path.read_text(encoding="utf-8"))
    assert saved["bench_version"] == APP_VERSION


# A failed probe cleanup does not trigger the home-folder fallback
def test_writable_base_keeps_dir_when_unlink_fails(monkeypatch, tmp_path):
    import main

    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    real_unlink = Path.unlink

    def failing_unlink(self, *a, **kw):
        if self.name == ".botlabs_write_probe":
            raise PermissionError("locked")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fallback-home"))
    assert main._writable_base() == tmp_path
    assert not (tmp_path / "fallback-home").exists()


def test_app_dir_keeps_dir_when_unlink_fails(monkeypatch, tmp_path):
    app = pytest.importorskip("app")

    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(app, "__file__", str(tmp_path / "app.py"))
    real_unlink = Path.unlink

    def failing_unlink(self, *a, **kw):
        if self.name == ".botlabs_write_probe":
            raise PermissionError("locked")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fallback-home"))
    assert app.app_dir() == tmp_path
    assert not (tmp_path / "fallback-home").exists()


# compare_runs gives a clear error for unreadable / bad JSON
def test_compare_runs_bad_json_raises_clear_error(tmp_path):
    from compare import CompareError, compare_runs

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"bench_version": APP_VERSION}), encoding="utf-8")
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(CompareError, match="broken.json"):
        compare_runs([good, bad], tmp_path / "out.html")
    with pytest.raises(CompareError, match="missing.json"):
        compare_runs([tmp_path / "missing.json"], tmp_path / "out.html")


# Windows default drive prefers the system drive, then the first existing one
def test_windows_default_drive_prefers_system_drive(monkeypatch):
    import validate

    monkeypatch.setenv("SystemDrive", "D:")
    monkeypatch.setattr(validate.os.path, "isdir", lambda p: p in ("D:\\", "E:\\"))
    assert validate.windows_default_drive() == "D:\\"


def test_windows_default_drive_falls_back_to_first_existing(monkeypatch):
    import validate

    monkeypatch.delenv("SystemDrive", raising=False)
    monkeypatch.setattr(validate.os.path, "isdir", lambda p: p == "E:\\")
    assert validate.windows_default_drive() == "E:\\"


def test_default_disk_root_uses_windows_default(monkeypatch):
    import validate

    monkeypatch.setattr(validate, "windows_default_drive", lambda: "D:\\")
    monkeypatch.setattr("sys.platform", "win32")
    assert validate.default_disk_root() == "D:\\"


# Network User-Agent comes from the version constant
def test_download_user_agent_uses_app_version(monkeypatch):
    from tests import network

    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b""

    def fake_urlopen(req, timeout=None, context=None):
        seen["ua"] = req.get_header("User-agent")
        return FakeResp()

    monkeypatch.setattr(network.urllib.request, "urlopen", fake_urlopen)
    network._download_speed("https://example.invalid/x", lambda: False, lambda *a: None, 0.0, 1.0)
    assert seen["ua"] == f"BotlabsBench/{APP_VERSION}"
