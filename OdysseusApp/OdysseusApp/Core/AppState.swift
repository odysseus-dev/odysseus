import Foundation
import Combine

enum LaunchPhase {
    case idle
    case checkingDependencies
    case needsInstallPermission
    case installingDependencies
    case checkingSetup
    case needsFirstTimeSetup
    case runningSetup
    case startingServer
    case waitingForHealth
    case ready(port: Int)
    case error(String)
}

@MainActor
final class AppState: ObservableObject {
    @Published var phase: LaunchPhase = .idle
    @Published var statusMessage: String = "Initializing…"
    @Published var logLines: [LogEntry] = []
    @Published var pendingDependencies: [PendingDependency] = []

    @Published var repoPath: String {
        didSet { scheduleSave { UserDefaults.standard.set(self.repoPath, forKey: "odysseusRepoPath") } }
    }
    @Published var preferredPort: Int {
        didSet { scheduleSave { UserDefaults.standard.set(self.preferredPort, forKey: "odysseusPort") } }
    }
    @Published var lanAccess: Bool {
        didSet { scheduleSave { UserDefaults.standard.set(self.lanAccess, forKey: "odysseusLanAccess") } }
    }

    var serverPort: Int = 7860
    private var flushSaveWorkItem: DispatchWorkItem?

    init() {
        let savedPath = UserDefaults.standard.string(forKey: "odysseusRepoPath")
        self.repoPath = savedPath ?? AppState.defaultRepoPath()
        let savedPort = UserDefaults.standard.integer(forKey: "odysseusPort")
        self.preferredPort = savedPort > 0 ? savedPort : 7860
        self.lanAccess = UserDefaults.standard.bool(forKey: "odysseusLanAccess")
    }

    private static func defaultRepoPath() -> String {
        var candidate = Bundle.main.bundleURL.deletingLastPathComponent()
        for _ in 0..<6 {
            if FileManager.default.fileExists(
                atPath: candidate.appendingPathComponent("setup.py").path
            ) {
                return candidate.path
            }
            candidate = candidate.deletingLastPathComponent()
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("odysseus").path
    }

    func transition(to newPhase: LaunchPhase) {
        phase = newPhase
        switch newPhase {
        case .idle:                     statusMessage = "Initializing…"
        case .checkingDependencies:     statusMessage = "Checking dependencies…"
        case .needsInstallPermission:   statusMessage = "Setup required"
        case .installingDependencies:   statusMessage = "Installing dependencies…"
        case .checkingSetup:            statusMessage = "Checking environment…"
        case .needsFirstTimeSetup:      statusMessage = "First-time setup"
        case .runningSetup:             statusMessage = "Running first-time setup…"
        case .startingServer:           statusMessage = "Starting server…"
        case .waitingForHealth:         statusMessage = "Waiting for server…"
        case .ready:                    statusMessage = "Ready"
        case .error(let msg):           statusMessage = msg
        }
    }

    func appendLog(_ line: String) {
        logLines.append(LogEntry(text: line))
        if logLines.count > 500 { logLines.removeFirst() }
    }

    private func scheduleSave(block: @escaping () -> Void) {
        flushSaveWorkItem?.cancel()
        let work = DispatchWorkItem(block: block)
        flushSaveWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5, execute: work)
    }
}

struct LogEntry: Identifiable {
    let id = UUID()
    let text: String
}
