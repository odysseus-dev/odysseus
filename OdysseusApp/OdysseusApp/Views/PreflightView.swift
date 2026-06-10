import SwiftUI

struct PreflightView: View {
    @EnvironmentObject var appState: AppState

    private var allCanAutoInstall: Bool {
        appState.pendingDependencies.allSatisfy { $0.canAutoInstall }
    }

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 28) {
                VStack(spacing: 8) {
                    Image(systemName: "wrench.and.screwdriver")
                        .font(.system(size: 52))
                        .foregroundColor(.accentColor)
                    Text("Setup Required")
                        .font(.largeTitle.bold())
                    Text("Odysseus needs a few things before it can start.")
                        .foregroundColor(.secondary)
                }

                VStack(alignment: .leading, spacing: 16) {
                    ForEach(appState.pendingDependencies) { dep in
                        DependencyRow(dep: dep)
                    }
                }
                .padding(20)
                .background(Color(nsColor: .controlBackgroundColor))
                .cornerRadius(12)
                .frame(maxWidth: 480)

                if allCanAutoInstall {
                    VStack(spacing: 10) {
                        Button("Install & Continue") { startInstall() }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                            .keyboardShortcut(.defaultAction)

                        Button("Quit") { NSApplication.shared.terminate(nil) }
                            .buttonStyle(.plain)
                            .foregroundColor(.secondary)
                    }
                } else {
                    VStack(spacing: 10) {
                        Text("Install the items marked above, then relaunch the app.")
                            .foregroundColor(.secondary)
                            .font(.callout)
                            .multilineTextAlignment(.center)

                        Button("Quit") { NSApplication.shared.terminate(nil) }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                    }
                }
            }

            Spacer()
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func startInstall() {
        let repoURL = URL(fileURLWithPath: appState.repoPath)
        Task {
            await appState.transition(to: .installingDependencies)
            let ok = await PreflightRunner.shared.install(repoURL: repoURL, appState: appState)
            if ok {
                await ServerManager.shared.continueAfterPreflight(appState: appState)
            } else {
                await appState.transition(to: .error(
                    "Dependency installation failed.\nCheck the log above and try again."
                ))
            }
        }
    }
}

private struct DependencyRow: View {
    let dep: PendingDependency

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: dep.canAutoInstall ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .foregroundColor(dep.canAutoInstall ? .green : .orange)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text(dep.name).fontWeight(.medium)
                    Text(dep.detail)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            if let cmd = dep.manualCommand {
                HStack(spacing: 6) {
                    Text(cmd)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color(nsColor: .textBackgroundColor))
                        .cornerRadius(4)
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(cmd, forType: .string)
                    } label: {
                        Image(systemName: "doc.on.doc")
                    }
                    .buttonStyle(.borderless)
                    .help("Copy command")
                }
                .padding(.leading, 28)
            }
        }
    }
}
