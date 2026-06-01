import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?
    private var repoPath: String?
    private var controlScriptPath: String?
    private var isStarting = false

    private var appHost: String { ProcessInfo.processInfo.environment["ODYSSEUS_APP_HOST"] ?? "127.0.0.1" }
    private var appPort: String { ProcessInfo.processInfo.environment["ODYSSEUS_APP_PORT"] ?? "7001" }
    private var appURL: URL { URL(string: "http://\(appHost):\(appPort)")! }
    private var stateDirURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/OdysseusDesktop", isDirectory: true)
    }
    private var persistedRepoPathURL: URL { stateDirURL.appendingPathComponent("repo_path.txt") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMenu()
        applyDockIcon()
        setupWindow()
        resolvePaths()
        startBackendAndLoad()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func setupMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenuItem.submenu = appMenu
        appMenu.addItem(withTitle: "Quit Odysseus", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let editMenuItem = NSMenuItem()
        editMenuItem.title = "Edit"
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenuItem.submenu = editMenu

        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let controlMenuItem = NSMenuItem()
        controlMenuItem.title = "Control"
        mainMenu.addItem(controlMenuItem)
        let controlMenu = NSMenu(title: "Control")
        controlMenuItem.submenu = controlMenu

        let reloadItem = controlMenu.addItem(withTitle: "Reload", action: #selector(reloadPage), keyEquivalent: "r")
        reloadItem.target = self
        controlMenu.addItem(NSMenuItem.separator())
        let browserItem = controlMenu.addItem(withTitle: "Open in Browser", action: #selector(openInBrowser), keyEquivalent: "b")
        browserItem.target = self
        controlMenu.addItem(NSMenuItem.separator())
        let restartItem = controlMenu.addItem(withTitle: "Restart Backend", action: #selector(restartBackend), keyEquivalent: "R")
        restartItem.target = self
        let stopItem = controlMenu.addItem(withTitle: "Stop Backend", action: #selector(stopBackend), keyEquivalent: "")
        stopItem.target = self
        controlMenu.addItem(NSMenuItem.separator())
        let chooseRepoItem = controlMenu.addItem(withTitle: "Choose Repo Folder…", action: #selector(chooseRepoFolder), keyEquivalent: "o")
        chooseRepoItem.target = self

        NSApp.mainMenu = mainMenu
    }

    private func applyDockIcon() {
        guard let iconURL = Bundle.main.url(forResource: "Odysseus", withExtension: "icns"),
              let icon = NSImage(contentsOf: iconURL)
        else {
            return
        }
        NSApp.applicationIconImage = icon
    }

    private func setupWindow() {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = true

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.autoresizingMask = [.width, .height]
        self.webView = webView

        let window = NSWindow(
            contentRect: NSRect(x: 80, y: 80, width: 1280, height: 840),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Odysseus"
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        window.center()
        self.window = window

        NSApp.activate(ignoringOtherApps: true)
    }

    private func resolvePaths() {
        var candidates: [String] = []

        if let fromEnv = ProcessInfo.processInfo.environment["ODYSSEUS_REPO_PATH"], !fromEnv.isEmpty {
            candidates.append(fromEnv)
        }
        if let persisted = loadTextFile(persistedRepoPathURL) {
            candidates.append(persisted)
        }
        if let inferred = inferredRepoPathFromBundleLocation() {
            candidates.append(inferred)
        }
        if let bundled = bundledRepoPath() {
            candidates.append(bundled)
        }

        for candidate in candidates {
            if isValidRepoPath(candidate) {
                configureRepoPath(candidate)
                return
            }
        }

        if let selected = promptForRepoPath() {
            configureRepoPath(selected)
            return
        }

        showErrorPage(
            "Odysseus repo not found",
            details: "The app could not locate a valid Odysseus checkout. Use Control -> Choose Repo Folder… and select the repo root."
        )
    }

    private func configureRepoPath(_ path: String) {
        let normalized = (path as NSString).expandingTildeInPath
        self.repoPath = normalized
        self.controlScriptPath = "\(normalized)/scripts/odysseus-desktop-control.sh"
        persistRepoPath(normalized)
    }

    private func bundledRepoPath() -> String? {
        guard let resources = Bundle.main.resourceURL else { return nil }
        return loadTextFile(resources.appendingPathComponent("repo_path.txt"))
    }

    private func inferredRepoPathFromBundleLocation() -> String? {
        let bundleURL = Bundle.main.bundleURL
        let distURL = bundleURL.deletingLastPathComponent()
        let repoURL = distURL.deletingLastPathComponent()
        let candidate = repoURL.path
        return isValidRepoPath(candidate) ? candidate : nil
    }

    private func loadTextFile(_ url: URL) -> String? {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func persistRepoPath(_ path: String) {
        do {
            try FileManager.default.createDirectory(at: stateDirURL, withIntermediateDirectories: true)
            try "\(path)\n".write(to: persistedRepoPathURL, atomically: true, encoding: .utf8)
        } catch {
            // Non-fatal: app can still run without persistence.
        }
    }

    private func isValidRepoPath(_ path: String) -> Bool {
        let expanded = (path as NSString).expandingTildeInPath
        let scriptPath = "\(expanded)/scripts/odysseus-desktop-control.sh"
        let appEntryPath = "\(expanded)/app.py"
        return FileManager.default.isExecutableFile(atPath: scriptPath)
            && FileManager.default.fileExists(atPath: appEntryPath)
    }

    private func promptForRepoPath() -> String? {
        let panel = NSOpenPanel()
        panel.title = "Select Odysseus Repository Folder"
        panel.prompt = "Use Folder"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser

        let result = panel.runModal()
        guard result == .OK, let url = panel.url else { return nil }

        let path = url.path
        guard isValidRepoPath(path) else {
            showErrorPage(
                "Invalid folder selected",
                details: "The selected folder does not look like an Odysseus repo (missing app.py or scripts/odysseus-desktop-control.sh)."
            )
            return nil
        }
        return path
    }

    private func startBackendAndLoad() {
        guard !isStarting else { return }
        guard let controlScriptPath, FileManager.default.isExecutableFile(atPath: controlScriptPath) else {
            showErrorPage("Control script missing", details: "scripts/odysseus-desktop-control.sh is missing or not executable.")
            return
        }

        isStarting = true
        showStatusPage("Starting Odysseus", details: "Launching backend services. This can take a few seconds.")

        runControlCommand("start") { [weak self] code, output in
            guard let self else { return }
            self.isStarting = false
            if code == 0 {
                self.loadApp()
                return
            }
            let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
            let details = trimmed.isEmpty ? "No output from launcher." : trimmed
            self.showErrorPage("Failed to start backend", details: details)
        }
    }

    private func runControlCommand(_ command: String, completion: @escaping (Int32, String) -> Void) {
        guard let controlScriptPath else {
            completion(1, "Control script path not resolved.")
            return
        }

        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/bash")
            process.arguments = [controlScriptPath, command]

            if let repoPath = self.repoPath {
                process.currentDirectoryURL = URL(fileURLWithPath: repoPath, isDirectory: true)
            }

            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe

            do {
                try process.run()
                process.waitUntilExit()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                let text = String(data: data, encoding: .utf8) ?? ""
                DispatchQueue.main.async {
                    completion(process.terminationStatus, text)
                }
            } catch {
                DispatchQueue.main.async {
                    completion(1, "Process launch error: \(error.localizedDescription)")
                }
            }
        }
    }

    private func loadApp() {
        let request = URLRequest(url: appURL)
        webView?.load(request)
    }

    private func showStatusPage(_ title: String, details: String) {
        let html = """
        <html><body style="font-family:-apple-system; margin:24px; line-height:1.45;">
        <h2>\(title)</h2>
        <p>\(details)</p>
        <p>Target: <code>\(appURL.absoluteString)</code></p>
        </body></html>
        """
        webView?.loadHTMLString(html, baseURL: nil)
    }

    private func showErrorPage(_ title: String, details: String) {
        let escaped = details.replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\n", with: "<br>")

        let html = """
        <html><body style="font-family:-apple-system; margin:24px; line-height:1.45;">
        <h2>\(title)</h2>
        <p><strong>What to check:</strong></p>
        <ul>
          <li><code>\(repoPath ?? "(unknown repo path)")/venv</code> exists and dependencies are installed</li>
          <li><code>scripts/odysseus-desktop-control.sh status</code></li>
          <li><code>~/Library/Application Support/OdysseusDesktop/logs/odysseus.log</code></li>
        </ul>
        <hr>
        <div style="font-family:ui-monospace, SFMono-Regular, Menlo, monospace; white-space:normal;">\(escaped)</div>
        </body></html>
        """
        webView?.loadHTMLString(html, baseURL: nil)
    }

    @objc private func reloadPage() {
        webView?.reload()
    }

    @objc private func openInBrowser() {
        NSWorkspace.shared.open(appURL)
    }

    @objc private func restartBackend() {
        showStatusPage("Restarting backend", details: "Stopping and starting services...")
        runControlCommand("restart") { [weak self] code, output in
            guard let self else { return }
            if code == 0 {
                self.loadApp()
            } else {
                self.showErrorPage("Restart failed", details: output)
            }
        }
    }

    @objc private func stopBackend() {
        runControlCommand("stop") { [weak self] _, output in
            let details = output.trimmingCharacters(in: .whitespacesAndNewlines)
            self?.showStatusPage("Backend stopped", details: details.isEmpty ? "Odysseus services stopped." : details)
        }
    }

    @objc private func chooseRepoFolder() {
        guard let selected = promptForRepoPath() else { return }
        configureRepoPath(selected)
        showStatusPage("Repository updated", details: "Using repo at \(selected). Starting backend...")
        startBackendAndLoad()
    }
}

@main
final class OdysseusDesktopApp {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}
