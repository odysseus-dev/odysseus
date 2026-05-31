import Foundation

/// Owns the cloned Odysseus repo under ~/Library/Application Support/Odysseus/repo.
///
/// `ensureCloned` is safe to call from multiple call sites concurrently —
/// a class-level lock serializes the actual clone so a fast click-through
/// can't end up with two `git clone` invocations stomping each other (or
/// one call seeing `.git/` exist while files are still being checked out).
enum GitManager {
    /// Held while a clone is in flight. NSLock works across the detached
    /// Tasks that LifecycleController and WelcomeView use for clone work.
    private static let cloneLock = NSLock()

    /// True iff the repo on disk is in a usable, fully-checked-out state.
    /// We check the post-checkout artifacts (HEAD + docker-compose.yml), not
    /// just `.git/`, because git creates `.git/` at the start of a clone and
    /// only writes the working-tree files at the end.
    static var repoExists: Bool {
        let fm = FileManager.default
        return fm.fileExists(atPath: Config.repoDir.appendingPathComponent(".git/HEAD").path)
            && fm.fileExists(atPath: Config.composeBase.path)
    }

    /// Ensure the repo is on disk. Idempotent: a no-op when the working
    /// tree is already complete. Concurrent callers serialize on `cloneLock`.
    static func ensureCloned(progress: @escaping (String) -> Void) -> String? {
        cloneLock.lock()
        defer { cloneLock.unlock() }

        if repoExists { return nil }

        let fm = FileManager.default
        try? fm.createDirectory(at: Config.supportDir, withIntermediateDirectories: true)

        // A partial clone (`.git/` exists but no working tree, or a stale
        // directory) makes `git clone` refuse with "destination path …
        // already exists and is not an empty directory". Clear it first so
        // the second concurrent caller (if any) sees a clean slate.
        if fm.fileExists(atPath: Config.repoDir.path) {
            try? fm.removeItem(at: Config.repoDir)
        }

        guard Shell.which("git") != nil else {
            return "git not found — install Xcode Command Line Tools (`xcode-select --install`)."
        }
        progress("Cloning Odysseus repository…")
        let res = Shell.run("git", [
            "clone", "--depth", "1",
            "--branch", Config.defaultBranch,
            Config.repoURL, Config.repoDir.path,
        ], timeout: 300)
        if !res.ok { return "Clone failed:\n\(res.stderr)" }
        return repoExists ? nil : "Clone reported success but the working tree is empty."
    }

    /// Apply pending updates: fetch + fast-forward. Caller is responsible
    /// for restarting the stack afterwards.
    static func applyUpdate(progress: @escaping (String) -> Void) -> String? {
        guard repoExists else { return ensureCloned(progress: progress) }
        progress("Fetching latest…")
        let fetch = Shell.run("git", ["-C", Config.repoDir.path, "fetch", "--depth", "1", "origin", Config.defaultBranch], timeout: 60)
        if !fetch.ok { return "Fetch failed (offline?):\n\(fetch.stderr)" }
        let reset = Shell.run("git", ["-C", Config.repoDir.path, "reset", "--hard", "origin/\(Config.defaultBranch)"], timeout: 30)
        return reset.ok ? nil : "Could not fast-forward:\n\(reset.stderr)"
    }
}
