import Foundation

final class HealthPoller {
    static let shared = HealthPoller()

    func poll(port: Int, appState: AppState) async {
        await appState.transition(to: .waitingForHealth)

        let url = URL(string: "http://127.0.0.1:\(port)/api/health")!
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 2
        config.timeoutIntervalForResource = 2
        let session = URLSession(configuration: config)

        for attempt in 1...120 {
            try? await Task.sleep(nanoseconds: 1_000_000_000)

            // Stop polling if the process already died and set an error
            let currentPhase = await MainActor.run { appState.phase }
            if case .error = currentPhase { return }

            if let (_, response) = try? await session.data(from: url),
               (response as? HTTPURLResponse)?.statusCode == 200 {
                await appState.transition(to: .ready(port: port))
                return
            }

            await MainActor.run {
                appState.statusMessage = "Waiting for server… (\(attempt)s)"
            }
        }

        await appState.transition(to: .error("Server didn't respond after 120s.\nCheck the log below for errors."))
    }
}
