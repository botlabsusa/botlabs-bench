"""Write HTML (self-contained), CSV, and XLSX reports from a run state."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _svg_line(series_x: list[float], series_y: list[float], width: int = 640, height: int = 180, color: str = "#3b82f6", title: str = "") -> str:
    if not series_x or not series_y or len(series_x) != len(series_y):
        return f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg"><text x="12" y="24" fill="#666">{title or "No data"}</text></svg>'
    # Filter NaNs
    pts = [(x, y) for x, y in zip(series_x, series_y) if y == y]  # y==y rejects nan
    if not pts:
        return f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg"><text x="12" y="24" fill="#666">{title}: n/a</text></svg>'
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x <= min_x:
        max_x = min_x + 1
    if max_y <= min_y:
        max_y = min_y + 1
    pad_l, pad_r, pad_t, pad_b = 40, 12, 28, 28

    def tx(x: float) -> float:
        return pad_l + (x - min_x) / (max_x - min_x) * (width - pad_l - pad_r)

    def ty(y: float) -> float:
        return pad_t + (1 - (y - min_y) / (max_y - min_y)) * (height - pad_t - pad_b)

    poly = " ".join(f"{tx(x):.1f},{ty(y):.1f}" for x, y in pts)
    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="background:#0f172a;border-radius:8px;margin:8px 0">
  <text x="12" y="18" fill="#e2e8f0" font-family="Segoe UI,sans-serif" font-size="13">{title}</text>
  <text x="12" y="{height - 8}" fill="#94a3b8" font-size="11">{min_y:.1f} - {max_y:.1f}</text>
  <polyline fill="none" stroke="{color}" stroke-width="2" points="{poly}"/>
</svg>'''


def _esc(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _score_cards(results: dict[str, Any]) -> str:
    cards = []
    cpu = results.get("cpu") or {}
    if cpu:
        sc = (cpu.get("single_core") or {}).get("score_primes_per_sec", "-")
        ac = (cpu.get("all_core") or {}).get("score_primes_per_sec", "-")
        thr = (cpu.get("sustained") or {}).get("throttle_drop_pct", "-")
        cards.append(("CPU single-core", f"{sc}", "primes/s"))
        cards.append(("CPU all-core", f"{ac}", "primes/s"))
        if thr != "-":
            cards.append(("CPU throttle drop", f"{thr}%", "sustained"))
    mem = results.get("memory") or {}
    if mem:
        bw = mem.get("bandwidth") or {}
        cards.append(("Mem read", f"{bw.get('read_mb_s', '-')}", "MB/s"))
        cards.append(("Mem write", f"{bw.get('write_mb_s', '-')}", "MB/s"))
        cards.append(("Mem latency", f"{mem.get('latency_ns', '-')}", "ns"))
        ec = mem.get("error_check") or {}
        if "passed" in ec:
            cards.append(("Mem errors", "PASS" if ec.get("passed") else f"FAIL ({ec.get('errors')})", ""))
    disk = results.get("disk") or {}
    if disk:
        seq = disk.get("sequential") or {}
        rnd = disk.get("random_4k") or {}
        cards.append(("Disk seq read", f"{seq.get('read_mb_s_avg', '-')}", "MB/s"))
        cards.append(("Disk seq write", f"{seq.get('write_mb_s_avg', '-')}", "MB/s"))
        cards.append(("Disk 4K read", f"{rnd.get('read_iops', '-')}", "IOPS"))
    net = results.get("network") or {}
    if net:
        inet = net.get("internet") or {}
        cards.append(("Internet", f"{inet.get('best_mbps', '-')}", "Mbps"))
        lan = net.get("lan") or {}
        if lan.get("write_mb_s") is not None:
            cards.append(("LAN write", f"{lan.get('write_mb_s')}", "MB/s"))
    gpu = results.get("gpu") or {}
    if gpu:
        if gpu.get("fps") is not None:
            unit = "OpenGL"
            if gpu.get("software_renderer"):
                unit = "OpenGL (software)"
            cards.append(("GPU FPS avg", f"{gpu.get('fps')}", unit))
            if gpu.get("fps_1pct_low") is not None:
                cards.append(("GPU 1% low", f"{gpu.get('fps_1pct_low')}", "FPS"))
            if gpu.get("gpu_score") is not None:
                cards.append(("GPU score", f"{gpu.get('gpu_score')}", gpu.get("gl_renderer") or "OpenGL"))
        elif gpu.get("status") == "skipped":
            cards.append(("GPU", "skipped", "OpenGL unavailable"))

    html = ['<div class="cards">']
    for title, val, unit in cards:
        html.append(
            f'<div class="card"><div class="card-title">{_esc(title)}</div>'
            f'<div class="card-val">{_esc(val)}</div>'
            f'<div class="card-unit">{_esc(unit)}</div></div>'
        )
    html.append("</div>")
    return "\n".join(html)


def render_html(state: dict[str, Any]) -> str:
    si = state.get("sysinfo") or {}
    series = state.get("sensor_series") or {}
    times = series.get("times") or []
    charts = []
    charts.append(_svg_line(times, series.get("cpu") or [], color="#38bdf8", title="CPU %"))
    charts.append(_svg_line(times, series.get("mem") or [], color="#a78bfa", title="Memory %"))
    charts.append(_svg_line(times, series.get("clock") or [], color="#34d399", title="CPU clock MHz"))
    charts.append(_svg_line(times, series.get("temp") or [], color="#f87171", title="CPU temp °C"))
    charts.append(_svg_line(times, series.get("disk_mb_s") or [], color="#fbbf24", title="Disk MB/s"))

    disks = si.get("disks") or []
    disk_rows = "".join(
        f"<tr><td>{_esc(d.get('mount'))}</td><td>{_esc(d.get('total_gb'))} GB</td><td>{_esc(d.get('free_gb'))} GB</td></tr>"
        for d in disks
    )
    results_json = _esc(json.dumps(state.get("results") or {}, indent=2))
    temp_note = _esc((state.get("sensor_summary") or {}).get("temps_note") or "")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>Botlabs Bench - {_esc(state.get('run_id'))}</title>
<style>
body{{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:#020617;color:#e2e8f0}}
.wrap{{max-width:980px;margin:0 auto;padding:24px}}
h1{{font-size:1.6rem;margin:0 0 4px}}
.sub{{color:#94a3b8;margin-bottom:20px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin:16px 0}}
.card{{background:#1e293b;border-radius:12px;padding:14px}}
.card-title{{font-size:.75rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em}}
.card-val{{font-size:1.4rem;font-weight:700;margin-top:6px}}
.card-unit{{font-size:.75rem;color:#64748b}}
table{{width:100%;border-collapse:collapse;margin:12px 0}}
td,th{{border-bottom:1px solid #334155;padding:8px;text-align:left}}
pre{{background:#0f172a;padding:12px;border-radius:8px;overflow:auto;font-size:12px}}
.badge{{display:inline-block;background:#14532d;color:#86efac;padding:2px 10px;border-radius:999px;font-size:.8rem}}
.badge.warn{{background:#7c2d12;color:#fdba74}}
</style></head><body><div class="wrap">
<h1>Botlabs Bench Report</h1>
<div class="sub">{_esc(state.get('run_id'))} ·
<span class="badge{' warn' if state.get('status')!='completed' else ''}">{_esc(state.get('status'))}</span>
· mode {_esc(state.get('mode'))}
· elapsed {_esc(state.get('elapsed_sec'))}s</div>

<h2>System</h2>
<table>
<tr><th>Computer</th><td>{_esc(si.get('computer_name'))}</td></tr>
<tr><th>CPU</th><td>{_esc(si.get('cpu_name'))} ({_esc(si.get('cpu_physical_cores'))}P / {_esc(si.get('cpu_logical_cores'))}L)</td></tr>
<tr><th>RAM</th><td>{_esc(si.get('ram_total_gb'))} GB total, {_esc(si.get('ram_available_gb'))} GB free at start</td></tr>
<tr><th>OS</th><td>{_esc(si.get('os'))} {_esc(si.get('os_release'))} · {_esc(si.get('os_build'))}</td></tr>
<tr><th>GPU</th><td>{_esc(', '.join(si.get('gpus') or []))}</td></tr>
</table>
<table><tr><th>Drive</th><th>Total</th><th>Free</th></tr>{disk_rows}</table>

<h2>Scores</h2>
{_score_cards(state.get('results') or {})}

<h2>Live sensors during run</h2>
<p class="sub">{temp_note}</p>
{''.join(charts)}

<h2>Raw results</h2>
<pre>{results_json}</pre>
<p class="sub">Generated by Botlabs Bench 1.0 · offline self-contained report</p>
</div></body></html>"""


def write_csv(state: dict[str, Any], path: Path) -> None:
    rows = []
    si = state.get("sysinfo") or {}
    rows.append(["field", "value"])
    rows.append(["run_id", state.get("run_id")])
    rows.append(["computer", si.get("computer_name")])
    rows.append(["status", state.get("status")])
    rows.append(["mode", state.get("mode")])
    rows.append(["elapsed_sec", state.get("elapsed_sec")])
    results = state.get("results") or {}
    cpu = results.get("cpu") or {}
    rows.append(["cpu_single_primes_per_sec", (cpu.get("single_core") or {}).get("score_primes_per_sec")])
    rows.append(["cpu_all_primes_per_sec", (cpu.get("all_core") or {}).get("score_primes_per_sec")])
    rows.append(["cpu_throttle_drop_pct", (cpu.get("sustained") or {}).get("throttle_drop_pct")])
    mem = results.get("memory") or {}
    bw = mem.get("bandwidth") or {}
    rows.append(["mem_read_mb_s", bw.get("read_mb_s")])
    rows.append(["mem_write_mb_s", bw.get("write_mb_s")])
    rows.append(["mem_copy_mb_s", bw.get("copy_mb_s")])
    rows.append(["mem_latency_ns", mem.get("latency_ns")])
    rows.append(["mem_errors", (mem.get("error_check") or {}).get("errors")])
    disk = results.get("disk") or {}
    rows.append(["disk_seq_read_mb_s", (disk.get("sequential") or {}).get("read_mb_s_avg")])
    rows.append(["disk_seq_write_mb_s", (disk.get("sequential") or {}).get("write_mb_s_avg")])
    rows.append(["disk_4k_read_iops", (disk.get("random_4k") or {}).get("read_iops")])
    rows.append(["disk_4k_write_iops", (disk.get("random_4k") or {}).get("write_iops")])
    net = results.get("network") or {}
    rows.append(["net_internet_mbps", (net.get("internet") or {}).get("best_mbps")])
    rows.append(["net_lan_write_mb_s", (net.get("lan") or {}).get("write_mb_s")])
    gpu = results.get("gpu") or {}
    rows.append(["gpu_fps_avg", gpu.get("fps")])
    rows.append(["gpu_fps_1pct_low", gpu.get("fps_1pct_low")])
    rows.append(["gpu_score", gpu.get("gpu_score")])
    rows.append(["gpu_gl_renderer", gpu.get("gl_renderer")])
    rows.append(["gpu_gl_vendor", gpu.get("gl_vendor")])
    rows.append(["gpu_software_renderer", gpu.get("software_renderer")])
    rows.append(["gpu_status", gpu.get("status")])
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def write_xlsx(state: dict[str, Any], path: Path) -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    # Reuse CSV rows
    import io
    import tempfile

    tmp = path.with_suffix(".csv.tmp")
    write_csv(state, tmp)
    with open(tmp, encoding="utf-8") as f:
        for r_i, row in enumerate(csv.reader(f), 1):
            for c_i, val in enumerate(row, 1):
                ws.cell(r_i, c_i, val)
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass

    # Sensors sheet
    series = state.get("sensor_series") or {}
    ws2 = wb.create_sheet("Sensors")
    headers = ["t", "cpu", "mem", "clock", "temp", "disk_mb_s"]
    for c, h in enumerate(headers, 1):
        ws2.cell(1, c, h)
    n = len(series.get("times") or [])
    for i in range(n):
        for c, h in enumerate(headers, 1):
            arr = series.get(h if h != "t" else "times") or []
            ws2.cell(i + 2, c, arr[i] if i < len(arr) else None)
    wb.save(path)


def write_all_reports(state: dict[str, Any], results_dir: Path) -> dict[str, Path]:
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    run_id = state.get("run_id") or "run"
    html_path = results_dir / f"{run_id}.html"
    csv_path = results_dir / f"{run_id}.csv"
    xlsx_path = results_dir / f"{run_id}.xlsx"
    html_path.write_text(render_html(state), encoding="utf-8")
    write_csv(state, csv_path)
    try:
        write_xlsx(state, xlsx_path)
    except Exception:
        xlsx_path = None  # type: ignore
    out = {"html": html_path, "csv": csv_path, "json": results_dir / f"{run_id}.json"}
    if xlsx_path:
        out["xlsx"] = xlsx_path
    return out
