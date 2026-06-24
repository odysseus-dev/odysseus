# Odysseus Simple Signal Android

This folder is a first-pass native Android Studio wrapper for Odysseus. It
does not bundle the Python/FastAPI backend into the APK yet.

The app now has two modes:

- **Standalone Mobile**: native Android chat storage plus direct
  a phone-local backend which serves the same Odysseus web UI from bundled
  `static/` assets. This mode does not require the PC Odysseus backend and
  starts with fresh mobile-only sessions/endpoints.
- **Connect to PC**: the original WebView shell that opens an already-running
  Odysseus server on a computer.

## Open In Android Studio

1. Open Android Studio.
2. Choose **File -> Open**.
3. Select this `android/` folder.
4. Let Android Studio install the requested Android SDK / Gradle pieces.
5. Run the `app` configuration.

## Backend URLs

Backend URLs apply only to **Connect to PC** mode.

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

## Standalone Mobile Mode

Standalone mode starts a small local backend on the phone and opens the same
Odysseus frontend at:

```text
http://127.0.0.1:7019/static/index.html
```

If that port is busy, the app tries the next local port up to `7039`.

The mobile backend stores chat history, model endpoints, model names, and
optional API keys/tokens in the app's private Android preferences. This keeps
the phone install separate from the PC database, so the UI is a fresh slate.

Supported now:

```text
https://api.deepseek.com
https://api.openai.com/v1
https://openrouter.ai/api/v1
other OpenAI-compatible /v1 endpoints reachable by the phone
```

Add endpoints through the normal Odysseus Settings / Model Endpoints UI. The
mobile backend posts to:

```text
<endpoint>/chat/completions
```

This first pass intentionally implements the mobile subset of the backend:
model endpoint setup, sessions, history, and chat streaming. Desktop-heavy
features such as PC filesystem tools, email/calendar sync, RAG indexing, and
document processing still need native/mobile backend equivalents.

It also does not include on-device GGUF/llama.cpp model execution yet. That is
the next native-runtime step for fully local phone inference.

## Build-Time URL Override

Android Studio can override the default URL with a Gradle property:

```properties
ODYSSEUS_ANDROID_URL=http://192.168.1.25:7000
```

Put that in `android/local.properties` or pass it on the command line. The app
also lets you change and save the URL at runtime if the first connection fails.

## Direct-Sideload Release Builds

Direct-sideload release APKs must be signed with a persistent local release
keystore. Do not distribute debug APKs as releases.

1. Copy `android/keystore.properties.example` to `android/keystore.properties`.
2. Fill in `storeFile`, `storePassword`, `keyAlias`, and `keyPassword`.
3. Keep both `android/keystore.properties` and the keystore file private.
4. Build and verify the sideload APK from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-android-sideload.ps1
```

The script writes `release-assets/android/Odysseus-Simple-Signal-<version>-sideload.apk`,
creates a SHA-256 checksum, and refuses unsigned or Android-debug-signed APKs.
Keep using the same keystore for future sideload builds so Android and Play
Protect see the same developer certificate over time.

## Current Scope

Included now:

- Gradle Android project
- Native Java `MainActivity`
- Native Java `MobileBackendServer`
- First-run mode picker
- Same Odysseus web UI in standalone mode
- Bundled `static/` frontend assets
- Standalone sessions/endpoints/history stored on the phone
- Mobile backend routes for model endpoints, sessions, history, and chat
- WebView with JavaScript, DOM storage, cookies, progress bar, back navigation,
  and file upload support
- Error/fallback screen for configuring the Odysseus server URL
- Development HTTP networking config
- Local release signing config for direct-sideload APKs

Still for Android Studio / next pass:

- Generate or update the Gradle wrapper if desired
- Move standalone API key storage to Android Keystore-backed encryption
- Add import/export or sync between Standalone Mobile and PC Odysseus
- Expand mobile backend coverage for documents, notes, gallery, calendar, and
  other desktop backend routes
- Add an on-device model runtime such as llama.cpp/GGUF if desired
- Decide whether to package a real on-device Python backend later
- Tighten cleartext HTTP rules before production distribution
