#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="${APP_NAME:-Odysseus}"
BUNDLE_DIR="${ROOT_DIR}/dist/${APP_NAME}.app"
EXEC_NAME="${APP_NAME}"
SOURCE_FILE="${ROOT_DIR}/desktop-macos/OdysseusDesktopMain.swift"
BUILD_DIR="${ROOT_DIR}/.build/desktop-macos"
TMP_EXEC="${BUILD_DIR}/${EXEC_NAME}"
ICON_SOURCE="${ICON_SOURCE:-${ROOT_DIR}/static/odysseus-favicon.svg}"
ICONSET_DIR="${BUILD_DIR}/${APP_NAME}.iconset"
ICON_BASENAME="${APP_NAME}.icns"

MACOS_DIR="${BUNDLE_DIR}/Contents/MacOS"
RES_DIR="${BUNDLE_DIR}/Contents/Resources"
PLIST_PATH="${BUNDLE_DIR}/Contents/Info.plist"
EXEC_PATH="${MACOS_DIR}/${EXEC_NAME}"
ICON_PATH="${RES_DIR}/${ICON_BASENAME}"

if [[ ! -f "${SOURCE_FILE}" ]]; then
  echo "Missing desktop source file:"
  echo "  ${SOURCE_FILE}"
  exit 1
fi

mkdir -p "${BUILD_DIR}"
mkdir -p "${MACOS_DIR}" "${RES_DIR}"

echo "Compiling native macOS wrapper..."
xcrun swiftc \
  -O \
  -parse-as-library \
  -framework AppKit \
  -framework WebKit \
  "${SOURCE_FILE}" \
  -o "${TMP_EXEC}"

if [[ -f "${ICON_SOURCE}" ]]; then
  echo "Generating app icon from ${ICON_SOURCE} ..."
  rm -rf "${ICONSET_DIR}"
  mkdir -p "${ICONSET_DIR}"

  SQUARE_SOURCE="${BUILD_DIR}/icon-source-square.png"
  sips -z 1024 1024 -s format png "${ICON_SOURCE}" --out "${SQUARE_SOURCE}" >/dev/null

  sips -z 16 16     -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_16x16.png" >/dev/null
  sips -z 32 32     -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_16x16@2x.png" >/dev/null
  sips -z 32 32     -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_32x32.png" >/dev/null
  sips -z 64 64     -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_32x32@2x.png" >/dev/null
  sips -z 128 128   -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_128x128.png" >/dev/null
  sips -z 256 256   -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_128x128@2x.png" >/dev/null
  sips -z 256 256   -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_256x256.png" >/dev/null
  sips -z 512 512   -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_256x256@2x.png" >/dev/null
  sips -z 512 512   -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_512x512.png" >/dev/null
  sips -z 1024 1024 -s format png "${SQUARE_SOURCE}" --out "${ICONSET_DIR}/icon_512x512@2x.png" >/dev/null

  iconutil --convert icns --output "${ICON_PATH}" "${ICONSET_DIR}"
else
  echo "Icon source not found at ${ICON_SOURCE}; keeping existing icon settings."
fi

cat > "${PLIST_PATH}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleExecutable</key>
  <string>${EXEC_NAME}</string>
  <key>CFBundleIdentifier</key>
  <string>local.odysseus.desktop</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleIconFile</key>
  <string>${APP_NAME}</string>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <false/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
    <key>NSAllowsArbitraryLoadsInWebContent</key>
    <true/>
  </dict>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

cp "${TMP_EXEC}" "${EXEC_PATH}"

printf "%s\n" "${ROOT_DIR}" > "${RES_DIR}/repo_path.txt"
chmod +x "${EXEC_PATH}"

echo "Built app bundle:"
echo "  ${BUNDLE_DIR}"
echo
echo "Open it by double-clicking:"
echo "  ${BUNDLE_DIR}"
