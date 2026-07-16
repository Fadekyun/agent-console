import { defineConfig } from '@playwright/test';

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || (process.platform === 'linux' ? '/usr/bin/chromium' : undefined);
const liveBaseURL = process.env.LIVE_AGENT_CONSOLE_URL;

export default defineConfig({
  testDir: './tests/ui',
  workers: 1,
  fullyParallel: false,
  reporter: [['line']],
  outputDir: 'test-results',
  webServer: liveBaseURL ? undefined : {
    command: process.platform === 'win32' ? 'py -3 tests/ui_server.py' : 'python3 tests/ui_server.py',
    port: 4173,
    reuseExistingServer: true,
  },
  use: {
    baseURL: liveBaseURL || 'http://127.0.0.1:4173',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1280, height: 800 } } },
    { name: 'samsung', use: { viewport: { width: 360, height: 800 }, hasTouch: true, isMobile: true } },
    { name: 'iphone', use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } },
  ],
});
