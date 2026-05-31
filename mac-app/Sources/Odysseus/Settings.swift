import Foundation

/// User-visible source selection. Drives every git decision in
/// LifecycleController: which ref to checkout, whether to poll for
/// updates, whether to touch the working tree at all.
enum SourceMode: String, Codable, CaseIterable, Identifiable {
    case latest    // Track origin/<branch>, auto-check for updates.
    case pinned    // Stay on a specific tag/branch, no auto-updates.
    case dev       // Local working copy in support dir; no git ops.

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .latest: return "Latest release"
        case .pinned: return "Pinned version"
        case .dev:    return "Developer mode"
        }
    }

    var explainer: String {
        switch self {
        case .latest: return "Track the main branch and surface an in-app update prompt whenever new commits land."
        case .pinned: return "Stay on a specific tagged version. No auto-updates — you switch when you're ready."
        case .dev:    return "Don't touch the support folder's repo. Edit code locally, hit Restart, your changes run."
        }
    }
}

/// Persisted user preferences. Read on launch, rewritten whenever the
/// user changes a preference via Welcome or Preferences. Anything that
/// might evolve across releases should default to a sensible value when
/// decoded from an older JSON shape.
struct Settings: Codable, Equatable {
    var sourceMode: SourceMode
    /// Only consulted when sourceMode == .pinned.
    var pinnedRef: String?
    /// Bumped on first save so we can detect "needs welcome" vs "stale settings".
    var schemaVersion: Int

    static let current = Settings(sourceMode: .latest, pinnedRef: nil, schemaVersion: 1)
}
