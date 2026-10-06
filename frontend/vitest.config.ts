import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Unit tests (Vitest + React Testing Library + jsdom). The Playwright e2e tests live in
// tests/e2e and run with `npm run test:e2e`.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
