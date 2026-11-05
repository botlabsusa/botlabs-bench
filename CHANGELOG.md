# Changelog

All notable changes to Botlabs Bench are documented here.

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
