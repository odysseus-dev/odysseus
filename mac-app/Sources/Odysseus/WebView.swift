import SwiftUI
import WebKit

/// SwiftUI wrapper around WKWebView. The `reloadToken` binding lets the
/// rest of the app trigger a reload after a container restart without
/// reconstructing the view (which would lose scroll state).
///
/// A WKNavigationDelegate surfaces load failures back into AppState so a
/// blank window doesn't leave the user with nothing to debug.
struct WebView: NSViewRepresentable {
    let url: URL
    @Binding var reloadToken: Int
    @EnvironmentObject var state: AppState

    func makeNSView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        cfg.preferences.javaScriptCanOpenWindowsAutomatically = false

        let webView = WKWebView(frame: .zero, configuration: cfg)
        webView.allowsBackForwardNavigationGestures = true
        webView.customUserAgent = "OdysseusApp/1.0 (Macintosh; AppleWebKit)"
        webView.navigationDelegate = context.coordinator
        context.coordinator.state = state
        context.coordinator.load(webView, url: url)
        context.coordinator.lastToken = reloadToken
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.state = state
        // If the URL itself changed (e.g. user remapped the port via the
        // conflict dialog) reload immediately; otherwise only reload when
        // the token bumps.
        let currentURL = webView.url
        let tokenChanged = context.coordinator.lastToken != reloadToken
        let urlChanged = currentURL == nil || currentURL?.host != url.host || currentURL?.port != url.port

        if tokenChanged || urlChanged {
            context.coordinator.lastToken = reloadToken
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                context.coordinator.load(webView, url: url)
            }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var lastToken = 0
        weak var state: AppState?

        /// Soft retry window. Compose says the container is up before
        /// uvicorn actually binds the port, so the first navigation often
        /// races and fires `didFailProvisionalNavigation` with NSURLError
        /// -1004 (cannot connect). We retry quietly for a few seconds
        /// before treating a failure as fatal.
        private let gracePeriod: TimeInterval = 5
        private let retryInterval: TimeInterval = 0.5
        private var deadline: Date = .distantPast

        func load(_ webView: WKWebView, url: URL) {
            // Each explicit (re)load resets the grace window — covers the
            // first load AND any post-restart reload triggered by reloadToken.
            deadline = Date().addingTimeInterval(gracePeriod)
            var req = URLRequest(url: url)
            req.cachePolicy = .reloadIgnoringLocalCacheData
            webView.load(req)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            // First successful navigation closes the grace window so a
            // later genuine error (e.g. container crash hours in) surfaces
            // immediately instead of silently retrying.
            deadline = .distantPast
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            handleFailure(webView, error: error)
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            handleFailure(webView, error: error)
        }

        private func handleFailure(_ webView: WKWebView, error: Error) {
            // NSURLErrorCancelled (-999) is fired when one navigation
            // supersedes another — typical during redirect chains, page
            // refreshes, or JS-initiated location changes. Apple
            // documents it as informational; surfacing it as a hard
            // failure would have the user staring at a "WebView failed
            // to load" overlay every time Odysseus's login redirect ran.
            let nsError = error as NSError
            if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
                return
            }
            if Date() < deadline, let url = webView.url ?? URLRequest(url: Config.webURL).url {
                // Still inside the soft window — wait briefly and retry quietly.
                DispatchQueue.main.asyncAfter(deadline: .now() + retryInterval) { [weak self] in
                    guard let self else { return }
                    guard Date() < self.deadline else {
                        // Window closed while we were sleeping; report it.
                        self.report("Could not reach \(url.absoluteString): \(error.localizedDescription)")
                        return
                    }
                    webView.load(URLRequest(url: url))
                }
                return
            }
            report("Could not reach \(webView.url?.absoluteString ?? "Odysseus"): \(error.localizedDescription)")
        }

        private func report(_ message: String) {
            DispatchQueue.main.async { [weak self] in
                self?.state?.set(.failed(message), "WebView failed to load.")
            }
        }
    }
}
