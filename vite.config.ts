import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API is FastAPI on 8124 in dev (port pinned so parallel agents do not collide);
// in production FastAPI mounts frontend/dist and serves the app and /api on one origin.
export default defineConfig({
  root: 'frontend',
  base: '/',
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: '127.0.0.1',
    port: 5175,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8124',
        changeOrigin: true,
      },
    },
  },
})
