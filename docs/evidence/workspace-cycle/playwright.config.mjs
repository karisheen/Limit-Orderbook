import { createRequire } from 'node:module';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const require = createRequire(resolve(root, 'frontend/package.json'));
const { defineConfig } = require('@playwright/test');
const baseURL = new URL(process.env.LOB_E2E_BASE_URL ?? 'http://127.0.0.1:8015/');
if (baseURL.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(baseURL.hostname) || baseURL.username || baseURL.password || baseURL.pathname !== '/' || baseURL.search || baseURL.hash) {
  throw new Error('LOB_E2E_BASE_URL must be a loopback HTTP origin');
}

export default defineConfig({
  testDir: resolve(root, 'frontend/e2e'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45000,
  expect: { timeout: 15000 },
  reporter: 'list',
  outputDir: mkdtempSync(join(tmpdir(), 'lob-e2e-results-')),
  use: {
    baseURL: baseURL.href,
    browserName: 'chromium',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
