import { defineConfig } from '@playwright/test';
import { existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const fixturePort = Number(process.env.AGCONSOLE_UI_TEST_PORT || 4173);
// Workers inherit the same per-run directory; fixture output stays out of source.
const outputDir = process.env.AGCONSOLE_UI_OUTPUT_DIR ||=
  (process.env.CI ? 'test-results' : mkdtempSync(join(tmpdir(), 'agent-console-ui-')));

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || (process.platform === 'linux' && existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
const liveBaseURL = process.env.LIVE_AGENT_CONSOLE_URL;

export default defineConfig({
  testDir: './tests/ui',
  workers: 1,
  fullyParallel: false,
  reporter: [['line']],
  outputDir,
  webServer: liveBaseURL ? undefined : {
    command: process.platform === 'win32' ? 'py -3 tests/ui_server.py' : 'python3 tests/ui_server.py',
    port: fixturePort,
    reuseExistingServer: false,
    env: {AGCONSOLE_UI_TEST_PORT: String(fixturePort)},
  },
  use: {
    baseURL: liveBaseURL || `http://127.0.0.1:${fixturePort}`,
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
