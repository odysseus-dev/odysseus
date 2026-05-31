import Foundation

/// Canonical paths + tunables. Everything that's a constant ("where do we
/// keep things on disk?", "which repo?") lives here so the rest of the
/// codebase doesn't have to know about the support-dir layout.
enum Config {
    static let repoURL = "https://github.com/pewdiepie-archdaemon/odysseus.git"
    static let defaultBranch = "main"
    static let composeProject = "odysseus"

    static var supportDir: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Odysseus", isDirectory: true)
    }

    /// The cloned repo. Data lives at `repo/data` / `repo/logs` (both
    /// gitignored) so version switches via `git checkout` preserve user
    /// data automatically.
    static var repoDir: URL { supportDir.appendingPathComponent("repo", isDirectory: true) }
    static var composeBase: URL { repoDir.appendingPathComponent("docker-compose.yml") }
    static var envFile: URL { repoDir.appendingPathComponent(".env") }

    /// Mac-specific override. Lives in the support dir (NOT the repo) so
    /// `git checkout <old-tag>` to a commit that predates this override
    /// doesn't strand the .app without it.
    static var composeOverride: URL { supportDir.appendingPathComponent("docker-compose.mac.yml") }

    /// Where the .app's settings live (source mode, pinned version, etc.).
    static var settingsFile: URL { supportDir.appendingPathComponent("app-settings.json") }

    /// Host port the WebView and "Open in Browser" target.
    static var webURL: URL {
        let port = EnvManager.read("ODYSSEUS_HOST_PORT") ?? "7000"
        return URL(string: "http://localhost:\(port)")!
    }
}
