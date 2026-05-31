import AppKit
import Foundation

/// Drives `docker compose` against the cloned Odysseus repo.
///
/// We always pass `--project-directory <repoDir>` so relative paths in the
/// base file (which is part of the repo and pins `./data`, `./logs`,
/// `./config/searxng/settings.yml`) resolve inside the repo as the upstream
/// project expects. The Mac override is loaded by absolute path from the
/// support dir so an older checked-out tag doesn't strand it.
enum ComposeManager {
    /// Copy the bundled docker-compose.mac.yml into the support dir if it
    /// isn't there yet (or if the bundled copy is newer). Idempotent and
    /// cheap — safe to call every launch.
    static func syncOverrideFromBundle() {
        guard let bundled = Bundle.main.url(forResource: "docker-compose.mac", withExtension: "yml") else { return }
        let fm = FileManager.default
        try? fm.createDirectory(at: Config.supportDir, withIntermediateDirectories: true)

        // If a copy exists and matches the bundle byte-for-byte, no work to do.
        if let live = try? Data(contentsOf: Config.composeOverride),
           let pkg = try? Data(contentsOf: bundled),
           live == pkg { return }
        // Overwrite — user is not expected to edit the override; advanced
        // tweaks belong in .env or a separate `docker-compose.override.yml`
        // they can place in support dir (we can add support for that later).
        try? fm.removeItem(at: Config.composeOverride)
        try? fm.copyItem(at: bundled, to: Config.composeOverride)
    }

    @discardableResult
    private static func compose(_ args: [String], timeout: TimeInterval = 120) -> Shell.Result {
        let env = ["PUID": String(getuid()), "PGID": String(getgid())]
        let base = [
            "compose",
            "--project-name", Config.composeProject,
            "--project-directory", Config.repoDir.path,
            "--file", Config.composeBase.path,
            "--file", Config.composeOverride.path,
        ]
        return Shell.run("docker", base + args, env: env, timeout: timeout)
    }

    static func start(progress: @escaping (String) -> Void) -> String? {
        guard FileManager.default.fileExists(atPath: Config.composeBase.path) else {
            return "docker-compose.yml not found at \(Config.composeBase.path)."
        }
        syncOverrideFromBundle()
        progress("Pulling images (first run can take several minutes)…")
        _ = compose(["pull"], timeout: 900)
        progress("Starting containers…")
        let res = compose(["up", "-d"], timeout: 300)
        return res.ok ? nil : "docker compose up failed:\n\(res.stderr)"
    }

    static func stop() -> String? {
        let res = compose(["stop"], timeout: 60)
        return res.ok ? nil : res.stderr
    }

    static func restart() -> String? {
        let res = compose(["restart"], timeout: 120)
        return res.ok ? nil : res.stderr
    }

    static func down() -> String? {
        let res = compose(["down"], timeout: 60)
        return res.ok ? nil : res.stderr
    }

    static func isHealthy() -> Bool {
        let res = compose(["ps", "--services", "--filter", "status=running"], timeout: 10)
        return res.ok && res.stdout.split(separator: "\n").contains("odysseus")
    }

    /// Fast-path predicate for "already running, skip the whole bootstrap".
    /// Requires BOTH that our odysseus container is up AND that something is
    /// answering on the configured port — without the container check, an
    /// unrelated process on the default port (AirPlay on :7000 is the
    /// classic) would trigger a false positive and the WebView would happily
    /// load that 403/empty body instead.
    static func isServing() -> Bool {
        guard isHealthy() else { return false }
        let url = Config.webURL.absoluteString
        let res = Shell.run("curl", ["-sS", "-o", "/dev/null", "-m", "2", "-w", "%{http_code}", url], timeout: 4)
        return res.ok && Int(res.stdout) != nil
    }

    static func openLogs() {
        let cmd = "docker compose --project-name \(Config.composeProject) --project-directory '\(Config.repoDir.path)' --file '\(Config.composeBase.path)' --file '\(Config.composeOverride.path)' logs -f"
        let script = """
        tell application "Terminal"
            activate
            do script "\(cmd)"
        end tell
        """
        var err: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&err)
    }

    static func openSupportDir() { NSWorkspace.shared.open(Config.supportDir) }
    static func openRepoDir()    { NSWorkspace.shared.open(Config.repoDir) }
}
