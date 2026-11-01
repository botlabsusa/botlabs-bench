#!/usr/bin/env bash
# Build BotlabsBench-x86_64.AppImage from the shared codebase.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
VENV="${ROOT}/.venv"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

DIST="${ROOT}/dist-linux"
BUILD="${ROOT}/build-linux"
APPDIR="${DIST}/BotlabsBench.AppDir"
APPIMAGE_OUT="${DIST}/BotlabsBench-x86_64.AppImage"
PYI_NAME="BotlabsBench"

rm -rf "$DIST" "$BUILD"
mkdir -p "$DIST" "$BUILD"

echo "==> PyInstaller onedir"
pyinstaller \
  --noconfirm \
  --clean \
  --onedir \
  --console \
  --name "$PYI_NAME" \
  --distpath "$DIST/pyi" \
  --workpath "$BUILD/pyi" \
  --specpath "$BUILD" \
  --paths "$ROOT/src" \
  --hidden-import=tkinter \
  --hidden-import=_tkinter \
  --hidden-import=PIL \
  --hidden-import=PIL._tkinter_finder \
  --hidden-import=matplotlib \
  --hidden-import=matplotlib.backends.backend_tkagg \
  --hidden-import=openpyxl \
  --hidden-import=moderngl \
  --hidden-import=glcontext \
  --hidden-import=glfw \
  --hidden-import=certifi \
  --collect-data certifi \
  --collect-all tkinter \
  --collect-all moderngl \
  --collect-all glcontext \
  --collect-all glfw \
  --collect-submodules matplotlib \
  --collect-data tcl \
  --collect-data tk \
  "$ROOT/src/main.py"

echo "==> Assemble AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$APPDIR/usr/share/applications"
cp -a "$DIST/pyi/$PYI_NAME" "$APPDIR/usr/bin/$PYI_NAME"
cp "$ROOT/assets/botlabsbench.png" "$APPDIR/botlabsbench.png"
cp "$ROOT/assets/botlabsbench.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/botlabsbench.png"

# Strip host graphics / display / font / pango libs that MUST come from the host.
# Bundling fontconfig/freetype/pango/harfbuzz/glib clashes with system pango on Arch
# (undefined symbol FcConfigSetDefaultSubstitute / fc-list errors).
echo "==> Strip host GL/X11/Wayland/NVIDIA/drm/fontconfig/pango/glib libs from bundle"
BUNDLE="$APPDIR/usr/bin/$PYI_NAME"
# Prefer deleting only top-level bundled copies; keep pillow.libs/* hashed deps.
find "$BUNDLE" -type f \( \
  -name 'libGL.so*' -o -name 'libEGL.so*' -o -name 'libGLX.so*' -o \
  -name 'libGLdispatch.so*' -o -name 'libOpenGL.so*' -o \
  -name 'libnvidia*' -o -name 'libdrm.so*' -o -name 'libdrm_*' -o \
  -name 'libX11.so*' -o -name 'libX11-xcb.so*' -o -name 'libxcb.so*' -o -name 'libxcb-*.so*' -o \
  -name 'libXext.so*' -o -name 'libXrender.so*' -o -name 'libXi.so*' -o \
  -name 'libXrandr.so*' -o -name 'libXcursor.so*' -o -name 'libXinerama.so*' -o \
  -name 'libXxf86vm.so*' -o -name 'libXfixes.so*' -o \
  -name 'libwayland*.so*' -o -name 'libxkbcommon*.so*' -o \
  -name 'libEGL_mesa.so*' -o -name 'libGLX_mesa.so*' -o \
  -name 'libglapi.so*' -o -name 'libgbm.so*' -o \
  -name 'libfontconfig.so*' -o -name 'libfreetype.so*' -o \
  -name 'libpango-*.so*' -o -name 'libpangocairo-*.so*' -o -name 'libpangoft2-*.so*' -o \
  -name 'libharfbuzz.so*' -o -name 'libharfbuzz-*.so*' -o \
  -name 'libglib-*.so*' -o -name 'libgobject-*.so*' -o -name 'libgio-*.so*' -o \
  -name 'libgmodule-*.so*' -o -name 'libgthread-*.so*' -o \
  -name 'libgraphite2.so*' -o -name 'libfribidi.so*' -o -name 'libthai.so*' -o \
  -name 'libdatrie.so*' \
  \) ! -path '*/pillow.libs/*' -print -delete || true

# Desktop entry
cat > "$APPDIR/botlabsbench.desktop" << 'DESK'
[Desktop Entry]
Type=Application
Name=Botlabs Bench
Comment=Portable hardware benchmark (CPU, Memory, Disk, Network, GPU)
Exec=BotlabsBench
Icon=botlabsbench
Categories=Utility;System;
Terminal=false
DESK
cp "$APPDIR/botlabsbench.desktop" "$APPDIR/usr/share/applications/botlabsbench.desktop"

# AppRun: prefer host libGL; put our libs first except for graphics
cat > "$APPDIR/AppRun" << 'APPRUN'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="$HERE"
BIN="$HERE/usr/bin/BotlabsBench/BotlabsBench"
INTERNAL="$HERE/usr/bin/BotlabsBench/_internal"
if [ -d "$INTERNAL" ]; then
  export LD_LIBRARY_PATH="$INTERNAL${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  if [ -d "$INTERNAL/tcl8.6" ]; then
    export TCL_LIBRARY="$INTERNAL/tcl8.6"
  elif [ -d "$INTERNAL/_tcl_data" ]; then
    export TCL_LIBRARY="$INTERNAL/_tcl_data"
  fi
  if [ -d "$INTERNAL/tk8.6" ]; then
    export TK_LIBRARY="$INTERNAL/tk8.6"
  elif [ -d "$INTERNAL/_tk_data" ]; then
    export TK_LIBRARY="$INTERNAL/_tk_data"
  fi
fi
exec "$BIN" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# Fetch appimagetool if needed
TOOL="${ROOT}/tools/appimagetool-x86_64.AppImage"
mkdir -p "$ROOT/tools"
if [ ! -x "$TOOL" ]; then
  echo "==> Download appimagetool"
  curl -fsSL -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
    || curl -fsSL -o "$TOOL" \
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$TOOL"
fi

echo "==> Pack AppImage"
export ARCH=x86_64
# Prefer extract-and-run for nested AppImage on systems without FUSE
if "$TOOL" --appimage-extract-and-run "$APPDIR" "$APPIMAGE_OUT" 2>/tmp/appimagetool.err; then
  :
elif APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$APPIMAGE_OUT" 2>>/tmp/appimagetool.err; then
  :
else
  # Fallback: extract tool and run
  mkdir -p "$BUILD/appimagetool"
  cd "$BUILD/appimagetool"
  "$TOOL" --appimage-extract >/dev/null 2>&1 || true
  if [ -x squashfs-root/AppRun ]; then
    ./squashfs-root/AppRun "$APPDIR" "$APPIMAGE_OUT"
  else
    cat /tmp/appimagetool.err || true
    exit 1
  fi
fi

chmod +x "$APPIMAGE_OUT"
ls -lh "$APPIMAGE_OUT"
echo "DONE $APPIMAGE_OUT"
