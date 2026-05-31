import SwiftUI

@main
struct OdysseusApp: App {
    @StateObject private var manager = ServerManager.shared
    
    init() {
        // Start server on startup if enabled
        if ServerManager.shared.autostart {
            ServerManager.shared.startServer()
        }
    }
    
    var body: some Scene {
        WindowGroup("Odysseus Workspace") {
            ContentView()
                .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in
                    // Make sure server stops when app closes
                    ServerManager.shared.stopServer()
                }
        }
        
        MenuBarExtra("Odysseus", systemImage: "ship.fill") {
            Button(manager.isRunning ? "Status: Connected (Port \(manager.port))" : (manager.isStarting ? "Status: Starting..." : "Status: Disconnected")) {
                // Status label
            }
            .disabled(true)
            
            Divider()
            
            Button("Start Server") {
                manager.startServer()
            }
            .disabled(manager.isRunning || manager.isStarting)
            
            Button("Stop Server") {
                manager.stopServer()
            }
            .disabled(!manager.isRunning)
            
            Divider()
            
            Button("Open Web Interface (Browser)") {
                if let url = URL(string: "http://127.0.0.1:\(manager.port)") {
                    NSWorkspace.shared.open(url)
                }
            }
            
            Divider()
            
            Button("Quit Odysseus") {
                manager.stopServer()
                NSApp.terminate(nil)
            }
        }
    }
}
