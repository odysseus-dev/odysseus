import Foundation
import Darwin

final class ServerManager {
    static let shared = ServerManager()
    private var process: Process?

    private init() {
        atexit { ServerManager.shared.stop() }
        signal(SIGTERM) { _ in ServerManager.shared.stop(); exit(0) }
        signal(SIGHUP)  { _ in ServerManager.shared.stop(); exit(0) }
    }

    func start(appState: AppState) async {
        await stopOnBackgroundThread()
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            DispatchQueue.global(qos: .utility).async { [weak self] in
                self?.killOrphans(around: appState.preferredPort)
                cont.resume()
            }
        }
        await appState.transition(to: .checkingDependencies)

        let repoURL = URL(fileURLWithPath: appState.repoPath)
        let missing = PreflightRunner.shared.check(repoURL: repoURL)

        if !missing.isEmpty {
            await MainActor.run { appState.pendingDependencies = missing }
            await appState.transition(to: .needsInstallPermission)
            return  // PreflightView takes over
        }

        await continueAfterPreflight(appState: appState)
    }

    func continueAfterPreflight(appState: AppState) async {
        await appState.transition(to: .checkingSetup)

        let repoURL = URL(fileURLWithPath: appState.repoPath)
        let pythonPath = repoURL.appendingPathComponent("venv/bin/python3").path

        let needsSetup = !FileManager.default.fileExists(atPath: repoURL.appendingPathComponent("data/app.db").path)
                      || !FileManager.default.fileExists(atPath: repoURL.appendingPathComponent("data/auth.json").path)

        if needsSetup {
            await appState.transition(to: .needsFirstTimeSetup)
            return
        }

        let port = findFreePort(starting: appState.preferredPort)
        await MainActor.run { appState.serverPort = port }

        await launchServer(repoURL: repoURL, pythonPath: pythonPath, port: port, appState: appState)
    }

    func runSetupAndStart(username: String, password: String, appState: AppState) async {
        await appState.transition(to: .runningSetup)

        let repoURL = URL(fileURLWithPath: appState.repoPath)
        let pythonPath = repoURL.appendingPathComponent("venv/bin/python3").path

        let result = await SetupRunner.run(repoURL: repoURL, pythonPath: pythonPath,
                                           username: username, password: password)

        if !result.success {
            await appState.transition(to: .error("First-time setup failed. Check that the venv is complete and try again."))
            return
        }

        let port = findFreePort(starting: appState.preferredPort)
        await MainActor.run { appState.serverPort = port }
        await launchServer(repoURL: repoURL, pythonPath: pythonPath, port: port, appState: appState)
    }

    private func launchServer(repoURL: URL, pythonPath: String, port: Int, appState: AppState) async {
        let host = appState.lanAccess ? "0.0.0.0" : "127.0.0.1"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: pythonPath)
        p.arguments = ["-m", "uvicorn", "app:app", "--host", host, "--port", "\(port)"]
        p.currentDirectoryURL = repoURL

        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["ODYSSEUS_SKIP_RUN_HINT"] = "1"
        env["APP_PORT"] = "\(port)"
        env["APP_BIND"] = host
        p.environment = env

        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        self.process = p

        pipe.fileHandleForReading.readabilityHandler = { [weak appState] fh in
            let data = fh.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            let lines = text.components(separatedBy: "\n").filter { !$0.isEmpty }
            Task { @MainActor in lines.forEach { appState?.appendLog($0) } }
        }

        p.terminationHandler = { [weak appState] proc in
            Task { @MainActor in
                guard let appState else { return }
                if case .ready = appState.phase { return }
                appState.transition(to: .error("Server exited unexpectedly (code \(proc.terminationStatus)). Check logs."))
            }
        }

        await appState.transition(to: .startingServer)

        do {
            try p.run()
        } catch {
            await appState.transition(to: .error("Failed to launch server: \(error.localizedDescription)"))
            return
        }

        await HealthPoller.shared.poll(port: port, appState: appState)
    }

    func stop() {
        guard let p = process, p.isRunning else { process = nil; return }
        // Clear handler so intentional stop doesn't trigger an error-state transition
        p.terminationHandler = nil
        let sema = DispatchSemaphore(value: 0)
        p.terminationHandler = { _ in sema.signal() }
        p.interrupt()
        if sema.wait(timeout: .now() + 3) == .timedOut { p.terminate() }
        process = nil
    }

    private func stopOnBackgroundThread() async {
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.stop()
                cont.resume()
            }
        }
    }

    func restart(appState: AppState) {
        Task { await start(appState: appState) }
    }

    private func killOrphans(around preferredPort: Int) {
        let lo = max(1024, preferredPort - 10)
        let hi = preferredPort + 30
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        task.arguments = ["-iTCP:\(lo)-\(hi)", "-sTCP:LISTEN", "-nP", "-t", "-c", "Python", "-a"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice
        try? task.run()
        task.waitUntilExit()

        let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let myPid = ProcessInfo.processInfo.processIdentifier
        let pids = output.components(separatedBy: "\n").compactMap { Int32($0.trimmingCharacters(in: .whitespaces)) }
        for pid in pids where pid != myPid {
            let ps = Process()
            ps.executableURL = URL(fileURLWithPath: "/bin/ps")
            ps.arguments = ["-p", "\(pid)", "-o", "args="]
            let psPipe = Pipe()
            ps.standardOutput = psPipe
            ps.standardError = FileHandle.nullDevice
            try? ps.run()
            ps.waitUntilExit()
            let args = String(data: psPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            if args.contains("uvicorn") && args.contains("app:app") {
                Darwin.kill(pid, SIGTERM)
            }
        }
    }

    private func findFreePort(starting preferred: Int) -> Int {
        let start = max(1024, min(preferred, 65514))
        for port in start...(start + 20) {
            if isPortFree(port) { return port }
        }
        return start
    }

    private func isPortFree(_ port: Int) -> Bool {
        let fd = Darwin.socket(PF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return true }
        defer { Darwin.close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = CFSwapInt16HostToBig(UInt16(port))
        addr.sin_addr.s_addr = INADDR_ANY
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        let result = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return result == 0
    }
}
