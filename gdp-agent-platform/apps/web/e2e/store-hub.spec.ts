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

test("Stores stays highlighted inside any store", async ({ page }) => {
  await page.goto("/dashboard/sttm/c1");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Stores", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Store›Data Mapping Store›DIM_CUSTOMER");
});

test("collapsed, a workspace icon opens a flyout of its pages", async ({ page }) => {
  await page.goto("/dashboard");
  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("button", { name: "Collapse sidebar" }).click();
  await nav.getByRole("link", { name: "Stores", exact: true }).hover();
  const flyout = page.getByRole("menu", { name: "Stores" });
  await expect(flyout.getByRole("menuitem", { name: /Data Quality Store/ })).toBeVisible();
  await flyout.getByRole("menuitem", { name: /Profiling Store/ }).click();
  await page.waitForURL(/\/dashboard\/profiling$/);
  await nav.getByRole("button", { name: "Expand sidebar" }).click();
});

test("run pages are unchanged", async ({ page }) => {
  await page.goto(`/runs/${RUN}/source`);
  await expect(page).toHaveURL(new RegExp(`/runs/${RUN}/source$`));
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Stores", exact: true })).not.toHaveAttribute("aria-current", "page");
});

test("the Source Store shows source-only figures and keeps profiles in the Profiling Store", async ({ page }) => {
  await page.goto("/dashboard/sources");
  const kpis = page.getByTestId("source-kpis");
  for (const label of ["Sources connected", "Ingestion pipelines", "Landed tables", "Ingestion issues"]) await expect(kpis).toContainText(label);
  await expect(page.getByRole("tab", { name: /Profile store/ })).toHaveCount(0);
  await expect(page.getByText("Schemas profiled")).toHaveCount(0);
  // one Source Store: the catalog, and an External sources panel only once something is onboarded externally
  await expect(page.getByRole("tab", { name: /External systems/ })).toHaveCount(0);
  await expect(page.getByText("SnapLogic ingestion")).toHaveCount(0);
});
