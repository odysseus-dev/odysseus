import SwiftUI
import AppKit

struct MenuBarContent: View {
    @EnvironmentObject var state: AppState
    @ObservedObject var controller: LifecycleController
    @ObservedObject var settings: SettingsStore

    var body: some View {
        Text("Odysseus — \(state.phaseSummary)")
        Text(sourceLabel).font(.caption)
        if case .running = state.phase {
            Button("Open Window") { focusMainWindow() }
            Button("Open in Browser") { NSWorkspace.shared.open(Config.webURL) }
        }
        if state.updateAvailable && settings.settings.sourceMode == .latest {
            Divider()
            Button("Update Available — Apply Now") {
                Task { await controller.applyUpdate(state: state, settings: settings) }
            }
            .disabled(isBusy)
        }
        Divider()
        Button("Start")   { Task { await controller.start(state: state, settings: settings) } }
            .disabled(isBusy)
        Button("Restart") { Task { await controller.restart(state: state) } }
            .disabled(!isRunning)
        Button("Stop")    { Task { await controller.stop(state: state) } }
            .disabled(!isRunning)
        Divider()
        Button("Preferences…")          { state.showingPreferences = true }
        Button("View Logs…")           { ComposeManager.openLogs() }
        Button("Show Support Folder")  { ComposeManager.openSupportDir() }
        if settings.settings.sourceMode == .latest {
            Button("Check for Updates Now") { Task { await controller.pollForUpdates(state: state) } }
                .disabled(isBusy)
        }
        Divider()
        Button("Quit Odysseus") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    private var sourceLabel: String {
        switch settings.settings.sourceMode {
        case .latest: return "Source: latest from main"
        case .pinned: return "Source: pinned to \(settings.settings.pinnedRef ?? "—")"
        case .dev:    return "Source: developer mode (local)"
        }
    }

    private var isRunning: Bool { if case .running = state.phase { return true } else { return false } }
    private var isBusy: Bool {
        switch state.phase {
        case .bootstrapping, .starting: return true
        default: return false
        }
    }

    private func focusMainWindow() {
        NSApp.activate(ignoringOtherApps: true)
        NSApp.windows.first { $0.title == "Odysseus" }?.makeKeyAndOrderFront(nil)
    }
}
