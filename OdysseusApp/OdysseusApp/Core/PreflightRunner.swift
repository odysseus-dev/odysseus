import Foundation

struct PendingDependency: Identifiable {
    let id = UUID()
    let name: String
    let detail: String
    let canAutoInstall: Bool
    let manualCommand: String?
}

final class PreflightRunner {
    static let shared = PreflightRunner()

    var brewPath: String? {
        ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"].first {
            FileManager.default.isExecutableFile(atPath: $0)
        }
    }

    func findPython() -> String? {
        #if arch(arm64)
        let candidates = [
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3.11",
        ]
        #else
        let candidates = [
            "/usr/local/bin/python3.13",
            "/usr/local/bin/python3.12",
            "/usr/local/bin/python3.11",
            "/usr/bin/python3",
        ]
        #endif
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    func check(repoURL: URL) -> [PendingDependency] {
        var deps: [PendingDependency] = []
        let hasBrew   = brewPath != nil
        let hasPython = findPython() != nil
        let hasVenv   = FileManager.default.isExecutableFile(
            atPath: repoURL.appendingPathComponent("venv/bin/python3").path
        )

        if !hasPython {
            if !hasBrew {
                deps.append(PendingDependency(
                    name: "Homebrew",
                    detail: "Paste this into Terminal, then relaunch",
                    canAutoInstall: false,
                    manualCommand: "/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
                ))
            }
            deps.append(PendingDependency(
                name: "Python 3.11",
                detail: hasBrew ? "Will be installed via Homebrew (~2 min)" : "Run after installing Homebrew",
                canAutoInstall: hasBrew,
                manualCommand: hasBrew ? nil : "brew install python@3.11"
            ))
        }

        if !hasVenv {
            deps.append(PendingDependency(
                name: "Python packages",
                detail: "First install takes a few minutes",
                canAutoInstall: hasPython || hasBrew,
                manualCommand: nil
            ))
        }

        return deps
    }

    func install(repoURL: URL, appState: AppState) async -> Bool {
        if findPython() == nil {
            guard let brew = brewPath else {
                await appState.appendLog("✗ Homebrew not found. Install it first, then relaunch.")
                return false
            }
            await appState.appendLog("▶ Installing Python 3.11 via Homebrew…")
            guard await run([brew, "install", "python@3.11"], appState: appState) else {
                await appState.appendLog("✗ Python installation failed.")
                return false
            }
        }

        guard let python = findPython() else {
            await appState.appendLog("✗ Python 3.11+ not found after installation.")
            return false
        }

        let venvPath   = repoURL.appendingPathComponent("venv").path
        let venvPython = repoURL.appendingPathComponent("venv/bin/python3").path

        if !FileManager.default.isExecutableFile(atPath: venvPython) {
            await appState.appendLog("▶ Creating Python environment…")
            guard await run([python, "-m", "venv", venvPath], appState: appState) else {
                await appState.appendLog("✗ Failed to create Python environment.")
                return false
            }
        }

        let req = repoURL.appendingPathComponent("requirements.txt").path
        await appState.appendLog("▶ Installing Python packages — this may take a few minutes…")
        guard await run([venvPython, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
                        appState: appState) else { return false }
        guard await run([venvPython, "-m", "pip", "install", "-r", req],
                        workingDir: repoURL, appState: appState) else {
            await appState.appendLog("✗ Package installation failed.")
            return false
        }

        await appState.appendLog("✓ All dependencies ready.")
        return true
    }

    private func run(_ args: [String], workingDir: URL? = nil, appState: AppState) async -> Bool {
        await withCheckedContinuation { continuation in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: args[0])
            p.arguments = Array(args.dropFirst())
            if let dir = workingDir { p.currentDirectoryURL = dir }

            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe

            pipe.fileHandleForReading.readabilityHandler = { fh in
                let data = fh.availableData
                guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
                let lines = text.components(separatedBy: "\n").filter { !$0.isEmpty }
                Task { @MainActor in lines.forEach { appState.appendLog($0) } }
            }

            p.terminationHandler = { proc in
                pipe.fileHandleForReading.readabilityHandler = nil
                continuation.resume(returning: proc.terminationStatus == 0)
            }

            do {
                try p.run()
            } catch {
                Task { @MainActor in appState.appendLog("✗ \(error.localizedDescription)") }
                continuation.resume(returning: false)
            }
        }
    }
}
