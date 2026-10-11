import { expect, test, type BrowserContext } from "@playwright/test";

// Read-only checks of the shell, the Command Center and the two stores in both themes. Dev sign-in only
// (AIP_AUTH=dev): the aip_dev cookie stands in for the login.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext, theme: "light" | "dark") {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
  // seeds the theme once; a choice made on the page survives reloads
  await context.addInitScript((t) => {
    try { if (!localStorage.getItem("aip.theme")) localStorage.setItem("aip.theme", t); } catch { /* none */ }
  }, theme);
}

for (const theme of ["light", "dark"] as const) {
  test.describe(`${theme} theme`, () => {
    test.beforeEach(async ({ context }) => devSession(context, theme));

    test("command center shows the ribbon, six stores and the pipeline table", async ({ page }) => {
      await page.goto("/dashboard");
      await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark).*$/);
      await expect(page.getByTestId("hero-ribbon")).toBeVisible();
      for (const store of ["source", "profiling", "models", "sttm", "qa", "dq"]) await expect(page.getByTestId(`store-${store}`)).toBeVisible();
      await expect(page.getByTestId("pipeline-table")).toBeVisible();
    });

    test("ctrl+k opens the palette and jumps to a page", async ({ page }) => {
      await page.goto("/dashboard");
      await page.keyboard.press("Control+k");
      await expect(page.getByTestId("command-palette")).toBeVisible();
      await page.keyboard.type("DQ Store");
      await page.keyboard.press("Enter");
      await page.waitForURL(/\/dashboard\/dq-store/);
      await expect(page.getByRole("heading", { name: "Attribute checks catalog" })).toBeVisible();
    });

    test("QA Store and DQ Store render", async ({ page }) => {
      await page.goto("/dashboard/qa-store");
      await expect(page.getByRole("heading", { name: "Domain test packages" })).toBeVisible();
      await page.goto("/dashboard/dq-store");
      await expect(page.getByRole("heading", { name: "Attribute checks catalog" })).toBeVisible();
    });
  });
}

test("the theme toggle switches and remembers the theme", async ({ page, context }) => {
  await devSession(context, "light");
  await page.goto("/dashboard");
  await page.getByTestId("theme-toggle").click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
});
