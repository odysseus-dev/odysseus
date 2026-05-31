import AppKit
import SwiftUI

/// First-run flow. Shown until `SettingsStore.needsWelcome` flips to false.
/// Asks the one question that actually matters (where to get the code from),
/// then hands off to the normal bootstrap.
struct WelcomeView: View {
    @EnvironmentObject var settings: SettingsStore
    @EnvironmentObject var state: AppState
    @EnvironmentObject var controller: LifecycleController

    @State private var draft: Settings = .current
    @State private var refs: [VersionManager.Ref] = []

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                Divider()
                Text("How should Odysseus stay up to date?")
                    .font(.headline)
                PreferencesForm(draft: $draft, availableRefs: refs, currentRef: "")
                Divider()
                HStack {
                    Spacer()
                    Button {
                        commitAndStart()
                    } label: {
                        Label("Get Started", systemImage: "arrow.right")
                            .frame(minWidth: 140)
                    }
                    .controlSize(.large)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                }
            }
            .padding(40)
            .frame(maxWidth: 720)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.windowBackgroundColor))
        .task { await loadRefs() }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 18) {
            if let icon = NSImage(named: "AppIcon") ?? NSApp.applicationIconImage {
                Image(nsImage: icon)
                    .resizable()
                    .frame(width: 88, height: 88)
                    .clipShape(RoundedRectangle(cornerRadius: 18))
                    .shadow(color: .black.opacity(0.2), radius: 8, y: 4)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Welcome to Odysseus").font(.system(size: 28, weight: .semibold))
                Text("A self-hosted AI workspace running on your Mac via Docker.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func loadRefs() async {
        // Refs are only meaningful in pinned mode, but loading them up front
        // means the picker is responsive the moment the user clicks Pinned.
        // Clone-on-demand happens here too if the repo doesn't exist yet
        // (cheap shallow clone, ~5s on broadband).
        if !GitManager.repoExists, draft.sourceMode != .dev {
            _ = await Task.detached { GitManager.ensureCloned(progress: { _ in }) }.value
        }
        refs = await Task.detached { VersionManager.availableRefs() }.value
    }

    private func commitAndStart() {
        settings.update {
            $0.sourceMode = draft.sourceMode
            $0.pinnedRef = draft.pinnedRef
        }
        settings.completeWelcome()
        Task { await controller.bootstrap(state: state, settings: settings) }
    }
}
