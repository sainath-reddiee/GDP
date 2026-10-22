import { expect, test, type BrowserContext } from "@playwright/test";

// Model Workbench against the fake API (dev sign-in as ANA): COMPANY has v2 in review by BEN, PROPERTY has ANA's draft.
const BASE = process.env.AIP_WEB_URL ?? "http://localhost:3000";

async function devSession(context: BrowserContext) {
  await context.addCookies([{ name: "aip_dev", value: "1", url: BASE }]);
}

test.beforeEach(async ({ context }) => devSession(context));

test("domain models are listed with their state", async ({ page }) => {
  await page.goto("/dashboard/modeling");
  await expect(page.getByTestId("model-COMPANY")).toContainText("v2 in review (BEN)");
  await expect(page.getByTestId("model-PROPERTY")).toContainText("v2 draft");
  await expect(page.getByTestId("model-FINANCE")).toContainText("No model yet");
  await expect(page.getByRole("button", { name: /Baseline from registry/ })).toBeVisible();
});

test("a run proposal is merged into the open draft", async ({ page }) => {
  await page.goto("/dashboard/modeling/m-property?tab=changes");
  const changes = page.getByTestId("run-changes");
  await expect(changes).toContainText("PROPERTY_CORE.YEAR_BUILT added");
  await expect(changes).toContainText("0 breaking · 1 additive");
  await page.getByTestId("merge-chg-1").click();
  await expect(page.getByText("Merged into draft v2; review and submit it")).toBeVisible();
  await expect(page.getByTestId("merge-chg-1")).toHaveCount(0);
});

test("a modeler edits the draft, sees the change classes and submits it", async ({ page }) => {
  await page.goto("/dashboard/modeling/m-property?tab=entities");
  await page.getByTestId("entity-PROPERTY_CORE").click();
  await page.getByTestId("add-column").click();
  const row = page.getByTestId("col-NEW_COLUMN");
  await row.getByLabel("Column name").fill("year_built");
  await page.getByTestId("col-YEAR_BUILT").getByLabel("Data type").fill("NUMBER(4,0)");
  await expect(page.getByTestId("model-bar")).toContainText("Unsaved changes");
  await expect(page.getByTestId("submit-review")).toBeDisabled();
  await page.getByLabel("Change note").fill("Year built for valuation");
  await page.getByTestId("save-draft").click();
  await expect(page.getByText(/Draft saved: 1 additive, 0 breaking/)).toBeVisible();
  await expect(page.getByTestId("model-bar")).toContainText("Draft v2 saved");
  await page.getByTestId("submit-review").click();
  await expect(page.getByText("v2 sent for review")).toBeVisible();
  await page.getByTestId("model-tab-review").click();
  await expect(page.getByTestId("decision")).toContainText("You submitted or edited this version");
});

test("a reviewer sees the diff and impact, comments, and approves with the breaking change acknowledged", async ({ page }) => {
  await page.goto("/dashboard/modeling/m-company");
  await expect(page.getByTestId("model-diff")).toContainText("1 breaking");
  await expect(page.getByTestId("model-impact")).toContainText("Data Mapping lines: 1");
  await page.getByLabel("Comment kind").selectOption("COMMENT");
  await page.getByLabel("Comment", { exact: true }).fill("Narrowing is fine, the longest name is 42 characters");
  await page.getByTestId("post-comment").click();
  await expect(page.getByTestId("comments")).toContainText("longest name is 42");
  await expect(page.getByTestId("approve-version")).toBeDisabled();
  await page.getByTestId("ack-breaking").check();
  await page.getByTestId("approve-version").click();
  await expect(page.getByText("v2 approved and applied to the domain registry")).toBeVisible();
});

test("versions can be compared and the diagram renders", async ({ page }) => {
  await page.goto("/dashboard/modeling/m-property?tab=versions");
  await page.getByTestId("compare-versions").click();
  await expect(page.getByTestId("model-diff")).toBeVisible();
  await page.getByTestId("model-tab-diagram").click();
  await expect(page.getByTestId("er-PROPERTY_CORE")).toBeVisible();
});

test("an approved version is published to Git and the merged pull request makes it published", async ({ page }) => {
  await page.goto("/dashboard/modeling/m-property?tab=publish");
  await expect(page.getByLabel("Repository")).toHaveValue("repo-1");
  await page.getByTestId("publish-version").click();
  await expect(page.getByText("Pull request opened for v1: feature/model-property-v1")).toBeVisible();
  const table = page.getByTestId("publish");
  await expect(table).toContainText("#42");
  await expect(page.getByTestId("publish-version")).toHaveCount(0);
  await page.getByRole("button", { name: "Check pull requests" }).click();
  await expect(table).toContainText("merged");
  await expect(table).toContainText(/published/i);
});

test("a run proposes its design to the domain model instead of approving it", async ({ page }) => {
  await page.goto("/runs/11111111-aaaa-bbbb-cccc-000000000001/mapping");
  await expect(page.getByTestId("run-workbench")).toContainText("governed in the Model Workbench");
  await expect(page.getByRole("button", { name: "Approve and use for mapping" })).toHaveCount(0);
  await page.getByRole("button", { name: "Propose to domain model" }).click();
  await expect(page.getByText(/Proposed 1 change to the domain model/).first()).toBeVisible();
  await expect(page.getByTestId("run-workbench")).toContainText("proposal pending");
  await expect(page.getByRole("link", { name: "Open proposals" })).toHaveAttribute("href", "/dashboard/modeling/m-company?tab=changes");
  await expect(page.getByRole("button", { name: "Propose again" })).toBeVisible();
});
