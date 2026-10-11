import { expect, test, type BrowserContext } from "@playwright/test";

// Share onboarding against the fake API: PARTNER_SALES is mounted (PUBLIC has three tables), WEATHER_DAILY is not.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("a share's schemas and tables are listed, landing is chosen from lists, and the run continues to profiling", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=share");
  const wizard = page.getByTestId("share-wizard");
  await expect(wizard.getByTestId("share-next")).toBeDisabled();  // nothing is preselected
  await wizard.getByTestId("share-WEATHER_DAILY").click();
  await expect(wizard.getByTestId("mount-share")).toBeVisible();  // not mounted yet: offered to mount now
  await wizard.getByTestId("share-PARTNER_SALES").click();
  await expect(wizard.getByTestId("mount-share")).toHaveCount(0);
  await wizard.getByTestId("share-next").click();

  await expect(wizard).toContainText("Schemas in PARTNER_SALES_DB");
  await wizard.getByRole("radio", { name: /PUBLIC/ }).click();
  await wizard.getByTestId("share-next").click();

  await expect(wizard).toContainText("0 of 3 selected");
  await wizard.getByRole("checkbox", { name: /ORDERS/ }).check();
  await wizard.getByRole("checkbox", { name: /CUSTOMERS/ }).check();
  await wizard.getByTestId("share-next").click();

  await expect(wizard.getByLabel("Source system name")).toHaveValue("PARTNER_SALES");
  await wizard.getByLabel("Landing schema").selectOption("RAW");
  await expect(wizard.getByTestId("landing-preview")).toContainText("RAW.PARTNER_SALES__ORDERS");
  await wizard.getByTestId("share-next").click();

  await wizard.getByTestId("share-run").click();
  await expect(wizard.getByTestId("share-result")).toContainText("2 tables landed");
  await expect(wizard.getByRole("link", { name: /Continue to profiling/ })).toHaveAttribute("href", /\/profile$/);
});

test("going back to another share clears the schema and tables picked before", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=share");
  const wizard = page.getByTestId("share-wizard");
  await wizard.getByTestId("share-PARTNER_SALES").click();
  await wizard.getByTestId("share-next").click();
  await wizard.getByRole("radio", { name: /PUBLIC/ }).click();
  await wizard.getByRole("button", { name: "Back" }).click();
  await wizard.getByTestId("share-WEATHER_DAILY").click();
  await wizard.getByTestId("share-PARTNER_SALES").click();
  await wizard.getByTestId("share-next").click();
  await expect(wizard.getByRole("radio", { name: /PUBLIC/ })).toHaveAttribute("aria-checked", "false");
  await expect(wizard.getByTestId("share-next")).toBeDisabled();
});
