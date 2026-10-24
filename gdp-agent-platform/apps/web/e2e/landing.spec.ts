import { expect, test } from "@playwright/test";

// Signed out, /login is the front door: what the platform does, with sign-in as a small dialog from the top bar.
// Dev sign-in only (AIP_AUTH=dev): the SSO button sets the aip_dev cookie and opens the Command Center.

test("the home page explains the platform before sign-in", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/$/);   // the product page itself, not a redirect to sign-in
  await expect(page).toHaveTitle("Agentic Data Operating System");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Humans approve");
  await expect(page.getByTestId("run-preview")).toBeVisible();
  for (const id of ["agent", "knowledge", "features", "how", "governance", "value", "sources"]) {
    await page.locator(`#${id}`).scrollIntoViewIfNeeded();
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
  await expect(page.locator("#how")).toContainText("Map the fields");
  await expect(page.locator("#how")).not.toContainText("()");   // plain words, no function names
  await expect(page.locator("#knowledge")).toContainText("Which knowledge each stage reads");
  await page.getByTestId("feature-learning").click();
  await expect(page.locator("#features")).toContainText("Replayed on past runs");
  // the form is not on the page until it is asked for
  await expect(page.getByTestId("sign-in-dialog")).toHaveCount(0);
});

test("/login opens the sign-in dialog over the product page", async ({ page }) => {
  await page.goto("/login");
  await expect(page).toHaveTitle("Agentic Data Operating System · Sign in");
  await expect(page.getByTestId("sign-in-dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("sign-in-dialog")).toHaveCount(0);
  await expect(page.locator("#how")).toBeAttached();
});

test("a protected page visited signed out goes to sign-in", async ({ page }) => {
  await page.goto("/runs");
  await page.waitForURL(/\/login$/);
  await expect(page.getByTestId("sign-in-dialog")).toBeVisible();
});

test("sign in opens a small dialog and lands on Home", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("open-sign-in").click();
  const dialog = page.getByTestId("sign-in-dialog");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByTestId("open-sign-in").click();
  await dialog.getByRole("button", { name: /Continue with Snowflake SSO/ }).click();
  await page.waitForURL(/\/os$/);
  await expect(page.getByRole("navigation", { name: "Agentic Data OS" }).first().getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
});

test("an ended session opens the dialog with a notice", async ({ page }) => {
  await page.goto("/login?expired=1");
  await expect(page.getByTestId("sign-in-dialog")).toContainText("Your session ended");
});

test("Home is where everyone lands: the stage strip, the build workflows and Start a workflow; the Inbox keeps its address", async ({ page, context }) => {
  await context.addCookies([{ name: "aip_dev", value: "1", url: process.env.AIP_WEB_URL ?? "http://localhost:3000" }]);
  await page.goto("/");
  await page.waitForURL(/\/os$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Build workflows");
  await expect(page.getByTestId("home-views")).toBeVisible();
  await expect(page.getByTestId("start-panel")).toBeVisible();
  await expect(page.getByTestId("stage-strip")).toBeVisible();
  // the Inbox is no longer a tab of Home, and keeps its address
  await page.goto("/os/work/inbox");
  for (const id of ["tiles", "approve", "runs", "fix", "knowledge", "proposals", "prs"]) await expect(page.getByTestId(`work-${id}`)).toBeVisible();
});
