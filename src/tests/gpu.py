"""Built-in OpenGL GPU 3D benchmark (Intel / AMD / NVIDIA).

Uses moderngl + glfw to render a shader-heavy animated scene at increasing
load levels. Works in the frozen onefile exe when a usable OpenGL context
exists. Falls back with a clear message on RDP / basic display drivers.
"""
from __future__ import annotations

import logging
import math
import statistics
import subprocess
import sys
import sys
import time
from typing import Any, Callable, Optional

ProgressCb = Callable[[str, float, Optional[dict]], None]
CancelCb = Callable[[], bool]

log = logging.getLogger("botlabsbench.gpu")

# Fullscreen-triangle raymarch / plasma fragment shader (GPU-bound).
_VERT = """
#version 330
in vec2 in_vert;
void main() {
    gl_Position = vec4(in_vert, 0.0, 1.0);
}
"""

_FRAG = """
#version 330
uniform float u_time;
uniform vec2 u_res;
uniform int u_quality;  // 1..8 iteration multiplier
out vec4 f_color;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    float a = hash(i);
    float b = hash(i + vec2(1.0, 0.0));
    float c = hash(i + vec2(0.0, 1.0));
    float d = hash(i + vec2(1.0, 1.0));
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
}

float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 8; i++) {
        if (i >= u_quality) break;
        v += a * noise(p);
        p *= 2.07;
        a *= 0.5;
    }
    return v;
}

// Cheap sphere raymarch for extra ALU
float map(vec3 p) {
    float d = length(p) - 1.0;
    d += 0.15 * sin(p.x * 6.0 + u_time) * sin(p.y * 5.0) * sin(p.z * 4.0 + u_time * 0.7);
    return d;
}

vec3 raymarch(vec3 ro, vec3 rd) {
    float t = 0.0;
    for (int i = 0; i < 48; i++) {
        if (i >= 8 + u_quality * 5) break;
        vec3 p = ro + rd * t;
        float d = map(p);
        if (d < 0.002) {
            vec3 n = normalize(vec3(
                map(p + vec3(0.01,0,0)) - map(p - vec3(0.01,0,0)),
                map(p + vec3(0,0.01,0)) - map(p - vec3(0,0.01,0)),
                map(p + vec3(0,0,0.01)) - map(p - vec3(0,0,0.01))
            ));
            float diff = clamp(dot(n, normalize(vec3(0.4, 0.8, 0.3))), 0.0, 1.0);
            vec3 col = mix(vec3(0.1, 0.2, 0.55), vec3(0.95, 0.55, 0.15), diff);
            col += 0.25 * fbm(p.xy * 3.0 + u_time);
            return col;
        }
        t += d;
        if (t > 12.0) break;
    }
    return vec3(0.02, 0.03, 0.08) + 0.35 * fbm(rd.xy * 4.0 + u_time * 0.2);
}

void main() {
    vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;
    float ang = u_time * 0.35;
    mat2 rot = mat2(cos(ang), -sin(ang), sin(ang), cos(ang));
    uv = rot * uv;
    vec3 ro = vec3(0.0, 0.0, 3.2);
    vec3 rd = normalize(vec3(uv, -1.4));
    vec3 col = raymarch(ro, rd);
    // Extra fullscreen fbm pass for fill-rate pressure
    col += 0.15 * vec3(fbm(uv * 8.0 + u_time), fbm(uv * 9.0 - u_time), fbm(uv * 7.0));
    f_color = vec4(col, 1.0);
}
"""


def _ps(cmd: str) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""



def _linux_gpus() -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            for line in (r.stdout or "").splitlines():
                parts = [x.strip() for x in line.split(",")]
                if not parts or not parts[0]:
                    continue
                vram = None
                try:
                    if len(parts) > 1 and parts[1]:
                        vram = float(parts[1])
                except Exception:
                    pass
                gpus.append(
                    {
                        "name": parts[0],
                        "vram_mb_wmi": vram,
                        "driver_version": parts[2] if len(parts) > 2 else None,
                        "status": "OK",
                    }
                )
    except FileNotFoundError:
        pass
    except Exception as e:
        log.debug("nvidia-smi GPU query failed: %s", e)
    return gpus


def _dxgi_wmi_gpus() -> list[dict[str, Any]]:
    """GPU name + AdapterRAM via WMI (useful alongside GL renderer)."""
    raw = _ps(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM, DriverVersion, Status | ConvertTo-Json -Compress"
    )
    gpus: list[dict[str, Any]] = []
    if not raw:
        return gpus
    try:
        import json

        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        for item in data or []:
            ram = item.get("AdapterRAM")
            vram_mb = None
            try:
                if ram is not None and int(ram) > 0:
                    vram_mb = round(int(ram) / (1024 * 1024), 1)
            except Exception:
                pass
            gpus.append(
                {
                    "name": item.get("Name") or "Unknown",
                    "vram_mb_wmi": vram_mb,
                    "driver_version": item.get("DriverVersion"),
                    "status": item.get("Status"),
                }
            )
    except Exception as e:
        log.debug("WMI GPU parse failed: %s", e)
    return gpus


def _percentile_low(sorted_vals: list[float], pct: float) -> float:
    """pct=1 -> approx 1% low (slowest 1% frames -> lowest FPS)."""
    if not sorted_vals:
        return 0.0
    # frame times high = slow; we pass FPS list ascending
    n = len(sorted_vals)
    idx = max(0, min(n - 1, int(math.floor(n * (pct / 100.0)))))
    return sorted_vals[idx]


def _is_software_renderer(renderer: str, vendor: str) -> bool:
    blob = f"{renderer} {vendor}".lower()
    keys = (
        "gdi generic",
        "microsoft basic",
        "llvmpipe",
        "swiftshader",
        "software rasterizer",
        "mesa offscreen",
        "rdp",
        "remote desktop",
    )
    return any(k in blob for k in keys)


def _run_opengl_bench(
    duration_sec: float,
    single_pass: bool,
    progress: ProgressCb,
    cancel: CancelCb,
) -> dict[str, Any]:
    import glfw
    import moderngl
    import numpy as np

    results: dict[str, Any] = {
        "name": "gpu",
        "status": "ok",
        "mode": "opengl_moderngl",
        "api": "OpenGL",
    }
    wmi = _dxgi_wmi_gpus() if sys.platform.startswith("win") else _linux_gpus()
    if wmi:
        results["adapters_wmi"] = wmi

    if not glfw.init():
        raise RuntimeError("glfw.init() failed - no windowing system available.")

    window = None
    ctx = None
    try:
        glfw.window_hint(glfw.VISIBLE, glfw.TRUE)
        glfw.window_hint(glfw.RESIZABLE, glfw.FALSE)
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)
        # Prefer a real GPU; on failure caller handles fallback message
        width, height = (1280, 720) if single_pass else (1600, 900)
        window = glfw.create_window(width, height, "Botlabs Bench - GPU OpenGL", None, None)
        if not window:
            raise RuntimeError(
                "Could not create an OpenGL 3.3 window. "
                "This often happens over RDP or with a basic display driver."
            )
        glfw.make_context_current(window)
        glfw.swap_interval(0)  # uncapped for measurement

        ctx = moderngl.create_context()
        vendor = ctx.info.get("GL_VENDOR", "")
        renderer = ctx.info.get("GL_RENDERER", "")
        version = ctx.info.get("GL_VERSION", "")
        results["gl_vendor"] = vendor
        results["gl_renderer"] = renderer
        results["gl_version"] = version
        results["gpu_name"] = renderer or (wmi[0]["name"] if wmi else "Unknown GPU")
        results["software_renderer"] = _is_software_renderer(renderer, vendor)
        if results["software_renderer"]:
            results["note"] = (
                "OpenGL context is a software / basic renderer "
                f"('{renderer}'). Scores reflect CPU fallback, not a discrete GPU. "
                "Local console login usually enables the real Intel/AMD/NVIDIA driver."
            )

        prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        # Fullscreen triangle
        vertices = np.array([-1.0, -1.0, 3.0, -1.0, -1.0, 3.0], dtype="f4")
        vbo = ctx.buffer(vertices.tobytes())
        vao = ctx.simple_vertex_array(prog, vbo, "in_vert")

        # Load levels: quality 2,4,6,8 (iterations)
        if single_pass:
            levels = [(2, 2.0), (4, 2.0)]  # quality, seconds
        else:
            budget = max(8.0, float(duration_sec))
            slice_t = budget / 4.0
            levels = [(2, slice_t), (4, slice_t), (6, slice_t), (8, slice_t)]

        all_fps: list[float] = []
        level_stats: list[dict[str, Any]] = []
        t_origin = time.perf_counter()
        total_levels = len(levels)

        for li, (quality, secs) in enumerate(levels):
            if cancel():
                results["status"] = "cancelled"
                break
            prog["u_quality"].value = int(quality)
            frame_times: list[float] = []
            frames = 0
            level_start = time.perf_counter()
            last_t = level_start
            while (time.perf_counter() - level_start) < secs:
                if cancel() or glfw.window_should_close(window):
                    results["status"] = "cancelled"
                    break
                now = time.perf_counter()
                # Resize slightly mid-run on later levels for more fill-rate work
                fw, fh = width, height
                if quality >= 6:
                    fw, fh = width + 160, height + 90
                glfw.set_window_size(window, fw, fh)
                ctx.viewport = (0, 0, fw, fh)
                prog["u_time"].value = float(now - t_origin)
                prog["u_res"].value = (float(fw), float(fh))
                ctx.clear(0.0, 0.0, 0.0, 1.0)
                vao.render(mode=moderngl.TRIANGLES)
                glfw.swap_buffers(window)
                glfw.poll_events()
                after = time.perf_counter()
                dt = after - last_t
                last_t = after
                if dt > 0:
                    frame_times.append(dt)
                    frames += 1
                    fps_inst = 1.0 / dt
                    all_fps.append(fps_inst)
                # Progress ~ every 8 frames
                if frames % 8 == 0:
                    elapsed_level = after - level_start
                    frac_level = min(1.0, elapsed_level / max(secs, 1e-6))
                    overall = (li + frac_level) / total_levels
                    avg_so_far = (frames / max(after - level_start, 1e-9)) if frames else 0.0
                    progress(
                        f"GPU OpenGL Q{quality}",
                        0.05 + 0.9 * overall,
                        {
                            "fps": round(avg_so_far, 1),
                            "quality": quality,
                            "gpu": renderer,
                        },
                    )
            if not frame_times:
                continue
            fps_list = [1.0 / t for t in frame_times if t > 0]
            fps_list_sorted = sorted(fps_list)
            avg_fps = statistics.fmean(fps_list) if fps_list else 0.0
            # 1% low = FPS at 1st percentile (slowest frames)
            low1 = _percentile_low(fps_list_sorted, 1.0) if fps_list_sorted else 0.0
            level_stats.append(
                {
                    "quality": quality,
                    "seconds": round(time.perf_counter() - level_start, 2),
                    "frames": frames,
                    "fps_avg": round(avg_fps, 2),
                    "fps_1pct_low": round(low1, 2),
                    "frame_ms_avg": round((1000.0 / avg_fps) if avg_fps else 0.0, 3),
                }
            )

        results["levels"] = level_stats
        results["resolution"] = f"{width}x{height}"
        if level_stats:
            # Primary score: geometric-ish blend of avg and 1% low on hardest completed level
            hardest = level_stats[-1]
            avg = hardest["fps_avg"]
            low = hardest["fps_1pct_low"]
            results["fps"] = avg
            results["fps_avg"] = avg
            results["fps_1pct_low"] = low
            results["frame_ms_avg"] = hardest["frame_ms_avg"]
            # Score scaled so ~60 FPS @ Q8 ~ 6000-ish; keep simple
            results["gpu_score"] = int(round(avg * 50 + low * 30))
            overall_avg = statistics.fmean([s["fps_avg"] for s in level_stats])
            results["fps_avg_all_levels"] = round(overall_avg, 2)
        else:
            results["fps"] = 0.0
            results["gpu_score"] = 0
            results["status"] = "error"
            results["error"] = "No frames rendered."

        progress(
            "GPU OpenGL done",
            1.0,
            {"fps": results.get("fps"), "score": results.get("gpu_score"), "gpu": renderer},
        )
        return results
    finally:
        try:
            if window is not None:
                glfw.destroy_window(window)
        except Exception:
            pass
        try:
            glfw.terminate()
        except Exception:
            pass


def run_gpu_tests(
    duration_sec: float,
    single_pass: bool,
    progress: ProgressCb,
    cancel: CancelCb,
) -> dict[str, Any]:
    progress("GPU OpenGL prepare", 0.02, None)
    try:
        return _run_opengl_bench(duration_sec, single_pass, progress, cancel)
    except Exception as e:
        log.exception("OpenGL GPU test failed")
        wmi = _dxgi_wmi_gpus() if sys.platform.startswith("win") else _linux_gpus()
        msg = (
            f"OpenGL GPU test unavailable: {e}. "
            "This is common over Remote Desktop (RDP) or when only a basic "
            "display driver is active. Sign in on the local console with "
            "Intel/AMD/NVIDIA drivers for a real GPU score."
        )
        progress("GPU OpenGL unavailable", 1.0, {"error": str(e)})
        return {
            "name": "gpu",
            "status": "skipped",
            "mode": "opengl_unavailable",
            "error": str(e),
            "note": msg,
            "adapters_wmi": wmi,
            "fps": None,
            "gpu_score": None,
        }
