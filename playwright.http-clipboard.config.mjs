import config from './playwright.config.mjs';

// Localhost is a secure context even on HTTP; use a mapped non-loopback hostname.
const port = Number(process.env.AGCONSOLE_UI_TEST_PORT || 4192);
export default {
  ...config,
  testMatch: 'http-clipboard.spec.mjs',
  timeout: 180000,
  outputDir: 'test-results/http-clipboard',
  webServer: {...config.webServer, port, env: {AGCONSOLE_UI_TEST_PORT: String(port)}},
  use: {
    ...config.use,
    baseURL: `http://console-http.test:${port}`,
    launchOptions: {
      ...config.use.launchOptions,
      args: ['--host-resolver-rules=MAP console-http.test 127.0.0.1', '--no-proxy-server'],
    },
  },
};
