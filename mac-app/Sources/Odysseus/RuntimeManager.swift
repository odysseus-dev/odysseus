import Foundation

/// Ensures a working Docker engine is available. Two supported runtimes:
///  - Docker Desktop (if the user already has it)
///  - Colima (auto-installed via Homebrew if neither runtime is present)
///
/// We deliberately don't ship our own runtime: bundling a VM image inside the
/// .app would inflate it to ~500MB and break across macOS upgrades.
enum RuntimeManager {
    enum State {
        case ready
        case needsColimaStart           // Colima installed but VM is stopped
        case needsBrewInstall           // Homebrew present, Colima missing
        case needsHomebrew              // No Homebrew, no runtime — manual step
    }

    static func probe() -> State {
        if Shell.run("docker", ["info"], timeout: 5).ok { return .ready }
        if Shell.which("colima") != nil { return .needsColimaStart }
        if Shell.which("brew") != nil { return .needsBrewInstall }
        return .needsHomebrew
    }

    /// Bring the runtime up to a usable state, reporting progress messages.
    /// Returns nil on success or a human-readable error string on failure.
    static func bringUp(progress: @escaping (String) -> Void) -> String? {
        switch probe() {
        case .ready:
            return nil

        case .needsColimaStart:
            progress("Starting Colima VM (this can take ~30s on first start)…")
            let res = Shell.run("colima", ["start", "--cpu", "2", "--memory", "4"], timeout: 180)
            if !res.ok { return "colima start failed:\n\(res.stderr)" }
            return Shell.run("docker", ["info"], timeout: 10).ok ? nil : "Docker still unreachable after colima start."

        case .needsBrewInstall:
            progress("Installing Colima + Docker CLI via Homebrew…")
            let install = Shell.run("brew", ["install", "colima", "docker", "docker-compose"], timeout: 600)
            if !install.ok { return "brew install failed:\n\(install.stderr)" }
            progress("Starting Colima VM…")
            let start = Shell.run("colima", ["start", "--cpu", "2", "--memory", "4"], timeout: 180)
            if !start.ok { return "colima start failed:\n\(start.stderr)" }
            return nil

        case .needsHomebrew:
            return """
            No Docker runtime found and Homebrew isn't installed.

            Install Homebrew first from https://brew.sh, then reopen Odysseus.
            (Or install Docker Desktop manually if you prefer.)
            """
        }
    }
}
