import { expect, test, type BrowserContext, type Page } from "@playwright/test";

// The Copilot workspace (/dashboard/copilot) against the real API with dev sign-in (AIP_AUTH=dev) and migration V048.
// The first group needs no AI and writes only the signed-in person's own copilot rows (CORE.COPILOT_*).
// The "live AI" group calls Cortex and is skipped unless AIP_E2E_LIVE_AI=1; it drafts but never publishes.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";
const API = process.env.AIP_API_URL ?? "http://127.0.0.1:8001";
const LIVE_AI = process.env.AIP_E2E_LIVE_AI === "1";
const DQ_TABLE = process.env.AIP_E2E_DQ_TABLE ?? "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.NATION";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.use({ viewport: { width: 1440, height: 900 } });

// every conversation a test opens is deleted afterwards (its drafts are discarded; nothing was published), so test
// runs do not fill the person's conversation list. Dev sign-in only: with PAT sign-in the API refuses, and that is ignored.
const opened = new Set<string>();
test.beforeEach(async ({ context, page }) => {
  await devSession(context);
  opened.clear();
  page.on("framenavigated", (frame) => {
    const id = frame.url().match(/\/dashboard\/copilot\/([0-9a-f-]{36})/)?.[1];
    if (id) opened.add(id);
  });
});
test.afterEach(async ({ request }) => {
  for (const id of opened) await request.post(`${API}/api/copilot/sessions/${id}/delete`).catch(() => undefined);
});

async function openHome(page: Page) {
  await page.goto("/dashboard/copilot");
  await expect(page.getByRole("heading", { name: "What should we build?" })).toBeVisible();
  const notReady = page.getByText(/not deployed yet|cannot be reached|needs the AI\.USE privilege/);
  test.skip(await notReady.isVisible(), "the copilot is not usable for this user or environment (see the page message)");
}

/** Wait until the copilot has finished its turn (the send button stops working). */
async function turnEnds(page: Page, timeout = 300_000) {
  await expect(page.getByRole("button", { name: "Working" })).toHaveCount(0, { timeout });
}

const file = (name: string, text: string, mimeType = "text/plain") => ({ name, mimeType, buffer: Buffer.from(text) });

test("home shows the composer and starters, and a starter fills the message", async ({ page }) => {
  await openHome(page);
  const box = page.getByLabel("Message the copilot");
  await expect(box).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
  for (const title of ["Onboard a source", "Pick up a Jira issue", "Change a model", "Add quality checks", "Work from a file", "Ask the domain"]) {
    await expect(page.getByRole("button", { name: new RegExp(title) })).toBeVisible();
  }
  await page.getByRole("button", { name: /Onboard a source/ }).click();
  await expect(box).toHaveValue(/Onboard a new source through SnapLogic/);
  await expect(page.getByRole("button", { name: "Send" })).toBeEnabled();
});

test("the site-wide copilot launcher stays out of the workspace", async ({ page }) => {
  await openHome(page);
  await expect(page.locator("[data-copilot-launcher]")).toHaveCount(0);
  await page.goto("/dashboard");
  await expect(page.locator("[data-copilot-launcher]")).toHaveCount(1);
});

test("attaching a file starts a conversation, reads the file and can remove it", async ({ page }) => {
  await openHome(page);
  await page.locator('input[type="file"]').setInputFiles(file("requirements.csv", "table,column,rule\nNATION,N_NAME,not null\n"));
  await page.waitForURL(/\/dashboard\/copilot\/[0-9a-f-]{36}$/);
  const chip = page.getByRole("button", { name: "Remove requirements.csv" });
  await expect(chip).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeEnabled();   // a file alone can be sent
  await chip.click();
  await expect(chip).toHaveCount(0);
  await expect(page.getByRole("link", { name: /requirements\.csv|Files/ }).first()).toBeVisible();   // listed in Conversations
});

test("a PDF is attached by name with a note, and an oversized file is refused before upload", async ({ page }) => {
  await openHome(page);
  await page.locator('input[type="file"]').setInputFiles(file("spec.pdf", "%PDF-1.7 binary", "application/pdf"));
  const pdf = page.getByText("spec.pdf");
  await expect(pdf).toBeVisible();
  await expect(page.locator('[title*="PDF text cannot be read"]')).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(
    { name: "huge.csv", mimeType: "text/csv", buffer: Buffer.alloc(10 * 1024 * 1024 + 10, 97) });
  await expect(page.getByRole("alert").filter({ hasText: "huge.csv: larger than 10 MB" })).toBeVisible();
});

test("the Jira picker opens, toggles closed and closes on Escape", async ({ page }) => {
  await openHome(page);
  const toggle = page.getByRole("button", { name: "Add a Jira issue" });
  await toggle.click();
  const picker = page.getByRole("dialog", { name: "Add a Jira issue" });
  await expect(picker).toBeVisible();
  // whatever the Jira state is, the picker says it: not set up, connect, or the issues
  await expect(picker.getByText(/not set up|Connect Jira|Search, or type a key|could not be reached|Checking/)).toBeVisible();
  await toggle.click();
  await expect(picker).toHaveCount(0);
  await toggle.click();
  await expect(picker).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(picker).toHaveCount(0);
});

test("a conversation is kept: reopening it shows its files and plan", async ({ page }) => {
  await openHome(page);
  await page.locator('input[type="file"]').setInputFiles(file("mapping.json", JSON.stringify({ N_NAME: "UPPER(n_name)" })));
  await page.waitForURL(/\/dashboard\/copilot\/[0-9a-f-]{36}$/);
  const url = page.url();
  await expect(page.getByRole("button", { name: "Remove mapping.json" })).toBeVisible();
  await page.goto(url);
  // not sent yet: the file is still waiting in the message box
  await expect(page.getByRole("button", { name: "Remove mapping.json" })).toBeVisible();
  await page.goto("/dashboard/copilot");
  await expect(page.getByRole("navigation", { name: "Conversations" }).getByRole("link").first()).toBeVisible();
});

test("a conversation can be deleted from the list, after confirming, and stays gone", async ({ page }) => {
  await openHome(page);
  await page.locator('input[type="file"]').setInputFiles(file("delete-me.txt", "scratch"));
  await page.waitForURL(/\/dashboard\/copilot\/[0-9a-f-]{36}$/);
  const url = page.url();
  const id = url.split("/").pop()!;
  const list = page.getByRole("navigation", { name: "Conversations" });
  const row = list.locator(`a[href="/dashboard/copilot/${id}"]`);
  const group = list.locator("div.group").filter({ has: page.locator(`a[href="/dashboard/copilot/${id}"]`) });
  await expect(row).toBeVisible();

  // asking first: Cancel keeps it
  await row.hover();
  await group.getByRole("button", { name: /^Delete conversation / }).click();
  await expect(list.getByText(/Delete this conversation\?/)).toBeVisible();
  await list.getByRole("button", { name: "Cancel" }).click();
  await expect(row).toBeVisible();

  // deleting the open conversation leaves it for a fresh page
  await row.hover();
  await group.getByRole("button", { name: /^Delete conversation / }).click();
  await list.getByRole("button", { name: "Delete", exact: true }).click();
  await page.waitForURL(/\/dashboard\/copilot$/);
  await expect(page.getByRole("navigation", { name: "Conversations" }).locator(`a[href="/dashboard/copilot/${id}"]`)).toHaveCount(0);
  await page.goto(url);   // the shell streams first, so the status is 200; the page itself says it is gone
  await expect(page.getByText("This page could not be found")).toBeVisible();
});

test.describe("live AI", () => {
  test.skip(!LIVE_AI, "set AIP_E2E_LIVE_AI=1 to run the conversations that call Cortex");

  test("a question is answered from the platform, with its work folded into one line", async ({ page }) => {
    test.setTimeout(300_000);
    await openHome(page);
    await page.getByLabel("Message the copilot").fill("Which business domains are on the platform? Answer briefly.");
    await page.getByRole("button", { name: "Send" }).click();
    await page.waitForURL(/\/dashboard\/copilot\/[0-9a-f-]{36}$/);
    const thread = page.getByRole("log", { name: "Conversation" });
    await expect(thread.getByText(/Worked through \d+ steps?/)).toBeVisible({ timeout: 240_000 });
    await expect(thread.getByText(/COMPANY|domain/i).last()).toBeVisible();
    await thread.getByText(/Worked through/).click();
    await expect(thread.getByText(/Listed domains|Searched domain knowledge|Asked domain knowledge/).first()).toBeVisible();
  });

  test("a drafted data quality check shows in review, and nothing is published", async ({ page }) => {
    test.setTimeout(420_000);
    await openHome(page);
    await page.getByLabel("Message the copilot").fill(
      `Draft one data quality check on ${DQ_TABLE}: the first key column has no missing values. Do not ask me anything; draft it.`);
    await page.getByRole("button", { name: "Send" }).click();
    const thread = page.getByRole("log", { name: "Conversation" });
    await expect(thread.getByText("Data quality", { exact: true }).first()).toBeVisible({ timeout: 360_000 });
    await expect(page.getByRole("tabpanel").getByText(/Data quality checks|Snowflake dry run/).first()).toBeVisible();

    await page.getByRole("button", { name: /^Review \d+$/ }).click();
    const drawer = page.getByRole("dialog").filter({ hasText: "Review changes" });
    await expect(drawer.getByText(/checks for /)).toBeVisible();
    await drawer.getByRole("button", { name: /^Publish \d+$/ }).click();
    await expect(drawer.getByText(/Publishing runs \d+ API call/)).toBeVisible();   // the confirmation step
    await drawer.getByRole("button", { name: "Back" }).click();
    await page.keyboard.press("Escape");                                             // closes the drawer
    await expect(drawer).toHaveCount(0);
    await expect(thread.getByText("Drafted").first()).toBeVisible();                // still a draft: nothing published
    await turnEnds(page);
  });

  test("a question form is answered in place and the conversation carries on", async ({ page }) => {
    test.setTimeout(480_000);
    await openHome(page);
    await page.getByLabel("Message the copilot").fill(
      "Onboard a new source through SnapLogic. Ask me for the source details in a short form before drafting anything.");
    await page.getByRole("button", { name: "Send" }).click();
    const form = page.getByRole("form", { name: "Question from the copilot" }).last();
    await expect(form).toBeVisible({ timeout: 300_000 });
    for (const group of await form.getByRole("radiogroup").all()) await group.getByRole("radio").first().click();
    for (const box of await form.locator("input[type=text], input:not([type]), textarea").all()) {
      if (!(await box.inputValue())) await box.fill("E2E_TEST");
    }
    for (const box of await form.locator("input[type=number]").all()) {
      if (!(await box.inputValue())) await box.fill("1");
    }
    const send = form.getByRole("button", { name: "Send answers" });
    await expect(send).toBeEnabled();
    await send.click();
    await expect(send).toHaveCount(0);                                                 // answered: the form is done
    await expect(page.getByRole("button", { name: "Working" })).toBeVisible();        // the answers started a turn
    const thread = page.getByRole("log", { name: "Conversation" });
    await expect(thread.getByText(/E2E_TEST/).first()).toBeVisible();                  // the answers are in the thread
    await turnEnds(page);
    await expect(thread.getByRole("alert")).toHaveCount(0);                            // and it ended without an error
  });

  test("a domain model change is drawn on the ER diagram in review", async ({ page }) => {
    test.setTimeout(480_000);
    await openHome(page);
    await page.getByLabel("Message the copilot").fill(
      "List the domain models. For the first one with an approved version, draft adding a nullable VARCHAR column " +
      "E2E_TIER to its first entity, saved without submitting. Do not ask me anything; draft it.");
    await page.getByRole("button", { name: "Send" }).click();
    const thread = page.getByRole("log", { name: "Conversation" });
    await expect(thread.getByText("Data model", { exact: true }).first()).toBeVisible({ timeout: 360_000 });
    await page.getByRole("button", { name: /^Review \d+$/ }).click();
    const drawer = page.getByRole("dialog").filter({ hasText: "Review changes" });
    const diagram = drawer.getByTestId("copilot-model-diagram");
    await expect(diagram).toBeVisible();
    await expect(diagram.locator('[data-mark="added"]').first()).toBeAttached();        // the new column is highlighted
    await expect(drawer.getByText(/E2E_TIER/).first()).toBeVisible();
    await drawer.getByText(/What publishing does/).click();
    await expect(drawer.getByText(/\/api\/hub\/domain-models\/[^/]+\/draft/).first()).toBeVisible();
    await expect(drawer.getByText(/\/approve/)).toHaveCount(0);                        // the copilot never approves
    await page.keyboard.press("Escape");
    await turnEnds(page);
  });
});
