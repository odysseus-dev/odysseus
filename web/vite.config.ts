import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// Inject the FastAPI CSP nonce placeholder onto every emitted <script>.
// `_serve_html_with_nonce` substitutes {{CSP_NONCE}} per request.
const cspNonce = {
  name: 'csp-nonce',
  transformIndexHtml(html: string) {
    return html.replace(/<script(?![^>]*\bnonce=)/g, '<script nonce="{{CSP_NONCE}}"')
  },
}

export default defineConfig({
  base: '/static-v2/',
  plugins: [react(), tailwindcss(), cspNonce],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    modulePreload: { polyfill: false },
    rollupOptions: {
      output: {
        // Content-hash the entry too (not a stable `index.js`) so a deploy can't
        // serve stale cached JS — index.html references the new hash each build.
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
})
