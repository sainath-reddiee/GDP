import { expect, test, type BrowserContext } from "@playwright/test";

// Airflow failure resolution (Operations > Resolutions and Known fixes): the queue with its filters, the guardrails,
// the known-fix catalogue and the new-fix form, and the workspace tabs. Read only: nothing is saved, published,
// approved or switched on.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.use({ viewport: { width: 1440, height: 900 } });
test.beforeEach(async ({ context }) => devSession(context));

test("Resolutions: the queue, its status filters and the workspace tabs", async ({ page }) => {
  await page.goto("/ops/resolutions");
  await expect(page.getByRole("heading", { name: "Resolutions", exact: true })).toBeVisible({ timeout: 90_000 });
  const tabs = page.getByRole("navigation", { name: "Operations pages" });
  await expect(tabs.getByRole("link", { name: "Resolutions" })).toHaveAttribute("aria-current", "page");
  await expect(tabs.getByRole("link", { name: "Known fixes" })).toBeVisible();
  await expect(page.getByText("Fixed by a known fix (30 days)")).toBeVisible();
  await page.getByRole("link", { name: /^Ticketed/ }).click();
  await page.waitForURL(/status=TICKETED/);
  await expect(page.getByTestId("failure-table").or(page.getByText(/No failures in this state/))).toBeVisible();
});

test("Resolutions: approvals and guardrails views", async ({ page }) => {
  await page.goto("/ops/resolutions?view=approvals");
  await expect(page.getByRole("navigation", { name: "Resolution views" }).getByRole("link", { name: /Approvals/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("fix-approvals").or(page.getByText("No fix waits for approval."))).toBeVisible({ timeout: 60_000 });
  await page.goto("/ops/resolutions?view=guardrails");
  const g = page.getByTestId("guardrails");
  await expect(g).toBeVisible({ timeout: 60_000 });
  await expect(g.getByText("Automatic resolution", { exact: true })).toBeVisible();
  await expect(g.getByRole("heading", { name: "Pipeline scope" })).toBeVisible();
  await expect(g.getByRole("heading", { name: "Fix approvers" })).toBeVisible();
});

test("Known fixes: catalogue, skills and the new-fix form (not saved)", async ({ page }) => {
  await page.goto("/ops/fixes");
  await expect(page.getByRole("heading", { name: "Known fixes", exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("heading", { name: /Multi-step fixes/ })).toBeVisible();
  await page.getByRole("link", { name: "New fix" }).click();
  await page.waitForURL(/\/ops\/fixes\/new/);
  const form = page.getByTestId("fix-form");
  await expect(form.getByText("Match text")).toBeVisible();
  await form.getByRole("radio", { name: "Several steps (skill)" }).check();
  await expect(form.getByText("Skill", { exact: true })).toBeVisible();
  await expect(form.getByRole("checkbox", { name: /Ask for human approval/ })).toBeChecked();
});
