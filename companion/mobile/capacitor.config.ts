import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'app.odysseus.companion',
  appName: 'Odysseus',
  webDir: 'dist',
  // The app talks to YOUR Odysseus server over the network -- there is no
  // bundled backend. On a home LAN that server is plain http://, which Android
  // blocks as cleartext by default; allow it here. For access from anywhere,
  // prefer an https tunnel (e.g. Tailscale) so you can turn this back off.
  server: {
    androidScheme: 'https',
    cleartext: true,
  },
};

export default config;
