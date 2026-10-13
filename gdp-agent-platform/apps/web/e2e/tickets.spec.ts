import { expect, test, type BrowserContext, type Page } from "@playwright/test";

// The Tickets page (/tickets): Jira issues and platform cases in one list, filters kept in the URL, the Jira search and a
// ticket's triage, sprints, and the entry points (sidebar, top bar, Command Center, search, Copilot hand-off).
// Read only: the New ticket form is opened and checked, never submitted; nothing is moved to a sprint.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";
const KEY = process.env.AIP_E2E_JIRA_KEY ?? "GDPQA-1";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.use({ viewport: { width: 1440, height: 900 } });
test.beforeEach(async ({ context }) => devSession(context));

/** Waits for the list to load: the counts line ("N tickets · ..."). */
async function listLoaded(page: Page) {
  await expect(page.getByText(/\d+ tickets · \d+ Jira · \d+ cases/)).toBeVisible({ timeout: 90_000 });
}

test("one list of Jira tickets and cases, with filters kept in the URL", async ({ page }) => {
  await page.goto("/tickets");
  await expect(page.getByRole("heading", { name: "Tickets", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "All tickets" })).toHaveAttribute("aria-selected", "true");
  await listLoaded(page);
  await page.getByRole("radiogroup", { name: "Assignee" }).getByRole("radio", { name: "Me" }).click();
  await page.waitForURL(/assignee=me/);
  await page.getByRole("radiogroup", { name: "Source" }).getByRole("radio", { name: "Cases" }).click();
  await page.waitForURL(/source=case/);
  await listLoaded(page);
  await expect(page.getByText(/\d+ tickets · 0 Jira/)).toBeVisible();   // cases only
  await page.getByRole("button", { name: "More filters" }).click();
  await expect(page.getByLabel("Sprint", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "SLA breached" })).toBeVisible();
  await page.goto("/tickets?source=jira&priority=P1,P3&status=any");
  await expect(page.getByRole("radiogroup", { name: "Source" }).getByRole("radio", { name: "Jira" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByRole("button", { name: "P3" })).toHaveAttribute("aria-pressed", "true");
});

test("old QA workspace and Jira links open the Tickets page; QA testing keeps suites and results", async ({ page }) => {
  await page.goto("/qa?tab=cases&mine=1");
  await page.waitForURL(/\/tickets\?source=case&assignee=me/);
  await page.goto("/qa?tab=inbox");
  await page.waitForURL(/\/tickets\?tab=jira$/);
  await page.goto(`/qa?tab=triage&key=${KEY}`);
  await page.waitForURL(new RegExp(`/tickets\\?tab=ticket&key=${KEY}$`));
  await page.goto(`/jira?key=${KEY}`);
  await page.waitForURL(new RegExp(`/tickets\\?tab=ticket&key=${KEY}$`));
  await expect(page.getByRole("tab", { name: "Ticket", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.goto("/qa");
  await expect(page.getByRole("heading", { name: "QA testing" })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "QA views" }).getByRole("tab")).toHaveText(["Suites", "Results"]);
});

test("Tickets sits under the Copilot, in the top bar and on the Command Center", async ({ page }) => {
  await page.goto("/dashboard");
  const overview = page.getByRole("navigation").getByRole("link", { name: /^(Command Center|Copilot|Tickets)/ });
  await expect(overview.nth(2)).toHaveAttribute("href", "/tickets");
  await expect(page.getByRole("navigation").getByRole("link", { name: /^QA testing/ })).toHaveAttribute("href", "/qa");
  await expect(page.getByTestId("tickets-shortcut")).toHaveAttribute("href", "/tickets");
  await expect(page.getByTestId("tickets-card")).toBeVisible();
});

test("a ticket opens in the Copilot, attached, with a first message ready", async ({ page }) => {
  await page.goto(`/dashboard/copilot?jira=${KEY}`);
  await expect(page.getByRole("button", { name: `Remove ${KEY}` })).toBeVisible();
  await expect(page.getByLabel("Message the copilot")).toHaveValue(new RegExp(`^Work on ${KEY}:`));
  await page.goto("/dashboard/copilot?jira=not-a-key");
  await expect(page.getByLabel("Message the copilot")).toHaveValue("");   // anything else is ignored
});

test("a Jira ticket offers the Copilot and its sprint; New ticket checks before creating", async ({ page }) => {
  await page.goto(`/tickets?tab=ticket&key=${KEY}`);
  const work = page.getByTestId("work-in-copilot");
  await expect.poll(async () => (await work.isVisible())
    || (await page.getByRole("main").getByText(/Connect Jira|Reconnect Jira|not found|cannot see it|not set up/i).first().isVisible()),
    { timeout: 90_000 }).toBe(true);
  test.skip(!(await work.isVisible()), "the ticket is not readable for this user");
  await expect(work).toHaveAttribute("href", `/dashboard/copilot?jira=${KEY}`);
  await expect(page.getByTestId("sprint-control")).toBeVisible();

  await page.getByRole("button", { name: "New ticket" }).click();
  const jiraItem = page.getByRole("menuitem", { name: /Jira ticket/ });
  test.skip(await jiraItem.isDisabled(), "creating Jira tickets needs JIRA.WRITE and a connected account");
  await expect(page.getByRole("menuitem", { name: /Platform case/ })).toBeVisible();
  await jiraItem.click();
  const dialog = page.getByRole("dialog").filter({ hasText: "New Jira ticket" });
  await expect(dialog.getByLabel("Project")).not.toHaveValue("", { timeout: 30_000 });
  await expect(dialog.getByLabel("Type")).not.toHaveValue("", { timeout: 30_000 });
  const submit = dialog.getByRole("button", { name: "Create ticket" });
  await expect(submit).toBeDisabled();                         // no summary yet
  await dialog.getByLabel("Summary").fill("E2E check, never submitted");
  await expect(submit).toBeEnabled();
  await dialog.getByRole("button", { name: "Cancel" }).click();   // nothing is created
  await expect(dialog).toHaveCount(0);
});

test("selecting Jira tickets offers moving them to a sprint", async ({ page }) => {
  await page.goto("/tickets?source=jira&status=any");
  await listLoaded(page);
  const box = page.getByRole("checkbox", { name: /^Select [A-Z][A-Z0-9_]*-\d+$/ }).first();
  test.skip(!(await box.isVisible()), "no Jira tickets for this user");
  await box.check();
  const bar = page.getByTestId("sprint-bar");
  await expect(bar).toBeVisible();
  await expect(bar.getByLabel("Move to sprint")).toBeVisible();
  await expect(bar.getByRole("button", { name: "Move" })).toBeDisabled();   // nothing chosen, nothing moved
});

test("search finds a ticket by key", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByTestId("open-palette").click();
  await page.keyboard.type(KEY);
  const hit = page.getByRole("option", { name: new RegExp(KEY) }).first();
  const found = await hit.waitFor({ timeout: 30_000 }).then(() => true, () => false);
  test.skip(!found, "Jira is not connected for this user, or the ticket is not visible");
  await hit.click();
  await page.waitForURL(new RegExp(`/tickets\\?tab=ticket&key=${KEY}$`));
});
