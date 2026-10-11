import { expect, test, type BrowserContext } from "@playwright/test";

// QA packs and Git, and test history, against the fake API (dev sign-in as ANA): "Business keys" has a repository
// conflict, "Referential integrity" has an approved v2, and the workspace test "Orphan addresses" has two versions.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("a repository conflict is shown on the pack and resolved by taking the repository version", async ({ page }) => {
  await page.goto("/dashboard/qa-store");
  await expect(page.getByTestId("pack-Business keys")).toContainText("The repository file changed since the app edits");
  await page.getByTestId("pack-Business keys").click();
  const banner = page.getByTestId("pack-conflict");
  await expect(banner).toContainText("domain/company/qa/business_keys.sql");
  await page.getByTestId("take-repo").click();
  await expect(page.getByText("The repository version is now draft v2; submit it for approval")).toBeVisible();
  await expect(page.getByTestId("pack-conflict")).toHaveCount(0);
});

test("an approved pack version is published to Git", async ({ page }) => {
  await page.goto("/dashboard/qa-store");
  await page.getByTestId("pack-Referential integrity").click();
  await page.getByRole("button", { name: "Git" }).click();
  await expect(page.getByLabel("Repository")).toHaveValue("repo-1");
  await page.getByTestId("publish-pack").click();
  await expect(page.getByText("Pull request opened for v2: feature/qa-referential-integrity-v2")).toBeVisible();
  await page.getByRole("button", { name: "Check pull requests" }).click();
  await expect(page.getByText("1 pull request(s) checked, 1 merged")).toBeVisible();
});

test("a saved workspace test shows its history and an older version can be restored", async ({ page }) => {
  await page.goto("/qa?tab=suites&table=tt-company-core");
  await page.getByText("Orphan addresses").first().click();
  await page.getByTestId("history-t-orphans").click();
  const history = page.getByTestId("test-history");
  await expect(history).toContainText("severity: HIGH to CRITICAL");
  await expect(history).toContainText("+WHERE a.COMPANY_ID IS NULL;");
  await page.getByTestId("restore-v1").click();
  await expect(page.getByText("Version 1 restored.").first()).toBeVisible();
});
