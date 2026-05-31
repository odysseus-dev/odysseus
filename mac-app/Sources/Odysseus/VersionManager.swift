import Foundation

/// Enumerates and switches between releases of the Odysseus repo.
///
/// We do as little git work as possible: `ls-remote --tags` for the list
/// (no fetch, no working-tree mutation), and a targeted `git fetch
/// --depth 1 origin <ref>` + `git checkout` when actually switching. This
/// keeps the initial clone shallow and the version dropdown fast even on
/// a slow network.
enum VersionManager {
    struct Ref: Identifiable, Hashable {
        enum Kind: String { case branch, tag }
        let name: String        // e.g. "main", "v1.2.3"
        let kind: Kind
        var id: String { "\(kind.rawValue)/\(name)" }

        var displayName: String {
            switch kind {
            case .branch: return "\(name) (latest)"
            case .tag:    return name
            }
        }
    }

    /// Branches we always offer at the top of the picker, in order.
    private static let pinnedBranches = ["main"]

    /// Available refs the user can pin to. Always includes `main`; tags
    /// come back newest-first per GitHub's default sort. Returns whatever
    /// we have if the network call fails — we'd rather show a stale list
    /// than an empty one.
    static func availableRefs() -> [Ref] {
        var refs: [Ref] = pinnedBranches.map { Ref(name: $0, kind: .branch) }

        guard GitManager.repoExists, Shell.which("git") != nil else { return refs }
        let res = Shell.run("git", ["-C", Config.repoDir.path, "ls-remote", "--tags", "--sort=-v:refname", "origin"], timeout: 15)
        guard res.ok else { return refs }

        for line in res.stdout.split(whereSeparator: \.isNewline) {
            // Format: "<sha>\trefs/tags/<name>"
            let parts = line.split(separator: "\t", maxSplits: 1, omittingEmptySubsequences: true)
            guard parts.count == 2 else { continue }
            let raw = String(parts[1])
            guard raw.hasPrefix("refs/tags/") else { continue }
            var name = String(raw.dropFirst("refs/tags/".count))
            // Skip the peeled-tag dereferences git emits as "<tag>^{}".
            if name.hasSuffix("^{}") { name.removeLast(3) }
            // Dedupe — peeled + tag share the name.
            if !refs.contains(where: { $0.name == name && $0.kind == .tag }) {
                refs.append(Ref(name: name, kind: .tag))
            }
        }
        return refs
    }

    /// Check out the given ref, fetching it first if it's not in the
    /// shallow clone. Returns nil on success, error string otherwise.
    static func checkout(_ ref: Ref, progress: @escaping (String) -> Void) -> String? {
        guard GitManager.repoExists else { return "Repository not present." }
        guard Shell.which("git") != nil else { return "git not found." }

        progress("Fetching \(ref.displayName)…")
        // Fetch the ref explicitly — `--depth 1 origin <ref>` works for
        // both branches and tags and won't bloat the shallow clone.
        let fetchArgs: [String] = [
            "-C", Config.repoDir.path,
            "fetch", "--depth", "1", "--tags", "origin", ref.name,
        ]
        let fetch = Shell.run("git", fetchArgs, timeout: 90)
        if !fetch.ok { return "Fetch failed (offline?):\n\(fetch.stderr)" }

        progress("Switching to \(ref.displayName)…")
        // For branches: reset --hard to FETCH_HEAD so the working tree
        // matches origin/<branch>. For tags: detached HEAD at the tag.
        let checkout = Shell.run("git", ["-C", Config.repoDir.path, "checkout", "-f", "FETCH_HEAD"], timeout: 30)
        if !checkout.ok { return "Checkout failed:\n\(checkout.stderr)" }
        return nil
    }

    /// Current HEAD's symbolic ref or short SHA, for surfacing in the UI.
    static func currentRef() -> String {
        guard GitManager.repoExists else { return "—" }
        let branch = Shell.run("git", ["-C", Config.repoDir.path, "rev-parse", "--abbrev-ref", "HEAD"], timeout: 5)
        let name = branch.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        if name != "HEAD" && !name.isEmpty { return name }
        let sha = Shell.run("git", ["-C", Config.repoDir.path, "rev-parse", "--short", "HEAD"], timeout: 5)
        return sha.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
