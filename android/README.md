# Odysseus Simple Signal Android

This folder is a first-pass native Android Studio wrapper for Odysseus. It
does not bundle the Python/FastAPI backend into the APK yet. The app is a
WebView shell that opens an already-running Odysseus server.

## Open In Android Studio

1. Open Android Studio.
2. Choose **File -> Open**.
3. Select this `android/` folder.
4. Let Android Studio install the requested Android SDK / Gradle pieces.
5. Run the `app` configuration.

## Backend URLs

The default debug URL is:

```text
http://10.0.2.2:7000
```

Use that for the Android emulator while Odysseus is running on the same
computer at `http://127.0.0.1:7000`.

For a physical Android device, run Odysseus on a reachable interface and enter
one of these in the app's fallback screen:

```text
http://<your-computer-lan-ip>:7000
http://<your-tailscale-ip>:7000
```

Keep `AUTH_ENABLED=true` before exposing Odysseus outside localhost. For LAN
testing, set `APP_BIND=0.0.0.0` only on a trusted network or VPN.

## Build-Time URL Override

Android Studio can override the default URL with a Gradle property:

```properties
ODYSSEUS_ANDROID_URL=http://192.168.1.25:7000
```

Put that in `android/local.properties` or pass it on the command line. The app
also lets you change and save the URL at runtime if the first connection fails.

## Current Scope

Included now:

- Gradle Android project
- Native Java `MainActivity`
- WebView with JavaScript, DOM storage, cookies, progress bar, back navigation,
  and file upload support
- Error/fallback screen for configuring the Odysseus server URL
- Development HTTP networking config

Still for Android Studio / next pass:

- Generate or update the Gradle wrapper if desired
- Add signing config for release builds
- Decide whether the app should stay a remote WebView client or attempt a real
  on-device Python backend package
- Tighten cleartext HTTP rules before production distribution
