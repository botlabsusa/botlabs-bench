"""Load multiple JSON result files and produce a side-by-side HTML comparison."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _esc(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _metric(state: dict[str, Any], path: list[str]) -> Any:
    cur: Any = state
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur



def _version_warning(states: list[dict[str, Any]]) -> str:
    """HTML banner when schema/bench versions differ across compared runs."""
    rows = []
    for s in states:
        v = s.get("bench_version") or s.get("version") or "unknown"
        name = (s.get("sysinfo") or {}).get("computer_name") or s.get("run_id") or "?"
        rows.append((str(name), str(v)))
    unique = {v for _, v in rows}
    if len(unique) <= 1:
        vers = rows[0][1] if rows else "unknown"
        return f"<p class=\"meta\">bench_version={_esc(vers)}</p>"
    items = "".join(f"<li>{_esc(n)}: bench_version={_esc(v)}</li>" for n, v in rows)
    return (
        "<div class=\"warn\"><strong>Warning:</strong> benchmark versions differ — "
        "scores may not be directly comparable. Compare only runs with the same "
        f"bench_version when possible.<ul>{items}</ul></div>"
    )



METRICS = [
    ("Bench version", ["bench_version"]),
    ("Computer", ["sysinfo", "computer_name"]),
    ("CPU", ["sysinfo", "cpu_name"]),
    ("RAM GB", ["sysinfo", "ram_total_gb"]),
    ("CPU single (primes/s)", ["results", "cpu", "single_core", "score_primes_per_sec"]),
    ("CPU all-core (primes/s)", ["results", "cpu", "all_core", "score_primes_per_sec"]),
    ("CPU throttle drop %", ["results", "cpu", "sustained", "throttle_drop_pct"]),
    ("Mem read MB/s", ["results", "memory", "bandwidth", "read_mb_s"]),
    ("Mem write MB/s", ["results", "memory", "bandwidth", "write_mb_s"]),
    ("Mem latency ns", ["results", "memory", "latency_ns"]),
    ("Disk seq read MB/s", ["results", "disk", "sequential", "read_mb_s_avg"]),
    ("Disk seq write MB/s", ["results", "disk", "sequential", "write_mb_s_avg"]),
    ("Disk 4K read IOPS", ["results", "disk", "random_4k", "read_iops"]),
    ("Internet Mbps", ["results", "network", "internet", "best_mbps"]),
    ("LAN write MB/s", ["results", "network", "lan", "write_mb_s"]),
    ("GPU FPS avg (OpenGL)", ["results", "gpu", "fps"]),
    ("GPU 1% low FPS", ["results", "gpu", "fps_1pct_low"]),
    ("GPU score", ["results", "gpu", "gpu_score"]),
]


def compare_runs(json_paths: list[Path], out_html: Path) -> Path:
    states = []
    for p in json_paths:
        with open(p, encoding="utf-8") as f:
            states.append(json.load(f))

    headers = "".join(f"<th>{_esc((s.get('sysinfo') or {}).get('computer_name') or s.get('run_id'))}</th>" for s in states)
    rows = []
    for label, path in METRICS:
        cells = "".join(f"<td>{_esc(_metric(s, path))}</td>" for s in states)
        rows.append(f"<tr><th>{_esc(label)}</th>{cells}</tr>")

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Botlabs Bench Compare</title>
<style>
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#020617;color:#e2e8f0;margin:0}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px}}
table{{width:100%;border-collapse:collapse}}
th,td{{border-bottom:1px solid #334155;padding:10px;text-align:left}}
th{{color:#94a3b8}}
h1{{margin-top:0}}
</style></head><body><div class="wrap">
<h1>Botlabs Bench - Side-by-side</h1>
<p>{len(states)} runs compared</p>
{_version_warning(states)}
<table><tr><th>Metric</th>{headers}</tr>
{''.join(rows)}
</table>
</div></body></html>"""
    out_html.write_text(html, encoding="utf-8")
    return out_html
