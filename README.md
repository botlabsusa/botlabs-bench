# Botlabs Bench

[![Support on Patreon](https://img.shields.io/badge/Patreon-Support%20Botlabs-F96854?logo=patreon&logoColor=white)](https://www.patreon.com/botlabs)

Botlabs Bench is a portable, cross-platform PC hardware benchmark for Windows and Linux. It measures CPU, memory, disk, network, and GPU performance in one app, with live progress graphs and offline HTML, CSV, XLSX, and JSON reports. No installer is required: download a single executable (Windows) or AppImage (Linux) and run it.

Use it to baseline a new machine, compare upgrades, or spot thermal throttling and storage bottlenecks. Results stay on your computer in a `BenchResults` folder next to the app. A built-in Compare view places multiple runs side by side and warns when benchmark versions differ so scores are not mixed incorrectly.

## Features

- **CPU** — single-core and all-core scores, plus sustained load to estimate throttling
- **Memory** — bandwidth, latency, and pattern error check
- **Disk** — sequential and 4K random read/write (falls back to a writable folder when the chosen root is not writable)
- **GPU** — built-in OpenGL test via moderngl (Intel / AMD / NVIDIA); no third-party 3D suite required
- **Network** — Cloudflare download speed test and optional LAN/NAS copy throughput
- **Modes** — Quick (~10 minutes), Detailed (~1 hour), or Custom (pick tests and duration, or a single pass)
- **Live UI** — dark professional theme, maximized window, progress, live graphs (separate GPU FPS axis), Results summary panel, and **Stop / Save partial** so cancelled runs still keep partial reports
- **Reports** — HTML, CSV, XLSX, and JSON written to `BenchResults` next to the app
- **Compare** — side-by-side view with a warning when `bench_version` values differ
- **Headless CLI** — run without a GUI for automation or remote machines

## Platforms

| Platform | Package | Notes |
|----------|---------|--------|
| Windows 10 / 11 (64-bit) | Portable `.exe` | No install |
| Linux x86_64 | AppImage | glibc 2.38+ recommended |

## Download

Get the latest binaries from [GitHub Releases](https://github.com/botlabsusa/botlabs-bench/releases).

## Install / run

### Windows

1. Download `BotlabsBench.exe` from Releases (v1.2.0+ asks for administrator rights on launch).
2. Double-click to open the GUI; the window opens maximized.
3. Windows SmartScreen may warn because the binary is not code-signed; choose *More info* → *Run anyway* if you trust the release.
4. Approve the User Account Control prompt so temperature sensors and full disk tests can work when available.

### Linux

```bash
chmod +x BotlabsBench-x86_64.AppImage
./BotlabsBench-x86_64.AppImage
```

### Headless (Windows or Linux)

```text
BotlabsBench --headless --mode custom --tests cpu,memory,disk,network,gpu --single-pass
```

Useful options:

- `--mode quick|detailed|custom`
- `--tests cpu,memory,disk,network,gpu` (comma-separated subset)
- `--single-pass` — one pass of each selected test
- `--duration-sec N` — custom timed run (seconds)
- `--disk-drive PATH` — drive/mount root for the disk test (e.g. `C:\` or `/home`)
- `--lan-drive PATH` — mapped drive / NAS mount for the LAN copy test
- `--results-dir PATH` — override results directory

Results land in `BenchResults` next to the executable (or under `~/BotlabsBench` if that location is not writable).

### Development / testing

A hidden `--gui-selftest` flag drives a short Custom GUI run, presses Stop, and checks that partial reports were saved. It is intended for automation, not end users.

## Build from source

Requires Python 3.11+ and a working OpenGL stack for the GPU test.

```bash
git clone https://github.com/botlabsusa/botlabs-bench.git
cd botlabs-bench
python -m venv .venv   # or `python -m venv venv` on Windows
```

### Windows

```powershell
.\venv\Scripts\Activate.ps1   # after: python -m venv venv
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
# Output: dist\BotlabsBench.exe
```

`build_exe.ps1` creates `venv` if needed, installs dependencies, and runs PyInstaller (onefile).

### Linux

```bash
source .venv/bin/activate
pip install -r requirements.txt
./build_appimage.sh
# Output: dist-linux/BotlabsBench-x86_64.AppImage
```

`build_appimage.sh` expects an activated project virtualenv with dependencies installed. It builds a PyInstaller onedir bundle, strips host graphics/font/pango/glib libraries that must come from the system, and packs an AppImage (downloads `appimagetool` into `tools/` if missing).

### Run from source (dev)

```bash
# from repo root, with venv active
PYTHONPATH=src python src/main.py --headless --mode custom --tests cpu,memory --single-pass
```

## Known limitations

- **Windows temperatures** are often unavailable without admin rights or a sensor service; scores still work.
- **Short Custom timed runs** can finish the disk and network tests early relative to the overall duration; CPU/GPU sustained work may use more of the remaining time.
- The GPU test needs a usable OpenGL context (may be unavailable over RDP or with basic display drivers).

## License

MIT License. Copyright (c) 2026 Botlabs USA. See [LICENSE](LICENSE).

## Help & contact

Support: support@botlabsusa.com

## Support Botlabs

Botlabs apps are free. If they help you, consider supporting development on [Patreon](https://www.patreon.com/botlabs) for early builds, feature votes and your name in the app credits.
