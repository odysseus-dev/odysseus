import Foundation
import Combine

/// Shared observable state surfaced in the menu bar and the window's
/// loading overlay. UI reads `phase`/`statusMessage`; backend work
/// publishes updates via `set(...)`. Port-conflict prompts use the
/// async `awaitPortResolution` helper so the controller can pause
/// startup until the user picks alternatives.
@MainActor
final class AppState: ObservableObject {
    enum Phase {
        case bootstrapping        // git pull / runtime checks in progress
        case starting             // docker compose up running
        case running              // odysseus container is up
        case stopped              // user-triggered stop or never started
        case failed(String)       // last operation failed
    }

    @Published private(set) var phase: Phase = .bootstrapping
    @Published private(set) var statusMessage: String = "Starting up…"
    @Published private(set) var reloadToken: Int = 0

    /// Set by UpdateChecker when origin/<branch> is ahead of the local repo.
    /// Drives a small badge on the menu-bar icon + an "Apply update" action.
    @Published var updateAvailable: Bool = false

    /// Toggle this from any view (StoppedView, menu bar, …) to surface
    /// the Preferences sheet. OdysseusApp binds it to the WindowGroup's
    /// `.sheet`. Lives on AppState (rather than each view's own @State)
    /// so all triggers share one source of truth.
    @Published var showingPreferences: Bool = false

    /// Non-empty when the UI should show the port-conflict sheet.
    @Published private(set) var pendingConflicts: [PortManager.Conflict] = []
    private var conflictContinuation: CheckedContinuation<[String: Int], Never>?

    func set(_ phase: Phase, _ message: String? = nil) {
        self.phase = phase
        if let message { self.statusMessage = message }
        if case .running = phase { self.reloadToken += 1 }
    }

    var phaseSummary: String {
        switch phase {
        case .bootstrapping: return "Starting…"
        case .starting:      return "Starting containers…"
        case .running:       return "Running"
        case .stopped:       return "Stopped"
        case .failed:        return "Error"
        }
    }

    /// Suspends the caller until the user resolves the listed conflicts.
    /// Returns a map of envKey -> chosen port. Resolved via `resolvePortPrompt`.
    func awaitPortResolution(_ conflicts: [PortManager.Conflict]) async -> [String: Int] {
        pendingConflicts = conflicts
        return await withCheckedContinuation { cont in
            self.conflictContinuation = cont
        }
    }

    func resolvePortPrompt(_ choices: [String: Int]) {
        pendingConflicts = []
        conflictContinuation?.resume(returning: choices)
        conflictContinuation = nil
    }
}
