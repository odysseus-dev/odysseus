import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'app.odysseus.companion',
  appName: 'Odysseus',
  webDir: 'dist',
  // The app talks to YOUR Odysseus server over the network -- there is no
  // bundled backend. On a home LAN that server is plain http://. Serve the app
  // itself over http://localhost (androidScheme 'http') for two reasons:
  //   1. no https->http "mixed content" block when fetching the http LAN server;
  //   2. the app's origin becomes http://localhost, which Odysseus already
  //      allows by default (ALLOWED_ORIGINS), so cross-origin fetch + SSE work
  //      without extra CORS config.
  // localhost is still a secure context, so the camera (QR) and mic (voice)
  // keep working. cleartext allows the http LAN calls. For access from anywhere,
  // prefer an https tunnel (e.g. Tailscale).
  server: {
    androidScheme: 'http',
    cleartext: true,
  },
};

export default config;
