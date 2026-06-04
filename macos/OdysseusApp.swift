import Cocoa
import WebKit

struct RuntimeConfig {
    let port: Int
    let bundleRuntimeURL: URL
    let supportRuntimeURL: URL
    let serverURL: URL
    let bundleVersion: String

    static func load() throws -> RuntimeConfig {
        guard let resourcesURL = Bundle.main.resourceURL else {
            throw NSError(domain: "Odysseus", code: 1, userInfo: [NSLocalizedDescriptionKey: "Missing app resources directory."])
        }

        let bundleRuntimeURL = resourcesURL.appendingPathComponent("runtime", isDirectory: true)
        guard FileManager.default.fileExists(atPath: bundleRuntimeURL.path) else {
            throw NSError(domain: "Odysseus", code: 2, userInfo: [NSLocalizedDescriptionKey: "Missing packaged runtime. Rebuild the app."])
        }

        let appSupportRoot = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("Odysseus", isDirectory: true)
        let supportRuntimeURL = appSupportRoot.appendingPathComponent("runtime", isDirectory: true)

        let portValue = Bundle.main.object(forInfoDictionaryKey: "OdysseusPort") as? String ?? "7860"
        guard let port = Int(portValue) else {
            throw NSError(domain: "Odysseus", code: 3, userInfo: [NSLocalizedDescriptionKey: "Invalid port value in bundle info: \(portValue)"])
        }

        let bundleVersion = Bundle.main.object(forInfoDictionaryKey: "OdysseusRuntimeVersion") as? String ?? "unknown"

        return RuntimeConfig(
            port: port,
            bundleRuntimeURL: bundleRuntimeURL,
            supportRuntimeURL: supportRuntimeURL,
            serverURL: URL(string: "http://127.0.0.1:\(port)")!,
            bundleVersion: bundleVersion
        )
    }
}

final class RuntimeInstaller {
    private let fm = FileManager.default
    private let bundleRuntimeURL: URL
    private let supportRuntimeURL: URL
    private let bundleVersion: String

    init(bundleRuntimeURL: URL, supportRuntimeURL: URL, bundleVersion: String) {
        self.bundleRuntimeURL = bundleRuntimeURL
        self.supportRuntimeURL = supportRuntimeURL
        self.bundleVersion = bundleVersion
    }

    func prepare() throws -> (URL, String?) {
        let parent = supportRuntimeURL.deletingLastPathComponent()
        try fm.createDirectory(at: parent, withIntermediateDirectories: true)

        if !fm.fileExists(atPath: supportRuntimeURL.path) {
            try fm.copyItem(at: bundleRuntimeURL, to: supportRuntimeURL)
            try fixBundledPythonRuntime()
            try writeMarker()
        }

        if currentMarker() != bundleVersion {
            try refreshRuntime()
            try fixBundledPythonRuntime()
            try writeMarker()
        }

        let setupOutput = try runFirstLaunchSetupIfNeeded()
        return (supportRuntimeURL, setupOutput)
    }

    private func markerURL() -> URL {
        supportRuntimeURL.appendingPathComponent(".bundle-version")
    }

    private func currentMarker() -> String? {
        guard let data = try? Data(contentsOf: markerURL()),
              let text = String(data: data, encoding: .utf8) else {
            return nil
        }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func writeMarker() throws {
        try bundleVersion.write(to: markerURL(), atomically: true, encoding: .utf8)
    }

    private func hasFirstRunState() -> Bool {
        let dataDir = supportRuntimeURL.appendingPathComponent("data", isDirectory: true)
        let appDB = dataDir.appendingPathComponent("app.db")
        let authJSON = dataDir.appendingPathComponent("auth.json")
        return fm.fileExists(atPath: appDB.path) && fm.fileExists(atPath: authJSON.path)
    }

    private func refreshRuntime() throws {
        let preservedNames: Set<String> = [
            "data",
            "logs",
            ".env",
            ".bundle-version"
        ]

        let existing = try fm.contentsOfDirectory(at: supportRuntimeURL, includingPropertiesForKeys: nil)
        for item in existing where !preservedNames.contains(item.lastPathComponent) {
            try fm.removeItem(at: item)
        }

        let bundled = try fm.contentsOfDirectory(at: bundleRuntimeURL, includingPropertiesForKeys: nil)
        for item in bundled where !preservedNames.contains(item.lastPathComponent) {
            let destination = supportRuntimeURL.appendingPathComponent(item.lastPathComponent)
            if fm.fileExists(atPath: destination.path) {
                try fm.removeItem(at: destination)
            }
            try fm.copyItem(at: item, to: destination)
        }
    }

    private func fixBundledPythonRuntime() throws {
        let pythonVersion = try rewritePyVenvConfig()
        let pythonPath = supportRuntimeURL.appendingPathComponent("venv/bin/python3").path
        let binDir = supportRuntimeURL.appendingPathComponent("venv/bin")

        guard fm.fileExists(atPath: binDir.path) else { return }

        let items = try fm.contentsOfDirectory(at: binDir, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        for item in items {
            let values = try item.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            guard values.isRegularFile == true, values.isSymbolicLink != true else { continue }
            guard let contents = try? String(contentsOf: item, encoding: .utf8) else { continue }
            guard contents.hasPrefix("#!") else { continue }

            let firstLineEnd = contents.firstIndex(of: "\n") ?? contents.endIndex
            let firstLine = String(contents[..<firstLineEnd])
            guard firstLine.contains("python") else { continue }

            let remaining = firstLineEnd == contents.endIndex ? "" : String(contents[contents.index(after: firstLineEnd)...])
            let rewritten = "#!\(pythonPath)\n\(remaining)"
            try rewritten.write(to: item, atomically: true, encoding: .utf8)
            try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: item.path)
        }

        let versionedPython = supportRuntimeURL.appendingPathComponent("venv/bin/python\(pythonVersion)")
        if fm.fileExists(atPath: versionedPython.path) {
            try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: versionedPython.path)
        }
    }

    private func rewritePyVenvConfig() throws -> String {
        let cfgURL = supportRuntimeURL.appendingPathComponent("venv/pyvenv.cfg")
        let versionDir = try bundledPythonHome()
        let version = versionDir.lastPathComponent
        let homePath = versionDir.appendingPathComponent("bin").path
        let executablePath = supportRuntimeURL.appendingPathComponent("venv/bin/python\(version)").path
        let commandPath = "\(homePath)/python\(version) -m venv \(supportRuntimeURL.appendingPathComponent("venv").path)"

        var lines = (try? String(contentsOf: cfgURL, encoding: .utf8).components(separatedBy: .newlines)) ?? []
        if lines.isEmpty {
            lines = ["include-system-site-packages = false", "version = \(version)"]
        }

        func setLine(prefix: String, value: String) {
            if let idx = lines.firstIndex(where: { $0.hasPrefix(prefix) }) {
                lines[idx] = "\(prefix)\(value)"
            } else {
                lines.append("\(prefix)\(value)")
            }
        }

        setLine(prefix: "home = ", value: homePath)
        setLine(prefix: "version = ", value: version)
        setLine(prefix: "executable = ", value: executablePath)
        setLine(prefix: "command = ", value: commandPath)

        let text = lines.filter { !$0.isEmpty }.joined(separator: "\n") + "\n"
        try text.write(to: cfgURL, atomically: true, encoding: .utf8)
        return version
    }

    private func bundledPythonHome() throws -> URL {
        let frameworkRoot = supportRuntimeURL.appendingPathComponent("python-framework", isDirectory: true)
        let frameworkDirs = try fm.contentsOfDirectory(at: frameworkRoot, includingPropertiesForKeys: [.isDirectoryKey])
            .filter { $0.pathExtension == "framework" }
        guard let frameworkDir = frameworkDirs.first else {
            throw NSError(domain: "Odysseus", code: 16, userInfo: [NSLocalizedDescriptionKey: "Missing bundled Python.framework in packaged runtime."])
        }

        let versionsDir = frameworkDir.appendingPathComponent("Versions", isDirectory: true)
        let versionDirs = try fm.contentsOfDirectory(at: versionsDir, includingPropertiesForKeys: [.isDirectoryKey])
            .filter { $0.lastPathComponent != "Current" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        guard let versionDir = versionDirs.last else {
            throw NSError(domain: "Odysseus", code: 17, userInfo: [NSLocalizedDescriptionKey: "Missing Python framework version directory in packaged runtime."])
        }
        return versionDir
    }

    private func runFirstLaunchSetupIfNeeded() throws -> String? {
        guard !hasFirstRunState() else { return nil }

        let pythonURL = supportRuntimeURL.appendingPathComponent("venv/bin/python3")
        guard fm.isExecutableFile(atPath: pythonURL.path) else {
            throw NSError(domain: "Odysseus", code: 13, userInfo: [NSLocalizedDescriptionKey: "Missing runtime Python interpreter in \(supportRuntimeURL.path)."])
        }

        let setupURL = supportRuntimeURL.appendingPathComponent("setup.py")
        guard fm.fileExists(atPath: setupURL.path) else {
            throw NSError(domain: "Odysseus", code: 14, userInfo: [NSLocalizedDescriptionKey: "Missing setup script in packaged runtime."])
        }

        let outPipe = Pipe()
        let errPipe = Pipe()
        let proc = Process()
        proc.executableURL = pythonURL
        proc.currentDirectoryURL = supportRuntimeURL
        proc.arguments = ["setup.py"]

        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["ODYSSEUS_SKIP_RUN_HINT"] = "1"
        environment["VIRTUAL_ENV"] = supportRuntimeURL.appendingPathComponent("venv").path
        environment["PATH"] = supportRuntimeURL.appendingPathComponent("venv/bin").path + ":" + (environment["PATH"] ?? "")
        environment["PYTHONHOME"] = try bundledPythonHome().path
        proc.environment = environment
        proc.standardOutput = outPipe
        proc.standardError = errPipe

        try proc.run()
        proc.waitUntilExit()

        let output = (String(data: outPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "")
            + (String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "")

        if proc.terminationStatus != 0 {
            throw NSError(
                domain: "Odysseus",
                code: 15,
                userInfo: [NSLocalizedDescriptionKey: "First-run setup failed.\n\n\(output)"]
            )
        }

        let logsURL = supportRuntimeURL.appendingPathComponent("logs", isDirectory: true)
        try fm.createDirectory(at: logsURL, withIntermediateDirectories: true)
        try output.write(to: logsURL.appendingPathComponent("first-run-setup.log"), atomically: true, encoding: .utf8)
        return output
    }
}

final class BackendController {
    private let runtimeURL: URL
    private let port: Int
    private let serverURL: URL
    private var process: Process?
    private var outputPipe: Pipe?
    private var logHandle: FileHandle?
    private var launchedProcess = false

    init(runtimeURL: URL, port: Int, serverURL: URL) {
        self.runtimeURL = runtimeURL
        self.port = port
        self.serverURL = serverURL
    }

    func probe(completion: @escaping (Bool) -> Void) {
        var request = URLRequest(url: serverURL.appendingPathComponent("api/health"))
        request.timeoutInterval = 1.5

        URLSession.shared.dataTask(with: request) { data, response, _ in
            guard let http = response as? HTTPURLResponse, http.statusCode == 200, let data else {
                completion(false)
                return
            }
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let status = json["status"] as? String else {
                completion(false)
                return
            }
            completion(status == "healthy")
        }.resume()
    }

    func startIfNeeded(completion: @escaping (Result<Bool, Error>) -> Void) {
        probe { [weak self] running in
            guard let self else {
                completion(.success(false))
                return
            }
            if running {
                completion(.success(true))
                return
            }
            do {
                try self.launchProcess()
                self.waitUntilReady(deadline: Date().addingTimeInterval(180), completion: completion)
            } catch {
                completion(.failure(error))
            }
        }
    }

    func stop() {
        guard launchedProcess, let process, process.isRunning else { return }
        process.terminate()
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 2) {
            if process.isRunning {
                kill(process.processIdentifier, SIGKILL)
            }
        }
    }

    private func launchProcess() throws {
        let pythonCandidates = [
            runtimeURL.appendingPathComponent("venv/bin/python3"),
            runtimeURL.appendingPathComponent("venv/bin/python")
        ]
        guard let pythonURL = pythonCandidates.first(where: { FileManager.default.isExecutableFile(atPath: $0.path) }) else {
            throw NSError(domain: "Odysseus", code: 10, userInfo: [NSLocalizedDescriptionKey: "Missing runtime Python interpreter in \(runtimeURL.path)."])
        }

        try FileManager.default.createDirectory(at: runtimeURL.appendingPathComponent("logs"), withIntermediateDirectories: true)

        let logURL = runtimeURL.appendingPathComponent("logs/odysseus-app.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }

        let fh = try FileHandle(forUpdating: logURL)
        try fh.seekToEnd()
        logHandle = fh

        let pipe = Pipe()
        outputPipe = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.logHandle?.write(data)
        }

        let proc = Process()
        proc.executableURL = pythonURL
        proc.currentDirectoryURL = runtimeURL
        proc.arguments = ["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "\(port)"]

        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["VIRTUAL_ENV"] = runtimeURL.appendingPathComponent("venv").path
        environment["PATH"] = runtimeURL.appendingPathComponent("venv/bin").path + ":" + (environment["PATH"] ?? "")
        environment["PYTHONHOME"] = try bundledPythonHome().path
        proc.environment = environment
        proc.standardOutput = pipe
        proc.standardError = pipe
        proc.terminationHandler = { [weak self] _ in
            self?.outputPipe?.fileHandleForReading.readabilityHandler = nil
            try? self?.logHandle?.close()
        }

        try proc.run()
        process = proc
        launchedProcess = true
    }

    private func waitUntilReady(deadline: Date, completion: @escaping (Result<Bool, Error>) -> Void) {
        probe { [weak self] ready in
            guard let self else {
                completion(.success(false))
                return
            }
            if ready {
                completion(.success(true))
                return
            }
            if let process = self.process, !process.isRunning {
                completion(.failure(NSError(domain: "Odysseus", code: 11, userInfo: [NSLocalizedDescriptionKey: "Odysseus stopped before it became ready. Check logs/odysseus-app.log."])))
                return
            }
            if Date() > deadline {
                completion(.failure(NSError(domain: "Odysseus", code: 12, userInfo: [NSLocalizedDescriptionKey: "Timed out waiting for Odysseus to start. Check logs/odysseus-app.log."])))
                return
            }
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 1) {
                self.waitUntilReady(deadline: deadline, completion: completion)
            }
        }
    }

    private func bundledPythonHome() throws -> URL {
        let frameworkRoot = runtimeURL.appendingPathComponent("python-framework", isDirectory: true)
        let frameworkDirs = try FileManager.default.contentsOfDirectory(at: frameworkRoot, includingPropertiesForKeys: [.isDirectoryKey])
            .filter { $0.pathExtension == "framework" }
        guard let frameworkDir = frameworkDirs.first else {
            throw NSError(domain: "Odysseus", code: 18, userInfo: [NSLocalizedDescriptionKey: "Missing bundled Python.framework in runtime."])
        }

        let versionsDir = frameworkDir.appendingPathComponent("Versions", isDirectory: true)
        let versionDirs = try FileManager.default.contentsOfDirectory(at: versionsDir, includingPropertiesForKeys: [.isDirectoryKey])
            .filter { $0.lastPathComponent != "Current" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        guard let versionDir = versionDirs.last else {
            throw NSError(domain: "Odysseus", code: 19, userInfo: [NSLocalizedDescriptionKey: "Missing Python framework version directory in runtime."])
        }
        return versionDir
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var overlay: NSVisualEffectView!
    private var statusLabel: NSTextField!
    private var backend: BackendController?
    private var runtimeConfig: RuntimeConfig?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        installMenuBar()
        buildWindow()
        startRuntime()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        backend?.stop()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
        return true
    }

    private func installMenuBar() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)

        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "Quit Odysseus", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        mainMenu.addItem(editItem)

        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }

    private func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1280, height: 900)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Odysseus"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.center()
        window.delegate = self

        let contentView = NSView(frame: rect)
        contentView.wantsLayer = true
        contentView.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        window.contentView = contentView

        webView = WKWebView(frame: contentView.bounds)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        contentView.addSubview(webView)

        overlay = NSVisualEffectView(frame: contentView.bounds)
        overlay.autoresizingMask = [.width, .height]
        overlay.material = .windowBackground
        overlay.blendingMode = .withinWindow
        overlay.state = .active
        contentView.addSubview(overlay)

        let stack = NSStackView()
        stack.translatesAutoresizingMaskIntoConstraints = false
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 12

        let spinner = NSProgressIndicator()
        spinner.translatesAutoresizingMaskIntoConstraints = false
        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.startAnimation(nil)

        statusLabel = NSTextField(labelWithString: "Starting Odysseus…")
        statusLabel.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        statusLabel.alignment = .center

        stack.addArrangedSubview(spinner)
        stack.addArrangedSubview(statusLabel)
        overlay.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: overlay.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: overlay.centerYAnchor)
        ])

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func startRuntime() {
        statusLabel.stringValue = "Preparing runtime…"

        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let config = try RuntimeConfig.load()
                let installer = RuntimeInstaller(
                    bundleRuntimeURL: config.bundleRuntimeURL,
                    supportRuntimeURL: config.supportRuntimeURL,
                    bundleVersion: config.bundleVersion
                )
                let (runtimeURL, setupOutput) = try installer.prepare()

                let backend = BackendController(runtimeURL: runtimeURL, port: config.port, serverURL: config.serverURL)
                DispatchQueue.main.async {
                    self.runtimeConfig = config
                    self.backend = backend
                    self.statusLabel.stringValue = "Starting Odysseus…"
                    backend.startIfNeeded { result in
                        DispatchQueue.main.async {
                            switch result {
                            case .success:
                                self.overlay.isHidden = true
                                self.webView.load(URLRequest(url: config.serverURL))
                                if let setupOutput {
                                    self.presentSetupNotice(setupOutput)
                                }
                            case .failure(let error):
                                self.showFatalError(error.localizedDescription)
                            }
                        }
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    self.showFatalError(error.localizedDescription)
                }
            }
        }
    }

    private func showFatalError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Odysseus failed to start"
        alert.informativeText = message
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Quit")
        alert.runModal()
        NSApp.terminate(nil)
    }

    private func presentSetupNotice(_ output: String) {
        let lines = output
            .split(separator: "\n")
            .map(String.init)
            .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            .filter {
                $0.contains("Initial admin user created") ||
                $0.contains("Temporary password:") ||
                $0.contains("Login with your admin credentials.")
            }

        guard !lines.isEmpty else { return }

        let alert = NSAlert()
        alert.messageText = "Odysseus is ready"
        alert.informativeText = lines.joined(separator: "\n")
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        overlay.isHidden = true
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        statusLabel.stringValue = error.localizedDescription
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        statusLabel.stringValue = error.localizedDescription
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
