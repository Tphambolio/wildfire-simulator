import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          // Separate the large mapping library
          'maplibre': ['maplibre-gl'],
          // Separate React framework (the app imports react-dom/client, so list it too)
          'react-framework': ['react', 'react-dom', 'react-dom/client'],
        }
      }
    },
    // Increase chunk size warning limit since maplibre is inherently large
    chunkSizeWarningLimit: 1000,
    // Sourcemaps as files, without the sourceMappingURL comment, so browsers do not fetch
    // them (they are 3.7 MB); local debugging uses the dev server
    sourcemap: 'hidden'
  }
})
