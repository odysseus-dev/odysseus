#!/usr/bin/env bash
# packaging/macos/build.sh — builds a portable, self-contained Odysseus .app + DMG.
#
# Platform: Apple Silicon (arm64) only — by design.
#
# Run from anywhere:
#   cd packaging/macos && ./build.sh
#   # or
#   packaging/macos/build.sh
#
# Produces:
#   packaging/macos/dist/Odysseus.app                    — self-contained .app
#   packaging/macos/dist/Odysseus-1.0.1-macOS-arm64.dmg  — releasable DMG
#
# Unlike build-macos-app.sh (which creates a path-baked launcher that requires
# a local venv), this produces a truly portable bundle: Python, all pip deps,
# and SearXNG are frozen inside the .app — end users install nothing.
#
# Prerequisites:
#   python3.11    (brew install python@3.11)
#   Xcode CLT     (xcode-select --install)
#   create-dmg    (brew install create-dmg)
#
# Optional — for signed, notarizable builds:
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./build.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"   # repo root (two levels up from packaging/macos/)

BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist"
STAGING_DIR="$BUILD_DIR/staging"
APP="$DIST_DIR/Odysseus.app"
RES="$APP/Contents/Resources"

APP_VERSION="1.0.1"
APP_NAME="Odysseus"
APP_ICON_BASENAME="odysseus"
INNER_GUI_BUNDLE_NAME="Odysseus.app"
ODYSSEUS_PORT="7860"
SEARXNG_PORT="8080"

# python-build-standalone — arm64 macOS, Python 3.11 (pinned)
PBS_VERSION="20250708"
PBS_PYTHON_VERSION="3.11.13"
PBS_FILENAME="cpython-${PBS_PYTHON_VERSION}+${PBS_VERSION}-aarch64-apple-darwin-install_only.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_VERSION}/${PBS_FILENAME}"
PBS_SHA256="baec549f2f9367993731d15f9bbed81394c381f8d66bacdee7d448e3a8adaa3b"

# SearXNG — pinned commit for reproducible builds
SEARXNG_REPO="https://github.com/searxng/searxng.git"
SEARXNG_COMMIT="86903a2c666da974462264060fdd80d1f09dd2ee"

# Pinned tool versions
PYINSTALLER_VERSION="6.14.1"
PYWEBVIEW_VERSION="6.2.1"

# ─── Helpers ──────────────────────────────────────────────────────────────────
log()  { echo "▸ $*"; }
ok()   { echo "  ✓ $*"; }
die()  { echo "✗ $*" >&2; exit 1; }
warn() { echo "  ⚠ $*"; }

require() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not found. $2"
}

# ─── Pre-flight checks ────────────────────────────────────────────────────────
log "Checking build prerequisites..."
require python3.11   "brew install python@3.11"
require git          "xcode-select --install"
require curl         "brew install curl"
require hdiutil      "xcode-select --install"
require sips         "xcode-select --install"
require iconutil     "xcode-select --install"
require create-dmg   "brew install create-dmg"

[[ -f "$REPO_DIR/app.py" ]] || die "$REPO_DIR missing app.py — run from inside the odysseus repo"
[[ -f "$SCRIPT_DIR/launcher.sh" ]]   || die "launcher.sh not found"
[[ -f "$SCRIPT_DIR/bootstrap.py" ]]  || die "bootstrap.py not found"
[[ -f "$SCRIPT_DIR/run.py" ]]        || die "run.py not found"
[[ -f "$SCRIPT_DIR/odysseus.spec" ]] || die "odysseus.spec not found"
ok "Prerequisites satisfied"

mkdir -p "$BUILD_DIR" "$DIST_DIR" "$STAGING_DIR"

# ─── Step 1: Build venv for Odysseus + PyInstaller ────────────────────────────
log "Step 1/7 — Setting up Odysseus build venv..."
ODYSSEUS_VENV="$BUILD_DIR/odysseus_venv"
if [[ ! -d "$ODYSSEUS_VENV" ]]; then
  python3.11 -m venv "$ODYSSEUS_VENV"
fi
"$ODYSSEUS_VENV/bin/pip" install --quiet --upgrade pip
"$ODYSSEUS_VENV/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"
"$ODYSSEUS_VENV/bin/pip" install --quiet \
  "pyinstaller==${PYINSTALLER_VERSION}" \
  "pywebview==${PYWEBVIEW_VERSION}"
ok "Odysseus venv ready"

# ─── Step 2: Download python-build-standalone (arm64) ─────────────────────────
log "Step 2/7 — Fetching python-build-standalone (arm64)..."
PBS_DIR="$BUILD_DIR/python-standalone"
PBS_TARBALL="$BUILD_DIR/$PBS_FILENAME"

if [[ ! -d "$PBS_DIR/bin" ]]; then
  if [[ ! -f "$PBS_TARBALL" ]]; then
    log "  Downloading $PBS_FILENAME (~60 MB)..."
    curl -L --progress-bar -o "$PBS_TARBALL" "$PBS_URL"
  fi
  if ! echo "$PBS_SHA256  $PBS_TARBALL" | shasum -a 256 -c - >/dev/null 2>&1; then
    rm -f "$PBS_TARBALL"
    die "Checksum mismatch for $PBS_FILENAME — download corrupted or tampered. Re-run to retry."
  fi
  ok "Checksum verified"
  mkdir -p "$PBS_DIR"
  tar -xzf "$PBS_TARBALL" -C "$PBS_DIR" --strip-components=1
fi
PBS_PYTHON="$PBS_DIR/bin/python3.11"
[[ -x "$PBS_PYTHON" ]] || die "python-build-standalone extraction failed"
ok "Standalone Python ready: $("$PBS_PYTHON" --version)"

# ─── Step 3: SearXNG runtime ──────────────────────────────────────────────────
log "Step 3/7 — Setting up SearXNG runtime..."
SEARXNG_SRC="$BUILD_DIR/searxng_src"
SEARXNG_VENV="$BUILD_DIR/searxng_venv"

if [[ ! -d "$SEARXNG_SRC/.git" ]]; then
  git init -q "$SEARXNG_SRC"
  git -C "$SEARXNG_SRC" remote add origin "$SEARXNG_REPO"
fi
if [[ "$(git -C "$SEARXNG_SRC" rev-parse HEAD 2>/dev/null)" != "$SEARXNG_COMMIT" ]]; then
  git -C "$SEARXNG_SRC" fetch -q --depth=1 origin "$SEARXNG_COMMIT" \
    || die "Could not fetch SearXNG commit $SEARXNG_COMMIT"
  git -C "$SEARXNG_SRC" checkout -q "$SEARXNG_COMMIT"
fi
ok "SearXNG at $(git -C "$SEARXNG_SRC" rev-parse --short HEAD)"

if [[ ! -d "$SEARXNG_VENV" ]]; then
  "$PBS_PYTHON" -m venv "$SEARXNG_VENV"
fi
"$SEARXNG_VENV/bin/pip" install --quiet --upgrade pip setuptools wheel
"$SEARXNG_VENV/bin/pip" install --quiet msgspec
if [[ -f "$SEARXNG_SRC/requirements.txt" ]]; then
  "$SEARXNG_VENV/bin/pip" install --quiet -r "$SEARXNG_SRC/requirements.txt"
fi
"$SEARXNG_VENV/bin/pip" install --quiet --no-build-isolation "$SEARXNG_SRC"
ok "SearXNG venv ready"

# ─── Step 4: Run PyInstaller ──────────────────────────────────────────────────
log "Step 4/7 — Running PyInstaller..."

# Stage run.py and symlink repo files for PyInstaller discovery
cp "$SCRIPT_DIR/run.py" "$STAGING_DIR/run.py"
for f in "$REPO_DIR"/*; do
  fname="$(basename "$f")"
  [[ "$fname" == "run.py" ]] && continue
  [[ ! -e "$STAGING_DIR/$fname" ]] && ln -s "$f" "$STAGING_DIR/$fname"
done

cd "$STAGING_DIR"
"$ODYSSEUS_VENV/bin/pyinstaller" \
  "$SCRIPT_DIR/odysseus.spec" \
  --distpath "$BUILD_DIR/pyinstaller_dist" \
  --workpath "$BUILD_DIR/pyinstaller_work" \
  --noconfirm
FROZEN_APP="$BUILD_DIR/pyinstaller_dist/odysseus_app"
[[ -d "$FROZEN_APP" ]] || die "PyInstaller did not produce odysseus_app"
ok "PyInstaller frozen app at $FROZEN_APP"

# ─── Step 4a: Generate icon ────────────────────────────────────────────────────
log "Step 4a/7 — Generating app icon..."
ICON_SRC=""
[[ -f "$SCRIPT_DIR/assets/icon.png" ]]                         && ICON_SRC="$SCRIPT_DIR/assets/icon.png"
[[ -z "$ICON_SRC" && -f "$REPO_DIR/docs/odysseus_icon.png" ]] && ICON_SRC="$REPO_DIR/docs/odysseus_icon.png"
[[ -z "$ICON_SRC" && -f "$REPO_DIR/docs/odysseus.jpg" ]]      && ICON_SRC="$REPO_DIR/docs/odysseus.jpg"
[[ -n "$ICON_SRC" ]] || die "No icon source found — add packaging/macos/assets/icon.png (1024x1024 PNG)"

TMPICON="$(mktemp -d)"
ICONSET="$TMPICON/odysseus.iconset"
mkdir -p "$ICONSET"
sips -s format png "$ICON_SRC" --out "$TMPICON/base.png" >/dev/null 2>&1 || cp "$ICON_SRC" "$TMPICON/base.png"
for size in 16 32 64 128 256 512; do
  sips -z $size $size "$TMPICON/base.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1
  double=$((size * 2))
  sips -z $double $double "$TMPICON/base.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null 2>&1
done
iconutil -c icns "$ICONSET" -o "$BUILD_DIR/odysseus.icns" >/dev/null 2>&1 || die "iconutil failed"
rm -rf "$TMPICON"
[[ -f "$BUILD_DIR/odysseus.icns" ]] || die "Icon build failed"
ok "Icon ready at $BUILD_DIR/odysseus.icns"

# ─── Step 4b: Build GUI PyInstaller BUNDLE ────────────────────────────────────
log "Step 4b/7 — Building GUI .app bundle..."
[[ -f "$SCRIPT_DIR/odysseus_gui.spec" ]] || die "odysseus_gui.spec not found"

export ODYSSEUS_APP_NAME="$APP_NAME"
export ODYSSEUS_APP_VERSION="$APP_VERSION"

"$ODYSSEUS_VENV/bin/pyinstaller" \
  "$SCRIPT_DIR/odysseus_gui.spec" \
  --distpath "$BUILD_DIR/pyinstaller_gui_dist" \
  --workpath "$BUILD_DIR/pyinstaller_gui_work" \
  --noconfirm

GUI_BUNDLE="$BUILD_DIR/pyinstaller_gui_dist/${INNER_GUI_BUNDLE_NAME}"
[[ -d "$GUI_BUNDLE" ]] || die "PyInstaller did not produce Odysseus.app"

GUI_BIN="$GUI_BUNDLE/Contents/MacOS/Odysseus"
[[ -f "$GUI_BIN" ]] || GUI_BIN="$GUI_BUNDLE/Contents/MacOS/OdysseusGUI"
[[ -f "$GUI_BIN" ]] || die "GUI binary not found in $GUI_BUNDLE/Contents/MacOS/"
file "$GUI_BIN" | grep -q "Mach-O" || die "GUI binary is not a Mach-O"

cp "$BUILD_DIR/odysseus.icns" "$GUI_BUNDLE/Contents/Resources/odysseus.icns" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile odysseus" "$GUI_BUNDLE/Contents/Info.plist" 2>/dev/null || true
ok "GUI bundle ready"

# ─── Step 5: Assemble Odysseus.app ────────────────────────────────────────────
log "Step 5/7 — Assembling Odysseus.app..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                  <string>${APP_NAME}</string>
  <key>CFBundleDisplayName</key>           <string>${APP_NAME}</string>
  <key>CFBundleIdentifier</key>            <string>com.odysseus.app</string>
  <key>CFBundleVersion</key>               <string>${APP_VERSION}</string>
  <key>CFBundleShortVersionString</key>    <string>${APP_VERSION}</string>
  <key>CFBundlePackageType</key>           <string>APPL</string>
  <key>CFBundleExecutable</key>            <string>Odysseus</string>
  <key>CFBundleIconFile</key>              <string>${APP_ICON_BASENAME}</string>
  <key>LSMinimumSystemVersion</key>        <string>12.0</string>
  <key>NSHighResolutionCapable</key>       <true/>
  <key>LSMultipleInstancesProhibited</key> <true/>
  <key>LSApplicationCategoryType</key>     <string>public.app-category.productivity</string>
  <key>LSUIElement</key>                   <false/>
</dict>
</plist>
PLIST

# Copy icon to app bundle
cp "$BUILD_DIR/odysseus.icns" "$RES/${APP_ICON_BASENAME}.icns"

# Bake ports into launcher
sed \
  -e "s|__ODYSSEUS_PORT__|${ODYSSEUS_PORT}|g" \
  -e "s|__SEARXNG_PORT__|${SEARXNG_PORT}|g" \
  -e "s|__APP_NAME__|${APP_NAME}|g" \
  "$SCRIPT_DIR/launcher.sh" > "$APP/Contents/MacOS/Odysseus"
chmod +x "$APP/Contents/MacOS/Odysseus"

cp -R "$FROZEN_APP"   "$RES/odysseus_app"
cp -R "$GUI_BUNDLE"   "$RES/${INNER_GUI_BUNDLE_NAME}"

mkdir -p "$RES/searxng_runtime"
cp -R "$PBS_DIR"      "$RES/searxng_runtime/python"
cp -R "$SEARXNG_VENV" "$RES/searxng_runtime/venv"
cp -R "$SEARXNG_SRC"  "$RES/searxng_runtime/searxng_src"

mkdir -p "$RES/config/searxng"
cp "$REPO_DIR/config/searxng/settings.yml" "$RES/config/searxng/settings.yml"

cp "$REPO_DIR/.env.example"   "$RES/.env.example"
cp "$SCRIPT_DIR/bootstrap.py" "$RES/bootstrap.py"
ok "Odysseus.app assembled"

/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f -R -trusted "$APP" 2>/dev/null || true

# ─── Step 6: Bundle sanity checks ─────────────────────────────────────────────
log "Step 6/7 — Sanity checks..."
[[ -x "$APP/Contents/MacOS/Odysseus" ]]              || die "Launcher not executable"
[[ -f "$RES/odysseus_app/odysseus_app" ]]             || die "Frozen binary missing"
[[ -x "$RES/searxng_runtime/python/bin/python3.11" ]] || die "Standalone Python missing"
[[ -f "$RES/config/searxng/settings.yml" ]]           || die "SearXNG config missing"
[[ -f "$RES/bootstrap.py" ]]                          || die "bootstrap.py missing"
GUI_EXE="$RES/${INNER_GUI_BUNDLE_NAME}/Contents/MacOS/Odysseus"
[[ -x "$GUI_EXE" ]] || die "Inner GUI binary missing at $GUI_EXE"
file "$GUI_EXE" | grep -q "Mach-O" || die "Inner GUI is not Mach-O"
grep -q "_is_pyinstaller_child" "$RES/odysseus_app/_internal/run.py" \
  || warn "run.py in bundle missing multiprocessing child guard"
ok "All sanity checks passed"

# ─── Step 6a: Code signing (optional) ─────────────────────────────────────────
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  log "Step 6a/7 — Code signing as '$CODESIGN_IDENTITY'..."
  ENTITLEMENTS="$SCRIPT_DIR/assets/entitlements.plist"
  [[ -f "$ENTITLEMENTS" ]] || die "assets/entitlements.plist missing"

  find "$RES" -type f \( -name "*.dylib" -o -name "*.so" -o -perm -111 \) -print0 |
    while IFS= read -r -d '' f; do
      file -b "$f" | grep -q "Mach-O" || continue
      codesign --force --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS" --sign "$CODESIGN_IDENTITY" "$f" \
        || die "codesign failed: $f"
    done

  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$CODESIGN_IDENTITY" \
    "$RES/${INNER_GUI_BUNDLE_NAME}" || die "codesign failed on GUI bundle"
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$CODESIGN_IDENTITY" \
    "$APP" || die "codesign failed on Odysseus.app"

  codesign --verify --deep --strict "$APP" || die "Signature verification failed"
  ok "Code signing complete"
else
  warn "CODESIGN_IDENTITY not set — app will be unsigned (right-click → Open required)"
fi

# ─── Step 7: Package DMG ──────────────────────────────────────────────────────
log "Step 7/7 — Packaging DMG..."
DMG_NAME="Odysseus-${APP_VERSION}-macOS-arm64"
DMG_PATH="$DIST_DIR/${DMG_NAME}.dmg"
rm -f "$DMG_PATH"

create-dmg \
  --volname "Odysseus" \
  --volicon "$RES/${APP_ICON_BASENAME}.icns" \
  --window-pos 200 120 \
  --window-size 660 400 \
  --icon-size 128 \
  --icon "Odysseus.app" 180 170 \
  --hide-extension "Odysseus.app" \
  --app-drop-link 480 170 \
  "$DMG_PATH" \
  "$APP"

if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG_PATH" \
    || die "codesign failed on DMG"
  ok "DMG signed — notarize before distributing"
fi

ok "DMG ready: $DMG_PATH"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Build complete!  v${APP_VERSION}"
echo ""
echo "  App:  $APP"
echo "  DMG:  $DMG_PATH"
echo ""
echo "  Test: open '$APP'"
echo "        First launch: right-click → Open (Gatekeeper)"
echo "═══════════════════════════════════════════════════"
