import SwiftUI

struct ContentView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        ZStack {
            switch appState.phase {
            case .idle, .checkingDependencies, .installingDependencies,
                 .checkingSetup, .runningSetup, .startingServer, .waitingForHealth:
                SplashView()
            case .needsInstallPermission:
                PreflightView()
            case .needsFirstTimeSetup:
                SetupView()
            case .ready(let port):
                WebView(url: URL(string: "http://127.0.0.1:\(port)")!)
                    .ignoresSafeArea()
            case .error(let message):
                ErrorView(message: message)
            }
        }
        .task {
            await ServerManager.shared.start(appState: appState)
        }
    }
}


