import { expect, test, type BrowserContext } from "@playwright/test";

// Data Quality suites against the fake API (dev sign-in as ANA): CUSTOMERS has v2 in review by BEN (one check softened,
// one added), ORDERS has only its approved v1.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";
const CUSTOMERS = "/dashboard/dq-store/suite?table=RAW.CRM.CUSTOMERS";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("the catalog shows what runs and what is in review", async ({ page }) => {
  await page.goto("/dashboard/dq-store");
  const strip = page.getByTestId("suite-CUSTOMERS");
  await expect(strip).toContainText("Running approved v1");
  await expect(strip).toContainText("v2 in review (BEN)");
  await expect(page.getByTestId("suite-ORDERS")).toContainText("Running approved v1");
});

test("a check edit lands in a draft, and its author cannot approve it", async ({ page }) => {
  await page.goto("/dashboard/dq-store?table=RAW.CRM.ORDERS");
  await page.getByRole("button", { name: "Add a check to RAW.CRM.ORDERS" }).click();
  await page.getByLabel("SodaCL").fill("missing_count(ORDER_ID) = 0");
  await page.getByTestId("save-check").click();
  await expect(page.getByText(/Check added in draft v2 of ORDERS \(draft opened\)/).first()).toBeVisible();
  await expect(page.getByTestId("suite-ORDERS")).toContainText("Draft v2 by ANA");
  await page.goto("/dashboard/dq-store/suite?table=RAW.CRM.ORDERS");
  await expect(page.getByTestId("suite-diff")).toContainText("1 stricter");
  await page.getByLabel("Change note").fill("Every order has an id");
  await page.getByTestId("suite-submit").click();
  await expect(page.getByText("v2 sent for review")).toBeVisible();
  await expect(page.getByTestId("suite-decision")).toContainText("You submitted or edited this version");
});

test("a reviewer sees the changes and the model check, and approves with the loosening confirmed", async ({ page }) => {
  await page.goto(CUSTOMERS);
  const diff = page.getByTestId("suite-diff");
  await expect(diff).toContainText("1 looser");
  await expect(diff).toContainText("1 stricter");
  await expect(diff).toContainText("severity fail to warn");
  await expect(page.getByTestId("suite-model")).toContainText("CUSTOMERS in the approved model v2");
  await expect(page.getByTestId("suite-model")).toContainText("COUNTRY is not a column of the approved model");
  await expect(page.getByTestId("suite-approve")).toBeDisabled();
  await page.getByTestId("suite-ack").check();
  await page.getByTestId("suite-approve").click();
  await expect(page.getByText("v2 approved; 5 checks now run on CUSTOMERS")).toBeVisible();
});

test("versions compare, export renders SodaCL, and the approved version is published to Git", async ({ page }) => {
  await page.goto(`${CUSTOMERS}&tab=versions`);
  await expect(page.getByTestId("suite-versions")).toContainText(/superseded/i);
  await page.getByLabel("From", { exact: true }).selectOption("1");
  await page.getByLabel("To", { exact: true }).selectOption("2");
  await page.getByTestId("suite-compare").click();
  await expect(page.getByTestId("suite-diff")).toContainText("COUNTRY");
  await page.getByTestId("suite-tab-export").click();
  await expect(page.getByTestId("suite-export")).toContainText("checks for RAW.CRM.CUSTOMERS");
  await page.getByRole("radio", { name: "Great Expectations" }).click();
  await expect(page.getByTestId("suite-export")).toContainText("\"expectations\"");
  await page.getByTestId("suite-tab-publish").click();
  await page.getByTestId("suite-publish-version").click();
  await expect(page.getByText("Pull request opened for v2: feature/dq-customers-v2")).toBeVisible();
  await page.getByRole("button", { name: "Check pull requests" }).click();
  await expect(page.getByTestId("suite-publish")).toContainText("merged");
});
