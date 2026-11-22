"""Write version_info.txt (Windows exe file properties) from src/version.py.

Used by build_exe.ps1 / build_exe_only.ps1 via PyInstaller --version-file so the
exe's Properties > Details tab always matches APP_VERSION.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def app_version() -> str:
    text = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("APP_VERSION not found in src/version.py")
    return m.group(1)


def main() -> int:
    ver = app_version()
    nums = [int(x) for x in re.findall(r"\d+", ver)[:4]]
    nums += [0] * (4 - len(nums))
    tup = ", ".join(str(n) for n in nums)
    dotted = ".".join(str(n) for n in nums)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "version_info.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Botlabs USA'),
        StringStruct('FileDescription', 'Botlabs Bench'),
        StringStruct('FileVersion', '{dotted}'),
        StringStruct('InternalName', 'BotlabsBench'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 Botlabs USA. MIT License.'),
        StringStruct('OriginalFilename', 'BotlabsBench.exe'),
        StringStruct('ProductName', 'Botlabs Bench'),
        StringStruct('ProductVersion', '{ver}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
