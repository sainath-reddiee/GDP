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

test("the classic shell still frames the classic pages and links to /os", async ({ page }) => {
  await page.goto("/work");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav).toBeVisible();
  await expect(nav.getByRole("link", { name: "Agentic Data OS", exact: true })).toHaveAttribute("href", "/os");
  await expect(page.getByRole("navigation", { name: "Agentic Data OS" })).toHaveCount(0);
});
