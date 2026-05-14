import { defineConfig, devices } from '@playwright/test'
import os from 'os'
import path from 'path'
import { fileURLToPath } from 'url'

// Resolve ROOT relative to this config file so the path is correct regardless
// of which directory the playwright CLI is invoked from.
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const TEST_DATA = path.join(os.tmpdir(), 'sa-runner-pw-e2e')

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 30_000 },

  use: {
    baseURL: 'http://127.0.0.1:7892',
    trace: 'retain-on-failure',
  },

  webServer: {
    command:
      'python -m uvicorn app.main:app --host 127.0.0.1 --port 7892 --log-level error',
    cwd: ROOT,
    port: 7892,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    env: {
      APP_PASSWORD: 'playwright-test',
      SESSION_SECRET: 'playwright-test-secret-32bytesxx',
      SA_RUNNER_MOCK_PIPELINE: '1',
      SA_RUNNER_MOCK_STEP_DELAY_MS: '800',
      SA_RUNNER_PYTHON_EXE:
        process.env.SA_RUNNER_PYTHON_EXE ?? 'python',
      SA_RUNNER_NODE_EXE: 'node',
      SA_RUNNER_SKILL_DIR: path.join(TEST_DATA, 'skill'),
      LOCALAPPDATA: TEST_DATA,
      SA_RUNNER_MASTER_KEY_PATH: path.join(TEST_DATA, 'master.key'),
    },
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
