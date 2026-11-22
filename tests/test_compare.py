"""Compare HTML and version-mismatch warning."""
from __future__ import annotations

import json
import sys
from pathlib import Path


from compare import METRICS, _esc, _metric, _version_warning, compare_runs


def test_version_warning_same_version_has_no_banner():
    html = _version_warning(
        [
            {"bench_version": "1.2.0", "sysinfo": {"computer_name": "PC-A"}},
            {"bench_version": "1.2.0", "sysinfo": {"computer_name": "PC-B"}},
        ]
    )
    assert "Warning" not in html
    assert "bench_version=1.2.0" in html


def test_version_warning_mismatched_versions():
    html = _version_warning(
        [
            {"bench_version": "1.1.0", "sysinfo": {"computer_name": "PC-A"}},
            {"bench_version": "1.2.0", "sysinfo": {"computer_name": "PC-B"}},
        ]
    )
    assert "Warning" in html
    assert "versions differ" in html
    assert "1.1.0" in html and "1.2.0" in html
    assert "PC-A" in html and "PC-B" in html


def test_version_warning_falls_back_to_version_field():
    # Same numeric version via the older "version" key should not warn
    same = _version_warning(
        [
            {"version": "1.2.0", "sysinfo": {"computer_name": "PC-A"}},
            {"bench_version": "1.2.0", "sysinfo": {"computer_name": "PC-B"}},
        ]
    )
    assert "Warning" not in same
    assert "bench_version=1.2.0" in same

    mismatched = _version_warning(
        [
            {"version": "1.1.0", "sysinfo": {"computer_name": "PC-A"}},
            {"bench_version": "1.2.0", "sysinfo": {"computer_name": "PC-B"}},
        ]
    )
    assert "Warning" in mismatched
    assert "versions differ" in mismatched


def test_metric_nested_path_and_missing():
    state = {"results": {"cpu": {"single_core": {"score_primes_per_sec": 123.4}}}}
    assert _metric(state, ["results", "cpu", "single_core", "score_primes_per_sec"]) == 123.4
    assert _metric(state, ["results", "cpu", "missing"]) is None
    assert _metric(state, ["results", "cpu", "single_core", "score_primes_per_sec", "nope"]) is None
    assert any(label == "Bench version" for label, _path in METRICS)
    assert _esc("a < b & c") == "a &lt; b &amp; c"


def test_compare_runs_matching_versions(tmp_path):
    a = {
        "run_id": "run-1",
        "bench_version": "1.2.0",
        "sysinfo": {"computer_name": "PC-A", "cpu_name": "CPU-A"},
        "results": {"cpu": {"single_core": {"score_primes_per_sec": 123.4}}},
    }
    b = {
        "run_id": "run-2",
        "bench_version": "1.2.0",
        "sysinfo": {"computer_name": "PC-B", "cpu_name": "CPU-B"},
        "results": {"cpu": {"single_core": {"score_primes_per_sec": 150.0}}},
    }
    p1, p2 = tmp_path / "run-1.json", tmp_path / "run-2.json"
    p1.write_text(json.dumps(a), encoding="utf-8")
    p2.write_text(json.dumps(b), encoding="utf-8")
    out = tmp_path / "compare.html"
    compare_runs([p1, p2], out)
    content = out.read_text(encoding="utf-8")
    assert "PC-A" in content and "PC-B" in content
    assert "123.4" in content and "150.0" in content
    assert "Warning" not in content


def test_compare_runs_writes_warning_when_versions_differ(tmp_path):
    a = {
        "run_id": "run-1",
        "bench_version": "1.1.0",
        "sysinfo": {"computer_name": "PC-A"},
        "results": {},
    }
    b = {
        "run_id": "run-2",
        "bench_version": "1.2.0",
        "sysinfo": {"computer_name": "PC-B"},
        "results": {},
    }
    p1, p2 = tmp_path / "run-1.json", tmp_path / "run-2.json"
    p1.write_text(json.dumps(a), encoding="utf-8")
    p2.write_text(json.dumps(b), encoding="utf-8")
    out = tmp_path / "compare.html"
    compare_runs([p1, p2], out)
    content = out.read_text(encoding="utf-8")
    assert "PC-A" in content and "PC-B" in content
    assert "Warning" in content
    assert "versions differ" in content
