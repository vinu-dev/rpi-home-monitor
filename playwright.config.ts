import { defineConfig, devices } from "@playwright/test";

const serverPort = process.env.PLAYWRIGHT_SERVER_PORT ?? "5443";
const cameraPort = process.env.PLAYWRIGHT_CAMERA_PORT ?? "5444";

export default defineConfig({
  testDir: "./tests/e2e/playwright",
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ["html", { open: "never" }],
    ["list"],
  ],
  use: {
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: process.env.CI ? "retain-on-failure" : "off",
  },
  projects: [
    {
      name: "setup",
      testMatch: /auth\/.*\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "smoke",
      dependencies: ["setup"],
      testMatch: /smoke\/.*\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: `https://127.0.0.1:${serverPort}`,
      },
    },
    {
      name: "full",
      dependencies: ["setup"],
      testMatch: /regression\/.*\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: `https://127.0.0.1:${serverPort}`,
      },
    },
  ],
  webServer: [
    {
      command:
        `python scripts/testing/run_server_app.py --mode seeded --port ${serverPort}`,
      url: `https://127.0.0.1:${serverPort}/login`,
      ignoreHTTPSErrors: true,
      reuseExistingServer: !process.env.CI,
      timeout: 120000,
    },
    {
      command:
        `python scripts/testing/run_camera_status_server.py --port ${cameraPort}`,
      url: `https://127.0.0.1:${cameraPort}/login`,
      ignoreHTTPSErrors: true,
      reuseExistingServer: !process.env.CI,
      timeout: 120000,
    },
  ],
});
