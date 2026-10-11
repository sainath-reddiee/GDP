import { expect, test, type BrowserContext } from "@playwright/test";

// The Store: one sidebar item over the six asset stores, its hub page, and the tab bar across stores. Dev sign-in.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";
const RUN = "11111111-aaaa-bbbb-cccc-000000000001";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("the Store hub shows every store and its tab bar keeps the domain", async ({ page }) => {
  await page.goto("/dashboard/store?domain=COMPANY");
  await expect(page.getByRole("heading", { name: "Store", exact: true })).toBeVisible();
  for (const store of ["source", "profiling", "models", "sttm", "qa", "dq"]) await expect(page.getByTestId(`store-${store}`)).toBeVisible();
  await page.getByTestId("store-nav-sttm").click();
  await page.waitForURL(/\/dashboard\/sttm\?domain=COMPANY$/);
  await expect(page.getByTestId("store-nav-sttm")).toHaveAttribute("aria-current", "page");
  await page.getByTestId("store-nav-quality").click();
  await page.waitForURL(/\/dashboard\/dq-store\?domain=COMPANY$/);
  await page.goBack();
  await page.waitForURL(/\/dashboard\/sttm\?domain=COMPANY$/);
  await expect(page.getByTestId("store-nav-sttm")).toHaveAttribute("aria-current", "page");
});

test("a hub tab link and the short names open each store's own page", async ({ page }) => {
  await page.goto("/dashboard/store?tab=quality&status=ACTIVE");
  await page.waitForURL(/\/dashboard\/dq-store\?status=ACTIVE$/);
  await page.goto("/dashboard/qa");
  await page.waitForURL(/\/dashboard\/qa-store$/);
  await page.goto("/store");
  await page.waitForURL(/\/dashboard\/store$/);
});

test("Store stays highlighted inside any store and its sections can be hidden", async ({ page }) => {
  await page.goto("/dashboard/sttm/c1");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "STTM Store" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Store›STTM Store›DIM_CUSTOMER");
  await nav.getByRole("button", { name: "Hide Store sections" }).click();
  await expect(nav.getByRole("link", { name: "STTM Store" })).toHaveCount(0);
  await nav.getByRole("button", { name: "Show Store sections" }).click();
  await expect(nav.getByRole("link", { name: "QA Store" })).toBeVisible();
});

test("collapsed, the Store icon opens a flyout of the stores", async ({ page }) => {
  await page.goto("/dashboard");
  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("button", { name: "Collapse sidebar" }).click();
  await nav.getByRole("link", { name: "Store", exact: true }).hover();
  const flyout = page.getByRole("menu", { name: "Store" });
  await expect(flyout.getByRole("menuitem", { name: /Data Quality Store/ })).toBeVisible();
  await flyout.getByRole("menuitem", { name: /Profiling Store/ }).click();
  await page.waitForURL(/\/dashboard\/profiling$/);
  await nav.getByRole("button", { name: "Expand sidebar" }).click();
});

test("run pages are unchanged", async ({ page }) => {
  await page.goto(`/runs/${RUN}/source`);
  await expect(page).toHaveURL(new RegExp(`/runs/${RUN}/source$`));
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Store", exact: true })).not.toHaveAttribute("aria-current", "page");
});
