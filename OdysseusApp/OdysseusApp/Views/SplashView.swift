import SwiftUI

struct SplashView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            HStack(spacing: 12) {
                BoatIcon()
                    .frame(width: 38, height: 38)
                    .foregroundColor(.accentColor)
                Text("Odysseus")
                    .font(.largeTitle.bold())
            }

            Text(appState.statusMessage)
                .foregroundColor(.secondary)
                .animation(.easeInOut, value: appState.statusMessage)

            ProgressView()
                .controlSize(.large)
                .padding(.top, 4)

            if !appState.logLines.isEmpty {
                logView
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }

            Spacer()
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(.easeInOut(duration: 0.3), value: appState.logLines.isEmpty)
    }

    private var logView: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical) {
                LazyVStack(alignment: .leading, spacing: 1) {
                    ForEach(appState.logLines) { entry in
                        Text(entry.text)
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(8)
            }
            .onChange(of: appState.logLines.count) {
                if let last = appState.logLines.last {
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
        .frame(maxWidth: 640, maxHeight: 180)
        .background(Color(nsColor: .textBackgroundColor))
        .cornerRadius(8)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.2)))
    }
}
