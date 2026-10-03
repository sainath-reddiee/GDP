import { defineConfig } from "@playwright/test";

/** Runs against already-running servers: Next on :3000 and the FastAPI backend it points to. */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 30_000 },
  workers: 1,
  use: { baseURL: process.env.AIP_WEB_URL ?? "http://localhost:3000", trace: "retain-on-failure" },
});
