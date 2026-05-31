import Foundation

/// Detects host-port collisions before `docker compose up` and proposes
/// alternatives so users can remap interactively instead of staring at a
/// `bind: address already in use` failure.
///
/// We only probe ports the Mac override actually exposes on the host. The
/// internal-only services (chromadb/searxng/ntfy on the docker network)
/// never reach lsof so they're not in this list.
enum PortManager {
    struct Binding {
        let envKey: String      // .env variable to write the chosen port to
        let defaultPort: Int    // value used when the env var is unset
        let label: String       // human-friendly name for the conflict dialog
    }

    struct Conflict: Identifiable, Hashable {
        let envKey: String
        let label: String
        let port: Int
        let occupiedBy: String          // e.g. "ControlCe" (lsof's COMMAND column, truncated as lsof does)
        let suggested: Int              // first free port we found scanning upward from `port`
        var id: String { envKey }
    }

    /// Single source of truth for host ports the Mac install needs.
    /// Add a binding here, parameterize the corresponding port in the
    /// compose files with `${KEY:-default}`, and the conflict UI picks it up.
    static let macBindings: [Binding] = [
        Binding(envKey: "ODYSSEUS_HOST_PORT", defaultPort: 7000, label: "Odysseus web UI"),
    ]

    static func detectConflicts(_ bindings: [Binding] = macBindings) -> [Conflict] {
        bindings.compactMap { b in
            // Honor any previous user choice from .env — if they already
            // remapped to 7001 and 7001 is free (or held by our own docker
            // container), there's no conflict to surface a second time.
            let configured = Int(EnvManager.read(b.envKey) ?? "") ?? b.defaultPort
            if isFree(configured) || isHeldByDocker(configured) { return nil }
            return Conflict(
                envKey: b.envKey,
                label: b.label,
                port: configured,
                occupiedBy: occupant(of: configured),
                suggested: findFree(startingAt: configured)
            )
        }
    }

    /// Treat ports held by Docker Desktop / Colima as "ours" so a healthy
    /// running stack doesn't get flagged as a conflict on the next launch.
    /// The names are what lsof reports for each backend's port-proxy process.
    private static func isHeldByDocker(_ port: Int) -> Bool {
        let cmd = occupant(of: port).lowercased()
        return cmd.contains("docker") || cmd.contains("com.docke") || cmd.contains("qemu") || cmd.contains("vpnkit")
    }

    static func isFree(_ port: Int) -> Bool {
        let res = Shell.run("lsof", ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN"], timeout: 5)
        // lsof exits 1 with empty stdout when nothing is listening.
        return res.stdout.isEmpty
    }

    /// First column of lsof's first data row, or "unknown process" if we
    /// can't parse it. Good enough for a UI hint — users don't need a PID.
    private static func occupant(of port: Int) -> String {
        let res = Shell.run("lsof", ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN"], timeout: 5)
        let lines = res.stdout.split(whereSeparator: \.isNewline)
        guard lines.count >= 2 else { return "unknown process" }
        let cols = lines[1].split(separator: " ", omittingEmptySubsequences: true)
        return cols.first.map(String.init) ?? "unknown process"
    }

    /// Scan upward from `base + 1` for the first free port. Capped at a
    /// short range so a pathologically busy machine doesn't stall startup;
    /// in practice the first or second probe succeeds.
    private static func findFree(startingAt base: Int) -> Int {
        for candidate in (base + 1)...(base + 50) where isFree(candidate) {
            return candidate
        }
        return base + 1
    }
}
