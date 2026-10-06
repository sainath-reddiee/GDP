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

const API_URL = process.env.AIP_API_URL ?? "http://127.0.0.1:8001";

/** Creates a run straight through the API (dev auth only); the wizard intent auto-registers the source. */
async function createRunViaApi(page: Page, name: string, environment = "DEV"): Promise<string> {
  test.skip((process.env.AIP_AUTH ?? "dev") !== "dev", "API-created fixtures need AIP_AUTH=dev");
  const res = await page.request.post(`${API_URL}/api/runs`, {
    data: {
      run_name: name,
      environment,
      intent: {
        path: "profile_suggest", run_name: name, targets: [], model_existing: false,
        created_at: new Date().toISOString(),
        source: {
          origin: "snowflake", database: DEMO_DATABASE, schema: "CRM", source_system_name: "SMOKE_CRM",
          source_type: "SNOWFLAKE_DATABASE", tables: [],
        },
      },
    },
  });
  expect(res.ok(), await res.text()).toBeTruthy();
  return (await res.json()).run_id as string;
}

function watchHydration(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /hydrat|did not match|Minified React error/i.test(msg.text())) errors.push(msg.text());
  });
  page.on("pageerror", (err) => errors.push(err.message));
  return errors;
}

test("source step: multi-select tables, profile badges and landing target", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page);
  const errors = watchHydration(page);
  const runId = await createRunViaApi(page, `smoke-src-${Date.now()}`, "TEST");

  await page.goto(`/runs/${runId}/source`);
  await expect(page.getByRole("heading", { name: "Select source tables" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Register source and keep this map" })).toHaveCount(0);

  await page.getByRole("button", { name: "Select all" }).click();
  await expect(page.getByText(/^2 selected/)).toBeVisible();
  await page.getByLabel("select CRM_ORDER").uncheck();
  await expect(page.getByText(/^1 selected/)).toBeVisible();
  await expect(page.getByLabel("select CRM_CUSTOMER")).toBeChecked();
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByText(/^0 selected/)).toBeVisible();

  await expect(page.getByRole("columnheader", { name: "Profile" })).toBeVisible();
  await expect(page.getByText(/Profiled \(cached\)|Unprofiled/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Landing target" })).toBeVisible();
  await expect(page.getByLabel(/Landing schema in/)).toHaveValue("LANDING");
  await expect(page.getByRole("button", { name: /Validate & land \(0 selected\)/ })).toBeDisabled();

  expect(errors).toEqual([]);
});

test("runs page: filters and bulk archive, restore, delete", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page);
  const errors = watchHydration(page);
  const names = [`smoke-bulk-a-${Date.now()}`, `smoke-bulk-b-${Date.now()}`];
  for (const name of names) await createRunViaApi(page, name);

  await page.goto("/runs");
  for (const name of names) await page.getByLabel(`select ${name}`).check();
  await expect(page.getByText("2 selected")).toBeVisible();
  await page.getByRole("button", { name: "Archive selected" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Archived 2 run(s)" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel(`select ${names[0]}`)).toHaveCount(0);

  await page.getByRole("link", { name: "Archived", exact: true }).click();
  await expect(page).toHaveURL(/status=archived/);
  for (const name of names) {
    await expect(page.getByRole("row", { name: new RegExp(name) }).getByText("ARCHIVED")).toBeVisible();
    await page.getByLabel(`select ${name}`).check();
  }
  await expect(page.getByRole("button", { name: "Restore selected" })).toBeVisible();

  await page.getByRole("button", { name: "Delete selected" }).click();
  const dialog = page.getByRole("dialog", { name: "Confirm delete" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel(/Drop landing tables/)).toBeChecked();
  await dialog.getByRole("button", { name: "Delete 2 runs" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Deleted 2 runs." })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Staged table profiles were kept.")).toBeVisible();
  for (const name of names) await expect(page.getByLabel(`select ${name}`)).toHaveCount(0);

  await page.getByRole("link", { name: "Active", exact: true }).click();
  await expect(page).toHaveURL(/status=active/);
  expect(errors).toEqual([]);
});
