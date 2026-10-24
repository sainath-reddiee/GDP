import { expect, test } from "@playwright/test";

// Read-only walk of the Agentic Data OS experience (/os) over live data, and a check that the classic shell is
// untouched. Dev sign-in only (AIP_AUTH=dev): the aip_dev cookie stands in for the login.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

const SCREENS: [string, RegExp][] = [
  ["/os", /Platform actions/],
  ["/os/intake", /Requests/],
  ["/os/intake/new", /Create an onboarding request/],
  ["/os/requests", /Onboarding requests/],
  ["/os/enable", /Source readiness/],
  ["/os/design", /Profile, model and map/],
  ["/os/build", /Generate, validate and hand off/],
  ["/os/review", /Validation gate/],
  ["/os/skills", /Improve skill/],
  ["/os/release", /Code, test and release/],
  ["/os/knowledge", /Knowledge hub/],
  ["/os/run", /Production operations/],
  ["/os/help", /One workflow, many personas/],
  ["/os/history", /Run history/],
  ["/os/build/ready", /Ready to build/],
  ["/os/requests/history", /Request history/],
  ["/os/enable/new-domain", /Onboard domain/],
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

test("the rail marks the current screen", async ({ page }) => {
  await page.goto("/os/knowledge");
  await expect(page.getByRole("navigation", { name: "Agentic Data OS" }).getByRole("link", { name: "Knowledge" })).toHaveAttribute("aria-current", "page");
  await page.goto("/os");
  await expect(page.getByRole("navigation", { name: "Agentic Data OS" }).getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
});

// Intake: a workflow opened from Home lands on Intake, selected; the in-flight counts filter New and Enhance; the
// source form asks for the request first, then the sources grouped by domain, and shows where the request goes.
test("Home opens a workflow on Intake, and Intake filters New and Enhance", async ({ page }) => {
  await page.goto("/os?view=all");
  const open = page.getByTestId("workflow-row").first().getByRole("link", { name: /^Open / });
  test.skip(!(await open.count()), "no workflow on record");
  await expect(open).toHaveAttribute("href", /^\/os\/intake\?view=selected&request=/);
  await page.goto("/os/intake?view=active");
  await page.getByTestId("inflight-enhance").click();
  await page.waitForURL(/intent=enhance/);
  for (const row of await page.getByTestId("workflow-row").all()) await expect(row).toContainText(/Enhance /);
});

test("the source form puts the request first, groups sources by domain and shows the route", async ({ page }) => {
  await page.goto("/os/intake/new?type=source&intent=new");
  const details = page.getByRole("heading", { name: "Request details" });
  const sources = page.getByRole("heading", { name: "Sources", exact: true });
  await expect(details).toBeVisible();
  expect((await details.boundingBox())!.y).toBeLessThan((await sources.boundingBox())!.y);
  await expect(page.getByTestId("flow-preview")).toContainText("New source flow");
  await page.goto("/os/intake/new?type=source&intent=enhance");
  await expect(page.getByTestId("flow-preview")).toContainText("Enhance flow");
  await expect(page.getByTestId("flow-preview")).toContainText("Impact & delta mapping");
  await expect(page.getByTestId("sources-registered")).toHaveCount(0);   // Enhance widens a source the domain already has
});

// The stage boards: Selected, Action required, Active and Completed; a workflow opened from Home lands selected.
test("Intake, Design and Build are boards, and a workflow opens selected", async ({ page }) => {
  for (const [path, testId] of [["/os/intake", "intake-board"], ["/os/design", "design-board"], ["/os/build", "build-board"]] as const) {
    await page.goto(path);
    await expect(page.getByTestId(testId)).toBeVisible();
    const tabs = page.getByTestId("board-tabs");
    for (const t of ["Action required", "Active", "Completed"]) await expect(tabs.getByRole("link", { name: new RegExp(t) })).toBeVisible();
  }
  // a run card on Design opens it selected, on Profile, Model or Map
  await page.goto("/os/design?view=active");
  const card = page.getByTestId("run-card").first();
  test.skip(!(await card.isVisible()), "no run is in design");
  await card.getByRole("link", { name: /^Open / }).click();
  await page.waitForURL(/\/os\/design\?view=selected&run=/);
  await expect(page.getByTestId("design-selected")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Design step" })).toContainText("Profile");
});

// Home: no hero, no workspace pill; Copilot in the header; workflows filtered in the URL; New or Enhance per kind.
test("Home lists the build workflows by view, type, intent and name, and starts new or enhanced ones", async ({ page }) => {
  await page.goto("/os");
  await expect(page.getByText(/Good (morning|afternoon|evening)/)).toHaveCount(0);
  await expect(page.getByText(/WORKSPACE$/)).toHaveCount(0);
  // a clean header: no domain filter, no search; Copilot opens its page, Ctrl J the quick drawer
  await expect(page.getByTestId("domain-switcher")).toHaveCount(0);
  await expect(page.getByText(/Search runs, domains/)).toHaveCount(0);
  await expect(page.getByTestId("onboarded-domains")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Section" })).toHaveCount(0);   // no Inbox or Overview tabs
  const copilot = page.locator("header [data-copilot-launcher]");
  await expect(copilot).toHaveAttribute("href", "/os/work/copilot");
  await expect(async () => {
    await page.keyboard.press("Control+j");
    await expect(page.getByRole("dialog", { name: "Copilot" })).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 20_000 });
  await page.keyboard.press("Escape");
  // views and filters are links that keep each other
  const views = page.getByTestId("home-views");
  for (const v of ["Active", "Action required", "All"]) await expect(views.getByRole("link", { name: new RegExp(v) })).toBeVisible();
  await views.getByRole("link", { name: /^All/ }).click();
  await page.waitForURL(/view=all/);
  await page.getByTestId("home-types").getByRole("link", { name: "Source" }).click();
  await page.waitForURL(/view=all&type=source/);
  for (const row of await page.getByTestId("workflow-row").all()) await expect(row).toContainText(/(New|Enhance) source/);
  await page.getByTestId("home-search").fill("zz-no-such-workflow");
  await page.waitForURL(/q=zz-no-such-workflow/);
  await expect(page.getByText('No workflow matches "zz-no-such-workflow"')).toBeVisible();
  // Start a workflow: New and Enhance for a domain and a source open Intake; data products are coming soon
  await page.goto("/os");
  await expect(page.getByTestId("start-product-new")).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByTestId("start-product-enhance")).toHaveAttribute("aria-disabled", "true");
  await page.getByTestId("start-domain-enhance").click();
  await page.waitForURL(/\/os\/intake\/new\?type=domain&intent=enhance$/);
  await expect(page.getByTestId("domain-enhance")).toBeVisible();
  await expect(page.locator("#domain")).toBeVisible();
  await expect(page.locator("#rq-domain")).toHaveCount(0);
  await page.goto("/os");
  await page.getByTestId("start-source-new").click();
  await page.waitForURL(/\/os\/intake\/new\?type=source&intent=new$/);
  await expect(page.getByTestId("intake-intent").getByRole("tab", { name: "New" })).toHaveAttribute("aria-selected", "true");
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
  // Build appears for its read-only Run history tab
  await expect(railLinks(page)).toHaveText(["Home", "Intake", "Design", "Build", "Knowledge", "Run & DQ", "Help"]);
  await expect(railLinks(page).filter({ hasText: "Build" })).toHaveAttribute("href", "/os/history");
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
  await expect(railLinks(page)).toHaveText(["Home", "Intake", "Design", "Build", "Review", "Release", "Knowledge", "Run & DQ", "Help"]);
  await expect(page.getByLabel("Persona")).toHaveText("Data Developer");
});

test("without the privilege, an action becomes a change request to the approver role", async ({ page }) => {
  // a data engineer builds but does not approve code: "Review code" is never offered as their own action
  await previewAs(page, "forward_lead");
  await page.goto("/os/build");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "Review code", exact: true })).toHaveCount(0);
  const request = page.getByRole("link", { name: /^Request approval: / });
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
  await expect(railLinks(page)).toHaveText(["Home", "Intake", "Enable", "Design", "Build", "Review", "Skills", "Release", "Knowledge", "Run & DQ", "Admin", "Help"]);
  // classic pages open inside the shell, under their entry, as one of its tabs
  await page.goto("/runs");
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  await expect(railLinks(page).filter({ hasText: "Build" })).toHaveAttribute("aria-current", "page");
  const tabs = page.getByTestId("section-tabs");
  // the old Silver and Gold lists live on under Run history
  await expect(tabs.getByRole("link")).toHaveText(["Ready to build", "Workbench", "Lineage", "Code", "Run history"]);
  await expect(tabs.getByRole("link", { name: "Run history" })).toHaveAttribute("aria-current", "page");
  await page.goto("/dashboard/sttm");
  await expect(railLinks(page).filter({ hasText: "Design" })).toHaveAttribute("aria-current", "page");
  await expect(tabs.getByRole("link", { name: "Stores" })).toHaveAttribute("aria-current", "page");
  await page.goto("/incidents");
  await expect(railLinks(page).filter({ hasText: "Run & DQ" })).toHaveAttribute("aria-current", "page");
  await page.goto("/os/requests");
  await expect(railLinks(page).filter({ hasText: "Intake" })).toHaveAttribute("aria-current", "page");
  // Intake is one board (its form and the older lists sit under it), so it has no tab bar
  await expect(tabs).toHaveCount(0);
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
test("Enable is configuration first: requests on top, one way in per object, no table catalog", async ({ page }) => {
  await page.goto("/os/enable");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Source readiness");
  await expect(page.getByText("Requests waiting on a source")).toBeVisible();
  // onboarding a source or a domain is reached once, from Enable's tabs; no header copies or duplicate kind tiles
  await expect(page.getByRole("link", { name: "Open Source Store" })).toHaveCount(1);
  await expect(page.getByRole("link", { name: "Onboard source", exact: true })).toHaveCount(1);
  await expect(page.getByRole("link", { name: "Onboard domain", exact: true })).toHaveCount(1);
  await expect(page.getByTestId("onboard-ways")).toHaveCount(0);
  // configuration cards, never a table catalog or a secret value
  await expect(page.getByTestId("source-config").first()).toBeVisible();
  await expect(page.locator("th", { hasText: "Staged" })).toHaveCount(0);
  await expect(page.getByText(/password\s*:/i)).toHaveCount(0);
  // a source a request asks for opens the wizard for that request
  const ask = page.getByTestId("enable-asks").getByRole("link", { name: "Onboard source for request" }).first();
  if (await ask.count()) await expect(ask).toHaveAttribute("href", /type=source&request=[0-9a-f-]+&item=[0-9a-f-]+&name=/);
});

test("the wizard opened for a request says so and starts from the request's source name", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source&branch=external&kind=database&request=00000000-0000-0000-0000-000000000000&item=x&name=Yardi%20Voyager");
  await expect(page.getByRole("status").filter({ hasText: "could not be read" })).toBeVisible();
  await expect(page.locator("#source_name")).toHaveValue("YARDI_VOYAGER");
});

test("Intake offers the sources already feeding the domain, the other registered ones and a new one", async ({ page }) => {
  await page.goto("/os/intake/new?type=source");
  await expect(page.getByTestId("sources-feeding")).toHaveCount(0);
  await expect(page.getByTestId("sources-registered")).toBeVisible();
  const domain = page.locator("#domain");
  const first = await domain.locator("option").nth(1).getAttribute("value");
  test.skip(!first, "no domain is onboarded");
  await page.goto(`/os/intake/new?type=source&domain=${first}`);
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

// Phase 13: history, the code standards agent and the demo paths.
test("Run history lists every run with its validation, and viewers read it without the workbench", async ({ page }) => {
  await page.goto("/os/history");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Run history");
  const table = page.getByTestId("run-history");
  test.skip(!(await table.count()), "no run on record");
  await expect(table.locator("th", { hasText: "Validation" })).toBeVisible();
  await expect(table.locator('a[href^="/os/build?run="]').first()).toBeVisible();
  await page.goto("/os/history?status=completed&pipeline=SILVER");
  await expect(page.locator("#h-status")).toHaveValue("completed");
  await previewAs(page, "viewer");
  await page.goto("/os/history");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Run history");
  await expect(page.locator('a[href^="/os/build?run="]')).toHaveCount(0);
});

test("Request history shows finished requests and opens each one's timeline", async ({ page }) => {
  await page.goto("/os/requests/history");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Request history");
  const rows = page.getByTestId("request-history").locator('a[href^="/os/requests/"]');
  if (await rows.count()) await expect(rows.first()).toHaveAttribute("href", /\/os\/requests\/[0-9a-f-]+$/);
});

test("the code standards agent can be run alone from Review, and never in a preview", async ({ page }) => {
  await page.goto("/os/review");
  const button = page.getByTestId("review-standards-button");
  test.skip(!(await button.count()), "no generated run to review");
  // not clicked: it calls Cortex on the platform
  await expect(button).toHaveText(/Review standards|Request a standards review/);
  await previewAs(page, "forward_lead");
  await page.goto("/os/review");
  if (await page.getByTestId("review-standards-button").count()) await expect(page.getByTestId("review-standards-button")).toBeDisabled();
});

test("Help walks the two demos on live records", async ({ page }) => {
  await page.goto("/os/help");
  const demos = page.getByTestId("demo-paths");
  await expect(demos).toBeVisible();
  await expect(demos.getByText("1 · Forward Engineering Lead, happy path")).toBeVisible();
  await expect(demos.getByText("2 · Onboarding with people in the loop")).toBeVisible();
  await expect(demos.getByText("File the request")).toBeVisible();
});

// Onboarding a source is the Data OS Administrator's alone, in Enable; domains are onboarded from Design.
test("only the administrator onboards sources, from Enable; others request them", async ({ page }) => {
  await page.goto("/dashboard/onboarding?type=source");
  await expect(page.getByRole("heading", { name: "Onboard source" })).toBeVisible();
  const tabs = page.getByTestId("section-tabs");
  await expect(tabs.getByRole("link")).toHaveText(["Source readiness", "Onboard source", "Onboard domain", "Integrations"]);
  await expect(railLinks(page).filter({ hasText: "Enable" })).toHaveAttribute("aria-current", "page");
  await page.goto("/os/intake/new?type=source");
  await expect(page.getByRole("link", { name: "Open the onboarding wizard" })).toHaveCount(0);
  await expect(page.getByText("Sources are onboarded by the Data OS Administrator")).toBeVisible();
  for (const persona of ["designer", "product_owner", "forward_lead"]) {
    await previewAs(page, persona);
    await page.goto("/os");
    await expect(railLinks(page).filter({ hasText: "Enable" })).toHaveCount(0);
    await page.goto("/dashboard/onboarding?type=source").catch(() => undefined);
    await page.waitForURL(/\/os\?denied=onboarding$/);
  }
  await previewAs(page, "admin");
  await page.goto("/os");
  await expect(railLinks(page).filter({ hasText: "Enable" })).toBeVisible();
});

// Skills: the Forward Engineering Lead (a skill owner) promotes directly; a designer's promotion goes to a skill owner;
// a developer has no Skill studio. A preview never submits: the buttons are shown and disabled.
test("skill promotion follows the persona: the lead promotes, a designer requests, a developer has none", async ({ page }) => {
  await previewAs(page, "forward_lead");
  await page.goto("/os/skills?skill=SODA_SKILL");
  const promote = page.getByRole("button", { name: /^(Promote|Make candidate)$/ });
  test.skip(!(await promote.count()), "no SODA_SKILL version to move");
  await expect(promote.first()).toBeDisabled();
  await expect(page.getByRole("button", { name: /^Request promotion$/ })).toHaveCount(0);
  await previewAs(page, "designer");
  await page.goto("/os/skills?skill=SODA_SKILL").catch(() => undefined);
  // a designer edits skills (SKILL.EDIT) but does not release them: promotion becomes a request to the skill owner
  if (page.url().includes("/os/skills")) {
    await expect(page.getByRole("button", { name: /^Promote$/ })).toHaveCount(0);
  }
  await previewAs(page, "developer");
  await page.goto("/os/skills").catch(() => undefined);
  await page.waitForURL(/\/os\?denied=skills$/);
});

// A new domain is a request to the administrator, who onboards it in Enable; data products are coming soon.
test("a new domain is requested by name and onboarded by the administrator; data products are coming soon", async ({ page }) => {
  await page.goto("/os/intake/new?type=domain");
  const form = page.getByTestId("domain-request");
  await expect(form).toBeVisible();
  // no list of the domains that exist: a new domain is named
  await expect(page.locator("#domain")).toHaveCount(0);
  await expect(page.locator("#rq-domain")).toBeVisible();
  await expect(page.getByText("Domains are onboarded by the Data OS Administrator")).toBeVisible();
  await page.goto("/os/intake/new?type=product");
  await expect(page.getByTestId("product-coming-soon")).toContainText("Coming soon");
  await expect(page.getByTestId("submit-request")).toHaveCount(0);
  // the old Design address lands on Enable, Onboard a domain
  await page.goto("/os/design/new-domain?import=1");
  await page.waitForURL(/\/os\/enable\/new-domain\?import=1$/);
  await expect(railLinks(page).filter({ hasText: "Enable" })).toHaveAttribute("aria-current", "page");
  for (const persona of ["product_owner", "designer"]) {
    await previewAs(page, persona);
    await page.goto("/os/enable/new-domain").catch(() => undefined);
    await page.waitForURL(/\/os\?denied=newDomain$/);
  }
});

// Build: what is ready (design done), built end to end by autopilot up to code review.
test("Ready to build shows each domain's design readiness and builds ready runs end to end", async ({ page }) => {
  await page.goto("/os");
  await expect(railLinks(page).filter({ hasText: "Build" })).toHaveAttribute("href", "/os/build/ready");
  await page.goto("/os/build/ready");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Ready to build");
  await expect(page.getByRole("list", { name: "Flow" }).getByRole("link")).toHaveText([/Intake/, /Enable/, /Design/, /Build/, /Review/]);
  const board = page.getByTestId("ready-board");
  test.skip(!(await board.count()), "no Silver run on record");
  const row = board.getByTestId("ready-row").first();
  for (const check of ["Model", "Mapping", "Transformations", "DQ checks"]) await expect(row).toContainText(check);
  // never clicked here: a build is a real run of the platform's agents
  const build = page.getByTestId("build-end-to-end");
  if (await build.count()) await expect(build.first()).toBeVisible();
  await page.goto("/os/history");
  await expect(page.getByRole("link", { name: "Silver pipeline list" })).toHaveAttribute("href", "/runs");
  // a preview never builds; a viewer reads Run history, not the board
  await previewAs(page, "forward_lead");
  await page.goto("/os/build/ready");
  if (await page.getByTestId("build-end-to-end").count()) await expect(page.getByTestId("build-end-to-end").first()).toBeDisabled();
  await previewAs(page, "viewer");
  await page.goto("/os/build/ready").catch(() => undefined);
  await page.waitForURL(/\/os\?denied=ready$/);
});
