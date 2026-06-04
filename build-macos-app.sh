#!/bin/bash
# Build a downloadable macOS launcher app + .dmg for Odysseus.
#
#   ./build-macos-app.sh
#
# Produces:
#   dist/Odysseus.app   — double-click: menu bar item, drives the local
#                         server (using this repo's venv) in the background.
#                         Quit from the menu bar item (Cmd-Q) SIGTERMs the
#                         worker cleanly.
#   dist/Odysseus.dmg   — drag-to-Applications disk image (the downloadable).
#
# This is a *launcher* wrapper: it drives the venv we set up in this repo, it
# does not bundle Python. The install path is baked into the app at build time,
# so rebuild if you move the repo. Override the port with ODYSSEUS_PORT.
#
# The .app is ad-hoc signed (codesign -s -) so it runs locally without
# Gatekeeper prompts. Distribution to other machines needs a Developer
# ID, which is out of scope.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Odysseus"
INSTALL_DIR="$REPO_DIR"
PORT="${ODYSSEUS_PORT:-7860}"
DIST="$REPO_DIR/dist"
APP="$DIST/$APP_NAME.app"
BUNDLE_ID="com.odysseus.launcher"

echo "Building $APP_NAME.app"
echo "  install dir: $INSTALL_DIR"
echo "  port:        $PORT"
echo "  bundle id:   $BUNDLE_ID"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ── Compile the Swift launcher stub ─────────────────────────────────────
# The Swift binary IS the Contents/MacOS/Odysseus executable. It gets
# the install dir + port via argv at build time (we bake them in).
SWIFT_SRC="$REPO_DIR/app/macos/OdysseusLauncher.swift"
SWIFT_BIN="$APP/Contents/MacOS/$APP_NAME"
if [ ! -f "$SWIFT_SRC" ]; then
  echo "✗ Swift source not found: $SWIFT_SRC" >&2
  exit 1
fi
# Bake --install-dir + --port into the binary by appending them to a
# tiny wrapper script that execs the real binary. We can't actually
# change argv at compile time, so we use a wrapper as the "executable"
# in the bundle. (Info.plist's CFBundleExecutable points at the wrapper.)
# Use the absolute path to the Swift binary so we don't hit a global
# `odysseus` symlink on PATH (which would resolve to the bash launcher).
WRAPPER="$APP/Contents/MacOS/launcher"
SWIFT_BIN_PATH="$APP/Contents/MacOS/$APP_NAME"
cat > "$WRAPPER" <<EOF
#!/bin/bash
# Launcher wrapper. Bakes --install-dir and --port into the Swift host.
exec "$SWIFT_BIN_PATH" --install-dir "$INSTALL_DIR" --port "$PORT" "\$@"
EOF
chmod +x "$WRAPPER"

# Compile Swift. -O for size + speed; target arm64+x86_64 universal so
# the .app works on both Apple Silicon and Intel Macs.
ARCH_FLAGS=()
if [ "$(uname -m)" = "arm64" ]; then
  ARCH_FLAGS=(-target arm64-apple-macosx11.0)
else
  ARCH_FLAGS=(-target x86_64-apple-macosx11.0)
fi
xcrun swiftc -O "${ARCH_FLAGS[@]}" \
  -framework Cocoa -framework Foundation \
  -Xfrontend -warn-implicit-overrides \
  -o "$SWIFT_BIN" \
  "$SWIFT_SRC" 2>&1 | sed 's/^/  swiftc: /' | grep -v "was deprecated in macOS 11.0" | grep -v "All NSUserNotifications API" | head -20

# Sanity check: the binary exists and is executable.
if [ ! -x "$SWIFT_BIN" ]; then
  echo "✗ Swift compilation failed: $SWIFT_BIN not produced." >&2
  exit 1
fi

# ── Bundle the bash worker ──────────────────────────────────────────────
# The Swift host spawns Contents/Resources/odysseus-app.sh on launch.
# Bake the install dir + port in via sed — the same trick we used for
# the old bash-only launcher.
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__PORT__|$PORT|g" \
    "$REPO_DIR/app/macos/odysseus-app.sh" \
    > "$APP/Contents/Resources/odysseus-app.sh"
chmod +x "$APP/Contents/Resources/odysseus-app.sh"

# ── Icon (best effort) — center-crop docs/odysseus.jpg to a square .icns ──
if [ -f "$REPO_DIR/docs/odysseus.jpg" ] && command -v sips >/dev/null 2>&1; then
  TMPIMG="$(mktemp -d)"
  # Center-crop to a square, scale to 512 (sips' icns encoder caps at 512), and
  # let sips emit the .icns directly — more robust across macOS versions than
  # building an .iconset by hand.
  sips -c 720 720 "$REPO_DIR/docs/odysseus.jpg" --out "$TMPIMG/sq.png" >/dev/null 2>&1 || cp "$REPO_DIR/docs/odysseus.jpg" "$TMPIMG/sq.png"
  sips -z 512 512 "$TMPIMG/sq.png" --out "$TMPIMG/icon.png" >/dev/null 2>&1
  if sips -s format icns "$TMPIMG/icon.png" --out "$APP/Contents/Resources/odysseus.icns" >/dev/null 2>&1; then
    echo "  icon:        odysseus.icns"
  else
    echo "  icon:        (skipped — conversion failed)"
  fi
  rm -rf "$TMPIMG"
else
  echo "  icon:        (skipped — no docs/odysseus.jpg)"
fi

# ── Info.plist ──
# Notes on the keys:
#   * CFBundleExecutable = launcher (our wrapper, not the Swift binary)
#   * LSUIElement        = true → menu bar app, no dock icon
#   * LSApplicationCategoryType = public.app-category.developer-tools
#                            → identifies the .app's category in Launchpad
#   * NSAppleScriptEnabled = true (default) so the "Open in Terminal"
#                            menu item can spawn Terminal.app
#   * NSUserNotificationAlertStyle = alert so error toasts aren't silent
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                  <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>           <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>            <string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key>               <string>1.0</string>
    <key>CFBundleShortVersionString</key>    <string>1.0</string>
    <key>CFBundlePackageType</key>           <string>APPL</string>
    <key>CFBundleExecutable</key>            <string>launcher</string>
    <key>CFBundleIconFile</key>              <string>odysseus</string>
    <key>LSMinimumSystemVersion</key>        <string>11.0</string>
    <key>LSApplicationCategoryType</key>     <string>public.app-category.developer-tools</string>
    <key>LSUIElement</key>                   <true/>
    <key>NSHighResolutionCapable</key>       <true/>
    <key>NSAppleScriptEnabled</key>          <true/>
    <key>NSUserNotificationAlertStyle</key>  <string>alert</string>
</dict>
</plist>
PLIST

# ── Validate the plist is well-formed ──────────────────────────────────
if ! plutil -lint "$APP/Contents/Info.plist" >/dev/null 2>&1; then
  echo "✗ Info.plist failed plutil -lint." >&2
  plutil -lint "$APP/Contents/Info.plist" >&2 || true
  exit 1
fi

# ── Ad-hoc code sign ────────────────────────────────────────────────────
# codesign --force --deep --sign - signs with an ad-hoc identity. That's
# enough for a launcher that the user double-clicks locally — no
# Gatekeeper prompt, no quarantine, and the .app can be moved around
# the user's machine without breaking. Distribution to other people
# would need a Developer ID identity.
echo "  codesign:    ad-hoc"
if ! codesign --force --deep --sign - "$APP" 2>&1; then
  echo "  ⚠ codesign failed; the .app may prompt on first launch." >&2
fi
codesign --verify --verbose "$APP" 2>&1 | sed 's/^/  verify:     /' || true

# Refresh Finder's icon cache for the new bundle.
touch "$APP"

# ── .dmg (drag-to-Applications) ────────────────────────────────────────
echo "Packaging dist/$APP_NAME.dmg"
STAGE="$(mktemp -d)/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DIST/$APP_NAME.dmg"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DIST/$APP_NAME.dmg" >/dev/null
rm -rf "$STAGE"

echo ""
echo "Done:"
echo "  $APP"
echo "  $DIST/$APP_NAME.dmg"
echo ""
echo "Run it:        open '$APP'"
echo "Install:       open '$DIST/$APP_NAME.dmg'  (drag Odysseus to Applications)"
echo ""
echo "Note: the install path is baked in at build time. After moving the"
echo "repo, re-run:  $REPO_DIR/build-macos-app.sh"
