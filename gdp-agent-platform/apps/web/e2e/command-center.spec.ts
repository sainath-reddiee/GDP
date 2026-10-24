import { expect, test, type BrowserContext } from "@playwright/test";

// Read-only checks of the shell, the Command Center and the two stores in both themes. Dev sign-in only
// (AIP_AUTH=dev): the aip_dev cookie stands in for the login.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext, theme: "light" | "dark") {
  // these checks are of the classic layout, which admins keep behind the "Classic layout" switch (ados_classic)
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }, { name: "ados_classic", value: "1", url: BASE }]);
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
      const ribbon = page.getByTestId("hero-ribbon");
      for (const stat of ["Runs, last 7 days", "Awaiting human approval", "Stale landed tables", "Failing checks"]) await expect(ribbon).toContainText(stat);
      for (const store of ["source", "profiling", "models", "sttm", "qa", "dq"]) await expect(page.getByTestId(`store-${store}`)).toBeVisible();
      await expect(page.getByTestId("pipeline-table")).toBeVisible();
      // nine workspaces in four sections; a workspace's pages are tabs on the page, not rows in the sidebar
      const nav = page.getByRole("navigation", { name: "Main" });
      for (const section of ["Work", "Build", "Run", "Govern"]) await expect(nav.getByText(section, { exact: true })).toBeVisible();
      for (const [name, href] of [["My Work", "/work"], ["Overview", "/dashboard"], ["Pipelines", "/runs"], ["Stores", "/dashboard/store"],
                                  ["Knowledge", "/domains"], ["Quality", "/dashboard/quality"], ["Governance", "/approvals"]]) {
        await expect(nav.getByRole("link", { name, exact: true })).toHaveAttribute("href", href);
      }
      await expect(nav.getByRole("link", { name: "Source Store" })).toHaveCount(0);
      // the stores' own tab bar lists the six stores on the Store hub
      await page.goto("/dashboard/store");
      for (const store of ["sources", "profiling", "modeling", "sttm", "qa", "quality"]) await expect(page.getByTestId(`store-nav-${store}`)).toBeVisible();
      await expect(nav.getByRole("link", { name: "Stores", exact: true })).toHaveAttribute("aria-current", "page");
      // other workspaces get the shell's tab bar on their pages
      await page.goto("/runs");
      const tabs = page.getByTestId("workspace-tabs");
      for (const tab of ["Silver Pipeline", "Gold Pipeline", "Lineage"]) await expect(tabs.getByRole("link", { name: tab })).toBeVisible();
      await tabs.getByRole("link", { name: "Gold Pipeline" }).click();
      await page.waitForURL(/\/gold$/);
      await expect(nav.getByRole("link", { name: "Pipelines", exact: true })).toHaveAttribute("aria-current", "page");
      // the build is not shown to people; support reads it from the page metadata (and /bff/version)
      await expect(page.getByTestId("build-stamp")).toHaveCount(0);
      await expect(page.locator('meta[name="aip-build"]')).toHaveAttribute("content", /.+/);
    });

    test("a store card's action starts the task, the card opens the store", async ({ page }) => {
      await page.goto("/dashboard");
      await page.getByTestId("store-qa-action").click();
      await page.waitForURL(/\/dashboard\/qa-store\?new=1/);
      await expect(page.getByRole("dialog").getByText("New QA pack")).toBeVisible();
      await page.goto("/dashboard");
      await page.getByRole("link", { name: "Open the Data Mapping Store" }).click();
      await page.waitForURL(/\/dashboard\/sttm$/);
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

test("source onboarding is the administrator's wizard, one choice per step; domains are onboarded in Enable too", async ({ page, context }) => {
  await devSession(context, "light");
  await page.goto("/dashboard/sources/new?branch=external");
  await page.waitForURL(/\/dashboard\/onboarding\?type=source&branch=external/);
  await expect(page.getByRole("heading", { name: "Onboard a source" })).toBeVisible();
  // sources only: no kind picker, and a deep link arrives with its choice made
  await expect(page.getByTestId("onboard-kind-domain")).toHaveCount(0);
  await expect(page.getByTestId("onboard-source-chosen")).toContainText("Other sources");
  // other sources then ask which kind of source it is, before the wizard
  await expect(page.getByTestId("onboard-kind-of-source-database")).toBeVisible();
  await expect(page.getByTestId("onboard-kind-of-source-file")).toBeVisible();
  // the page without a kind is the source wizard
  await page.goto("/dashboard/onboarding");
  await page.waitForURL(/\/dashboard\/onboarding\?type=source$/);
  // a domain (and an old link to one) opens Enable, Onboard a domain: the starter files and every accepted format
  await page.goto("/dashboard/onboarding?type=domain&import=1");
  await page.waitForURL(/\/os\/enable\/new-domain\?import=1$/);
  await expect(page.getByTestId("import-domain")).toBeVisible();
  await expect(page.getByTestId("template-viewer")).toContainText("domain_pack.json");
  await expect(page.getByTestId("formats-matrix")).toContainText("Erwin .xml");
});

test("a Data Mapping Store edit is staged and sent for approval", async ({ page, context }) => {
  await devSession(context, "light");
  await page.goto("/dashboard/sttm/c1");
  await expect(page.getByTestId("pending-changes")).toContainText("1 mapping change waiting for approval");
  await page.getByTestId("line-FULL_NAME").click();
  await page.getByLabel("Target type").fill("VARCHAR(300)");
  await page.getByTestId("stage-line").click();
  await expect(page.getByTestId("change-bar")).toContainText("1 change staged");
  await page.getByTestId("sttm-tab-joins").click();
  await expect(page.getByTestId("filters-editor").getByLabel("Filter condition")).toHaveValue("IS_DELETED = FALSE");
  await page.getByTestId("sttm-tab-sql").click();
  await expect(page.getByLabel("Model SQL")).toHaveValue(/WHERE \(IS_DELETED = FALSE\)/);
  await page.getByLabel("Change note").fill("Wider names for the new CRM");
  await page.getByTestId("submit-change").click();
  await expect(page.getByText("Sent to STTM_APPROVER for approval")).toBeVisible();
  await expect(page.getByTestId("change-bar")).toHaveCount(0);
});

test("the Modeling Store shows run models and dbt read-only with its changes", async ({ page, context }) => {
  await devSession(context, "light");
  await page.goto("/dashboard/modeling?view=runs");
  await expect(page.getByTestId("run-models")).toContainText("CRM customers to silver");
  await page.getByTestId("store-tab-dbt").click();
  await expect(page.getByTestId("dbt-generations")).toContainText("Data Mapping changed since; regenerate");
  await page.getByTestId("dbt-g1").click();
  await page.waitForURL(/\/dashboard\/modeling\/dbt\/g1/);
  await expect(page.getByTestId("dbt-readonly")).toBeVisible();
  await expect(page.getByLabel("File content")).toHaveAttribute("readonly", "");
  await expect(page.getByTestId("change-bar")).toHaveCount(0);
  await page.getByTestId("dbt-changes").click();
  await expect(page.getByTestId("dbt-diff")).toContainText("Compared with generation v1");
});

test("the DQ Store decides run checks through approval", async ({ page, context }) => {
  await devSession(context, "light");
  await page.goto("/dashboard/dq-store?view=runs&run=r1");
  await expect(page.getByTestId("run-checks")).toContainText("2 approved · 2 proposed");
  await page.getByRole("button", { name: "Coverage" }).click();
  await expect(page.getByTestId("coverage")).toContainText("80% of 5 Data Mapping columns");
  await page.getByTestId("check-e1").click();
  await expect(page.getByText("4100 rows with a null EMAIL")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByLabel("Select COUNTRY_CODE ACCEPTED_VALUES").check();
  await page.getByTestId("approve-checks").click();
  await page.getByLabel("Justification").fill("Agreed with the client");
  await page.getByTestId("confirm-decision").click();
  await expect(page.getByText("Sent to DQ_APPROVER for approval")).toBeVisible();
});

test("the QA Store promotes run tests into a pack draft", async ({ page, context }) => {
  await devSession(context, "light");
  await page.goto("/dashboard/qa-store?view=tests");
  await expect(page.getByTestId("run-tests")).toContainText("Every order has a customer");
  await page.getByTestId("test-t0").click();
  await expect(page.getByText("2 rows returned; expected 0")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByLabel("Select Every order has a customer").check();
  await page.getByTestId("promote-tests").click();
  await page.getByLabel("New pack name").fill("Customer integrity");
  await page.getByTestId("confirm-promote").click();
  await expect(page.getByText("1 test added to a pack draft")).toBeVisible();
});
