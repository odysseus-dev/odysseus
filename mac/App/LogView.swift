import SwiftUI

struct LogView: View {
    @ObservedObject var manager = ServerManager.shared
    @State private var searchText = ""
    @State private var autoScroll = true
    
    var filteredLogs: String {
        if searchText.isEmpty {
            return manager.logs
        }
        return manager.logs.split(separator: "\n")
            .filter { $0.localizedCaseInsensitiveContains(searchText) }
            .joined(separator: "\n")
    }
    
    var body: some View {
        VStack(spacing: 0) {
            // Toolbar
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(.gray)
                TextField("Search logs...", text: $searchText)
                    .textFieldStyle(PlainTextFieldStyle())
                    .frame(maxWidth: 250)
                
                Spacer()
                
                Toggle("Auto-scroll", isOn: $autoScroll)
                    .toggleStyle(.checkbox)
                
                Button("Copy Logs") {
                    let pasteboard = NSPasteboard.general
                    pasteboard.declareTypes([.string], owner: nil)
                    pasteboard.setString(manager.logs, forType: .string)
                }
                .buttonStyle(BorderedButtonStyle())
                
                Button("Clear Console") {
                    manager.logs = "--- Log cleared ---\n"
                }
                .buttonStyle(BorderedButtonStyle())
            }
            .padding(10)
            .background(Color(NSColor.windowBackgroundColor))
            
            Divider()
            
            // Console display
            ScrollViewReader { proxy in
                ScrollView {
                    Text(filteredLogs)
                        .font(.system(.body, design: .monospaced))
                        .foregroundColor(.green)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .id("bottom")
                }
                .background(Color.black)
                .onChange(of: manager.logs) { _ in
                    if autoScroll {
                        withAnimation {
                            proxy.scrollTo("bottom", anchor: .bottom)
                        }
                    }
                }
                .onAppear {
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
            }
        }
        .frame(minWidth: 600, minHeight: 400)
    }
}
