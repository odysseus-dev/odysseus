import SwiftUI

struct SettingsView: View {
    @ObservedObject var manager = ServerManager.shared
    
    @State private var tempProjectPath = ""
    @State private var tempPythonPath = ""
    @State private var tempPort = 7000
    @State private var tempAutostart = true
    
    @Environment(\.presentationMode) var presentationMode
    
    var body: some View {
        Form {
            Section(header: Text("Odysseus Paths").font(.headline)) {
                HStack {
                    Text("Project Directory:")
                        .frame(width: 130, alignment: .trailing)
                    TextField("Path to Odysseus repo root", text: $tempProjectPath)
                        .textFieldStyle(RoundedBorderTextFieldStyle())
                    Button("Browse...") {
                        selectFolder { path in
                            tempProjectPath = path
                        }
                    }
                }
                
                HStack {
                    Text("Python Path:")
                        .frame(width: 130, alignment: .trailing)
                    TextField("Path to Python executable in venv", text: $tempPythonPath)
                        .textFieldStyle(RoundedBorderTextFieldStyle())
                    Button("Browse...") {
                        selectFile { path in
                            tempPythonPath = path
                        }
                    }
                }
            }
            
            Divider()
            
            Section(header: Text("Network & Startup").font(.headline)) {
                HStack {
                    Text("Server Port:")
                        .frame(width: 130, alignment: .trailing)
                    TextField("Port", value: $tempPort, formatter: NumberFormatter())
                        .textFieldStyle(RoundedBorderTextFieldStyle())
                        .frame(width: 100)
                }
                
                Toggle("Auto-start server on app launch", isOn: $tempAutostart)
                    .padding(.leading, 135)
            }
            
            Spacer()
            
            HStack {
                Button("Reset Defaults") {
                    let home = NSHomeDirectory()
                    tempProjectPath = home + "/Downloads/DumbSlut"
                    tempPythonPath = home + "/Downloads/DumbSlut/venv/bin/python"
                    tempPort = 7000
                    tempAutostart = true
                }
                
                Spacer()
                
                Button("Cancel") {
                    presentationMode.wrappedValue.dismiss()
                }
                
                Button("Save Settings") {
                    manager.projectPath = tempProjectPath
                    manager.pythonPath = tempPythonPath
                    manager.port = tempPort
                    manager.autostart = tempAutostart
                    presentationMode.wrappedValue.dismiss()
                    
                    // If the server is running, offer to restart it
                    if manager.isRunning {
                        manager.stopServer()
                        manager.startServer()
                    }
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(BorderedProminentButtonStyle())
            }
            .padding(.top, 15)
        }
        .padding(20)
        .frame(width: 550, height: 280)
        .onAppear {
            tempProjectPath = manager.projectPath
            tempPythonPath = manager.pythonPath
            tempPort = manager.port
            tempAutostart = manager.autostart
        }
    }
    
    private func selectFolder(completion: @escaping (String) -> Void) {
        let openPanel = NSOpenPanel()
        openPanel.canChooseFiles = false
        openPanel.canChooseDirectories = true
        openPanel.allowsMultipleSelection = false
        openPanel.directoryURL = URL(fileURLWithPath: manager.projectPath)
        
        openPanel.begin { response in
            if response == .OK, let url = openPanel.url {
                completion(url.path)
            }
        }
    }
    
    private func selectFile(completion: @escaping (String) -> Void) {
        let openPanel = NSOpenPanel()
        openPanel.canChooseFiles = true
        openPanel.canChooseDirectories = false
        openPanel.allowsMultipleSelection = false
        
        let initialDir = (manager.projectPath as NSString).appendingPathComponent("venv/bin")
        openPanel.directoryURL = URL(fileURLWithPath: initialDir)
        
        openPanel.begin { response in
            if response == .OK, let url = openPanel.url {
                completion(url.path)
            }
        }
    }
}
