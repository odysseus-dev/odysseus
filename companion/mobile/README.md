# Odysseus companion (mobile)

A thin **remote control** for an Odysseus server, built with Vite + React + TypeScript and packaged for Android via Capacitor. The phone does no AI work - it pairs with your PC and talks to the `/api/companion/*` endpoints to list sessions, watch one stream live, and stop it.

> Status: MVP. Implemented: pair -> list sessions -> live stream -> stop.
> Planned: QR pairing, start a new session, push notifications (silent/loud).

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

```bash
npm run build              # vite build -> dist/
npx cap add android        # one-time: generates the native android/ project
npm run cap:sync           # copy web build into the native project
npm run cap:android        # build + run on a connected device/emulator
```

Requires Android Studio + SDK. The generated `android/` folder is gitignored
(regenerate locally); only the web app and `capacitor.config.ts` are committed.

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
