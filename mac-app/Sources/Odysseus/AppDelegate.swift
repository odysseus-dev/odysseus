import AppKit

/// Intercepts Cmd+Q / menu Quit to ask whether to also stop the Docker
/// stack. The .app and the containers have independent lifecycles —
/// closing the window doesn't stop uvicorn — so the prompt avoids the
/// "I quit Odysseus but it's still listening on :7001" surprise.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // Skip the prompt entirely if there's nothing to stop. We check
        // synchronously — it's a single `docker compose ps` call, ~50ms.
        guard ComposeManager.isHealthy() else { return .terminateNow }

        let alert = NSAlert()
        alert.messageText = "Stop Odysseus containers?"
        alert.informativeText = "Odysseus is still running in Docker. The containers will keep going in the background unless you stop them now."
        alert.addButton(withTitle: "Stop and Quit")
        alert.addButton(withTitle: "Quit (Keep Running)")
        alert.addButton(withTitle: "Cancel")
        // Make Cancel the safe default so an accidental Return doesn't kill containers.
        alert.buttons[2].keyEquivalent = "\u{1b}"   // Esc

        switch alert.runModal() {
        case .alertFirstButtonReturn:
            // Drop work onto a background queue so the modal doesn't hang
            // while compose stop runs (can take a few seconds).
            DispatchQueue.global(qos: .userInitiated).async {
                _ = ComposeManager.stop()
                DispatchQueue.main.async {
                    NSApp.reply(toApplicationShouldTerminate: true)
                }
            }
            return .terminateLater

        case .alertSecondButtonReturn:
            return .terminateNow

        default:
            return .terminateCancel
        }
    }
}
