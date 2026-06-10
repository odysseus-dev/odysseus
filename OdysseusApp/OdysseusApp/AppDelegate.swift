import AppKit

class AppDelegate: NSObject, NSApplicationDelegate {

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        ServerManager.shared.stop()
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        ServerManager.shared.stop()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}
