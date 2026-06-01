# Install Odysseus on your phone

Odysseus runs as a Progressive Web App (PWA): once installed it gets its own
home-screen icon and opens full-screen like a native app — no app store, no
separate download. This guide covers iPhone/iPad and Android.

## Before you start: use HTTPS

Installing only works when Odysseus is served over **HTTPS**. Open it through
your reverse proxy's secure URL (see
[Putting it behind HTTPS](../README.md#putting-it-behind-https)), not
`http://your-host:7000` directly.

Over plain HTTP, phones won't offer the install option, and some features (like
copy-to-clipboard) are blocked by the browser as well. The quickest secure URL
for a Tailscale setup is `tailscale serve`, which gives you an
`https://<host>.<tailnet>.ts.net` address you can open on your phone.

## iPhone / iPad (Safari)

iOS only installs PWAs from **Safari** — Chrome, Firefox, and other iOS browsers
can't add a standalone app, and there is no automatic install prompt.

1. Open your HTTPS Odysseus URL in **Safari**.
2. Tap the **Share** icon (the square with the up-arrow).
3. Scroll down and tap **Add to Home Screen**.
4. Tap **Add**. Odysseus now has an icon on your home screen and opens
   full-screen.

The first time you open Odysseus in iOS Safari, a one-time hint points you at
this flow — that's expected, since iOS has no "Install" button to offer.

## Android (Chrome)

1. Open your HTTPS Odysseus URL in Chrome.
2. Tap the **Install** button in the prompt Odysseus shows at the bottom of the
   screen, **or** open Chrome's menu (⋮) and choose **Install app** /
   **Add to Home screen**.
3. Confirm. Odysseus installs and opens in its own window.

The in-app Install prompt appears automatically once Chrome decides the app is
installable (HTTPS + a registered service worker + valid icons). Dismiss it and
it won't nag you again.

## What you get

- A standalone, full-screen window (no browser chrome).
- A home-screen icon.
- An offline shell — the app loads even on a flaky connection (you still need
  the server reachable for live data).
- Push notifications via ntfy, if you've set that up.

## Troubleshooting

- **No Install prompt on Android.** You're most likely on plain HTTP — switch to
  the HTTPS URL. If you're already on HTTPS, the app also needs valid PWA icons
  (`/static/icon-192.png` and `/static/icon-512.png`) to load; Chrome won't offer
  install if they're missing.
- **Nothing happens in Safari.** Make sure you're in **Safari** itself, not an
  in-app browser or another iOS browser, and that the URL is HTTPS.
- **Blank or ugly icon on the home screen.** The icon assets aren't loading —
  confirm the icons above exist and are served.
- **App won't update.** The service worker caches the shell. Pull-to-refresh,
  or remove and reinstall the app, to force the latest version.

<!-- Screenshots: iOS Share sheet + Android install prompt to be added. -->
