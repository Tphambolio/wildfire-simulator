import { defineConfig, devices } from '@playwright/test'

// E2E tests run against a production build served by `vite preview`. The FireSim API is not
// started: tests/e2e/mockApi.ts answers the HTTP calls with page.route and replays the recorded
// fixture over a routed WebSocket (page.routeWebSocket), and blocks every external request
// (map tiles, fonts, Open-Meteo, Nominatim, Overpass), so runs are deterministic and offline.
const PORT = Number(process.env.E2E_PORT ?? 4173)

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        // No GPU on CI runners or headless workstations: render WebGL with SwiftShader
        launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] },
      },
    },
  ],
  webServer: {
    // E2E_SKIP_BUILD=1 reuses an existing dist/ (CI builds it in an earlier step)
    command: `${process.env.E2E_SKIP_BUILD ? '' : 'npm run build && '}npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
})
