import { expect, test } from "@playwright/test";

// Read-only walk of the Agentic Data OS experience (/os) over live data, and a check that the classic shell is
// untouched. Dev sign-in only (AIP_AUTH=dev): the aip_dev cookie stands in for the login.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

const SCREENS: [string, RegExp][] = [
  ["/os", /Good (morning|afternoon|evening)/],
  ["/os/intake", /Create an onboarding request/],
  ["/os/requests", /Onboarding requests/],
  ["/os/enable", /Source readiness/],
  ["/os/design", /Profile, model and map/],
  ["/os/build", /build|Build workbench/i],
  ["/os/review", /Validation gate/],
  ["/os/skills", /Improve skill/],
  ["/os/release", /Code, test and release/],
  ["/os/knowledge", /Knowledge hub/],
  ["/os/run", /Production operations/],
  ["/os/help", /One workflow, many personas/],
];

test.beforeEach(async ({ context }) => {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
});

test("every /os screen renders in its own shell, with no classic chrome", async ({ page }) => {
  test.setTimeout(300_000);
  for (const [path, heading] of SCREENS) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(heading);
    const rail = page.getByRole("navigation", { name: "Agentic Data OS" });
    await expect(rail).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
    await expect(page.getByText("Application error")).toHaveCount(0);
  }
});

test("the rail marks the current screen and the persona reorders the queue", async ({ page }) => {
  await page.goto("/os/knowledge");
  await expect(page.getByRole("navigation", { name: "Agentic Data OS" }).getByRole("link", { name: "Knowledge" })).toHaveAttribute("aria-current", "page");
  await page.goto("/os");
  await page.getByLabel("View as persona").first().selectOption("designer");
  await expect(page.getByText("Mission control / Data Designer")).toBeVisible();
  await page.getByLabel("View as persona").first().selectOption("forward_lead");
  await expect(page.getByText("Mission control / Forward Engineering Lead")).toBeVisible();
});

// Governance: the dev user is a super admin, so it can preview what each role is shown (a cookie the UI honours only
// for super and governance admins). These read only; a preview never changes privileges in the API.
async function previewAs(page: import("@playwright/test").Page, role: string) {
  // a super admin previews a persona (Admin, People); the cookie is honoured for super admins only
  await page.context().addCookies([{ name: "ados_preview_persona", value: role, url: BASE }]);
}
const railLinks = (page: import("@playwright/test").Page) =>
  page.getByRole("navigation", { name: "Agentic Data OS" }).first().getByRole("link");

test("a viewer sees only the screens their role opens, and is turned back from the rest", async ({ page }) => {
  await previewAs(page, "viewer");
  await page.goto("/os");
  await expect(page.getByRole("status").filter({ hasText: "Previewing as viewer" })).toBeVisible();
  // the package's ten entries, filtered: a viewer reads requests, domains, the audit trail, knowledge and operations
  // the package's entries a viewer may open; Admin, governance and the audit trail are a super admin's alone
  await expect(railLinks(page)).toHaveText(["My Work", "Intake", "Design", "Knowledge", "Run & DQ", "Help"]);
  await expect(page.getByLabel("Persona")).toHaveText("Viewer");
  await page.goto("/os/build");
  await page.waitForURL(/\/os\?denied=build$/);
  await expect(page.getByText("Build: Workbench is not part of your role.")).toBeVisible();
  // the classic pages are gated by the same list
  await page.goto("/runs");
  await page.waitForURL(/\/os\?denied=pipelines$/);
  await page.goto("/admin");
  await page.waitForURL(/\/os\?denied=admin$/);
});

test("a code reviewer gets the build, review and release screens and the developer persona", async ({ page }) => {
  await previewAs(page, "developer");
  await page.goto("/os");
  await expect(railLinks(page)).toHaveText(["My Work", "Intake", "Design", "Build", "Review", "Release", "Knowledge", "Run & DQ", "Help"]);
  await expect(page.getByLabel("Persona")).toHaveText("Data Developer");
});

test("without the privilege, an action becomes a change request to the approver role", async ({ page }) => {
  // a data engineer builds but does not approve code: "Review code" is never offered as their own action
  await previewAs(page, "forward_lead");
  await page.goto("/os/build");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "Review code", exact: true })).toHaveCount(0);
  const request = page.getByRole("link", { name: /^Request: / });
  for (const link of await request.all()) await expect(link).toHaveAttribute("title", /You do not hold [A-Z.]+\. Your change goes to/);
  await page.goto("/os/help");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("One workflow, many personas");
  await expect(page.getByText("Access list")).toBeVisible();
});

// In-place decisions (Phase 9). These never submit: approving a gate, signing off QA, publishing a PR, promoting a
// skill or verifying knowledge are real writes on the platform. They check the controls appear for the right people,
// stay locked until a reason is written, and are off while an admin previews another role.
test("a gate is decided in place on Review, with a written justification", async ({ page }) => {
  await page.goto("/os/review");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  if (!(await page.getByTestId("gate-decision").count())) {
    // the default run is not at a gate: open the first one waiting
    const waiting = page.locator('a[href^="/os/review?run="]').first();
    test.skip(!(await waiting.count()), "no run is waiting at a gate");
    await waiting.click();
  }
  const decision = page.getByTestId("gate-decision");
  await expect(decision).toBeVisible();
  await expect(page.getByTestId("gate-approve")).toBeDisabled();
  await decision.getByRole("textbox").fill("Checked against the domain contract");
  await expect(page.getByTestId("gate-approve")).toBeEnabled();
  await expect(decision.getByRole("combobox", { name: "Send back to" }).getByRole("option", { name: /cancel/i })).toHaveCount(0);
});

test("previewing another role turns the decisions off", async ({ page }) => {
  await previewAs(page, "forward_lead");
  await page.goto("/os/review");
  const decision = page.getByTestId("gate-decision");
  test.skip(!(await decision.count()), "no gate this role may act on is open");
  await expect(decision.getByText("Decisions are off while you preview another role")).toBeVisible();
  await expect(page.getByTestId("gate-approve")).toBeDisabled();
});

test("knowledge stewards verify records and decide the inbox in place; viewers do not", async ({ page }) => {
  await page.goto("/os/knowledge");
  await expect(page.getByTestId("verify-knowledge")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Steward inbox" })).toBeVisible();
  await previewAs(page, "viewer");
  await page.goto("/os/knowledge");
  await expect(page.getByTestId("verify-knowledge")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Steward inbox" })).toHaveCount(0);
});

test("skill versions can be made candidate or promoted in place", async ({ page }) => {
  await page.goto("/os/skills?skill=SODA_SKILL");
  await expect(page.getByRole("button", { name: /^(Promote|Make candidate)$/ }).first()).toBeVisible();
});

test("the rail is the package's ten entries and every page is a tab of one", async ({ page }) => {
  await page.goto("/os");
  await expect(railLinks(page)).toHaveText(["My Work", "Intake", "Enable", "Design", "Build", "Review", "Skills", "Release", "Knowledge", "Run & DQ", "Admin", "Help"]);
  // classic pages open inside the shell, under their entry, as one of its tabs
  await page.goto("/runs");
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  await expect(railLinks(page).filter({ hasText: "Build" })).toHaveAttribute("aria-current", "page");
  const tabs = page.getByTestId("section-tabs");
  await expect(tabs.getByRole("link")).toHaveText(["Workbench", "Silver pipeline", "Gold pipeline", "Lineage", "Code"]);
  await expect(tabs.getByRole("link", { name: "Silver pipeline" })).toHaveAttribute("aria-current", "page");
  await page.goto("/dashboard/sttm");
  await expect(railLinks(page).filter({ hasText: "Design" })).toHaveAttribute("aria-current", "page");
  await expect(tabs.getByRole("link", { name: "Stores" })).toHaveAttribute("aria-current", "page");
  await page.goto("/incidents");
  await expect(railLinks(page).filter({ hasText: "Run & DQ" })).toHaveAttribute("aria-current", "page");
  await page.goto("/os/requests");
  await expect(railLinks(page).filter({ hasText: "Intake" })).toHaveAttribute("aria-current", "page");
  await expect(tabs.getByRole("link")).toHaveText(["New request", "Requests", "Onboarding wizard"]);
});

test("admins can switch the classic pages back to the classic layout", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByTestId("classic-layout-switch")).toHaveAttribute("aria-checked", "false");
  await page.getByTestId("classic-layout-switch").click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Agentic Data OS", exact: true })).toHaveAttribute("href", "/os");
  // /os keeps its own shell either way
  await page.goto("/os");
  await expect(page.getByRole("navigation", { name: "Agentic Data OS" })).toBeVisible();
  await page.goto("/admin");
  await page.getByTestId("classic-layout-switch").click();
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
});


// Phase 11: Admin and governance belong to super admins; people get personas, not role toggles.
test("only a super admin reaches Admin, people and the audit trail", async ({ page }) => {
  test.setTimeout(420_000);
  for (const persona of ["admin", "forward_lead", "developer", "viewer"]) {
    await page.context().clearCookies();
    await page.context().addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
    await previewAs(page, persona);
    await page.goto("/os");
    await expect(railLinks(page).filter({ hasText: "Admin" })).toHaveCount(0);
    for (const [path, key] of [["/os/people", "people"], ["/admin", "admin"], ["/audit", "audit"]]) {
      // the guard turns the visit around before it finishes, which Playwright reports as an aborted navigation
      await page.goto(path).catch(() => undefined);
      await page.waitForURL(new RegExp(`/os\\?denied=${key}$`));
    }
  }
});

test("a super admin gives personas in People and the header has no role picker", async ({ page }) => {
  await page.goto("/os/people");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("People and personas");
  await expect(page.getByTestId("bundle-designer")).toBeVisible();
  await expect(page.getByTestId("preview-forward_lead")).toBeVisible();
  await expect(page.getByLabel("Preview as role")).toHaveCount(0);
  // previewing from People shows the banner, and Stop preview returns to People
  await page.getByTestId("preview-developer").click();
  await expect(page.getByRole("status").filter({ hasText: "Previewing as Data Developer" })).toBeVisible();
  await page.getByRole("button", { name: "Stop preview" }).click();
  await page.waitForURL(/\/os\/people$/);
});

// Phase 12: the administrator onboards sources as configuration, owners attach what exists, the gate has a minimum score.
test("Enable is configuration first: requests on top, the wizard's ways in, no table catalog", async ({ page }) => {
  await page.goto("/os/enable");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Source readiness");
  await expect(page.getByText("Requests waiting on a source")).toBeVisible();
  const ways = page.getByTestId("onboard-ways").getByRole("link");
  await expect(ways).toHaveCount(3);
  await expect(ways.nth(0)).toHaveAttribute("href", /\/dashboard\/onboarding\?type=source&branch=share$/);
  await expect(ways.nth(1)).toHaveAttribute("href", /branch=external&kind=database$/);
  await expect(ways.nth(2)).toHaveAttribute("href", /branch=external&kind=file$/);
  // configuration cards, never a table catalog or a secret value
  await expect(page.getByTestId("source-config").first()).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Staged" })).toHaveCount(0);
  await expect(page.getByText(/password\s*:/i)).toHaveCount(0);
  // a source a request asks for opens the wizard for that request
  const ask = page.getByTestId("enable-asks").getByRole("link", { name: /^Onboard as/ }).first();
  if (await ask.count()) await expect(ask).toHaveAttribute("href", /request=[0-9a-f-]+&item=[0-9a-f-]+&name=/);
});

test("the wizard opened for a request says so and starts from the request's source name", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=external&kind=database&request=00000000-0000-0000-0000-000000000000&item=x&name=Yardi%20Voyager");
  await expect(page.getByRole("status").filter({ hasText: "could not be read" })).toBeVisible();
  await expect(page.locator("#source_name")).toHaveValue("YARDI_VOYAGER");
});

test("Intake offers the sources already feeding the domain, the other registered ones and a new one", async ({ page }) => {
  await page.goto("/os/intake?type=source");
  await expect(page.getByTestId("sources-feeding")).toHaveCount(0);
  await expect(page.getByTestId("sources-registered")).toBeVisible();
  const domain = page.locator("#domain");
  const first = await domain.locator("option").nth(1).getAttribute("value");
  test.skip(!first, "no domain is onboarded");
  await page.goto(`/os/intake?type=source&domain=${first}`);
  await expect(page.getByTestId("sources-feeding")).toBeVisible();
  await expect(page.getByTestId("sources-registered")).toBeVisible();
  await expect(page.getByText("Request a new source", { exact: true })).toBeVisible();
});

test("the validation gate shows the minimum from the rules and validates again in place", async ({ page }) => {
  await page.goto("/os/review");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const required = page.getByText(/^required \d+(\.\d+)?%/);
  test.skip(!(await required.count()), "no run to show");
  await expect(required).toBeVisible();
  // never submitted here: validating is a real write on the platform
  const again = page.getByTestId("validate-again-button");
  if (await again.count()) await expect(again).toHaveText(/Validate again|Request validation/);
});
