const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/ui', fullyParallel: false, workers: 1,
  timeout: 30000, expect: { timeout: 8000 },
  reporter: [['list'], ['json', { outputFile: 'ui-test-results.json' }]],
  use: { baseURL: 'http://127.0.0.1:8018', channel: 'chrome', headless: true,
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  projects: [
    {name: 'desktop', use: {viewport: {width: 1280, height: 800}}},
    {name: 'mobile', use: {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true}},
  ],
  webServer: {command: '.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8018',
    url: 'http://127.0.0.1:8018/health', reuseExistingServer: false,
    env: {OPENAI_API_KEY: '', NEXTTRACK_ONLINE_CATALOG: '0'}, timeout: 60000},
});
