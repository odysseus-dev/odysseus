import SwiftUI

/// Source-mode + version picker, used in two places:
///   1. WelcomeView wraps it for the first-run flow.
///   2. The menu bar's Preferences action shows it as a sheet.
///
/// Stays purely declarative — applying changes (checkout, restart, etc.)
/// is the caller's job via `onApply`.
struct PreferencesForm: View {
    @Binding var draft: Settings
    let availableRefs: [VersionManager.Ref]
    let currentRef: String

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            ForEach(SourceMode.allCases) { mode in
                Button {
                    draft.sourceMode = mode
                    if mode != .pinned { draft.pinnedRef = nil }
                    if mode == .pinned && draft.pinnedRef == nil {
                        draft.pinnedRef = availableRefs.first?.name
                    }
                } label: {
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: draft.sourceMode == mode ? "largecircle.fill.circle" : "circle")
                            .foregroundStyle(draft.sourceMode == mode ? Color.accentColor : Color.secondary)
                            .font(.title3)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(mode.displayName).font(.headline)
                            Text(mode.explainer)
                                .font(.callout)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer()
                    }
                    .padding(12)
                    .background(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(draft.sourceMode == mode ? Color.accentColor : Color.secondary.opacity(0.2),
                                    lineWidth: draft.sourceMode == mode ? 1.5 : 1)
                    )
                }
                .buttonStyle(.plain)
            }

            if draft.sourceMode == .pinned {
                pinnedPicker
                    .transition(.opacity)
            }
            if draft.sourceMode == .dev {
                devNotice
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.18), value: draft.sourceMode)
    }

    private var pinnedPicker: some View {
        HStack {
            Text("Version:")
            Picker("", selection: Binding(
                get: { draft.pinnedRef ?? availableRefs.first?.name ?? Config.defaultBranch },
                set: { draft.pinnedRef = $0 }
            )) {
                ForEach(availableRefs) { ref in
                    Text(ref.displayName).tag(ref.name)
                }
            }
            .labelsHidden()
            .frame(maxWidth: 240)
            if !currentRef.isEmpty && currentRef != "—" {
                Text("currently: \(currentRef)")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            Spacer()
        }
        .padding(.leading, 36)
    }

    private var devNotice: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Repo path", systemImage: "folder")
                .font(.caption.bold())
            Text(Config.repoDir.path)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
            Text("Drop your working copy here. The .app won't touch this directory — every Restart just re-runs your code.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button("Open in Finder") { ComposeManager.openSupportDir() }
                .buttonStyle(.link)
        }
        .padding(.leading, 36)
    }
}

/// Sheet wrapper used after the first run for re-editing.
struct PreferencesView: View {
    @EnvironmentObject var settings: SettingsStore
    @EnvironmentObject var state: AppState
    @EnvironmentObject var controller: LifecycleController
    @Environment(\.dismiss) private var dismiss

    @State private var draft: Settings = .current
    @State private var refs: [VersionManager.Ref] = []
    @State private var currentRef: String = "—"

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Preferences").font(.title2).bold()
            PreferencesForm(draft: $draft, availableRefs: refs, currentRef: currentRef)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Apply") {
                    apply()
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(width: 520)
        .task { await load() }
    }

    private func load() async {
        draft = settings.settings
        refs = await Task.detached { VersionManager.availableRefs() }.value
        currentRef = await Task.detached { VersionManager.currentRef() }.value
    }

    private func apply() {
        let prior = settings.settings
        settings.update {
            $0.sourceMode = draft.sourceMode
            $0.pinnedRef = draft.pinnedRef
        }
        // If the source/ref actually changed, kick a restart through the
        // lifecycle so the new mode takes effect.
        if prior.sourceMode != draft.sourceMode || prior.pinnedRef != draft.pinnedRef {
            Task { await controller.start(state: state, settings: settings) }
        }
    }
}
