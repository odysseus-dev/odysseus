import SwiftUI

struct ContentView: View {
    @ObservedObject var manager = ServerManager.shared
    
    @State private var isLoading = false
    @State private var canGoBack = false
    @State private var canGoForward = false
    @State private var reloadTrigger = false
    @State private var showSettings = false
    @State private var showLogs = false
    
    var body: some View {
        VStack(spacing: 0) {
            // Navigation Bar / Control Bar
            HStack(spacing: 12) {
                // Navigation controls
                Button(action: { canGoBack.toggle() }) {
                    Image(systemName: "chevron.left")
                }
                .disabled(!canGoBack)
                .buttonStyle(PlainButtonStyle())
                
                Button(action: { canGoForward.toggle() }) {
                    Image(systemName: "chevron.right")
                }
                .disabled(!canGoForward)
                .buttonStyle(PlainButtonStyle())
                
                Button(action: { reloadTrigger = true }) {
                    Image(systemName: "arrow.clockwise")
                }
                .disabled(!manager.isRunning)
                .buttonStyle(PlainButtonStyle())
                
                // Loading spinner / status dot
                if isLoading {
                    ProgressView()
                        .controlSize(.small)
                        .scaleEffect(0.7)
                } else {
                    Circle()
                        .fill(manager.isRunning ? Color.green : (manager.isStarting ? Color.orange : Color.red))
                        .frame(width: 8, height: 8)
                }
                
                Text(manager.isRunning ? "Odysseus Connected" : (manager.isStarting ? "Starting server..." : "Disconnected"))
                    .font(.caption)
                    .foregroundColor(.secondary)
                
                Spacer()
                
                // Extra options
                Button(action: { showLogs.toggle() }) {
                    Label("Logs", systemImage: "terminal")
                }
                .buttonStyle(BorderedButtonStyle())
                .sheet(isPresented: $showLogs) {
                    VStack {
                        HStack {
                            Text("Odysseus Server Console Logs")
                                .font(.headline)
                            Spacer()
                            Button("Close") {
                                showLogs = false
                            }
                        }
                        .padding([.top, .leading, .trailing])
                        LogView()
                    }
                }
                
                Button(action: { showSettings.toggle() }) {
                    Image(systemName: "gearshape")
                }
                .buttonStyle(BorderedButtonStyle())
                .sheet(isPresented: $showSettings) {
                    SettingsView()
                }
            }
            .padding(10)
            .background(Color(NSColor.windowBackgroundColor))
            
            Divider()
            
            // Web View or Offline State
            ZStack {
                if manager.isRunning {
                    WebView(
                        url: URL(string: "http://127.0.0.1:\(manager.port)")!,
                        isLoading: $isLoading,
                        canGoBack: $canGoBack,
                        canGoForward: $canGoForward,
                        reloadTrigger: $reloadTrigger
                    )
                    .transition(.opacity)
                } else {
                    // Beautiful glassmorphism offline view
                    VStack(spacing: 20) {
                        Image(systemName: "antenna.radiowaves.left.and.right.slash")
                            .font(.system(size: 64))
                            .foregroundColor(.secondary)
                        
                        Text("Odysseus Server is Offline")
                            .font(.title)
                            .bold()
                        
                        Text("The local AI workspace server needs to be running to access the application.")
                            .font(.body)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                            .frame(maxWidth: 400)
                        
                        if manager.isStarting {
                            VStack(spacing: 10) {
                                ProgressView()
                                Text("Starting python server processes...")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        } else {
                            HStack(spacing: 15) {
                                Button("Start Server") {
                                    manager.startServer()
                                }
                                .buttonStyle(BorderedProminentButtonStyle())
                                .keyboardShortcut(.defaultAction)
                                
                                Button("Configure Paths") {
                                    showSettings = true
                                }
                                .buttonStyle(BorderedButtonStyle())
                            }
                        }
                        
                        if let error = manager.error {
                            Text(error)
                                .font(.caption)
                                .foregroundColor(.red)
                                .padding(10)
                                .background(Color.red.opacity(0.1))
                                .cornerRadius(8)
                                .padding(.top, 10)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color(NSColor.windowBackgroundColor))
                    .transition(.opacity)
                }
            }
        }
        .frame(minWidth: 900, minHeight: 600)
    }
}
