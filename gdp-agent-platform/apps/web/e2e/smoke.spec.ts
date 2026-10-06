import { expect, test, type Page } from "@playwright/test";

const DEMO_DATABASE = process.env.AIP_DEMO_SOURCE ?? "DEV_AIP_DEMO_SOURCE";

async function signIn(page: Page) {
  await page.goto("/login");
  if (await page.getByRole("button", { name: "Log in to workspace" }).isVisible()) {
    await page.getByRole("button", { name: "Log in to workspace" }).click();
  } else {
    test.skip(!process.env.AIP_E2E_USER || !process.env.AIP_E2E_TOKEN, "PAT mode needs AIP_E2E_USER and AIP_E2E_TOKEN");
    await page.getByLabel("Snowflake user").fill(process.env.AIP_E2E_USER!);
    await page.getByLabel("Programmatic access token").fill(process.env.AIP_E2E_TOKEN!);
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

test("onboard a source: register, validate access, land, with later stages locked", async ({ page }) => {
  test.setTimeout(420_000);
  await signIn(page);

  const name = `smoke-${Date.now()}`;
  await page.goto("/onboarding");
  await page.getByLabel("Run name").fill(name);
  await page.getByLabel("Target model").fill("GDP.DIM_CUSTOMER");
  await page.getByRole("button", { name: "Create run" }).click();
  await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/);
  const runUrl = page.url();
  await expect(page.getByRole("heading", { name: "SOURCE — CREATED" })).toBeVisible();
  await expect(page.getByRole("button", { name: "SOURCE_REGISTERED" })).toHaveCount(0);

  await page.goto(`${runUrl}/mapping`);
  await expect(page.getByRole("heading", { name: "Stage locked" })).toBeVisible();

  await page.goto(runUrl);
  await page.getByRole("link", { name: /Register the source/ }).click();
  await page.getByLabel("Source system name").fill("SMOKE_CRM");
  await page.getByLabel("Database").fill(DEMO_DATABASE);
  await page.getByLabel("Schema").fill("CRM");
  await page.getByRole("button", { name: "Register source" }).click();

  await expect(page.getByRole("heading", { name: "Select objects to onboard" })).toBeVisible({ timeout: 90_000 });
  await page.getByLabel("select CRM_CUSTOMER").check();
  await page.getByRole("button", { name: /Validate & land \(1 selected\)/ }).click();
  await expect(page.getByText("LANDING_COMPLETE").first()).toBeVisible({ timeout: 240_000 });

  await expect(page.getByRole("cell", { name: "CRM_CUSTOMER readable" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Landed tables" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "SMOKE_CRM__CRM_CUSTOMER", exact: false })).toBeVisible();
  await expect(page.getByRole("cell", { name: "500", exact: true }).first()).toBeVisible();

  await page.goto(`${runUrl}/landing`);
  await expect(page).toHaveURL(new RegExp(`${runUrl}/source$`));

  await page.goto(`${runUrl}/mapping`);
  await expect(page.getByRole("heading", { name: "Stage locked" })).toBeVisible();

  await page.goto(`${runUrl}/profile`);
  await expect(page.getByRole("heading", { name: "Profiling" })).toBeVisible();
  await page.getByRole("button", { name: "Start profiling" }).click();
  await expect(page.getByRole("cell", { name: "CUST_ID" })).toBeVisible({ timeout: 180_000 });

  await page.goto(`${runUrl}/domain`);
  await expect(page.getByRole("heading", { name: "Knowledge pack" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "GDP" }).first()).toBeVisible({ timeout: 90_000 });

  await page.goto(runUrl);
  await page.getByPlaceholder(/source tables/).fill("What is the state of this run?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("agent-log")).toContainText(/agent|token/i);

  await page.goto(`${runUrl}/audit`);
  await expect(page.getByRole("cell", { name: "LANDING_COMPLETE" }).first()).toBeVisible();

  await page.goto(runUrl);
  await page.getByRole("button", { name: "CANCELLED" }).click();
  await expect(page.getByText("CANCELLED").first()).toBeVisible();
});
