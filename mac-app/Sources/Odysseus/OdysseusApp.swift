import SwiftUI

@main
struct OdysseusApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var state = AppState()
    @StateObject private var controller = LifecycleController()
    @StateObject private var settings = SettingsStore()
    @State private var showingPreferences = false

    var body: some Scene {
        WindowGroup("Odysseus") {
            Group {
                if settings.needsWelcome {
                    WelcomeView()
                } else {
                    MainWindow()
                        // First non-welcome render kicks off the bootstrap.
                        // (Welcome runs bootstrap itself via Get Started.)
                        .task { await controller.bootstrap(state: state, settings: settings) }
                }
            }
            .environmentObject(state)
            .environmentObject(controller)
            .environmentObject(settings)
            .sheet(isPresented: $showingPreferences) {
                PreferencesView()
                    .environmentObject(state)
                    .environmentObject(controller)
                    .environmentObject(settings)
            }
        }
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Preferences…") { showingPreferences = true }
                    .keyboardShortcut(",")
                Button("Reload Web View") { state.set(.running, "Reloading…") }
                    .keyboardShortcut("r")
            }
        }

        MenuBarExtra {
            MenuBarContent(controller: controller, settings: settings, showPreferences: { showingPreferences = true })
                .environmentObject(state)
        } label: {
            let icon: Image = {
                switch state.phase {
                case .running:  return Image(systemName: "circle.fill")
                case .failed:   return Image(systemName: "exclamationmark.triangle.fill")
                default:        return Image(systemName: "circle.dotted")
                }
            }()
            if state.updateAvailable {
                icon.symbolRenderingMode(.hierarchical)
                    .overlay(alignment: .topTrailing) {
                        Circle().fill(.blue).frame(width: 5, height: 5).offset(x: 2, y: -2)
                    }
            } else {
                icon
            }
        }
    }
}
