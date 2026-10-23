import { expect, test } from "@playwright/test";

// Read-only walk of the Agentic Data OS experience (/os) over live data, and a check that the classic shell is
// untouched. Dev sign-in only (AIP_AUTH=dev): the aip_dev cookie stands in for the login.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

const SCREENS: [string, RegExp][] = [
  ["/os", /Good (morning|afternoon|evening)/],
  ["/os/intake", /Create an onboarding request/],
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
  await page.context().addCookies([{ name: "ados_preview_role", value: role, url: BASE }]);
}
const railLinks = (page: import("@playwright/test").Page) =>
  page.getByRole("navigation", { name: "Agentic Data OS" }).first().getByRole("link");

test("a viewer sees only the screens their role opens, and is turned back from the rest", async ({ page }) => {
  await previewAs(page, "VIEWER");
  await page.goto("/os");
  await expect(page.getByRole("status").filter({ hasText: "Previewing as viewer" })).toBeVisible();
  await expect(railLinks(page)).toHaveText(["My Work", "Inbox", "Overview", "Knowledge", "Run & DQ", "Quality", "Operations", "Audit", "Value", "Help"]);
  await expect(page.getByLabel("Persona")).toHaveText("Viewer");
  await page.goto("/os/build");
  await page.waitForURL(/\/os\?denied=build$/);
  await expect(page.getByText("Build is not part of your role.")).toBeVisible();
  // the classic pages are gated by the same list
  await page.goto("/runs");
  await page.waitForURL(/\/os\?denied=pipelines$/);
  await page.goto("/admin");
  await page.waitForURL(/\/os\?denied=admin$/);
});

test("a code reviewer gets the build, review and release screens and the developer persona", async ({ page }) => {
  await previewAs(page, "CODE_REVIEWER");
  await page.goto("/os");
  await expect(railLinks(page)).toHaveText(["My Work", "Inbox", "Overview", "Copilot", "Build", "Review", "Release", "Pipelines", "Stores", "Knowledge",
                                          "Run & DQ", "Quality", "Operations", "Approvals", "Audit", "Value", "Help"]);
  await expect(page.getByLabel("Persona")).toHaveText("Data Developer");
});

test("without the privilege, an action becomes a change request to the approver role", async ({ page }) => {
  // a data engineer builds but does not approve code: "Review code" is never offered as their own action
  await previewAs(page, "DATA_ENGINEER");
  await page.goto("/os/build");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "Review code", exact: true })).toHaveCount(0);
  const request = page.getByRole("link", { name: /^Request: / });
  for (const link of await request.all()) await expect(link).toHaveAttribute("title", /You do not hold [A-Z.]+\. Your change goes to/);
  await page.goto("/os/help");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("One workflow, many personas");
  await expect(page.getByText("Access list")).toBeVisible();
});

test("classic pages open inside the Agentic Data OS shell, with their own tabs", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByRole("navigation", { name: "Agentic Data OS" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  await expect(railLinks(page).filter({ hasText: "Pipelines" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("workspace-tabs").getByRole("link", { name: "Gold Pipeline" })).toBeVisible();
  await page.goto("/dashboard/store");
  await expect(railLinks(page).filter({ hasText: "Stores" })).toHaveAttribute("aria-current", "page");
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
