import Combine
import Foundation

/// Loads + persists user preferences as JSON in the support dir. Publishes
/// the current `Settings` so SwiftUI views can react to changes.
@MainActor
final class SettingsStore: ObservableObject {
    @Published private(set) var settings: Settings
    /// True iff we have no on-disk settings file yet — the .app shows the
    /// welcome screen instead of bootstrapping until the user picks options.
    @Published private(set) var needsWelcome: Bool

    init() {
        if let loaded = SettingsStore.load() {
            self.settings = loaded
            self.needsWelcome = false
        } else {
            self.settings = .current
            self.needsWelcome = true
        }
    }

    func update(_ mutate: (inout Settings) -> Void) {
        var draft = settings
        mutate(&draft)
        guard draft != settings else { return }
        settings = draft
        persist()
    }

    /// Marks the welcome screen as dismissed and writes the current
    /// settings (typically post-welcome). Idempotent — calling twice
    /// just rewrites the file.
    func completeWelcome() {
        needsWelcome = false
        persist()
    }

    private func persist() {
        try? FileManager.default.createDirectory(at: Config.supportDir, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? encoder.encode(settings) {
            try? data.write(to: Config.settingsFile, options: .atomic)
        }
    }

    private static func load() -> Settings? {
        guard let data = try? Data(contentsOf: Config.settingsFile) else { return nil }
        return try? JSONDecoder().decode(Settings.self, from: data)
    }
}
