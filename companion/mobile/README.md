# Odysseus companion (mobile)

A thin **remote control** for an Odysseus server, built with Vite + React + TypeScript and packaged for Android via Capacitor. The phone does no AI work - it pairs with your PC and talks to the `/api/companion/*` endpoints to list sessions, watch one stream live, and stop it.

> Implemented: QR / manual pairing; list & open any session; start a chat and
> send follow-ups; switch model mid-chat; reasoning ("thinking") view;
> agent / web-search / terminal toggles; attach images from the phone or a PC
> file; chat search; and the desktop tools (email read/AI-summary/AI-reply/send,
> calendar, notes, tasks).
> Planned: voice dictation, push notifications (needs HTTPS / a tunnel).

## How it talks to the PC

```
 phone (this app) --HTTP+SSE-->  GET  /api/companion/sessions          list
                                 GET  /api/companion/sessions/:id/stream  watch (SSE)
                                 POST /api/companion/sessions/:id/stop    stop
                                 GET  /api/companion/ping                 validate pairing
```

Every request carries the pairing token as `Authorization: Bearer ody_...`. The
server resolves it to the token's owner and scopes everything to that user, so
the phone sees exactly what the owner's desktop UI sees. See `../README.md` and
`../routes.py`.

## Run it (web, fastest loop)

```bash
cd companion/mobile
npm install
# Point dev requests at your PC to dodge browser CORS (see vite.config.ts):
VITE_PROXY_TARGET=http://<your-pc-ip>:7000 npm run dev
```

Open the printed URL. On the pairing screen, enter your server address and a
token minted at `<server>/api/companion/pair` (admin only).

> Without the proxy, a browser blocks cross-origin calls to your PC. Either use
> `VITE_PROXY_TARGET` (above) or add your dev origin to the server's
> `ALLOWED_ORIGINS`. The packaged Android app has no such restriction.

## Build the Android app

Requires Android Studio (for the SDK + a bundled JDK 17/21). The generated
`android/` folder is gitignored - regenerate it locally; only the web app and
`capacitor.config.ts` are committed.

```bash
npm install
npm run build              # vite build -> dist/
npx cap add android        # one-time: generates the native android/ project
npx cap sync android       # copy the web build + plugins into android/
```

Then apply two project tweaks (Capacitor's defaults need nudging) before the
first Gradle build:

1. **SDK level** - `android/variables.gradle`: set `compileSdkVersion` and
   `targetSdkVersion` to a platform you actually have installed (e.g. `35`).
   Capacitor 6 defaults to `34`.
2. **Camera + microphone permissions** (QR scanner + voice dictation) - add to
   `android/app/src/main/AndroidManifest.xml`:
   ```xml
   <uses-permission android:name="android.permission.CAMERA" />
   <uses-feature android:name="android.hardware.camera" android:required="false" />
   <uses-permission android:name="android.permission.RECORD_AUDIO" />
   <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
   ```

Build the APK (point Gradle at a JDK 17/21 if your system default is newer -
Android Studio ships one under `.../Android Studio/jbr`):

```bash
cd android
# optional, if your default `java` is >21:
#   echo "org.gradle.java.home=/path/to/Android Studio/jbr" >> gradle.properties
./gradlew assembleDebug     # -> app/build/outputs/apk/debug/app-debug.apk
```

Install the resulting `app-debug.apk` on a phone (enable "install from unknown
sources"). `cleartext: true` in `capacitor.config.ts` lets the installed app
reach a plain-http LAN server; drop it once you front Odysseus with HTTPS.

## Reaching it from anywhere

The app is address-agnostic - it just needs a reachable URL. **Don't** forward
ports from your router to expose Odysseus to the public internet. Instead put
your phone and PC on a private tunnel:

- **Tailscale** (recommended): install on both, pair with the PC's Tailscale
  address. Works on any network, no port-forwarding.
- **Cloudflare Tunnel**: gives an https hostname; also lets you drop the Android
  cleartext allowance in `capacitor.config.ts`.

See the repo's `THREAT_MODEL.md`.

## Layout

```
src/
  lib/connection.ts   paired server + token, persisted via @capacitor/preferences
  lib/api.ts          typed client for /api/companion/*
  lib/sse.ts          fetch-based SSE reader (EventSource can't send a Bearer token)
  screens/            Pair, Sessions, Session (stream+stop), Settings
  components/         BottomNav
```
