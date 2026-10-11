import { expect, test, type BrowserContext } from "@playwright/test";

// Profiling Store governance against the fake API (dev sign-in as ANA): CUSTOMERS has two passes (COUNTRY nulls jumped),
// BEN proposed an annotation of COUNTRY, and RAW.CRM is profiled daily.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

async function openCustomers(page: import("@playwright/test").Page) {
  await page.goto("/dashboard/profiling");
  await page.getByRole("button", { name: /CUSTOMERS/ }).first().click();
  await expect(page.getByLabel("Profiling pass")).toBeVisible();
}

test("any two passes are compared column by column", async ({ page }) => {
  await openCustomers(page);
  const options = await page.getByLabel("Compare with").locator("option").allTextContents();
  await page.getByLabel("Compare with").selectOption({ label: options[1] });
  const cmp = page.getByTestId("pass-compare");
  await expect(cmp).toContainText("COUNTRY");
  await expect(cmp).toContainText("nulls 2.0% -> 17.4%");
  await expect(cmp).toContainText("1 changed");
});

test("a column annotation is sent for review, and a pending one shows on the profile", async ({ page }) => {
  await openCustomers(page);
  await expect(page.getByTitle("An annotation is waiting for review")).toBeVisible();
  await page.getByRole("button", { name: /^EMAIL/ }).click();
  await page.getByTestId("annotate-EMAIL").click();
  await page.getByLabel("PII class").fill("EMAIL");
  await page.getByLabel("Why (shown to the reviewer)").fill("Customer contact address");
  await page.getByTestId("send-annotation").click();
  await expect(page.getByText(/Annotation of EMAIL sent for review/)).toBeVisible();
});

test("a data steward approves another person's annotation and it is proposed to the model", async ({ page }) => {
  await page.goto("/dashboard/profiling?section=annotations");
  const country = page.getByTestId("annotation-COUNTRY");
  await expect(country).toContainText("Billing country, ISO 3166 alpha 2");
  await expect(country).toContainText("(profiler: COUNTRY)");
  await page.getByTestId("approve-COUNTRY").click();
  await expect(page.getByText("Approved; proposed to the domain model (CUSTOMERS)")).toBeVisible();
  await expect(page.getByTestId("annotations")).toContainText("Proposed to the model");
  await expect(page.getByTestId("annotation-EMAIL")).toContainText("Withdraw");
});

test("profiling runs on a schedule and reports drift", async ({ page }) => {
  await page.goto("/dashboard/profiling?section=schedules");
  const crm = page.getByTestId("schedule-CRM");
  await expect(crm).toContainText("every table · Daily 05:00");
  await expect(crm).toContainText("1 table drifted since the previous pass");
  await crm.getByRole("button", { name: "Run now" }).click();
  await expect(page.getByText("1 profiled, 1 unchanged, 0 failed, 1 drifted")).toBeVisible();
  await page.getByTestId("add-schedule").click();
  await page.getByLabel("Database").fill("RAW");
  await page.getByLabel("Schema").fill("SALES");
  await page.getByTestId("save-schedule").click();
  await expect(page.getByText("Profiling scheduled Daily 05:00 for RAW.SALES")).toBeVisible();
});
