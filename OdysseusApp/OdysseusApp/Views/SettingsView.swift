import SwiftUI
import AppKit
import Darwin

struct SettingsView: View {
    @EnvironmentObject var appState: AppState
    @State private var networkIPs: (lan: String?, tailscale: String?) = (nil, nil)

    var body: some View {
        Form {
            Section("Repository") {
                HStack {
                    TextField("Path", text: $appState.repoPath)
                        .font(.system(.body, design: .monospaced))
                    Button("Browse…") { browseForRepo() }
                }
                Text("The folder containing setup.py, venv/, and app.py.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Section("Server") {
                HStack {
                    Text("Port")
                    Spacer()
                    TextField("Port", value: $appState.preferredPort, formatter: NumberFormatter())
                        .frame(width: 80)
                        .multilineTextAlignment(.trailing)
                        .onChange(of: appState.preferredPort) {
                            let clamped = max(1024, min(65535, appState.preferredPort))
                            if appState.preferredPort != clamped {
                                appState.preferredPort = clamped
                            }
                        }
                }
            }

            Section("Network Access") {
                Picker("Bind to", selection: $appState.lanAccess) {
                    Text("Localhost only (most secure)").tag(false)
                    Text("LAN / Tailscale (all interfaces)").tag(true)
                }
                .pickerStyle(.radioGroup)

                if appState.lanAccess {
                    let ips = networkIPs

                    if let ip = ips.lan {
                        IPRow(label: "Local WiFi", url: "http://\(ip):\(appState.preferredPort)")
                    }

                    if let ip = ips.tailscale {
                        IPRow(label: "Tailscale", url: "http://\(ip):\(appState.preferredPort)")
                    } else {
                        HStack(spacing: 4) {
                            Image(systemName: "circle.slash")
                                .foregroundColor(.secondary)
                            Text("Tailscale not detected —")
                                .foregroundColor(.secondary)
                            Link("download it", destination: URL(string: "https://tailscale.com/download")!)
                        }
                        .font(.callout)
                    }

                    Text("Anyone on your WiFi can reach these URLs. Your password is still required to log in. Do not expose this to the public internet.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Text("Changes take effect after restarting the server.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Section("Actions") {
                HStack {
                    Button("Restart Server") {
                        appState.logLines = []
                        ServerManager.shared.restart(appState: appState)
                    }

                    Spacer()

                    Button("Open in Browser") {
                        if case .ready(let port) = appState.phase {
                            NSWorkspace.shared.open(URL(string: "http://127.0.0.1:\(port)")!)
                        }
                    }
                    .disabled({
                        if case .ready = appState.phase { return false }
                        return true
                    }())
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 500, height: appState.lanAccess ? 480 : 360)
        .onAppear { networkIPs = detectNetworkIPs() }
        .onChange(of: appState.lanAccess) { networkIPs = detectNetworkIPs() }
    }

    private func browseForRepo() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.title = "Select Odysseus Repository Folder"
        panel.prompt = "Select"
        if panel.runModal() == .OK, let url = panel.url {
            appState.repoPath = url.path
        }
    }
}

private struct IPRow: View {
    let label: String
    let url: String

    var body: some View {
        HStack {
            Text(label)
                .foregroundColor(.secondary)
                .frame(width: 80, alignment: .leading)
            Text(url)
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
            Spacer()
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(url, forType: .string)
            } label: {
                Image(systemName: "doc.on.doc")
            }
            .buttonStyle(.borderless)
            .help("Copy URL")
        }
    }
}

private func detectNetworkIPs() -> (lan: String?, tailscale: String?) {
    var ifaddr: UnsafeMutablePointer<ifaddrs>?
    guard getifaddrs(&ifaddr) == 0 else { return (nil, nil) }
    defer { freeifaddrs(ifaddr) }

    var lan: String? = nil
    var tailscale: String? = nil

    var ptr = ifaddr
    while let current = ptr {
        defer { ptr = current.pointee.ifa_next }
        guard let addr = current.pointee.ifa_addr,
              addr.pointee.sa_family == UInt8(AF_INET) else { continue }

        var hostname = [CChar](repeating: 0, count: Int(NI_MAXHOST))
        getnameinfo(addr, socklen_t(addr.pointee.sa_len),
                    &hostname, socklen_t(hostname.count),
                    nil, 0, NI_NUMERICHOST)
        let ip = String(cString: hostname)
        guard ip != "127.0.0.1" else { continue }

        let parts = ip.split(separator: ".").compactMap { Int($0) }
        if parts.count == 4 && parts[0] == 100 && parts[1] >= 64 && parts[1] < 128 {
            tailscale = ip
        } else if lan == nil {
            lan = ip
        }
    }

    return (lan, tailscale)
}
