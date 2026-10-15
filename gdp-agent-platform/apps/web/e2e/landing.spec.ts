import { expect, test } from "@playwright/test";

// Signed out, /login is the front door: what the platform does, with sign-in as a small dialog from the top bar.
// Dev sign-in only (AIP_AUTH=dev): the SSO button sets the aip_dev cookie and opens the Command Center.

test("the landing page explains the platform before sign-in", async ({ page }) => {
  await page.goto("/login");
  await expect(page).toHaveTitle("Agentic Data Operating System · Sign in");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Humans approve");
  await expect(page.getByTestId("run-preview")).toBeVisible();
  for (const id of ["agent", "knowledge", "features", "how", "governance", "value", "sources"]) {
    await page.locator(`#${id}`).scrollIntoViewIfNeeded();
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
  await expect(page.locator("#how")).toContainText("generate_mapping_candidates()");
  await expect(page.locator("#knowledge")).toContainText("Which knowledge each stage reads");
  await page.getByTestId("feature-learning").click();
  await expect(page.locator("#features")).toContainText("Replayed on past runs");
  // the form is not on the page until it is asked for
  await expect(page.getByTestId("sign-in-dialog")).toHaveCount(0);
});

test("sign in opens a small dialog and lands on the Command Center", async ({ page }) => {
  await page.goto("/login");
  await page.getByTestId("open-sign-in").click();
  const dialog = page.getByTestId("sign-in-dialog");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByTestId("open-sign-in").click();
  await dialog.getByRole("button", { name: /Continue with Snowflake SSO/ }).click();
  await page.waitForURL(/\/dashboard$/);
  await expect(page.getByRole("navigation", { name: "Main" }).getByText("Build", { exact: true })).toBeVisible();
});

test("an ended session opens the dialog with a notice", async ({ page }) => {
  await page.goto("/login?expired=1");
  await expect(page.getByTestId("sign-in-dialog")).toContainText("Your session ended");
});
