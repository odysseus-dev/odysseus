import Foundation

/// Orchestrates startup (runtime check → repo → ports → compose up) and
/// routes menu-bar actions. Now consults `SettingsStore` to decide what
/// to do with git: pull main, sit on a pinned tag, or stay out of the
/// working tree entirely (dev mode).
@MainActor
final class LifecycleController: ObservableObject {
    private var booted = false

    func bootstrap(state: AppState, settings: SettingsStore) async {
        guard !booted else { return }
        booted = true
        await start(state: state, settings: settings)

        // Update poller only makes sense when we're tracking a branch.
        // In pinned/dev mode the user has explicitly opted out of nags.
        if settings.settings.sourceMode == .latest {
            Task.detached { [weak self] in
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                while !Task.isCancelled {
                    await self?.pollForUpdates(state: state)
                    try? await Task.sleep(nanoseconds: 1_800_000_000_000)
                }
            }
        }
    }

    func start(state: AppState, settings: SettingsStore) async {
        // Fast path — already serving, no need to redo any of this.
        if await offMain({ ComposeManager.isServing() }) {
            state.set(.running, "Running at \(Config.webURL.absoluteString)")
            return
        }

        state.set(.bootstrapping, "Checking Docker runtime…")
        if let err = await offMain({ RuntimeManager.bringUp(progress: { msg in Task { @MainActor in state.set(.bootstrapping, msg) } }) }) {
            state.set(.failed(err), "Docker runtime unavailable."); return
        }

        // Repo prep depends on source mode. Dev mode is hands-off; the
        // other two need at minimum a clone so the compose files exist.
        if settings.settings.sourceMode != .dev {
            state.set(.bootstrapping, "Preparing repository…")
            if let err = await offMain({ GitManager.ensureCloned(progress: { msg in Task { @MainActor in state.set(.bootstrapping, msg) } }) }) {
                state.set(.failed(err), "Could not clone repository."); return
            }
            // Pinned mode: enforce the chosen ref on every start in case
            // someone manually changed HEAD or the .app was updated.
            if settings.settings.sourceMode == .pinned, let pinned = settings.settings.pinnedRef {
                let ref = VersionManager.Ref(name: pinned, kind: pinned == Config.defaultBranch ? .branch : .tag)
                if VersionManager.currentRef() != pinned {
                    if let err = await offMain({ VersionManager.checkout(ref, progress: { msg in Task { @MainActor in state.set(.bootstrapping, msg) } }) }) {
                        state.set(.failed(err), "Could not switch to \(pinned)."); return
                    }
                }
            }
        } else {
            // Dev mode: the user is the source of truth. The compose files
            // need to exist somewhere though — bail with a helpful message
            // if they haven't dropped a repo into place yet.
            if !FileManager.default.fileExists(atPath: Config.composeBase.path) {
                state.set(.failed("Dev mode is on but no repo found at \(Config.repoDir.path). Clone it there manually, or switch source mode in Preferences."), "No source tree."); return
            }
        }

        seedEnvFileIfMissing()

        // Probe host ports before compose tries to bind them.
        state.set(.bootstrapping, "Checking host ports…")
        let conflicts = await offMain { PortManager.detectConflicts() }
        if !conflicts.isEmpty {
            state.set(.bootstrapping, "Resolving port conflicts…")
            let choices = await state.awaitPortResolution(conflicts)
            for (key, value) in choices { EnvManager.upsert(key, String(value)) }
        }

        state.set(.starting, "Starting containers…")
        if let err = await offMain({ ComposeManager.start(progress: { msg in Task { @MainActor in state.set(.starting, msg) } }) }) {
            state.set(.failed(err), "Could not start Odysseus."); return
        }

        await waitForHealthy(state: state)
    }

    func stop(state: AppState) async {
        state.set(.bootstrapping, "Stopping containers…")
        if let err = await offMain({ ComposeManager.stop() }) {
            state.set(.failed(err), "Stop failed."); return
        }
        state.set(.stopped, "Stopped.")
    }

    func restart(state: AppState) async {
        state.set(.starting, "Restarting…")
        if let err = await offMain({ ComposeManager.restart() }) {
            state.set(.failed(err), "Restart failed."); return
        }
        await waitForHealthy(state: state)
    }

    /// "Update available" applied — fetch + reset to origin/<branch>, then
    /// re-pull images and restart. Only meaningful in `.latest` mode.
    func applyUpdate(state: AppState, settings: SettingsStore) async {
        state.set(.bootstrapping, "Applying update…")
        if let err = await offMain({ GitManager.applyUpdate(progress: { msg in Task { @MainActor in state.set(.bootstrapping, msg) } }) }) {
            state.set(.failed(err), "Update failed."); return
        }
        state.updateAvailable = false
        await restart(state: state)
    }

    /// Background poll. Cheap (one `git ls-remote HEAD`). No-op outside `.latest`.
    func pollForUpdates(state: AppState) async {
        state.updateAvailable = await offMain { UpdateChecker.hasUpdates() }
    }

    /// User picked a different ref from the picker. Checkout + restart.
    func switchVersion(to ref: VersionManager.Ref, state: AppState, settings: SettingsStore) async {
        state.set(.bootstrapping, "Switching to \(ref.displayName)…")
        if let err = await offMain({ VersionManager.checkout(ref, progress: { msg in Task { @MainActor in state.set(.bootstrapping, msg) } }) }) {
            state.set(.failed(err), "Switch failed."); return
        }
        settings.update { $0.pinnedRef = ref.name }
        await restart(state: state)
    }

    private func seedEnvFileIfMissing() {
        let envFile = Config.envFile
        let example = Config.repoDir.appendingPathComponent(".env.example")
        let fm = FileManager.default
        guard !fm.fileExists(atPath: envFile.path) else { return }
        if fm.fileExists(atPath: example.path) {
            try? fm.copyItem(at: example, to: envFile)
        } else {
            fm.createFile(atPath: envFile.path, contents: Data())
        }
    }

    private func waitForHealthy(state: AppState) async {
        let deadline = Date().addingTimeInterval(90)
        while Date() < deadline {
            if await offMain({ ComposeManager.isServing() }) {
                state.set(.running, "Running at \(Config.webURL.absoluteString)")
                return
            }
            try? await Task.sleep(nanoseconds: 1_500_000_000)
        }
        state.set(.failed("Odysseus didn't become healthy within 90s. Check logs via the menu bar."),
                  "Startup timed out.")
    }

    private func offMain<T: Sendable>(_ work: @Sendable @escaping () -> T) async -> T {
        await Task.detached(priority: .userInitiated) { work() }.value
    }
}
