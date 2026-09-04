#!/bin/bash
# Build a downloadable macOS launcher app + .dmg for Odysseus.
#
#   ./build-macos-app.sh
#
# Produces:
#   dist/Odysseus.app   — double-click: starts the local server (using this
#                         repo's venv) and opens the UI in an app-style window.
#   dist/Odysseus.dmg   — drag-to-Applications disk image (the downloadable).
#
# This is a *launcher* wrapper: it drives the venv we set up in this repo, it
# does not bundle Python. The install path is baked into the app at build time,
# so rebuild if you move the repo. Override the port with ODYSSEUS_PORT.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Odysseus"
INSTALL_DIR="$REPO_DIR"
PORT="${ODYSSEUS_PORT:-7860}"
DIST="$REPO_DIR/dist"
APP="$DIST/$APP_NAME.app"

echo "Building $APP_NAME.app"
echo "  install dir: $INSTALL_DIR"
echo "  port:        $PORT"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ── Icon (best effort) — center-crop the branding image to a square .icns ──
if [ -f "$REPO_DIR/assets/branding/odysseus.jpg" ] && command -v sips >/dev/null 2>&1; then
  TMPIMG="$(mktemp -d)"
  # Center-crop to a square, scale to 512 (sips' icns encoder caps at 512), and
  # let sips emit the .icns directly — more robust across macOS versions than
  # building an .iconset by hand.
  sips -c 720 720 "$REPO_DIR/assets/branding/odysseus.jpg" --out "$TMPIMG/sq.png" >/dev/null 2>&1 || cp "$REPO_DIR/assets/branding/odysseus.jpg" "$TMPIMG/sq.png"
  sips -z 512 512 "$TMPIMG/sq.png" --out "$TMPIMG/icon.png" >/dev/null 2>&1
  if sips -s format icns "$TMPIMG/icon.png" --out "$APP/Contents/Resources/odysseus.icns" >/dev/null 2>&1; then
    echo "  icon:        odysseus.icns"
  else
    echo "  icon:        (skipped — conversion failed)"
  fi
  rm -rf "$TMPIMG"
else
  echo "  icon:        (skipped — no assets/branding/odysseus.jpg)"
fi

# ── Info.plist ──
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>     <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>      <string>com.odysseus.launcher</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleExecutable</key>      <string>$APP_NAME</string>
    <key>CFBundleIconFile</key>        <string>odysseus</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>LSUIElement</key>             <true/>
    <key>NSDocumentsFolderUsageDescription</key>
    <string>Odysseus needs access to its installation folder to start the local server.</string>
    <key>NSDesktopFolderUsageDescription</key>
    <string>Odysseus needs access to its installation folder to start the local server.</string>
    <key>NSDownloadsFolderUsageDescription</key>
    <string>Odysseus needs access to its installation folder to start the local server.</string>
</dict>
</plist>
PLIST

# ── Launcher script (placeholders filled below) ──
#
# Keep this in Resources and start it from a native bundle executable. If the
# CFBundleExecutable is itself a shell script, macOS attributes protected-folder
# access to "sh" instead of Odysseus and cannot use this bundle's privacy usage
# descriptions to present a useful consent prompt.
cat > "$APP/Contents/Resources/$APP_NAME-launcher.tmpl" <<'LAUNCHER'
#!/bin/bash
# Odysseus.app — start the local server and open the UI in an app window.
INSTALL_DIR="__INSTALL_DIR__"
PORT="__PORT__"
URL="http://127.0.0.1:${PORT}"
# uvicorn is started with --port below, but APP_PORT is what the app itself
# reads when it needs to build a URL for this instance (internal_api_base(),
# companion pairing, the MCP OAuth callback), so export it as well.
export APP_PORT="$PORT"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

UVICORN="$INSTALL_DIR/venv/bin/uvicorn"
PYVENV_CFG="$INSTALL_DIR/venv/pyvenv.cfg"
LOG="$INSTALL_DIR/logs/odysseus-app.log"

notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Odysseus\"" >/dev/null 2>&1; }
die_gui() {
  /usr/bin/osascript -e "display dialog \"$1\" with title \"Odysseus\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1
  exit 1
}

# A Finder-launched app is subject to macOS Files & Folders privacy controls.
# Probe a file Python itself must read so a protected install location prompts
# before startup, and turn a previous denial into an actionable error instead
# of Python's opaque init_import_site/pyvenv.cfg traceback.
if ! /bin/cat "$PYVENV_CFG" >/dev/null 2>&1; then
  die_gui "macOS blocked the Odysseus shell launcher from reading:

$PYVENV_CFG

To allow access:
1. Open System Settings → Privacy & Security → Files & Folders.
2. Expand “Odysseus”.
3. Turn on “Documents Folder”.
4. Reopen Odysseus.

There is no Add button in Files & Folders; apps appear there after requesting access."
fi

[ -x "$UVICORN" ] || die_gui "Odysseus isn't set up yet. Open Terminal and run:

cd $INSTALL_DIR
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python setup.py"

# Open the UI in a chrome-less app window (Chromium browsers), else default browser.
open_ui() {
  local b base exe bin
  for b in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
    for base in "/Applications" "$HOME/Applications"; do
      if [ -d "$base/$b.app" ]; then
        exe="$(/usr/bin/defaults read "$base/$b.app/Contents/Info" CFBundleExecutable 2>/dev/null)"
        bin="$base/$b.app/Contents/MacOS/$exe"
        if [ -x "$bin" ]; then
          "$bin" --app="$URL" --new-window >/dev/null 2>&1 &
          return 0
        fi
      fi
    done
  done
  /usr/bin/open "$URL"
}

mkdir -p "$INSTALL_DIR/logs" || die_gui "Odysseus can't write to its logs folder:
$INSTALL_DIR/logs

Allow access in System Settings → Privacy & Security → Files & Folders, then reopen the app."

# Already running? Just open the UI.
if /usr/bin/curl -s -o /dev/null --max-time 2 "$URL"; then
  open_ui
  exit 0
fi

notify "Starting…"
cd "$INSTALL_DIR" || die_gui "Install folder not found: $INSTALL_DIR"
if [ "$(uname -m)" = "arm64" ]; then
  arch -arm64 "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1 &
else
  "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1 &
fi
SERVER_PID=$!

# Quitting the app stops the server it started.
trap 'kill $SERVER_PID 2>/dev/null; exit 0' TERM INT

# Wait for readiness (first run downloads an embedding model — allow ~2 min).
READY=0
for i in $(seq 1 120); do
  /usr/bin/curl -s -o /dev/null --max-time 2 "$URL" && { READY=1; break; }
  kill -0 "$SERVER_PID" 2>/dev/null || die_gui "Odysseus failed to start. Log:
$LOG"
  sleep 1
done

if [ "$READY" = "1" ]; then
  open_ui
else
  notify "Odysseus is taking a while — open $URL once it finishes starting."
fi
wait "$SERVER_PID"
LAUNCHER

sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__PORT__|$PORT|g" \
    "$APP/Contents/Resources/$APP_NAME-launcher.tmpl" > "$APP/Contents/Resources/$APP_NAME-launcher"
rm -f "$APP/Contents/Resources/$APP_NAME-launcher.tmpl"
chmod +x "$APP/Contents/Resources/$APP_NAME-launcher"

# A native menu-bar parent gives TCC/Files & Folders the app's bundle identity.
# It stays alive while the shell launcher runs; Exit Odysseus terminates the
# shell, whose existing cleanup trap then stops the server.
NATIVE_SRC="$(mktemp)"
cat > "$NATIVE_SRC" <<'NATIVE_LAUNCHER'
#import <Cocoa/Cocoa.h>

@interface OdysseusDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) NSTask *launcherTask;
@end

@implementation OdysseusDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    [[NSProcessInfo processInfo]
        disableAutomaticTermination:@"Odysseus server is running"];
    [[NSProcessInfo processInfo] disableSuddenTermination];

    self.statusItem = [[NSStatusBar systemStatusBar]
        statusItemWithLength:NSSquareStatusItemLength];
    NSStatusBarButton *button = self.statusItem.button;
    NSImage *icon = [NSImage imageNamed:NSImageNameApplicationIcon];
    icon.size = NSMakeSize(18.0, 18.0);
    button.image = icon;
    button.toolTip = @"Odysseus is running";

    NSMenu *menu = [[NSMenu alloc] init];
    NSMenuItem *status = [[NSMenuItem alloc]
        initWithTitle:@"Odysseus is running" action:nil keyEquivalent:@""];
    status.enabled = NO;
    [menu addItem:status];
    [menu addItem:[NSMenuItem separatorItem]];
    NSMenuItem *exitItem = [[NSMenuItem alloc]
        initWithTitle:@"Exit Odysseus"
               action:@selector(exitOdysseus:)
        keyEquivalent:@"q"];
    exitItem.target = self;
    [menu addItem:exitItem];
    self.statusItem.menu = menu;

    NSString *launcher = [[[NSBundle mainBundle] resourcePath]
        stringByAppendingPathComponent:@"Odysseus-launcher"];
    self.launcherTask = [[NSTask alloc] init];
    self.launcherTask.executableURL = [NSURL fileURLWithPath:@"/bin/bash"];
    self.launcherTask.arguments = @[launcher];

    __weak OdysseusDelegate *weakSelf = self;
    self.launcherTask.terminationHandler = ^(NSTask *task) {
        (void)task;
        dispatch_async(dispatch_get_main_queue(), ^{
            if (weakSelf != nil) [NSApp terminate:nil];
        });
    };

    NSError *error = nil;
    if (![self.launcherTask launchAndReturnError:&error]) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"Odysseus could not start";
        alert.informativeText = error.localizedDescription;
        [alert runModal];
        [NSApp terminate:nil];
    }
}

- (void)exitOdysseus:(id)sender {
    (void)sender;
    [NSApp terminate:nil];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    (void)notification;
    if (self.launcherTask.running) {
        [self.launcherTask terminate];
        [self.launcherTask waitUntilExit];
    }
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    (void)sender;
    return NSTerminateNow;
}

@end

int main(int argc, const char *argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        static OdysseusDelegate *delegate;
        delegate = [[OdysseusDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
NATIVE_LAUNCHER
xcrun clang -Os -Wall -Wextra -mmacosx-version-min=11.0 \
    -fobjc-arc -framework Cocoa -x objective-c "$NATIVE_SRC" \
    -o "$APP/Contents/MacOS/$APP_NAME"
rm -f "$NATIVE_SRC"

# Give Launch Services and TCC a code identity even for local, non-notarized
# builds. Release distribution can replace this with a Developer ID signature.
codesign --force --deep --sign - "$APP"

# Refresh Finder's icon cache for the new bundle.
touch "$APP"

# ── .dmg (drag-to-Applications) ──
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
