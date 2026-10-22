import { expect, test, type BrowserContext } from "@playwright/test";

// Source control and the Approvals preview, against the fake API (dev sign-in as ANA, an STTM and DQ approver):
// BEN's pending STTM edit and schedule change, ANA's applied run check decisions, and four pull requests (one per kind).
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("a Data Mapping edit request shows each line, design change and the requester's note before approval", async ({ page }) => {
  await page.goto("/approvals?scope=inbox");
  await page.getByRole("button", { name: /Edit a mapping in the Data Mapping catalog/ }).click();
  const preview = page.getByTestId("request-preview");
  await expect(preview).toContainText("Data Mapping edit of SILVER.COMPANY.DIM_CUSTOMER on v3: 1 added, 1 removed, 1 changed");
  await expect(page.getByTestId("preview-Lines changed")).toContainText("FULL_NAME · transformation");
  await expect(page.getByTestId("preview-Lines changed")).toContainText("INITCAP(TRIM(NAME)) || ''");
  await expect(page.getByTestId("preview-Lines added")).toContainText("CUSTOMERS.EMAIL");
  await expect(page.getByTestId("preview-Lines removed")).toContainText("COUNTRY_CODE");
  await expect(page.getByTestId("preview-Table design")).toContainText("one row per customer and source");
  await expect(page.getByTestId("preview-Requester's note")).toContainText("Align with the CRM contract");
  await expect(preview.getByRole("link", { name: "Open in Data Mapping Store" })).toHaveAttribute("href", "/dashboard/sttm/c1");
  await expect(preview.locator("pre").first()).toBeHidden();  // the stored call stays collapsed
});

test("a schedule request shows the current and requested schedule", async ({ page }) => {
  await page.goto("/approvals?scope=inbox");
  await page.getByRole("button", { name: /Schedule data quality checks/ }).click();
  const rows = page.getByTestId("preview-Schedule");
  await expect(rows).toContainText("0 6 * * * UTC");
  await expect(rows).toContainText("0 */4 * * * UTC");
  await expect(rows).toContainText("4 active check(s)");
});

test("an applied request shows the decisions beside the checks and the result in brief", async ({ page }) => {
  await page.goto("/approvals?scope=all");
  await page.getByRole("button", { name: /Decide run data quality checks/ }).click();
  const decisions = page.getByTestId("preview-Decisions");
  await expect(decisions).toContainText("COUNTRY_CODE · ACCEPTED_VALUES (e2)");
  await expect(decisions).toContainText("France onboarded");
  await expect(page.getByTestId("preview-result")).toContainText("modified");
});

test("every pull request is listed and filtered, and checking them reads merges back", async ({ page }) => {
  await page.goto("/dashboard/source-control");
  await expect(page.getByTestId("pr-list").locator(":scope > li")).toHaveCount(4);
  await page.getByRole("tab", { name: "Data Mapping" }).click();
  await expect(page.getByTestId("pr-list").locator(":scope > li")).toHaveCount(1);
  await expect(page.getByTestId("pr-STTM-1")).toContainText("Publishing failed: GitHub refused the push (403)");
  await page.getByRole("tab", { name: "All assets" }).click();
  await page.getByLabel("State").selectOption("attention");
  await expect(page.getByTestId("pr-list").locator(":scope > li")).toHaveCount(2);
  await expect(page.getByTestId("pr-QA_PACK-2")).toContainText("closed without merging");
  await page.getByLabel("State").selectOption("");
  await page.getByRole("button", { name: "Check all now" }).click();
  await expect(page.getByText("1 pull request(s) checked, 1 merged")).toBeVisible();
  await expect(page.getByTestId("pr-MODEL-3")).toContainText("merged 77aa01b");
});

test("an approved mapping version is published to Git from the Data Mapping Store", async ({ page }) => {
  await page.goto("/dashboard/sttm/c1");
  const git = page.getByTestId("sttm-git");
  await expect(git.getByLabel("Repository")).toHaveValue("repo-1");
  await page.getByTestId("publish-sttm").click();
  await expect(page.getByText("Pull request opened for v3: feature/sttm-dim-customer-v3")).toBeVisible();
  await expect(git).toContainText("#44");
});
