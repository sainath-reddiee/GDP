import { expect, test, type BrowserContext } from "@playwright/test";

// SnapLogic ingestion control: the external onboarding wizard and the configurations in the Source Store, against the
// fake API (in memory, validated by the real service; ERP_ORACLE is already configured as master 1).
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("other sources ask which kind of source first, then that kind's questions", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=external");
  await expect(page.getByTestId("ingestion-wizard")).toHaveCount(0);
  await page.getByTestId("onboard-kind-of-source-database").click();
  await expect(page).toHaveURL(/kind=database/);
  await expect(page.getByTestId("ingestion-wizard")).toBeVisible();
  // a relational database is never asked a file type, an Excel sheet or an API
  await expect(page.getByLabel("File type")).toHaveCount(0);
  await expect(page.getByLabel("Sheet name")).toHaveCount(0);
  await page.getByTestId("onboard-kind-of-source-change").click();
  await page.getByTestId("onboard-kind-of-source-file").click();
  await expect(page.getByLabel("File type")).toBeVisible();
  await expect(page.getByLabel("Source path")).toBeVisible();
});

test("a relational database is one page: one check shows every missing field, then the review, then save", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=external&kind=database");
  const wizard = page.getByTestId("ingestion-wizard");
  await expect(wizard).toBeVisible();
  // every section is on the page at once, the defaults already filled in; the review appears only after a check
  for (const id of ["source", "tables", "target", "load", "snaplogic"]) await expect(page.getByTestId(`section-${id}`)).toBeVisible();
  await expect(page.getByTestId("section-review")).toHaveCount(0);
  await expect(page.getByLabel("Landing schema")).toHaveValue("LANDING");
  await expect(page.getByLabel("Previous load time")).toHaveCount(0);
  await expect(page.getByLabel("Secret reference")).toHaveCount(0);

  // one check: the problems of every section at once, counted per section
  await page.getByTestId("check-ingestion").click();
  for (const text of ["Source name is required", "Source account is required", "Domain is required", "Source database is required",
                      "Source schema is required", "Primary keys are required for a relational database table"]) {
    await expect(wizard.getByText(text)).toBeVisible();
  }
  await expect(page.getByTestId("section-source")).toContainText("to fix");
  await expect(page.getByTestId("ingestion-status")).toContainText("to fix");

  await page.getByLabel("Source name").fill("crm_postgres");
  await page.getByLabel("Source account").fill("crm_postgres_prod");
  await page.getByLabel("Domain").selectOption({ index: 1 });
  await page.getByLabel("Source database").fill("CRMPROD");
  await page.getByLabel("Source schema").fill("SALES");
  await page.getByLabel("Source table or query").fill("SALES.CUSTOMERS");
  await page.getByLabel("Primary keys").fill("CUSTOMER_ID");
  // delete fields stay hidden until the toggle is on
  await expect(page.getByTestId("deletes-0")).toHaveCount(0);
  await page.getByLabel("Source deletes rows").check();
  await page.locator("#dkey-0").fill("IS_DELETED");
  await page.getByLabel("Delete key value").fill("Y");
  await page.getByRole("button", { name: "Add table" }).click();
  await page.getByTestId("table-1").getByLabel("Source table or query").fill("SELECT ID, TOTAL FROM SALES.ORDERS");
  await page.getByTestId("table-1").getByLabel("Primary keys").fill("ID");
  await page.getByLabel("Landing database").fill("DEV_GDP_LANDING_DB");
  await expect(page.locator("#external_volume")).toHaveCount(0);
  await page.getByLabel("Frequency").selectOption("ON_DEMAND");
  await expect(page.getByLabel("Trigger (cron)")).toHaveCount(0);
  await page.getByLabel("SnapLogic project").fill("GDP/Ingestion");
  await page.getByLabel("Parent pipeline").fill("P_INGEST_MASTER");
  await page.getByLabel("Child pipeline").fill("C_POSTGRES_TO_LANDING");
  await page.getByTestId("check-ingestion").click();

  const review = page.getByTestId("ingestion-review");
  await expect(review).toContainText("CRM_POSTGRES__CUSTOMERS");
  await expect(review).toContainText("CRM_POSTGRES__ORDERS");
  await expect(review).toContainText("IS_DELETED = Y");
  const assigned = page.getByTestId("assigned-on-save");
  for (const col of ["MASTER_ID", "CHILD_ID", "CREATED_BY", "CREATED_AT, UPDATED_AT", "SOURCE_FILE_TYPE"]) await expect(assigned).toContainText(col);
  // a change after the check hides the review until the next check
  await page.getByLabel("Source schema").fill("SALES2");
  await expect(review).toHaveCount(0);
  await page.getByLabel("Source schema").fill("SALES");
  await expect(review).toBeVisible();
  await page.getByTestId("save-ingestion").click();
  await expect(page.getByText(/Configuration saved: master ID \d+, 2 tables registered for P_INGEST_MASTER\./)).toBeVisible();
  await expect(page.getByTestId("ingestion-saved")).toBeVisible();
});

test("a file source asks the path, file and file type; the Excel sheet only for Excel", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=external&kind=file");
  const wizard = page.getByTestId("ingestion-wizard");
  await expect(wizard).toBeVisible();
  await expect(page.getByLabel("Sheet name")).toHaveCount(0);
  await page.getByLabel("File type").selectOption("XLSX");
  await page.getByLabel("Table name").fill("SELECT * FROM X");
  await page.getByTestId("check-ingestion").click();
  for (const text of ["Source path is required", "Source file is required", "Sheet name is required for an Excel workbook"]) {
    await expect(wizard.getByText(text)).toBeVisible();
  }
  // a table name, not a query; keys are optional for an APPEND load
  await expect(wizard.getByText(/A table name for the file's rows/)).toBeVisible();
  await expect(wizard.getByText(/Primary keys are required/)).toHaveCount(0);
});

test("configurations are listed by source in the Source Store, with their history, and can be switched off", async ({ page }) => {
  await page.goto("/dashboard/sources");
  await expect(page.getByTestId("external-sources")).toBeVisible();
  const table = page.getByTestId("ingestion-table");
  await expect(table.getByTestId("master-1")).toContainText("ERP_ORACLE");
  await expect(table.getByTestId("master-1")).toContainText("2 tables · 2 active");
  await table.getByRole("switch", { name: "ERP_ORACLE__ACCOUNTS active" }).click();
  await expect(page.getByText("ERP_ORACLE__ACCOUNTS deactivated; SnapLogic skips it from now on.")).toBeVisible();
  await expect(table.getByRole("switch", { name: "ERP_ORACLE__ACCOUNTS active" })).toHaveAttribute("aria-checked", "false");
  await table.getByText("GL.JOURNAL_LINES").click();
  const detail = page.getByTestId("ingestion-detail");
  await expect(detail).toContainText("P_INGEST_MASTER");
  await expect(detail).toContainText("vault:snaplogic/ingest");
  await expect(page.getByTestId("ingestion-history")).toContainText("created");
});
