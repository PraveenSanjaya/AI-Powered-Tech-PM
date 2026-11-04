import { defineConfig } from "@playwright/test";

// Requires a disposable PostgreSQL database (see docs/testing.md). The API is started on :8000 with demo data and
// the Next.js dev server on :3000. Tests never contact real Jira/Slack/etc.: only the demo connectors exist.
export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: { baseURL: "http://localhost:3000", trace: "retain-on-failure" },
  webServer: [
    { command: "node e2e/start-api.mjs", url: "http://localhost:8000/readyz", reuseExistingServer: false, timeout: 180_000 },
    { command: "npm run dev", url: "http://localhost:3000/login", reuseExistingServer: false, timeout: 180_000 },
  ],
});
