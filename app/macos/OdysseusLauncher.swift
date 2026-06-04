// OdysseusLauncher.swift — minimal Cocoa host for the Odysseus .app bundle.
//
// What this file does:
//   * Lives in the .app as Contents/MacOS/Odysseus (built by build-macos-app.sh).
//   * Spawns the bash worker (`odysseus-app.sh` in Contents/Resources) and
//     supervises it.
//   * Shows a menu bar item (NSStatusItem) with the current server state.
//   * Forwards Cmd-Q into a SIGTERM to the worker and waits for clean exit.
//   * Reads ~/Library/Application Support/Odysseus/state.json — written
//     by the worker — to drive the menu bar UI.
//
// What this file intentionally does NOT do:
//   * It does not bundle Python, the venv, or the source code. The .app
//     is a launcher that drives a separate repo install (~/odysseus or
//     whatever INSTALL_DIR was baked in at build time).
//   * It does not request code signing. The .app is ad-hoc signed at
//     build time, which is enough to launch locally and to be moved
//     around the user's machine. Distribution to other people needs
//     a Developer ID, which is out of scope.
//
// Build:
//   swiftc -O -target arm64-apple-macosx11.0 \
//          -framework Cocoa -framework Foundation \
//          -o Odysseus OdysseusLauncher.swift
// (build-macos-app.sh handles the invocation.)

import Cocoa

// MARK: - State model

struct AppState: Decodable {
    enum State: String, Decodable { case starting, running, stopped, error }
    let state: State
    let port: Int
    let pid: Int
    let url: String
    let message: String
}

extension AppState.State {
    var menuBarTitle: String {
        switch self {
        case .starting: return "⚙︎ Odysseus"
        case .running:  return "● Odysseus"
        case .stopped:  return "○ Odysseus"
        case .error:    return "✕ Odysseus"
        }
    }
    var isError: Bool { self == .error }
}

// MARK: - Worker process

final class Worker {
    let path: String
    let installDir: String
    let port: Int
    private(set) var process: Process?
    private(set) var stdoutPipe: Pipe?
    private(set) var stderrPipe: Pipe?

    init(path: String, installDir: String, port: Int) {
        self.path = path
        self.installDir = installDir
        self.port = port
    }

    func start() throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [path]
        // Forward ODYSSEUS_FROM_APP=1 so the bash worker knows to
        // relocate data/ to ~/Library/Application Support/Odysseus.
        var env = ProcessInfo.processInfo.environment
        env["ODYSSEUS_FROM_APP"] = "1"
        env["ODYSSEUS_PORT"] = String(port)
        p.environment = env
        p.currentDirectoryURL = URL(fileURLWithPath: installDir)

        let out = Pipe()
        let err = Pipe()
        p.standardOutput = out
        p.standardError = err
        stdoutPipe = out
        stderrPipe = err

        // Don't crash the host if the worker dies — we want the menu
        // bar UI to keep working so the user can read the error and
        // pick "Open in Terminal" / "Reveal Log".
        p.terminationHandler = { _ in
            DispatchQueue.main.async {
                NotificationCenter.default.post(name: .workerDidExit, object: nil)
            }
        }
        try p.run()
        process = p
    }

    func terminate(gracefulSeconds: Double = 5.0, completion: (() -> Void)? = nil) {
        guard let p = process, p.isRunning else {
            completion?()
            return
        }
        p.terminate()
        // Wait for the worker to exit cleanly. We poll rather than
        // block on waitUntilExit() because the Cocoa run loop still
        // needs to spin for applicationShouldTerminate's
        // .terminateLater reply to land.
        DispatchQueue.global(qos: .userInitiated).async {
            let deadline = Date().addingTimeInterval(gracefulSeconds)
            while p.isRunning && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.1)
            }
            if p.isRunning {
                // Worker ignored SIGTERM. Send SIGINT (Process.interrupt)
                // before SIGKILL as a last-ditch attempt at clean exit.
                p.interrupt()
                Thread.sleep(forTimeInterval: 0.5)
                if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            }
            DispatchQueue.main.async { completion?() }
        }
    }
}

extension Notification.Name {
    static let workerDidExit = Notification.Name("com.odysseus.workerDidExit")
}

// MARK: - State file watcher

final class StateWatcher {
    private var source: DispatchSourceFileSystemObject?
    private let url: URL
    private let queue = DispatchQueue(label: "com.odysseus.statewatcher")
    private(set) var last: AppState?

    init(url: URL) {
        self.url = url
    }

    func start(_ onChange: @escaping (AppState) -> Void) {
        // Make sure the file exists so DispatchSource has something to watch.
        let fm = FileManager.default
        if !fm.fileExists(atPath: url.path) {
            try? Data().write(to: url)
        }
        // Read once synchronously so the menu bar has a value on first paint.
        if let initial = read() {
            last = initial
            onChange(initial)
        }
        let fd = open(url.path, O_EVTONLY)
        guard fd >= 0 else { return }
        let src = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd, eventMask: [.write, .extend, .rename], queue: queue
        )
        src.setEventHandler { [weak self] in
            DispatchQueue.main.async {
                guard let self = self, let s = self.read() else { return }
                self.last = s
                onChange(s)
            }
        }
        src.setCancelHandler { close(fd) }
        src.resume()
        source = src
    }

    private func read() -> AppState? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(AppState.self, from: data)
    }
}

// MARK: - AppDelegate

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var menu: NSMenu!
    private var statusMenuItem: NSMenuItem!
    private var portMenuItem: NSMenuItem!
    private var openMenuItem: NSMenuItem!
    private var openInTerminalItem: NSMenuItem!
    private var revealLogItem: NSMenuItem!
    private var quitItem: NSMenuItem!

    private var worker: Worker!
    private var watcher: StateWatcher!
    private var currentState: AppState?
    private let workerInstallDir: String
    private let workerPort: Int

    // Both values are baked in at build time as command-line args to the
    // Swift binary: --install-dir <path> --port <N>. Reading them in
    // main() makes the build script's job easy.
    init(installDir: String, port: Int) {
        self.workerInstallDir = installDir
        self.workerPort = port
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Catch SIGTERM/SIGINT before Cocoa does, so we can forward
        // them to the worker as a clean shutdown.
        installSignalHandlers()

        // ── Menu bar ───────────────────────────────────────────────────
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "○ Odysseus"
        statusItem.button?.toolTip = "Odysseus — starting…"

        menu = NSMenu()
        statusMenuItem = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)

        portMenuItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        portMenuItem.isEnabled = false
        menu.addItem(portMenuItem)

        menu.addItem(NSMenuItem.separator())

        openMenuItem = NSMenuItem(title: "Open in Browser", action: #selector(openInBrowser), keyEquivalent: "o")
        openMenuItem.target = self
        menu.addItem(openMenuItem)

        openInTerminalItem = NSMenuItem(title: "Open in Terminal", action: #selector(openInTerminal), keyEquivalent: "t")
        openInTerminalItem.target = self
        menu.addItem(openInTerminalItem)

        revealLogItem = NSMenuItem(title: "Reveal Log in Finder", action: #selector(revealLog), keyEquivalent: "l")
        revealLogItem.target = self
        menu.addItem(revealLogItem)

        menu.addItem(NSMenuItem.separator())
        quitItem = NSMenuItem(title: "Quit Odysseus", action: #selector(quit), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
        statusItem.menu = menu

        // ── Worker ────────────────────────────────────────────────────
        let appSupport = (NSHomeDirectory() as NSString)
            .appendingPathComponent("Library/Application Support/Odysseus")
        let stateFile = URL(fileURLWithPath: appSupport).appendingPathComponent("state.json")

        watcher = StateWatcher(url: stateFile)
        watcher.start { [weak self] state in
            self?.applyState(state)
        }

        let workerPath = Bundle.main.path(forResource: "odysseus-app", ofType: "sh") ?? ""
        if workerPath.isEmpty {
            applyState(AppState(
                state: .error, port: workerPort, pid: 0, url: "",
                message: "Bundled worker script missing — was the .app built correctly?"
            ))
            return
        }
        worker = Worker(path: workerPath, installDir: workerInstallDir, port: workerPort)
        do {
            try worker.start()
        } catch {
            applyState(AppState(
                state: .error, port: workerPort, pid: 0, url: "",
                message: "Could not start worker: \(error.localizedDescription)"
            ))
        }

        NotificationCenter.default.addObserver(
            self, selector: #selector(handleWorkerExit),
            name: .workerDidExit, object: nil
        )
    }

    private func applyState(_ state: AppState) {
        currentState = state
        statusItem.button?.title = state.state.menuBarTitle
        statusItem.button?.toolTip = "Odysseus: \(state.message)"
        statusMenuItem.title = state.message
        portMenuItem.title = state.url.isEmpty ? "" : "URL: \(state.url)"

        // The user can open the browser as soon as the server is up.
        let canOpen = (state.state == .running) && !state.url.isEmpty
        openMenuItem.isEnabled = canOpen

        // Surface errors as a notification so the user doesn't have to
        // click the menu bar item to find out something is wrong.
        if state.state == .error {
            let n = NSUserNotification()
            n.title = "Odysseus"
            n.subtitle = "Server failed to start"
            n.informativeText = state.message
            NSUserNotificationCenter.default.deliver(n)
        }
    }

    @objc private func openInBrowser() {
        guard let urlStr = currentState?.url, !urlStr.isEmpty,
              let url = URL(string: urlStr) else { return }
        // Prefer an app-style window in a Chromium browser if available.
        for browser in ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"] {
            let path = "/Applications/\(browser).app"
            if FileManager.default.fileExists(atPath: path) {
                let task = Process()
                task.executableURL = URL(fileURLWithPath: path)
                task.arguments = ["--app=\(urlStr)", "--new-window"]
                try? task.run()
                return
            }
        }
        NSWorkspace.shared.open(url)
    }

    @objc private func openInTerminal() {
        // Spawn Terminal with a prefilled command. Useful when the
        // first-run venv setup needs to run interactively (brew prompts,
        // TCC consent, etc.) — Terminal inherits the user's TCC consents
        // for ~/Desktop, ~/Documents, etc., which the launchd agent
        // doesn't.
        let script = """
        tell application "Terminal"
          do script "cd '\(workerInstallDir)' && ./odysseus.sh --launch=native"
          activate
        end tell
        """
        if let apple = NSAppleScript(source: script) {
            var err: NSDictionary?
            _ = apple.executeAndReturnError(&err)
        }
    }

    @objc private func revealLog() {
        let appSupport = (NSHomeDirectory() as NSString)
            .appendingPathComponent("Library/Application Support/Odysseus")
        let url = URL(fileURLWithPath: appSupport)
        // Create the dir if the worker hasn't yet, so the user sees
        // a Finder window they can navigate rather than nothing.
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    @objc private func handleWorkerExit() {
        // Worker died. Update the menu bar to reflect the state file
        // (which the worker writes on its way out). If the worker
        // died without writing a "stopped" state, the last read may
        // still show "running" — re-read to be sure.
        if let final = watcher.last, final.state == .stopped { return }
        // Give the worker a beat to write its exit state, then re-read.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self = self else { return }
            // Re-read the state file directly in case DispatchSource
            // missed a final write.
            let appSupport = (NSHomeDirectory() as NSString)
                .appendingPathComponent("Library/Application Support/Odysseus")
            let path = URL(fileURLWithPath: appSupport).appendingPathComponent("state.json")
            if let data = try? Data(contentsOf: path),
               let s = try? JSONDecoder().decode(AppState.self, from: data) {
                self.applyState(s)
            }
        }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    // ── Proper Cmd-Q + SIGTERM handling ─────────────────────────────────
    // Two paths trigger shutdown:
    //   1. Cmd-Q → applicationShouldTerminate
    //   2. SIGTERM (kill from another terminal) → DispatchSource signal
    // Both should SIGTERM the worker, wait for it, then exit the host.
    // The .terminateLater / reply(toApplicationShouldTerminate:) dance
    // is the official way for path 1; for path 2 we just call NSApp.terminate
    // which routes through the same code.
    private var terminateRequested = false
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminateRequested { return .terminateNow }
        terminateRequested = true
        worker?.terminate(gracefulSeconds: 5.0) {
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    // SIGTERM from outside the run loop (kill from another terminal,
    // a parent process dying) — Cocoa doesn't always turn those into
    // applicationShouldTerminate, especially for accessory apps. Hook
    // the signal directly via DispatchSource so the worker always
    // gets a clean shutdown pass.
    private var sigtermSource: DispatchSourceSignal?
    func installSignalHandlers() {
        signal(SIGTERM, SIG_IGN)
        signal(SIGINT, SIG_IGN)
        let src = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        src.setEventHandler { [weak self] in
            guard let self = self else { return }
            if !self.terminateRequested {
                self.terminateRequested = true
                self.worker?.terminate(gracefulSeconds: 5.0) {
                    NSApp.terminate(nil)
                }
            } else {
                NSApp.terminate(nil)
            }
        }
        src.resume()
        sigtermSource = src
        let intSrc = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        intSrc.setEventHandler { [weak self] in
            self?.worker?.terminate(gracefulSeconds: 2.0) { NSApp.terminate(nil) }
        }
        intSrc.resume()
    }
}

// MARK: - main

// Parse --install-dir and --port from argv. build-macos-app.sh bakes
// these in by appending them to the Swift binary's launch command in
// the .app's Contents/Info.plist (via NSPrincipalClass / NSDocumentClass
// would be the Cocoa way, but plain argv is simpler and we control the
// build).
var installDir = ""
var port = 7860
var i = 1
let argv = CommandLine.arguments
while i < argv.count {
    switch argv[i] {
    case "--install-dir":
        i += 1
        if i < argv.count { installDir = argv[i] }
    case "--port":
        i += 1
        if i < argv.count { port = Int(argv[i]) ?? 7860 }
    default: break
    }
    i += 1
}
if installDir.isEmpty {
    fputs("error: --install-dir not provided\n", stderr)
    exit(1)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // Menu bar app — no dock icon
let delegate = AppDelegate(installDir: installDir, port: port)
app.delegate = delegate
app.run()
