import { expect, test, type BrowserContext } from "@playwright/test";

// SnapLogic ingestion control: the external onboarding wizard and the configurations in the Source Store, against the
// fake API (in memory, validated by the real service; ERP_ORACLE is already configured as master 1).
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("the wizard checks each step, hides optional fields until asked, and saves one row per table", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=external");
  const wizard = page.getByTestId("ingestion-wizard");
  await expect(wizard).toBeVisible();

  // Source: the sheet name only appears for Excel and is then required
  await expect(page.getByLabel("Sheet name")).toHaveCount(0);
  await page.getByLabel("Source format").selectOption("XLSX");
  await page.getByTestId("wizard-next").click();
  await expect(wizard.getByText("Source name is required")).toBeVisible();
  await expect(wizard.getByText("Sheet name is required for an Excel workbook")).toBeVisible();
  await page.getByLabel("Source format").selectOption("TABLE");
  await page.getByLabel("Source name").fill("crm_oracle");
  await page.getByLabel("Source account").fill("crm_oracle_prod");
  await page.getByTestId("wizard-next").click();

  // Tables: delete fields stay hidden until the toggle is on
  await page.getByLabel("Source table or query").fill("SALES.CUSTOMERS");
  await page.getByLabel("Primary keys").fill("CUSTOMER_ID");
  await expect(page.getByTestId("deletes-0")).toHaveCount(0);
  await page.getByLabel("Source deletes rows").check();
  await expect(page.getByTestId("deletes-0")).toBeVisible();
  await page.getByTestId("wizard-next").click();
  await expect(wizard.getByText("The column that marks a deleted row")).toBeVisible();
  await page.locator("#dkey-0").fill("IS_DELETED");
  await page.getByLabel("Delete key value").fill("Y");
  await page.getByRole("button", { name: "Add table" }).click();
  await page.getByTestId("table-1").getByLabel("Source table or query").fill("SELECT ID, TOTAL FROM SALES.ORDERS");
  await page.getByTestId("table-1").getByLabel("Primary keys").fill("ID");
  await page.getByTestId("table-1").getByLabel("Previous load time").fill("2026-10-01T00:00");
  await page.getByTestId("wizard-next").click();

  // Target: Iceberg fields appear with the toggle
  await page.getByLabel("Landing database").fill("DEV_GDP_LANDING_DB");
  await expect(page.locator("#external_volume")).toHaveCount(0);
  await page.getByTestId("wizard-next").click();

  // Load: the cron goes away for on demand
  await page.getByLabel("Frequency").selectOption("ON_DEMAND");
  await expect(page.getByLabel("Trigger (cron)")).toHaveCount(0);
  await page.getByTestId("wizard-next").click();

  // SnapLogic: a pasted token is refused, a secret reference is accepted
  await page.getByLabel("SnapLogic project").fill("GDP/Ingestion");
  await page.getByLabel("Parent pipeline").fill("P_INGEST_MASTER");
  await page.getByLabel("Child pipeline").fill("C_ORACLE_TO_LANDING");
  await page.getByLabel("Secret reference").fill("ghp_abcdefghijklmnopqrstuvwxyz0123456789");
  await page.getByTestId("wizard-next").click();
  await expect(wizard.getByText("This looks like a token")).toBeVisible();
  await page.getByLabel("Secret reference").fill("vault:snaplogic/ingest");
  await page.getByTestId("wizard-next").click();

  // Review and save
  const review = page.getByTestId("ingestion-review");
  await expect(review).toContainText("CRM_ORACLE__CUSTOMERS");
  await expect(review).toContainText("CRM_ORACLE__ORDERS");
  await expect(review).toContainText("IS_DELETED = Y");
  const assigned = page.getByTestId("assigned-on-save");
  for (const col of ["MASTER_ID", "CHILD_ID", "CREATED_BY", "CREATED_AT, UPDATED_AT"]) await expect(assigned).toContainText(col);
  await page.getByTestId("save-ingestion").click();
  await expect(page.getByText(/Configuration saved: master ID \d+, 2 tables registered for P_INGEST_MASTER\./)).toBeVisible();
  await expect(page.getByTestId("ingestion-saved")).toBeVisible();
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
