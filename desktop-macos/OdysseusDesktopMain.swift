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
        guard
            let resources = Bundle.main.resourceURL,
            let repoPathRaw = try? String(contentsOf: resources.appendingPathComponent("repo_path.txt"), encoding: .utf8)
        else {
            showErrorPage("Missing app resources", details: "repo_path.txt was not found in the app bundle.")
            return
        }

        let repoPath = repoPathRaw.trimmingCharacters(in: .whitespacesAndNewlines)
        self.repoPath = repoPath
        self.controlScriptPath = "\(repoPath)/scripts/odysseus-desktop-control.sh"
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
