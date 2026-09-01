import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure'
  },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' }
    },
    {
      name: 'firefox-basic',
      grep: /应用壳层|登录页/,
      use: { browserName: 'firefox' }
    }
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173/responsive-layout-preview.html',
    reuseExistingServer: true,
    timeout: 120_000
  }
})
