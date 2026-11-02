import { expect, test, type BrowserContext } from "@playwright/test";

// Share onboarding against the fake API: PARTNER_SALES is mounted (PUBLIC has three tables), WEATHER_DAILY is not.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("a share's schemas and tables are listed, landing is chosen from lists, and Load continues to profiling", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=share");
  const wizard = page.getByTestId("share-wizard");
  await expect(wizard.getByTestId("share-run")).toBeDisabled();  // nothing is preselected
  await wizard.getByLabel("Database or share").selectOption({ label: "WEATHER_DAILY (not mounted)" });
  await expect(wizard.getByTestId("mount-share")).toBeVisible();  // not mounted yet: offered to mount now
  await wizard.getByLabel("Database or share").selectOption({ label: "PARTNER_SALES" });
  await expect(wizard.getByTestId("mount-share")).toHaveCount(0);

  // schemas and tables are listed as soon as the choice before them is made
  await wizard.getByLabel("Schema", { exact: true }).selectOption("PUBLIC");
  await expect(wizard).toContainText("0 of 3 selected");
  await wizard.getByRole("checkbox", { name: /ORDERS/ }).check();
  await wizard.getByRole("checkbox", { name: /CUSTOMERS/ }).check();

  await expect(wizard.getByLabel("Source system name")).toHaveValue("PARTNER_SALES");
  await wizard.getByLabel("Landing schema").selectOption("RAW");
  await expect(wizard.getByTestId("landing-preview")).toContainText("RAW.PARTNER_SALES__ORDERS");

  await wizard.getByTestId("share-run").click();
  await expect(wizard.getByTestId("share-result")).toContainText("2 tables landed");
  await expect(wizard.getByRole("link", { name: /Continue to profiling/ })).toHaveAttribute("href", /\/profile$/);
});

test("choosing another share clears the schema and tables picked before", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=share");
  const wizard = page.getByTestId("share-wizard");
  await wizard.getByLabel("Database or share").selectOption({ label: "PARTNER_SALES" });
  await wizard.getByLabel("Schema", { exact: true }).selectOption("PUBLIC");
  await expect(wizard).toContainText("0 of 3 selected");
  await wizard.getByLabel("Database or share").selectOption({ label: "WEATHER_DAILY (not mounted)" });
  await wizard.getByLabel("Database or share").selectOption({ label: "PARTNER_SALES" });
  await expect(wizard.getByLabel("Schema", { exact: true })).toHaveValue("");
  await expect(wizard.getByTestId("share-run")).toBeDisabled();
});
