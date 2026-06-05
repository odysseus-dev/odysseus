import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// In the browser during `npm run dev`, requests to a different host (your PC's
// LAN IP) are blocked by CORS unless the server allows your dev origin. To avoid
// touching the server config, set VITE_PROXY_TARGET to your Odysseus URL and the
// app will call relative `/api/...` paths that Vite proxies for you.
//   VITE_PROXY_TARGET=http://192.168.1.50:7000 npm run dev
// In the packaged Capacitor app there is no dev server, so the app talks to the
// paired base URL directly (see src/lib/connection.ts).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_PROXY_TARGET;
  return {
    plugins: [react()],
    server: target
      ? { proxy: { '/api': { target, changeOrigin: true } } }
      : {},
  };
});
