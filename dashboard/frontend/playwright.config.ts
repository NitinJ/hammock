import { defineConfig, devices } from "@playwright/test";

const PORT = Number.parseInt(process.env.HAMMOCK_PORT ?? "8771", 10);
const HAMMOCK_ROOT = process.env.HAMMOCK_ROOT ?? "/tmp/hammock-playwright-root";
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    headless: true,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], channel: undefined },
    },
  ],
  webServer: {
    // Build the SPA first (cd ../.. → repo root), then run uvicorn with
    // a clean root so each suite starts from a known state.
    command: `bash -c "rm -rf ${HAMMOCK_ROOT} && mkdir -p ${HAMMOCK_ROOT}/jobs && pnpm build >/dev/null && cd ../.. && HAMMOCK_ROOT=${HAMMOCK_ROOT} HAMMOCK_PORT=${PORT} HAMMOCK_FAKE_FIXTURES_DIR=${HAMMOCK_ROOT}/fakes uv run python -m dashboard"`,
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
