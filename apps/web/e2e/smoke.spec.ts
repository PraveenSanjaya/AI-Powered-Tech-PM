import { expect, type Page, test } from "@playwright/test";

const PASSWORD = "demo-password-123";

async function login(page: Page, role: "manager" | "admin" | "member" = "manager") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(`${role}@demo.example`);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).last().click();
  await expect(page.getByRole("heading", { name: "Choose a workspace and project" })).toBeVisible();
  await page.getByRole("button", { name: /ATLAS/ }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("login rejects bad credentials", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("manager@demo.example");
  await page.getByLabel("Password", { exact: true }).fill("wrong-password-1");
  await page.getByRole("button", { name: "Sign in", exact: true }).last().click();
  await expect(page.getByRole("alert")).toContainText("Invalid email or password");
});

test("dashboard loads with demo labelling, sprint goal, blockers and freshness", async ({ page }) => {
  await login(page);
  await expect(page.getByRole("heading", { name: /Atlas Checkout Revamp/ })).toBeVisible();
  await expect(page.getByText("DEMO MODE")).toBeVisible();
  await expect(page.getByText(/Ship the new checkout flow/)).toBeVisible();
  await expect(page.getByText("confirmed blocked")).toBeVisible();
  await expect(page.getByText("Synchronization freshness:")).toBeVisible();
  await expect(page.getByText(/Historical sprint snapshots have not been collected/)).toBeVisible();
});

test("issue and pull request filters", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Issues", exact: true }).click();
  await expect(page.getByRole("cell", { name: "ATLAS-101" }).first()).toBeVisible();
  await page.getByLabel("Status").selectOption("Done");
  await expect(page.getByRole("link", { name: "ATLAS-102" })).toBeVisible();
  await expect(page.getByRole("link", { name: "ATLAS-101" })).toHaveCount(0);
  await page.getByLabel("Status").selectOption("");
  await page.getByLabel("Assignee").selectOption("none");
  await expect(page.getByRole("link", { name: "ATLAS-108" })).toBeVisible();
  await expect(page.getByRole("link", { name: "ATLAS-102" })).toHaveCount(0);

  await page.getByRole("link", { name: "Pull requests", exact: true }).click();
  await expect(page.getByRole("link", { name: "atlas-api#218" })).toBeVisible();
  await page.getByLabel("Checks").selectOption("failing");
  await expect(page.getByRole("link", { name: "atlas-web#212" })).toBeVisible();
  await expect(page.getByRole("link", { name: "atlas-api#218" })).toHaveCount(0);
});

test("assistant answers with valid evidence references", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "AI assistant" }).click();
  await page.getByRole("button", { name: "What threatens our sprint goal?" }).click();
  const answer = page.locator('[data-role="assistant"]').last();
  await expect(answer).toContainText("Confirmed blockers");
  await expect(answer).toContainText("ATLAS-101");
  await expect(answer).toContainText("Demo data and deterministic template output");
  await answer.getByText(/^Evidence \(/).click();
  const citations = answer.getByTestId("citation");
  expect(await citations.count()).toBeGreaterThan(0);
  await expect(citations.first()).toContainText("demo link");
});

test("draft action review, approval, simulated execution and safe retry", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "AI assistant" }).click();
  await page.getByRole("button", { name: "Prepare a Jira comment for ATLAS-101" }).click();
  await page.getByRole("link", { name: /Review proposed action/ }).click();
  await expect(page.getByText("DEMO MODE: executing this action only writes")).toBeVisible();
  const payload = page.getByTestId("payload");
  await expect(payload).toContainText("Status update on ATLAS-101");
  const exact = await payload.innerText();

  await page.getByRole("button", { name: /Approve version 1/ }).click();
  await expect(page.getByText("approved", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Execute (simulated)" }).click();
  await expect(page.getByText(/external id/)).toContainText("demo-jira-");
  await expect(page.getByText("(simulated, not a real service)")).toBeVisible();

  await page.getByRole("button", { name: /Retry \(safe/ }).click();
  await expect(page.getByText(/#1 succeeded/)).toBeVisible();
  await expect(page.getByText(/#2 /)).toHaveCount(0); // no second execution attempt was created
  expect(await payload.innerText()).toBe(exact);

  await page.getByRole("link", { name: "Audit history" }).click();
  await expect(page.getByRole("cell", { name: "action.executed" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "action.approved", exact: true })).toBeVisible();
});

test("integration failure and disconnected states are visible", async ({ page }) => {
  await login(page, "admin");
  // 1. API failure -> error state with retry
  await page.route("**/api/workspaces/*/integrations", (route) => route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ detail: "Upstream unavailable" }) }));
  await page.getByRole("link", { name: "Integrations" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Upstream unavailable" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" }).first()).toBeVisible();
  await page.unroute("**/api/workspaces/*/integrations");
  await page.getByRole("button", { name: "Retry" }).first().click();

  // 2. disconnect Jira -> disconnected + stale warning, sync disabled
  const jira = page.getByTestId("integration-jira");
  await expect(jira.getByText("connected", { exact: true })).toBeVisible();
  await jira.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect(jira.getByText("disconnected", { exact: true })).toBeVisible();
  await expect(jira.getByText(/no longer being refreshed/)).toBeVisible();
  await expect(jira.getByRole("button", { name: "Sync now" })).toHaveCount(0);
  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByText(/Disconnected or failing integration/)).toBeVisible();
});
