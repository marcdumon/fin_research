import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Built into the Python package so FastAPI serves it, and proxied in development so the browser talks to
// the same origin either way.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: '../src/fin_research/web/static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:18000' } },
})
