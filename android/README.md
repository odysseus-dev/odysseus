# Odysseus Simple Signal Android

This folder is a first-pass native Android Studio wrapper for Odysseus. It
does not bundle the Python/FastAPI backend into the APK yet.

The app now has two modes:

- **Standalone Mobile**: native Android chat storage plus direct
  OpenAI-compatible `/chat/completions` requests from the phone. This mode does
  not require the PC Odysseus backend.
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

Standalone mode stores its chat history, endpoint URL, model name, and optional
API key/token in the app's private Android preferences. It is meant as a
mobile-first scaffold that can run without a PC.

Supported now:

```text
https://api.deepseek.com/v1
https://api.openai.com/v1
https://openrouter.ai/api/v1
other OpenAI-compatible /v1 endpoints reachable by the phone
```

Enter the endpoint base URL, model, and optional API key in the in-app
**Settings** button. The app posts to:

```text
<endpoint>/chat/completions
```

This first pass intentionally does not include on-device GGUF/llama.cpp model
execution yet. That is the next native-runtime step for fully local phone
inference.

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
- Native Java `StandaloneChatActivity`
- First-run mode picker
- Standalone chat history stored on the phone
- Standalone OpenAI-compatible endpoint/model/key settings
- Native Android HTTP calls to `/chat/completions`
- WebView with JavaScript, DOM storage, cookies, progress bar, back navigation,
  and file upload support
- Error/fallback screen for configuring the Odysseus server URL
- Development HTTP networking config

Still for Android Studio / next pass:

- Generate or update the Gradle wrapper if desired
- Add signing config for release builds
- Move standalone API key storage to Android Keystore-backed encryption
- Add import/export or sync between Standalone Mobile and PC Odysseus
- Add an on-device model runtime such as llama.cpp/GGUF if desired
- Decide whether to package a real on-device Python backend later
- Tighten cleartext HTTP rules before production distribution
