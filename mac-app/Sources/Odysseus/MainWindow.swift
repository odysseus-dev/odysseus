import AppKit
import SwiftUI

/// Top-level window. Renders one of three states:
///   - .running                → embedded Odysseus web UI
///   - .stopped                → idle landing screen with a Start button
///   - bootstrapping/starting/failed → spinner overlay with status + retry
struct MainWindow: View {
    @EnvironmentObject var state: AppState
    @EnvironmentObject var controller: LifecycleController
    @EnvironmentObject var settings: SettingsStore
    @State private var reloadToken = 0

    var body: some View {
        Group {
            switch state.phase {
            case .running:  runningView
            case .stopped:  StoppedView()
            default:        progressView
            }
        }
        .frame(minWidth: 1100, minHeight: 720)
        .onChange(of: state.reloadToken) { newValue in reloadToken = newValue }
        .sheet(isPresented: Binding(
            get: { !state.pendingConflicts.isEmpty },
            set: { _ in }
        )) {
            PortConflictView(conflicts: state.pendingConflicts) { choices in
                state.resolvePortPrompt(choices)
            }
        }
    }

    private var runningView: some View {
        WebView(url: Config.webURL, reloadToken: $reloadToken)
    }

    private var progressView: some View {
        ZStack {
            Color(.windowBackgroundColor)
            VStack(spacing: 16) {
                ProgressView().controlSize(.large)
                Text("Odysseus").font(.title2).bold()
                Text(state.statusMessage)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: 420)
                if case .failed(let detail) = state.phase {
                    ScrollView {
                        Text(detail).font(.system(.caption, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(8)
                    }
                    .frame(maxWidth: 480, maxHeight: 160)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                    Button("Try Again") {
                        Task { await controller.start(state: state, settings: settings) }
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
            .padding(40)
        }
    }
}

/// Shown when the user has stopped the stack. Big logo + Start button +
/// the same secondary actions surfaced in the menu bar.
private struct StoppedView: View {
    @EnvironmentObject var state: AppState
    @EnvironmentObject var controller: LifecycleController
    @EnvironmentObject var settings: SettingsStore

    var body: some View {
        ZStack {
            Color(.windowBackgroundColor)
            VStack(spacing: 28) {
                // App icon doubles as our brand mark — keeps the dock icon
                // and the in-app logo in sync without a second asset.
                if let appIcon = NSImage(named: "AppIcon") ?? NSApp.applicationIconImage {
                    Image(nsImage: appIcon)
                        .resizable()
                        .interpolation(.high)
                        .frame(width: 160, height: 160)
                        .clipShape(RoundedRectangle(cornerRadius: 28))
                        .shadow(color: .black.opacity(0.25), radius: 14, y: 6)
                }
                VStack(spacing: 6) {
                    Text("Odysseus").font(.system(size: 40, weight: .semibold))
                    Text("Stopped").foregroundStyle(.secondary)
                }
                Button {
                    Task { await controller.start(state: state, settings: settings) }
                } label: {
                    Label("Start Odysseus", systemImage: "play.fill")
                        .frame(minWidth: 160)
                }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)

                HStack(spacing: 16) {
                    Button("View Logs") { ComposeManager.openLogs() }
                    Button("Show Support Folder") { ComposeManager.openSupportDir() }
                }
                .buttonStyle(.link)
                .font(.callout)
            }
        }
    }
}
