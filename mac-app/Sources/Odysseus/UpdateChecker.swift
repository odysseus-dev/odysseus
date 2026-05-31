import Foundation

/// Background watcher that asks GitHub whether the tracked branch has
/// commits the user hasn't pulled yet. Reports back via an async callback
/// so the menu bar can light up an "Update available" badge.
///
/// Uses `git ls-remote` — one cheap HEAD-only request, no fetch, no
/// working-tree mutation. Compared to a local `git rev-parse HEAD`.
enum UpdateChecker {
    /// Returns true if origin/<branch> has commits ahead of the local HEAD.
    /// Returns false on any error (offline, repo missing, etc.) so we never
    /// false-positive the badge.
    static func hasUpdates() -> Bool {
        guard GitManager.repoExists, Shell.which("git") != nil else { return false }

        let local = Shell.run("git", ["-C", Config.repoDir.path, "rev-parse", "HEAD"], timeout: 5)
        guard local.ok else { return false }
        let localSHA = local.stdout.trimmingCharacters(in: .whitespacesAndNewlines)

        let remote = Shell.run("git", ["-C", Config.repoDir.path, "ls-remote", "origin", Config.defaultBranch], timeout: 15)
        guard remote.ok, let firstLine = remote.stdout.split(whereSeparator: \.isNewline).first else { return false }
        let remoteSHA = firstLine.split(separator: "\t").first.map(String.init) ?? ""

        return !remoteSHA.isEmpty && remoteSHA != localSHA
    }
}
