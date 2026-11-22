# Changelog

All notable changes to Botlabs Bench are documented here.

## 1.2.1

Small bug-fix release. Tests and scores work the same as 1.2.0.

- Reports now show the right version everywhere: the HTML report title and footer, and the internet speed test, all use 1.2.1 (they used to say 1.0 or 1.1). CSV and Excel exports now include `bench_version`, like the JSON file.
- Custom runs only check free disk space when the Disk test is selected, so a nearly full drive no longer blocks a CPU or memory run.
- A zero or negative `--duration-sec` is now rejected with a clear error instead of being quietly ignored.
- Stopping the memory test early no longer makes memory latency look better than it really was.
- On Windows, the default disk test drive is now the drive Windows is installed on (or the first drive found) instead of always C:.
- Small reliability fixes: the disk test picks its temp folder more carefully, a broken sensor reading can no longer break the HTML report, a failed Excel export is logged, Compare explains a broken or missing results file, and the app collects system info once per run instead of twice.
- The Windows exe now shows its version in file Properties, and the AppImage carries version info.
- Added automated tests (`tests/`, run with `pytest`).

## 1.2.0

- Professional dark GUI redesign with a cleaner layout and consistent theme.
- App opens maximized; Windows build requests administrator rights on launch.
- Results summary panel stays visible beside the live graph; graph has a separate GPU FPS axis.
- Hidden `--render-snapshots` and `--site-shots` modes for UI verification and website images.
- Support contact `support@botlabsusa.com` in README and CLI help.

## 1.1.1

- Hidden `--gui-selftest` automation hook (optional `--selftest-stop-after`, `--selftest-tests`, `--selftest-timeout`) opens the GUI, starts a short Custom run, presses Stop, and verifies graphs and partial reports.
- Silence the harmless matplotlib warning for non-scalable emoji/bitmap fonts (e.g. NotoColorEmoji on Linux).

## 1.1.0

- Disk test falls back to a writable folder (e.g. under the user home) when the chosen drive/mount root is not writable.
- Linux internet speed test certificate fix: prefer the system CA bundle, with certifi as a bundled fallback.
- AppImage build excludes host fontconfig / pango / glib / GL (and related) libraries so the desktop UI no longer hits FcConfigSetDefaultSubstitute / decoration / fc-list errors on Arch-based systems.
- Reports include `bench_version`; Compare view warns when compared runs have mismatched versions.

## 1.0.0

- Initial release: portable Windows `.exe` and Linux AppImage with CPU, memory, disk, network, and built-in OpenGL GPU tests; Quick / Detailed / Custom modes; live progress and graphs; Stop keeps partial results; HTML / CSV / XLSX / JSON reports; headless CLI.
